"""Paralelní stahování JSON souborů úředních desek ze serverů úřadů."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from . import feed, galileo, imunis, tls, vismo, webpage
from .nkod import BoardSource
from .normalize import BoardFormatError, decode_board

log = logging.getLogger(__name__)

MAX_BYTES = 50 * 1024 * 1024
PER_HOST = 2  # víc souběžných spojení na jeden server část hostingů odmítá
# Weby jednoho poskytovatele běží na stejných serverech (Galileo: ~2 300 obcí) – souběh se omezí
# na celého poskytovatele, jinak hosting po chvíli přestane přijímat spojení.
RETRY_DELAY = 5.0
PROVIDER_LIMITS = {"galileo": 6, "imunis": 4, "vismo": 6}

# Druhy chyb – frontend k nim zobrazuje vysvětlení.
KIND_TIMEOUT = "timeout"
KIND_CONNECT = "connect"
KIND_TLS = "tls"
KIND_NOT_FOUND = "not_found"
KIND_SERVER = "server"
KIND_HTTP = "http"
KIND_HTML = "html"
KIND_EMPTY = "empty"
KIND_INVALID = "invalid"
KIND_FORMAT = "format"


@dataclass
class FetchResult:
    ok: bool
    payload: dict | None = None
    error: str | None = None
    http_status: int | None = None
    elapsed_ms: int = 0
    kind: str | None = None
    url: str | None = None
    warnings: list[str] = field(default_factory=list)
    via_proxy: bool = False


class _Failure(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None, retry: bool = False):
        super().__init__(message)
        self.kind, self.status, self.retry = kind, status, retry


def _format_kind(message: str) -> str:
    if message.startswith("Místo dat vrací HTML"):
        return KIND_HTML
    if message.startswith("Prázdná odpověď"):
        return KIND_EMPTY
    if message.startswith("Server vrací místo dat chybovou hlášku"):
        return KIND_SERVER
    if message.startswith("Neplatný JSON"):
        return KIND_INVALID
    return KIND_FORMAT


async def _get(client: httpx.AsyncClient, url: str) -> tuple[bytes, int]:
    async with client.stream("GET", url) as resp:
        if resp.status_code == 404 or resp.status_code == 410:
            raise _Failure(KIND_NOT_FOUND, f"HTTP {resp.status_code}", resp.status_code)
        if resp.status_code >= 500:
            raise _Failure(KIND_SERVER, f"HTTP {resp.status_code}", resp.status_code, retry=True)
        if resp.status_code >= 400:
            raise _Failure(KIND_HTTP, f"HTTP {resp.status_code}", resp.status_code)
        buf = bytearray()
        async for chunk in resp.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > MAX_BYTES:
                raise _Failure(KIND_FORMAT, "Soubor je větší než 50 MB", resp.status_code)
        return bytes(buf), resp.status_code


async def _get_with_tls_fix(client: httpx.AsyncClient, url: str) -> tuple[bytes, int, list[str]]:
    try:
        content, status = await _get(client, url)
        return content, status, []
    except httpx.ConnectError as exc:
        if not tls.is_missing_issuer(exc):
            raise
        parts = urlsplit(url)
        ctx = await tls.context_with_intermediates(client, parts.hostname or "", parts.port or 443)
        if ctx is None:
            raise _Failure(KIND_TLS, "Server neposílá úplný řetězec certifikátů a chybějící se nepodařilo dohledat") from exc
        async with httpx.AsyncClient(verify=ctx, headers=client.headers, timeout=client.timeout,
                                     follow_redirects=True) as fixed:
            content, status = await _get(fixed, url)
        return content, status, ["server neposílá mezilehlý certifikát (doplněn automaticky)"]


async def _fetch_url(client: httpx.AsyncClient, url: str, retries: int) -> FetchResult:
    started = time.monotonic()
    ms = lambda: int((time.monotonic() - started) * 1000)  # noqa: E731
    failure: _Failure | None = None
    for attempt in range(retries + 1):
        try:
            content, status, warnings = await _get_with_tls_fix(client, url)
            payload, decode_warnings = decode_board(content)
            return FetchResult(True, payload, None, status, ms(), None, url, warnings + decode_warnings)
        except BoardFormatError as exc:
            return FetchResult(False, None, str(exc), None, ms(), _format_kind(str(exc)), url)
        except _Failure as exc:
            failure = exc
        except httpx.TimeoutException as exc:
            phase = "připojení" if isinstance(exc, httpx.ConnectTimeout) else "odpověď"
            failure = _Failure(KIND_TIMEOUT, f"Vypršel čas na {phase}", retry=True)
        except httpx.ConnectError as exc:
            msg = str(exc) or type(exc).__name__
            kind = KIND_TLS if "CERTIFICATE" in msg or "SSL" in msg else KIND_CONNECT
            failure = _Failure(kind, msg[:200], retry=kind == KIND_CONNECT)
        except httpx.HTTPError as exc:
            failure = _Failure(KIND_CONNECT, f"{type(exc).__name__}: {exc}"[:200], retry=True)
        if not failure.retry or attempt == retries:
            break
        await asyncio.sleep(3 * 2**attempt)
    assert failure is not None
    return FetchResult(False, None, str(failure), failure.status, ms(), failure.kind, url)


ADAPTERS = {"imunis": imunis.fetch_board, "galileo": galileo.fetch_board,
            "rss": feed.fetch_board, "vismo": vismo.fetch_board,
            "html": webpage.fetch_board}


BLOCKED = (401, 403, 451)  # server odmítá – často jen zahraniční IP (runner GitHubu je v USA)


def needs_proxy(exc: Exception) -> bool:
    """Chyba, kterou může vyřešit záložní proxy v ČR: nepřipojí se, vyprší čas, nebo server přístup odmítne."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in BLOCKED
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))


async def _fetch_adapter(client: httpx.AsyncClient, src: BoardSource,
                         host_locks: dict[str, asyncio.Semaphore],
                         fallback: httpx.AsyncClient | None = None) -> FetchResult:
    """Desky mimo NKOD čtené adaptérem (RSS / HTML) – výsledek je ve tvaru OFN.

    Když server nejde přímo (timeout, spojení, 401/403/451) a je k dispozici záložní klient (proxy v ČR),
    zkusí se deska přes něj.
    """
    started = time.monotonic()
    ms = lambda: int((time.monotonic() - started) * 1000)  # noqa: E731
    host = urlsplit(src.access_url).hostname or ""
    via_proxy = False
    async with host_locks.setdefault(host, asyncio.Semaphore(PER_HOST)):
        try:
            try:
                try:
                    payload = await ADAPTERS[src.fmt](client, src.access_url)
                except (httpx.TimeoutException, httpx.ConnectError):
                    await asyncio.sleep(RETRY_DELAY)  # přetížený hosting – jeden pozdější pokus
                    payload = await ADAPTERS[src.fmt](client, src.access_url)
            except httpx.HTTPError as exc:
                if fallback is None or not needs_proxy(exc):
                    raise
                log.info("Deska %s přímo nejde (%s), zkouším záložní proxy", src.publisher, type(exc).__name__)
                payload = await ADAPTERS[src.fmt](fallback, src.access_url)
                via_proxy = True
            return FetchResult(True, payload, None, 200, ms(), None, src.access_url, via_proxy=via_proxy)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            kind = KIND_NOT_FOUND if code in (404, 410) else KIND_SERVER if code >= 500 else KIND_HTTP
            return FetchResult(False, None, f"HTTP {code}", code, ms(), kind, src.access_url)
        except httpx.TimeoutException:
            return FetchResult(False, None, "Vypršel čas na připojení", None, ms(), KIND_TIMEOUT, src.access_url)
        except httpx.HTTPError as exc:
            return FetchResult(False, None, f"{type(exc).__name__}: {exc}"[:200], None, ms(), KIND_CONNECT, src.access_url)
        except Exception as exc:  # noqa: BLE001 – neplatné RSS apod.
            return FetchResult(False, None, f"Nečitelná deska: {exc}"[:200], None, ms(), KIND_FORMAT, src.access_url)


async def _fetch_board(client: httpx.AsyncClient, src: BoardSource, retries: int,
                       host_locks: dict[str, asyncio.Semaphore],
                       fallback: httpx.AsyncClient | None = None) -> FetchResult:
    """Zkusí postupně všechny distribuce datové sady; vrátí první úspěch, jinak chybu první z nich.

    Pokud žádná nejde kvůli síti (timeout, odmítnuté spojení) a je k dispozici záložní klient
    (proxy v ČR – část hostingů blokuje zahraniční IP), zkusí se hlavní distribuce přes něj.
    """
    if src.fmt in ADAPTERS:
        return await _fetch_adapter(client, src, host_locks, fallback)
    first: FetchResult | None = None
    for url in src.urls():
        host = urlsplit(url).hostname or ""
        lock = host_locks.setdefault(host, asyncio.Semaphore(PER_HOST))
        async with lock:
            res = await _fetch_url(client, url, retries)
        if res.ok:
            if first is not None:
                res.warnings.append(f"hlavní distribuce nefunguje ({first.error}), použita záložní")
            return res
        first = first or res
    assert first is not None
    if fallback is not None and first.kind in (KIND_TIMEOUT, KIND_CONNECT):
        res = await _fetch_url(fallback, src.access_url, retries=1)
        if res.ok:
            res.via_proxy = True
            return res
    return first


async def fetch_all(
    client: httpx.AsyncClient,
    sources: list[BoardSource],
    concurrency: int = 24,
    retries: int = 2,
    fallback: httpx.AsyncClient | None = None,
    board_timeout: float = 240,
    total_budget: float = 40 * 60,
) -> dict[str, FetchResult]:
    sem = asyncio.Semaphore(concurrency)
    host_locks: dict[str, asyncio.Semaphore] = {}

    loop = asyncio.get_running_loop()
    deadline = loop.time() + total_budget

    providers = {name: asyncio.Semaphore(n) for name, n in PROVIDER_LIMITS.items()}

    async def run(src: BoardSource) -> tuple[str, FetchResult]:
        # zámek poskytovatele se bere před globálním slotem a mimo limit času na desku
        async with providers.get(src.fmt) or contextlib.nullcontext(), sem:
            left = deadline - loop.time()
            if left <= 0:
                return src.id, FetchResult(False, None, "Nestaženo – vyčerpán čas na celý sběr", None, 0,
                                           KIND_TIMEOUT, src.access_url)
            try:
                # celkový limit na desku: server, který posílá data po kapkách, obchází timeout čtení
                res = await asyncio.wait_for(_fetch_board(client, src, retries, host_locks, fallback),
                                             timeout=min(board_timeout, left))
            except asyncio.TimeoutError:
                res = FetchResult(False, None, f"Stahování desky trvalo déle než {int(min(board_timeout, left))} s",
                                  None, int(min(board_timeout, left) * 1000), KIND_TIMEOUT, src.access_url)
            if not res.ok:
                log.info("Chyba [%s] %s (%s): %s", res.kind, src.publisher or src.title, res.url, res.error)
            elif res.warnings:
                log.info("Upozornění %s: %s", src.publisher or src.title, "; ".join(res.warnings))
            return src.id, res

    pairs = await asyncio.gather(*(run(s) for s in sources))
    return dict(pairs)

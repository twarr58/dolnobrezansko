"""Paralelní stahování JSON souborů úředních desek ze serverů obcí."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from .nkod import BoardSource
from .normalize import BoardFormatError, parse_payload

log = logging.getLogger(__name__)

MAX_BYTES = 50 * 1024 * 1024


@dataclass
class FetchResult:
    ok: bool
    payload: dict | None = None
    error: str | None = None
    http_status: int | None = None
    elapsed_ms: int = 0


async def _fetch_one(client: httpx.AsyncClient, src: BoardSource, retries: int) -> FetchResult:
    started = time.monotonic()
    last_error = "neznámá chyba"
    status = None
    for attempt in range(retries + 1):
        try:
            async with client.stream("GET", src.access_url) as resp:
                status = resp.status_code
                if resp.status_code >= 400:
                    last_error = f"HTTP {resp.status_code}"
                    if resp.status_code < 500:
                        break
                else:
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > MAX_BYTES:
                            raise BoardFormatError("Soubor je větší než limit")
                    payload = parse_payload(bytes(buf))
                    return FetchResult(True, payload, None, status, int((time.monotonic() - started) * 1000))
        except BoardFormatError as exc:
            last_error = str(exc)
            break
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        if attempt < retries:
            await asyncio.sleep(2**attempt)
    return FetchResult(False, None, last_error, status, int((time.monotonic() - started) * 1000))


async def fetch_all(
    client: httpx.AsyncClient,
    sources: list[BoardSource],
    concurrency: int = 32,
    retries: int = 2,
) -> dict[str, FetchResult]:
    sem = asyncio.Semaphore(concurrency)

    async def run(src: BoardSource) -> tuple[str, FetchResult]:
        async with sem:
            res = await _fetch_one(client, src, retries)
            if not res.ok:
                log.info("Chyba %s (%s): %s", src.publisher or src.title, src.access_url, res.error)
            return src.id, res

    pairs = await asyncio.gather(*(run(s) for s in sources))
    return dict(pairs)

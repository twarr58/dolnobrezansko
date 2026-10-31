"""Diagnostika nedostupných desek: postupně (bez souběhu) ověří DNS, TCP a HTTP.

Slouží k rozlišení, zda servery blokují zahraniční IP, omezují souběžná spojení,
nebo jsou prostě nedostupné. Výstup jde do logu workflow.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import USER_AGENT


def _resolve(host: str) -> list[str]:
    try:
        return sorted({ai[4][0] for ai in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})
    except socket.gaierror as exc:
        return [f"DNS chyba: {exc}"]


def _tcp(ip: str, port: int, timeout: float) -> str:
    started = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return f"ok {int((time.monotonic() - started) * 1000)} ms"
    except OSError as exc:
        return f"{type(exc).__name__} po {int((time.monotonic() - started) * 1000)} ms"


async def run(boards_path: Path, kinds: set[str]) -> None:
    boards = json.loads(boards_path.read_text("utf-8"))
    failed = [b for b in boards if not b.get("ok") and (not kinds or b.get("kind") in kinds)]
    print(f"Diagnostika {len(failed)} desek (druhy: {', '.join(sorted(kinds)) or 'vše'})")
    try:
        me = httpx.get("https://api.ipify.org", timeout=10).text
        print(f"Veřejná IP runneru: {me}")
    except httpx.HTTPError:
        pass
    by_ip: dict[str, list[str]] = defaultdict(list)
    tcp_ok = http_ok = 0
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=httpx.Timeout(20, connect=10),
                                 follow_redirects=True) as client:
        for b in failed:
            url = b.get("used_url") or b["access_url"]
            parts = urlsplit(url)
            host, port = parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)
            ips = await asyncio.to_thread(_resolve, host)
            v4 = [ip for ip in ips if "." in ip and not ip.startswith("DNS")]
            for ip in v4:
                by_ip[ip].append(host)
            tcp = await asyncio.to_thread(_tcp, v4[0], port, 8) if v4 else "bez IPv4"
            tcp_ok += tcp.startswith("ok")
            try:
                started = time.monotonic()
                r = await client.get(url)
                http = f"HTTP {r.status_code} {r.headers.get('content-type', '?')} {len(r.content)} B za {int((time.monotonic() - started) * 1000)} ms, začátek {r.content[:60]!r}"
                http_ok += r.status_code < 400
            except httpx.HTTPError as exc:
                http = f"{type(exc).__name__}: {str(exc)[:120]}"
            print(flush=True)
            print(f"- {b.get('name') or b['publisher']} | {url}\n    původně: [{b.get('kind')}] {b.get('error')}\n    IP: {', '.join(ips)}\n    TCP {port}: {tcp}\n    {http}")
    print(f"\nShrnutí: TCP spojení ok {tcp_ok}/{len(failed)}, HTTP ok {http_ok}/{len(failed)}")
    shared = {ip: hosts for ip, hosts in by_ip.items() if len(hosts) > 1}
    print(f"IP adresy sdílené více nedostupnými weby ({len(shared)}):")
    for ip, hosts in sorted(shared.items(), key=lambda kv: -len(kv[1])):
        print(f"  {ip}: {len(hosts)} – {', '.join(sorted(set(hosts))[:8])}")

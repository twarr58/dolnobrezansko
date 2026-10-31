"""Doplnění chybějících mezilehlých certifikátů (AIA chasing).

Část serverů úřadů posílá jen vlastní certifikát bez mezilehlého. Prohlížeče si chybějící
certifikát stáhnou podle rozšíření Authority Information Access, Python to neumí a spojení
odmítne (CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate).
Tady se mezilehlé certifikáty dohledají a přidají do důvěryhodného úložiště jen pro daný host.
Ověření řetězce až ke kořenové autoritě zůstává zapnuté.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl

import certifi
import httpx
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

log = logging.getLogger(__name__)

_cache: dict[str, ssl.SSLContext | None] = {}


def is_missing_issuer(exc: BaseException) -> bool:
    text = str(exc)
    return "CERTIFICATE_VERIFY_FAILED" in text and ("local issuer" in text or "unable to get issuer" in text)


def _leaf_certificate(host: str, port: int) -> x509.Certificate:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # jen kvůli přečtení certifikátu, data se tímto spojením nestahují
    with socket.create_connection((host, port), timeout=20) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    return x509.load_der_x509_certificate(der)


def _issuer_urls(cert: x509.Certificate) -> list[str]:
    try:
        aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    except x509.ExtensionNotFound:
        return []
    return [d.access_location.value for d in aia
            if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS
            and isinstance(d.access_location, x509.UniformResourceIdentifier)]


def _load_cert(data: bytes) -> x509.Certificate | None:
    for loader in (x509.load_der_x509_certificate, x509.load_pem_x509_certificate):
        try:
            return loader(data)
        except ValueError:
            continue
    return None


async def context_with_intermediates(client: httpx.AsyncClient, host: str, port: int = 443) -> ssl.SSLContext | None:
    """Vrátí SSLContext doplněný o mezilehlé certifikáty serveru, nebo None, pokud to nejde."""
    key = f"{host}:{port}"
    if key in _cache:
        return _cache[key]
    ctx = None
    try:
        cert = await asyncio.to_thread(_leaf_certificate, host, port)
        pems: list[str] = []
        for _ in range(3):  # nejvýš tři úrovně mezilehlých autorit
            urls = _issuer_urls(cert)
            if not urls or cert.issuer == cert.subject:
                break
            resp = await client.get(urls[0], timeout=20)
            resp.raise_for_status()
            issuer = _load_cert(resp.content)
            if issuer is None:
                break
            pems.append(issuer.public_bytes(Encoding.PEM).decode("ascii"))
            cert = issuer
        if pems:
            ctx = ssl.create_default_context(cafile=certifi.where())
            ctx.load_verify_locations(cadata="".join(pems))
            log.info("TLS: %s – doplněno %d mezilehlých certifikátů", host, len(pems))
    except Exception as exc:  # noqa: BLE001 – jakékoli selhání znamená jen „neumíme opravit“
        log.info("TLS: %s – mezilehlý certifikát se nepodařilo doplnit: %s", host, exc)
    _cache[key] = ctx
    return ctx

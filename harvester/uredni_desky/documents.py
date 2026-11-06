"""Přílohy oznámení: stažení, vytažení textu a rozpoznání druhu (text / mapa / sken / obrázek).

Výsledek se ukládá do mezipaměti podle URL – příloha se stahuje a čte jen jednou.
Skeny se čtou přes OCR (tesseract + pdftoppm), pokud jsou nástroje k dispozici.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from xml.etree import ElementTree

import httpx

log = logging.getLogger(__name__)
logging.getLogger("pypdf").setLevel(logging.ERROR)  # stovky varování o fontech na PDF z úřadů

# Tesseract si jinak bere všechna jádra (OpenMP) a souběžné OCR několika příloh se zahltí.
_OCR_ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}

MAX_BYTES = 30 * 1024 * 1024
TEXT_PAGES = 400  # text z PDF (dlouhé dokumenty jdou Claude jako text – musí být celé)
OCR_PAGES = 3
EXCERPT = 8000  # kolik textu se ukládá do mezipaměti (pro pravidla a hledání)

KIND_TEXT, KIND_MAP, KIND_SCAN, KIND_IMAGE, KIND_OTHER = "text", "mapa", "sken", "obrazek", "jine"

_MAP_NAME = re.compile(r"(mapa|mapy|výkres|vykres|situac|zákres|zakres|katastr|ortofoto|koordinačn|koordinacn|"
                       r"grafick|schéma|schema|plánek|planek|výřez|vyrez|polohopis|objízd|objizd)", re.I)


@dataclass
class DocInfo:
    url: str
    ext: str = ""
    kind: str = KIND_OTHER
    pages: int = 0
    size: int = 0
    chars: int = 0
    ocr: bool = False
    error: str = ""
    sha: str = ""  # otisk obsahu – pozná výměnu souboru na stejné adrese
    etag: str = ""
    lm: str = ""  # Last-Modified
    checked: str = ""  # kdy se naposledy ověřilo, že se soubor nezměnil
    text: str = ""  # výňatek (EXCERPT znaků)
    full_text: str = field(default="", repr=False)  # jen během běhu, neukládá se

    def to_cache(self) -> dict:
        d = asdict(self)
        d.pop("full_text")
        return {k: v for k, v in d.items() if v not in ("", 0, False)}


def sniff(content: bytes, url: str = "", content_type: str = "") -> str:
    """Typ souboru podle obsahu (přípona i Content-Type bývají špatně)."""
    head = content[:8]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tif"
    if head.startswith(b"PK\x03\x04"):
        try:
            names = zipfile.ZipFile(io.BytesIO(content)).namelist()
        except zipfile.BadZipFile:
            return "zip"
        if "word/document.xml" in names:
            return "docx"
        if "content.xml" in names and "mimetype" in names:
            return "odt"
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
        return "zip"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):  # OLE2 – starý Word (.doc), případně .xls
        return "xls" if re.search(r"\.xls(?:$|[?#])", url.lower()) else "doc"
    low = content[:512].lstrip().lower()
    if low.startswith((b"<!doctype html", b"<html")) or b"<html" in low:
        return "html"
    m = re.search(r"\.([a-z0-9]{2,4})(?:$|[?#])", url.lower())
    return m.group(1) if m else (content_type.split("/")[-1].split(";")[0] if content_type else "")


def _clean(text: str) -> str:
    text = text.replace("\x00", " ").replace("­", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _xml_text(data: bytes) -> str:
    root = ElementTree.fromstring(data)
    out, para = [], []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("p", "h") and para:
            out.append("".join(para))
            para = []
        if tag == "t" or tag in ("span", "p", "h"):
            if el.text:
                para.append(el.text)
        if tag in ("tab", "s"):
            para.append(" ")
    if para:
        out.append("".join(para))
    return "\n".join(out)


def _office_text(content: bytes, ext: str) -> str:
    zf = zipfile.ZipFile(io.BytesIO(content))
    return _xml_text(zf.read("word/document.xml" if ext == "docx" else "content.xml"))


_DOC_RUN16 = re.compile(r"(?:[\x20-\x7e\xa0-\u024f\r\n\t\u2013\u2014\u2018-\u201e\u2022\u2026]){12,}")
_DOC_RUN8 = re.compile(rb"[\x20-\x7e\x8a-\xff\r\n\t]{20,}")


def _doc_text(content: bytes) -> str:
    """Text ze starého Wordu (.doc, OLE2) bez externích nástrojů.

    Word 97–2003 ukládá text buď jako UTF-16LE, nebo „komprimovaně“ v jednobajtové kódové stránce (cp1250).
    Vezmou se souvislé úseky čitelných znaků – stačí na úřední dokumenty (usnesení, zápisy); formátování,
    tabulky a vložené objekty se ztratí.
    """
    utf16 = "\n".join(m.group(0) for m in _DOC_RUN16.finditer(content.decode("utf-16le", errors="ignore")))
    if len(utf16) >= 200 and sum(c.isalpha() for c in utf16) > len(utf16) * 0.5:
        text = utf16
    else:
        text = "\n".join(m.group(0).decode("cp1250", errors="ignore") for m in _DOC_RUN8.finditer(content))
    # konec textu dokumentu: za ním bývají názvy stylů, písem a metadata
    cut = re.search(r"\n(?:Normal|Normální|Default Paragraph Font|Výchozí písmo odstavce|Standardní písmo odstavce|Times New Roman)\b", text)
    return text[:cut.start()] if cut and cut.start() > 200 else text


def _ocr_pdf(content: bytes, pages: int) -> str:
    """OCR prvních stran PDF. Vyžaduje pdftoppm (poppler) a tesseract s češtinou."""
    if not (shutil.which("pdftoppm") and shutil.which("tesseract")):
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "doc.pdf"
        pdf.write_bytes(content)
        subprocess.run(["pdftoppm", "-r", "200", "-gray", "-png", "-f", "1", "-l", str(pages), str(pdf),
                        str(Path(tmp) / "p")], check=True, timeout=120, capture_output=True)
        texts = []
        for img in sorted(Path(tmp).glob("p*.png")):
            r = subprocess.run(["tesseract", str(img), "-", "-l", "ces+eng"], timeout=120, capture_output=True,
                               env=_OCR_ENV)
            texts.append(r.stdout.decode("utf-8", "replace"))
        return "\n".join(texts)


def analyze_pdf(content: bytes, name: str = "") -> tuple[str, int, str, bool]:
    """Vrátí (druh, počet stran, text, ocr)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    n = len(reader.pages)
    texts, big = [], 0
    for page in reader.pages[:TEXT_PAGES]:
        try:
            texts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 – poškozená stránka nesmí shodit zbytek
            texts.append("")
        box = page.mediabox
        if max(float(box.width), float(box.height)) > 1300:  # větší než A3 (1191 pt)
            big += 1
    text = _clean("\n".join(texts))
    per_page = len(text) / max(1, min(n, TEXT_PAGES))
    if _MAP_NAME.search(name) and per_page < 1500 or big and big >= len(texts) / 2 and per_page < 1500:
        return KIND_MAP, n, text, False
    if per_page < 80:
        try:
            ocr = _clean(_ocr_pdf(content, min(n, OCR_PAGES)))
        except (subprocess.SubprocessError, OSError) as exc:
            log.info("OCR selhalo: %s", exc)
            ocr = ""
        return KIND_SCAN, n, ocr or text, bool(ocr)
    return KIND_TEXT, n, text, False


def analyze(content: bytes, url: str, name: str = "", content_type: str = "") -> DocInfo:
    ext = sniff(content, url, content_type)
    info = DocInfo(url=url, ext=ext, size=len(content))
    try:
        if ext == "pdf":
            info.kind, info.pages, text, info.ocr = analyze_pdf(content, f"{name} {url}")
        elif ext in ("docx", "odt"):
            info.kind, text = KIND_TEXT, _clean(_office_text(content, ext))
        elif ext == "doc":
            info.kind, text = KIND_TEXT, _clean(_doc_text(content))
        elif ext in ("png", "jpg", "jpeg", "tif", "gif", "webp"):
            info.kind, text = (KIND_MAP if _MAP_NAME.search(f"{name} {url}") else KIND_IMAGE), ""
        elif ext in ("txt", "html", "htm"):
            raw = content.decode("utf-8", "replace")
            info.kind, text = KIND_TEXT, _clean(re.sub(r"<[^>]+>", " ", raw) if ext.startswith("htm") else raw)
        else:
            text = ""
    except Exception as exc:  # noqa: BLE001 – nečitelná příloha je běžná, popis se udělá z názvu
        info.error = f"{type(exc).__name__}: {exc}"[:200]
        text = ""
    info.full_text = text
    info.text = text[:EXCERPT]
    info.chars = len(text)
    return info


@dataclass
class Download:
    content: bytes | None  # None = nezměněno (HTTP 304)
    ctype: str = ""
    etag: str = ""
    lm: str = ""


async def download(client: httpx.AsyncClient, url: str, etag: str = "", last_modified: str = "") -> Download:
    """Stáhne přílohu; s etag/last_modified se ptá podmíněně a nezměněný soubor nestahuje (304)."""
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    async with client.stream("GET", url, timeout=60, headers=headers) as resp:
        if resp.status_code == 304:
            return Download(None)
        resp.raise_for_status()
        buf = bytearray()
        async for chunk in resp.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > MAX_BYTES:
                raise ValueError("příloha je větší než 30 MB")
        return Download(bytes(buf), resp.headers.get("content-type", ""), resp.headers.get("etag", ""),
                        resp.headers.get("last-modified", ""))


def fingerprint(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:16]


# ---------- stránka oznámení: přílohy a text ----------

_ANCHOR = re.compile(r'<a\b[^>]*?href\s*=\s*["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_DROP = re.compile(r"<(script|style|noscript|nav|header|footer|form)\b.*?</\1\s*>", re.I | re.S)
_MAIN = re.compile(r"<(main|article)\b[^>]*>(.*?)</\1\s*>", re.I | re.S)
_FILE_EXT = re.compile(r"\.(pdf|docx?|odt|rtf|xlsx?|ods|jpe?g|png|tiff?|zip)(?:$|[?#])", re.I)
_FILE_HINT = re.compile(r"(download|getfile|/file[/?]|soubor|priloh|attachment|e_download|dokument[/?])", re.I)
_TAG = re.compile(r"<[^>]+>")


def page_attachments(html: str, base: str) -> tuple[list[dict], str]:
    """Odkazy na přílohy a čitelný text ze stránky detailu oznámení (desky bez příloh v seznamu)."""
    from html import unescape
    from urllib.parse import urljoin, urlsplit

    html = _DROP.sub(" ", html)
    m = _MAIN.search(html)
    body = m.group(2) if m else html
    host = (urlsplit(base).hostname or "").removeprefix("www.")
    links, seen = [], set()
    for href, inner in _ANCHOR.findall(html):
        href = unescape(href.strip())
        if href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base, href)
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        path = urlsplit(url).path + "?" + urlsplit(url).query
        if not (_FILE_EXT.search(path) or _FILE_HINT.search(path)):
            continue
        uhost = (urlsplit(url).hostname or "").removeprefix("www.")
        if uhost != host and not _FILE_EXT.search(path):  # cizí web jen s přímým odkazem na soubor
            continue
        seen.add(url)
        name = " ".join(unescape(_TAG.sub(" ", inner)).split()) or url.rstrip("/").rsplit("/", 1)[-1]
        links.append({"name": name[:200], "url": url})
    text = _clean(unescape(_TAG.sub("\n", body)))
    return links[:20], text[:EXCERPT]

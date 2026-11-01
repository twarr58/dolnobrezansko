"""Obecný adaptér pro úřední desku jako HTML stránku na webu obce (libovolný redakční systém).

Weby malých obcí běží na stovkách různých systémů (WordPress, Joomla, Webnode, vlastní PHP…).
Společné mají jen to, že deska je seznam oznámení: odkaz s názvem (často přímo na PDF)
a u něj datum vyvěšení, případně sejmutí. Adaptér proto:

1. odstraní z HTML menu, hlavičku, patičku a skripty,
2. ke každému odkazu najde nejmenší nadřazený prvek, který obsahuje datum a málo odkazů
   (řádek tabulky, položka seznamu, blok článku),
3. kandidáty seskupí podle „tvaru“ prvku (značka + třída) a vezme největší skupinu – to je seznam
   oznámení, ne novinky v postranním panelu,
4. z textu položky určí datum vyvěšení a sejmutí (podle popisků, jinak první a druhé datum).

Když stránka desku jen vkládá (iframe se službou e-deska apod.), načte se obsah iframu.
"""

from __future__ import annotations

import collections
import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import lxml.html
from lxml import etree

_DATE = re.compile(r"(?<!\d)(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})(?!\d)|(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_UNTIL_LABEL = re.compile(r"(sejm|svěšen|vyvěšeno\s+do|zveřejněno\s+do|platnost\s+do|\bdo\s*:?\s*$|\s-\s*$|–\s*$)", re.I)
_POSTED_LABEL = re.compile(r"(vyvěš|zveřejn|\bod\s*:?\s*$|datum\s*:?\s*$|vloženo)", re.I)
_SKIP_HREF = re.compile(r"^(#|javascript:|mailto:|tel:|data:)", re.I)
_DOC_EXT = re.compile(r"\.(pdf|docx?|odt|rtf|xlsx?|ods|zip|rar|7z|jpe?g|png|tiff?)(\?|$)", re.I)
_DOC_HINT = re.compile(r"(download|file|soubor|priloh|attachment|getfile|dokument)", re.I)
_GENERIC_TEXT = re.compile(
    r"^(stáhnout|stahnout|pdf|docx?|odt|zobrazit|detail|více|vice|číst dále|cist dale|otevřít|"
    r"příloha|priloha|soubor|dokument|klikněte zde|zde|\d+(\s*[kKmM]i?B)?|\[?pdf\]?|»|›|>|\+)\b.{0,30}$", re.I)
_NOISE = re.compile(r"(^|[\s_-])(menu|nav|navbar|navigation|header|footer|breadcrumbs?|cookie|sidebar|"
                    r"submenu|topbar|skip|social|search|login|lang)([\s_-]|$)", re.I)
_PAGER = re.compile(r"(stránk|page|předchozí|další|následující|archiv|starší)", re.I)
_MIN_GROUP = 2
_MAX_LINKS_IN_ITEM = 6


@dataclass
class Item:
    url: str
    title: str
    posted: str | None = None
    until: str | None = None
    docs: list[tuple[str, str]] = field(default_factory=list)


def _iso(m: re.Match) -> str | None:
    if m.group(1):
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        y, mth, d = int(m.group(4)), int(m.group(5)), int(m.group(6))
    if not (1 <= d <= 31 and 1 <= mth <= 12 and 2000 <= y <= 2100):
        return None
    return f"{y:04d}-{mth:02d}-{d:02d}"


def dates_with_labels(text: str) -> list[tuple[str, str]]:
    """[(datum, text před datem)] – podle textu před datem se pozná vyvěšení / sejmutí."""
    out, last = [], 0
    for m in _DATE.finditer(text):
        if iso := _iso(m):
            out.append((iso, text[max(last, m.start() - 40):m.start()]))
        last = m.end()
    return out


def pick_dates(text: str) -> tuple[str | None, str | None]:
    found = dates_with_labels(text)
    posted = until = None
    for iso, before in found:
        if _UNTIL_LABEL.search(before) and not until:
            until = iso
        elif _POSTED_LABEL.search(before) and not posted:
            posted = iso
    rest = [iso for iso, _ in found if iso not in (posted, until)]
    if not posted and rest:
        posted = rest.pop(0)
    if not until and rest and posted and rest[0] > posted:
        until = rest[0]
    if posted and until and until < posted:
        posted, until = until, posted
    return posted, until


def _text(el) -> str:
    return " ".join(" ".join(el.itertext()).split())


def _strip_noise(root) -> None:
    for bad in root.xpath("//script|//style|//noscript|//nav|//header|//footer|//form|//select|//template"):
        bad.drop_tree()
    for el in root.xpath("//*[@class or @id or @role]"):
        if el.getparent() is None:
            continue
        marker = " ".join(filter(None, (el.get("class"), el.get("id"), el.get("role"))))
        if _NOISE.search(marker) and el.tag not in ("body", "html", "main", "table", "tr", "td", "li"):
            el.drop_tree()


def _clean_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


def _is_doc(url: str) -> bool:
    return bool(_DOC_EXT.search(url) or _DOC_HINT.search(urlsplit(url).path + "?" + urlsplit(url).query))


def _signature(el) -> str:
    cls = " ".join(sorted(c for c in (el.get("class") or "").split() if not re.search(r"\d", c)))
    parent = el.getparent()
    ptag = parent.tag if parent is not None else ""
    return f"{ptag}>{el.tag}.{cls}"


def _links(el) -> list:
    return [a for a in el.iter("a") if a.get("href") and not _SKIP_HREF.match(a.get("href").strip())]


def parse_board(html: str | bytes, base: str) -> list[Item]:
    try:
        root = lxml.html.fromstring(html)
    except (etree.ParserError, ValueError):
        return []
    _strip_noise(root)
    host = urlsplit(base).hostname or ""

    containers: dict[int, tuple] = {}
    for a in _links(root):
        href = _clean_url(urljoin(base, a.get("href").strip()))
        if not href.startswith(("http://", "https://")):
            continue
        el = a
        for _ in range(6):
            el = el.getparent()
            if el is None or el.tag in ("body", "html"):
                break
            links = _links(el)
            if len(links) > _MAX_LINKS_IN_ITEM:
                break
            text = _text(el)
            if len(text) > 1500:
                break
            if dates_with_labels(text):
                containers.setdefault(id(el), (el, text, links))
                break

    groups: dict[str, list] = collections.defaultdict(list)
    for el, text, links in containers.values():
        groups[_signature(el)].append((el, text, links))
    # vnořené kontejnery (řádek i buňka) – ponechá se vnější, aby se položka nezapočítala dvakrát
    best: list = []
    for sig, members in groups.items():
        if len(members) > len(best):
            best = members
    if len(best) < _MIN_GROUP:
        return []

    items: list[Item] = []
    for el, text, links in best:
        titled, docs = [], []
        for a in links:
            url = _clean_url(urljoin(base, a.get("href").strip()))
            label = " ".join((a.text_content() or a.get("title") or "").split())
            if _PAGER.fullmatch(label or "") or not url.startswith("http"):
                continue
            if _is_doc(url):
                docs.append((label, url))
            if label and not _GENERIC_TEXT.match(label) and not _DATE.fullmatch(label) and len(label) >= 4:
                titled.append((label, url))
        if titled:
            title, url = max(titled, key=lambda t: len(t[0]))
        elif links:
            url = _clean_url(urljoin(base, links[0].get("href").strip()))
            title = _DATE.sub("", text)
            title = re.sub(r"(vyvěšeno|sejmuto|datum|zveřejněno|od|do)\s*:?", "", title, flags=re.I)
            title = " ".join(title.split()).strip(" -–|:,")[:300]
        else:
            continue
        if len(title) < 4:
            continue
        posted, until = pick_dates(text)
        if not posted:
            continue
        uhost = urlsplit(url).hostname or ""
        if uhost != host and not _is_doc(url) and not uhost.endswith("." + host.removeprefix("www.")):
            # odkaz mimo web obce, který není soubor – nejspíš reklama / jiný web
            if not docs:
                continue
        items.append(Item(url, title, posted, until, [(n or title, u) for n, u in docs]))

    # stejné URL u více položek (např. všechny vedou na jednu stránku) – odlišit podle názvu
    counts = collections.Counter(i.url for i in items)
    seen, out = set(), []
    for it in items:
        if counts[it.url] > 1:
            it.url = f"{it.url}#{hashlib.sha1(it.title.encode()).hexdigest()[:10]}"
        if it.url not in seen:
            seen.add(it.url)
            out.append(it)
    return out


def to_informace(item: Item) -> dict:
    info: dict = {"iri": item.url, "url": item.url.split("#")[0], "název": {"cs": item.title},
                  "vyvěšení": {"datum": item.posted}}
    if item.until:
        info["relevantní_do"] = {"datum": item.until}
    if item.docs:
        info["dokument"] = [{"název": {"cs": n}, "url": u} for n, u in item.docs]
    return info


_IFRAME = re.compile(r'<iframe[^>]+src="([^"]+)"', re.I)


def iframe_sources(html: str, base: str) -> list[str]:
    out = []
    for src in _IFRAME.findall(html):
        url = urljoin(base, src.replace("&amp;", "&").strip())
        if url.startswith("http") and not re.search(r"(youtube|google|facebook|mapy\.cz|vimeo|twitter)", url, re.I):
            out.append(url)
    return out[:3]


async def fetch_items(client: httpx.AsyncClient, url: str) -> tuple[list[Item], str]:
    resp = await client.get(url)
    resp.raise_for_status()
    items = parse_board(resp.content, str(resp.url))
    if not items:
        for src in iframe_sources(resp.text, str(resp.url)):
            try:
                r = await client.get(src)
                r.raise_for_status()
            except httpx.HTTPError:
                continue
            if items := parse_board(r.content, str(r.url)):
                return items, str(r.url)
    return items, str(resp.url)


async def fetch_board(client: httpx.AsyncClient, url: str) -> dict:
    items, _ = await fetch_items(client, url)
    if not items:
        raise ValueError("Na stránce nebyl rozpoznán seznam oznámení s daty vyvěšení")
    return {"informace": [to_informace(i) for i in items]}

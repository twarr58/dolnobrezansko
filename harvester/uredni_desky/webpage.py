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

_MONTHS = {"led": 1, "úno": 2, "uno": 2, "bře": 3, "bre": 3, "dub": 4, "kvě": 5, "kve": 5, "čer": 6, "cer": 6,
           "srp": 8, "zář": 9, "zar": 9, "říj": 10, "rij": 10, "lis": 11, "pro": 12}
_DATE = re.compile(r"(?<!\d)(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})(?!\d)|(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"
                   r"|(?<!\d)(\d{1,2})\.\s*(ledna|leden|února|únor|března|březen|dubna|duben|května|květen|června|"
                   r"červen|července|červenec|srpna|srpen|září|října|říjen|listopadu|listopad|prosince|prosinec)\s+(\d{4})(?!\d)",
                   re.I)
_UNTIL_LABEL = re.compile(r"(sejm|svěšen|vyvěšeno\s+do|zveřejněno\s+do|platnost\s+do|\bdo\s*:?\s*$|\s-\s*$|–\s*$)", re.I)
_POSTED_LABEL = re.compile(r"(vyvěš|zveřejn|\bod\s*:?\s*$|datum\s*:?\s*$|vloženo)", re.I)
_SKIP_HREF = re.compile(r"^(#|javascript:|mailto:|tel:|data:)", re.I)
_DOC_EXT = re.compile(r"\.(pdf|docx?|odt|rtf|xlsx?|ods|zip|rar|7z|jpe?g|png|tiff?)(\?|$)", re.I)
_DOC_HINT = re.compile(r"(download|file|soubor|priloh|attachment|getfile|dokument)", re.I)
_GENERIC_TEXT = re.compile(
    r"^(stáhnout|stahnout|pdf|docx?|odt|zobrazit|detail|více|vice|číst dále|cist dale|otevřít|"
    r"příloha|priloha|soubor|dokument|klikněte zde|zde|celý příspěvek|celý článek|číst více|cist vice|"
    r"zobrazit více|více informací|\d+(\s*[kKmM]i?B)?|\[?pdf\]?|»|›|>|\+)\b.{0,30}$", re.I)
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
    elif m.group(7):
        name = m.group(8).lower()
        mth = 7 if name in ("července", "červenec") else _MONTHS.get(name[:3], 0)
        d, y = int(m.group(7)), int(m.group(9))
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
    # jen jednoznačné značky; podle tříd se neodstraňuje (obal celé stránky mívá třídu typu „has-sidebar“)
    for bad in root.xpath("//script|//style|//noscript|//nav|//select|//template|//header[not(.//table)]|"
                          "//footer[not(.//table)]|//*[@role='navigation']"):
        if bad.getparent() is not None:
            bad.drop_tree()


HTML_HEADERS = {"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}
_VOLATILE_PARAM = re.compile(r"^(kshow|utm_[a-z]+|sid|phpsessid|fbclid)=", re.I)


def _clean_url(url: str) -> str:
    """Bez fragmentu a parametrů, které se mění (kategorie výpisu, sledování) – URL slouží jako identifikátor."""
    p = urlsplit(url)
    query = "&".join(q for q in p.query.split("&") if q and not _VOLATILE_PARAM.match(q))
    return urlunsplit((p.scheme, p.netloc, p.path, query, ""))


def _is_doc(url: str) -> bool:
    return bool(_DOC_EXT.search(url) or _DOC_HINT.search(urlsplit(url).path + "?" + urlsplit(url).query))


def _signature(el) -> str:
    cls = " ".join(sorted(c for c in (el.get("class") or "").split() if not re.search(r"\d", c)))
    parent = el.getparent()
    ptag = parent.tag if parent is not None else ""
    return f"{ptag}>{el.tag}.{cls}"


def _links(el) -> list:
    return [a for a in el.iter("a") if a.get("href") and not _SKIP_HREF.match(a.get("href").strip())]


_META_CHARSET = re.compile(rb'<meta[^>]+charset=["\']?([a-z0-9_-]+)', re.I)


def decode_html(content: bytes, header_charset: str | None = None) -> str:
    """Text stránky: kódování z hlavičky, z <meta charset>, jinak UTF-8 a záložně windows-1250."""
    for enc in (header_charset, (m.group(1).decode() if (m := _META_CHARSET.search(content[:4096])) else None)):
        if enc:
            try:
                return content.decode(enc)
            except (LookupError, UnicodeDecodeError):
                pass
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1250", errors="replace")


def response_text(resp: httpx.Response) -> str:
    return decode_html(resp.content, resp.charset_encoding)


def parse_board(html: str | bytes, base: str) -> list[Item]:
    if isinstance(html, bytes):
        html = decode_html(html)
    html = re.sub(r"^\s*<\?xml[^>]*\?>", "", html)
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
        own = _text(a)
        own_dates = bool(dates_with_labels(own))
        chosen = None
        el = a
        for _ in range(7):
            el = el.getparent()
            if el is None or el.tag in ("body", "html"):
                break
            links = _links(el)
            if len(links) > _MAX_LINKS_IN_ITEM:
                break
            text = _text(el)
            if len(text) > 1500:
                break
            outside = text
            for lnk in links:  # datum mimo všechny odkazy – jinak jde o obal více položek
                if lt := _text(lnk):
                    outside = outside.replace(lt, " ", 1)
            if dates_with_labels(outside):
                chosen = (el, text, links)
                break
        if chosen is None and own_dates:
            # celá položka uvnitř odkazu (<a class="card">název, datum</a>), nebo datum jen v názvu
            chosen = (a, own, [a])
        if chosen:
            containers.setdefault(id(chosen[0]), chosen)

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
        if el.tag == "a":
            # celá položka v odkazu: název z nadpisu uvnitř, jinak text před prvním datem / popiskem
            heads = el.xpath(".//h1|.//h2|.//h3|.//h4|.//h5|.//strong|.//*[contains(@class,'title')]")
            head = next((_text(h) for h in heads if len(_text(h)) >= 4), "")
            if not head:
                head = re.split(r"(vyvěšeno|zveřejněno|vystavení|sejmut|platnost)", _text(el), 1, flags=re.I)[0]
            url = _clean_url(urljoin(base, el.get("href").strip()))
            titled = [(" ".join(head.split()).strip(" -–|:,"), url)] if len(head.strip()) >= 4 else titled
        if titled:
            title, url = max(titled, key=lambda t: len(t[0]))
        elif links:
            url = _clean_url(urljoin(base, links[0].get("href").strip()))
            title = _DATE.sub("", text)
            title = re.sub(r"(vyvěšeno|sejmuto|datum|zveřejněno|od|do)\s*:?", "", title, flags=re.I)
            title = " ".join(title.split()).strip(" -–|:,")[:300]
        else:
            continue
        title = re.sub(r"\s*[\(\[]?\s*(\d+([,.]\d+)?\s*[kKMm]i?B|pdf|docx?)\s*[\)\]]?\s*$", "", title).strip()
        if len(title) < 4:
            continue
        outside = text
        for lnk in links:
            if (lt := _text(lnk)) and lnk is not el:
                outside = outside.replace(lt, " ", 1)
        posted, until = pick_dates(outside if dates_with_labels(outside) else text)
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


_ALIS = re.compile(r'href="(https?://urednideska\.alis\.cz/([a-z0-9-]+))', re.I)


def subpages(html: str, base: str, limit: int = 6) -> list[str]:
    """Podstránky desky (deska rozdělená do kategorií: …/uredni-deska/stavebni-rizeni/)."""
    root_path = urlsplit(base).path.rstrip("/") + "/"
    out = []
    for href in re.findall(r'href="([^"#?]+)"', html):
        url = _clean_url(urljoin(base, href.replace("&amp;", "&")))
        p = urlsplit(url)
        if p.netloc == urlsplit(base).netloc and p.path.startswith(root_path) and p.path.rstrip("/") + "/" != root_path \
                and p.path.count("/") <= root_path.count("/") + 1 and not _is_doc(url) and not re.search(r"archiv|20[01]\d", p.path):
            out.append(url)
    return list(dict.fromkeys(out))[:limit]


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    try:
        r = await client.get(url, headers=HTML_HEADERS)
        r.raise_for_status()
        return r
    except (httpx.HTTPError, httpx.InvalidURL):
        return None


async def fetch_items(client: httpx.AsyncClient, url: str) -> tuple[list[Item], str]:
    resp = await client.get(url, headers=HTML_HEADERS)
    resp.raise_for_status()
    return await items_from_response(client, resp)


async def items_from_response(client: httpx.AsyncClient, resp: httpx.Response) -> tuple[list[Item], str]:
    """Položky ze stažené stránky desky, případně z iframu nebo z podstránek kategorií."""
    base = str(resp.url)
    items = parse_board(response_text(resp), base)
    if items:
        return items, base
    for src in iframe_sources(resp.text, base):
        if (r := await _get(client, src)) and (items := parse_board(response_text(r), str(r.url))):
            return items, str(r.url)
    merged: dict[str, Item] = {}
    for sp in subpages(resp.text, base):
        if r := await _get(client, sp):
            for it in parse_board(response_text(r), str(r.url)):
                merged.setdefault(it.url, it)
    return list(merged.values()), base


async def alis_infos(client: httpx.AsyncClient, html: str) -> list[dict] | None:
    """Stránka desky jen odkazuje na ALIS (urednideska.alis.cz/<obec>) – položky z jeho RSS."""
    from .feed import parse_feed
    if not (m := _ALIS.search(html)):
        return None
    try:
        r = await client.get(f"https://urednideska.alis.cz/{m.group(2)}/rss")
        r.raise_for_status()
        return parse_feed(r.content, str(r.url)) or None
    except (httpx.HTTPError, ValueError, etree.ParseError, Exception):  # noqa: BLE001
        return None


async def fetch_board(client: httpx.AsyncClient, url: str) -> dict:
    resp = await client.get(url, headers=HTML_HEADERS)
    resp.raise_for_status()
    items, _ = await items_from_response(client, resp)
    if items:
        return {"informace": [to_informace(i) for i in items]}
    if infos := await alis_infos(client, resp.text):
        return {"informace": infos}
    raise ValueError("Na stránce nebyl rozpoznán seznam oznámení s daty vyvěšení")

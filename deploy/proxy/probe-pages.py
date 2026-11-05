#!/usr/bin/env python3
"""Ladění zdrojů: stáhne stránky přes proxy na VPS (hlavičky prohlížeče) a vypíše zhuštěnou strukturu od prvního <h1>:
odkazy (A href | text), texty (T …), třídy položek seznamu a data-* atributy s rokem. Spustit na VPS jako root:
  python3 probe-pages.py URL [URL …]
"""
import os, re, sys, urllib.request
from html.parser import HTMLParser

PROXY = "http://harvest:%s@127.0.0.1:3128" % open("/etc/squid/harvest.pass").read().strip()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


class P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.a, self.h1 = [], 0, None, None

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag in ("script", "style", "nav", "footer", "noscript"):
            self.skip += 1
        if tag == "a":
            self.a = [d.get("href", ""), ""]
        if tag == "h1" and self.h1 is None:
            self.h1 = len(self.out)
        if tag in ("li", "article") and d.get("class"):
            self.out.append("<%s .%s>" % (tag, d["class"][:40]))
        for k, v in attrs:
            if k.startswith("data-") and v and re.search(r"\d{4}", v or ""):
                self.out.append("[%s=%s]" % (k, v[:40]))

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "noscript") and self.skip:
            self.skip -= 1
        if tag == "a" and self.a is not None:
            if not self.skip:
                self.out.append("A %s | %s" % (self.a[0][:110], " ".join(self.a[1].split())[:60]))
            self.a = None

    def handle_data(self, data):
        if self.skip:
            return
        if self.a is not None:
            self.a[1] += data
        elif data.strip():
            self.out.append("T " + " ".join(data.split())[:90])


for url in sys.argv[1:]:
    print("=====", url)
    try:
        html = opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30).read().decode("utf-8", "replace")
    except Exception as e:
        print("ERR", e)
        continue
    p = P()
    p.feed(html)
    start = p.h1 or 0
    for line in p.out[start:start + 70]:
        print(line)

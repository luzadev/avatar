"""Ricerca web per i motori locali: Brave (se c'è la chiave), altrimenti Bing e DuckDuckGo via HTML, senza chiavi né server."""
from __future__ import annotations

import html
import re

import requests



def brave_search(query: str, api_key: str, count: int = 6) -> list[dict]:
    res = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": count, "country": "IT", "search_lang": "it"},
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        timeout=15,
    )
    res.raise_for_status()
    hits = []
    for r in (res.json().get("web") or {}).get("results") or []:
        if r.get("url"):
            hits.append({"title": r.get("title") or r["url"], "url": r["url"],
                         "description": re.sub(r"<[^>]+>", "", r.get("description") or "")})
    return hits


UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
      "Accept-Language": "it-IT,it;q=0.9"}


def _clean(t: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", t)).split())


def bing_search(query: str, count: int = 6) -> list[dict]:
    r = requests.get("https://www.bing.com/search", params={"q": query, "setlang": "it", "cc": "IT"}, headers=UA, timeout=15)
    r.raise_for_status()
    hits = []
    for block in re.findall(r'<li class="b_algo".*?</li>', r.text, re.S):
        a = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not a or not a.group(1).startswith("http"):
            continue
        snip = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
        hits.append({"title": _clean(a.group(2)), "url": html.unescape(a.group(1)), "description": _clean(snip.group(1)) if snip else ""})
        if len(hits) >= count:
            break
    return hits


def ddg_search(query: str, count: int = 6) -> list[dict]:
    r = requests.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "it-it"}, timeout=15, headers=UA)
    r.raise_for_status()
    hits = []
    for m in re.finditer(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S):
        url = html.unescape(m.group(1))
        if "uddg=" in url:
            url = requests.utils.unquote(url.split("uddg=")[1].split("&")[0])
        hits.append({"title": html.unescape(re.sub(r"<[^>]+>", "", m.group(2))), "url": url,
                     "description": html.unescape(re.sub(r"<[^>]+>", "", m.group(3)))})
        if len(hits) >= count:
            break
    return hits


def web_search(query: str, api_key: str = "", count: int = 6) -> list[dict]:
    """Brave (con chiave) → Bing → DuckDuckGo, senza chiavi né server. Errore solo se falliscono tutti."""
    errors = []
    for name, fn in (("Brave", (lambda: brave_search(query, api_key, count)) if api_key else None),
                     ("Bing", lambda: bing_search(query, count)), ("DuckDuckGo", lambda: ddg_search(query, count))):
        if fn is None:
            continue
        try:
            hits = fn()
            if hits:
                return hits
            errors.append(f"{name}: nessun risultato")
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise RuntimeError("; ".join(errors))


if __name__ == "__main__":
    hits = web_search("meteo Ortona domani")
    assert hits and hits[0]["url"].startswith("http") and hits[0]["title"], hits
    print(len(hits), "risultati:", hits[0]["title"][:60], "|", hits[0]["description"][:80])

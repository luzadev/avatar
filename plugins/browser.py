"""Browser e web: legge una pagina, la pagina aperta in Safari, cerca su YouTube o Google."""
from __future__ import annotations

import html
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.macos import osa  # noqa: E402

MAX_CHARS = 6000
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) AvatarPy/1.0"}


def _html_to_text(page: str) -> str:
    page = re.sub(r"<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    page = re.sub(r"<br\s*/?>|</p>|</div>|</h\d>|</li>|</tr>", "\n", page, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    lines = [" ".join(l.split()) for l in text.splitlines()]
    return "\n".join(l for l in lines if len(l) > 2)


def leggi_pagina(params: dict, ctx: dict) -> str:
    url = str(params.get("url", "")).strip()
    if not url:
        try:
            url = osa('tell application "Safari" to return URL of front document')
        except Exception:
            return "Nessun indirizzo dato e Safari non ha pagine aperte."
    if not url.startswith("http"):
        url = "https://" + url
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    title = re.search(r"<title[^>]*>(.*?)</title>", r.text, flags=re.S | re.I)
    text = _html_to_text(r.text)
    return f"{html.unescape(title.group(1).strip()) if title else url}\n{text[:MAX_CHARS]}{' […]' if len(text) > MAX_CHARS else ''}"


def cerca(params: dict, ctx: dict) -> str:
    q = str(params.get("query", "")).strip()
    dove = str(params.get("dove", "google")).lower()
    if not q:
        return "Errore: serve la query."
    url = {"youtube": f"https://www.youtube.com/results?search_query={quote_plus(q)}",
           "maps": f"https://www.google.com/maps/search/{quote_plus(q)}",
           "amazon": f"https://www.amazon.it/s?k={quote_plus(q)}",
           "wikipedia": f"https://it.wikipedia.org/w/index.php?search={quote_plus(q)}"}.get(dove, f"https://www.google.com/search?q={quote_plus(q)}")
    subprocess.run(["open", url], check=False, timeout=10)
    return f"Apro la ricerca di «{q}» su {dove}."


def youtube(params: dict, ctx: dict) -> str:
    q = str(params.get("query", "")).strip()
    if not q:
        return "Errore: serve cosa cercare."
    r = requests.get(f"https://www.youtube.com/results?search_query={quote_plus(q)}", headers=UA, timeout=15)
    m = re.search(r'"videoId":"([\w-]{11})"', r.text)
    if not m:
        subprocess.run(["open", f"https://www.youtube.com/results?search_query={quote_plus(q)}"], check=False)
        return f"Apro i risultati YouTube per «{q}»."
    subprocess.run(["open", f"https://www.youtube.com/watch?v={m.group(1)}"], check=False)
    t = re.search(r'"videoId":"' + m.group(1) + r'".{0,400}?"title":\{"runs":\[\{"text":"([^"]+)"', r.text)
    return f"Riproduco su YouTube: {html.unescape(t.group(1)) if t else q}."


TOOLS = [
    {"name": "web_leggi_pagina", "description": "Legge il testo di una pagina web (dato l'url) o della pagina aperta in Safari, per riassumerla o rispondere.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}}, "run": leggi_pagina},
    {"name": "web_cerca_apri", "description": "Apre nel browser una ricerca su Google, YouTube, Maps, Amazon o Wikipedia.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "dove": {"type": "string", "enum": ["google", "youtube", "maps", "amazon", "wikipedia"]}}, "required": ["query"]}, "run": cerca},
    {"name": "youtube_riproduci", "description": "Cerca un video su YouTube e apre direttamente il primo risultato.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, "run": youtube},
]

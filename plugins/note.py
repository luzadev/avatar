"""Note di macOS: crea, cerca e legge note (AppleScript)."""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.macos import osa  # noqa: E402

SEP = "␟"


def _testo(body_html: str) -> str:
    t = re.sub(r"<br\s*/?>|</div>|</p>|</li>", "\n", body_html, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    return "\n".join(line.strip() for line in t.splitlines() if line.strip())


def crea(params: dict, ctx: dict) -> str:
    titolo = str(params.get("titolo", "")).strip() or "Nota"
    testo = str(params.get("testo", "")).strip()
    body = f"<h1>{html.escape(titolo)}</h1>" + "".join(f"<div>{html.escape(l) or '<br>'}</div>" for l in testo.splitlines())
    osa('on run argv\ntell application "Notes" to make new note at folder "Notes" with properties {name:item 1 of argv, body:item 2 of argv}\nend run', titolo, body)
    return f"Nota creata: «{titolo}»."


def cerca(params: dict, ctx: dict) -> str:
    q = str(params.get("query", "")).strip()
    if not q:
        return "Errore: serve il campo 'query'."
    out = osa(f'''on run argv
  set sep to "{SEP}"
  set out to ""
  set n to 0
  tell application "Notes"
    repeat with nt in (notes whose name contains (item 1 of argv) or plaintext contains (item 1 of argv))
      set n to n + 1
      if n > 8 then exit repeat
      set out to out & (name of nt) & sep & ((modification date of nt) as string) & linefeed
    end repeat
  end tell
  return out
end run''', q, timeout=90)
    rows = [l.split(SEP) for l in out.splitlines() if l]
    if not rows:
        return f"Nessuna nota trovata per '{q}'."
    return f"Note trovate per '{q}':\n" + "\n".join(f"- {r[0]} ({r[1]})" for r in rows) + "\nPer leggerne una usa note_leggi con il titolo."


def leggi(params: dict, ctx: dict) -> str:
    titolo = str(params.get("titolo", "")).strip()
    if not titolo:
        return "Errore: serve il titolo."
    out = osa('on run argv\ntell application "Notes" to return body of (first note whose name contains (item 1 of argv))\nend run', titolo, timeout=60)
    testo = _testo(out)
    return testo[:2500] + (" […]" if len(testo) > 2500 else "") if testo else "Nota vuota."


def aggiungi(params: dict, ctx: dict) -> str:
    titolo = str(params.get("titolo", "")).strip()
    testo = str(params.get("testo", "")).strip()
    if not titolo or not testo:
        return "Errore: servono titolo (della nota esistente) e testo da aggiungere."
    extra = "".join(f"<div>{html.escape(l) or '<br>'}</div>" for l in testo.splitlines())
    osa('on run argv\ntell application "Notes"\nset nt to first note whose name contains (item 1 of argv)\nset body of nt to (body of nt) & (item 2 of argv)\nend tell\nend run', titolo, extra)
    return f"Aggiunto alla nota «{titolo}»."


TOOLS = [
    {"name": "note_crea", "description": "Crea una nuova nota nell'app Note con titolo e testo. Usalo per 'prendi nota', 'appunta che…'.",
     "parameters": {"type": "object", "properties": {"titolo": {"type": "string"}, "testo": {"type": "string"}}, "required": ["testo"]}, "run": crea},
    {"name": "note_aggiungi", "description": "Aggiunge testo in fondo a una nota esistente, cercata per titolo.",
     "parameters": {"type": "object", "properties": {"titolo": {"type": "string"}, "testo": {"type": "string"}}, "required": ["titolo", "testo"]}, "run": aggiungi},
    {"name": "note_cerca", "description": "Cerca note per parola nel titolo o nel testo.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, "run": cerca},
    {"name": "note_leggi", "description": "Legge il contenuto di una nota cercata per titolo.",
     "parameters": {"type": "object", "properties": {"titolo": {"type": "string"}}, "required": ["titolo"]}, "run": leggi},
]

"""Comandi Rapidi (Shortcuts) di macOS: elenca ed esegue i tuoi comandi, anche per la domotica HomeKit."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.macos import sh  # noqa: E402


def elenca(params: dict, ctx: dict) -> str:
    names = [n for n in sh("shortcuts", "list").splitlines() if n.strip()]
    q = str(params.get("filtro", "")).strip().lower()
    if q:
        names = [n for n in names if q in n.lower()]
    if not names:
        return "Nessun comando rapido" + (f" contenente '{q}'." if q else ".")
    return f"Comandi rapidi ({len(names)}):\n" + "\n".join(f"- {n}" for n in names[:40])


def esegui(params: dict, ctx: dict) -> str:
    nome = str(params.get("nome", "")).strip()
    if not nome:
        return "Errore: serve il nome del comando rapido."
    names = [n for n in sh("shortcuts", "list").splitlines() if n.strip()]
    match = next((n for n in names if n.lower() == nome.lower()), None) or next((n for n in names if nome.lower() in n.lower()), None)
    if not match:
        return f"Nessun comando rapido chiamato '{nome}'."
    inp = str(params.get("input", "")).strip()
    out = sh("shortcuts", "run", match, *(["-i", "-"] if inp else []), timeout=120) if not inp else \
        __import__("subprocess").run(["shortcuts", "run", match, "-i", "-"], input=inp, capture_output=True, text=True, timeout=120).stdout
    return f"Eseguito «{match}»." + (f" Risultato: {out.strip()[:500]}" if out and out.strip() else "")


TOOLS = [
    {"name": "comandi_rapidi_elenca", "description": "Elenca i Comandi Rapidi disponibili sul Mac (anche quelli per luci, prese e casa).",
     "parameters": {"type": "object", "properties": {"filtro": {"type": "string"}}}, "run": elenca},
    {"name": "comandi_rapidi_esegui", "description": "Esegue un Comando Rapido per nome (es. 'Accendi luci soggiorno'). Usalo per domotica e automazioni create dall'utente.",
     "parameters": {"type": "object", "properties": {"nome": {"type": "string"}, "input": {"type": "string", "description": "Testo da passare in ingresso (facoltativo)."}}, "required": ["nome"]}, "run": esegui},
]

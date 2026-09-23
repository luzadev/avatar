"""Strumenti di memoria condivisi dai motori, sopra memory_manager di Mark-LIV."""
from __future__ import annotations

import json
import re

from memory import memory_manager as mm

CATEGORIES = ["identity", "preferences", "projects", "relationships", "wishes", "notes"]

SAVE_SCHEMA = {
    "type": "object",
    "properties": {
        "categoria": {"type": "string", "enum": CATEGORIES,
                       "description": "identity (nome, età, città…), preferences (gusti), projects, relationships (persone), wishes (desideri, obiettivi), notes (altro)."},
        "chiave": {"type": "string", "description": "Etichetta breve in minuscolo con underscore, es. 'nome', 'caffe_preferito', 'sorella_anna'."},
        "valore": {"type": "string", "description": "Il fatto, in una frase breve in italiano."},
    },
    "required": ["categoria", "chiave", "valore"],
    "additionalProperties": False,
}
FORGET_SCHEMA = {
    "type": "object",
    "properties": {
        "categoria": {"type": "string", "enum": CATEGORIES},
        "chiave": {"type": "string"},
    },
    "required": ["categoria", "chiave"],
    "additionalProperties": False,
}
SEARCH_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "Parole chiave da cercare nella memoria."}},
    "required": ["query"],
    "additionalProperties": False,
}

BULK_SCHEMA = {
    "type": "object",
    "properties": {
        "voci": {"type": "array", "description": "Elenco di fatti da salvare.",
                  "items": {"type": "object", "properties": {
                      "categoria": {"type": "string", "enum": CATEGORIES},
                      "chiave": {"type": "string"}, "valore": {"type": "string"}},
                      "required": ["categoria", "chiave", "valore"], "additionalProperties": False}},
    },
    "required": ["voci"],
    "additionalProperties": False,
}

TOOL_SPECS = [
    ("salva_memorie",
     "Salva più fatti sull'utente in una volta sola (per esempio importando memorie da un altro assistente o da un testo con molte informazioni). Ogni voce: categoria, chiave breve, valore in una frase.",
     BULK_SCHEMA),
    ("salva_memoria",
     "Salva in modo permanente un fatto sull'utente utile in futuro: nome, persone care, preferenze, abitudini, obiettivi, scadenze. Usalo anche quando l'utente chiede esplicitamente di ricordare qualcosa. Non dire mai di aver salvato senza averlo chiamato.",
     SAVE_SCHEMA),
    ("dimentica_memoria", "Cancella una memoria salvata (categoria e chiave come nell'elenco delle memorie).", FORGET_SCHEMA),
    ("cerca_memoria", "Cerca nella memoria a lungo termine fatti non presenti nel riepilogo del prompt.", SEARCH_SCHEMA),
]


def anthropic_tools() -> list[dict]:
    return [
        {"name": n, "description": d, "strict": True, "eager_input_streaming": True, "input_schema": s}
        for n, d, s in TOOL_SPECS
    ]


def openai_tools() -> list[dict]:
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": s}} for n, d, s in TOOL_SPECS]


def mcp_tools() -> list[dict]:
    return [{"name": n, "description": d, "inputSchema": s} for n, d, s in TOOL_SPECS]


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", s.lower().strip()).strip("_")
    return s[:40] or "nota"


def run_tool(name: str, args: dict) -> tuple[str, dict | None]:
    """Esegue uno strumento di memoria. Restituisce (risultato, evento per la UI)."""
    if name == "salva_memorie":
        voci = args.get("voci") or []
        saved = []
        for v in voci:
            if not isinstance(v, dict) or not str(v.get("valore", "")).strip():
                continue
            cat = v.get("categoria") if v.get("categoria") in CATEGORIES else "notes"
            key = _slug(str(v.get("chiave", "")))
            mm.remember(key, str(v["valore"]).strip(), cat)
            saved.append(f"{cat}/{key}")
        if not saved:
            return "Nessuna voce valida.", None
        return f"Salvate {len(saved)} memorie: {', '.join(saved[:20])}{'…' if len(saved) > 20 else ''}.", {"type": "memory_saved", "text": f"{len(saved)} voci importate"}
    if name == "salva_memoria":
        cat = args.get("categoria") if args.get("categoria") in CATEGORIES else "notes"
        key = _slug(str(args.get("chiave", "")))
        val = str(args.get("valore", "")).strip()
        if not val:
            return "Errore: serve il campo 'valore'.", None
        mm.remember(key, val, cat)
        return f"Memoria salvata: {cat}/{key}.", {"type": "memory_saved", "text": val}
    if name == "dimentica_memoria":
        cat = args.get("categoria") if args.get("categoria") in CATEGORIES else "notes"
        key = _slug(str(args.get("chiave", "")))
        res = mm.forget(key, cat)
        ok = res.startswith("Forgotten")
        return ("Memoria cancellata." if ok else "Nessuna memoria con quella chiave."), ({"type": "memory_removed"} if ok else None)
    if name == "cerca_memoria":
        return mm.search_memory(str(args.get("query", "")), limit=8) or "Nessun risultato.", None
    return f"Strumento sconosciuto: {name}", None


def memory_prompt() -> str:
    try:
        block = mm.format_memory_for_prompt(mm.load_memory())
    except Exception:
        block = ""
    return block or "Nessuna memoria salvata finora."


def parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}

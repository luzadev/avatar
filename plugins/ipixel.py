"""Pannello LED iPIXEL: scritte, immagini, luminosità, orologio, accensione. Lo stato di LuZa e le notifiche
compaiono da soli se l'opzione è attiva nelle impostazioni (scheda Casa)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar import ipixel  # noqa: E402


def _p():
    if ipixel.panel is None:
        raise RuntimeError("pannello iPIXEL non attivo: abilitalo nelle impostazioni (scheda Casa)")
    return ipixel.panel


def scrivi(params: dict, ctx: dict) -> str:
    testo = str(params.get("testo") or "").strip()
    if not testo:
        return "Errore: serve il testo."
    col = ipixel.NOMI_COLORI.get(str(params.get("colore") or "").lower(), None)
    durata = float(params.get("secondi") or 30)
    _p().show_text(testo, col, durata)
    return f"Sul pannello: «{testo}» per {int(durata)} secondi."


def immagine(params: dict, ctx: dict) -> str:
    path = str(params.get("percorso") or "").strip()
    if not Path(path).expanduser().is_file():
        return "Errore: file immagine non trovato."
    _p().show_image(str(Path(path).expanduser()), float(params.get("secondi") or 30))
    return "Immagine inviata al pannello."


def luminosita(params: dict, ctx: dict) -> str:
    v = max(1, min(int(params.get("livello") or 40), 100))
    _p().run_cmd([5, 0, 4, 0x80, v])
    _p().settings.set("ipixel_luminosita", v); _p().settings.save()
    return f"Luminosità del pannello al {v}%."


def accendi(params: dict, ctx: dict) -> str:
    on = str(params.get("acceso", True)).lower() not in ("false", "0", "no")
    _p().run_cmd([5, 0, 7, 1, 1 if on else 0])
    return "Pannello acceso." if on else "Pannello spento."


def orologio(params: dict, ctx: dict) -> str:
    _p().clock()
    return "Il pannello mostra l'orologio."


def stato(params: dict, ctx: dict) -> str:
    p = ipixel.panel
    if p is None:
        return "Pannello iPIXEL disattivato nelle impostazioni."
    return (f"Pannello {'collegato' if p.connected else 'non collegato'} ({p.size[0]}×{p.size[1]})" + (f", errore: {p.error}" if p.error and not p.connected else "") + ".")


def animazione(params: dict, ctx: dict) -> str:
    from avatar import ipixel_fun as fun
    import time
    nome = str(params.get("nome") or "").lower()
    p = _p()
    if nome in fun.ANIMAZIONI:
        data = fun.ANIMAZIONI[nome](p.size)
        with p._lock:
            p._want = (f"fun:{time.time()}", data, True); p._notify_until = time.time() + float(params.get("secondi") or 15)
        p._shown_key = None; p._poke()
        return f"Animazione «{nome}» sul pannello."
    kind, val = fun.scegli(p.size)
    if kind == "gif":
        with p._lock:
            p._want = (f"fun:{time.time()}", val, True); p._notify_until = time.time() + 15
        p._shown_key = None; p._poke(); return "Sorpresa animata sul pannello."
    p.show_text(str(val), None, 12)
    return f"Sul pannello: «{val}»."


TOOLS = [
    {"name": "ipixel_animazione", "description": "Mostra un'animazione sul pannello LED: plasma, matrix, stelle, cuore, equalizzatore, pacman; senza nome una sorpresa a caso (frase spiritosa, info o animazione).",
     "parameters": {"type": "object", "properties": {"nome": {"type": "string"}, "secondi": {"type": "number"}}}, "run": animazione},
    {"name": "ipixel_scrivi", "description": "Scrive un testo sul pannello LED iPIXEL (scorre se è lungo) per alcuni secondi, poi torna all'orologio. colore: rosso, verde, blu, azzurro, giallo, arancione, viola, rosa, bianco, ciano.",
     "parameters": {"type": "object", "properties": {"testo": {"type": "string"}, "colore": {"type": "string"}, "secondi": {"type": "number"}}, "required": ["testo"]}, "run": scrivi},
    {"name": "ipixel_immagine", "description": "Mostra un'immagine (percorso di un file) ridimensionata sul pannello LED.",
     "parameters": {"type": "object", "properties": {"percorso": {"type": "string"}, "secondi": {"type": "number"}}, "required": ["percorso"]}, "run": immagine},
    {"name": "ipixel_luminosita", "description": "Imposta la luminosità del pannello LED (1-100).",
     "parameters": {"type": "object", "properties": {"livello": {"type": "integer"}}, "required": ["livello"]}, "run": luminosita},
    {"name": "ipixel_accendi", "description": "Accende o spegne il pannello LED (acceso=true/false).",
     "parameters": {"type": "object", "properties": {"acceso": {"type": "boolean"}}}, "run": accendi},
    {"name": "ipixel_orologio", "description": "Riporta il pannello LED all'orologio.",
     "parameters": {"type": "object", "properties": {}}, "run": orologio},
    {"name": "ipixel_stato", "description": "Dice se il pannello LED è collegato e la sua risoluzione.",
     "parameters": {"type": "object", "properties": {}}, "run": stato},
]

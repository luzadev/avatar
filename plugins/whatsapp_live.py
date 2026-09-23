"""WhatsApp in tempo reale (dispositivo collegato): novità, stato e invio con conferma."""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.macos import confirm_or_param, CONFERMATO  # noqa: E402
from avatar import whatsapp_bridge as wb  # noqa: E402
from avatar.settings import DATA_DIR  # noqa: E402

DB = DATA_DIR / "whatsapp" / "index.sqlite"


def stato(params: dict, ctx: dict) -> str:
    return wb.status_text()


def novita(params: dict, ctx: dict) -> str:
    minuti = int(params.get("minuti") or 0)
    lim = max(1, min(int(params.get("limite") or 20), 60))
    if not DB.exists():
        return "Nessun messaggio ricevuto finora."
    since = (datetime.now() - timedelta(minutes=minuti)).strftime("%Y-%m-%d %H:%M:%S") if minuti else (datetime.now().strftime("%Y-%m-%d") + " 00:00:00")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT ts, chat, sender, text FROM messages WHERE source = 'live' AND from_me = 0 AND ts >= ? ORDER BY ts DESC LIMIT ?", (since, lim)).fetchall()
    finally:
        con.close()
    if not rows:
        return "Nessun messaggio WhatsApp nuovo" + (f" negli ultimi {minuti} minuti." if minuti else " oggi.") + (" " + wb.status_text() if not wb.running() else "")
    lines = [f"[{ts[11:16]}] {chat}{' — ' + sender if sender != chat else ''}: {text[:200]}" for ts, chat, sender, text in reversed(rows)]
    return f"Messaggi WhatsApp ricevuti ({len(rows)}):\n" + "\n".join(lines)


def invia(params: dict, ctx: dict) -> str:
    a = str(params.get("a", "")).strip()
    testo = str(params.get("testo", "")).strip()
    if not a or not testo:
        return "Errore: servono destinatario (nome del contatto o del gruppo, oppure numero) e testo."
    if not wb.running():
        wb.start()
    try:
        res = wb.resolve(a)
    except Exception as err:
        return f"WhatsApp non disponibile: {err}"
    if not res.get("jid"):
        return f"Non trovo '{a}' tra i contatti WhatsApp: prova con il numero di telefono."
    name = res.get("name") or a

    def do() -> str:
        out = wb.send(a, testo)
        msg = f"Messaggio WhatsApp inviato a {out.get('name') or name}."
        if ctx.get("say"):
            ctx["say"](msg)
        return msg

    return confirm_or_param(ctx, params, "whatsapp_invia", "Inviare su WhatsApp?", f"A: {name}\n{testo[:200]}", do)


def aggiorna_contatti(params: dict, ctx: dict) -> str:
    n = wb.sync_contacts()
    return f"Rubrica sincronizzata: {n} numeri associati ai nomi."


TOOLS = [
    {"name": "whatsapp_aggiorna_contatti", "description": "Associa i numeri WhatsApp ai nomi della rubrica del Mac (da usare se le chat compaiono come numeri).",
     "parameters": {"type": "object", "properties": {}}, "run": aggiorna_contatti},
    {"name": "whatsapp_novita", "description": "Messaggi WhatsApp ricevuti in tempo reale oggi o negli ultimi N minuti (tutte le chat). Usalo per 'ci sono novità su WhatsApp?', 'chi mi ha scritto?'.",
     "parameters": {"type": "object", "properties": {"minuti": {"type": "integer"}, "limite": {"type": "integer"}}}, "run": novita},
    {"name": "whatsapp_invia", "description": "Invia un messaggio WhatsApp a un contatto, a un gruppo o a un numero. Azione irreversibile con conferma sullo schermo; con [CONFIRMATION_PENDING] di' all'utente di confermare e non dire che è inviato.",
     "parameters": {"type": "object", "properties": {"a": {"type": "string"}, "testo": {"type": "string"}, "confermato": CONFERMATO}, "required": ["a", "testo"]}, "run": invia},
    {"name": "whatsapp_stato", "description": "Stato del collegamento WhatsApp in tempo reale.", "parameters": {"type": "object", "properties": {}}, "run": stato},
]

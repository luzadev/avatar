"""SMS e iMessage ricevuti sul Mac (app Messaggi): non letti, conversazioni, ricerca."""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.phonebook import name_for  # noqa: E402

DB = Path.home() / "Library" / "Messages" / "chat.db"
EPOCH = datetime(2001, 1, 1)


def _ts(raw) -> datetime:
    if raw is None:
        return EPOCH
    raw = int(raw)
    if raw > 10**12:          # nanosecondi
        raw //= 10**9
    return EPOCH + timedelta(seconds=raw)


def decode_body(blob: bytes | None) -> str:
    """Estrae il testo da attributedBody (archivio NSAttributedString)."""
    if not blob:
        return ""
    i = blob.find(b"NSString")
    if i < 0:
        return ""
    j = blob.find(b"+", i)
    if j < 0:
        return ""
    k = j + 1
    length = blob[k]
    k += 1
    if length == 0x81:
        length = int.from_bytes(blob[k:k + 2], "little"); k += 2
    elif length == 0x82:
        length = int.from_bytes(blob[k:k + 3], "little"); k += 3
    return blob[k:k + length].decode("utf-8", errors="replace")


def _query(where: str, params: tuple, limit: int):
    if not DB.exists():
        raise RuntimeError("Database di Messaggi non trovato.")
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    except sqlite3.OperationalError as err:
        raise RuntimeError("Non posso leggere Messaggi: concedi «Accesso completo al disco» all'app in Impostazioni di Sistema > Privacy e sicurezza.") from err
    try:
        rows = con.execute(f"""SELECT m.ROWID, m.text, m.attributedBody, m.date, m.is_from_me, m.is_read, m.service, COALESCE(h.id, ''), COALESCE(m.cache_roomnames, '')
            FROM message m LEFT JOIN handle h ON h.ROWID = m.handle_id WHERE 1=1 {where} ORDER BY m.date DESC LIMIT ?""", (*params, limit)).fetchall()
    except sqlite3.OperationalError as err:
        if "unable to open" in str(err) or "authorization" in str(err):
            raise RuntimeError("Non posso leggere Messaggi: concedi «Accesso completo al disco» all'app in Impostazioni di Sistema > Privacy e sicurezza.") from err
        raise
    finally:
        con.close()
    out = []
    for rid, text, body, date, from_me, is_read, service, handle, room in rows:
        t = (text or "").strip() or decode_body(body).strip()
        if not t:
            continue
        out.append({"id": rid, "testo": t, "data": _ts(date), "mio": bool(from_me), "letto": bool(is_read), "servizio": service or "?",
                    "chi": name_for(handle), "handle": handle, "gruppo": bool(room)})
    return out


def _fmt(rows) -> str:
    return "\n".join(f"- [{r['data']:%d/%m %H:%M}] {'io → ' + r['chi'] if r['mio'] else r['chi']} ({r['servizio']}): {r['testo'][:220]}" for r in rows)


def non_letti(params: dict, ctx: dict) -> str:
    lim = max(1, min(int(params.get("limite") or 15), 50))
    rows = _query("AND m.is_from_me = 0 AND m.is_read = 0", (), lim)
    return f"Messaggi non letti ({len(rows)}):\n{_fmt(rows)}" if rows else "Nessun SMS o iMessage non letto."


def recenti(params: dict, ctx: dict) -> str:
    lim = max(1, min(int(params.get("limite") or 15), 60))
    chi = str(params.get("contatto", "")).strip()
    ore = int(params.get("ore") or 0)
    where, args = "", []
    if ore:
        since = int((datetime.now() - timedelta(hours=ore) - EPOCH).total_seconds()) * 10**9
        where += " AND m.date >= ?"; args.append(since)
    rows = _query(where, tuple(args), 400 if chi else lim)
    if chi:
        q = chi.lower()
        rows = [r for r in rows if q in r["chi"].lower() or q in r["handle"].lower()][:lim]
    rows = rows[:lim]
    if not rows:
        return "Nessun messaggio trovato."
    return f"Messaggi{' con ' + chi if chi else ''} (dal più recente):\n{_fmt(rows)}"


def cerca(params: dict, ctx: dict) -> str:
    q = str(params.get("query", "")).strip().lower()
    if not q:
        return "Errore: serve la query."
    rows = [r for r in _query("", (), 3000) if q in r["testo"].lower()][:20]
    return f"Messaggi con '{q}':\n{_fmt(rows)}" if rows else f"Nessun messaggio contiene '{q}'."


TOOLS = [
    {"name": "sms_non_letti", "description": "SMS e iMessage non letti ricevuti sul Mac (app Messaggi), con mittente risolto dalla rubrica.",
     "parameters": {"type": "object", "properties": {"limite": {"type": "integer"}}}, "run": non_letti},
    {"name": "sms_recenti", "description": "Ultimi SMS/iMessage, di tutti o di un contatto (nome o numero), opzionalmente nelle ultime N ore.",
     "parameters": {"type": "object", "properties": {"contatto": {"type": "string"}, "limite": {"type": "integer"}, "ore": {"type": "integer"}}}, "run": recenti},
    {"name": "sms_cerca", "description": "Cerca una parola negli SMS/iMessage.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, "run": cerca},
]

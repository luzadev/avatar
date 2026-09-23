"""Telegram con il tuo account: chat non lette, lettura, ricerca e invio (con conferma)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.macos import confirm_or_param, CONFERMATO  # noqa: E402
from avatar import telegram_client as tg  # noqa: E402


def _name(entity) -> str:
    for attr in ("title",):
        if getattr(entity, attr, None):
            return entity.title
    return f"{getattr(entity, 'first_name', '') or ''} {getattr(entity, 'last_name', '') or ''}".strip() or getattr(entity, "username", "") or "?"


def _preview(msg) -> str:
    t = (msg.message or "").strip() if msg else ""
    if not t and msg is not None:
        t = "[media]" if getattr(msg, "media", None) else ""
    return " ".join(t.split())[:140]


def non_letti(params: dict, ctx: dict) -> str:
    lim = max(1, min(int(params.get("limite") or 10), 30))

    async def go(client):
        out, total = [], 0
        async for d in client.iter_dialogs(limit=100):
            if d.unread_count > 0 and not d.is_channel or (d.is_channel and d.is_group and d.unread_count > 0):
                total += d.unread_count
                sender = ""
                if d.message and d.is_group:
                    try:
                        s = await d.message.get_sender()
                        sender = _name(s) + ": "
                    except Exception:
                        pass
                out.append(f"- {d.name} ({d.unread_count} non lett{'o' if d.unread_count == 1 else 'i'}): {sender}{_preview(d.message)}")
                if len(out) >= lim:
                    break
        return out, total

    out, total = tg.run(go)
    if not out:
        return "Nessun messaggio Telegram non letto (canali esclusi)."
    return f"Telegram, {total} messaggi non letti:\n" + "\n".join(out) + "\nPer leggere una chat usa telegram_leggi con il nome."


async def _find_dialog(client, name: str):
    name_l = name.lower()
    best = None
    async for d in client.iter_dialogs(limit=300):
        n = (d.name or "").lower()
        if n == name_l:
            return d
        if name_l in n and best is None:
            best = d
    if best is None:
        try:
            ent = await client.get_entity(name)
            return ent
        except Exception:
            return None
    return best


def leggi(params: dict, ctx: dict) -> str:
    chat = str(params.get("chat", "")).strip()
    n = max(1, min(int(params.get("numero") or 10), 40))
    if not chat:
        return "Errore: serve il nome della chat o del contatto."

    async def go(client):
        d = await _find_dialog(client, chat)
        if d is None:
            return None
        entity = getattr(d, "entity", d)
        lines = []
        async for m in client.iter_messages(entity, limit=n):
            who = "tu" if m.out else _name(await m.get_sender()) if m.sender_id else "?"
            lines.append(f"[{m.date.astimezone():%d/%m %H:%M}] {who}: {_preview(m)}")
        try:
            await client.send_read_acknowledge(entity)
        except Exception:
            pass
        return getattr(d, "name", None) or _name(entity), list(reversed(lines))

    res = tg.run(go)
    if res is None:
        return f"Nessuna chat trovata per '{chat}'."
    name, lines = res
    return f"Chat «{name}», ultimi messaggi:\n" + "\n".join(lines)


def cerca(params: dict, ctx: dict) -> str:
    q = str(params.get("query", "")).strip()
    if not q:
        return "Errore: serve la query."

    async def go(client):
        lines = []
        async for m in client.iter_messages(None, search=q, limit=15):
            try:
                chat = _name(await m.get_chat())
            except Exception:
                chat = "?"
            lines.append(f"[{m.date.astimezone():%d/%m %H:%M}] {chat}: {_preview(m)}")
        return lines

    lines = tg.run(go)
    return f"Messaggi Telegram con '{q}':\n" + "\n".join(lines) if lines else f"Nessun messaggio trovato per '{q}'."


def invia(params: dict, ctx: dict) -> str:
    chat = str(params.get("chat", "")).strip()
    testo = str(params.get("testo", "")).strip()
    if not chat or not testo:
        return "Errore: servono destinatario (nome della chat, @username o numero) e testo."

    async def resolve(client):
        d = await _find_dialog(client, chat)
        if d is None:
            return None
        return getattr(d, "name", None) or _name(d)

    name = tg.run(resolve)
    if name is None:
        return f"Non trovo '{chat}' tra le tue chat Telegram."

    def do() -> str:
        async def send(client):
            d = await _find_dialog(client, chat)
            await client.send_message(getattr(d, "entity", d), testo)
            return f"Messaggio Telegram inviato a {name}."
        msg = tg.run(send)
        if ctx.get("say"):
            ctx["say"](msg)
        return msg

    return confirm_or_param(ctx, params, "telegram_invia", "Inviare su Telegram?", f"A: {name}\n{testo[:200]}", do)


TOOLS = [
    {"name": "telegram_non_letti", "description": "Elenca le chat Telegram con messaggi non letti e l'ultimo messaggio di ciascuna.",
     "parameters": {"type": "object", "properties": {"limite": {"type": "integer"}}}, "run": non_letti},
    {"name": "telegram_leggi", "description": "Legge gli ultimi messaggi di una chat o di un contatto Telegram (per nome) e li segna come letti.",
     "parameters": {"type": "object", "properties": {"chat": {"type": "string"}, "numero": {"type": "integer"}}, "required": ["chat"]}, "run": leggi},
    {"name": "telegram_cerca", "description": "Cerca messaggi in tutte le chat Telegram per parola.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, "run": cerca},
    {"name": "telegram_invia", "description": "Invia un messaggio Telegram a un contatto o a un gruppo (per nome o @username). Azione irreversibile con conferma sullo schermo; con [CONFIRMATION_PENDING] di' all'utente di confermare e non dire che è inviato.",
     "parameters": {"type": "object", "properties": {"chat": {"type": "string"}, "testo": {"type": "string"}, "confermato": CONFERMATO}, "required": ["chat", "testo"]}, "run": invia},
]

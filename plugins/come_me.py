"""Rispondere come l'utente e stimare il suo umore dai messaggi che scrive (WhatsApp e Telegram).

- stile: profilo del modo di scrivere dell'utente (globale e per contatto) ricavato dai suoi messaggi, con esempi.
- come_me_contesto: coda della conversazione + profilo di stile, perché il modello scriva la risposta in prima persona
  come l'utente e la invii con whatsapp_invia / telegram_invia (che chiedono conferma).
- umore: analisi periodica dei messaggi scritti dall'utente (tono, energia, temi) salvata in data/umore.json;
  la riga "umore recente" entra nel prompt di LuZa solo se l'opzione è attiva nelle impostazioni.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.settings import DATA_DIR, Settings  # noqa: E402
from avatar import quick_llm  # noqa: E402

WA_DB = DATA_DIR / "whatsapp" / "index.sqlite"
STILE = DATA_DIR / "stile.json"
UMORE = DATA_DIR / "umore.json"


def _names() -> list[str]:
    s = Settings()
    raw = str(s.get("io_nomi") or "Luciano")
    return [n.strip().lower() for n in raw.split(",") if n.strip()]


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


# ── messaggi dell'utente ─────────────────────────────────────────────────────
def _chat_names(query: str) -> list[str]:
    """Nomi di chat reali che corrispondono a uno dei nomi in `query` (separati da |), confrontati senza emoji/punteggiatura."""
    if not WA_DB.exists():
        return []
    keys = [re.sub(r"[^a-z0-9]", "", q.lower()) for q in query.split("|") if q.strip()]
    keys = [k for k in keys if k]
    con = sqlite3.connect(f"file:{WA_DB}?mode=ro", uri=True, timeout=5)
    try:
        names = [r[0] for r in con.execute("SELECT DISTINCT chat FROM messages WHERE chat IS NOT NULL").fetchall()]
    finally:
        con.close()
    out = []
    for n in names:
        nk = re.sub(r"[^a-z0-9]", "", n.lower())
        if any(k == nk or (len(k) >= 3 and k in nk) for k in keys):
            out.append(n)
    return out


def _wa_mine(chat: str = "", since: float | None = None, limit: int = 400) -> list[dict]:
    if not WA_DB.exists():
        return []
    names = _names()
    con = sqlite3.connect(f"file:{WA_DB}?mode=ro", uri=True, timeout=5)
    try:
        q = "SELECT chat, ts, sender, text, from_me FROM messages WHERE text != '' AND (from_me = 1 OR lower(sender) IN (%s))" % ",".join("?" * len(names))
        args: list = list(names)
        if chat:
            names_chat = _chat_names(chat)
            if not names_chat:
                return []
            q += " AND chat IN (" + ",".join("?" * len(names_chat)) + ")"; args += names_chat
        if since:
            q += " AND ts >= ?"; args.append(datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M"))
        q += " ORDER BY ts DESC LIMIT ?"; args.append(limit)
        rows = con.execute(q, args).fetchall()
    finally:
        con.close()
    return [{"canale": "whatsapp", "chat": c or "", "ts": ts or "", "testo": t} for c, ts, s, t, fm in rows if not str(t).startswith(("<", "[")) and len(str(t)) > 1]


def _wa_tail(chat: str, n: int = 20) -> list[dict]:
    if not WA_DB.exists():
        return []
    names = _names()
    con = sqlite3.connect(f"file:{WA_DB}?mode=ro", uri=True, timeout=5)
    try:
        names_chat = _chat_names(chat)
        if not names_chat:
            return []
        rows = con.execute("SELECT chat, ts, sender, text, from_me FROM messages WHERE chat IN (" + ",".join("?" * len(names_chat)) + ") AND text != '' ORDER BY ts DESC LIMIT ?", (*names_chat, n)).fetchall()
    finally:
        con.close()
    out = []
    for c, ts, s, t, fm in reversed(rows):
        mine = bool(fm) or (s or "").lower() in names
        out.append({"chi": "io" if mine else (s or c), "ts": ts, "testo": t})
    return out


def _tg_mine(since: float | None, limit_dialogs: int = 25) -> list[dict]:
    try:
        from avatar import telegram_client as tg
    except Exception:
        return []
    cutoff = datetime.fromtimestamp(since) if since else None

    async def go(client):
        out = []
        async for d in client.iter_dialogs(limit=limit_dialogs):
            if d.is_channel and not d.is_group:
                continue
            async for m in client.iter_messages(d.entity, limit=40):
                if not m.out or not (m.message or "").strip():
                    continue
                if cutoff and m.date.replace(tzinfo=None) < cutoff - timedelta(hours=2):
                    break
                out.append({"canale": "telegram", "chat": d.name or "", "ts": m.date.strftime("%Y-%m-%d %H:%M"), "testo": m.message})
        return out
    try:
        return tg.run(go)
    except Exception:
        return []


def _tg_tail(chat: str, n: int = 20) -> list[dict]:
    try:
        from avatar import telegram_client as tg
        import plugins.telegram as tplug  # noqa
    except Exception:
        tplug = None

    async def go(client):
        d = None
        name_l = chat.lower()
        async for x in client.iter_dialogs(limit=300):
            if name_l in (x.name or "").lower():
                d = x; break
        if d is None:
            return []
        out = []
        async for m in client.iter_messages(d.entity, limit=n):
            if (m.message or "").strip():
                out.append({"chi": "io" if m.out else d.name, "ts": m.date.strftime("%Y-%m-%d %H:%M"), "testo": m.message})
        return list(reversed(out))
    try:
        from avatar import telegram_client as tg
        return tg.run(go)
    except Exception as err:
        return [{"chi": "errore", "ts": "", "testo": str(err)}]


# ── stile ────────────────────────────────────────────────────────────────────
def _profilo_stile(msgs: list[dict], contatto: str = "") -> dict:
    sample = random.sample(msgs, min(len(msgs), 120))
    testo = "\n".join(f"- {m['testo'][:200]}" for m in sample)
    prompt = (f"Questi sono messaggi scritti da Luciano{' a ' + contatto if contatto else ' a varie persone'} su WhatsApp/Telegram:\n{testo}\n\n"
              "Descrivi in italiano, in 6-10 righe, il suo modo di scrivere: registro (tu/lei, formale/informale), lunghezza tipica, punteggiatura e maiuscole, "
              "uso di emoji e faccine, parole ed espressioni ricorrenti, dialetto o intercalari, come saluta e come chiude, tono abituale"
              + (", come chiama questa persona e che rapporto traspare" if contatto else "") + ". Solo la descrizione, niente premesse.")
    desc = quick_llm.ask(prompt, system="Sei un linguista che descrive lo stile di scrittura di una persona.", max_tokens=500)
    esempi = [m["testo"][:160] for m in random.sample(msgs, min(len(msgs), 25))]
    return {"descrizione": desc.strip(), "esempi": esempi, "n": len(msgs), "quando": time.strftime("%Y-%m-%d")}


def stile_impara(params: dict, ctx: dict) -> str:
    contatto = str(params.get("contatto") or "").strip()
    alias = str(params.get("alias") or "").strip()
    if ctx.get("player") is not None and hasattr(ctx["player"], "set_state"):
        ctx["player"].set_state("PROCESSING · studio il tuo stile")
    msgs = _wa_mine("|".join(x for x in (contatto, alias) if x), limit=600)
    if not contatto:
        msgs += _tg_mine(None)
    if len(msgs) < 15:
        return f"Troppo pochi messaggi tuoi {('a ' + contatto) if contatto else ''} ({len(msgs)}): servono almeno 15 per ricavare uno stile."
    prof = _profilo_stile(msgs, contatto)
    if alias:
        prof["alias"] = alias
    d = _load(STILE); d[contatto.lower() or "_globale"] = prof; _save(STILE, d)
    return f"Stile {'con ' + contatto if contatto else 'generale'} appreso da {len(msgs)} messaggi:\n{prof['descrizione']}"


def come_me_contesto(params: dict, ctx: dict) -> str:
    chat = str(params.get("chat") or "").strip()
    canale = str(params.get("canale") or "whatsapp").lower()
    if not chat:
        return "Errore: serve il nome della chat o del contatto."
    d = _load(STILE)
    prof = next((v for k, v in d.items() if k != "_globale" and k in chat.lower()), None)
    chat_q = "|".join(x for x in (chat, (prof or {}).get("alias", "")) if x)
    tail = _wa_tail(chat_q, 20) if canale == "whatsapp" else _tg_tail(chat, 20)
    if not tail:
        return f"Non trovo la conversazione '{chat}' su {canale}."
    if prof is None:
        mine = _wa_mine(chat, limit=300) if canale == "whatsapp" else []
        if len(mine) >= 15:
            prof = _profilo_stile(mine, chat); d[chat.lower()] = prof; _save(STILE, d)
    glob = d.get("_globale")
    if glob is None:
        mine = _wa_mine("", limit=600) + _tg_mine(None)
        if len(mine) >= 15:
            glob = _profilo_stile(mine); d["_globale"] = glob; _save(STILE, d)
    stile = (prof or glob or {}).get("descrizione", "stile non ancora appreso: informale, frasi brevi")
    esempi = (prof or glob or {}).get("esempi", [])[:12]
    conv = "\n".join(f"[{m['ts'][-5:] if m['ts'] else ''}] {m['chi']}: {m['testo'][:300]}" for m in tail)
    umore = _load(UMORE).get("ultimo", {})
    out = [f"Conversazione recente con {chat} ({canale}), 'io' = Luciano:", conv, "",
           f"STILE DI LUCIANO{' con questa persona' if prof else ''}: {stile}",
           "ESEMPI DI SUOI MESSAGGI: " + " | ".join(esempi)]
    if umore:
        out.append(f"UMORE RECENTE DI LUCIANO: {umore.get('tono', '')} ({umore.get('nota', '')})")
    istr = str(params.get("istruzioni") or "").strip()
    out.append("ISTRUZIONI: scrivi la risposta come se fossi Luciano, in prima persona, con il suo stile, lunghezza e tono (niente stile da assistente, niente firma)."
               + (f" Indicazioni dell'utente: {istr}." if istr else "")
               + f" Poi inviala con {'whatsapp_invia' if canale == 'whatsapp' else 'telegram_invia'} a «{chat}»: prima della conferma leggi il testo all'utente.")
    return "\n".join(out)


# ── umore ────────────────────────────────────────────────────────────────────
def analizza_umore(ore: int = 24) -> dict:
    since = time.time() - ore * 3600
    msgs = _wa_mine("", since=since, limit=300) + _tg_mine(since)
    msgs.sort(key=lambda m: m["ts"])
    if len(msgs) < 3:
        return {"tono": "", "nota": f"troppo pochi messaggi ({len(msgs)}) nelle ultime {ore} ore", "n": len(msgs), "quando": time.strftime("%Y-%m-%d %H:%M")}
    testo = "\n".join(f"[{m['ts'][5:]} {m['canale']} → {m['chat'][:18]}] {m['testo'][:220]}" for m in msgs[-120:])
    prompt = (f"Messaggi scritti da Luciano nelle ultime {ore} ore (destinatari indicati tra parentesi):\n{testo}\n\n"
              "Stima il suo stato d'animo. Rispondi SOLO con un JSON: {\"tono\": una o due parole (es. sereno, allegro, stanco, teso, giù, concentrato, irritato), "
              "\"energia\": \"bassa|media|alta\", \"nota\": una frase breve con gli indizi (temi, ritmo, orari), \"attenzione\": true se emerge malessere o stress marcato, altrimenti false}")
    raw = quick_llm.ask(prompt, system="Analizzi con delicatezza lo stato d'animo dai messaggi. Rispondi solo con il JSON richiesto.", max_tokens=300)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except Exception:
        d = {}
    res = {"tono": str(d.get("tono", "")).strip(), "energia": str(d.get("energia", "")), "nota": str(d.get("nota", "")).strip(),
           "attenzione": bool(d.get("attenzione")), "n": len(msgs), "quando": time.strftime("%Y-%m-%d %H:%M")}
    st = _load(UMORE)
    st["ultimo"] = res
    giorno = time.strftime("%Y-%m-%d")
    st.setdefault("giorni", {})[giorno] = res
    st["giorni"] = dict(sorted(st["giorni"].items())[-60:])
    _save(UMORE, st)
    return res


def umore_analizza(params: dict, ctx: dict) -> str:
    ore = max(3, min(int(params.get("ore") or 24), 168))
    if ctx.get("player") is not None and hasattr(ctx["player"], "set_state"):
        ctx["player"].set_state("PROCESSING · leggo i tuoi messaggi")
    r = analizza_umore(ore)
    if not r["tono"]:
        return r["nota"]
    return f"Umore stimato dalle ultime {ore} ore ({r['n']} messaggi tuoi): {r['tono']}, energia {r['energia']}. {r['nota']}" + (" Sembra un momento pesante: valuta se chiederglielo con tatto." if r["attenzione"] else "")


def umore_oggi(params: dict, ctx: dict) -> str:
    st = _load(UMORE)
    giorni = max(1, min(int(params.get("giorni") or 7), 60))
    if not st.get("giorni"):
        return "Nessuna stima dell'umore ancora: usa umore_analizza, oppure attiva l'opzione nelle impostazioni (scheda Monitor) per farla ogni ora."
    rows = sorted(st["giorni"].items())[-giorni:]
    lines = [f"- {g}: {r.get('tono') or '?'} (energia {r.get('energia') or '?'}) — {r.get('nota', '')}" for g, r in rows]
    return f"Umore degli ultimi {len(rows)} giorni (dai messaggi scritti da Luciano):\n" + "\n".join(lines)


def riga_prompt() -> str:
    """Riga per il prompt di LuZa, solo se l'opzione è attiva e la stima è recente (meno di 12 ore)."""
    try:
        if not Settings().get("umore_enabled"):
            return ""
        u = _load(UMORE).get("ultimo") or {}
        if not u.get("tono"):
            return ""
        if time.time() - datetime.strptime(u["quando"], "%Y-%m-%d %H:%M").timestamp() > 12 * 3600:
            return ""
        return f"Umore recente dell'utente (stimato dai suoi messaggi alle {u['quando'][-5:]}): {u['tono']}, energia {u.get('energia', '?')}. {u.get('nota', '')} Adatta il tono di conseguenza senza citare l'analisi, a meno che non te lo chieda."
    except Exception:
        return ""


TOOLS = [
    {"name": "stile_impara", "description": "Studia il modo di scrivere dell'utente dai suoi messaggi WhatsApp/Telegram (in generale, o con un contatto specifico) e lo memorizza, per poter rispondere come lui.",
     "parameters": {"type": "object", "properties": {"contatto": {"type": "string"}, "alias": {"type": "string", "description": "altro nome della stessa chat nell'archivio (es. 'My ❤️' per Valentina)"}}}, "run": stile_impara},
    {"name": "come_me_contesto", "description": "Quando l'utente chiede di rispondere a qualcuno 'come me' / 'al posto mio' su WhatsApp o Telegram: restituisce la conversazione recente con quella persona, lo stile dell'utente ed eventuali istruzioni. Poi scrivi tu la risposta in prima persona come l'utente e inviala con whatsapp_invia o telegram_invia (che chiedono conferma), leggendola prima a voce.",
     "parameters": {"type": "object", "properties": {"chat": {"type": "string"}, "canale": {"type": "string", "enum": ["whatsapp", "telegram"]}, "istruzioni": {"type": "string", "description": "cosa vuole dire l'utente, se lo ha specificato"}}, "required": ["chat"]}, "run": come_me_contesto},
    {"name": "umore_analizza", "description": "Stima lo stato d'animo dell'utente dai messaggi che ha scritto su WhatsApp e Telegram nelle ultime ore (default 24): tono, energia, indizi.",
     "parameters": {"type": "object", "properties": {"ore": {"type": "integer"}}}, "run": umore_analizza},
    {"name": "umore_oggi", "description": "Diario dell'umore stimato negli ultimi giorni (default 7): per 'come mi vedi oggi?', 'com'è andata la settimana?'.",
     "parameters": {"type": "object", "properties": {"giorni": {"type": "integer"}}}, "run": umore_oggi},
]

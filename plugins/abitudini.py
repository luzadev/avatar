"""Abitudini: ogni settimana distilla routine e ricorrenze dalle attività (richieste a LuZa, calendario, messaggi,
dispositivi di casa) in poche frasi salvate nella memoria (categoria Abitudini). Dati solo locali.
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.settings import DATA_DIR  # noqa: E402
from avatar import quick_llm  # noqa: E402
from memory import memory_manager as mm  # noqa: E402

STATO = DATA_DIR / "abitudini.json"
DIARIO = DATA_DIR / "diario.jsonl"
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
HOME = Path.home()


def _fascia(h: int) -> str:
    return "mattina presto" if h < 8 else "mattina" if h < 13 else "pomeriggio" if h < 18 else "sera" if h < 23 else "notte"


def _stato() -> dict:
    try:
        return json.loads(STATO.read_text(encoding="utf-8"))
    except Exception:
        return {}


def da_aggiornare() -> bool:
    return time.time() - float(_stato().get("ultimo", 0)) > 7 * 86400


# ── sorgenti ─────────────────────────────────────────────────────────────────
def _richieste(giorni: int) -> list[dict]:
    """Richieste a LuZa: diario + sessioni Claude Code di LuZa (cartella home) come storico iniziale."""
    since = datetime.now() - timedelta(days=giorni)
    out = []
    if DIARIO.exists():
        for line in DIARIO.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line); ts = datetime.strptime(d["ts"], "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if ts >= since:
                out.append({"ts": ts, "testo": d.get("testo", ""), "strumenti": d.get("strumenti", [])})
    try:
        import claude_sessioni as cs
        for root in cs._roots():
            pdir = root / "projects" / ("-" + str(HOME).strip("/").replace("/", "-"))
            if not pdir.is_dir():
                continue
            for f in pdir.glob("*.jsonl"):
                if datetime.fromtimestamp(f.stat().st_mtime) < since:
                    continue
                try:   # solo le sessioni di LuZa (usano gli strumenti mcp__avatar), non quelle del Terminale nella home
                    if b"mcp__avatar" not in f.read_bytes():
                        continue
                except Exception:
                    continue
                for role, text, ts in cs._messages(f):
                    if role != "user" or not ts or "<system-reminder" in text or text.startswith("[Image") or "[Request interrupted" in text:
                        continue
                    try:
                        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
                    except Exception:
                        continue
                    if t >= since and len(text) < 400:
                        out.append({"ts": t, "testo": text, "strumenti": []})
    except Exception:
        pass
    return out


def _calendario(settimane: int) -> list[dict]:
    try:
        import calendario as cal
        end = datetime.now(); start = end - timedelta(weeks=settimane)
        return [{"titolo": str(e.title()), "start": cal._pydate(e.startDate())} for e in cal._events_between(start, end)]
    except Exception:
        return []


def _messaggi(giorni: int) -> list[dict]:
    try:
        import come_me
        since = time.time() - giorni * 86400
        rows = come_me._wa_mine("", since=since, limit=2000) + come_me._tg_mine(since)
        out = []
        for r in rows:
            try:
                out.append({"ts": datetime.strptime(r["ts"][:16], "%Y-%m-%d %H:%M"), "chat": r["chat"], "canale": r["canale"]})
            except Exception:
                pass
        return out
    except Exception:
        return []


def _casa(giorni: int) -> list[dict]:
    try:
        import home_assistant as ha
        start = (datetime.now() - timedelta(days=giorni)).astimezone().isoformat()
        rows = ha.api(f"/api/logbook/{start}") or []
        out = []
        for r in rows:
            eid = r.get("entity_id", "")
            if eid.split(".")[0] not in ("light", "switch", "media_player", "climate", "cover", "scene", "script") or not r.get("context_user_id"):
                continue
            try:
                out.append({"ts": datetime.fromisoformat(r["when"]).astimezone().replace(tzinfo=None), "nome": r.get("name") or eid, "stato": r.get("state") or r.get("message", "")})
            except Exception:
                pass
        return out
    except Exception:
        return []


# ── statistiche compatte ─────────────────────────────────────────────────────
def riassunto_dati(giorni: int = 60) -> str:
    parti = []
    req = _richieste(giorni)
    if req:
        ore = Counter(_fascia(r["ts"].hour) for r in req); gg = Counter(GIORNI[r["ts"].weekday()] for r in req)
        strum = Counter(s for r in req for s in r["strumenti"])
        temi = Counter()
        for r in req:
            for w in re.findall(r"[a-zàèéìòù]{5,}", r["testo"].lower()):
                temi[w] += 1
        stop = {"della", "delle", "degli", "questo", "questa", "quello", "quella", "anche", "sempre", "fammi", "dimmi", "puoi", "vorrei", "grazie", "cosa", "come", "quando", "perché"}
        temi = [(w, c) for w, c in temi.most_common(40) if w not in stop][:15]
        parti.append(f"RICHIESTE A LUZA negli ultimi {giorni} giorni: {len(req)} (prime: {req[0]['ts']:%d/%m} … ultime: {req[-1]['ts']:%d/%m}).\n"
                     f"Per fascia oraria: {dict(ore.most_common())}. Per giorno: {dict(gg.most_common())}.\n"
                     f"Strumenti più usati: {dict(strum.most_common(10))}. Parole più frequenti nelle richieste: {', '.join(f'{w} ({c})' for w, c in temi)}.\n"
                     "Esempi di richieste tipiche: " + " | ".join(r["testo"][:70] for r in req[-40::4]))
    ev = _calendario(8)
    if ev:
        per_t = defaultdict(list)
        for e in ev:
            per_t[re.sub(r"\d+", "#", e["titolo"].strip().lower())[:40]].append(e["start"])
        ric = [(t, ds) for t, ds in per_t.items() if len(ds) >= 3]
        ric.sort(key=lambda x: -len(x[1]))
        righe = [f"«{t}» {len(ds)} volte, di solito {Counter(GIORNI[d.weekday()] for d in ds).most_common(1)[0][0]} alle {Counter(d.hour for d in ds).most_common(1)[0][0]:02d}" for t, ds in ric[:12]]
        parti.append(f"CALENDARIO (8 settimane, {len(ev)} eventi). Ricorrenti: " + ("; ".join(righe) if righe else "nessuno con 3+ occorrenze") + ". Altri titoli recenti: " + ", ".join(sorted({e['titolo'][:30] for e in ev[-15:]})))
    msg = _messaggi(min(giorni, 45))
    if msg:
        ore = Counter(_fascia(m["ts"].hour) for m in msg); chats = Counter(m["chat"] for m in msg)
        primo = Counter(m["ts"].hour for m in msg if m["ts"].hour < 12).most_common(1)
        ultimo = Counter(m["ts"].hour for m in msg if m["ts"].hour >= 18).most_common(1)
        parti.append(f"MESSAGGI SCRITTI DALL'UTENTE (WhatsApp/Telegram, {len(msg)}): per fascia {dict(ore.most_common())}; persone più sentite: {', '.join(f'{c} ({n})' for c, n in chats.most_common(8))}; "
                     f"prima ora del mattino più frequente: {primo[0][0] if primo else '?'}; ora serale più frequente: {ultimo[0][0] if ultimo else '?'}; per giorno: {dict(Counter(GIORNI[m['ts'].weekday()] for m in msg).most_common())}.")
    casa = _casa(14)
    if casa:
        per_d = defaultdict(list)
        for c in casa:
            per_d[c["nome"]].append(c["ts"].hour)
        righe = [f"{n}: {len(h)} azioni, ore tipiche {sorted(Counter(h).most_common(3))}" for n, h in sorted(per_d.items(), key=lambda kv: -len(kv[1]))[:10]]
        parti.append("CASA (14 giorni, azioni manuali su dispositivi): " + "; ".join(righe))
    return "\n\n".join(parti) if parti else ""


def aggiorna(giorni: int = 60) -> list[str]:
    dati = riassunto_dati(giorni)
    if not dati:
        return []
    prompt = (f"Dati sulle attività di Luciano:\n\n{dati}\n\n"
              "Ricava le sue ABITUDINI e routine ricorrenti (orari tipici della giornata, cosa chiede di solito e quando, giorni particolari, persone sentite regolarmente, "
              "impegni ricorrenti, dispositivi di casa usati a certe ore). Solo ciò che è davvero ricorrente e utile a un assistente per anticipare; niente ovvietà né dati sensibili. "
              "Rispondi SOLO con un JSON: una lista di oggetti {\"chiave\": breve_identificatore_snake_case, \"abitudine\": frase in italiano di massimo 25 parole}. Da 4 a 12 elementi.")
    raw = quick_llm.ask(prompt, system="Analista discreto delle abitudini. Rispondi solo con il JSON richiesto.", max_tokens=900)
    m = re.search(r"\[.*\]", raw, re.S)
    try:
        items = json.loads(m.group(0)) if m else []
    except Exception:
        items = []
    items = [it for it in items if isinstance(it, dict) and it.get("abitudine")]
    if not items:
        return []
    mem = mm.load_memory()
    for k in list((mem.get("habits") or {}).keys()):
        if k.startswith("auto_"):
            mm.forget(k, "habits")
    out = []
    for it in items[:12]:
        key = "auto_" + re.sub(r"[^a-z0-9]+", "_", str(it.get("chiave", "")).lower()).strip("_")[:40]
        mm.remember(key, str(it["abitudine"]).strip()[:220], "habits")
        out.append(str(it["abitudine"]).strip())
    STATO.parent.mkdir(parents=True, exist_ok=True)
    STATO.write_text(json.dumps({"ultimo": time.time(), "quando": time.strftime("%Y-%m-%d %H:%M"), "abitudini": out, "dati": dati[:4000]}, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def t_aggiorna(params: dict, ctx: dict) -> str:
    if ctx.get("player") is not None and hasattr(ctx["player"], "set_state"):
        ctx["player"].set_state("PROCESSING · studio le tue abitudini")
    out = aggiorna(max(14, min(int(params.get("giorni") or 60), 180)))
    if not out:
        return "Non ho abbastanza dati per ricavare abitudini (servono richieste, calendario o messaggi degli ultimi giorni)."
    return "Abitudini aggiornate e salvate in memoria:\n" + "\n".join(f"- {a}" for a in out)


def t_elenca(params: dict, ctx: dict) -> str:
    mem = mm.load_memory().get("habits") or {}
    if not mem:
        return "Nessuna abitudine appresa finora: usa abitudini_aggiorna."
    st = _stato()
    lines = [f"- {mm._entry_value(v)}" for k, v in mem.items()]
    return f"Abitudini in memoria ({len(lines)}, aggiornate il {st.get('quando', '?')}):\n" + "\n".join(lines)


def t_dati(params: dict, ctx: dict) -> str:
    d = riassunto_dati(max(14, min(int(params.get("giorni") or 60), 180)))
    return d or "Nessun dato disponibile."


TOOLS = [
    {"name": "abitudini_aggiorna", "description": "Ricava adesso le abitudini dell'utente dalle attività recenti (richieste, calendario, messaggi, casa) e le salva in memoria, sostituendo quelle apprese in automatico. Normalmente avviene da sola ogni settimana.",
     "parameters": {"type": "object", "properties": {"giorni": {"type": "integer"}}}, "run": t_aggiorna},
    {"name": "abitudini_elenca", "description": "Elenca le abitudini dell'utente salvate in memoria (per 'cosa sai delle mie abitudini?').",
     "parameters": {"type": "object", "properties": {}}, "run": t_elenca},
    {"name": "abitudini_dati", "description": "Statistiche grezze delle attività dell'utente (orari, giorni, strumenti, persone, eventi ricorrenti) per rispondere a domande come 'a che ora scrivo di solito?' o 'cosa faccio il sabato?'.",
     "parameters": {"type": "object", "properties": {"giorni": {"type": "integer"}}}, "run": t_dati},
]

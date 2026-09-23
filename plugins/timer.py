"""Timer, sveglie e attività programmate: l'app avvisa a voce o esegue un comando all'ora stabilita, anche ogni giorno."""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.settings import DATA_DIR  # noqa: E402

FILE = DATA_DIR / "timers.json"


def load() -> list[dict]:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save(items: list[dict]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _when(params: dict) -> datetime:
    now = datetime.now()
    if params.get("minuti"):
        return now + timedelta(minutes=float(params["minuti"]))
    s = str(params.get("ora", "")).strip().replace("T", " ")
    if not s:
        raise ValueError("Serve 'minuti' oppure 'ora'.")
    if len(s) <= 5:  # HH:MM oggi (o domani se già passata)
        t = datetime.strptime(s, "%H:%M").time()
        dt = datetime.combine(now.date(), t)
        return dt if dt > now else dt + timedelta(days=1)
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def imposta(params: dict, ctx: dict) -> str:
    testo = str(params.get("testo", "")).strip() or "Timer scaduto"
    try:
        at = _when(params)
    except ValueError as err:
        return f"Errore: {err}"
    item = {"id": uuid.uuid4().hex[:6], "at": at.strftime("%Y-%m-%d %H:%M"), "testo": testo,
            "ripeti": "giorno" if params.get("ogni_giorno") else "no", "comando": bool(params.get("esegui_come_comando"))}
    items = load(); items.append(item); save(items)
    quando = at.strftime("%H:%M") if at.date() == datetime.now().date() else at.strftime("%d/%m alle %H:%M")
    tipo = "comando" if item["comando"] else "avviso"
    return f"{tipo.capitalize()} programmato per le {quando}{' ogni giorno' if item['ripeti'] == 'giorno' else ''}: «{testo}» [id {item['id']}]."


def elenca(params: dict, ctx: dict) -> str:
    items = sorted(load(), key=lambda x: x["at"])
    if not items:
        return "Nessun timer o attività programmata."
    return "Programmati:\n" + "\n".join(f"- [{i['id']}] {i['at']}{' ogni giorno' if i.get('ripeti') == 'giorno' else ''}: {i['testo']}{' (comando)' if i.get('comando') else ''}" for i in items)


def annulla(params: dict, ctx: dict) -> str:
    key = str(params.get("id", "")).strip().lower()
    items = load()
    keep = [i for i in items if i["id"] != key and key not in i["testo"].lower()] if key else []
    if key and len(keep) == len(items):
        return "Nessun timer corrispondente."
    if not key:
        save([]); return "Tutti i timer annullati."
    save(keep)
    return f"Annullati {len(items) - len(keep)} timer."


def due(now: datetime | None = None) -> list[dict]:
    """Restituisce gli elementi scaduti e aggiorna il file (usato dallo scheduler dell'app)."""
    now = now or datetime.now()
    items, fired, keep = load(), [], []
    for i in items:
        try:
            at = datetime.strptime(i["at"], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if at <= now:
            fired.append(i)
            if i.get("ripeti") == "giorno":
                nxt = at + timedelta(days=1)
                while nxt <= now:
                    nxt += timedelta(days=1)
                keep.append({**i, "at": nxt.strftime("%Y-%m-%d %H:%M")})
        else:
            keep.append(i)
    if fired:
        save(keep)
    return fired


TOOLS = [
    {"name": "timer_imposta",
     "description": "Imposta un timer ('tra 10 minuti'), una sveglia/avviso a un'ora, o un'attività programmata anche giornaliera. Con esegui_come_comando=true, all'ora stabilita il testo viene eseguito come richiesta all'assistente (es. 'leggimi la posta'), altrimenti viene annunciato a voce.",
     "parameters": {"type": "object", "properties": {"testo": {"type": "string", "description": "Cosa annunciare, o il comando da eseguire."},
                                                     "minuti": {"type": "number", "description": "Fra quanti minuti."},
                                                     "ora": {"type": "string", "description": "HH:MM oppure AAAA-MM-GG HH:MM."},
                                                     "ogni_giorno": {"type": "boolean"}, "esegui_come_comando": {"type": "boolean"}}, "required": ["testo"]},
     "run": imposta},
    {"name": "timer_elenca", "description": "Elenca timer e attività programmate.", "parameters": {"type": "object", "properties": {}}, "run": elenca},
    {"name": "timer_annulla", "description": "Annulla un timer per id o parola del testo; senza id annulla tutti.",
     "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}, "run": annulla},
]

"""Promemoria di macOS: elenca, aggiunge e completa promemoria (EventKit, sincronizzati con iPhone)."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

_store = None
_lock = threading.Lock()


def _get_store():
    global _store
    with _lock:
        if _store is not None:
            return _store
        import EventKit
        store = EventKit.EKEventStore.alloc().init()
        done, res = threading.Event(), {}

        def cb(granted, err):
            res["granted"] = bool(granted)
            done.set()

        if hasattr(store, "requestFullAccessToRemindersWithCompletion_"):
            store.requestFullAccessToRemindersWithCompletion_(cb)
        else:
            store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeReminder, cb)
        done.wait(60)
        if not res.get("granted"):
            raise RuntimeError("Accesso ai Promemoria negato: concedilo in Impostazioni di Sistema > Privacy e sicurezza > Promemoria.")
        _store = store
        return store


def _parse_dt(s: str) -> datetime | None:
    s = (s or "").strip().replace("T", " ")
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Data non valida: '{s}'. Usa AAAA-MM-GG HH:MM.")


def _due(r) -> datetime | None:
    comps = r.dueDateComponents()
    if comps is None:
        return None
    try:
        return datetime(comps.year(), comps.month(), comps.day(), comps.hour() if comps.hour() != 9223372036854775807 else 0,
                        comps.minute() if comps.minute() != 9223372036854775807 else 0)
    except Exception:
        return None


def _fetch_incomplete():
    store = _get_store()
    pred = store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(None, None, None)
    done, out = threading.Event(), []

    def cb(items):
        out.extend(list(items or []))
        done.set()

    store.fetchRemindersMatchingPredicate_completion_(pred, cb)
    done.wait(30)
    return out


def _fmt(r) -> str:
    d = _due(r)
    when = f" (entro {d:%d/%m %H:%M})" if d and (d.hour or d.minute) else (f" (entro {d:%d/%m})" if d else "")
    return f"- {r.title()}{when} [{r.calendar().title()}]"


def elenca(params: dict, ctx: dict) -> str:
    items = _fetch_incomplete()
    quando = str(params.get("quando", "tutti")).lower()
    today = datetime.now().date()
    if quando == "oggi":
        items = [r for r in items if (d := _due(r)) and d.date() <= today]
    elif quando == "settimana":
        items = [r for r in items if (d := _due(r)) and d.date() <= today + timedelta(days=7)]
    items.sort(key=lambda r: (_due(r) or datetime.max))
    if not items:
        return "Nessun promemoria da fare" + (" per oggi." if quando == "oggi" else ".")
    return f"Promemoria da fare ({len(items)}):\n" + "\n".join(_fmt(r) for r in items[:30])


def aggiungi(params: dict, ctx: dict) -> str:
    import EventKit
    from Foundation import NSDateComponents
    store = _get_store()
    titolo = str(params.get("titolo", "")).strip()
    if not titolo:
        return "Errore: serve il titolo."
    r = EventKit.EKReminder.reminderWithEventStore_(store)
    r.setTitle_(titolo)
    lista = str(params.get("lista", "")).strip()
    cal = None
    if lista:
        for c in store.calendarsForEntityType_(EventKit.EKEntityTypeReminder):
            if c.title().lower() == lista.lower():
                cal = c
    r.setCalendar_(cal or store.defaultCalendarForNewReminders())
    scadenza = _parse_dt(str(params.get("scadenza", "")))
    if scadenza:
        comps = NSDateComponents.alloc().init()
        comps.setYear_(scadenza.year); comps.setMonth_(scadenza.month); comps.setDay_(scadenza.day)
        comps.setHour_(scadenza.hour); comps.setMinute_(scadenza.minute)
        r.setDueDateComponents_(comps)
        from Foundation import NSDate
        alarm = EventKit.EKAlarm.alarmWithAbsoluteDate_(NSDate.dateWithTimeIntervalSince1970_(scadenza.timestamp()))
        r.addAlarm_(alarm)
    if params.get("note"):
        r.setNotes_(str(params["note"]))
    ok, err = store.saveReminder_commit_error_(r, True, None)
    return f"Promemoria aggiunto: {_fmt(r)[2:]}" if ok else f"Errore: {err}"


def completa(params: dict, ctx: dict) -> str:
    titolo = str(params.get("titolo", "")).strip().lower()
    if not titolo:
        return "Errore: serve il titolo (anche parziale)."
    found = [r for r in _fetch_incomplete() if titolo in (r.title() or "").lower()]
    if not found:
        return "Nessun promemoria con quel titolo."
    if len(found) > 1 and not params.get("tutti"):
        return "Ho trovato più promemoria, dimmi quale:\n" + "\n".join(_fmt(r) for r in found)
    store = _get_store()
    for r in found:
        r.setCompleted_(True)
        store.saveReminder_commit_error_(r, True, None)
    return "Completato: " + ", ".join(r.title() for r in found)


TOOLS = [
    {"name": "promemoria_elenca", "description": "Elenca i promemoria da fare dell'app Promemoria (tutti, di oggi o della settimana).",
     "parameters": {"type": "object", "properties": {"quando": {"type": "string", "enum": ["tutti", "oggi", "settimana"]}}}, "run": elenca},
    {"name": "promemoria_aggiungi", "description": "Aggiunge un promemoria (o una voce a una lista, per esempio 'Spesa'). Converti 'domani alle 17' in AAAA-MM-GG HH:MM usando la data del prompt.",
     "parameters": {"type": "object", "properties": {"titolo": {"type": "string"}, "scadenza": {"type": "string", "description": "AAAA-MM-GG HH:MM, facoltativa."},
                                                     "lista": {"type": "string", "description": "Nome della lista (facoltativo)."}, "note": {"type": "string"}}, "required": ["titolo"]},
     "run": aggiungi},
    {"name": "promemoria_completa", "description": "Segna come fatto un promemoria cercandolo per titolo.",
     "parameters": {"type": "object", "properties": {"titolo": {"type": "string"}, "tutti": {"type": "boolean", "description": "Completa tutti quelli che corrispondono."}}, "required": ["titolo"]},
     "run": completa},
]

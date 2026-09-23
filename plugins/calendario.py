"""Calendario di macOS: legge, crea, sposta e cancella eventi (EventKit)."""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

_store = None
_lock = threading.Lock()
GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
MESI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]


def _get_store():
    """EKEventStore con accesso richiesto una sola volta."""
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

        if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
            store.requestFullAccessToEventsWithCompletion_(cb)
        else:
            store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeEvent, cb)
        done.wait(60)
        if not res.get("granted"):
            raise RuntimeError("Accesso al Calendario negato: concedilo in Impostazioni di Sistema > Privacy e sicurezza > Calendari.")
        _store = store
        return store


def _nsdate(dt: datetime):
    from Foundation import NSDate
    return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


def _pydate(nsdate) -> datetime:
    return datetime.fromtimestamp(nsdate.timeIntervalSince1970())


def _parse_day(s: str) -> date:
    s = (s or "").strip().lower()
    today = date.today()
    if s in ("", "oggi", "today"):
        return today
    if s in ("domani", "tomorrow"):
        return today + timedelta(days=1)
    if s in ("dopodomani",):
        return today + timedelta(days=2)
    if s in ("ieri",):
        return today - timedelta(days=1)
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _parse_dt(s: str) -> datetime:
    s = (s or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Data e ora non valide: '{s}'. Usa il formato AAAA-MM-GG HH:MM.")


def _fmt(e) -> str:
    start, end = _pydate(e.startDate()), _pydate(e.endDate())
    day = f"{GIORNI[start.weekday()]} {start.day} {MESI[start.month - 1]}"
    when = "tutto il giorno" if e.isAllDay() else f"{start:%H:%M}–{end:%H:%M}"
    extra = f" @ {e.location()}" if e.location() else ""
    return f"{day} {when}: {e.title()} ({e.calendar().title()}){extra}"


def _events_between(start: datetime, end: datetime):
    store = _get_store()
    pred = store.predicateForEventsWithStartDate_endDate_calendars_(_nsdate(start), _nsdate(end), None)
    evs = list(store.eventsMatchingPredicate_(pred) or [])
    evs.sort(key=lambda e: e.startDate().timeIntervalSince1970())
    return evs


def eventi(params: dict, ctx: dict) -> str:
    da = _parse_day(params.get("da", ""))
    a = _parse_day(params.get("a", "")) if params.get("a") else da
    if a < da:
        a = da
    evs = _events_between(datetime.combine(da, datetime.min.time()), datetime.combine(a + timedelta(days=1), datetime.min.time()))
    label = f"{da:%d/%m}" if da == a else f"dal {da:%d/%m} al {a:%d/%m}"
    if not evs:
        return f"Nessun evento {label}."
    return f"Eventi {label}:\n" + "\n".join(f"- {_fmt(e)}" for e in evs[:40])


def _find_calendar(store, name: str):
    import EventKit
    cals = [c for c in store.calendarsForEntityType_(EventKit.EKEntityTypeEvent) if c.allowsContentModifications()]
    if name:
        for c in cals:
            if c.title().lower() == name.lower():
                return c
        for c in cals:
            if name.lower() in c.title().lower():
                return c
    return store.defaultCalendarForNewEvents() or (cals[0] if cals else None)


def crea(params: dict, ctx: dict) -> str:
    import EventKit
    store = _get_store()
    titolo = str(params.get("titolo", "")).strip()
    if not titolo:
        return "Errore: serve il titolo dell'evento."
    start = _parse_dt(str(params.get("inizio", "")))
    all_day = bool(params.get("tutto_il_giorno", False))
    if all_day:
        start = start.replace(hour=0, minute=0)
        end = start + timedelta(days=1)
    elif params.get("fine"):
        end = _parse_dt(str(params["fine"]))
    else:
        end = start + timedelta(minutes=int(params.get("durata_minuti") or 60))
    ev = EventKit.EKEvent.eventWithEventStore_(store)
    ev.setTitle_(titolo)
    ev.setStartDate_(_nsdate(start))
    ev.setEndDate_(_nsdate(end))
    ev.setAllDay_(all_day)
    cal = _find_calendar(store, str(params.get("calendario", "")))
    if cal is None:
        return "Errore: nessun calendario modificabile disponibile."
    ev.setCalendar_(cal)
    if params.get("luogo"):
        ev.setLocation_(str(params["luogo"]))
    if params.get("note"):
        ev.setNotes_(str(params["note"]))
    ok, err = store.saveEvent_span_error_(ev, EventKit.EKSpanThisEvent, None)
    if not ok:
        return f"Errore nel salvataggio: {err}"
    return f"Evento creato: {_fmt(ev)}"


def _match(params: dict) -> tuple[list, str]:
    titolo = str(params.get("titolo", "")).strip().lower()
    giorno = _parse_day(params.get("giorno", ""))
    evs = _events_between(datetime.combine(giorno, datetime.min.time()), datetime.combine(giorno + timedelta(days=1), datetime.min.time()))
    found = [e for e in evs if titolo and titolo in (e.title() or "").lower()] if titolo else evs
    return found, f"{giorno:%d/%m}"


def elimina(params: dict, ctx: dict) -> str:
    import EventKit
    found, label = _match(params)
    if not found:
        return f"Nessun evento corrispondente il {label}."
    if len(found) > 1:
        return "Ho trovato più eventi, dimmi quale:\n" + "\n".join(f"- {_fmt(e)}" for e in found)
    ev = found[0]
    desc = _fmt(ev)

    def do() -> str:
        store = _get_store()
        ok, err = store.removeEvent_span_error_(ev, EventKit.EKSpanThisEvent, None)
        msg = f"Evento cancellato: {desc}" if ok else f"Errore nella cancellazione: {err}"
        if ctx.get("say"):
            ctx["say"](msg)
        return msg

    confirm = ctx.get("confirm")
    if confirm:
        return confirm("calendario_elimina", "Cancellare l'evento?", desc, do)
    # Senza interfaccia (server MCP): serve la conferma esplicita nei parametri.
    if str(params.get("confermato", "")).lower() in ("true", "1", "sì", "si", "yes"):
        return do()
    return f"Prima di cancellare chiedi conferma all'utente per: {desc}. Poi richiama con confermato=true."


def sposta(params: dict, ctx: dict) -> str:
    import EventKit
    found, label = _match(params)
    if not found:
        return f"Nessun evento corrispondente il {label}."
    if len(found) > 1:
        return "Ho trovato più eventi, dimmi quale:\n" + "\n".join(f"- {_fmt(e)}" for e in found)
    ev = found[0]
    new_start = _parse_dt(str(params.get("nuovo_inizio", "")))
    duration = _pydate(ev.endDate()) - _pydate(ev.startDate())
    ev.setStartDate_(_nsdate(new_start))
    ev.setEndDate_(_nsdate(new_start + duration))
    ok, err = _get_store().saveEvent_span_error_(ev, EventKit.EKSpanThisEvent, None)
    return f"Evento spostato: {_fmt(ev)}" if ok else f"Errore: {err}"


DAY = {"type": "string", "description": "Giorno: 'oggi', 'domani', 'dopodomani' oppure AAAA-MM-GG."}
TOOLS = [
    {"name": "calendario_eventi",
     "description": "Elenca gli eventi del Calendario di macOS in un giorno o in un intervallo. Usalo per domande come 'che impegni ho', 'cosa ho domani', 'sono libero venerdì'.",
     "parameters": {"type": "object", "properties": {"da": DAY, "a": {**DAY, "description": "Ultimo giorno dell'intervallo (facoltativo)."}}, "required": ["da"]},
     "run": eventi},
    {"name": "calendario_crea",
     "description": "Crea un evento nel Calendario. Converti tu le espressioni come 'domani alle 15' in data e ora assolute usando la data odierna del prompt.",
     "parameters": {"type": "object", "properties": {
         "titolo": {"type": "string"},
         "inizio": {"type": "string", "description": "Inizio, formato AAAA-MM-GG HH:MM."},
         "fine": {"type": "string", "description": "Fine, formato AAAA-MM-GG HH:MM (facoltativo)."},
         "durata_minuti": {"type": "integer", "description": "Durata in minuti se manca la fine (default 60)."},
         "tutto_il_giorno": {"type": "boolean"},
         "calendario": {"type": "string", "description": "Nome del calendario (facoltativo, altrimenti quello predefinito)."},
         "luogo": {"type": "string"}, "note": {"type": "string"}},
         "required": ["titolo", "inizio"]},
     "run": crea},
    {"name": "calendario_sposta",
     "description": "Sposta un evento esistente a un nuovo orario, mantenendone la durata.",
     "parameters": {"type": "object", "properties": {"titolo": {"type": "string", "description": "Parte del titolo."}, "giorno": DAY,
                                                     "nuovo_inizio": {"type": "string", "description": "Nuovo inizio, AAAA-MM-GG HH:MM."}},
                    "required": ["titolo", "giorno", "nuovo_inizio"]},
     "run": sposta},
    {"name": "calendario_elimina",
     "description": "Cancella un evento. Azione irreversibile: viene chiesta conferma sullo schermo; se lo strumento risponde con [CONFIRMATION_PENDING], di' all'utente di confermare sul pannello e non dire che è fatto.",
     "parameters": {"type": "object", "properties": {"titolo": {"type": "string", "description": "Parte del titolo."}, "giorno": DAY,
                                                     "confermato": {"type": "boolean", "description": "Solo senza interfaccia: true dopo la conferma esplicita dell'utente."}},
                    "required": ["titolo", "giorno"]},
     "run": elimina},
]

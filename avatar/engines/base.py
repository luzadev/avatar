"""Tipi comuni ai motori di conversazione."""
from __future__ import annotations

import json
import locale
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from avatar.settings import BASE_DIR, DATA_DIR

Event = dict[str, Any]
Emit = Callable[[Event], None]

PERSONA_FILE = BASE_DIR / "avatar" / "persona.md"


class Meter:
    """Stato dettagliato durante la generazione: "ragiono · 80 token", "genero la risposta · 120 token · 45/s".
    Emette al massimo ogni `every` secondi per non intasare l'interfaccia."""

    def __init__(self, emit: "Emit", every: float = 0.5, unit: str = "token") -> None:
        self.emit, self.every, self.unit = emit, every, unit
        self.n, self.t0, self.last, self.phase = 0, None, 0.0, ""

    def tick(self, phase: str, count: int = 1, force: bool = False) -> None:
        import time
        now = time.monotonic()
        if self.t0 is None or phase != self.phase:
            self.t0, self.n, self.phase = now, 0, phase
        self.n += count
        if not force and now - self.last < self.every:
            return
        self.last = now
        rate = self.n / max(0.2, now - self.t0)
        if phase == "thinking":
            self.emit({"type": "status", "status": "thinking", "detail": f"ragiono · {self.n} {self.unit}" if self.n else "ragiono"})
        elif self.n < 8:
            self.emit({"type": "status", "status": "responding", "detail": "genero la risposta"})
        else:
            self.emit({"type": "status", "status": "responding", "detail": f"genero la risposta · {self.n} {self.unit} · {rate:.0f}/s"})


class ChatBackend(Protocol):
    def send(self, user_text: str, emit: Emit, abort: threading.Event) -> None: ...
    def reset(self) -> None: ...


def today_label() -> str:
    try:
        locale.setlocale(locale.LC_TIME, "it_IT.UTF-8")
    except Exception:
        pass
    d = datetime.now()
    giorni = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
    mesi = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
            "agosto", "settembre", "ottobre", "novembre", "dicembre"]
    return f"{giorni[d.weekday()]} {d.day} {mesi[d.month - 1]} {d.year}, ore {d:%H:%M}"


def persona_text(assistant_name: str) -> str:
    try:
        text = PERSONA_FILE.read_text(encoding="utf-8")
    except Exception:
        text = "Sei Ava, un'assistente personale gentile e concreta. Rispondi in italiano."
    return text.replace("Ava", assistant_name or "Ava")


def user_block(user_name: str, memory_block: str) -> str:
    who = f"L'utente si chiama {user_name}." if user_name else "Non conosci ancora il nome dell'utente: chiediglielo con naturalezza e salvalo."
    return f"{who}\n\nMemoria a lungo termine sull'utente:\n{memory_block}"


class History:
    """Cronologia su disco per un motore."""

    def __init__(self, name: str) -> None:
        self.file: Path = DATA_DIR / f"conversation-{name}.json"
        self.messages: list[Any] = []
        self.meta: dict[str, Any] = {}
        try:
            d = json.loads(self.file.read_text(encoding="utf-8"))
            self.messages = list(d.get("messages", []))
            self.meta = dict(d.get("meta", {}))
        except Exception:
            pass

    def save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps({"messages": self.messages, "meta": self.meta}, ensure_ascii=False), encoding="utf-8")

    def clear(self) -> None:
        self.messages, self.meta = [], {}
        self.save()


# ── Compattazione della cronologia (motori locali) ───────────────────────────
COMPACT_AFTER = 30   # oltre questo numero di messaggi i più vecchi vengono riassunti
COMPACT_KEEP = 12    # messaggi recenti lasciati per esteso


def render_messages(msgs: list, per_msg: int = 600) -> str:
    out = []
    for m in msgs:
        role = m.get("role")
        text = str(m.get("content") or "").strip()
        if role == "user":
            out.append("Utente: " + text[:per_msg])
        elif role == "assistant":
            calls = m.get("tool_calls") or []
            if calls:
                names = ", ".join(str((c.get("function") or {}).get("name", "?")) for c in calls)
                text = (text + f" [usa strumenti: {names}]").strip()
            if text:
                out.append("Assistente: " + text[:per_msg])
        elif role == "tool":
            out.append(f"Risultato di {m.get('name', 'strumento')}: " + text[:200])
    return "\n".join(out)


def summary_prompt(previous: str, new_text: str) -> str:
    return ("Riassumi in italiano, in modo compatto (al massimo 150 parole), ciò che serve per continuare la conversazione: "
            "richieste dell'utente, cose fatte o decise, fatti e preferenze emersi, questioni aperte. Niente saluti, niente frasi generiche.\n\n"
            + (f"Riassunto precedente:\n{previous}\n\n" if previous else "")
            + f"Nuovi messaggi:\n{new_text}\n\nRiassunto aggiornato:")


def compact_history(history: "History", summarize: Callable[[str], str]) -> bool:
    """Se la cronologia è lunga, riassume i messaggi più vecchi in history.meta['summary'] e li rimuove."""
    msgs = history.messages
    if len(msgs) <= COMPACT_AFTER:
        return False
    cut = len(msgs) - COMPACT_KEEP
    while cut < len(msgs) and msgs[cut].get("role") != "user":
        cut += 1
    if cut <= 0 or cut >= len(msgs):
        return False
    text = render_messages(msgs[:cut])
    try:
        summary = (summarize(summary_prompt(str(history.meta.get("summary") or ""), text)) or "").strip()
    except Exception as err:
        print(f"[cronologia] riassunto fallito: {err}")
        return False
    if not summary:
        return False
    history.meta["summary"] = summary[:2000]
    history.messages = msgs[cut:]
    history.save()
    return True


def summary_block(history: "History") -> str:
    s = str(history.meta.get("summary") or "").strip()
    return f"\n\nRiassunto della conversazione precedente (i messaggi più vecchi sono stati compattati):\n{s}" if s else ""

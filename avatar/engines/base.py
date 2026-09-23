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

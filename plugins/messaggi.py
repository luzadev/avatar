"""Messaggi (iMessage/SMS): invia un messaggio a un numero o a un contatto, con conferma."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.macos import osa, confirm_or_param, CONFERMATO  # noqa: E402

SEND = '''on run argv
  tell application "Messages"
    set svc to 1st account whose service type = iMessage
    set b to participant (item 1 of argv) of svc
    send (item 2 of argv) to b
  end tell
  return "ok"
end run'''


def invia(params: dict, ctx: dict) -> str:
    a = str(params.get("a", "")).strip()
    testo = str(params.get("testo", "")).strip()
    if not a or not testo:
        return "Errore: servono destinatario (numero con prefisso, es. +39…, o email iMessage) e testo."
    if "@" not in a and not a.replace("+", "").replace(" ", "").isdigit():
        return "Il destinatario deve essere un numero di telefono o un'email: cerca il contatto con contatti_cerca."

    def do() -> str:
        osa(SEND, a.replace(" ", ""), testo, timeout=30)
        msg = f"Messaggio inviato a {a}."
        if ctx.get("say"):
            ctx["say"](msg)
        return msg

    return confirm_or_param(ctx, params, "messaggi_invia", "Inviare questo messaggio?", f"A: {a}\n{testo[:200]}", do)


TOOLS = [
    {"name": "messaggi_invia", "description": "Invia un iMessage a un numero di telefono (con prefisso) o a un'email. Azione irreversibile con conferma sullo schermo; con [CONFIRMATION_PENDING] di' all'utente di confermare e non dire che è inviato. Per i nomi usa prima contatti_cerca.",
     "parameters": {"type": "object", "properties": {"a": {"type": "string"}, "testo": {"type": "string"}, "confermato": CONFERMATO}, "required": ["a", "testo"]}, "run": invia},
]

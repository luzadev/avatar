"""Messaggi (iMessage/SMS): invia un messaggio a un numero o a un contatto, con conferma."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.macos import osa, confirm_or_param, CONFERMATO  # noqa: E402

SEND = '''on run argv
  set kind to item 3 of argv
  tell application "Messages"
    if kind is "sms" then
      set svc to 1st account whose service type = SMS
    else
      set svc to 1st account whose service type = iMessage
    end if
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

    servizio = str(params.get("servizio", "auto")).lower()

    def do() -> str:
        dest = a.replace(" ", "")
        try:
            osa(SEND, dest, testo, "sms" if servizio == "sms" else "imessage", timeout=30)
            via = "SMS" if servizio == "sms" else "iMessage"
        except Exception as err:
            if servizio == "auto" and "@" not in dest:
                osa(SEND, dest, testo, "sms", timeout=30)
                via = "SMS"
            else:
                raise
        msg = f"Messaggio inviato a {a} via {via}."
        if ctx.get("say"):
            ctx["say"](msg)
        return msg

    return confirm_or_param(ctx, params, "messaggi_invia", "Inviare questo messaggio?", f"A: {a}\n{testo[:200]}", do)


TOOLS = [
    {"name": "messaggi_invia", "description": "Invia un iMessage o un SMS a un numero di telefono (con prefisso) o a un'email. Azione irreversibile con conferma sullo schermo; con [CONFIRMATION_PENDING] di' all'utente di confermare e non dire che è inviato. Per i nomi usa prima contatti_cerca.",
     "parameters": {"type": "object", "properties": {"a": {"type": "string"}, "testo": {"type": "string"}, "servizio": {"type": "string", "enum": ["auto", "imessage", "sms"], "description": "auto = iMessage con ripiego su SMS."}, "confermato": CONFERMATO}, "required": ["a", "testo"]}, "run": invia},
]

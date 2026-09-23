"""Avvisi del monitor di Mail, WhatsApp e Telegram: elenco, chiusura, controllo immediato."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _mon():
    from avatar import monitor as m
    if m.monitor is None:
        raise RuntimeError("Il monitor non è attivo in questo processo.")
    return m.monitor


def elenca(params: dict, ctx: dict) -> str:
    al = _mon().pending()
    if not al:
        return "Nessun avviso aperto."
    return "Avvisi aperti:\n" + "\n".join(f"- [{a['id']}] {a['quando']} {a['fonte']} · {a['chi']} — {a['motivo']} ({a['priorita']})\n    «{a['testo'][:160]}»" for a in al)


def chiudi(params: dict, ctx: dict) -> str:
    n = _mon().close(str(params.get("id", "")).strip())
    return f"Chiusi {n} avvisi." if n else "Nessun avviso corrispondente."


def controlla(params: dict, ctx: dict) -> str:
    al = _mon().scan()
    return f"Controllo fatto: {len(al)} nuovi avvisi." + ("" if not al else "\n" + "\n".join(f"- {a['fonte']} · {a['chi']} — {a['motivo']}" for a in al))


TOOLS = [
    {"name": "attenzione_elenca", "description": "Elenca gli avvisi aperti del monitor (messaggi Mail, WhatsApp e Telegram che richiedono attenzione).",
     "parameters": {"type": "object", "properties": {}}, "run": elenca},
    {"name": "attenzione_chiudi", "description": "Chiude un avviso per id o per nome del mittente; senza parametri chiude tutti.",
     "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}, "run": chiudi},
    {"name": "attenzione_controlla", "description": "Esegue subito un controllo di Mail, WhatsApp e Telegram alla ricerca di messaggi che richiedono attenzione.",
     "parameters": {"type": "object", "properties": {}}, "run": controlla},
]

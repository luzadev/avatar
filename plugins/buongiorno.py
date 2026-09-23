"""Riepilogo della giornata: calendario, promemoria, email non lette e meteo in un colpo solo."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def riepilogo(params: dict, ctx: dict) -> str:
    from avatar.plugins import registry
    citta = str(params.get("citta", "")).strip() or "Roma"
    parts = []
    for label, name, args in (
        ("Calendario", "calendario_eventi", {"da": "oggi", "a": "domani"}),
        ("Promemoria", "promemoria_elenca", {"quando": "oggi"}),
        ("Email", "mail_non_lette", {"limite": 5}),
        ("Meteo", "meteo", {"citta": citta, "giorni": 1}),
    ):
        if not registry.has(name):
            continue
        try:
            parts.append(f"## {label}\n{registry.run(name, args)}")
        except Exception as err:
            parts.append(f"## {label}\nnon disponibile ({err})")
    return ("\n\n".join(parts) + "\n\nFai un riepilogo parlato breve e naturale: prima gli impegni, poi i promemoria, "
            "poi le email importanti (solo mittente e oggetto), infine il meteo in una frase.")


TOOLS = [
    {"name": "buongiorno", "description": "Riepilogo della giornata: impegni di oggi e domani, promemoria, email non lette e meteo. Usalo per 'buongiorno', 'come si presenta la giornata', 'riepilogo'.",
     "parameters": {"type": "object", "properties": {"citta": {"type": "string", "description": "Città per il meteo (default Roma)."}}}, "run": riepilogo},
]

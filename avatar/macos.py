"""Funzioni comuni per i plugin che parlano con macOS."""
from __future__ import annotations

import subprocess


def osa(script: str, *args: str, timeout: int = 60) -> str:
    """Esegue AppleScript; i parametri arrivano in `argv` (niente problemi di virgolette)."""
    res = subprocess.run(["osascript", "-e", script, "--", *args], capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        err = (res.stderr or "").strip()
        if "-1743" in err:
            raise RuntimeError("Permesso negato: concedilo in Impostazioni di Sistema > Privacy e sicurezza > Automazione.")
        raise RuntimeError(err.splitlines()[-1] if err else "errore AppleScript")
    return res.stdout.rstrip("\n")


def sh(*cmd: str, timeout: int = 30) -> str:
    res = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip().splitlines()[-1] if (res.stderr or res.stdout).strip() else f"{cmd[0]} ha fallito")
    return res.stdout.rstrip("\n")


def confirm_or_param(ctx: dict, params: dict, key: str, title: str, detail: str, do):
    """Conferma a schermo se c'è l'interfaccia; altrimenti richiede confermato=true."""
    confirm = ctx.get("confirm")
    if confirm:
        return confirm(key, title, detail, do)
    if str(params.get("confermato", "")).lower() in ("true", "1", "sì", "si", "yes"):
        return do()
    return f"Prima di procedere chiedi conferma all'utente per: {title} — {detail}. Poi richiama con confermato=true."


CONFERMATO = {"type": "boolean", "description": "Solo senza interfaccia: true dopo la conferma esplicita dell'utente."}

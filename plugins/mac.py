"""Controlli del Mac: volume, luminosità, non disturbare, Wi-Fi, batteria, disco, schermo, screenshot."""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from avatar.macos import osa, sh, confirm_or_param, CONFERMATO  # noqa: E402


def volume(params: dict, ctx: dict) -> str:
    az = str(params.get("azione", "imposta")).lower()
    cur = int(osa("output volume of (get volume settings)"))
    if az == "leggi":
        muted = osa("output muted of (get volume settings)") == "true"
        return f"Volume al {cur}%" + (" (silenziato)." if muted else ".")
    if az == "muto":
        osa("set volume with output muted"); return "Audio silenziato."
    if az == "riattiva":
        osa("set volume without output muted"); return "Audio riattivato."
    v = int(params.get("valore") or 0)
    if az == "alza":
        v = min(100, cur + (int(params.get("valore") or 10)))
    elif az == "abbassa":
        v = max(0, cur - (int(params.get("valore") or 10)))
    v = max(0, min(100, v))
    osa(f"set volume output volume {v}")
    return f"Volume al {v}%."


def luminosita(params: dict, ctx: dict) -> str:
    az = str(params.get("azione", "alza")).lower()
    key = 144 if az == "alza" else 145
    n = max(1, min(16, int(params.get("passi") or 4)))
    for _ in range(n):
        osa(f'tell application "System Events" to key code {key}')
    return f"Luminosità {'aumentata' if az == 'alza' else 'diminuita'}."


def non_disturbare(params: dict, ctx: dict) -> str:
    on = bool(params.get("attiva", True))
    try:
        sh("shortcuts", "run", "Non disturbare ON" if on else "Non disturbare OFF")
        return "Non disturbare " + ("attivato." if on else "disattivato.")
    except Exception:
        return ("Per comandare Non disturbare crea due Comandi Rapidi chiamati 'Non disturbare ON' e 'Non disturbare OFF' "
                "con l'azione 'Imposta full immersion'. Poi funzionerà a voce.")


def wifi(params: dict, ctx: dict) -> str:
    on = bool(params.get("attiva", True))
    try:
        dev = sh("networksetup", "-listallhardwareports")
        m = re.search(r"Hardware Port: Wi-Fi\nDevice: (\w+)", dev)
        iface = m.group(1) if m else "en0"
        sh("networksetup", "-setairportpower", iface, "on" if on else "off")
        return "Wi-Fi " + ("acceso." if on else "spento.")
    except Exception as err:
        return f"Non riesco a cambiare il Wi-Fi: {err}"


def stato(params: dict, ctx: dict) -> str:
    parts = []
    try:
        batt = sh("pmset", "-g", "batt")
        m = re.search(r"(\d+)%;\s*([^;]+);", batt)
        if m:
            st = {"charging": "in carica", "discharging": "in uso a batteria", "charged": "carica", "AC attached": "collegato"}.get(m.group(2).strip(), m.group(2).strip())
            parts.append(f"Batteria al {m.group(1)}%, {st}")
    except Exception:
        pass
    try:
        df = sh("df", "-h", "/System/Volumes/Data").splitlines()[-1].split()
        parts.append(f"Disco: {df[3]} liberi su {df[1]} ({df[4]} usato)")
    except Exception:
        pass
    try:
        up = sh("uptime")
        m = re.search(r"up\s+([^,]+(?:,\s*\d+:\d+)?)", up)
        if m:
            parts.append(f"Acceso da {m.group(1).strip()}")
    except Exception:
        pass
    try:
        wifi_name = sh("networksetup", "-getairportnetwork", "en0")
        parts.append(wifi_name.replace("Current Wi-Fi Network:", "Wi-Fi:").strip())
    except Exception:
        pass
    return ". ".join(parts) + "." if parts else "Stato non disponibile."


def schermo(params: dict, ctx: dict) -> str:
    az = str(params.get("azione", "spegni")).lower()
    if az == "spegni":
        sh("pmset", "displaysleepnow"); return "Schermo spento."
    if az == "blocca":
        osa('tell application "System Events" to keystroke "q" using {command down, control down}'); return "Mac bloccato."
    if az == "sospendi":
        return confirm_or_param(ctx, params, "mac_sospendi", "Mettere il Mac in stop?", "Il Mac andrà in stop.", lambda: (sh("pmset", "sleepnow"), "Stop.")[1])
    return "Azione sconosciuta: spegni, blocca o sospendi."


def screenshot(params: dict, ctx: dict) -> str:
    dest = Path.home() / "Desktop" / f"Screenshot {datetime.now():%Y-%m-%d %H.%M.%S}.png"
    sh("screencapture", "-x", str(dest))
    return f"Screenshot salvato sul Desktop: {dest.name}"


TOOLS = [
    {"name": "mac_volume", "description": "Volume di sistema: leggi, imposta (0-100), alza, abbassa, muto, riattiva.",
     "parameters": {"type": "object", "properties": {"azione": {"type": "string", "enum": ["leggi", "imposta", "alza", "abbassa", "muto", "riattiva"]}, "valore": {"type": "integer"}}, "required": ["azione"]}, "run": volume},
    {"name": "mac_luminosita", "description": "Alza o abbassa la luminosità dello schermo (a passi).",
     "parameters": {"type": "object", "properties": {"azione": {"type": "string", "enum": ["alza", "abbassa"]}, "passi": {"type": "integer"}}, "required": ["azione"]}, "run": luminosita},
    {"name": "mac_non_disturbare", "description": "Attiva o disattiva Non disturbare (full immersion).",
     "parameters": {"type": "object", "properties": {"attiva": {"type": "boolean"}}, "required": ["attiva"]}, "run": non_disturbare},
    {"name": "mac_wifi", "description": "Accende o spegne il Wi-Fi.",
     "parameters": {"type": "object", "properties": {"attiva": {"type": "boolean"}}, "required": ["attiva"]}, "run": wifi},
    {"name": "mac_stato", "description": "Stato del Mac: batteria, spazio su disco, tempo di accensione, rete Wi-Fi.",
     "parameters": {"type": "object", "properties": {}}, "run": stato},
    {"name": "mac_schermo", "description": "Spegne lo schermo, blocca il Mac o lo mette in stop (con conferma).",
     "parameters": {"type": "object", "properties": {"azione": {"type": "string", "enum": ["spegni", "blocca", "sospendi"]}, "confermato": CONFERMATO}, "required": ["azione"]}, "run": schermo},
    {"name": "mac_screenshot", "description": "Cattura lo schermo e salva l'immagine sul Desktop.", "parameters": {"type": "object", "properties": {}}, "run": screenshot},
]

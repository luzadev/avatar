"""Gestione del ponte WhatsApp (processo Node con Baileys) e client della sua API locale."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import requests

from avatar.settings import BASE_DIR, DATA_DIR

BRIDGE_DIR = BASE_DIR / "whatsapp_bridge"
WA_DATA = DATA_DIR / "whatsapp"
AUTH_DIR = WA_DATA / "auth"
PORT = 8790
_proc: subprocess.Popen | None = None
_lock = threading.Lock()


def node_path() -> str | None:
    for cand in (shutil.which("node"), "/opt/homebrew/bin/node", "/usr/local/bin/node", os.path.expanduser("~/.nvm/current/bin/node")):
        if cand and Path(cand).exists():
            return cand
    return None


def is_linked() -> bool:
    return (AUTH_DIR / "creds.json").exists()


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def sync_contacts() -> int:
    """Copia nomi e numeri dalla rubrica del Mac nella tabella dei contatti WhatsApp e rinomina le chat già registrate."""
    import re
    import sqlite3
    import Contacts
    store = Contacts.CNContactStore.alloc().init()
    keys = [Contacts.CNContactGivenNameKey, Contacts.CNContactFamilyNameKey, Contacts.CNContactOrganizationNameKey, Contacts.CNContactPhoneNumbersKey]
    req = Contacts.CNContactFetchRequest.alloc().initWithKeysToFetch_(keys)
    pairs: list[tuple[str, str]] = []

    def visit(contact, stop):
        name = f"{contact.givenName()} {contact.familyName()}".strip() or contact.organizationName()
        if not name:
            return
        for ph in contact.phoneNumbers():
            digits = re.sub(r"\D", "", ph.value().stringValue())
            if digits.startswith("00"):
                digits = digits[2:]
            if len(digits) == 10 and digits.startswith("3"):
                digits = "39" + digits
            if len(digits) >= 10:
                pairs.append((f"{digits}@s.whatsapp.net", name))

    ok, err = store.enumerateContactsWithFetchRequest_error_usingBlock_(req, None, visit)
    db = WA_DATA / "index.sqlite"
    con = sqlite3.connect(db, timeout=10)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS wa_contacts (jid TEXT PRIMARY KEY, name TEXT, notify TEXT)")
        con.executemany("INSERT INTO wa_contacts (jid, name, notify) VALUES (?, ?, NULL) ON CONFLICT(jid) DO UPDATE SET name = excluded.name", pairs)
        # Rinomina le chat registrate come numero
        con.execute("UPDATE messages SET chat = (SELECT name FROM wa_contacts c WHERE c.jid = messages.jid) WHERE jid IN (SELECT jid FROM wa_contacts) AND chat GLOB '[0-9]*'")
        con.execute("UPDATE messages SET sender = (SELECT name FROM wa_contacts c WHERE c.jid = messages.jid) WHERE from_me = 0 AND jid IN (SELECT jid FROM wa_contacts) AND sender GLOB '[0-9]*'")
        con.commit()
    finally:
        con.close()
    return len(pairs)


def start(me_name: str = "") -> str:
    """Avvia il ponte se non è già attivo. Restituisce un messaggio di stato."""
    global _proc
    with _lock:
        if running():
            return "Ponte WhatsApp già attivo."
        node = node_path()
        if not node:
            return "Node.js non trovato: installa Node (brew install node) per usare WhatsApp in tempo reale."
        if not (BRIDGE_DIR / "node_modules").exists():
            return "Dipendenze del ponte mancanti: esegui `npm install` in whatsapp_bridge/."
        WA_DATA.mkdir(parents=True, exist_ok=True)
        log = open(WA_DATA / "bridge.log", "a")
        _proc = subprocess.Popen([node, str(BRIDGE_DIR / "index.js"), "--data", str(WA_DATA), "--port", str(PORT), "--me", me_name or "io"],
                                 stdout=log, stderr=subprocess.STDOUT, cwd=str(BRIDGE_DIR))
        threading.Thread(target=lambda: _safe_sync(), daemon=True).start()
        for _ in range(30):
            time.sleep(0.5)
            try:
                status()
                return "Ponte WhatsApp avviato."
            except Exception:
                if _proc.poll() is not None:
                    return "Il ponte WhatsApp si è chiuso subito: controlla data/whatsapp/bridge.log."
        return "Il ponte WhatsApp non risponde."


def _safe_sync() -> None:
    try:
        time.sleep(3)
        sync_contacts()
    except Exception as err:
        print(f"[whatsapp] sincronizzazione contatti fallita: {err}")


def stop() -> None:
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            _proc.terminate()
            try:
                _proc.wait(5)
            except Exception:
                _proc.kill()
        _proc = None


def _get(path: str, **params) -> dict:
    r = requests.get(f"http://127.0.0.1:{PORT}{path}", params=params, timeout=8)
    r.raise_for_status()
    return r.json()


def status() -> dict:
    return _get("/status")


def status_text() -> str:
    if not running():
        return "Ponte non avviato." + (" Sessione salvata: si collegherà all'avvio." if is_linked() else "")
    try:
        st = status()
    except Exception as err:
        return f"Ponte non raggiungibile: {err}"
    c = st.get("connection")
    if c == "open":
        me = st.get("me") or {}
        return f"WhatsApp collegato come {me.get('name') or me.get('id') or '?'} · messaggi ricevuti in questa sessione: {st.get('received', 0)}"
    if c == "qr":
        return "In attesa della scansione del QR."
    return f"Non collegato. {st.get('error') or ''}".strip()


def qr() -> str | None:
    try:
        return status().get("qr")
    except Exception:
        return None


def qr_png() -> bytes | None:
    code = qr()
    if not code:
        return None
    import io
    import qrcode
    buf = io.BytesIO()
    qrcode.make(code).save(buf, format="PNG")
    return buf.getvalue()


def logout() -> str:
    try:
        _get("/logout")
    except Exception:
        pass
    shutil.rmtree(AUTH_DIR, ignore_errors=True)
    return "WhatsApp scollegato."


def resolve(to: str) -> dict:
    return _get("/resolve", to=to)


def send(to: str, text: str) -> dict:
    r = requests.post(f"http://127.0.0.1:{PORT}/send", json={"to": to, "text": text}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(r.json().get("error", r.text))
    return r.json()

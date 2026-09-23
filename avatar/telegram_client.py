"""Accesso Telegram con l'account dell'utente (Telethon): login e chiamate sincrone per i plugin."""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable

from avatar.settings import DATA_DIR, Settings

SESSION = DATA_DIR / "telegram"          # Telethon aggiunge .session
LOGIN_STATE = DATA_DIR / "telegram_login.json"
_lock = threading.Lock()


def credentials() -> tuple[int, str]:
    s = Settings()
    api_id = str(s.get("telegram_api_id") or "").strip()
    api_hash = s.get_secret("telegram_api_hash")
    if not api_id.isdigit() or not api_hash:
        raise RuntimeError("Telegram non configurato: inserisci api id e api hash in Motore e Voce (da my.telegram.org).")
    return int(api_id), api_hash


def run(fn: Callable[[Any], Awaitable[Any]], require_auth: bool = True) -> Any:
    """Esegue `fn(client)` in un event loop dedicato, con connessione aperta e chiusa ogni volta."""
    from telethon import TelegramClient
    api_id, api_hash = credentials()

    async def main():
        client = TelegramClient(str(SESSION), api_id, api_hash)
        await client.connect()
        try:
            if require_auth and not await client.is_user_authorized():
                raise RuntimeError("Telegram: accesso non ancora effettuato. Vai in Motore e Voce e completa l'accesso.")
            return await fn(client)
        finally:
            await client.disconnect()

    with _lock:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(main())
        finally:
            loop.close()


# ── Login in due passi (dalla finestra impostazioni) ─────────────────────────
def send_code(phone: str) -> str:
    phone = phone.strip().replace(" ", "")

    async def go(client):
        prev = None
        try:
            prev = json.loads(LOGIN_STATE.read_text())
        except Exception:
            pass
        if prev and prev.get("phone") == phone and prev.get("hash"):
            # Seconda richiesta: Telegram lo rimanda con un altro metodo (SMS o chiamata).
            try:
                from telethon.tl.functions.auth import ResendCodeRequest
                sent = await client(ResendCodeRequest(phone_number=phone, phone_code_hash=prev["hash"]))
                LOGIN_STATE.write_text(json.dumps({"phone": phone, "hash": sent.phone_code_hash}))
                via = type(sent.type).__name__.replace("SentCodeType", "")
                return f"Codice reinviato via {via or 'altro metodo'}: controlla SMS o chiamata, poi inseriscilo qui."
            except Exception as err:
                LOGIN_STATE.unlink(missing_ok=True)
                print(f"[telegram] resend fallito: {err}")
        sent = await client.send_code_request(phone)
        LOGIN_STATE.write_text(json.dumps({"phone": phone, "hash": sent.phone_code_hash}))
        via = type(sent.type).__name__.replace("SentCodeType", "")
        dove = "nell'app Telegram (messaggio dal mittente «Telegram»)" if via == "App" else f"via {via}"
        return f"Codice inviato {dove}. Inseriscilo qui; se non arriva, premi di nuovo «Invia codice» per riceverlo con un altro metodo."

    return run(go, require_auth=False)


def sign_in(code: str, password: str = "") -> str:
    try:
        st = json.loads(LOGIN_STATE.read_text())
    except Exception:
        st = None
    if st is None or (not code.strip() and password):
        if not password:
            return "Prima premi «Invia codice» (oppure usa l'accesso con QR); se Telegram chiede la password, scrivila e premi Accedi."

        async def only_password(client):
            from telethon.errors import SessionPasswordNeededError  # noqa: F401
            await client.sign_in(password=password)
            me = await client.get_me()
            LOGIN_STATE.unlink(missing_ok=True)
            return f"Accesso effettuato come {me.first_name or ''} {me.last_name or ''} (@{me.username or '—'})."

        try:
            return run(only_password, require_auth=False)
        except Exception as err:
            return f"Password non accettata: {err}"

    async def go(client):
        from telethon.errors import SessionPasswordNeededError
        try:
            await client.sign_in(st["phone"], code.strip().replace(" ", ""), phone_code_hash=st["hash"])
        except SessionPasswordNeededError:
            if not password:
                return "Serve anche la password di verifica in due passaggi: inseriscila e premi di nuovo Accedi."
            await client.sign_in(password=password)
        me = await client.get_me()
        LOGIN_STATE.unlink(missing_ok=True)
        return f"Accesso effettuato come {me.first_name or ''} {me.last_name or ''} (@{me.username or '—'})."

    return run(go, require_auth=False)


def status() -> str:
    try:
        credentials()
    except RuntimeError as err:
        return str(err)
    if not Path(str(SESSION) + ".session").exists():
        return "Credenziali presenti; accesso non ancora effettuato."

    async def go(client):
        if not await client.is_user_authorized():
            return "Accesso non ancora effettuato."
        me = await client.get_me()
        return f"Collegato come {me.first_name or ''} {me.last_name or ''} (@{me.username or '—'})."

    try:
        return run(go, require_auth=False)
    except Exception as err:
        return f"Errore: {err}"


def logout() -> str:
    async def go(client):
        await client.log_out()
        return "Disconnesso da Telegram."
    try:
        return run(go, require_auth=False)
    finally:
        Path(str(SESSION) + ".session").unlink(missing_ok=True)


# ── Accesso con codice QR (Impostazioni › Dispositivi › Collega dispositivo) ──
_qr_state: dict = {}


def qr_login(on_update: Callable[[str, bytes | None], None]) -> None:
    """Genera un QR, lo passa a `on_update(messaggio, png)` e attende la scansione (fino a 3 minuti).
    Bloccante: chiamare da un thread. A ogni scadenza del QR ne genera uno nuovo."""
    import io
    import qrcode
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    api_id, api_hash = credentials()

    async def main():
        client = TelegramClient(str(SESSION), api_id, api_hash)
        await client.connect()
        try:
            if await client.is_user_authorized():
                me = await client.get_me()
                on_update(f"Già collegato come {me.first_name or ''} (@{me.username or '—'}).", None)
                return
            pwd = _qr_state.get("password", "")
            if pwd:
                # QR già scansionato in precedenza: manca solo la password.
                try:
                    await client.sign_in(password=pwd)
                    me = await client.get_me()
                    on_update(f"Accesso effettuato come {me.first_name or ''} {me.last_name or ''} (@{me.username or '—'}).", None)
                    return
                except Exception:
                    pass
            deadline = asyncio.get_event_loop().time() + 180
            while asyncio.get_event_loop().time() < deadline:
                try:
                    qr = await client.qr_login()
                except SessionPasswordNeededError:
                    on_update("QR accettato: ora serve la password di verifica in due passaggi. Scrivila nel campo password e premi «Accedi».", None)
                    return
                buf = io.BytesIO()
                qrcode.make(qr.url).save(buf, format="PNG")
                on_update("Inquadra il QR dal telefono: Telegram › Impostazioni › Dispositivi › Collega dispositivo desktop.", buf.getvalue())
                try:
                    await qr.wait(timeout=max(5, (qr.expires - qr.expires.__class__.now(qr.expires.tzinfo)).total_seconds()))
                    break
                except asyncio.TimeoutError:
                    continue
                except SessionPasswordNeededError:
                    pwd = _qr_state.get("password", "")
                    if not pwd:
                        on_update("Serve la password di verifica in due passaggi: scrivila nel campo password e ripeti «Accesso con QR».", None)
                        return
                    await client.sign_in(password=pwd)
                    break
            if await client.is_user_authorized():
                me = await client.get_me()
                on_update(f"Accesso effettuato come {me.first_name or ''} {me.last_name or ''} (@{me.username or '—'}).", None)
            else:
                on_update("QR scaduto senza scansione. Premi di nuovo «Accesso con QR».", None)
        finally:
            await client.disconnect()

    with _lock:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(main())
        finally:
            loop.close()

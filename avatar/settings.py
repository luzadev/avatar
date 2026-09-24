"""Impostazioni dell'app: file JSON in config/, chiavi API nel portachiavi di sistema."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
DATA_DIR = BASE_DIR / "data"
KEYRING_SERVICE = "AvatarPy"

DEFAULTS: dict[str, Any] = {
    "provider": "anthropic",            # anthropic | local | claudecode
    "effort": "medium",                 # low | medium | high
    "local_base_url": "http://localhost:8000/v1",
    "local_model": "",
    "search_api_key": "",
    "claudecode_model": "sonnet",
    "claudecode_access": "chat",        # chat | read | full
    "claudecode_config_dir": "",
    "claudecode_path": "",
    "tts_engine": "kokoro",             # kokoro | system
    "kokoro_voice": "if_sara",
    "chatterbox_exaggeration": 0.6,    # 0.3 sobria … 0.9 molto enfatica
    "chatterbox_cfg": 0.3,             # più basso = più veloce e meno aderente al testo
    "elevenlabs_voice_id": "",
    "elevenlabs_model": "eleven_flash_v2_5",   # rapido; eleven_multilingual_v2 = qualità massima
    "elevenlabs_stability": 0.45,
    "elevenlabs_style": 0.3,
    "chatterbox_ref": "preset:femminile",  # preset:femminile | preset:maschile | percorso di un wav da imitare
    "system_voice": "",                 # "" = automatica
    "stt_model": "mlx-community/whisper-small-mlx",
    "vad_threshold": 0.08,
    "avatar_model": "allegra_2",       # nome file (senza .glb) in avatar3d/models
    "telegram_api_id": "",
    "whatsapp_live": False,            # avvia il ponte WhatsApp all'apertura
    "whatsapp_annuncia": False,        # annuncia a voce i messaggi in arrivo
    "monitor_enabled": False,          # monitor Mail/WhatsApp/Telegram con avvisi
    "monitor_annuncia": True,          # annuncia a voce gli avvisi
    "monitor_intervallo": 60,          # secondi tra un controllo e l'altro
    "monitor_regole": "",              # cosa merita attenzione (vuoto = regole predefinite)
    "monitor_escludi": "",             # parole/frasi (separate da virgola) che escludono un messaggio dagli avvisi
}

SECRET_KEYS = ("anthropic_api_key", "local_api_key", "telegram_api_hash", "elevenlabs_api_key")


class Settings:
    def __init__(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = dict(DEFAULTS)
        try:
            self._data.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, values: dict[str, Any]) -> None:
        for k, v in values.items():
            if k in SECRET_KEYS:
                self.set_secret(k, str(v))
            else:
                self._data[k] = v
        self.save()

    def save(self) -> None:
        SETTINGS_FILE.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(SETTINGS_FILE, 0o600)
        except OSError:
            pass

    # ── Segreti ──────────────────────────────────────────────────────────
    def get_secret(self, key: str) -> str:
        env = {"anthropic_api_key": "ANTHROPIC_API_KEY"}.get(key)
        try:
            import keyring
            value = keyring.get_password(KEYRING_SERVICE, key)
            if value:
                return value
        except Exception:
            value = self._data.get(f"_{key}")
            if value:
                return str(value)
        if env and os.environ.get(env):
            return os.environ[env]
        return str(self._data.get(f"_{key}", "") or "")

    def set_secret(self, key: str, value: str) -> None:
        value = value.strip()
        try:
            import keyring
            if value:
                keyring.set_password(KEYRING_SERVICE, key, value)
            else:
                try:
                    keyring.delete_password(KEYRING_SERVICE, key)
                except Exception:
                    pass
            self._data.pop(f"_{key}", None)
        except Exception:
            # Portachiavi non disponibile: salva nel file (permessi 600).
            if value:
                self._data[f"_{key}"] = value
            else:
                self._data.pop(f"_{key}", None)

    @property
    def ready(self) -> bool:
        p = self.get("provider")
        if p == "local":
            return bool(self.get("local_base_url") and self.get("local_model"))
        if p == "claudecode":
            return True
        return bool(self.get_secret("anthropic_api_key"))

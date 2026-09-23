"""AvatarPy: assistente personale con volto animato (interfaccia da Mark-LIV, motori propri)."""
from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from avatar.settings import CONFIG_DIR, Settings  # noqa: E402


def ensure_identity_file() -> None:
    """L'interfaccia legge config/api_keys.json per nome, colore e stato di configurazione."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    f = CONFIG_DIR / "api_keys.json"
    data: dict = {}
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    changed = False
    if not data.get("os_system"):
        data["os_system"] = {"Darwin": "mac", "Windows": "windows"}.get(platform.system(), "linux")
        changed = True
    if not data.get("assistant_name"):
        data["assistant_name"] = "Ava"
        changed = True
    if changed:
        f.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    ensure_identity_file()
    settings = Settings()
    try:
        from PyQt6 import QtWebEngineWidgets  # noqa: F401  (va importato prima della QApplication)
    except Exception as err:
        print(f"[avatar3d] WebEngine non disponibile: {err}")
    from ui import JarvisUI  # crea la QApplication
    from avatar.assistant import Assistant
    from avatar.settings_dialog import SettingsDialog

    ui = JarvisUI(str(BASE_DIR / "core" / "face_model.obj"))
    ui._win._log._ai_name_lc = ui.assistant_name.lower()
    assistant = Assistant(ui, settings)

    ui.on_text_command = assistant.handle_text
    ui.on_interrupt = assistant.interrupt
    ui.on_voice_change = lambda: assistant.reload_voice(from_customise=True)
    ui.on_audio_device_change = assistant.reopen_audio
    ui.on_push_to_talk = assistant.set_push_to_talk
    ui.ptt_hold = assistant.ptt_hold
    ui.on_wake_toggle = assistant.on_wake_toggle
    ui.on_wake_manual = assistant.on_wake_manual
    ui.wake_get_state = assistant.wake_get_state
    from avatar.plugins import registry
    from core import confirm
    confirm.bind(ui.show_confirm, ui.hide_confirm, ui.write_log)
    registry.ctx = {"say": assistant.say, "log": ui.write_log, "confirm": confirm.request, "player": ui}
    ui.get_plugins = registry.list_for_ui
    ui.get_plugin_settings = lambda: []
    ui.request_say = assistant.say
    # Questi due sono attributi della finestra, non della facciata JarvisUI.
    ui._win.on_new_conversation = assistant.reset_conversation

    def open_settings() -> None:
        SettingsDialog(settings, assistant.reconfigure, parent=ui._win).exec()

    ui._win.on_engine_settings = open_settings

    if "--smoke" in sys.argv:
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: (print("SMOKE OK"), ui._app.quit()))
    else:
        if not settings.ready:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(800, open_settings)
        assistant.start()
    try:
        ui.root.mainloop()
    finally:
        try:
            from avatar import whatsapp_bridge as wb
            wb.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()

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


def _single_instance() -> bool:
    """Blocco esclusivo su data/luza.lock: una seconda LuZa (avvio automatico + avvio manuale) esce subito."""
    import fcntl
    from avatar.settings import DATA_DIR
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    global _lock_file
    _lock_file = open(DATA_DIR / "luza.lock", "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file.write(str(__import__("os").getpid())); _lock_file.flush()
        return True
    except OSError:
        return False


def main() -> None:
    if "--smoke" not in sys.argv and not _single_instance():
        print("LuZa è già in esecuzione: questa seconda istanza si chiude.")
        try:
            import subprocess
            subprocess.run(["osascript", "-e", 'display notification "LuZa è già aperta." with title "LuZa"'], timeout=5)
        except Exception:
            pass
        return
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
    try:
        from core import audio_devices
        audio_devices.prefetch()   # elenco dispositivi audio pronto prima che si apra la finestra
    except Exception:
        pass
    from avatar.plugins import registry
    from core import confirm
    confirm.bind(ui.show_confirm, ui.hide_confirm, ui.write_log)
    registry.ctx = {"say": assistant.say, "log": ui.write_log, "confirm": confirm.request, "player": ui}
    ui.get_plugins = registry.list_for_ui
    from avatar.mcp_client import manager as mcp_manager
    mcp_manager.start(registry, ui.write_log)
    ui._app.aboutToQuit.connect(mcp_manager.stop)
    ui.get_plugin_settings = lambda: []
    ui.request_say = assistant.say
    # Questi due sono attributi della finestra, non della facciata JarvisUI.
    ui._win.on_new_conversation = assistant.reset_conversation

    # ── Accesso remoto (app web per il telefono): specchia stato, registro e conferme; riceve testo e voce ──
    from avatar.remote import RemoteServer
    remote = RemoteServer(assistant, settings, ui.write_log)
    assistant.remote = remote
    _set_state, _write_log, _show_confirm, _hide_confirm = ui.set_state, ui.write_log, ui.show_confirm, ui.hide_confirm

    def set_state(state: str) -> None:
        _set_state(state); remote.on_state(state)

    def write_log(text: str) -> None:
        _write_log(text); remote.on_log(text)

    def show_confirm(title: str, detail: str) -> None:
        _show_confirm(title, detail); remote.on_confirm(title, detail)

    def hide_confirm() -> None:
        _hide_confirm(); remote.on_confirm_hide()

    ui.set_state, ui.write_log, ui.show_confirm, ui.hide_confirm = set_state, write_log, show_confirm, hide_confirm
    confirm.bind(ui.show_confirm, ui.hide_confirm, ui.write_log)
    registry.ctx["log"] = ui.write_log
    registry.ctx["on_image"] = remote.on_image
    registry.ctx["on_file"] = remote.on_file
    ui._win.on_remote_clicked = remote.urls
    from PyQt6.QtCore import QObject, pyqtSignal

    class _Bridge(QObject):
        apply = pyqtSignal()
    bridge = _Bridge(); bridge.apply.connect(assistant.reconfigure)
    remote.apply_cb = bridge.apply.emit
    ui._win._remote_bridge = bridge
    if settings.get("remote_enabled", True):
        remote.start()
        ui._app.aboutToQuit.connect(remote.stop)

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

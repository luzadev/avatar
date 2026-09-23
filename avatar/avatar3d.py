"""Vista 3D dell'avatar (three.js in un QWebEngineView), sincronizzata con stati, volume e visemi."""
from __future__ import annotations

import http.server
import threading
import time
from functools import partial
from pathlib import Path

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QColor
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView

ROOT = Path(__file__).resolve().parent.parent / "avatar3d"
MODELS_DIR = ROOT / "models"


def list_models() -> list[str]:
    """Nomi (senza estensione) dei modelli GLB disponibili."""
    return sorted(p.stem for p in MODELS_DIR.glob("*.glb"))


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


_server_port: int | None = None


def serve_root() -> int:
    """Server HTTP locale (una volta sola): i moduli ES non si caricano da file://."""
    global _server_port
    if _server_port:
        return _server_port
    handler = partial(_Quiet, directory=str(ROOT))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, name="avatar3d-http", daemon=True).start()
    _server_port = srv.server_address[1]
    return _server_port


class _Page(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source) -> None:  # noqa: N802
        print(f"[avatar3d] {message} ({Path(source).name}:{line})")


class Avatar3DView(QWebEngineView):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._page = _Page(self)
        self.setPage(self._page)
        self._page.setBackgroundColor(QColor("#00060a"))
        self._state = "LISTENING"
        self._level = 0.0
        self._sched: tuple[list, float, float] | None = None
        self._ready = False
        self._model = ""
        self.loadFinished.connect(self._on_loaded)
        try:
            from avatar.settings import Settings
            model = str(Settings().get("avatar_model") or "")
        except Exception:
            model = ""
        self.set_model(model)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def _on_loaded(self, ok: bool) -> None:
        self._ready = ok

    def set_model(self, name: str) -> None:
        """Carica (o ricarica) la pagina con il modello indicato."""
        available = list_models()
        if name not in available:
            name = available[0] if available else name
        if name == self._model and self._ready:
            return
        self._model = name
        self._ready = False
        self.load(QUrl(f"http://127.0.0.1:{serve_root()}/index.html?model={name}"))

    # ── API thread-safe (valori semplici, letti dal timer sul thread Qt) ──
    def set_state(self, state: str) -> None:
        self._state = str(state or "LISTENING")

    def set_audio_level(self, level: float) -> None:
        try:
            self._level = max(self._level, min(1.0, max(0.0, float(level))))
        except (TypeError, ValueError):
            pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        if not frames:
            return
        new = list(frames)
        cur = self._sched
        if cur is not None and abs(cur[2] - hop) < 1e-6:
            old, t0, _ = cur
            i = int(round((at - t0) / hop))
            if 0 <= i <= len(old) + 1:
                merged = old[:i] + new
                played = int((time.time() - t0) / hop) - 2
                if played > 60:
                    merged, t0 = merged[played:], t0 + played * hop
                self._sched = (merged, t0, hop)
                return
        self._sched = (new, float(at), float(hop))

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        if self._ready:
            self._page.runJavaScript(f"window.avatar3d && avatar3d.glance({dx:.3f},{dy:.3f},{hold:.2f})")

    def _tick(self) -> None:
        open_, width, vis_level = 0.0, 0.0, 0.0
        sched = self._sched
        if sched is not None:
            frames, t0, hop = sched
            i = int((time.time() - t0) / hop)
            if i >= len(frames):
                self._sched = None
            elif i >= 0:
                vis_level, open_, width = frames[i]
        level = max(self._level, vis_level)
        self._level *= 0.82
        if self._ready:
            self._page.runJavaScript(
                f"window.avatar3d && avatar3d.update({level:.3f},{open_:.3f},{width:.3f},'{self._state}')"
            )

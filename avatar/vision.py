"""Cattura di webcam, schermo e finestra in primo piano, con OCR e anteprima per i motori."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from avatar.settings import DATA_DIR

CAPTURES = DATA_DIR / "captures"
KEEP = 30


def _prune() -> None:
    files = sorted(CAPTURES.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[KEEP:]:
        p.unlink(missing_ok=True)


def _shrink(src: Path, max_side: int = 1600, quality: int = 85) -> Path:
    """Versione JPEG ridotta, adatta ai modelli con visione."""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    im.thumbnail((max_side, max_side))
    out = src.with_suffix(".jpg") if src.suffix.lower() != ".jpg" else src
    im.save(out, format="JPEG", quality=quality)
    if out != src:
        src.unlink(missing_ok=True)
    return out


def capture_webcam(warmup_frames: int = 8) -> Path:
    import cv2
    CAPTURES.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("Webcam non disponibile: controlla il permesso Fotocamera in Impostazioni di Sistema > Privacy e sicurezza.")
    try:
        frame = None
        for _ in range(warmup_frames):   # qualche fotogramma per l'esposizione automatica
            ok, frame = cap.read()
            if not ok:
                break
            time.sleep(0.05)
        if frame is None:
            raise RuntimeError("La webcam non ha restituito immagini.")
        out = CAPTURES / f"webcam-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
        cv2.imwrite(str(out), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    finally:
        cap.release()
    _prune()
    return _shrink(out)


def capture_screen(all_displays: bool = False) -> Path:
    CAPTURES.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = CAPTURES / f"schermo-{stamp}.png"
    cmd = ["screencapture", "-x", str(out)] if not all_displays else ["screencapture", "-x", str(out), str(CAPTURES / f"schermo2-{stamp}.png")]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if res.returncode != 0 or not out.exists():
        raise RuntimeError("Cattura schermo non riuscita: concedi «Registrazione schermo» all'app in Impostazioni di Sistema > Privacy e sicurezza.")
    _prune()
    return _shrink(out, max_side=2000)


def _front_window_id() -> tuple[int | None, str]:
    import Quartz
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    name = app.localizedName() if app else ""
    pid = app.processIdentifier() if app else None
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID):
        if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer", 0) == 0 and (w.get("kCGWindowBounds") or {}).get("Height", 0) > 50:
            return w["kCGWindowNumber"], name
    return None, name


def capture_front_window() -> tuple[Path, str]:
    CAPTURES.mkdir(parents=True, exist_ok=True)
    wid, name = _front_window_id()
    if wid is None:
        return capture_screen(), name or "schermo"
    out = CAPTURES / f"finestra-{time.strftime('%Y%m%d-%H%M%S')}.png"
    res = subprocess.run(["screencapture", "-x", "-l", str(wid), str(out)], capture_output=True, text=True, timeout=20)
    if res.returncode != 0 or not out.exists():
        raise RuntimeError("Cattura finestra non riuscita: serve il permesso «Registrazione schermo».")
    _prune()
    return _shrink(out, max_side=2000), name


def ocr_text(path: Path) -> str:
    from avatar.attachments import ocr
    try:
        return ocr(path).strip()
    except Exception:
        return ""

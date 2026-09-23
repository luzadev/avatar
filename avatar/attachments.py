"""Allegati dalla zona "File upload": testo dai documenti, OCR e immagine dalle foto."""
from __future__ import annotations

import io
import re
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tiff", ".bmp"}
MAX_TEXT = 8000


def ocr(path: Path) -> str:
    """Testo riconosciuto nell'immagine con il framework Vision di macOS."""
    import Quartz
    import Vision
    from Foundation import NSURL
    src = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(path)), None)
    if src is None:
        return ""
    img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if img is None:
        return ""
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(["it-IT", "en-US"])
    req.setUsesLanguageCorrection_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        return ""
    lines = []
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if cands:
            lines.append(str(cands[0].string()))
    return "\n".join(lines)


def image_payload(path: Path, max_side: int = 1568) -> tuple[str, bytes]:
    """Immagine ridotta per i modelli con visione: (media_type, bytes)."""
    from PIL import Image
    im = Image.open(path)
    im = im.convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return "image/jpeg", buf.getvalue()


def describe(path: Path) -> tuple[str, tuple[str, bytes] | None]:
    """Restituisce (contesto testuale da aggiungere al messaggio, immagine per la visione o None)."""
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        text = ""
        try:
            text = ocr(path)
        except Exception as err:
            text = f"(OCR non riuscito: {err})"
        image = None
        try:
            image = image_payload(path)
        except Exception:
            pass
        ctx = f"[Allegato immagine: {path.name}]\nTesto riconosciuto nell'immagine (OCR):\n{text.strip() or '(nessun testo)'}"
        return ctx, image
    # Documenti: riusa il lettore del plugin file
    try:
        from avatar.plugins import registry
        registry.load()
        text = registry.run("file_leggi", {"percorso": str(path)})
    except Exception as err:
        text = f"(lettura non riuscita: {err})"
    text = re.sub(r"\s+", " ", text)[:MAX_TEXT]
    return f"[Allegato: {path.name}]\n{text}", None

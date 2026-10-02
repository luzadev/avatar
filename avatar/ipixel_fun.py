"""Intermezzi per il pannello LED: frasi spiritose, animazioni procedurali (GIF) e piccole informazioni."""
from __future__ import annotations

import io
import math
import random
import time

from PIL import Image, ImageDraw

FRASI = [
    "Ciao Luciano!", "Respira. Bevi acqua.", "Il caffè è pronto?", "Sto pensando... scherzo",
    "LuZa ti osserva (con affetto)", "Oggi spacchi!", "Pausa? Te la meriti", "Error 418: sono una teiera",
    "Beat drop in 3, 2, 1...", "Ctrl+Z sulla giornata?", "Ricordati di sorridere", "Più bpm, meno stress",
    "Ho bevuto 0 caffè. Invidia", "I server stanno bene, credo", "Se mi spegni piango in binario",
    "01001100 01110101 01011010 01100001", "Modalità DJ: attiva", "Valentina ti pensa (probabile)",
    "Chiedimi qualcosa!", "Batteria sociale: 87%", "Che si mangia stasera?", "Alzati e sgranchisciti!",
]


def _gif(frames: list[Image.Image], ms: int = 60) -> bytes:
    from avatar.ipixel import _save_gif
    return _save_gif(frames, ms)


def plasma(size, n=14) -> bytes:
    w, h = size; out = []
    for f in range(n):
        im = Image.new("RGB", size); px = im.load(); t = f / n * 2 * math.pi
        for y in range(h):
            for x in range(w):
                v = math.sin(x / 6 + t) + math.sin(y / 3 + t * 1.3) + math.sin((x + y) / 8 + t * 0.7)
                px[x, y] = (int(128 + 127 * math.sin(v)), int(128 + 127 * math.sin(v + 2.1)), int(128 + 127 * math.sin(v + 4.2)))
        out.append(im)
    return _gif(out, 70)


def matrix(size, n=36) -> bytes:
    w, h = size; drops = [random.randint(-h, h) for _ in range(w)]; trail = [[0] * h for _ in range(w)]; out = []
    for _ in range(n):
        im = Image.new("RGB", size); px = im.load()
        for x in range(w):
            for y in range(h):
                trail[x][y] = max(0, trail[x][y] - 40)
            if random.random() < 0.6:
                drops[x] += 1
            if drops[x] >= h + random.randint(0, 8):
                drops[x] = random.randint(-6, 0)
            if 0 <= drops[x] < h:
                trail[x][drops[x]] = 255
            for y in range(h):
                v = trail[x][y]
                if v:
                    px[x, y] = (v // 4, v, v // 3) if v < 255 else (200, 255, 200)
        out.append(im)
    return _gif(out, 60)


def stelle(size, n=36) -> bytes:
    w, h = size; st = [[random.uniform(-w / 2, w / 2), random.uniform(-h / 2, h / 2), random.uniform(1, 8)] for _ in range(40)]; out = []
    for _ in range(n):
        im = Image.new("RGB", size); px = im.load()
        for s in st:
            s[2] -= 0.25
            if s[2] <= 0.3:
                s[:] = [random.uniform(-w / 2, w / 2), random.uniform(-h / 2, h / 2), 8]
            x, y = int(w / 2 + s[0] / s[2] * 2), int(h / 2 + s[1] / s[2] * 2)
            if 0 <= x < w and 0 <= y < h:
                b = int(255 * (1 - s[2] / 8)); px[x, y] = (b, b, min(255, b + 40))
        out.append(im)
    return _gif(out, 50)


CUORE = ["0110110", "1111111", "1111111", "0111110", "0011100", "0001000"]


def cuore(size, n=24) -> bytes:
    w, h = size; out = []
    for f in range(n):
        im = Image.new("RGB", size); d = ImageDraw.Draw(im)
        s = 2 if f % 8 < 4 else 1
        cx = int((w - 7 * s) * (0.5 + 0.45 * math.sin(f / n * 2 * math.pi)))
        cy = (h - 6 * s) // 2
        for r, row in enumerate(CUORE):
            for c, ch in enumerate(row):
                if ch == "1":
                    d.rectangle([cx + c * s, cy + r * s, cx + c * s + s - 1, cy + r * s + s - 1], fill=(255, 30, 80))
        out.append(im)
    return _gif(out, 90)


def equalizzatore(size, n=30) -> bytes:
    w, h = size; bars = w // 4; lv = [random.uniform(2, h) for _ in range(bars)]; out = []
    for _ in range(n):
        im = Image.new("RGB", size); d = ImageDraw.Draw(im)
        for i in range(bars):
            lv[i] = max(1, min(h, lv[i] + random.uniform(-4, 4)))
            for y in range(int(lv[i])):
                c = (0, 255, 80) if y < h * 0.5 else (255, 200, 0) if y < h * 0.8 else (255, 40, 40)
                d.rectangle([i * 4, h - 1 - y, i * 4 + 2, h - 1 - y], fill=c)
        out.append(im)
    return _gif(out, 70)


def pacman(size, n=40) -> bytes:
    w, h = size; out = []
    for f in range(n):
        im = Image.new("RGB", size); d = ImageDraw.Draw(im)
        x = int(-12 + (w + 24) * f / n); cy = h // 2
        for dx in range(x + 10, w, 8):
            d.rectangle([dx, cy - 1, dx + 1, cy], fill=(255, 200, 150))
        a = 35 if f % 4 < 2 else 5
        d.pieslice([x - 6, cy - 6, x + 6, cy + 6], a, 360 - a, fill=(255, 220, 0))
        gx = x - 20
        d.rounded_rectangle([gx - 5, cy - 6, gx + 5, cy + 6], 4, fill=(255, 60, 60))
        d.point([(gx - 2, cy - 2), (gx + 2, cy - 2)], fill=(255, 255, 255))
        out.append(im)
    return _gif(out, 60)


ANIMAZIONI = {"plasma": plasma, "matrix": matrix, "stelle": stelle, "cuore": cuore, "equalizzatore": equalizzatore, "pacman": pacman}


def info_breve() -> str:
    t = time.localtime()
    giorni = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
    mesi = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
    base = f"{giorni[t.tm_wday]} {t.tm_mday} {mesi[t.tm_mon - 1]}"
    try:
        import json, urllib.request
        u = "https://api.open-meteo.com/v1/forecast?latitude=42.35&longitude=14.40&current=temperature_2m,weather_code&timezone=Europe/Rome"
        d = json.load(urllib.request.urlopen(u, timeout=5))["current"]
        return f"{base} · Ortona {round(d['temperature_2m'])}°"
    except Exception:
        return base


def scegli(size) -> tuple[str, object]:
    """('testo', frase) oppure ('gif', dati)."""
    r = random.random()
    if r < 0.45:
        return "testo", random.choice(FRASI)
    if r < 0.6:
        return "info", info_breve()
    nome = random.choice(list(ANIMAZIONI))
    return "gif", ANIMAZIONI[nome](size)

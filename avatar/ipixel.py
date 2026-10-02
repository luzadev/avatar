"""Pannello LED iPIXEL (BLE, 64×16): connessione persistente in un thread asyncio, coda di schermate con priorità.

Protocollo (dal progetto Flutter ~/Downloads/Progetti2026/iPixel): servizio fa01, scrittura fa02, notifiche fa03;
comandi brevi (luminosità, accensione, orologio) e immagini PNG/GIF inviate a pacchetti da 244 byte con intestazione
[len][tipo 2=PNG 3=GIF][opt][lunghezza dati][crc32][0][slot].

Priorità: notifica (temporanea) > stato dell'assistente (mentre pensa/parla) > riposo (orologio del pannello).
"""
from __future__ import annotations

import asyncio
import io
import random
import struct
import threading
import time
import zlib

from PIL import Image, ImageDraw, ImageFont

SVC_WRITE = "0000fa02-0000-1000-8000-00805f9b34fb"
SVC_NOTIFY = "0000fa03-0000-1000-8000-00805f9b34fb"
SIZES = {128: (64, 64), 129: (32, 32), 130: (32, 16), 131: (64, 16), 132: (96, 16), 133: (64, 20), 134: (128, 16),
         135: (144, 16), 136: (192, 16), 137: (48, 24), 138: (16, 16), 139: (160, 16), 140: (32, 8), 141: (96, 32),
         142: (64, 32), 143: (128, 32), 144: (256, 16), 145: (320, 16), 146: (384, 16), 147: (448, 32)}
COLORI = {"LISTENING": (0, 255, 120), "THINKING": (255, 190, 0), "PROCESSING": (255, 190, 0), "SPEAKING": (255, 90, 0),
          "SLEEPING": (40, 120, 140), "MUTED": (255, 40, 90), "INFO": (0, 200, 255), "ALERT": (255, 40, 60)}
NOMI_COLORI = {"rosso": (255, 30, 30), "verde": (0, 230, 80), "blu": (40, 90, 255), "azzurro": (0, 200, 255), "giallo": (255, 210, 0),
               "arancione": (255, 120, 0), "viola": (170, 60, 255), "rosa": (255, 80, 170), "bianco": (255, 255, 255), "ciano": (0, 255, 230)}
from pathlib import Path as _P
PIXEL_FONT = _P(__file__).resolve().parent.parent / "assets" / "fonts" / "Silkscreen-Regular.ttf"   # OFL, font pixel per LED
FONT_PATHS = (str(PIXEL_FONT), "/System/Library/Fonts/Menlo.ttc")
SCROLL_MS = 70          # ms per pixel di scorrimento (circa 14 pixel al secondo)


def _font(size: int):
    for p in FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def windows(data: bytes, slot: int = 1, gif: bool = False, win: int = 12288) -> list[bytes]:
    """Finestre da 12 KB, ognuna con l'intestazione completa: [len][tipo 2=PNG 3=GIF][opt 0=prima 2=seguenti]
    [lunghezza totale][crc32 totale][0][slot] + dati. Tra una finestra e l'altra il pannello manda una conferma."""
    typ = 3 if gif else 2
    crc = zlib.crc32(data) & 0xFFFFFFFF
    out = []
    for wi, off in enumerate(range(0, len(data), win)):
        chunk = data[off:off + win]
        out.append(struct.pack("<HHBII", (15 + len(chunk)) & 0xFFFF, typ, 0 if wi == 0 else 2, len(data), crc) + bytes([0, slot & 0xFF]) + chunk)
    return out


def packets(data: bytes, slot: int = 1, gif: bool = False) -> list[bytes]:
    return [w[i:i + 244] for w in windows(data, slot, gif) for i in range(0, len(w), 244)]


def _single_gif_parts(img) -> tuple[bytes, bytes]:
    """(tavolozza, blocco immagine) di un fotogramma salvato da solo come GIF a schermo intero, non interlacciato."""
    buf = io.BytesIO(); img.save(buf, "GIF", optimize=False, interlace=False)
    b = buf.getvalue()
    flags = b[10]
    gct_len = 3 * (2 ** ((flags & 7) + 1)) if flags & 0x80 else 0
    gct = b[13:13 + gct_len]
    i = 13 + gct_len
    while b[i] == 0x21:                      # salta le estensioni
        i += 2
        while b[i]:
            i += b[i] + 1
        i += 1
    assert b[i] == 0x2C
    return gct, b[i:-1]                       # descrittore immagine + dati LZW (senza il terminatore 0x3B)


def _save_gif(frames: list, ms: int) -> bytes:
    """GIF con ogni fotogramma a schermo intero e tavolozza locale: niente ritagli né trasparenze, che il pannello
    disegnerebbe fuori posto (i 'pixel sparsi')."""
    w, h = frames[0].size
    out = bytearray(b"GIF89a" + struct.pack("<HHBBB", w, h, 0, 0, 0))
    out += b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00"          # ripetizione infinita
    delay = max(2, round(ms / 10))
    for f in frames:
        pimg = f.convert("RGB").quantize(colors=16, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        gct, block = _single_gif_parts(pimg)
        n = max(1, (len(gct) // 3).bit_length() - 1)
        size_bits = max(0, n - 1)
        lct = gct.ljust(3 * (2 ** (size_bits + 1)), b"\x00")
        out += b"\x21\xF9\x04" + bytes([1 << 2]) + struct.pack("<H", delay) + b"\x00\x00"   # GCE: disposal 1, nessuna trasparenza
        desc = bytearray(block[:10])
        desc[9] = 0x80 | size_bits                                            # tavolozza locale, non interlacciato
        out += bytes(desc) + lct + block[10:]
    out += b"\x3B"
    return bytes(out)


def _ink_rows(font, text="AQgj") -> tuple[int, int]:
    d = ImageDraw.Draw(Image.new("1", (1, 1)))
    b = d.textbbox((0, 0), "AHMQ", font=font)
    return b[1], b[3]


def render_text(text: str, color=(0, 200, 255), size=(64, 16), icon_color=None, bold: bool = False) -> tuple[bytes, bool]:
    """Testo su una riga con font pixel: statico se entra, altrimenti GIF che scorre lentamente. (dati, è_gif)."""
    w, h = size
    font = _font(16 if h >= 16 else 8)
    pad = 6 if icon_color else 0
    meas = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    tw = int(meas.textlength(text, font=font))
    top, bot = _ink_rows(font)
    ty = (h - (bot - top)) // 2 - top

    def frame(x: int) -> Image.Image:
        im = Image.new("RGB", (w, h), (0, 0, 0)); d = ImageDraw.Draw(im); d.fontmode = "1"
        d.text((x, ty), text, fill=color, font=font)
        if icon_color:
            d.rectangle([0, 0, pad - 2, h - 1], fill=(0, 0, 0)); d.ellipse([0, h // 2 - 2, 4, h // 2 + 2], fill=icon_color)
        return im

    if tw + pad <= w:
        buf = io.BytesIO(); frame(pad + (w - pad - tw) // 2).save(buf, "PNG"); return buf.getvalue(), False
    frames = [frame(x) for x in range(w, -tw - 1, -1)]
    return _save_gif(frames, SCROLL_MS), True


def _fmt_time(sec: float) -> str:
    sec = max(0, int(sec)); return f"{sec // 60}:{sec % 60:02d}"


def _big_frames(text: str, color, size, min_frames: int = 0) -> list:
    """Fotogrammi di una riga in caratteri grandi: ferma se entra, altrimenti scorre una volta da destra a sinistra."""
    w, h = size
    font = _font(16 if h >= 16 else 8)
    top, bot = _ink_rows(font)
    ty = (h - (bot - top)) // 2 - top
    tw = int(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(text, font=font))

    def frame(x: int):
        im = Image.new("RGB", (w, h), (0, 0, 0)); d = ImageDraw.Draw(im); d.fontmode = "1"; d.text((x, ty), text, fill=color, font=font); return im

    if tw <= w:
        return [frame((w - tw) // 2)] * max(min_frames, int(2500 / SCROLL_MS))
    xs = [0] * int(700 / SCROLL_MS) + list(range(0, -(tw - w) - 1, -1)) + [-(tw - w)] * int(700 / SCROLL_MS)
    return [frame(x) for x in xs]


def render_music(titolo: str, artista: str, posizione: float, durata: float, size=(64, 16), secondi: float = 20) -> bytes:
    """Ciclo musica (GIF): titolo in grande, autore in grande, poi schermata con autore e tempo che manca e barra
    di avanzamento, ripetuti fino a coprire `secondi`."""
    w, h = size
    green, white = (30, 215, 96), (230, 230, 230)
    font = _font(8)
    top, _bot = _ink_rows(font)
    y1, y2 = -top + 1, -top + 8
    meas = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    total = max(1, int(min(secondi, max(1.0, durata - posizione)) * 1000 / SCROLL_MS))
    big_t = _big_frames(titolo, green, size)
    big_a = _big_frames(artista, white, size)
    frames = []
    while len(frames) < total:
        frames += big_t + big_a
        start = len(frames)
        for k in range(int(4000 / SCROLL_MS)):         # 4 s di riepilogo con il tempo che scorre
            i = start + k
            el = i * SCROLL_MS / 1000.0
            im = Image.new("RGB", (w, h), (0, 0, 0)); d = ImageDraw.Draw(im); d.fontmode = "1"
            t_w = int(meas.textlength(titolo, font=font))
            d.text(((w - t_w) // 2 if t_w <= w else 0, y1), titolo, fill=green, font=font)
            rem = "-" + _fmt_time(durata - posizione - el)
            rw = int(meas.textlength(rem, font=font))
            art = Image.new("RGB", (w - rw - 2, 8), (0, 0, 0)); da = ImageDraw.Draw(art); da.fontmode = "1"
            da.text((0, y2 - 7), artista, fill=white, font=font); im.paste(art, (0, 7))
            d.text((w - rw, y2), rem, fill=white, font=font)
            frac = min(1.0, (posizione + el) / durata) if durata > 0 else 0
            d.line([(0, h - 1), (w - 1, h - 1)], fill=(25, 25, 25)); d.line([(0, h - 1), (int((w - 1) * frac), h - 1)], fill=green)
            frames.append(im)
    return _save_gif(frames[:max(total, len(big_t) + len(big_a) + 20)], SCROLL_MS)


def render_image(path: str, size=(64, 16)) -> bytes:
    im = Image.open(path).convert("RGB")
    im.thumbnail(size, Image.LANCZOS)
    bg = Image.new("RGB", size, (0, 0, 0)); bg.paste(im, ((size[0] - im.width) // 2, (size[1] - im.height) // 2))
    buf = io.BytesIO(); bg.save(buf, "PNG"); return buf.getvalue()


class IPixel:
    def __init__(self, settings, log=print) -> None:
        self.settings, self.log = settings, log
        self.size = (64, 16)
        self.connected = False
        self.error = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None
        self._wake: asyncio.Event | None = None
        self._notify_until = 0.0
        self._want: tuple | None = None       # (chiave, dati, gif) da mostrare
        self._shown_key = None
        self._state_key = None
        self._background: tuple | None = None   # (chiave, dati, gif, scadenza): schermata di riposo al posto dell'orologio
        self._lock = threading.Lock()
        self._stop = False

    # ── ciclo BLE ──────────────────────────────────────────────────────────
    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="ipixel").start()

    def stop(self) -> None:
        self._stop = True
        if self._loop:
            self._loop.call_soon_threadsafe(lambda: None)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._wake = asyncio.Event()
        self._loop.run_until_complete(self._main())

    async def _connect(self):
        from bleak import BleakClient, BleakScanner
        addr = str(self.settings.get("ipixel_address") or "")
        dev = None
        if addr:
            dev = await BleakScanner.find_device_by_address(addr, timeout=10)
        if dev is None:
            dev = await BleakScanner.find_device_by_filter(lambda d, a: (d.name or a.local_name or "").startswith("LED_BLE_"), timeout=12)
        if dev is None:
            raise RuntimeError("pannello iPIXEL non trovato (acceso e vicino?)")
        if dev.address != addr:
            self.settings.set("ipixel_address", dev.address); self.settings.save()
        c = BleakClient(dev, disconnected_callback=lambda _c: self._on_disconnect())
        await c.connect(timeout=15)
        notes: list[bytes] = []
        self._notes = notes
        await c.start_notify(SVC_NOTIFY, lambda _h, d: notes.append(bytes(d)))
        await c.write_gatt_char(SVC_WRITE, bytes([8, 0, 1, 0x80, 0, 0, 0, 0]), response=False)
        await asyncio.sleep(0.8)
        for n in notes:
            if len(n) >= 5 and n[2:4] == b"\x01\x80":
                self.size = SIZES.get(n[4], self.size)
        self._client = c
        self.connected, self.error = True, ""
        await self._cmd([5, 0, 4, 0x80, int(self.settings.get("ipixel_luminosita") or 40)])
        await self._sync_clock()
        self.log(f"SYS: Pannello iPIXEL collegato ({self.size[0]}×{self.size[1]}).")

    def _on_disconnect(self) -> None:
        self.connected = False
        self._client = None
        self._shown_key = None

    async def _cmd(self, frame: list[int]) -> None:
        if self._client:
            await self._client.write_gatt_char(SVC_WRITE, bytes(frame), response=False)

    async def _send(self, data: bytes, gif: bool) -> None:
        notes = getattr(self, "_notes", [])
        for w in windows(data, 1, gif):
            n0 = len(notes)
            for i in range(0, len(w), 244):
                await self._client.write_gatt_char(SVC_WRITE, w[i:i + 244], response=False)
                await asyncio.sleep(0.015)
            for _ in range(80):                     # conferma della finestra (max 8 s)
                if len(notes) > n0:
                    break
                await asyncio.sleep(0.1)
        del notes[:-20]

    async def _sync_clock(self) -> None:
        t = time.localtime()
        await self._cmd([8, 0, 1, 0x80, t.tm_hour, t.tm_min, t.tm_sec, 0])

    async def _show_clock(self) -> None:
        # Prima la modalità orologio, poi l'ora: inviata prima, il pannello la azzera entrando in modalità orologio.
        t = time.localtime()
        await self._cmd([11, 0, 6, 1, int(self.settings.get("ipixel_orologio_stile") or 1), 1, 0, t.tm_year % 100, t.tm_mon, t.tm_mday, t.tm_wday + 1])
        await asyncio.sleep(0.4)
        await self._sync_clock()
        await asyncio.sleep(0.3)
        await self._sync_clock()
        self._last_sync = time.time()

    async def _main(self) -> None:
        backoff = 5
        while not self._stop:
            if not self.settings.get("ipixel_enabled"):
                await asyncio.sleep(3); continue
            if not self.connected:
                try:
                    await self._connect(); backoff = 5
                except Exception as err:
                    self.error = str(err)[:160]
                    await asyncio.sleep(backoff); backoff = min(backoff * 2, 120); continue
            try:
                with self._lock:
                    want = self._want
                    notify = time.time() < self._notify_until
                if want and (notify or self._state_key) and want[0] != self._shown_key:
                    await self._send(want[1], want[2]); self._shown_key = want[0]
                elif (not want or (not notify and not self._state_key)) and self._background and time.time() < self._background[3]:
                    bg = self._background
                    if self._shown_key != bg[0]:
                        await self._send(bg[1], bg[2]); self._shown_key = bg[0]
                        with self._lock:
                            self._want = None
                elif not want or (not notify and not self._state_key):
                    if self._shown_key == "clock" and self._intermezzo_due():
                        await self._intermezzo(); continue
                    if self._shown_key == "clock" and time.time() - getattr(self, "_last_sync", 0) > 600:
                        await self._sync_clock(); self._last_sync = time.time()
                    if self._shown_key != "clock":
                        await self._show_clock(); self._shown_key = "clock"
                        with self._lock:
                            self._want = None
            except Exception as err:
                self.error = str(err)[:160]; self.connected = False
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                continue
            self._wake.clear()
            try:
                timeout = max(0.3, self._notify_until - time.time()) if time.time() < self._notify_until else 30
                if self._background:
                    timeout = min(timeout, max(0.5, self._background[3] - time.time()))
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass

    def _intermezzo_due(self) -> bool:
        every = int(self.settings.get("ipixel_intermezzi_min") or 0)
        if every <= 0:
            return False
        h = time.localtime().tm_hour
        if h >= 23 or h < 7:          # di notte niente sorprese
            return False
        last = getattr(self, "_last_fun", 0.0) or time.time() - every * 60 + 90   # il primo dopo un minuto e mezzo
        if not getattr(self, "_last_fun", 0.0):
            self._last_fun = last
        return time.time() - last > every * 60 * random.uniform(0.8, 1.2)

    async def _intermezzo(self) -> None:
        from avatar import ipixel_fun as fun
        self._last_fun = time.time()
        kind, val = await asyncio.get_event_loop().run_in_executor(None, fun.scegli, self.size)
        if kind == "gif":
            data, gif = val, True
        else:
            col = random.choice([(0, 200, 255), (255, 120, 0), (255, 60, 170), (120, 255, 60), (255, 220, 0), (170, 90, 255)])
            data, gif = render_text(str(val), col, self.size)
        with self._lock:
            self._want = (f"fun:{time.time()}", data, gif)
            self._notify_until = time.time() + 12
        self._shown_key = None

    def _poke(self) -> None:
        if self._loop and self._wake:
            self._loop.call_soon_threadsafe(self._wake.set)

    # ── API (da qualunque thread) ──────────────────────────────────────────
    def show_text(self, text: str, color=None, durata: float = 10.0, key: str | None = None, icon=None) -> None:
        data, gif = render_text(text, color or COLORI["INFO"], self.size, icon_color=icon)
        with self._lock:
            self._want = (key or f"n:{text}:{time.time()}", data, gif)
            self._notify_until = time.time() + durata
        self._poke()

    def show_image(self, path: str, durata: float = 15.0) -> None:
        data = render_image(path, self.size)
        with self._lock:
            self._want = (f"img:{path}:{time.time()}", data, False); self._notify_until = time.time() + durata
        self._poke()

    def on_state(self, state: str) -> None:
        """Stato dell'assistente: mostrato mentre lavora, orologio a riposo."""
        if not self.settings.get("ipixel_stati", True):
            return
        base, _, detail = state.partition(" · ")
        base = base.split(" ")[0]
        if base in ("LISTENING", "SLEEPING", "MUTED", "INITIALISING", ""):
            with self._lock:
                self._state_key = None
            self._poke(); return
        if time.time() < self._notify_until:
            return
        m = None
        import re
        if "token" in detail or "parole" in detail:
            m = re.search(r"(\d+)\s+(token|parole)(?:\s*·\s*(\d+)/s)?", detail)
        if m:
            text = f"{m.group(1)} tok" + (f" {m.group(3)}/s" if m.group(3) else "")
        elif base == "SPEAKING":
            text = "parlo"
        elif detail:
            text = detail.replace("strumento: ", "").replace("genero la risposta", "scrivo")[:40]
        else:
            text = {"THINKING": "penso…", "PROCESSING": "lavoro…"}.get(base, base.lower())
        key = f"s:{base}:{text}"
        if key == self._state_key:
            return
        data, gif = render_text(text, COLORI.get(base, COLORI["INFO"]), self.size, icon_color=COLORI.get(base))
        with self._lock:
            self._state_key = key
            self._want = (key, data, gif)
        self._poke()

    def set_background(self, key: str, data: bytes, gif: bool, durata: float) -> None:
        with self._lock:
            self._background = (key, data, gif, time.time() + durata)
        self._poke()

    def clear_background(self) -> None:
        with self._lock:
            had = self._background is not None
            self._background = None
        if had:
            self._shown_key = None; self._poke()

    def run_cmd(self, frame: list[int]) -> None:
        if self._loop and self.connected:
            asyncio.run_coroutine_threadsafe(self._cmd(frame), self._loop).result(timeout=10)
        else:
            raise RuntimeError(self.error or "pannello non collegato")

    def clock(self) -> None:
        with self._lock:
            self._want, self._state_key, self._notify_until = None, None, 0
        self._shown_key = None
        self._poke()


panel: IPixel | None = None

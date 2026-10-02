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
FONT_PATHS = ("/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Supplemental/Arial Bold.ttf")


def _font(size: int):
    for p in FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def packets(data: bytes, slot: int = 1, gif: bool = False) -> list[bytes]:
    typ = 3 if gif else 2
    hdr = struct.pack("<HHBII", (15 + len(data)) & 0xFFFF, typ, 0, len(data), zlib.crc32(data) & 0xFFFFFFFF) + bytes([0, slot & 0xFF])
    full = hdr + data
    out, off, wi = [], 0, 0
    while off < len(full):
        win = full[off:off + 12288]
        if wi:
            win = struct.pack("<HHB", (len(win) + 5) & 0xFFFF, typ, 2) + win
        out += [win[i:i + 244] for i in range(0, len(win), 244)]
        off += 12288; wi += 1
    return out


def render_text(text: str, color=(0, 200, 255), size=(64, 16), icon_color=None, bold: bool = True) -> tuple[bytes, bool]:
    """Testo su un pannello: statico se entra, altrimenti GIF scorrevole. Restituisce (dati, è_gif)."""
    w, h = size
    font = _font(11 if h <= 16 else 14)
    pad = 6 if icon_color else 0
    meas = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    tw = int(meas.textlength(text, font=font))
    bbox = meas.textbbox((0, 0), text, font=font)
    ty = (h - (bbox[3] - bbox[1])) // 2 - bbox[1]

    def frame(x: int) -> Image.Image:
        im = Image.new("RGB", (w, h), (0, 0, 0)); d = ImageDraw.Draw(im); d.fontmode = "1"
        d.text((x, ty), text, fill=color, font=font)
        if bold:
            d.text((x + 1, ty), text, fill=color, font=font)
        if icon_color:
            d.rectangle([0, 0, pad - 2, h - 1], fill=(0, 0, 0)); d.ellipse([0, h // 2 - 2, 4, h // 2 + 2], fill=icon_color)
        return im

    if tw + pad <= w:
        buf = io.BytesIO(); frame(pad + (w - pad - tw) // 2).save(buf, "PNG"); return buf.getvalue(), False
    frames = [frame(x) for x in range(w, -tw - 2, -2)]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=45, loop=0, disposal=1, optimize=False)
    return buf.getvalue(), True


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
        for p in packets(data, 1, gif):
            await self._client.write_gatt_char(SVC_WRITE, p, response=False)
            await asyncio.sleep(0.015)

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

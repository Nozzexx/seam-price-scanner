#!/usr/bin/env python3
"""
S.E.A.M. Price Scanner
======================

Reads a Star Citizen commodity terminal from your screen and reports every
price to S.E.A.M. in one go.

  Ctrl+Shift+F9    scan the terminal's BUY tab
  Ctrl+Shift+F10   scan the terminal's SELL tab

How it stays safe for your account: it never touches the game process, its
memory or its network traffic. It takes an ordinary screenshot of the monitor
the game is on (Easy Anti-Cheat does not object to screenshots) and reads it
with the text recognition built into Windows.

Every scan: capture -> read -> match commodity names and prices -> upload the
screenshot as evidence -> submit all prices in one call -> beep. Scans that
cannot be sent (offline, expired sign-in) are queued and retried.

Run:  tools\\scanner\\run-scanner.bat     (or: python seam_scanner.py)
Test: python seam_scanner.py --image terminal.png --mode sell --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import ctypes.wintypes as wt
import difflib
import io
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.3.0"
# The live S.E.A.M. site. The publishable key is public by design (the website ships it
# to every visitor); what a signed-in user may do is enforced by the database.
DEFAULT_SITE_URL = "https://ptumimzylfskitbnlfmf.supabase.co"
DEFAULT_ANON_KEY = "sb_publishable_XFJKM55eK5AJR4i7ucFFGQ_TXqO8OPY"
SOURCE_URL = "https://github.com/Nozzexx/seam-price-scanner"
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "SEAM" / "scanner"
CONFIG = APP_DIR / "config.json"
QUEUE_DIR = APP_DIR / "queue"
LOG_FILE = APP_DIR / "scans.log"

# --------------------------------------------------------------------------
# console + sound
# --------------------------------------------------------------------------

SINKS: list = []  # callables(str) that also receive every message (the window's log)


def say(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    try:
        print(line, flush=True)
    except Exception:  # no console (windowed app)
        pass
    for sink in list(SINKS):
        try:
            sink(line)
        except Exception:
            pass


def beep(kind: str) -> None:
    try:
        import winsound
        tones = {"start": [(880, 60)], "ok": [(1046, 90), (1318, 120)], "partial": [(988, 120), (784, 160)], "fail": [(392, 180), (330, 260)]}
        for f, d in tones.get(kind, []):
            winsound.Beep(f, d)
    except Exception:
        pass


def log_line(entry: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

# The sign-in token is encrypted with Windows DPAPI (CryptProtectData), so only your
# Windows account on this PC can read it; copying config.json elsewhere is useless.
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # dwFlags 0x1 = CRYPTPROTECT_UI_FORBIDDEN
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):
        raise OSError("Windows could not encrypt or decrypt the saved sign-in")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect_secret(text: str) -> str:
    return base64.b64encode(_dpapi(text.encode(), True)).decode()


def unprotect_secret(blob: str) -> str | None:
    try:
        return _dpapi(base64.b64decode(blob), False).decode()
    except Exception:
        return None


def load_config() -> dict:
    cfg = {}
    if CONFIG.is_file():
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    # Developers can point it elsewhere with SEAM_SITE_URL / SEAM_ANON_KEY; everyone
    # else uses the live S.E.A.M. site built in.
    cfg["site_url"] = cfg.get("site_url") or os.environ.get("SEAM_SITE_URL") or DEFAULT_SITE_URL
    cfg["anon_key"] = cfg.get("anon_key") or os.environ.get("SEAM_ANON_KEY") or DEFAULT_ANON_KEY
    # Sign-ins saved by 1.2 and earlier were plain text: encrypt them now.
    if cfg.get("refresh_token"):
        cfg["refresh_token_dpapi"] = protect_secret(cfg.pop("refresh_token"))
        save_config(cfg)
    cfg.setdefault("upload_screenshot", True)
    cfg.setdefault("hotkey_buy", "ctrl+shift+f9")
    cfg.setdefault("hotkey_sell", "ctrl+shift+f10")
    cfg.setdefault("min_match", 0.84)
    return cfg


def save_config(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

# --------------------------------------------------------------------------
# Supabase (REST, Auth, Storage) with plain urllib
# --------------------------------------------------------------------------

class SiteError(Exception):
    pass


class Site:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.url = cfg["site_url"].rstrip("/")
        if not re.match(r"^https://|^http://(127\.0\.0\.1|localhost)(:\d+)?$", self.url):
            raise SiteError(f"refusing to send your sign-in over an unencrypted connection: {self.url}")
        self.key = cfg["anon_key"]
        self.access: str | None = None
        self.access_exp = 0.0
        self.user_id: str | None = None

    def _req(self, method: str, path: str, body=None, headers=None, raw: bytes | None = None, timeout=30):
        h = {"apikey": self.key, "Authorization": f"Bearer {self.access or self.key}"}
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode() or "null"
                return json.loads(text)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            try:
                j = json.loads(detail)
                detail = j.get("message") or j.get("error_description") or j.get("msg") or j.get("error") or detail
            except Exception:
                pass
            raise SiteError(re.sub(r"^[A-Z0-9]{5}:\s*", "", str(detail)))
        except urllib.error.URLError as e:
            raise SiteError(f"cannot reach {self.url} ({e.reason})")

    # ---- auth ----
    def _take_session(self, s: dict) -> None:
        self.access = s["access_token"]
        self.access_exp = time.time() + int(s.get("expires_in", 3600)) - 60
        self.user_id = s["user"]["id"]
        self.cfg["refresh_token_dpapi"] = protect_secret(s["refresh_token"])
        self.cfg["email"] = s["user"].get("email")
        save_config(self.cfg)

    def login(self, email: str, password: str) -> None:
        self.access = None
        s = self._req("POST", "/auth/v1/token?grant_type=password", {"email": email, "password": password})
        self._take_session(s)

    def ensure_session(self) -> None:
        if self.access and time.time() < self.access_exp:
            return
        token = unprotect_secret(self.cfg["refresh_token_dpapi"]) if self.cfg.get("refresh_token_dpapi") else None
        if not token:
            raise SiteError("not signed in (type: login)")
        self.access = None
        try:
            s = self._req("POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": token})
        except SiteError as e:
            self.cfg.pop("refresh_token_dpapi", None)
            save_config(self.cfg)
            raise SiteError(f"sign-in expired, type: login ({e})")
        self._take_session(s)

    def logout(self) -> None:
        self.access = None
        self.cfg.pop("refresh_token_dpapi", None)
        save_config(self.cfg)

    # ---- data ----
    def commodities(self) -> list[dict]:
        return self._req("GET", "/rest/v1/commodities?select=slug,name,category&active=eq.true&order=name")

    def terminals(self) -> list[dict]:
        return self._req("GET", "/rest/v1/terminals_directory?select=terminal_slug,terminal_name,location_name,terminal_kind,system_name&terminal_kind=eq.commodity&order=terminal_name")

    def upload_screenshot(self, jpeg: bytes) -> str:
        self.ensure_session()
        path = f"{self.user_id}/scan-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.jpg"
        self._req("POST", f"/storage/v1/object/price-screenshots/{path}", raw=jpeg, headers={"Content-Type": "image/jpeg", "x-upsert": "false"})
        return path

    def submit_scan(self, terminal: str, mode: str, items: list[dict], screenshot: str | None) -> dict:
        self.ensure_session()
        return self._req("POST", "/rest/v1/rpc/submit_price_scan", {"terminal_slug": terminal, "mode": mode, "items": items, "screenshot_path": screenshot, "client_version": f"scanner/{VERSION}"})

    def my_scans(self, limit: int = 20) -> list[dict]:
        self.ensure_session()
        return self._req("POST", "/rest/v1/rpc/my_price_scans", {"max_rows": limit}) or []

    def withdraw_scan(self, scan_id: str, reason: str | None = None) -> dict:
        self.ensure_session()
        return self._req("POST", "/rest/v1/rpc/withdraw_price_scan", {"scan": scan_id, "reason": reason})

    def move_scan(self, scan_id: str, terminal: str) -> dict:
        self.ensure_session()
        return self._req("POST", "/rest/v1/rpc/move_price_scan", {"scan": scan_id, "terminal_slug": terminal})

# --------------------------------------------------------------------------
# screen capture (multi-monitor)
# --------------------------------------------------------------------------

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # physical pixels on every monitor
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def window_exe(hwnd: int) -> str:
    """File name of the program that owns a window (e.g. StarCitizen.exe)."""
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    kernel32 = ctypes.windll.kernel32
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return Path(buf.value).name
    finally:
        kernel32.CloseHandle(h)


def find_game_window() -> int | None:
    """The Star Citizen game window: title starts with "Star Citizen" AND the window
    belongs to StarCitizen.exe (so a browser tab about the game never matches)."""
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.strip().lower().startswith("star citizen") and window_exe(hwnd).lower() == "starcitizen.exe":
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def monitor_rect_for(hwnd: int | None) -> tuple[dict, str]:
    """The rectangle of the monitor the game is on, else the one under the mouse."""
    if hwnd:
        hmon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        why = "game window"
    else:
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        hmon = user32.MonitorFromPoint(pt, 2)
        why = "mouse (game window not found)"
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    r = mi.rcMonitor
    return {"left": r.left, "top": r.top, "width": r.right - r.left, "height": r.bottom - r.top}, why


def game_client_rect(hwnd: int, monitor: dict) -> dict:
    """The game's drawing area on screen, clipped to its monitor (so a windowed game
    is captured without whatever else is on that screen)."""
    rc = wt.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return monitor
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    left = max(pt.x, monitor["left"])
    top = max(pt.y, monitor["top"])
    right = min(pt.x + rc.right, monitor["left"] + monitor["width"])
    bottom = min(pt.y + rc.bottom, monitor["top"] + monitor["height"])
    if right - left < 640 or bottom - top < 360:  # minimised or odd: fall back to the monitor
        return monitor
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def capture():
    """One screenshot of the game. Returns (image, rect, why, is_game): is_game is False
    when the game was not found and the monitor under the mouse was used instead;
    such captures are read but never uploaded."""
    import mss
    from PIL import Image
    hwnd = find_game_window()
    rect, why = monitor_rect_for(hwnd)
    if hwnd:
        rect = game_client_rect(hwnd, rect)
    with mss.mss() as s:
        shot = s.grab(rect)
    img = Image.frombytes("RGB", shot.size, shot.rgb)
    return img, rect, why, bool(hwnd)

# --------------------------------------------------------------------------
# OCR (Windows.Media.Ocr)
# --------------------------------------------------------------------------

@dataclass
class Word:
    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


_ocr_engine = None


async def _ocr_async(png: bytes):
    global _ocr_engine
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
    if _ocr_engine is None:
        _ocr_engine = OcrEngine.try_create_from_language(Language("en-US"))
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    return await _ocr_engine.recognize_async(bitmap)


def _ocr_pass(img, scale: float, ox: float = 0.0, oy: float = 0.0) -> list[Word]:
    from PIL import ImageOps
    big = ImageOps.autocontrast(ImageOps.grayscale(img)).resize((int(img.width * scale), int(img.height * scale)))
    max_dim = 9000  # Windows OCR refuses images larger than OcrEngine.MaxImageDimension (10000)
    if max(big.size) > max_dim:
        f = max_dim / max(big.size)
        big = big.resize((int(big.width * f), int(big.height * f)))
        scale *= f
    buf = io.BytesIO()
    big.save(buf, "PNG")
    result = asyncio.run(_ocr_async(buf.getvalue()))
    words: list[Word] = []
    for line in result.lines:
        for wd in line.words:
            r = wd.bounding_rect
            words.append(Word(wd.text, ox + r.x / scale, oy + r.y / scale, r.width / scale, r.height / scale))
    return words


def ocr(img, scale: float | None = None) -> list[Word]:
    """Read every word with its box. The image is enlarged and contrast-stretched first:
    terminal text is small and light-on-dark, which Windows OCR reads better big."""
    return _ocr_pass(img, scale or (2.0 if img.width <= 2600 else 1.4))

# --------------------------------------------------------------------------
# parsing a terminal screen
# --------------------------------------------------------------------------

PRICE_RE = re.compile(r"^[¤$]?(\d{1,3}(?:[,.\s]\d{3})+|\d+(?:[.,]\d+)?)\s*([KkMm])?$")
INVENTORY = [("out of stock", "out_of_stock"), ("no stock", "out_of_stock"), ("very low", "low"), ("low", "low"),
             ("medium", "medium"), ("very high", "max"), ("high", "high"), ("max", "max"), ("full", "max")]


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def parse_price(tok: str) -> int | None:
    t = tok.replace("aUEC", "").replace("AUEC", "").replace("/SCU", "").replace("/scu", "").strip()
    m = PRICE_RE.match(t)
    if not m:
        return None
    num, suffix = m.group(1), (m.group(2) or "").upper()
    if suffix:
        v = float(num.replace(",", "."))
        v *= 1_000 if suffix == "K" else 1_000_000
    else:
        digits = re.sub(r"[,.\s]", "", num) if re.search(r"[,.\s]\d{3}$", num) else num.replace(",", ".")
        try:
            v = float(digits)
        except ValueError:
            return None
    return int(round(v)) if 0 < v <= 10_000_000 else None


_OCR_DIGIT = str.maketrans({"l": "1", "I": "1", "|": "1", "O": "0", "o": "0", "S": "5", "s": "5", "B": "8"})


def _clean_number(raw: str) -> int | None:
    """'l,446' -> 1446, '3.6S8' -> 3658, '1, 142' -> 1142, '6, ooo' -> 6000. Groups after the
    first must be exactly three digits, else the read is ambiguous and rejected."""
    t = re.sub(r"\s+", "", raw.strip().translate(_OCR_DIGIT))
    if not re.fullmatch(r"\d{1,3}([.,]\d{3})*|\d+", t):
        return None
    v = int(re.sub(r"[.,]", "", t))
    return v if 0 < v <= 10_000_000 else None


SCU = r"s\s*c\s*(?:u|lj|ij|l|j|i|\|)"
UNIT_PRICE_RE = re.compile(r"([0-9lIOoSsB|][0-9lIOoSsB|.,\s]*?)\s*(?:a?uec)?\s*/\s*" + SCU, re.I)
STOCK_RE = re.compile(r"(?<![/\w])([0-9lIOoSsB|][0-9lIOoSsB|.,\s]*?)\s*" + SCU + r"(?![a-z])(?!\s*/)", re.I)


def unit_price_in(text: str) -> int | None:
    """A price written as '<currency glyph><number>/SCU'. The glyph (read as Ä, A, n, a, ¤...)
    is not a digit, so the number run starts after it."""
    for m in UNIT_PRICE_RE.finditer(text):
        v = _clean_number(m.group(1))
        if v is not None:
            return v
    return None


def _trusted_unit_price(text: str) -> int | None:
    m = UNIT_PRICE_RE.search(text)
    if not m:
        return None
    v = _clean_number(m.group(1))
    if v is None:
        return None
    lead = text[:m.start(1)].strip()
    glyph = bool(lead) and not lead[-1].isdigit()
    return v if glyph or re.search(r"uec", text[m.start(1):m.end()], re.I) else None


def read_unit_price(seg, img) -> tuple[int | None, bool]:
    """(price, looked_like_a_price). Reads the segment; when the currency glyph is missing,
    digits next to it were probably lost, so the spot is re-read with small variations
    (OCR on tiny crops is jittery: one pixel can turn '¤1,142' into '142'). A price is
    accepted only when two trusted re-reads agree; otherwise nothing is sent."""
    if not UNIT_PRICE_RE.search(seg.text):
        return None, False
    v = _trusted_unit_price(seg.text)
    if v is not None or img is None:
        return v, True
    base = (seg.x - seg.h * 5, seg.y - seg.h * 0.6, seg.x_end + seg.h, seg.y + seg.h * 1.6)
    blue = img.split()[2].convert("RGB") if img.mode == "RGB" else img
    votes: dict[int, int] = {}
    for dx, dy in ((0, 0), (1, 1), (-2, 0), (3, 2), (-1, -2)):
        box = (int(max(0, base[0] + dx)), int(max(0, base[1] + dy)),
               int(min(img.width, base[2] + dx)), int(min(img.height, base[3] + dy)))
        for src in (img, blue):
            crop = src.crop(box)
            for scale in (2.0, 3.0):
                words = ocr(crop, scale)
                r = _trusted_unit_price(" ".join(w.text for w in sorted(words, key=lambda w: w.x)))
                if r is not None:
                    votes[r] = votes.get(r, 0) + 1
                    if votes[r] >= 2:
                        return r, True
    return None, True


def stock_in(text: str) -> float | None:
    """'6,000 SCU' (not followed by '/'): the terminal's stock."""
    for m in STOCK_RE.finditer(text):
        raw = m.group(1).strip()
        if raw in ("0", "o", "O"):
            return 0.0
        v = _clean_number(raw)
        if v is not None:
            return float(v)
    return None


INVENTORY_WORDS = [("out of stock", "out_of_stock"), ("no stock", "out_of_stock"), ("very low", "low"), ("very high", "max"),
                   ("max", "max"), ("full", "max"), ("high", "high"), ("medium", "medium"), ("low", "low")]


def inventory_in(text: str) -> str | None:
    """'MAX INVENTORY', 'VERY LOW INVENTORY', tolerant of misreads like 'HAX' or 'Lou'."""
    low = text.lower()
    m = re.search(r"([a-z\"'\s]{2,14})\s+inventory", low)
    if not m:
        return next((code for label, code in INVENTORY if label in low), None)
    lead = re.sub(r"[^a-z ]", "", m.group(1)).strip()
    parts = lead.split()
    cands = {lead, " ".join(parts[-2:]), parts[-1]} if parts else {lead}
    best, code = 0.0, None
    for label, c in INVENTORY_WORDS:
        for cand in cands:
            r = difflib.SequenceMatcher(None, cand, label).ratio()
            if r > best:
                best, code = r, c
    return code if best >= 0.6 else None


def mend_numbers(tokens: list[str]) -> list[str]:
    """Undo common OCR splits: '4,' '107' -> '4,107'; '12' ',000' -> '12,000'; a lone 'o' before SCU -> '0'."""
    out: list[str] = []
    for tok in tokens:
        t = tok
        if out and re.fullmatch(r"\d{1,3}[,.]", out[-1]) and re.fullmatch(r"\d{3}([,.]\d{3})*([KkMm])?", t):
            out[-1] = out[-1] + t
            continue
        if out and re.fullmatch(r"\d{1,3}([,.]\d{3})*", out[-1]) and re.fullmatch(r"[,.]\d{3}([,.]\d{3})*([KkMm])?", t):
            out[-1] = out[-1] + t
            continue
        out.append(t)
    for i, t in enumerate(out):
        if t in ("o", "O") and i + 1 < len(out) and out[i + 1].lower().startswith("scu"):
            out[i] = "0"
    return out


def group_rows(words: list[Word]) -> list[list[Word]]:
    """Words share a line when their vertical extents overlap by most of the smaller word's
    height and their sizes are similar. A small caption tucked under a big name stays separate."""
    rows: list[list[Word]] = []
    bands: list[tuple[float, float, float]] = []  # top, bottom, typical height
    for w in sorted(words, key=lambda w: w.cy):
        placed = False
        for i in range(len(rows) - 1, max(-1, len(rows) - 6), -1):
            top, bottom, hh = bands[i]
            overlap = min(bottom, w.y + w.h) - max(top, w.y)
            similar = 0.6 <= w.h / hh <= 1.6
            if overlap >= 0.55 * min(w.h, hh) and similar:
                rows[i].append(w)
                bands[i] = (min(top, w.y), max(bottom, w.y + w.h), (hh * (len(rows[i]) - 1) + w.h) / len(rows[i]))
                placed = True
                break
        if not placed:
            rows.append([w])
            bands.append((w.y, w.y + w.h, w.h))
    rows = [sorted(r, key=lambda w: w.x) for r in rows]
    return sorted(rows, key=lambda r: sum(w.cy for w in r) / len(r))


@dataclass
class Match:
    slug: str
    name: str
    price: int
    score: float
    stock: float | None = None
    inventory: str | None = None
    row_text: str = ""


@dataclass
class ParseResult:
    matches: list[Match] = field(default_factory=list)
    unmatched_rows: list[str] = field(default_factory=list)
    terminal_hint: str | None = None
    detected_mode: str | None = None   # 'buy' | 'sell' from the highlighted tab, when visible
    kiosk: str | None = None           # screen title, e.g. PHARMACY or COMMODITIES
    is_commodity_terminal: bool = True
    is_item_shop: bool = False         # item kiosks (Quick Buy buttons): not commodity prices


@dataclass
class Segment:
    """A run of words on one line, separated from its neighbours by a wide gap (a card or a column)."""
    words: list[Word]

    @property
    def x(self) -> float:
        return self.words[0].x

    @property
    def x_end(self) -> float:
        return self.words[-1].x + self.words[-1].w

    @property
    def cy(self) -> float:
        return sum(w.cy for w in self.words) / len(self.words)

    @property
    def y(self) -> float:
        return min(w.y for w in self.words)

    @property
    def h(self) -> float:
        return max(w.h for w in self.words)

    @property
    def tokens(self) -> list[str]:
        return [w.text for w in self.words]

    @property
    def text(self) -> str:
        return " ".join(self.tokens)


def segments(words: list[Word]) -> list[Segment]:
    """Lines, then split wherever the horizontal gap is much wider than a word space."""
    out: list[Segment] = []
    for row in group_rows(words):
        hs = sorted(w.h for w in row)
        gap_limit = max(40.0, hs[len(hs) // 2] * 3.2)
        cur = [row[0]]
        for w in row[1:]:
            prev = cur[-1]
            if w.x - (prev.x + prev.w) > gap_limit:
                out.append(Segment(cur))
                cur = [w]
            else:
                cur.append(w)
        out.append(Segment(cur))
    return out


def best_name(tokens: list[str], names: dict[str, tuple[str, str]], min_score: float):
    """Try every run of up to 6 words (starting in the first three) against the known names."""
    best = (0.0, None, 0)
    for start in range(0, min(3, len(tokens))):
        for end in range(start + 1, min(len(tokens), start + 6) + 1):
            cand = norm(" ".join(tokens[start:end]))
            if len(cand) < 3:
                continue
            variants = {cand, cand.replace("v", "w"), cand.replace("rn", "m"), cand.replace("0", "o"), cand.replace("1", "l")}
            for key, (slug, name) in names.items():
                if abs(len(key) - len(cand)) > max(3, len(key) // 3):
                    continue
                score = max(difflib.SequenceMatcher(None, v, key).ratio() for v in variants)
                if score > best[0]:
                    best = (score, (slug, name), end)
    return best if best[0] >= min_score else (best[0], None, 0)


VOLUME_UNITS = ("scu", "pscu", "µscu", "uscu", "mscu", "cscu", "kscu")


def prices_in(tokens: list[str]) -> tuple[int | None, float | None]:
    """First price and first stock figure in a run of tokens. Numbers followed by an SCU unit are
    volumes or stock, never prices; '¤' (often read as '�' or 'X') may sit in front of a price."""
    tokens = mend_numbers(tokens)
    price = None
    stock = None
    for j, tok in enumerate(tokens):
        nxt = tokens[j + 1].lower() if j + 1 < len(tokens) else ""
        prev = tokens[j - 1].lower() if j > 0 else ""
        if prev.rstrip(":") in ("volume", "vol", "wallet", "balance"):
            continue
        if nxt.startswith(VOLUME_UNITS) or nxt in VOLUME_UNITS:
            if nxt.startswith("scu") and price is not None and stock is None:
                stock = 0.0 if tok in ("0", "o", "O") else parse_price(tok)
            continue
        joined = tok + (nxt if nxt in ("k", "m") else "")
        p = parse_price(joined)
        if p is not None and price is None:
            price = p
    return price, stock


def detect_tab(img, words: list[Word]) -> str | None:
    """The selected BUY/SELL tab is drawn as a filled, bright box; the other is an outline.
    Compare the brightness around the two tab labels near the top of the screen."""
    if img is None:
        return None
    from PIL import ImageStat
    top = img.height * 0.35
    tabs = {}
    for w in words:
        t = w.text.strip().upper()
        if t in ("BUY", "SELL") and w.y < top and t not in tabs:
            pad_x, pad_y = w.w * 0.6, w.h * 0.5
            box = (int(max(0, w.x - pad_x)), int(max(0, w.y - pad_y)), int(min(img.width, w.x + w.w + pad_x)), int(min(img.height, w.y + w.h + pad_y)))
            tabs[t] = ImageStat.Stat(img.crop(box).convert("L")).mean[0]
    if len(tabs) < 2:
        return None
    buy, sell = tabs["BUY"], tabs["SELL"]
    if abs(buy - sell) < 18:  # too close to call
        return None
    return "buy" if buy > sell else "sell"


LABEL_RE = re.compile(r"\binventory\b|\bquantity\b|out of stock|^\W*(very\s+)?(low|high)\W*$|^\W*(medium|max|full)\W*$", re.I)


def parse_screen(words: list[Word], commodities: list[dict], terminals: list[dict], min_score: float, img=None) -> ParseResult:
    names = {norm(c["name"]): (c["slug"], c["name"]) for c in commodities}
    res = ParseResult()
    full_text = norm(" ".join(w.text for w in words))

    # Screen title: the largest text in the top quarter (PHARMACY, COMMODITIES, ...).
    head = [w for w in words if (img is None or w.y < img.height * 0.25) and sum(ch.isalpha() for ch in w.text) >= 3]
    if head:
        big = max(head, key=lambda w: w.h)
        res.kiosk = big.text.strip().upper()
        res.is_commodity_terminal = any(k in full_text for k in ("commodit", "tradeandcommerce", "cargoterminal")) or res.kiosk.startswith(("COMMOD", "TRADE"))
    res.detected_mode = detect_tab(img, words)
    res.is_item_shop = "quickbuy" in full_text and not res.is_commodity_terminal

    for t in terminals:
        for label in (t.get("location_name"), t.get("terminal_name")):
            if label and len(norm(label)) >= 5 and norm(label) in full_text:
                res.terminal_hint = t["terminal_slug"]
                break
        if res.terminal_hint:
            break

    segs = sorted(segments(words), key=lambda s: (s.cy, s.x))
    named: list[tuple[Segment, float, tuple[str, str], int]] = []
    for idx, seg in enumerate(segs):
        if LABEL_RE.search(seg.text):
            continue
        score, hit, used = best_name(seg.tokens, names, min_score)
        if not hit:
            # Long names wrap onto a second line in the same column.
            nxt = next((b for b in segs[idx + 1:] if 0 < b.cy - seg.cy <= seg.h * 1.9 and abs(b.x - seg.x) <= seg.h * 1.5 and 0.7 <= b.h / seg.h <= 1.4), None)
            if nxt is not None:
                s2, hit2, used2 = best_name(seg.tokens + nxt.tokens, names, min_score)
                if hit2 and used2 > len(seg.tokens):
                    score, hit, used = s2, hit2, len(seg.tokens)
                    seg = Segment(seg.words + nxt.words)
                    used = used2
        if hit:
            named.append((seg, score, hit, used))
    name_segs = {id(n[0]) for n in named}

    seen: set[str] = set()
    used_price_segs: set[int] = set()
    for seg, score, (slug, name), used in named:
        # 0. commodity terminal (4.x): the price is a '<glyph>N/SCU' line at the far right of the
        #    item card, just below the stock line; the inventory label sits under the name.
        nxt_cy = min((n[0].cy for n in named if n[0] is not seg and n[0].cy > seg.cy + seg.h * 0.8 and abs(n[0].x - seg.x) < seg.h * 6), default=float("inf"))
        band = sorted((b for b in segs if b is not seg and b.x >= seg.x_end - 2
                       and seg.cy - seg.h * 1.6 <= b.cy < min(nxt_cy - seg.h * 0.4, seg.cy + seg.h * 4.5)), key=lambda b: (b.cy, b.x))
        under = [b for b in segs if b is not seg and 0 < b.cy - seg.cy <= seg.h * 2.2 and seg.x - seg.h * 4 <= b.x < seg.x_end + seg.h * 4]
        band_text = " ".join(b.text for b in band)
        price_seg = next((b for b in band if "/" in b.text and UNIT_PRICE_RE.search(b.text)), None)
        uprice = None
        if price_seg is not None:
            used_price_segs.add(id(price_seg))
            uprice, _ = read_unit_price(price_seg, img)
            if uprice is None:
                res.unmatched_rows.append(f"{name}: price unclear ('{price_seg.text}'), not sent")
                seen.add(slug)
                continue
        if uprice is not None:
            ustock = stock_in(" ".join(b.text for b in band if b is not price_seg))
            inv = inventory_in(" ".join([seg.text] + [b.text for b in under])) or inventory_in(band_text)
            if inv is None and ustock == 0:
                inv = "out_of_stock"
            if slug not in seen:
                seen.add(slug)
                res.matches.append(Match(slug, name, uprice, round(score, 2), ustock, inv, seg.text))
            continue
        # 1. list layout: price on the same line, to the right of the name (same segment,
        #    or further columns on that line up to the next item name)
        row_tokens = list(seg.tokens[used:])
        row_text = seg.text
        right = sorted((r for r in segs if r is not seg and r.x > seg.x_end and abs(r.cy - seg.cy) <= seg.h * 0.6), key=lambda r: r.x)
        for r in right:
            if id(r) in name_segs:
                break
            row_tokens += r.tokens
            row_text += " " + r.text
        price, stock = prices_in(row_tokens)
        # 2. card layout: price a few lines below, in the same column, before the next item
        if price is None:
            for below in segs:
                if below.cy <= seg.cy or below.cy - seg.cy > seg.h * 7:
                    continue
                if abs(below.x - seg.x) > max(160.0, seg.h * 6):
                    continue
                if id(below) in name_segs:
                    break
                price, st = prices_in(below.tokens)
                stock = stock if stock is not None else st
                if price is not None:
                    break
        inv = next((code for label, code in INVENTORY if label in row_text.lower()), None)
        if price is None:
            res.unmatched_rows.append(f"{name}: no price found near '{seg.text}'")
            continue
        if slug in seen:
            continue
        seen.add(slug)
        res.matches.append(Match(slug, name, price, round(score, 2), stock, inv, seg.text))

    # 4. A '/SCU' price with no name matched above it means the whole-screen read skipped the
    #    item name (it happens on real terminals). Re-read just that card, enlarged.
    name_xs = [m_seg.x for m_seg, *_ in named]
    if img is not None and name_xs:
        col_x = min(name_xs)
        for ps in segs:
            if id(ps) in used_price_segs or "/" not in ps.text or ps.x <= col_x:
                continue
            uprice, looked = read_unit_price(ps, img)
            if not looked:
                continue
            box = (int(max(0, col_x - ps.h * 6)), int(max(0, ps.cy - ps.h * 5.5)), int(ps.x), int(min(img.height, ps.cy + ps.h)))
            if box[2] - box[0] < 40 or box[3] - box[1] < 20:
                continue
            card = ocr(img.crop(box), 3.0)
            card_segs = sorted(segments(card), key=lambda c: c.cy)
            for cs in card_segs:
                cscore, chit, _ = best_name(cs.tokens, names, min_score)
                if chit and chit[0] not in seen:
                    seen.add(chit[0])
                    if uprice is None:
                        res.unmatched_rows.append(f"{chit[1]}: price unclear ('{ps.text}'), not sent")
                        break
                    band_text = " ".join(b.text for b in segs if b.x >= ps.x - ps.h and ps.cy - ps.h * 4.5 <= b.cy <= ps.cy + ps.h * 0.5)
                    inv = inventory_in(" ".join(c.text for c in card_segs))
                    res.matches.append(Match(chit[0], chit[1], uprice, round(cscore, 2), stock_in(band_text), inv, cs.text + " (card re-read)"))
                    break
    return res

# --------------------------------------------------------------------------
# the scan pipeline
# --------------------------------------------------------------------------

class Scanner:
    def __init__(self, cfg: dict, dry_run: bool = False):
        self.cfg = cfg
        self.site = Site(cfg)
        self.dry_run = dry_run
        self.commodities: list[dict] = []
        self.terminals: list[dict] = []
        self.lock = threading.Lock()
        self.listener = None  # callable(kind: str, data: dict), set by the window

    def _emit(self, kind: str, **data) -> None:
        if self.listener:
            try:
                self.listener(kind, data)
            except Exception:
                pass

    def refresh_reference(self) -> None:
        self.commodities = self.site.commodities()
        self.terminals = self.site.terminals()
        say(f"Loaded {len(self.commodities)} commodities and {len(self.terminals)} commodity terminals from {self.site.url}.")

    def terminal_label(self, slug: str | None) -> str:
        t = next((t for t in self.terminals if t["terminal_slug"] == slug), None)
        return f"{t['terminal_name']} ({t['location_name']})" if t else (slug or "none")

    def set_terminal(self, query: str) -> None:
        q = norm(query)
        words = [norm(w) for w in re.split(r"[\s,()]+", query) if norm(w)]

        def haystack(t):
            return norm(" ".join(str(t.get(k) or "") for k in ("terminal_slug", "terminal_name", "location_name", "system_name")))

        exact = [t for t in self.terminals if norm(t["terminal_slug"]) == q]
        # Every word must appear: 'nyx gateway stanton' never lands on 'Stanton Gateway (Nyx)'
        # by accident, because both match and the scanner then asks which one.
        # Words in the order typed ('nyx gateway stanton') beat the same words in any order.
        phrase = [t for t in self.terminals if q and q in haystack(t)]
        hits = exact or phrase or [t for t in self.terminals if all(w in haystack(t) for w in words)]
        if len(hits) == 1:
            self.cfg["terminal"] = hits[0]["terminal_slug"]
            save_config(self.cfg)
            say(f"Terminal set: {self.terminal_label(self.cfg['terminal'])}")
            self._emit("terminal", slug=self.cfg["terminal"])
        elif not hits:
            say(f"No commodity terminal matches '{query}'. Type: terminals")
        else:
            say("More than one terminal matches; be more specific:")
            for t in hits[:12]:
                say(f"   {t['terminal_slug']:<28} {t['terminal_name']} ({t['location_name']})")

    def scan(self, mode: str, image=None, source_label: str = "") -> None:
        if not self.lock.acquire(blocking=False):
            say("Still working on the previous scan.")
            return
        try:
            beep("start")
            t0 = time.time()
            is_game = True
            if image is None:
                img, rect, why, is_game = capture()
                say(f"{mode.upper()} scan: captured {rect['width']}x{rect['height']} at ({rect['left']},{rect['top']}) via {why}.")
            else:
                img, why = image, source_label
                say(f"{mode.upper()} scan of {source_label}.")
            words = ocr(img)
            parsed = parse_screen(words, self.commodities, self.terminals, float(self.cfg["min_match"]), img)
            if parsed.detected_mode and parsed.detected_mode != mode:
                say(f"The {parsed.detected_mode.upper()} tab is selected on screen, so these are {parsed.detected_mode} prices (you pressed {mode.upper()}).")
                mode = parsed.detected_mode
            elif parsed.detected_mode:
                say(f"{mode.upper()} tab confirmed on screen.")
            if parsed.is_item_shop:
                beep("fail")
                say(f"This is a shop kiosk ({parsed.kiosk or 'items'}), not a commodity terminal. S.E.A.M. tracks commodity prices only, so nothing was sent.")
                self._emit("done", status="shop", message=f"{parsed.kiosk or 'Shop'} kiosk: shop items are not tracked. Nothing sent.")
                return
            terminal = parsed.terminal_hint or self.cfg.get("terminal")
            self._emit("read", mode=mode, terminal=parsed.terminal_hint or self.cfg.get("terminal"),
                       matches=[{"slug": m.slug, "name": m.name, "price": m.price, "stock": m.stock, "inventory": m.inventory} for m in parsed.matches],
                       skipped=list(parsed.unmatched_rows), seconds=round(time.time() - t0, 1))
            say(f"Read {len(words)} words in {time.time() - t0:.1f}s; matched {len(parsed.matches)} prices. Terminal: {self.terminal_label(terminal)}{' (recognised on screen)' if parsed.terminal_hint else ''}.")
            for m in parsed.matches:
                extra = (f"  stock {m.stock:g}" if m.stock is not None else "") + (f"  {m.inventory}" if m.inventory else "")
                say(f"   {m.name:<28} {m.price:>12,} aUEC   match {m.score:.2f}{extra}")
            for u in parsed.unmatched_rows[:6]:
                say(f"   (skipped) {u}")
            APP_DIR.mkdir(parents=True, exist_ok=True)
            img.save(APP_DIR / "last-scan.png")
            if not parsed.matches:
                beep("fail")
                say("Nothing to send. Is a commodity terminal open on the game's monitor? The capture was saved as last-scan.png.")
                self._emit("done", status="nothing", message="No prices found. Is a commodity terminal open on the game's monitor?")
                return
            if not terminal:
                beep("fail")
                say("Which terminal is this? Type: terminal <name>   then scan again. (Nothing sent.)")
                self._emit("done", status="no_terminal", message="Pick the terminal you are at, then scan again. Nothing sent.")
                return
            items = [{"commodity": m.slug, "price": m.price, **({"stock": m.stock} if m.stock is not None else {}), **({"inventory": m.inventory} if m.inventory else {})} for m in parsed.matches]
            # The screenshot goes with the scan so moderators can check it, unless the game
            # window was not found (then it could be anything on your screen) or you
            # turned it off ("upload_screenshot": false in config.json).
            jpeg = to_jpeg(img) if is_game and self.cfg.get("upload_screenshot", True) else None
            if jpeg is None:
                say("No screenshot attached: " + ("the Star Citizen window was not found." if not is_game else "screenshot upload is turned off in your settings."))
            if self.dry_run:
                say(f"Dry run: would send {len(items)} {mode} prices for {terminal}.")
                self._emit("done", status="dry", message=f"Test only: {len(items)} {mode} prices read, nothing sent.")
                beep("ok")
                return
            self.send(terminal, mode, items, jpeg)
        except Exception as e:  # never let one scan kill the hotkeys
            beep("fail")
            say(f"Scan failed: {e}")
            self._emit("done", status="error", message=f"Scan failed: {e}")
        finally:
            self.lock.release()

    def send(self, terminal: str, mode: str, items: list[dict], jpeg: bytes | None, queued_file: Path | None = None) -> bool:
        t0 = time.time()
        try:
            shot = self.site.upload_screenshot(jpeg) if jpeg else None
            r = self.site.submit_scan(terminal, mode, items, shot)
        except SiteError as e:
            if queued_file is None:
                QUEUE_DIR.mkdir(parents=True, exist_ok=True)
                stem = f"{datetime.now():%Y%m%d-%H%M%S}-{mode}"
                (QUEUE_DIR / f"{stem}.json").write_text(json.dumps({"terminal": terminal, "mode": mode, "items": items}), encoding="utf-8")
                if jpeg:
                    (QUEUE_DIR / f"{stem}.jpg").write_bytes(jpeg)
                say(f"Could not send ({e}). Queued; it will be retried (or type: retry).")
                self._emit("done", status="queued", message=f"Could not send ({e}). Saved; it will be sent automatically.")
            else:
                say(f"Retry failed ({e}).")
            beep("fail")
            return False
        ok, pend, fail = r.get("accepted", 0), r.get("pending", 0), r.get("failed", 0)
        say(f"Sent in {time.time() - t0:.1f}s: {ok} accepted, {pend} awaiting review, {fail} refused.")
        for x in r.get("results", []):
            if x.get("status") == "failed":
                say(f"   refused {x['commodity']}: {x.get('error')}")
            elif x.get("flags"):
                say(f"   {x['commodity']}: held for review ({', '.join(x['flags'])})")
        log_line({"at": datetime.now().isoformat(), "terminal": terminal, "mode": mode, "items": len(items), "accepted": ok, "pending": pend, "failed": fail})
        self._emit("done", status="sent" if fail == 0 else "partial", accepted=ok, pending=pend, failed=fail,
                   scan_id=r.get("scan_id"), terminal=terminal,
                   results=r.get("results", []), retried=queued_file is not None,
                   message=f"Sent: {ok} accepted, {pend} awaiting review, {fail} refused.")
        beep("ok" if fail == 0 else "partial")
        return True

    def retry_queue(self) -> None:
        if not QUEUE_DIR.is_dir():
            return
        pending = sorted(QUEUE_DIR.glob("*.json"))
        if not pending:
            return
        say(f"Retrying {len(pending)} queued scan(s)...")
        for f in pending:
            data = json.loads(f.read_text(encoding="utf-8"))
            jpg = f.with_suffix(".jpg")
            if self.send(data["terminal"], data["mode"], data["items"], jpg.read_bytes() if jpg.is_file() else None, queued_file=f):
                f.unlink(missing_ok=True)
                jpg.unlink(missing_ok=True)
            else:
                break


def to_jpeg(img) -> bytes:
    small = img if img.width <= 1920 else img.resize((1920, int(img.height * 1920 / img.width)))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=72, optimize=True)
    return buf.getvalue()

# --------------------------------------------------------------------------
# global hotkeys (RegisterHotKey; works while the game has focus)
# --------------------------------------------------------------------------

MODS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000


def vk_for(key: str) -> int:
    key = key.lower()
    if re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", key):
        return 0x70 + int(key[1:]) - 1
    if len(key) == 1 and key.isalnum():
        return ord(key.upper())
    raise ValueError(f"unsupported key: {key}")


def parse_hotkey(spec: str) -> tuple[int, int]:
    parts = [p.strip() for p in spec.lower().split("+")]
    mods = 0
    for p in parts[:-1]:
        mods |= MODS[p]
    return mods | MOD_NOREPEAT, vk_for(parts[-1])


def hotkey_loop(bindings: dict[int, tuple[str, int, int]], events: "queue.Queue[str]") -> None:
    for hid, (_, mods, vk) in bindings.items():
        if not user32.RegisterHotKey(None, hid, mods, vk):
            events.put(f"!hotkey {hid}")
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == WM_HOTKEY and msg.wParam in bindings:
            events.put(bindings[msg.wParam][0])

# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

HELP = """Commands:
  login                 sign in with your S.E.A.M. account (email + password)
  logout                forget the saved sign-in
  terminal <name>       set the terminal you are standing at (e.g. terminal tressler)
  terminals [filter]    list commodity terminals
  scan buy|sell         scan now without the hotkey
  test <image> buy|sell read a saved screenshot (sends unless started with --dry-run)
  retry                 resend queued scans
  status                show sign-in, terminal and hotkeys
  quit"""


class ConsoleReader:
    """Reads typed lines on a background thread, one at a time, only when asked.
    Hotkeys keep working while it waits because they arrive on another thread."""

    def __init__(self, events: "queue.Queue[str]"):
        self.events = events
        self.go = threading.Semaphore(0)
        self.prompt = ""
        self.secret = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        import getpass
        while True:
            self.go.acquire()
            try:
                if self.secret and sys.stdin.isatty():
                    line = getpass.getpass(self.prompt)
                else:
                    if self.prompt:
                        print(self.prompt, end="", flush=True)
                    line = sys.stdin.readline()
                    if line == "":  # stdin closed
                        self.events.put(">quit")
                        return
            except (EOFError, KeyboardInterrupt):
                self.events.put(">quit")
                return
            self.events.put(">" + line.strip())

    def next_command(self) -> None:
        self.prompt, self.secret = "", False
        self.go.release()

    def ask(self, prompt: str, secret: bool = False) -> str:
        """Prompt for one line; hotkey presses that arrive meanwhile are kept for later."""
        self.prompt, self.secret = prompt, secret
        self.go.release()
        held = []
        while True:
            ev = self.events.get()
            if ev.startswith(">"):
                for h in held:
                    self.events.put(h)
                return ev[1:]
            held.append(ev)


def interactive(scanner: Scanner, cfg: dict) -> None:
    events: "queue.Queue[str]" = queue.Queue()
    bindings = {}
    for hid, mode in ((1, "buy"), (2, "sell")):
        mods, vk = parse_hotkey(cfg[f"hotkey_{mode}"])
        bindings[hid] = (mode, mods, vk)
    threading.Thread(target=hotkey_loop, args=(bindings, events), daemon=True).start()
    console = ConsoleReader(events)

    say(f"S.E.A.M. Price Scanner {VERSION}. Hotkeys: {cfg['hotkey_buy']} = BUY tab, {cfg['hotkey_sell']} = SELL tab. Type 'help' for commands.")
    say(f"Terminal: {scanner.terminal_label(cfg.get('terminal'))}. Signed in as: {cfg.get('email') or 'nobody (type login)'}")
    scanner.retry_queue()
    console.next_command()
    while True:
        ev = events.get()
        if ev.startswith("!hotkey"):
            say(f"Could not register hotkey {ev.split()[1]} (another program uses it). Change hotkey_buy/hotkey_sell in {CONFIG}.")
            continue
        if ev in ("buy", "sell"):
            threading.Thread(target=scanner.scan, args=(ev,), daemon=True).start()
            continue
        cmd = ev[1:]
        if not cmd:
            console.next_command()
            continue
        word, _, arg = cmd.partition(" ")
        word = word.lower()
        try:
            if word in ("quit", "exit"):
                return
            if word == "help":
                print(HELP)
            elif word == "login":
                email = (arg or console.ask("Email: ")).strip()
                password = console.ask("Password (hidden while you type): ", secret=True)
                if not email or not password:
                    say("Login cancelled.")
                    console.next_command()
                    continue
                scanner.site.login(email, password)
                say(f"Signed in as {cfg.get('email')}.")
                scanner.retry_queue()
            elif word == "logout":
                scanner.site.logout()
                say("Signed out.")
            elif word == "terminal":
                scanner.set_terminal(arg) if arg else say(f"Terminal: {scanner.terminal_label(cfg.get('terminal'))}")
            elif word == "terminals":
                q = norm(arg)
                for t in scanner.terminals:
                    if not q or q in norm(t["terminal_name"] + (t["location_name"] or "")):
                        print(f"   {t['terminal_slug']:<28} {t['terminal_name']} ({t['location_name']}, {t['system_name']})")
            elif word == "scan" and arg in ("buy", "sell"):
                scanner.scan(arg)
            elif word == "test":
                path, _, mode = arg.rpartition(" ")
                from PIL import Image
                scanner.scan(mode or "sell", Image.open(path.strip('"')).convert("RGB"), path)
            elif word == "retry":
                scanner.retry_queue()
            elif word == "status":
                say(f"Site {scanner.site.url}; signed in as {cfg.get('email') or 'nobody'}; terminal {scanner.terminal_label(cfg.get('terminal'))}; hotkeys {cfg['hotkey_buy']} / {cfg['hotkey_sell']}; queue {len(list(QUEUE_DIR.glob('*.json'))) if QUEUE_DIR.is_dir() else 0}.")
            else:
                say("Unknown command. Type 'help'.")
        except SiteError as e:
            say(f"Error: {e}")
        except Exception as e:  # a bad 'test' path and the like must not kill the console
            say(f"Error: {e}")
        console.next_command()


def main() -> None:
    ap = argparse.ArgumentParser(description="S.E.A.M. Price Scanner")
    ap.add_argument("--image", help="Read this screenshot instead of the screen, then exit")
    ap.add_argument("--mode", choices=["buy", "sell"], default="sell")
    ap.add_argument("--terminal", help="Terminal slug for this run")
    ap.add_argument("--dry-run", action="store_true", help="Read and match, but do not send anything")
    ap.add_argument("--site-url")
    ap.add_argument("--anon-key")
    ap.add_argument("--email", help="Sign in non-interactively (with SEAM_PASSWORD in the environment)")
    args = ap.parse_args()

    cfg = load_config()
    if args.site_url:
        cfg["site_url"] = args.site_url
    if args.anon_key:
        cfg["anon_key"] = args.anon_key
    if not cfg.get("site_url") or not cfg.get("anon_key"):
        cfg["site_url"] = input("S.E.A.M. site API URL (e.g. https://xxxx.supabase.co): ").strip()
        cfg["anon_key"] = input("Publishable key: ").strip()
    save_config(cfg)

    scanner = Scanner(cfg, dry_run=args.dry_run)
    if args.email and os.environ.get("SEAM_PASSWORD"):
        scanner.site.login(args.email, os.environ["SEAM_PASSWORD"])
    scanner.refresh_reference()
    if args.terminal:
        cfg["terminal"] = args.terminal

    if args.image:
        from PIL import Image
        scanner.scan(args.mode, Image.open(args.image).convert("RGB"), args.image)
        return
    interactive(scanner, cfg)


if __name__ == "__main__":
    main()

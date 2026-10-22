#!/usr/bin/env python3
"""
S.E.A.M. Price Scanner: the window.

The same engine as seam_scanner.py (capture, Windows text recognition, matching,
upload) behind a window in the S.E.A.M. site colours. Global hotkeys keep
working while the game has focus.

Run:  run-scanner.bat        (or: pythonw seam_scanner_gui.py)
"""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

import seam_scanner as core

# --------------------------------------------------------------------------
# look: the site's palette (tailwind.config.js) and type
# --------------------------------------------------------------------------

BG = "#141414"          # window
PANEL = "#1e1e1e"       # cards (bg-panel)
RAISED = "#262626"      # table header, inputs
HOVER = "#3a3a3a"       # bg-hover
BORDER = "#404040"      # border-color
ACCENT = "#00A8E8"      # seam-blue
ACCENT_DIM = "#0b3a4d"
TEXT = "#f3f4f6"
MUTED = "#8b94a3"       # gray-500 (AA on black)
GREEN = "#22c55e"
AMBER = "#f59e0b"
RED = "#ef4444"

DEFAULT_WEB = "https://www.seamindustries.com"


def asset(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / name


def load_private_font(path: Path) -> bool:
    """Make the site's Geo typeface available to this process only."""
    try:
        return ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0) > 0  # FR_PRIVATE
    except Exception:
        return False


def dark_title_bar(root: tk.Tk) -> None:
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))  # immersive dark mode
        colour = ctypes.c_int(0x001E1E1E)  # COLORREF 0x00BBGGRR -> #1e1e1e
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colour), ctypes.sizeof(colour))  # caption (Win 11)
    except Exception:
        pass


class FlatButton(tk.Label):
    """A flat, hover-aware button in the site style ('primary' filled, 'ghost' outlined)."""

    def __init__(self, master, text: str, command=None, kind: str = "ghost", caption: str | None = None, **kw):
        self.kind = kind
        self.command = command
        self.enabled = True
        self.colours = {
            "primary": (ACCENT, "#33bdf0", "#ffffff"),
            "ghost": (PANEL, HOVER, TEXT),
            "danger": (PANEL, "#3a1f1f", "#fca5a5"),
        }[kind]
        bg, _, fg = self.colours
        label = text if not caption else f"{text}\n{caption}"
        pady = kw.pop("pady", 8)
        super().__init__(master, text=label, bg=bg, fg=fg, cursor="hand2", padx=14, pady=pady,
                         font=kw.pop("font", ("Segoe UI Semibold", 10)), justify="center",
                         highlightthickness=1, highlightbackground=ACCENT if kind != "danger" else "#7f1d1d", **kw)
        self.bind("<Enter>", lambda e: self.enabled and self.config(bg=self.colours[1]))
        self.bind("<Leave>", lambda e: self.config(bg=self.colours[0] if self.enabled else PANEL))
        self.bind("<Button-1>", self._click)

    def _click(self, _e=None):
        if self.enabled and self.command:
            self.command()

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        bg, _, fg = self.colours
        self.config(bg=bg if on else PANEL, fg=fg if on else "#5b6270", cursor="hand2" if on else "arrow",
                    highlightbackground=(ACCENT if self.kind != "danger" else "#7f1d1d") if on else BORDER)


def card(master, title: str | None = None, **pack) -> tk.Frame:
    outer = tk.Frame(master, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
    outer.pack(**({"fill": "x", "pady": (0, 12)} | pack))
    if title:
        tk.Label(outer, text=title.upper(), bg=PANEL, fg=ACCENT, font=("Segoe UI Semibold", 9), anchor="w").pack(fill="x", padx=14, pady=(12, 4))
    body = tk.Frame(outer, bg=PANEL)
    body.pack(fill="both", expand=True, padx=14, pady=(0, 14))
    return body


def link(master, text: str, url_or_cmd, bg=PANEL) -> tk.Label:
    lab = tk.Label(master, text=text, bg=bg, fg=ACCENT, cursor="hand2", font=("Segoe UI", 9, "underline"))
    lab.bind("<Button-1>", lambda e: url_or_cmd() if callable(url_or_cmd) else webbrowser.open(url_or_cmd))
    return lab


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------

class App:
    def __init__(self) -> None:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SEAM.PriceScanner")
        except Exception:
            pass
        self.has_geo = load_private_font(asset("Geo.ttf"))
        self.cfg = core.load_config()
        self.web = (self.cfg.get("web_url") or DEFAULT_WEB).rstrip("/")
        self.scanner = core.Scanner(self.cfg)
        self.ui: "queue.Queue[tuple]" = queue.Queue()
        self.hotkeys: "queue.Queue[str]" = queue.Queue()
        self.terminal_by_label: dict[str, str] = {}
        self.scanning = False
        self.last_rows: dict[str, str] = {}
        self.last_scan: dict | None = None  # {"id", "terminal"} of the last scan that reached S.E.A.M.

        self.root = tk.Tk()
        self.root.title("S.E.A.M. Price Scanner")
        self.root.configure(bg=BG)
        self.root.geometry("1080x800")
        self.root.minsize(960, 760)
        try:
            self.root.iconbitmap(default=str(asset("seam.ico")))
        except Exception:
            pass
        self._style()
        self._build()
        self.root.update_idletasks()
        dark_title_bar(self.root)

        core.SINKS.append(lambda line: self.ui.put(("log", line)))
        self.scanner.listener = lambda kind, data: self.ui.put(("event", kind, data))
        self._start_hotkeys()
        threading.Thread(target=self._load_reference, daemon=True).start()
        self.root.after(80, self._pump)

    # ---- styling ----
    def _style(self) -> None:
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure(".", background=PANEL, foreground=TEXT, bordercolor=BORDER, lightcolor=PANEL, darkcolor=PANEL,
                     troughcolor=BG, focuscolor=ACCENT, font=("Segoe UI", 10))
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=30,
                     bordercolor=BORDER, borderwidth=0, font=("Segoe UI", 10))
        st.map("Treeview", background=[("selected", ACCENT_DIM)], foreground=[("selected", TEXT)])
        st.configure("Treeview.Heading", background=RAISED, foreground=MUTED, relief="flat", borderwidth=0,
                     font=("Segoe UI Semibold", 9), padding=(8, 6))
        st.map("Treeview.Heading", background=[("active", HOVER)])
        st.configure("TCombobox", fieldbackground=RAISED, background=RAISED, foreground=TEXT, arrowcolor=ACCENT,
                     bordercolor=BORDER, lightcolor=RAISED, darkcolor=RAISED, padding=6, insertcolor=TEXT)
        st.map("TCombobox", fieldbackground=[("readonly", RAISED)], foreground=[("readonly", TEXT)],
               bordercolor=[("focus", ACCENT)])
        st.configure("Vertical.TScrollbar", background=RAISED, troughcolor=PANEL, arrowcolor=MUTED, bordercolor=PANEL,
                     lightcolor=RAISED, darkcolor=RAISED)
        self.root.option_add("*TCombobox*Listbox.background", RAISED)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DIM)
        self.root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))

    # ---- layout ----
    def _build(self) -> None:
        r = self.root
        title_font = ("Geo", 22) if self.has_geo and "Geo" in tkfont.families() else ("Segoe UI Semibold", 18)

        # header
        head = tk.Frame(r, bg=PANEL, highlightthickness=0)
        head.pack(fill="x")
        tk.Frame(r, bg=ACCENT, height=1).pack(fill="x")
        try:
            self.logo = tk.PhotoImage(file=str(asset("logo-40.png")))
            tk.Label(head, image=self.logo, bg=PANEL).pack(side="left", padx=(16, 10), pady=10)
        except Exception:
            pass
        titles = tk.Frame(head, bg=PANEL)
        titles.pack(side="left", pady=8)
        tk.Label(titles, text="S.E.A.M. PRICE SCANNER", bg=PANEL, fg=TEXT, font=title_font).pack(anchor="w")
        tk.Label(titles, text=f"Commodity terminal reader  ·  v{core.VERSION}", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")

        acct = tk.Frame(head, bg=PANEL)
        acct.pack(side="right", padx=16)
        self.acct_dot = tk.Label(acct, text="●", bg=PANEL, fg=MUTED, font=("Segoe UI", 11))
        self.acct_dot.pack(side="left")
        self.acct_text = tk.Label(acct, text="Not signed in", bg=PANEL, fg=MUTED, font=("Segoe UI", 10))
        self.acct_text.pack(side="left", padx=(4, 12))
        self.acct_btn = FlatButton(acct, "Sign in", self._sign_in_or_out, kind="primary", font=("Segoe UI Semibold", 9))
        self.acct_btn.pack(side="left")

        body = tk.Frame(r, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=16)
        left = tk.Frame(body, bg=BG, width=310)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))

        # terminal picker
        t = card(left, "Terminal")
        self.term_var = tk.StringVar()
        self.term_box = ttk.Combobox(t, textvariable=self.term_var, values=[], height=16)
        self.term_box.pack(fill="x")
        self.term_box.bind("<<ComboboxSelected>>", self._terminal_picked)
        self.term_box.bind("<KeyRelease>", self._terminal_filter)
        tk.Label(t, text="Type to filter. Picked up from the screen\nautomatically when the location name shows.",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9), justify="left", anchor="w").pack(fill="x", pady=(6, 0))

        # scan buttons
        s = card(left, "Scan")
        hb, hs = self.cfg.get("hotkey_buy", "ctrl+shift+f9"), self.cfg.get("hotkey_sell", "ctrl+shift+f10")
        self.buy_btn = FlatButton(s, "SCAN BUY TAB", lambda: self._scan("buy"), kind="primary", caption=pretty_key(hb),
                                  font=("Segoe UI Semibold", 11))
        self.buy_btn.pack(fill="x")
        self.sell_btn = FlatButton(s, "SCAN SELL TAB", lambda: self._scan("sell"), kind="ghost", caption=pretty_key(hs),
                                   font=("Segoe UI Semibold", 11))
        self.sell_btn.pack(fill="x", pady=(8, 0))
        tk.Label(s, text="Hotkeys work while the game has focus.\nThe tab lit up on screen always wins.",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9), justify="left", anchor="w").pack(fill="x", pady=(8, 0))

        # queue
        q = card(left, "Waiting to send")
        row = tk.Frame(q, bg=PANEL)
        row.pack(fill="x")
        self.queue_text = tk.Label(row, text="Nothing waiting", bg=PANEL, fg=MUTED, font=("Segoe UI", 10), anchor="w")
        self.queue_text.pack(side="left", fill="x", expand=True)
        self.retry_btn = FlatButton(row, "Send now", self._retry, font=("Segoe UI Semibold", 9))
        self.retry_btn.pack(side="right")

        # trust
        k = card(left, "Safe by design")
        for line in ("Screenshots only, like Print Screen.",
                     "Never reads game memory, files or traffic.",
                     "Text read by Windows' own recognition.",
                     "Prices go only to your S.E.A.M. account."):
            row = tk.Frame(k, bg=PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(row, text="✓", bg=PANEL, fg=GREEN, font=("Segoe UI Semibold", 10)).pack(side="left", anchor="n")
            tk.Label(row, text=line, bg=PANEL, fg=TEXT, font=("Segoe UI", 9), wraplength=250, justify="left",
                     anchor="w").pack(side="left", fill="x", padx=(6, 0))
        links = tk.Frame(k, bg=PANEL)
        links.pack(fill="x", pady=(8, 0))
        link(links, "How it works", self._about).pack(side="left")
        link(links, "Price board", f"{self.web}/prices").pack(side="left", padx=(14, 0))

        # status banner
        self.banner = tk.Frame(right, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        self.banner.pack(fill="x", pady=(0, 12))
        self.banner_bar = tk.Frame(self.banner, bg=ACCENT, width=4)
        self.banner_bar.pack(side="left", fill="y")
        self.banner_text = tk.Label(self.banner, text="Connecting to S.E.A.M...", bg=PANEL, fg=TEXT,
                                    font=("Segoe UI Semibold", 11), anchor="w", justify="left", padx=14, pady=12)
        self.banner_text.pack(side="left", fill="x", expand=True)

        # results
        res = tk.Frame(right, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        res.pack(fill="both", expand=True)
        top = tk.Frame(res, bg=PANEL)
        top.pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(top, text="LAST SCAN", bg=PANEL, fg=ACCENT, font=("Segoe UI Semibold", 9)).pack(side="left")
        self.recent_btn = FlatButton(top, "Recent scans", self._recent, font=("Segoe UI Semibold", 9), pady=3)
        self.recent_btn.pack(side="right")
        self.move_btn = FlatButton(top, "Move to terminal...", self._move_last, font=("Segoe UI Semibold", 9), pady=3)
        self.move_btn.pack(side="right", padx=(0, 6))
        self.undo_btn = FlatButton(top, "Undo", self._undo_last, kind="danger", font=("Segoe UI Semibold", 9), pady=3)
        self.undo_btn.pack(side="right", padx=(0, 6))
        self.undo_btn.set_enabled(False)
        self.move_btn.set_enabled(False)
        self.scan_meta = tk.Label(res, text="No scan yet", bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.scan_meta.pack(fill="x", padx=14, pady=(0, 6))
        cols = ("commodity", "price", "stock", "inventory", "result")
        wrap = tk.Frame(res, bg=PANEL)
        wrap.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        self.table = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for c, label, width, anchor in (("commodity", "COMMODITY", 220, "w"), ("price", "PRICE  aUEC / SCU", 140, "e"),
                                        ("stock", "STOCK  SCU", 110, "e"), ("inventory", "INVENTORY", 110, "w"),
                                        ("result", "RESULT", 190, "w")):
            self.table.heading(c, text=label, anchor=anchor)
            self.table.column(c, width=width, anchor=anchor, stretch=c in ("commodity", "result"))
        self.table.tag_configure("accepted", foreground=TEXT)
        self.table.tag_configure("pending", foreground=AMBER)
        self.table.tag_configure("refused", foreground=RED)
        self.table.tag_configure("skipped", foreground=MUTED)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=sb.set)
        self.table.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # activity log
        log = tk.Frame(right, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        log.pack(fill="x", pady=(12, 0))
        tk.Label(log, text="ACTIVITY", bg=PANEL, fg=ACCENT, font=("Segoe UI Semibold", 9), anchor="w").pack(fill="x", padx=14, pady=(10, 2))
        self.log = tk.Text(log, height=6, bg=PANEL, fg=MUTED, font=("Consolas", 9), relief="flat", wrap="word",
                           highlightthickness=0, padx=14, pady=4, insertbackground=TEXT)
        self.log.pack(fill="x", pady=(0, 8))
        self.log.configure(state="disabled")

        self._refresh_account()
        self._refresh_queue()

    # ---- startup ----
    def _load_reference(self) -> None:
        try:
            self.scanner.refresh_reference()
            self.ui.put(("reference_ok",))
            if self.cfg.get("refresh_token_dpapi"):
                self.scanner.retry_queue()
                self.ui.put(("queue",))
        except Exception as e:
            self.ui.put(("reference_fail", str(e)))

    def _start_hotkeys(self) -> None:
        bindings = {}
        for hid, mode in ((1, "buy"), (2, "sell")):
            try:
                mods, vk = core.parse_hotkey(self.cfg[f"hotkey_{mode}"])
                bindings[hid] = (mode, mods, vk)
            except Exception:
                pass
        threading.Thread(target=core.hotkey_loop, args=(bindings, self.hotkeys), daemon=True).start()

    # ---- event pump (worker threads -> UI thread) ----
    def _pump(self) -> None:
        try:
            while True:
                ev = self.hotkeys.get_nowait()
                if ev in ("buy", "sell"):
                    self._scan(ev)
                elif ev.startswith("!hotkey"):
                    which = "BUY" if ev.split()[1] == "1" else "SELL"
                    self._banner(f"The {which} hotkey is used by another program. Use the button, or change it in settings.", AMBER)
        except queue.Empty:
            pass
        try:
            while True:
                item = self.ui.get_nowait()
                getattr(self, f"_on_{item[0]}")(*item[1:])
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def _on_log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 400:
            self.log.delete("1.0", "100.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_reference_ok(self) -> None:
        labels = []
        self.terminal_by_label.clear()
        for t in sorted(self.scanner.terminals, key=lambda t: ((t.get("system_name") or ""), t["terminal_name"])):
            label = terminal_text(t)
            self.terminal_by_label[label] = t["terminal_slug"]
            labels.append(label)
        self.all_labels = labels
        self.term_box.configure(values=labels)
        self._show_current_terminal()
        n_c, n_t = len(self.scanner.commodities), len(self.scanner.terminals)
        if self.cfg.get("refresh_token_dpapi"):
            self._banner(f"Ready. {n_c} commodities and {n_t} terminals loaded. Open a commodity terminal and scan.", ACCENT)
        else:
            self._banner("Sign in to send prices. You can still test a scan without signing in.", AMBER)

    def _on_reference_fail(self, err: str) -> None:
        self._banner(f"Cannot reach S.E.A.M. ({err}). Scans are saved and sent later.", RED)

    def _on_queue(self) -> None:
        self._refresh_queue()

    def _on_event(self, kind: str, data: dict) -> None:
        if kind == "read":
            self._show_read(data)
        elif kind == "terminal":
            self._show_current_terminal()
        elif kind == "done":
            self._show_done(data)

    def _on_call(self, fn) -> None:
        fn()

    def _bg(self, work, done, failed=None) -> None:
        """Run work() off the UI thread; done(result) or failed(message) back on it."""
        def run():
            try:
                result = work()
                self.ui.put(("call", lambda: done(result)))
            except Exception as e:
                msg = str(e)
                self.ui.put(("call", lambda: (failed or (lambda m: self._banner(m, RED)))(msg)))
        threading.Thread(target=run, daemon=True).start()

    def _on_scan_finished(self) -> None:
        self.scanning = False
        self.buy_btn.set_enabled(True)
        self.sell_btn.set_enabled(True)
        self._refresh_queue()

    def _on_signed_in(self, email: str) -> None:
        self._refresh_account()
        self._banner(f"Signed in as {email}. Ready to scan.", GREEN)
        threading.Thread(target=lambda: (self.scanner.retry_queue(), self.ui.put(("queue",))), daemon=True).start()

    # ---- actions ----
    def _scan(self, mode: str) -> None:
        if self.scanning:
            return
        self.scanning = True
        self.buy_btn.set_enabled(False)
        self.sell_btn.set_enabled(False)
        self._banner(f"Reading the {mode.upper()} tab...", ACCENT)

        def work():
            try:
                self.scanner.scan(mode)
            finally:
                self.ui.put(("scan_finished",))
        threading.Thread(target=work, daemon=True).start()

    def _retry(self) -> None:
        if not self.cfg.get("refresh_token_dpapi"):
            self._sign_in()
            return
        threading.Thread(target=lambda: (self.scanner.retry_queue(), self.ui.put(("queue",))), daemon=True).start()

    def _terminal_picked(self, _e=None) -> None:
        slug = self.terminal_by_label.get(self.term_var.get())
        if slug:
            self.cfg["terminal"] = slug
            core.save_config(self.cfg)
            self._banner(f"Terminal set: {self.term_var.get()}", ACCENT)
            self.root.focus_set()

    def _terminal_filter(self, e) -> None:
        if e.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        words = self.term_var.get().lower().split()
        vals = [l for l in getattr(self, "all_labels", []) if all(w in l.lower() for w in words)]
        self.term_box.configure(values=vals or getattr(self, "all_labels", []))

    def _show_current_terminal(self) -> None:
        slug = self.cfg.get("terminal")
        label = next((l for l, s in self.terminal_by_label.items() if s == slug), "")
        self.term_var.set(label)

    def _sign_in_or_out(self) -> None:
        if self.cfg.get("refresh_token_dpapi"):
            self.scanner.site.logout()
            self._refresh_account()
            self._banner("Signed out. Scans will be saved until you sign in again.", AMBER)
        else:
            self._sign_in()

    # ---- display ----
    def _banner(self, text: str, colour: str) -> None:
        self.banner_bar.configure(bg=colour)
        self.banner_text.configure(text=text)

    def _refresh_account(self) -> None:
        email = self.cfg.get("email") if self.cfg.get("refresh_token_dpapi") else None
        if email:
            self.acct_dot.configure(fg=GREEN)
            self.acct_text.configure(text=f"Signed in as {email}", fg=TEXT)
            self.acct_btn.configure(text="Sign out")
        else:
            self.acct_dot.configure(fg=MUTED)
            self.acct_text.configure(text="Not signed in", fg=MUTED)
            self.acct_btn.configure(text="Sign in")

    def _refresh_queue(self) -> None:
        n = len(list(core.QUEUE_DIR.glob("*.json"))) if core.QUEUE_DIR.is_dir() else 0
        self.queue_text.configure(text=f"{n} scan{'s' if n != 1 else ''} waiting" if n else "Nothing waiting",
                                  fg=AMBER if n else MUTED)
        self.retry_btn.set_enabled(n > 0)

    def _show_read(self, data: dict) -> None:
        self.table.delete(*self.table.get_children())
        self.last_rows.clear()
        for m in data["matches"]:
            stock = "" if m["stock"] is None else f"{m['stock']:,.0f}"
            inv = (m["inventory"] or "").replace("_", " ").title()
            iid = self.table.insert("", "end", values=(m["name"], f"{m['price']:,}", stock, inv, "Read"))
            self.last_rows[m["slug"]] = iid
        for s in data["skipped"]:
            name, _, why = s.partition(":")
            self.table.insert("", "end", values=(name, "", "", "", "–  Skipped: " + why.strip()[:60]), tags=("skipped",))
        tt = next((t for t in self.scanner.terminals if t["terminal_slug"] == data.get("terminal")), None)
        term = terminal_text(tt) if tt else "no terminal picked"
        self.scan_meta.configure(text=f"{data['mode'].upper()} tab  ·  {term}  ·  {data['seconds']}s")

    def _show_done(self, data: dict) -> None:
        status = data.get("status")
        if data.get("retried"):
            self._banner("A saved scan was sent: " + data.get("message", ""), GREEN)
            return
        if status in ("sent", "partial") and data.get("scan_id"):
            self.last_scan = {"id": data["scan_id"], "terminal": data.get("terminal")}
            self.undo_btn.set_enabled(True)
            self.move_btn.set_enabled(True)
        else:
            self.last_scan = None
            self.undo_btn.set_enabled(False)
            self.move_btn.set_enabled(False)
        colour = {"sent": GREEN, "partial": AMBER, "queued": AMBER, "dry": ACCENT}.get(status, RED)
        self._banner(data.get("message", ""), colour)
        for r in data.get("results", []):
            iid = self.last_rows.get(r.get("commodity"))
            if not iid:
                continue
            vals = list(self.table.item(iid, "values"))
            st = r.get("status")
            if st == "failed":
                vals[4], tag = "✕  Refused: " + str(r.get("error", ""))[:40], "refused"
            elif st == "pending" or r.get("flags"):
                vals[4], tag = "◷  Awaiting review", "pending"
            else:
                vals[4], tag = "✓  Accepted", "accepted"
            self.table.item(iid, values=vals, tags=(tag,))
        if status == "queued":
            for iid in self.last_rows.values():
                vals = list(self.table.item(iid, "values"))
                vals[4] = "Saved, sending later"
                self.table.item(iid, values=vals, tags=("pending",))

    # ---- dialogs ----
    def _dialog(self, title: str, width: int = 420) -> tk.Toplevel:
        d = tk.Toplevel(self.root, bg=PANEL)
        d.title(title)
        d.transient(self.root)
        d.resizable(False, False)
        d.configure(highlightthickness=1, highlightbackground=BORDER)
        try:
            d.iconbitmap(str(asset("seam.ico")))
        except Exception:
            pass
        d.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_rooty() + 120
        d.geometry(f"+{x}+{y}")
        dark_title_bar(d)
        return d

    def _sign_in(self) -> None:
        d = self._dialog("Sign in to S.E.A.M.")
        f = tk.Frame(d, bg=PANEL)
        f.pack(padx=28, pady=24)
        tk.Label(f, text="Sign in", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 15)).pack(anchor="w")
        tk.Label(f, text="Use your S.E.A.M. account. The scanner keeps a sign-in\ntoken on this PC, never your password.",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(2, 14))

        def field(label, show=None):
            tk.Label(f, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
            e = tk.Entry(f, bg=RAISED, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 11), width=34,
                         highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT, show=show or "")
            e.pack(fill="x", ipady=6, pady=(2, 10))
            return e
        email = field("Email")
        pw = field("Password", show="•")
        email.insert(0, self.cfg.get("email") or "")
        err = tk.Label(f, text="", bg=PANEL, fg=RED, font=("Segoe UI", 9), wraplength=330, justify="left")
        err.pack(anchor="w")
        btns = tk.Frame(f, bg=PANEL)
        btns.pack(fill="x", pady=(8, 0))

        def submit(_e=None):
            if not email.get().strip() or not pw.get():
                err.configure(text="Enter your email and password.")
                return
            go.set_enabled(False)
            err.configure(text="Signing in...", fg=MUTED)

            def work():
                try:
                    self.scanner.site.login(email.get().strip(), pw.get())
                    self.ui.put(("signed_in", self.cfg.get("email") or email.get().strip()))
                    self.root.after(0, d.destroy)
                except Exception as ex:
                    msg = str(ex)
                    if "invalid login" in msg.lower():
                        msg = "That email and password do not match a S.E.A.M. account."
                    self.root.after(0, lambda: (err.configure(text=msg, fg=RED), go.set_enabled(True)))
            threading.Thread(target=work, daemon=True).start()

        go = FlatButton(btns, "Sign in", submit, kind="primary")
        go.pack(side="left")
        FlatButton(btns, "Cancel", d.destroy).pack(side="left", padx=(8, 0))
        more = tk.Frame(f, bg=PANEL)
        more.pack(fill="x", pady=(16, 0))
        link(more, "Forgot password?", f"{self.web}/login").pack(side="left")
        link(more, "Signed up with Discord? Set a password", f"{self.web}/login").pack(side="left", padx=(14, 0))
        d.bind("<Return>", submit)
        d.bind("<Escape>", lambda e: d.destroy())
        (pw if email.get() else email).focus_set()
        d.grab_set()

    def _about(self) -> None:
        d = self._dialog("How the scanner works", 520)
        f = tk.Frame(d, bg=PANEL)
        f.pack(padx=28, pady=24)
        tk.Label(f, text="How it works", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 15)).pack(anchor="w", pady=(0, 10))
        steps = [
            ("Capture", "When you press a hotkey it takes one screenshot of the Star Citizen window (only the "
                        "game, not the rest of your screen). It only listens for its two hotkeys, never other keys."),
            ("Read", "Windows' built-in text recognition reads the terminal on your PC. Nothing leaves your PC yet."),
            ("Match", "Names are matched to S.E.A.M.'s commodity list. A price that is not read cleanly is "
                      "skipped, never guessed."),
            ("Send", "The prices and the screenshot (as evidence for moderators; only you and S.E.A.M. admins can "
                     "see it) go to your S.E.A.M. account. If the game window is not found, no screenshot is sent."),
        ]
        for i, (head, text) in enumerate(steps, 1):
            row = tk.Frame(f, bg=PANEL)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=str(i), bg=ACCENT, fg="#ffffff", font=("Segoe UI Semibold", 10), width=2).pack(side="left", anchor="n")
            col = tk.Frame(row, bg=PANEL)
            col.pack(side="left", fill="x", padx=(10, 0))
            tk.Label(col, text=head, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 10)).pack(anchor="w")
            tk.Label(col, text=text, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=420, justify="left").pack(anchor="w")
        tk.Label(f, text="It never touches the game process, its memory, files or network traffic, so it is no\n"
                         "different to Easy Anti-Cheat than taking a screenshot.",
                 bg=PANEL, fg=TEXT, font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(12, 4))
        tk.Label(f, text=f"Settings and the last capture: {core.APP_DIR}", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w")
        tk.Label(f, text="Your sign-in is encrypted with your Windows account; your password is never saved.",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        link(f, "Read the source code", core.SOURCE_URL).pack(anchor="w", pady=(6, 0))
        FlatButton(f, "Close", d.destroy).pack(anchor="e", pady=(14, 0))
        d.bind("<Escape>", lambda e: d.destroy())
        d.grab_set()

    # ---- undo / move ----
    def _confirm(self, title: str, text: str, ok_label: str, on_ok, danger: bool = False) -> None:
        d = self._dialog(title)
        f = tk.Frame(d, bg=PANEL)
        f.pack(padx=28, pady=24)
        tk.Label(f, text=title, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 14)).pack(anchor="w")
        tk.Label(f, text=text, bg=PANEL, fg=MUTED, font=("Segoe UI", 10), wraplength=360, justify="left").pack(anchor="w", pady=(6, 16))
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x")
        FlatButton(row, ok_label, lambda: (d.destroy(), on_ok()), kind="danger" if danger else "primary").pack(side="left")
        FlatButton(row, "Cancel", d.destroy).pack(side="left", padx=(8, 0))
        d.bind("<Escape>", lambda e: d.destroy())
        d.grab_set()

    def _pick_terminal(self, title: str, current: str | None, on_pick) -> None:
        d = self._dialog(title)
        f = tk.Frame(d, bg=PANEL)
        f.pack(padx=28, pady=24)
        tk.Label(f, text=title, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 14)).pack(anchor="w")
        tk.Label(f, text="The same prices, stock and screenshot are filed at the terminal you pick,\nand the original is withdrawn. Type to filter.",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(4, 12))
        labels = [l for l, sl in self.terminal_by_label.items() if sl != current]
        var = tk.StringVar()
        box = ttk.Combobox(f, textvariable=var, values=labels, height=14, width=48)
        box.pack(fill="x")

        def filt(e):
            if e.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
                return
            words = var.get().lower().split()
            box.configure(values=[l for l in labels if all(w in l.lower() for w in words)] or labels)
        box.bind("<KeyRelease>", filt)
        err = tk.Label(f, text="", bg=PANEL, fg=RED, font=("Segoe UI", 9))
        err.pack(anchor="w", pady=(6, 0))

        def go(_e=None):
            slug = self.terminal_by_label.get(var.get())
            if not slug:
                err.configure(text="Pick a terminal from the list.")
                return
            d.destroy()
            on_pick(slug, var.get())
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x", pady=(10, 0))
        FlatButton(row, "Move here", go, kind="primary").pack(side="left")
        FlatButton(row, "Cancel", d.destroy).pack(side="left", padx=(8, 0))
        d.bind("<Return>", go)
        d.bind("<Escape>", lambda e: d.destroy())
        box.focus_set()
        d.grab_set()

    def _withdraw(self, scan_id: str, after=None) -> None:
        self._banner("Withdrawing the scan...", ACCENT)

        def done(r):
            n = r.get("withdrawn", 0)
            self._banner(f"Scan withdrawn: {n} price{'s' if n != 1 else ''} no longer count. Nothing was held against you.", AMBER)
            if self.last_scan and self.last_scan["id"] == scan_id:
                for iid in self.last_rows.values():
                    vals = list(self.table.item(iid, "values"))
                    vals[4] = "↶  Withdrawn"
                    self.table.item(iid, values=vals, tags=("skipped",))
                self.last_scan = None
                self.undo_btn.set_enabled(False)
                self.move_btn.set_enabled(False)
            if after:
                after()
        self._bg(lambda: self.scanner.site.withdraw_scan(scan_id, "withdrawn in the scanner"), done)

    def _move(self, scan_id: str, slug: str, label: str, after=None) -> None:
        self._banner(f"Moving the scan to {label}...", ACCENT)

        def done(r):
            ok, pend, fail = r.get("accepted", 0), r.get("pending", 0), r.get("failed", 0)
            self._banner(f"Moved to {label}: {ok} accepted, {pend} awaiting review, {fail} refused.", GREEN if fail == 0 else AMBER)
            if self.last_scan and self.last_scan["id"] == scan_id:
                self.last_scan = {"id": r.get("scan_id"), "terminal": slug}
                meta = self.scan_meta.cget("text").split("  ·  ")
                if len(meta) >= 2:
                    meta[1] = label
                    self.scan_meta.configure(text="  ·  ".join(meta))
            if after:
                after()
        self._bg(lambda: self.scanner.site.move_scan(scan_id, slug), done)

    def _undo_last(self) -> None:
        if not self.last_scan:
            return
        n = len(self.last_rows)
        self._confirm("Undo this scan?", f"The {n} price{'s' if n != 1 else ''} from your last scan will stop counting on the price board "
                      "and in the history graph straight away. You can undo a scan for 24 hours.",
                      "Undo scan", lambda: self._withdraw(self.last_scan["id"]), danger=True)

    def _move_last(self) -> None:
        if not self.last_scan:
            return
        sid = self.last_scan["id"]
        self._pick_terminal("Move to another terminal", self.last_scan.get("terminal"), lambda slug, label: self._move(sid, slug, label))

    def _recent(self) -> None:
        if not self.cfg.get("refresh_token_dpapi"):
            self._sign_in()
            return
        d = self._dialog("Your recent scans", 760)
        f = tk.Frame(d, bg=PANEL)
        f.pack(padx=24, pady=20, fill="both", expand=True)
        tk.Label(f, text="Your recent scans", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 14)).pack(anchor="w")
        tk.Label(f, text="Pick a scan to undo it or move it to the right terminal. Scans can be changed for 24 hours.",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 10))
        cols = ("when", "tab", "terminal", "prices", "status")
        tv = ttk.Treeview(f, columns=cols, show="headings", height=10, selectmode="browse")
        for c, label, w, anchor in (("when", "WHEN", 120, "w"), ("tab", "TAB", 60, "w"), ("terminal", "TERMINAL", 300, "w"),
                                    ("prices", "PRICES", 70, "e"), ("status", "STATUS", 160, "w")):
            tv.heading(c, text=label, anchor=anchor)
            tv.column(c, width=w, anchor=anchor, stretch=c == "terminal")
        tv.tag_configure("withdrawn", foreground=MUTED)
        tv.pack(fill="both", expand=True)
        rows: dict[str, dict] = {}
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x", pady=(12, 0))
        undo = FlatButton(row, "Undo", None, kind="danger")
        move = FlatButton(row, "Move to terminal...", None)
        undo.pack(side="left")
        move.pack(side="left", padx=(8, 0))
        FlatButton(row, "Close", d.destroy).pack(side="right")
        status = tk.Label(f, text="Loading...", bg=PANEL, fg=MUTED, font=("Segoe UI", 9))
        status.pack(anchor="w", pady=(8, 0))

        def selected():
            sel = tv.selection()
            return rows.get(sel[0]) if sel else None

        def refresh_buttons(_e=None):
            r = selected()
            can = bool(r and r.get("can_withdraw"))
            undo.set_enabled(can)
            move.set_enabled(can)

        def load():
            def done(data):
                tv.delete(*tv.get_children())
                rows.clear()
                for r in data:
                    when = local_time(r["created_at"])
                    st = "Withdrawn" if r.get("withdrawn_at") else f"{r['items_accepted']} accepted, {r['items_pending']} review"
                    iid = tv.insert("", "end", values=(when, r["mode"].upper(), r["terminal_name"], r["items_submitted"], st),
                                    tags=("withdrawn",) if r.get("withdrawn_at") else ())
                    rows[iid] = r
                status.configure(text=f"{len(data)} scan{'s' if len(data) != 1 else ''}." if data else "No scans yet.")
                refresh_buttons()
            self._bg(lambda: self.scanner.site.my_scans(25), done, lambda m: status.configure(text=m, fg=RED))

        def do_undo():
            r = selected()
            if r:
                self._withdraw(r["id"], after=load)

        def do_move():
            r = selected()
            if r:
                self._pick_terminal("Move to another terminal", r["terminal_slug"], lambda slug, label: self._move(r["id"], slug, label, after=load))
        undo.command = do_undo
        move.command = do_move
        tv.bind("<<TreeviewSelect>>", refresh_buttons)
        d.bind("<Escape>", lambda e: d.destroy())
        load()

    def run(self) -> None:
        self.root.mainloop()


def local_time(iso: str) -> str:
    """Database timestamps are UTC; show them in the player's own time."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%b %d  %H:%M")
    except ValueError:
        return iso[:16].replace("T", " ")


def terminal_text(t: dict) -> str:
    """'Admin Office, Port Tressler · Stanton'; no repeats when the name already says the system."""
    name, system = t["terminal_name"], t.get("system_name") or ""
    return name if not system or system.lower() in name.lower() else f"{name}  ·  {system}"


def pretty_key(spec: str) -> str:
    return "+".join(p.upper() if len(p) <= 3 else p.capitalize() for p in spec.split("+"))


def selftest(image_path: str) -> None:
    """--selftest <image>: read a screenshot with the full engine and write the result to
    selftest.json next to the settings (for support: proves recognition works on this PC)."""
    import json
    from PIL import Image
    cfg = core.load_config()
    sc = core.Scanner(cfg)
    out = {"version": core.VERSION}
    try:
        sc.refresh_reference()
        img = Image.open(image_path).convert("RGB")
        words = core.ocr(img)
        r = core.parse_screen(words, sc.commodities, sc.terminals, float(cfg.get("min_match", 0.84)), img)
        out |= {"ok": True, "words": len(words), "commodities": len(sc.commodities),
                "matches": [{"name": m.name, "price": m.price} for m in r.matches], "skipped": r.unmatched_rows}
    except Exception as e:
        out |= {"ok": False, "error": repr(e)}
    core.APP_DIR.mkdir(parents=True, exist_ok=True)
    (core.APP_DIR / "selftest.json").write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2])
    else:
        App().run()

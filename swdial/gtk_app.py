"""Shortwave Dial: GTK4 + libadwaita window that tunes a public KiwiSDR.

Run:  .venv/bin/python -m swdial.gtk_app
It starts kiwiclientd (audio -> PipeWire) with rigctl on, then sends F/M commands.
Fair use: one channel, closes the Kiwi connection when the window closes.
"""
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .bands import BANDS, MODES, clamp_to_band, format_mhz  # noqa: E402
from .rigctl import Rigctl  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
KIWID = ROOT / "vendor" / "kiwiclient" / "kiwiclientd.py"
STATE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "swdial.json"
RIG_PORT = 6400

RECEIVERS = [  # name, host, port, lat, lon
    ("Wessex V2 · South West UK", "wessex.zapto.org", 8074, 50.74, -2.64),
    ("Wessex V1 · South West UK", "wessex.zapto.org", 8073, 50.74, -2.64),
]

AMBER = (1.0, 0.72, 0.30)
TEAL = (0.30, 0.85, 0.80)

CSS = b"""
window { background: #1c1b19; }
.label-small { font-size: 15px; color: #b9b4aa; font-weight: 600; }
.readout { font-family: monospace; font-size: 58px; font-weight: 800; color: #ffb84d; }
.readout.pending { color: #8a6a3a; }
.lock { font-size: 16px; font-weight: 800; color: #6fdc8c; }
.lock.pending { color: #ffc83d; }
.lock.off { color: #ff7a6b; }
.mhz { font-size: 28px; font-weight: 700; color: #f2efe8; }
.mode-pill { font-size: 22px; font-weight: 800; color: #4fd8cc;
             border: 2px solid #4fd8cc; border-radius: 10px; padding: 2px 12px; }
.status { font-size: 14px; font-weight: 700; color: #6fdc8c; }
.status.off { color: #ff7a6b; }
.bigbtn { font-size: 18px; font-weight: 800; min-height: 42px; min-width: 0; padding: 0 6px; }
.tabbtn { font-size: 15px; font-weight: 700; min-height: 34px; padding: 0 10px; }
.tabbtn:checked { background: #4fd8cc; color: #1c1b19; }
.autobtn { font-size: 16px; font-weight: 800; min-height: 42px; padding: 0 10px; }
.autobtn:checked { background: #6aa8ff; color: #10141c; }
.lookup { font-size: 17px; font-weight: 700; color: #f2efe8; background: #262522; border-radius: 10px; padding: 8px 12px; }
entry.callbox { font-family: monospace; font-size: 20px; font-weight: 800; min-height: 44px; }
dropdown.rxdrop > button { min-height: 34px; font-size: 15px; }
.bigbtn.band:checked { background: #ffb84d; color: #1c1b19; }
.bigbtn.mode:checked { background: #4fd8cc; color: #1c1b19; }

.bigbtn.band.cond-good { border-bottom: 6px solid #3ddc84; }
.bigbtn.band.cond-fair { border-bottom: 6px solid #ffc83d; }
.bigbtn.band.cond-poor { border-bottom: 6px solid #ff5a4e; }
levelbar.smeter block { min-height: 14px; border-radius: 7px; }
levelbar.smeter block.low { background: #ff7a6b; }
levelbar.smeter block.high { background: #ffc83d; }
levelbar.smeter block.full { background: #3ddc84; }
levelbar.smeter block.empty { background: #33312d; }
.meter-lbl { font-family: monospace; font-size: 16px; font-weight: 800; color: #f2efe8; }
.compact .readout { font-size: 42px; }
.compact .mhz { font-size: 20px; }
.compact .bigbtn { font-size: 15px; min-height: 36px; }
.compact .autobtn { font-size: 14px; min-height: 36px; }
.compact .tabbtn { font-size: 13px; min-height: 30px; }
.compact .hint { font-size: 13px; }
.cw-text { font-family: monospace; font-size: 24px; font-weight: 800; color: #ffe08a; padding: 8px; }
.compact .cw-text { font-size: 18px; }
.cond-note { font-size: 15px; font-weight: 600; color: #d8d3c8; }
.spot-row { font-family: monospace; font-size: 15px; font-weight: 700; padding: 4px 8px; }
.spot-head { font-size: 15px; font-weight: 600; color: #b9b4aa; }
.hint { font-size: 16px; font-weight: 600; color: #f2efe8;
        background: #262522; border-radius: 22px; padding: 6px 16px; }
"""


class Dial(Gtk.DrawingArea):
    def __init__(self, app_win):
        super().__init__()
        self.win = app_win
        self.set_content_width(220)   # smallest; grows with the window
        self.set_content_height(220)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self.draw)

    def draw(self, _area, cr, w, h):
        self.hits = []  # (x, y, kind, item) for click-to-jump
        sc = min(w, h) / 380
        ox, oy = (w - 380 * sc) / 2, (h - 380 * sc) / 2
        cr.translate(ox, oy)
        cr.scale(sc, sc)
        w = h = 380
        self._draw(cr, w, h)
        self.hits = [(ox + x * sc, oy + y * sc, k, i) for x, y, k, i in self.hits]

    def _draw(self, cr, w, h):
        band = BANDS[self.win.band]
        cx, cy, r = w / 2, h / 2, min(w, h) / 2 - 16
        start, sweep = math.radians(135), math.radians(270)
        # face
        cr.set_source_rgb(0.15, 0.145, 0.135)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.fill()
        # track
        cr.set_line_width(10)
        cr.set_source_rgb(0.18, 0.30, 0.30)
        cr.arc(cx, cy, r - 12, start, start + sweep)
        cr.stroke()
        frac = (self.win.freq - band["start"]) / (band["end"] - band["start"])
        cr.set_source_rgb(*TEAL)
        cr.arc(cx, cy, r - 12, start, start + sweep * frac)
        cr.stroke()
        # ticks + labels
        span = band["end"] - band["start"]
        cr.select_font_face("monospace", 0, 1)
        cr.set_font_size(19)
        for i in range(41):
            a = start + sweep * i / 40
            major = i % 10 == 0
            r1, r2 = r - 30, r - (52 if major else 40)
            cr.set_source_rgb(0.85, 0.82, 0.76)
            cr.set_line_width(4 if major else 2)
            cr.move_to(cx + r1 * math.cos(a), cy + r1 * math.sin(a))
            cr.line_to(cx + r2 * math.cos(a), cy + r2 * math.sin(a))
            cr.stroke()
            if major:
                txt = f"{(band['start'] + span * i / 40) / 1e6:.2f}"
                ext = cr.text_extents(txt)
                rl = r - 82
                cr.move_to(cx + rl * math.cos(a) - ext.width / 2,
                           cy + rl * math.sin(a) + ext.height / 2)
                cr.show_text(txt)
        # voice (SSB) part of the band - blue band outside the track
        from .voice import VOICE_SEGMENTS
        seg = VOICE_SEGMENTS.get(self.win.band)
        if seg:
            f0 = max(0, (seg[0] - band["start"]) / span)
            f1 = min(1, (seg[1] - band["start"]) / span)
            cr.set_source_rgba(0.35, 0.62, 1.0, 0.35)
            cr.set_line_width(6)
            cr.arc(cx, cy, r - 3, start + sweep * f0, start + sweep * f1)
            cr.stroke()
        # live voice spots (DX cluster) - blue diamonds
        cr.set_source_rgb(0.40, 0.68, 1.0)
        for v in getattr(self.win, "voice", {}).get(self.win.band, []):
            f = (v["hz"] - band["start"]) / span
            if 0 <= f <= 1:
                a = start + sweep * f
                px, py = cx + (r - 4) * math.cos(a), cy + (r - 4) * math.sin(a)
                cr.move_to(px, py - 7); cr.line_to(px + 6, py); cr.line_to(px, py + 7); cr.line_to(px - 6, py)
                cr.close_path(); cr.fill()
                self.hits.append((px, py, "voice", v))
        # broadcast stations on air now - white ticks
        cr.set_source_rgb(0.95, 0.93, 0.88)
        cr.set_line_width(2)
        for b in getattr(self.win, "bc", []):
            f = (b["hz"] - band["start"]) / span
            if 0 <= f <= 1:
                a = start + sweep * f
                cr.move_to(cx + (r - 1) * math.cos(a), cy + (r - 1) * math.sin(a))
                cr.line_to(cx + (r - 9) * math.cos(a), cy + (r - 9) * math.sin(a))
                cr.stroke()
                self.hits.append((cx + (r - 5) * math.cos(a), cy + (r - 5) * math.sin(a), "station", b))
        # spot markers (stations heard nearby)
        cr.set_source_rgb(0.24, 0.86, 0.52)
        for sp in self.win.spots.get(self.win.band, []) if hasattr(self.win, "spots") else []:
            f = (sp["hz"] - band["start"]) / span
            if 0 <= f <= 1:
                a = start + sweep * f
                px, py = cx + (r - 22) * math.cos(a), cy + (r - 22) * math.sin(a)
                cr.arc(px, py, 3.5, 0, 2 * math.pi)
                cr.fill()
                self.hits.append((px, py, "spot", sp))
        # live CQ markers (decoded here) - amber triangles outside the track
        cr.set_source_rgb(1.0, 0.45, 0.35)
        for c in getattr(self.win, "cqs", {}).values():
            f = (c["rf"] - band["start"]) / span
            if 0 <= f <= 1:
                a = start + sweep * f
                px, py = cx + (r - 4) * math.cos(a), cy + (r - 4) * math.sin(a)
                cr.arc(px, py, 6, 0, 2 * math.pi)
                cr.fill()
                self.hits.append((px, py, "cq", c))
        # strongest stations near the receiver - gold stars with call signs
        cr.select_font_face("sans-serif", 0, 1)
        cr.set_font_size(13)
        for rank, st in enumerate(self.win.strongest()):
            f = (st["hz"] - band["start"]) / span
            if not 0 <= f <= 1:
                continue
            a = start + sweep * f
            px, py = cx + (r - 22) * math.cos(a), cy + (r - 22) * math.sin(a)
            cr.set_source_rgb(1.0, 0.84, 0.25)
            for k in range(10):  # 5-point star
                rr = 9 if k % 2 == 0 else 4
                ang = -math.pi / 2 + k * math.pi / 5
                (cr.move_to if k == 0 else cr.line_to)(px + rr * math.cos(ang), py + rr * math.sin(ang))
            cr.close_path()
            cr.fill()
            # call sign, staggered inward so neighbours don't overlap
            lr = r - 64 - (rank % 2) * 16
            label = f"{st['call']} {st['snr']:+.0f}"
            ext = cr.text_extents(label)
            cr.set_source_rgba(0.08, 0.08, 0.07, 0.75)
            lx, ly = cx + lr * math.cos(a) - ext.width / 2, cy + lr * math.sin(a) + ext.height / 2
            cr.rectangle(lx - 3, ly - ext.height - 3, ext.width + 6, ext.height + 6)
            cr.fill()
            cr.set_source_rgb(1.0, 0.84, 0.25)
            cr.move_to(lx, ly)
            cr.show_text(label)
            self.hits.append((px, py, st["kind"], st["item"]))
        # inner knob
        cr.set_source_rgb(0.20, 0.195, 0.185)
        cr.arc(cx, cy, r * 0.42, 0, 2 * math.pi)
        cr.fill()
        # needle
        a = start + sweep * frac
        cr.set_source_rgb(*AMBER)
        cr.set_line_width(6)
        cr.move_to(cx, cy)
        cr.line_to(cx + (r - 34) * math.cos(a), cy + (r - 34) * math.sin(a))
        cr.stroke()
        cr.arc(cx, cy, 11, 0, 2 * math.pi)
        cr.fill()


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Shortwave Dial")
        self.set_default_size(1024, 700)
        self.band, self.mode = "40m", "usb"
        self.freq = BANDS["40m"]["default"]
        self.proc = None
        self.rig = None
        self._pending = None
        self.rx_index = 0

        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Shortwave Dial", css_classes=["title"]))
        tv = Adw.ToolbarView()
        tv.add_top_bar(header)
        self.rx = Gtk.DropDown.new_from_strings([r[0] for r in RECEIVERS])
        self.rx.add_css_class("rxdrop")
        self.rx.connect("notify::selected", self.on_receiver)
        header.pack_start(self.rx)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=40,
                       margin_top=24, margin_bottom=24, margin_start=32, margin_end=32)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        self.body, self.left = body, left
        self.dial = Dial(self)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.on_scroll)
        self.dial.add_controller(scroll)
        click = Gtk.GestureClick()
        click.connect("pressed", self.on_dial_click)
        self.dial.add_controller(click)
        left.append(self.dial)
        hint = Gtk.Label(label="🖱  Scroll or ↑↓ ←→ to tune  ·  ⇧ = fine 10 Hz", css_classes=["hint"],
                         halign=Gtk.Align.CENTER)
        left.append(hint)
        body.append(left)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, hexpand=True)
        right.append(Gtk.Label(label="Frequency", xalign=0, css_classes=["label-small"]))
        row = Gtk.Box(spacing=12)
        self.readout = Gtk.Label(css_classes=["readout"])
        row.append(self.readout)
        row.append(Gtk.Label(label="MHz", css_classes=["mhz"], valign=Gtk.Align.END, margin_bottom=16))
        self.mode_pill = Gtk.Label(css_classes=["mode-pill"], valign=Gtk.Align.CENTER, hexpand=True,
                                   halign=Gtk.Align.END)
        row.append(self.mode_pill)
        right.append(row)
        self.auto = True
        self._guard = False
        self._skip_auto = False
        self.band_buttons = {}
        self.mode_buttons = {}
        mrow = Gtk.Box(spacing=10)
        modes = self._toggle_row([m.upper() for m in MODES], "mode", self.on_mode)
        modes.set_hexpand(True)
        mrow.append(modes)
        self.auto_btn = Gtk.ToggleButton(label="✨ Auto", active=True, css_classes=["autobtn"],
                                         tooltip_text="Pick CW / USB / LSB / AM for the part of the band you are in")
        self.auto_btn.connect("toggled", self.on_auto)
        mrow.append(self.auto_btn)
        right.append(mrow)
        self.lock = Gtk.Label(label="⏳ Waiting for receiver…", xalign=0, css_classes=["lock", "pending"])
        right.append(self.lock)
        mrow2 = Gtk.Box(spacing=10)
        self.meter = Gtk.LevelBar(min_value=0, max_value=15, hexpand=True, valign=Gtk.Align.CENTER,
                                  css_classes=["smeter"])
        for name, val in (("low", 5), ("high", 9), ("full", 15)):
            self.meter.add_offset_value(name, val)
        mrow2.append(self.meter)
        self.meter_lbl = Gtk.Label(label="📶 S-meter starting…", xalign=0, css_classes=["meter-lbl"])
        mrow2.append(self.meter_lbl)
        right.append(mrow2)
        GLib.timeout_add(500, self.update_meter)

        self.status = Gtk.Label(label="● Connecting…", xalign=0, css_classes=["status"])
        right.append(self.status)

        right.append(Gtk.Label(label="Band", xalign=0, css_classes=["label-small"], margin_top=8))
        right.append(self._toggle_row(list(BANDS), "band", self.on_band))
        self.cond_note = Gtk.Label(label="⏳ Checking band conditions…", xalign=0,
                                   css_classes=["cond-note"], wrap=True)
        right.append(self.cond_note)
        self.conds = {}
        self.spots = {}
        self.spot_head = Gtk.Label(label="📡 Heard nearby (click to jump)", xalign=0,
                                   css_classes=["spot-head"], margin_top=4)
        self.spot_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.spot_list.connect("row-activated", self.on_spot)
        sw = Gtk.ScrolledWindow(vexpand=True, min_content_height=150)
        sw.set_child(self.spot_list)
        spot_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        spot_page.append(self.spot_head)
        spot_page.append(sw)

        # live CQs decoded here by our own FT8 decoder
        self.cqs = {}
        self.cq_head = Gtk.Label(label="📣 Listening for CQs (USB mode, every 15 s)…", xalign=0,
                                 css_classes=["spot-head"], margin_top=4)
        self.cq_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.cq_list.connect("row-activated", self.on_cq)
        sw2 = Gtk.ScrolledWindow(vexpand=True, min_content_height=150)
        sw2.set_child(self.cq_list)
        cq_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        cq_page.append(self.cq_head)
        cq_page.append(sw2)

        stack = Gtk.Stack(vexpand=True)
        self.bc = []
        bc_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.bc_head = Gtk.Label(label="📻 Loading station list…", xalign=0, wrap=True,
                                 css_classes=["spot-head"], margin_top=4)
        self.bc_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.bc_list.connect("row-activated", self.on_station)
        sw5 = Gtk.ScrolledWindow(vexpand=True, min_content_height=150)
        sw5.set_child(self.bc_list)
        bc_page.append(self.bc_head)
        bc_page.append(sw5)
        stack.add_titled(bc_page, "bc", "📻 Stations")
        stack.add_titled(cq_page, "cq", "📣 Live CQs")
        self.voice = {}
        v_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.v_head = Gtk.Label(label="🎙️ Looking for voice stations…", xalign=0, wrap=True,
                                css_classes=["spot-head"], margin_top=4)
        self.v_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.v_list.connect("row-activated", self.on_voice)
        sw4 = Gtk.ScrolledWindow(vexpand=True, min_content_height=150)
        sw4.set_child(self.v_list)
        v_page.append(self.v_head)
        v_page.append(sw4)
        stack.add_titled(v_page, "voice", "🎙️ Voice")
        txt_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.txt_head = Gtk.Label(label="💬 FT8 messages appear here every 15 s (USB mode)", xalign=0, wrap=True)
        self.txt_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.txt_list.connect("row-activated", self.on_cq)
        sw3 = Gtk.ScrolledWindow(vexpand=True)
        sw3.set_child(self.txt_list)
        txt_page.append(self.txt_head)
        txt_page.append(sw3)
        stack.add_titled(txt_page, "txt", "💬 FT8 text")
        cw_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=4)
        self.cw_head = Gtk.Label(label="🔤 Switch to CW mode to decode Morse.", xalign=0, wrap=True,
                                 css_classes=["spot-head"])
        self.cw_text = Gtk.Label(label="", xalign=0, yalign=0, wrap=True, selectable=True,
                                 css_classes=["cw-text"], vexpand=True)
        sw7 = Gtk.ScrolledWindow(vexpand=True, min_content_height=150)
        sw7.set_child(self.cw_text)
        self.cw_scroll = sw7
        cw_page.append(self.cw_head)
        cw_page.append(sw7)
        clr = Gtk.Button(label="🧹 Clear", halign=Gtk.Align.START, css_classes=["tabbtn"])
        clr.connect("clicked", lambda *_: self.cw_text.set_label(""))
        cw_page.append(clr)
        stack.add_titled(cw_page, "cw", "🔤 Morse")
        self.txt_rows = []
        stack.add_titled(spot_page, "spots", "📡 Heard")
        log_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=6)
        lrow = Gtk.Box(spacing=8)
        self.call_entry = Gtk.Entry(placeholder_text="Callsign", hexpand=True, css_classes=["callbox"])
        self.call_entry.connect("activate", self.on_lookup)
        lrow.append(self.call_entry)
        b1 = Gtk.Button(label="🔎 Look up", css_classes=["bigbtn"])
        b1.connect("clicked", self.on_lookup)
        lrow.append(b1)
        b2 = Gtk.Button(label="📝 Log it", css_classes=["bigbtn"])
        b2.connect("clicked", self.on_log)
        lrow.append(b2)
        log_page.append(lrow)
        self.lookup_out = Gtk.Label(label="Type a call, or click a station anywhere to fill it in.",
                                    xalign=0, wrap=True, css_classes=["lookup"])
        log_page.append(self.lookup_out)
        self.log_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        sw6 = Gtk.ScrolledWindow(vexpand=True, min_content_height=120)
        sw6.set_child(self.log_list)
        log_page.append(sw6)
        stack.add_titled(log_page, "log", "📒 Log")
        self.stack = stack
        tabs = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                           max_children_per_line=6, min_children_per_line=3,
                           column_spacing=6, row_spacing=6, margin_top=6)
        first = None
        self.tab_btns = {}
        page = stack.get_pages()
        for i in range(page.get_n_items()):
            pg = page.get_item(i)
            b = Gtk.ToggleButton(label=pg.get_title(), css_classes=["tabbtn"])
            if first:
                b.set_group(first)
            else:
                first = b
                b.set_active(True)
            b.connect("toggled", lambda btn, name=pg.get_name(): btn.get_active() and stack.set_visible_child_name(name))
            self.tab_btns[pg.get_name()] = b
            tabs.append(b)
        right.append(tabs)
        self._grids = {}
        right.append(stack)
        rscroll = Gtk.ScrolledWindow(hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        rscroll.set_child(right)
        if os.environ.get("SWDIAL_DEBUG"):
            def _dbg():
                mn, nat, *_ = right.measure(Gtk.Orientation.HORIZONTAL, -1)
                print(f"LAYOUT right min={mn} nat={nat} shown={rscroll.get_width()} win={self.get_width()}", flush=True)
                return True
            GLib.timeout_add_seconds(3, _dbg)
        body.append(rscroll)

        tv.set_content(body)
        self.set_content(tv)
        self.set_size_request(380, 480)
        # Tiled Hyprland windows can be narrow: stack dial above controls.
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 820px"))
        bp.add_setter(body, "margin-start", 12)
        bp.add_setter(body, "margin-end", 12)
        bp.add_setter(body, "margin-top", 12)
        bp.connect("apply", lambda *_: self.add_css_class("compact"))
        bp.connect("unapply", lambda *_: self.remove_css_class("compact"))
        bp.add_setter(body, "orientation", Gtk.Orientation.VERTICAL)
        bp.add_setter(body, "spacing", 16)
        bp.add_setter(left, "vexpand", False)
        self.add_breakpoint(bp)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)  # beat button focus nav
        keys.connect("key-pressed", self.on_key)
        keys.connect("key-released", self.on_key_up)
        self.add_controller(keys)
        self.connect("close-request", self.on_close)
        self.refresh()
        GLib.idle_add(self.start_kiwi)
        self.refresh_conditions()
        GLib.timeout_add_seconds(300, self.refresh_conditions)  # PSKReporter fair use
        self.refresh_voice()
        GLib.timeout_add_seconds(120, self.refresh_voice)  # DX cluster, cached 2 min
        self.refresh_stations()
        GLib.timeout_add_seconds(600, self.refresh_stations)  # EiBi, downloaded once a day
        self.show_log()
        from .live_ft8 import LiveFT8
        self.live = LiveFT8(self._live_pid, self._on_ft8_slot)
        from .audio import Quiet
        from .cw import LiveCW
        self.quiet = Quiet()
        self._auto_tab_for = None
        self.live_cw = LiveCW(self._cw_pid, lambda t, w, f: GLib.idle_add(self.add_cw, t, w, f))

    # ----- Morse -----
    def _cw_pid(self):
        if self.proc and self.proc.poll() is None and self.mode == "cw" and not self._pending \
                and not getattr(self, "_held", False):
            return self.proc.pid
        return None

    def add_cw(self, text, wpm, tone):
        t = (self.cw_text.get_label() + text)[-1500:]
        self.cw_text.set_label(t)
        self.cw_head.set_label(f"🔤 Decoding Morse · {wpm} WPM · tone {tone:.0f} Hz")
        adj = self.cw_scroll.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()) or False)
        return False

    def select_tab(self, name):
        b = getattr(self, "tab_btns", {}).get(name)
        if b:
            b.set_active(True)

    # ----- locked: FT8 = quiet + chime + FT8 text, CW = Morse tab -----
    FT8_DIALS = (1_840_000, 3_573_000, 5_357_000, 7_074_000, 10_136_000, 14_074_000, 18_100_000,
                 21_074_000, 24_915_000, 28_074_000)

    def on_locked(self):
        pid = self.proc.pid if self.proc and self.proc.poll() is None else None
        on_ft8 = self.mode == "usb" and any(abs(self.freq - d) <= 1500 for d in self.FT8_DIALS)
        if on_ft8 and not self.quiet.active and pid:
            if self.quiet.on(pid):
                from .audio import chime
                chime()
                self.select_tab("txt")
        elif not on_ft8 and self.quiet.active:
            self.quiet.off(pid)
        if self.mode == "cw" and self._auto_tab_for != "cw":
            self.select_tab("cw")
        self._auto_tab_for = self.mode

    # ----- live FT8 CQs -----
    def _live_pid(self):
        if self.proc and self.proc.poll() is None and self.mode == "usb" and not self._pending:
            self._slot_dial = self.freq
            return self.proc.pid
        return None

    def _on_ft8_slot(self, slot, res):  # called from a worker thread
        GLib.idle_add(self.apply_ft8, slot, res, getattr(self, "_slot_dial", self.freq))

    def apply_ft8(self, slot, res, dial):
        now = time.time()
        res = [r for r in res if "error" not in r]
        ncq = 0
        for r in res:
            if r.get("is_cq") and r.get("call"):
                ncq += 1
                if r.get("grid"):
                    self._grids[r["call"]] = r["grid"]
                self.cqs[r["call"]] = {"call": r["call"], "grid": r.get("grid") or "",
                                       "rf": int(dial + r["freq_hz"]), "snr": r.get("snr"),
                                       "msg": r["msg"], "t": now, "slot": slot}
        self.cqs = {k: v for k, v in self.cqs.items() if now - v["t"] < 180}
        hh = f"{slot[:2]}:{slot[2:4]}:{slot[4:]}"
        print(f"FT8 {slot} decodes={len(res)} cq={ncq}", flush=True)
        self.cq_head.set_label(f"📣 {hh} UTC: {len(res)} decoded, {ncq} CQ"
                               f" · {len(self.cqs)} CQs in last 3 min (click to jump)")
        while (row := self.cq_list.get_row_at_index(0)) is not None:
            self.cq_list.remove(row)
        for c in sorted(self.cqs.values(), key=lambda c: -c["t"]):
            snr = f"{c['snr']:+.0f} dB" if c.get("snr") is not None else ""
            age = int(now - c["t"])
            text = f"{c['rf'] / 1e6:.5f}  {snr:>6}  {age:>3}s ago   {c['msg']}"
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=text, xalign=0, css_classes=["spot-row"]))
            row.cq = c
            self.cq_list.append(row)
        for k, r in enumerate(sorted(res, key=lambda r: r["freq_hz"])):
            snr = f"{r['snr']:+.0f}" if r.get("snr") is not None else ""
            text = f"{hh}  {snr:>4} dB  {int(r['freq_hz']):>4} Hz   {r['msg']}"
            row = Gtk.ListBoxRow()
            lab = Gtk.Label(label=text, xalign=0, css_classes=["spot-row"])
            if r.get("is_cq"):
                lab.add_css_class("cond-good")
            row.set_child(lab)
            row.cq = {"rf": int(dial + r["freq_hz"])}
            self.txt_list.insert(row, k)  # this slot's block goes on top
        self.txt_head.set_label(f"💬 {hh} UTC: {len(res)} messages (newest at top, green = CQ, click to jump)")
        # keep the list short: last ~80 lines
        i = 80
        while (row := self.txt_list.get_row_at_index(i)) is not None:
            self.txt_list.remove(row)
        self.dial.queue_draw()
        return False

    def refresh_voice(self):
        import threading

        def work():
            try:
                from .voice import voice_spots
                res = voice_spots()
            except Exception as e:
                res = {"error": str(e)}
            GLib.idle_add(self.apply_voice, res)

        threading.Thread(target=work, daemon=True).start()
        return True  # keep the 2-minute timer

    def apply_voice(self, res):
        if "error" in res:
            self.v_head.set_label(f"🔴 Voice spots unavailable: {res['error']}")
            return False
        self.voice = res
        self.show_voice()
        self.dial.queue_draw()
        return False

    def show_voice(self):
        from .voice import VOICE_SEGMENTS
        seg = VOICE_SEGMENTS.get(self.band)
        while (row := self.v_list.get_row_at_index(0)) is not None:
            self.v_list.remove(row)
        if not seg:
            self.v_head.set_label(f"📻 {self.band} is a broadcast band: it is all voice (AM).")
            return
        spots = sorted(self.voice.get(self.band, []), key=lambda v: v["hz"])
        self.v_head.set_label(f"🎙️ {self.band} voice part is {seg[0]/1e6:.3f}–{seg[1]/1e6:.3f} MHz (blue)."
                              f" {len(spots)} talking now (DX cluster, last 30 min, click to jump)")
        for v in spots:
            text = f"{v['hz']/1e6:.4f}  {v['mode'].upper()}  {v['call']:<10} {v['age_min']:>2}m ago  {v['comment'][:28]}"
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=text, xalign=0, css_classes=["spot-row"]))
            row.voice = v
            self.v_list.append(row)

    def on_voice(self, _lb, row):
        v = row.voice
        self.set_mode_ui(v["mode"])
        self._skip_auto = True
        self.fill_call(v["call"])
        self.freq = clamp_to_band(self.band, v["hz"])
        self.refresh()
        self.queue_send()

    # ----- strongest stations -----
    def strongest(self, n=5):
        """Top stations on this band by signal-to-noise: PSKReporter spots from
        receivers near the Kiwi, plus FT8 we decoded ourselves (fresher wins)."""
        best = {}
        for sp in getattr(self, "spots", {}).get(self.band, []):
            if sp.get("snr") is not None and sp.get("call"):
                best[sp["call"]] = {"call": sp["call"], "hz": sp["hz"], "snr": sp["snr"], "kind": "spot", "item": sp}
        for c in getattr(self, "cqs", {}).values():
            if c.get("snr") is not None and BANDS[self.band]["start"] <= c["rf"] <= BANDS[self.band]["end"]:
                best[c["call"]] = {"call": c["call"], "hz": c["rf"], "snr": c["snr"], "kind": "cq", "item": c}
        # keep them apart on the ring (one small tick) so the labels stay readable
        gap = (BANDS[self.band]["end"] - BANDS[self.band]["start"]) / 40
        top = []
        for x in sorted(best.values(), key=lambda x: -x["snr"]):
            if all(abs(x["hz"] - t["hz"]) >= gap for t in top):
                top.append(x)
            if len(top) == n:
                break
        return sorted(top, key=lambda x: x["hz"])

    # ----- auto mode -----
    def auto_mode(self, hz):
        if self.band == "40m":
            if hz < 7_040_000:
                return "cw"
            if hz < 7_060_000 or 7_070_000 <= hz <= 7_080_000:  # digital: FT8 7.074, JS8 7.078
                return "usb"
            return "lsb"
        if self.band == "20m":
            return "cw" if hz < 14_070_000 else "usb"
        return "am"  # broadcast bands

    def set_mode_ui(self, mode):
        self._guard = True
        try:
            self.mode = mode
            b = self.mode_buttons.get(mode.upper())
            if b:
                b.set_active(True)
            self.refresh()
        finally:
            self._guard = False

    def on_auto(self, btn):
        self.auto = btn.get_active()
        if self.auto:
            self.queue_send()

    # ----- click a marker on the dial -----
    def on_dial_click(self, _g, _n, x, y):
        best, bd = None, 15 ** 2
        for px, py, kind, item in getattr(self.dial, "hits", []):
            d = (px - x) ** 2 + (py - y) ** 2
            if d < bd:
                best, bd = (kind, item), d
        if not best:
            return
        from types import SimpleNamespace as NS
        kind, item = best
        {"spot": lambda: self.on_spot(None, NS(spot=item)),
         "cq": lambda: self.on_cq(None, NS(cq=item)),
         "voice": lambda: self.on_voice(None, NS(voice=item)),
         "station": lambda: self.on_station(None, NS(station=item))}[kind]()

    # ----- broadcast stations (EiBi) -----
    def refresh_stations(self):
        import threading

        def work():
            try:
                from .stations import stations_now
                res = {b: stations_now(v["start"], v["end"]) for b, v in BANDS.items() if v["mode"] == "am"}
            except Exception as e:
                res = {"error": str(e)}
            GLib.idle_add(self.apply_stations, res)

        threading.Thread(target=work, daemon=True).start()
        return True

    def apply_stations(self, res):
        if "error" in res:
            self.bc_head.set_label(f"🔴 Station list unavailable: {res['error']}")
            return False
        self._bc_all = res
        self.show_stations()
        return False

    def show_stations(self):
        all_ = getattr(self, "_bc_all", None)
        while (row := self.bc_list.get_row_at_index(0)) is not None:
            self.bc_list.remove(row)
        if all_ is None:
            return
        self.bc = all_.get(self.band, [])
        if BANDS[self.band]["mode"] != "am":
            self.bc_head.set_label("📻 Broadcast stations are on 31m and 25m. Pick one of those bands.")
        else:
            self.bc_head.set_label(f"📻 {len(self.bc)} stations on {self.band} right now (EiBi schedule, click to jump)")
        for b in self.bc:
            text = f"{b['hz'] / 1e3:>6.0f} kHz  {b['station'][:26]:<26} {b['lang'][:10]:<10} → {b['target']}"
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=text, xalign=0, css_classes=["spot-row"]))
            row.station = b
            self.bc_list.append(row)
        self.dial.queue_draw()

    def on_station(self, _lb, row):
        b = row.station
        self.set_mode_ui("am")
        self._skip_auto = True
        self.freq = clamp_to_band(self.band, b["hz"])
        self.refresh()
        self.queue_send()

    # ----- callsign lookup + log -----
    def fill_call(self, call, grid=""):
        if call and not call.startswith("<"):
            self.call_entry.set_text(call)
            if grid:
                self._grids[call] = grid
            self.on_lookup()

    def on_lookup(self, *_):
        call = self.call_entry.get_text().strip().upper()
        if not call:
            return
        _n, _h, _p, lat, lon = RECEIVERS[self.rx_index]
        try:
            from .lookup import lookup
            r = lookup(call, lat, lon, self._grids.get(call, ""))
        except Exception as e:
            self.lookup_out.set_label(f"🔴 Lookup failed: {e}")
            return
        where = f"  ·  📍 {r['km']:,} km, beam {r['bearing']}°" if "km" in r else ""
        grid = f"  ·  {r['grid']}" if r.get("grid") else ""
        self.lookup_out.set_label(f"🔎 {r['call']}  ·  🌍 {r['country']} ({r['cont']}, CQ {r['cq']}){grid}{where}")

    def on_log(self, *_):
        call = self.call_entry.get_text().strip().upper()
        if not call:
            self.lookup_out.set_label("✍️ Type a callsign first.")
            return
        from .lookup import log_entry
        rst = self.report()
        log_entry(call, self.freq, self.band, self.mode, grid=self._grids.get(call, ""), rst=rst,
                  note="FT8" if self.mode == "usb" and call in self.cqs else "")
        self.lookup_out.set_label(f"✅ Logged {call} on {self.freq / 1e6:.4f} MHz {self.mode.upper()}"
                                  + (f"  ·  report {rst}" if rst else ""))
        self.show_log()

    def show_log(self):
        from .lookup import read_log
        while (row := self.log_list.get_row_at_index(0)) is not None:
            self.log_list.remove(row)
        for d in read_log():
            t = d.get("TIME_ON", "")[:4]
            text = (f"{d.get('QSO_DATE', '')[6:8]}/{d.get('QSO_DATE', '')[4:6]} {t[:2]}:{t[2:]}Z  "
                    f"{d['CALL']:<10} {d.get('FREQ', '')[:7]:<8} {d.get('MODE', ''):<4} {d.get('RST_RCVD', ''):>4}  {d.get('GRIDSQUARE', '')}")
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=text, xalign=0, css_classes=["spot-row"]))
            self.log_list.append(row)

    def on_cq(self, _lb, row):
        c = row.cq
        self.set_mode_ui("usb")
        self._skip_auto = True
        self.fill_call(c.get("call"), c.get("grid", ""))
        self.freq = clamp_to_band(self.band, c["rf"] - 1500)  # heard as a 1.5 kHz tone
        self.refresh()
        self.queue_send()

    # ----- band conditions (green/amber/red) -----
    def refresh_conditions(self):
        import threading
        _n, _h, _p, lat, lon = RECEIVERS[self.rx_index]

        def work():
            try:
                from .conditions import band_conditions, heard_spots
                res = band_conditions(lat, lon)
                res["_spots"] = heard_spots(lat, lon)
            except Exception as e:  # never break the radio over this
                res = {"error": str(e)}
            GLib.idle_add(self.apply_conditions, res)

        threading.Thread(target=work, daemon=True).start()
        return True

    def apply_conditions(self, res):
        if "error" in res:
            self.cond_note.set_label("⚪ Band conditions unavailable")
            return False
        self.spots = res.pop("_spots", {}) or {}
        self.conds = res
        emoji = {"good": "🟢", "fair": "🟡", "poor": "🔴"}
        for band, btn in self.band_buttons.items():
            for c in ("cond-good", "cond-fair", "cond-poor"):
                btn.remove_css_class(c)
            info = res.get(band)
            if info and info.get("rating") in emoji:
                btn.add_css_class("cond-" + info["rating"])
                btn.set_tooltip_text(f"{emoji[info['rating']]} {info['rating'].upper()}: {info['reason']}")
        self.show_cond_note()
        self.show_spots()
        return False

    DIGITAL = ("FT8", "FT4", "JS8", "WSPR", "PSK31", "RTTY", "FST4", "Q65", "JT65")

    def show_spots(self):
        while (row := self.spot_list.get_row_at_index(0)) is not None:
            self.spot_list.remove(row)
        spots = sorted(self.spots.get(self.band, []),
                       key=lambda sp: -(sp["snr"] if sp.get("snr") is not None else -99))[:40]
        stars = {st["call"] for st in self.strongest()}
        self.spot_head.set_label(f"📡 Heard near the receiver on {self.band}: {len(self.spots.get(self.band, []))},"
                                 " strongest first. ⭐ = on the ring (click to jump)"
                                 if spots else f"📡 Nothing heard nearby on {self.band}")
        for sp in spots:
            km = f"{sp['km']:,} km" if sp.get("km") is not None else ""
            snr = f"{sp['snr']:+.0f} dB" if sp.get("snr") is not None else ""
            star = "⭐" if sp["call"] in stars else "  "
            text = f"{star} {sp['hz'] / 1e6:.5f}  {sp['call']:<10} {snr:>6}  {sp['mode']:<5} {km:>9}  {sp.get('grid','')}"
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=text, xalign=0, css_classes=["spot-row"]))
            row.spot = sp
            self.spot_list.append(row)
        self.dial.queue_draw()

    def on_spot(self, _lb, row):
        sp = row.spot
        mode = (sp.get("mode") or "").upper()
        if mode == "CW":
            self.mode, hz = "cw", sp["hz"]
        elif mode in self.DIGITAL:
            # Spot is the signal's RF frequency; tune 1500 Hz below in USB so
            # it is heard as a ~1.5 kHz tone (how WSJT-X dials work).
            self.mode, hz = "usb", sp["hz"] - 1500
        else:
            hz = sp["hz"]
        self.freq = clamp_to_band(self.band, hz)
        self.set_mode_ui(self.mode)
        self._skip_auto = True
        self.fill_call(sp.get("call"), sp.get("grid", ""))
        self.refresh()
        self.queue_send()

    def show_cond_note(self):
        info = self.conds.get(self.band)
        if info:
            e = {"good": "🟢", "fair": "🟡", "poor": "🔴"}.get(info["rating"], "⚪")
            self.cond_note.set_label(f"{e} {self.band} {info['rating'].upper()} · {info['reason']}")

    def _toggle_row(self, names, kind, handler):
        box = Gtk.Box(spacing=10, homogeneous=True)
        first = None
        for n in names:
            b = Gtk.ToggleButton(label=n, css_classes=["bigbtn", kind])
            if first:
                b.set_group(first)
            else:
                first = b
            if n.lower() == (self.band if kind == "band" else self.mode):
                b.set_active(True)
            b.connect("toggled", handler, n)
            if kind == "band":
                self.band_buttons[n] = b
            else:
                self.mode_buttons[n] = b
            box.append(b)
        return box

    # ----- kiwi + rigctl -----
    def start_kiwi(self):
        self.stop_kiwi()
        if getattr(self, "quiet", None) and self.quiet.active:
            self.quiet.off(None)  # new stream starts on the normal speakers
        name, host, port, _lat, _lon = RECEIVERS[self.rx_index]
        cmd = [sys.executable, str(KIWID), "-s", host, "-p", str(port),
               "-f", f"{self.freq / 1000:.3f}", "-m", self.mode,
               "-u", "ShortwaveDial-G7MJF", "--rigctl", "--rigctl-port", str(RIG_PORT)]
        self.proc = subprocess.Popen(cmd, cwd=KIWID.parent, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        import threading
        threading.Thread(target=self._read_kiwi, args=(self.proc,), daemon=True).start()
        self.set_status(f"● Connecting to {name}…", ok=True)
        self.set_lock("⏳ Waiting for receiver…", "pending")
        self._tries = 0
        GLib.timeout_add(500, self.try_rig)
        return False

    def _read_kiwi(self, proc):
        """kiwiclientd prints 'Block: …, RSSI: -97.7, …' per audio block. Keep a
        smoothed S-meter and write only the other lines to the log."""
        import re
        pat = re.compile(rb"RSSI:\s*(-?[0-9.]+)")
        with open(ROOT / "proof" / "kiwiclientd.log", "wb") as log:
            buf = b""
            while True:
                chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(512)
                if not chunk:
                    break
                buf += chunk
                *parts, buf = re.split(rb"[\r\n]", buf)
                for line in parts:
                    m = pat.search(line)
                    if m:
                        v = float(m.group(1))
                        old = getattr(self, "_rssi", None)
                        # fast attack, slow release, like a real S-meter
                        self._rssi = v if old is None or v > old else old * 0.85 + v * 0.15
                        if old is None:
                            print(f"RSSI first {v}", flush=True)
                    elif line.strip():
                        log.write(line + b"\n")
                        log.flush()

    @staticmethod
    def s_units(dbm):
        """HF S-meter: S9 = -73 dBm, 6 dB per S unit."""
        if dbm >= -73:
            over = int(round(dbm + 73))
            return 9, over
        return max(0, min(9, int(round(9 + (dbm + 73) / 6)))), 0

    def report(self):
        """Signal report for the log: RS for voice/AM (e.g. 57), RST for CW (579)."""
        if getattr(self, "_rssi", None) is None:
            return ""
        s, _ = self.s_units(self._rssi)
        s = max(1, s)
        return f"5{s}9" if self.mode == "cw" else f"5{s}"

    def update_meter(self):
        v = getattr(self, "_rssi", None)
        if v is None or self._pending or getattr(self, "_held", False):
            return True
        s, over = self.s_units(v)
        self.meter.set_value(min(15.0, s + over / 6))
        txt = f"S9+{over}" if over else f"S{s}"
        self.meter_lbl.set_label(f"📶 {txt}  ({v:.0f} dBm)  ·  report {self.report()}")
        return True

    def try_rig(self):
        if self.proc and self.proc.poll() is not None:
            self.set_status("● Refused or dropped — try another receiver", ok=False)
            return False
        try:
            self.rig = Rigctl(port=RIG_PORT).connect()
            self.set_status("● Connected", ok=True)
            self.check_lock()
            if not getattr(self, "_lock_timer", None):
                self._lock_timer = GLib.timeout_add_seconds(2, self.check_lock)
            self.write_state(True)
            return False
        except OSError:
            self._tries += 1
            if self._tries > 30:
                self.set_status("● Could not reach rigctl", ok=False)
                return False
            return True

    def stop_kiwi(self):
        if self.rig:
            self.rig.close()
            self.rig = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def send(self):
        self._pending = None
        if self.auto and not self._skip_auto:
            self.set_mode_ui(self.auto_mode(self.freq))
        self._skip_auto = False
        if not self.rig:
            return False
        for attempt in (1, 2):  # one retry: a slow reply can knock the link out of step
            try:
                self.rig.set_freq(self.freq)
                self.rig.set_mode(self.mode)
                self.set_status("● Connected", ok=True)
                break
            except Exception as e:  # keep UI alive
                print(f"SEND-ERR try {attempt} freq={self.freq!r} mode={self.mode!r}: {e!r}", flush=True)
                if attempt == 2:
                    self.set_status(f"● Couldn't tune: {e}", ok=False)
        self.check_lock()
        self.write_state(True)
        return False

    def check_lock(self):
        """Read the frequency back from the receiver link and compare."""
        if not self.rig or self._pending or getattr(self, "_held", False):
            return True
        try:
            rx_hz = self.rig.get_freq()
            rx_mode = self.rig.get_mode().lower()
        except Exception:
            self.set_lock("⚠️ No answer from receiver", "off")
            return True
        if abs(rx_hz - self.freq) <= 5 and rx_mode == self.mode:
            self.on_locked()
            quiet = "  ·  🔇 FT8, sound off" if self.quiet.active else ""
            self.set_lock(f"✅ Receiver locked on {rx_hz / 1e6:.5f} MHz {rx_mode.upper()}{quiet}", None)
        else:
            self.set_lock(f"⚠️ Receiver is on {rx_hz / 1e6:.5f} MHz {rx_mode.upper()}", "off")
        return True

    def set_lock(self, text, state):
        self.lock.set_label(text)
        for c in ("pending", "off"):
            self.lock.remove_css_class(c)
        if state:
            self.lock.add_css_class(state)
        if state == "pending":
            self.readout.add_css_class("pending")
        else:
            self.readout.remove_css_class("pending")

    def queue_send(self):
        if self._pending:
            GLib.source_remove(self._pending)
        self.set_lock("🟡 Tuning…", "pending")
        self._pending = GLib.timeout_add(250, self.send)  # scroll: wait until wheel settles

    # ----- UI handlers -----
    def on_scroll(self, ctrl, _dx, dy):
        step = 10 if ctrl.get_current_event_state() & 1 else 100  # Shift = fine
        self.freq = clamp_to_band(self.band, int(self.freq - dy * step))
        self.refresh()
        self.queue_send()
        return True

    def on_key(self, _ctrl, keyval, _code, state):
        from gi.repository import Gdk
        steps = {Gdk.KEY_Up: 100, Gdk.KEY_Down: -100, Gdk.KEY_Right: 1000, Gdk.KEY_Left: -1000}
        if keyval not in steps:
            return False
        if state & Gdk.ModifierType.SHIFT_MASK:  # fine tune: 10 Hz either way
            step = 10 if steps[keyval] > 0 else -10
        else:
            step = steps[keyval]
        self.freq = clamp_to_band(self.band, self.freq + step)
        self.refresh()
        # Only move the dial while held; tune the receiver on key release.
        if self._pending:
            GLib.source_remove(self._pending)
            self._pending = None
        self._held = True
        self.set_lock("🟡 Let go to tune…", "pending")
        return True

    def on_key_up(self, _ctrl, keyval, _code, _state):
        from gi.repository import Gdk
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right):
            self._held = False
            self.queue_send()

    def on_band(self, btn, name):
        if btn.get_active() and name != self.band:
            self.band = name
            self.freq = BANDS[name]["default"]
            self.show_cond_note()
            self.show_spots()
            self.show_voice()
            self.show_stations()
            self.refresh()
            self.queue_send()

    def on_mode(self, btn, name):
        if btn.get_active() and not self._guard and self.auto and name.lower() != self.mode:
            self.auto_btn.set_active(False)  # you chose a mode yourself
        if btn.get_active():
            self.mode = name.lower()
            self.refresh()
            self.queue_send()

    def on_receiver(self, dd, _p):
        self.rx_index = dd.get_selected()
        GLib.idle_add(self.start_kiwi)
        self.refresh_conditions()

    def set_status(self, text, ok):
        self.status.set_label(text)
        if ok:
            self.status.remove_css_class("off")
        else:
            self.status.add_css_class("off")

    def refresh(self):
        self.readout.set_label(f"{self.freq / 1e6:.5f}")  # 10 Hz resolution
        self.mode_pill.set_label(self.mode.upper())
        self.dial.queue_draw()

    def write_state(self, on):
        STATE.write_text(json.dumps({"freq": self.freq, "mode": self.mode, "band": self.band,
                                     "receiver": RECEIVERS[self.rx_index][0], "conditions": {b: v.get("rating") for b, v in self.conds.items() if isinstance(v, dict)}, "on": on,
                                     "t": time.time()}))

    def on_close(self, *_):
        if getattr(self, "live", None):
            self.live.stop()
        if getattr(self, "live_cw", None):
            self.live_cw.stop()
        if getattr(self, "quiet", None):
            self.quiet.off(self.proc.pid if self.proc else None)
        self.stop_kiwi()
        try:
            self.write_state(False)
        except OSError:
            pass
        return False


def main():
    Adw.init()
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS)
    from gi.repository import Gdk
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    app = Adw.Application(application_id="org.funcoder.ShortwaveDial")
    app.connect("activate", lambda a: Window(a).present())
    app.run([])


if __name__ == "__main__":
    main()

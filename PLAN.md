# Shortwave Dial — plan

## Steps
1. ✅ Design in Claude Design (one new artifact) → PNGs in `design/`.
2. ✅ rigctl client + CLI + Waybar JSON (`swdial/`), tested against a fake rigctld (`tests/`).
3. ⛔ GTK4 window (`swdial/gtk_app.py`) — blocked on this box: GTK4 + PyGObject are not installed.
4. ⛔ Live kiwiclientd + audio clip — blocked on this box (no outbound to KiwiSDR ports, no audio server).
5. Next: run steps 3–4 on Jonathan's Omarchy laptop (GTK4 and PipeWire come with Omarchy).

## GTK4 window (to build)
- `Adw.ApplicationWindow`, dark style, `Adw.HeaderBar` titled "Shortwave Dial".
- Left: `Gtk.DrawingArea` dial (cairo). `Gtk.EventControllerScroll` → step ±100 Hz
  (Shift = ±1 kHz) → `Rigctl.set_freq`, debounced ~80 ms so we don't flood the Kiwi.
- Right: big `Gtk.Label` readout (CSS 72px, amber, monospace), receiver `Gtk.DropDown`,
  band `Gtk.ToggleButton` row (40m/31m/25m/20m from `bands.py`), mode row (AM/LSB/USB/CW).
- Status dot + word "Connected" (not colour alone).
- On Omarchy: `sudo pacman -S python-gobject gtk4 libadwaita` (Jonathan's call; all likely present).

## Waybar
```jsonc
"custom/swdial": {
  "exec": "~/shortwave-dial/.venv/bin/python -m swdial.cli waybar",
  "return-type": "json", "interval": 2,
  "on-click": "~/shortwave-dial/.venv/bin/python -m swdial.gtk_app",
  "on-scroll-up": "~/shortwave-dial/.venv/bin/python -m swdial.cli step 100",
  "on-scroll-down": "~/shortwave-dial/.venv/bin/python -m swdial.cli step -100"
}
```

## Receiver
- Planned: G3SDR, Weston-super-Mare (g3sdr.com:8073…8079, several channels, GPS, UK).
- Check the receiver's ext-API allowance before use; pick another UK Kiwi if refused.

# Shortwave Dial — agent rules

## Goal
A retro tuning-dial window for Omarchy (Arch + Hyprland) that tunes ONE public KiwiSDR,
plus a Waybar pill ("📻 7.074 USB"). Owner: Jonathan (G7MJF) — keep UI text large, easy to read,
high contrast, no thin fonts, no all-caps blocks.

## Design
- Claude Design artifact "Shortwave Dial": https://claude.ai/artifact/KZ8vYGLxHvW5nf3LSYw69g
- Exports: `design/shortwave-dial-window.png`, `design/waybar-pill.png`
- Font: Atkinson Hyperlegible Next / Mono. Colours: amber (#FFB347-ish) + teal on near-black.

## How it works
```
GTK4 window / swdial CLI / Waybar ──TCP 6400 (hamlib rigctl: F <Hz>, M <mode>, f, m)──▶ kiwiclientd ──ws──▶ KiwiSDR
```
- `vendor/kiwiclient` = jks-prv/kiwiclient, cloned, NOT committed, NOT copied (it has no licence).
  We only run it as a separate program.
- `swdial/rigctl.py` — our tiny rigctl client. `swdial/cli.py` — CLI + Waybar JSON.

## Commands
```
python -m venv .venv && .venv/bin/pip install numpy soundcard pytest
git clone --depth 1 https://github.com/jks-prv/kiwiclient vendor/kiwiclient
.venv/bin/python vendor/kiwiclient/kiwiclientd.py -s <kiwi-host> -p 8073 -f 7074 -m usb --enable-rigctl --rigctl-port 6400
.venv/bin/python -m swdial.cli band 40m
.venv/bin/python -m pytest -q tests
```

## Rules
- Fair use on public receivers: one channel, short sessions, no scanning, disconnect when done.
  Only use receivers whose admin allows non-browser (ext API) connections.
- Installs go in `.venv` only. No `sudo`. No pushing — this repo has no remote.
- Done means: tests pass + window runs + screenshot next to the design PNG + short audio clip.

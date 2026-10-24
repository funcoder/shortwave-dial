"""Remember where the dial was, so the next start comes back to the same channel.

Kept in $XDG_STATE_HOME/shortwave-dial/last.json (~/.local/state), which survives a
reboot, unlike the Waybar state file in $XDG_RUNTIME_DIR.
"""
import json
import os
from pathlib import Path

from .bands import BANDS, MODES, clamp_to_band

PATH = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "shortwave-dial" / "last.json"


def save(freq, mode, band, receiver, path=PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"freq": int(freq), "mode": mode, "band": band, "receiver": receiver}))
    tmp.replace(path)  # never leave a half-written file if we die mid-write


def load(path=PATH):
    """{'freq', 'mode', 'band', 'receiver'} from last time, or None if missing or broken."""
    try:
        d = json.loads(path.read_text())
        band, mode = d["band"], d["mode"]
        if band not in BANDS or mode not in MODES:
            return None
        return {"freq": clamp_to_band(band, int(d["freq"])), "mode": mode, "band": band,
                "receiver": str(d.get("receiver", ""))}
    except (OSError, ValueError, KeyError, TypeError):
        return None

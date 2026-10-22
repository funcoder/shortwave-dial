"""Runtime-only audio helpers: quiet the Kiwi audio while still decoding it,
and play a short chime. Nothing here changes any config file.

'Quiet' moves only the kiwiclientd stream to a temporary null sink (made with
pactl load-module, removed again on loud() / exit). parec --monitor-stream
still records it, so the FT8 decoder keeps working while the speakers are silent.
"""
import json
import math
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

NULL = "swdial_quiet"


def _pactl(*a, timeout=3):
    return subprocess.run(["pactl", *a], capture_output=True, text=True, timeout=timeout)


def sink_input(pid):
    """(index, sink id) of the playback stream owned by pid."""
    try:
        for si in json.loads(_pactl("-f", "json", "list", "sink-inputs").stdout or "[]"):
            if str(si.get("properties", {}).get("application.process.id")) == str(pid):
                return si.get("index"), si.get("sink")
    except Exception:
        pass
    return None, None


class Quiet:
    def __init__(self):
        self.module = None
        self.orig_sink = None
        self.active = False

    def on(self, pid) -> bool:
        idx, sink = sink_input(pid)
        if idx is None:
            return False
        if self.module is None:
            r = _pactl("load-module", "module-null-sink", f"sink_name={NULL}",
                       "sink_properties=device.description=ShortwaveDial-quiet")
            if r.returncode != 0:
                return False
            self.module = r.stdout.strip()
        if not self.active:
            self.orig_sink = sink
        _pactl("move-sink-input", str(idx), NULL)
        self.active = True
        return True

    def off(self, pid=None):
        if pid is not None and self.active:
            idx, _ = sink_input(pid)
            if idx is not None:
                target = str(self.orig_sink) if self.orig_sink is not None else "@DEFAULT_SINK@"
                if _pactl("move-sink-input", str(idx), target).returncode != 0:
                    _pactl("move-sink-input", str(idx), "@DEFAULT_SINK@")
        self.active = False
        if self.module is not None:
            _pactl("unload-module", self.module)  # streams left on it fall back to default
            self.module = None


_CHIME = Path(tempfile.gettempdir()) / "swdial-chime.wav"


def chime():
    """Two soft rising notes, played in the background."""
    if not _CHIME.exists():
        sr = 24000
        frames = bytearray()
        for f, dur in ((880, 0.12), (1320, 0.18)):
            n = int(sr * dur)
            for i in range(n):
                env = min(1, i / 240) * min(1, (n - i) / 1200)
                frames += struct.pack("<h", int(9000 * env * math.sin(2 * math.pi * f * i / sr)))
        with wave.open(str(_CHIME), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(bytes(frames))
    try:
        subprocess.Popen(["pw-play", str(_CHIME)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


_BLIP = Path(tempfile.gettempdir()) / "swdial-blip.wav"


def blip():
    """One short soft tick for a new callsign."""
    if not _BLIP.exists():
        sr, f, n = 24000, 1600, int(24000 * 0.06)
        frames = bytearray()
        for i in range(n):
            env = min(1, i / 120) * min(1, (n - i) / 600)
            frames += struct.pack("<h", int(6000 * env * math.sin(2 * math.pi * f * i / sr)))
        with wave.open(str(_BLIP), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(bytes(frames))
    try:
        subprocess.Popen(["pw-play", str(_BLIP)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass

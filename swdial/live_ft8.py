"""Live FT8: capture the kiwiclientd playback stream (only that stream, via
PipeWire's pulse layer `parec --monitor-stream`) one 15 s slot at a time and
decode it with our own decoder in swdial.ft8. No second Kiwi connection."""
import json
import subprocess
import threading
import time

import numpy as np

from .ft8 import decode_samples
from .ft8.unpack import HashTable

SR = 12000
REC_S = 14.6  # leave time to start the next slot on the boundary


def find_sink_input(pid: int):
    """PipeWire/pulse sink-input index belonging to process `pid`."""
    try:
        out = subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"],
                             capture_output=True, text=True, timeout=3).stdout
        for si in json.loads(out or "[]"):
            props = si.get("properties", {})
            if str(props.get("application.process.id")) == str(pid):
                return si.get("index")
    except Exception:
        pass
    return None


class LiveFT8:
    def __init__(self, get_pid, on_slot):
        self.get_pid, self.on_slot = get_pid, on_slot
        self.hashes = HashTable()
        self._stop = threading.Event()
        self.enabled = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _record_slot(self, idx):
        cmd = ["parec", f"--monitor-stream={idx}", f"--rate={SR}", "--channels=1",
               "--format=s16le", "--raw", "--latency-msec=50"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        need = int(REC_S * SR) * 2
        buf = bytearray()
        t_end = time.time() + REC_S + 1.0
        while len(buf) < need and time.time() < t_end and not self._stop.is_set():
            chunk = p.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
        p.kill()
        p.wait()
        return np.frombuffer(bytes(buf[:need]), dtype="<i2").astype(np.float64)

    def _loop(self):
        while not self._stop.is_set():
            now = time.time()
            wait = 15 - (now % 15)
            if self._stop.wait(wait):
                return
            slot_t = time.gmtime(round(time.time() / 15) * 15)
            pid = self.get_pid()
            idx = find_sink_input(pid) if (pid and self.enabled) else None
            if idx is None:
                continue
            x = self._record_slot(idx)
            if x.size < SR * 13 or not np.any(x):
                continue
            threading.Thread(target=self._decode, args=(x, slot_t), daemon=True).start()

    def _decode(self, x, slot_t):
        try:
            res = decode_samples(x, SR, hashes=self.hashes)
        except Exception as e:  # never kill the radio over a decode
            res = [{"error": str(e)}]
        self.on_slot(time.strftime("%H%M%S", slot_t), res)

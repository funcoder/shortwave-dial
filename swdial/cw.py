"""Our own Morse (CW) decoder, plus a live reader of the kiwiclientd stream.

Pipeline: find the CW tone (FFT peak 300-1200 Hz) -> Goertzel level every
5 ms -> adaptive on/off threshold with hysteresis -> mark/space lengths ->
dit length learned from the marks (gives WPM) -> letters and word gaps.
"""
import subprocess
import threading
import time

import numpy as np

SR = 12000
BLOCK = 60  # 5 ms at 12 kHz

MORSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F", "--.": "G", "....": "H",
    "..": "I", ".---": "J", "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O", ".--.": "P",
    "--.-": "Q", ".-.": "R", "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X",
    "-.--": "Y", "--..": "Z", "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9", ".-.-.-": ".", "--..--": ",",
    "..--..": "?", "-..-.": "/", "-...-": "=", ".-.-.": "+", "-....-": "-", ".--.-.": "@",
    "...-.-": "<SK>", "-.--.": "<KN>", ".-...": "<AS>", "...---...": "<SOS>",
}
TO_MORSE = {v: k for k, v in MORSE.items()}


class CWDecoder:
    def __init__(self, sr=SR):
        self.sr = sr
        self.tone = 600.0
        self._spec = np.zeros(0)
        self._hist = []          # recent levels for the threshold
        self.on = False
        self.run = 0             # blocks in the current state
        self.dit = 60.0 / (20 * 50) * 1000  # ms, start at 20 WPM
        self.sym = ""
        self.out_gap_done = True
        self.word_done = True

    @property
    def wpm(self):
        return round(1200 / self.dit)

    def _find_tone(self, x):
        n = len(x)
        spec = np.abs(np.fft.rfft(x * np.hanning(n)))
        f = np.fft.rfftfreq(n, 1 / self.sr)
        band = (f > 300) & (f < 1200)
        if band.any():
            k = np.argmax(spec[band])
            peak = spec[band][k]
            if peak > 6 * np.median(spec[band]):
                self.tone = float(f[band][k])

    def _level(self, blk):
        n = len(blk)
        w = 2 * np.pi * self.tone / self.sr
        t = np.arange(n)
        return float(np.hypot(np.dot(blk, np.cos(w * t)), np.dot(blk, np.sin(w * t))) / n)

    def feed(self, x: np.ndarray) -> str:
        """Feed audio (any length, float). Returns any newly decoded text."""
        out = []
        if len(x) >= self.sr // 2:
            self._find_tone(x[-self.sr:])
        for i in range(0, len(x) - BLOCK + 1, BLOCK):
            self._raw = (getattr(self, "_raw", []) + [self._level(x[i:i + BLOCK])])[-3:]
            lvl = sum(self._raw) / len(self._raw)  # 15 ms smoothing
            self._hist.append(lvl)
            if len(self._hist) > 800:  # 4 s
                self._hist = self._hist[-800:]
            h = np.asarray(self._hist)
            lo, hi = np.percentile(h, 20), np.percentile(h, 95)
            if hi < lo * 3:  # no clear keying: just noise
                state = False
            else:
                mid = lo + (hi - lo) * (0.45 if self.on else 0.55)  # hysteresis
                state = lvl > mid
            if state == self.on:
                self._flip = 0
                self.run += 1
                out.append(self._idle())
                continue
            self._flip = getattr(self, "_flip", 0) + 1
            if self._flip < 2:  # must hold 10 ms before we believe it
                self.run += 1
                continue
            ms = (self.run - 1) * BLOCK * 1000 / self.sr
            if self.on:
                self._mark(ms)
            self.on, self.run, self._flip = state, 2, 0
        return "".join(out)

    def _mark(self, ms):
        if ms < 0.3 * self.dit or ms < 12:  # click / noise
            return
        if ms < 2 * self.dit:
            self.sym += "."
            self.dit = 0.8 * self.dit + 0.2 * ms
        else:
            self.sym += "-"
            self.dit = 0.8 * self.dit + 0.2 * (ms / 3)
        self.dit = min(240.0, max(24.0, self.dit))  # 5 - 50 WPM
        self.out_gap_done = self.word_done = False

    def _idle(self):
        if self.on:
            return ""
        ms = self.run * BLOCK * 1000 / self.sr
        s = ""
        if not self.out_gap_done and ms > 2.5 * self.dit:
            s = MORSE.get(self.sym, "▫") if self.sym else ""
            self.sym = ""
            self.out_gap_done = True
        if not self.word_done and ms > 6 * self.dit:
            s += " "
            self.word_done = True
        return s


def synth(text, wpm=20, tone=600, sr=SR, snr_db=10, seed=1):
    """Make test Morse audio."""
    dit = 1.2 / wpm
    seq = []
    for wi, word in enumerate(text.upper().split()):
        if wi:
            seq.append((0, 7))
        for ci, ch in enumerate(word):
            if ci:
                seq.append((0, 3))
            for si, s in enumerate(TO_MORSE[ch]):
                if si:
                    seq.append((0, 1))
                seq.append((1, 1 if s == "." else 3))
    seq = [(0, 10)] + seq + [(0, 14)]
    env = np.concatenate([np.full(int(n * dit * sr), on, float) for on, n in seq])
    env = np.convolve(env, np.ones(48) / 48, "same")  # soft keying
    t = np.arange(len(env)) / sr
    sig = env * np.sin(2 * np.pi * tone * t)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, np.sqrt(0.5 / 10 ** (snr_db / 10)) * np.sqrt(sr / 500), len(sig))
    return sig + noise


class LiveCW:
    """Stream the kiwiclientd audio while in CW mode and decode it."""

    def __init__(self, get_pid, on_text):
        self.get_pid, self.on_text = get_pid, on_text
        self.dec = CWDecoder()
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        from .audio import sink_input
        while not self._stop.is_set():
            pid = self.get_pid()
            idx = sink_input(pid)[0] if pid else None
            if idx is None:
                self._stop.wait(1)
                continue
            p = subprocess.Popen(["parec", f"--monitor-stream={idx}", f"--rate={SR}", "--channels=1",
                                  "--format=s16le", "--raw", "--latency-msec=50"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            buf = np.zeros(0)
            try:
                while not self._stop.is_set() and self.get_pid() == pid:
                    chunk = p.stdout.read(2400)  # 0.1 s
                    if not chunk:
                        break
                    x = np.frombuffer(chunk, dtype="<i2").astype(np.float64)
                    buf = np.concatenate([buf, x])[-SR:]
                    self.dec._find_tone(buf) if len(buf) >= SR // 2 and int(time.time() * 10) % 10 == 0 else None
                    txt = self.dec.feed(x)
                    if txt:
                        self.on_text(txt, self.dec.wpm, self.dec.tone)
            finally:
                p.kill()
                p.wait()


if __name__ == "__main__":
    for wpm, snr in ((20, 40), (15, 10), (20, 6), (28, 3)):
        x = synth("CQ CQ DE G7MJF G7MJF K", wpm=wpm, snr_db=snr)
        d = CWDecoder()
        text = "".join(d.feed(x[i:i + 1200]) for i in range(0, len(x), 1200))
        print(f"{wpm} WPM, {snr} dB -> {text.strip()!r}  (learned {d.wpm} WPM, tone {d.tone:.0f} Hz)")

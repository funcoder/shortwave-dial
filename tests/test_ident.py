"""Signal identifier on synthetic signals built to each mode's spec."""
import numpy as np
import pytest

from swdial import cw
from swdial.ident import SR, identify

T = 8.0
t = np.arange(int(T * SR)) / SR
rng = np.random.default_rng(7)


def noise(level=0.05):
    return rng.normal(0, level, len(t))


def fsk(freqs_per_symbol, baud):
    """Continuous-phase FSK: one frequency per symbol."""
    n = int(SR / baud)
    f = np.repeat(freqs_per_symbol, n)[:len(t)]
    f = np.pad(f, (0, len(t) - len(f)), mode="edge")
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def rtty(shift=170):
    bits = rng.integers(0, 2, int(T * 45.45) + 2)
    return fsk(np.where(bits, 1500 + shift, 1500), 45.45) + noise()


def carrier():
    return np.sin(2 * np.pi * 1000 * t) + noise()


def sstv():
    """Martin M1-ish: 1200 Hz sync 4.9 ms, then picture tones, line 0.446 s."""
    line = 0.446
    f = np.empty(len(t))
    pos = (t % line)
    img = 1500 + 800 * (0.5 + 0.5 * np.sin(2 * np.pi * pos / line * 3 + np.floor(t / line) * 0.1))
    f[:] = img
    f[pos < 0.0049] = 1200
    f[(pos >= 0.0049) & (pos < 0.0057)] = 1500
    return np.sin(2 * np.pi * np.cumsum(f) / SR) + noise()


def wefax():
    """120 lines a minute: mostly white (2300) with the same black marks each line."""
    line = 0.5
    pos = t % line
    black = ((pos > 0.10) & (pos < 0.13)) | ((pos > 0.31) & (pos < 0.33)) | (pos < 0.025)
    f = np.where(black, 1500, 2300).astype(float)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) + noise()


def speech():
    """Voiced syllables (pitch harmonics, formant-ish rolloff) with pauses."""
    x = np.zeros(len(t))
    env = np.zeros(len(t))
    s = 0.2
    while s < T - 0.4:
        dur = rng.uniform(0.12, 0.35)
        env[int(s * SR):int((s + dur) * SR)] = np.hanning(int((s + dur) * SR) - int(s * SR))
        s += dur + rng.uniform(0.05, 0.4)
    pitch = 140 + 20 * np.sin(2 * np.pi * 0.7 * t)
    ph = 2 * np.pi * np.cumsum(pitch) / SR
    for k in range(2, 20):
        x += np.sin(k * ph) / k
    return x * env + noise(0.01)


def olivia():
    """Olivia 8/500: 8 tones 62.5 Hz apart, 62.5 baud? (8/500 is 31.25 baud)."""
    tones = rng.integers(0, 8, int(T * 31.25) + 2)
    return fsk(1250 + 62.5 * tones + 31.25, 31.25) + noise()


def ofdm():
    """Many steady carriers with random phase steps (PSK/OFDM data), 1000-2400 Hz."""
    x = np.zeros(len(t))
    sym = int(SR / 50)
    for fc in np.arange(1000, 2400, 50):
        ph = np.repeat(rng.integers(0, 4, len(t) // sym + 1) * np.pi / 2, sym)[:len(t)]
        x += np.sin(2 * np.pi * fc * t + ph)
    return x / 10 + noise(0.02)


def ft8_band():
    from swdial.ft8.encode import synth
    x = noise(0.02)
    for i, f0 in enumerate((600, 1100, 1700, 2200)):
        tones = rng.integers(0, 8, 79)
        y = synth(tones, f0, sr=SR)[:len(t)]
        x[:len(y)] += y * (0.5 + 0.1 * i)
    return x


def morse():
    return cw.synth("CQ CQ DE G7MJF G7MJF K", wpm=20, tone=700, snr_db=20)[:len(t)]


@pytest.mark.parametrize("make,mode,on_ft8,want", [
    (lambda: noise(), "usb", False, "Just band noise"),
    (morse, "cw", False, "Morse code (CW)"),
    (carrier, "usb", False, "Steady carrier"),
    (rtty, "usb", False, "RTTY (radio teletype)"),
    (lambda: rtty(850), "usb", False, "RTTY / FSK data"),
    (sstv, "usb", False, "Picture (SSTV)"),
    (wefax, "usb", False, "Weather fax"),
    (speech, "lsb", False, "Speech (SSB voice)"),
    (olivia, "usb", False, "Multi-tone data (MFSK)"),
    (ofdm, "usb", False, "Data or digital voice"),
    (ft8_band, "usb", True, "FT8"),
])
def test_identifies(make, mode, on_ft8, want):
    r = identify(make(), mode, on_ft8)
    assert r["name"] == want, r


def test_silence():
    assert identify(np.zeros(SR * 8))["name"] == "No audio"

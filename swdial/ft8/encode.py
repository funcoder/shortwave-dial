"""FT8 transmitter side: message bits -> 79 tones -> audio. Used for tests."""
import numpy as np

from . import crc, ldpc
from .constants import COSTAS, GRAY_MAP, SAMPLE_RATE, SYMBOL_SAMPLES, TONE_SPACING

DATA_POS = np.array([i for i in range(79) if not (i < 7 or 36 <= i < 43 or i >= 72)])


def bits_to_tones(msg77) -> np.ndarray:
    cw = ldpc.encode(crc.add_crc(msg77))
    tones = np.zeros(79, np.int64)
    for p in (0, 36, 72):
        tones[p:p + 7] = COSTAS
    sym = cw.reshape(58, 3)
    vals = sym[:, 0] * 4 + sym[:, 1] * 2 + sym[:, 2]
    tones[DATA_POS] = np.array(GRAY_MAP)[vals]
    return tones


def _gfsk_pulse(bt: float, t: np.ndarray) -> np.ndarray:
    from math import erf, pi, sqrt
    c = pi * sqrt(2 / np.log(2)) * bt
    return np.array([0.5 * (erf(c * (x + 0.5)) - erf(c * (x - 0.5))) for x in t])


def synth(tones, f0: float, sr: int = SAMPLE_RATE, amp: float = 1.0, gfsk: bool = True,
          phase0: float = 0.0) -> np.ndarray:
    """Continuous-phase (G)FSK waveform for the tone sequence at base freq f0."""
    nsps = int(round(SYMBOL_SAMPLES * sr / SAMPLE_RATE))
    tones = np.asarray(tones, float)
    if gfsk:
        # Gaussian-smoothed frequency (BT=2), as FT8 transmits
        pulse = _gfsk_pulse(2.0, (np.arange(3 * nsps) - 1.5 * nsps) / nsps)
        dphi = np.zeros((len(tones) + 2) * nsps)
        for j, t in enumerate(tones):
            dphi[j * nsps:j * nsps + 3 * nsps] += t * pulse
        # extend first/last tone into the guard symbols
        dphi[:2 * nsps] += tones[0] * pulse[nsps:]
        dphi[len(tones) * nsps:] += tones[-1] * pulse[:2 * nsps]
        freq = f0 + TONE_SPACING * dphi[nsps:nsps + len(tones) * nsps]
    else:
        freq = f0 + TONE_SPACING * np.repeat(tones, nsps)
    phase = phase0 + 2 * np.pi * np.cumsum(freq) / sr
    x = amp * np.sin(phase)
    ramp = nsps // 8
    w = 0.5 - 0.5 * np.cos(np.pi * np.arange(ramp) / ramp)
    x[:ramp] *= w
    x[-ramp:] *= w[::-1]
    return x

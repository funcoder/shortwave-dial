"""Coarse Costas sync on a waterfall, then fine time/frequency sync on a
200 Hz complex baseband made by slicing one big FFT of the slot."""
import numpy as np

from .constants import COSTAS, COSTAS_POS, SAMPLE_RATE

PRE_S = 1.5                         # buffer starts 1.5 s before the slot boundary
NBUF = 210000                       # 17.5 s at 12 kHz
HOP = 960                           # half a symbol
NFFT_WF = 3840                      # 3.125 Hz bins (2x freq oversampling)
BIN_HZ = SAMPLE_RATE / NFFT_WF
DS_RATE = 200                       # baseband sample rate
NDS = NBUF * DS_RATE // SAMPLE_RATE  # 3500
SPS_DS = 32                         # samples per symbol at 200 Hz
DF_BIG = SAMPLE_RATE / NBUF         # big-FFT bin spacing

_COSTAS_SYMS = [(p + k, COSTAS[k]) for p in COSTAS_POS for k in range(7)]


def waterfall(buf: np.ndarray) -> np.ndarray:
    nfr = (len(buf) - 1920) // HOP + 1
    idx = np.arange(nfr)[:, None] * HOP + np.arange(1920)[None, :]
    frames = buf[idx] * np.hanning(1920)[None, :]
    spec = np.fft.rfft(frames, NFFT_WF, axis=1)
    return (spec.real ** 2 + spec.imag ** 2).astype(np.float32)


def coarse_candidates(P: np.ndarray, fmin=200.0, fmax=3000.0, tmin=-1.5, tmax=3.0,
                      max_cand=250, min_score=1.4):
    """Return list of (score, start_s_rel_buffer, f0_hz)."""
    t_lo = max(0, int(round((PRE_S + tmin) * SAMPLE_RATE / HOP)))
    t_hi = int(round((PRE_S + tmax) * SAMPLE_RATE / HOP))
    t_hi = min(t_hi, P.shape[0] - 1 - 2 * 78)
    b_lo = int(np.ceil(fmin / BIN_HZ))
    b_hi = int(np.floor((fmax - 7 * 6.25) / BIN_HZ))
    ts = np.arange(t_lo, t_hi + 1)
    bs = np.arange(b_lo, b_hi + 1)
    sig = np.zeros((ts.size, bs.size))
    tot = np.zeros_like(sig)
    blk = np.zeros((3,) + sig.shape)
    blk_tot = np.zeros_like(blk)
    for s, tone in _COSTAS_SYMS:
        rows = P[ts + 2 * s]
        all8 = sum(rows[:, bs + 2 * j] for j in range(8))
        on = rows[:, bs + 2 * tone]
        sig += on
        tot += all8
        blk[s // 36] += on
        blk_tot[s // 36] += all8
    eps = 1e-12
    score = sig / ((tot - sig) / 7 + eps)
    # also allow a signal whose first or last Costas block is cut off
    s12 = (blk[0] + blk[1]) / ((blk_tot[0] + blk_tot[1] - blk[0] - blk[1]) / 7 + eps)
    s23 = (blk[1] + blk[2]) / ((blk_tot[1] + blk_tot[2] - blk[1] - blk[2]) / 7 + eps)
    score = np.maximum(score, np.maximum(s12, s23))
    # normalise by a robust noise level so the threshold is stable
    score = score / np.median(score)
    # local-max suppression in a 5 (time) x 5 (freq) window
    pad = np.pad(score, 2, constant_values=0)
    is_max = np.ones_like(score, bool)
    for dt in range(-2, 3):
        for db in range(-2, 3):
            if dt or db:
                is_max &= score >= pad[2 + dt:2 + dt + score.shape[0], 2 + db:2 + db + score.shape[1]]
    ii, jj = np.nonzero(is_max & (score > min_score))
    order = np.argsort(-score[ii, jj])[:max_cand]
    return [(float(score[ii[k], jj[k]]), ts[ii[k]] * HOP / SAMPLE_RATE, bs[jj[k]] * BIN_HZ)
            for k in order]


class Baseband:
    """One big FFT of the slot buffer; cheap per-candidate downconversion."""

    def __init__(self, buf: np.ndarray):
        self.X = np.fft.rfft(buf)
        self.idx = np.round(np.fft.fftfreq(NDS) * NDS).astype(np.int64)
        f = self.idx * DF_BIG
        taper = np.ones(NDS)
        lo, hi, w = -8.0, 52.0, 6.0
        taper[f < lo - w] = 0
        taper[f > hi + w] = 0
        m = (f >= lo - w) & (f < lo)
        taper[m] = 0.5 - 0.5 * np.cos(np.pi * (f[m] - (lo - w)) / w)
        m = (f > hi) & (f <= hi + w)
        taper[m] = 0.5 + 0.5 * np.cos(np.pi * (f[m] - hi) / w)
        self.taper = taper

    def downconvert(self, f0: float):
        """Complex 200 Hz baseband with tone 0 near 0 Hz. Returns (z, residual_hz)."""
        k0 = int(round(f0 / DF_BIG))
        k = k0 + self.idx
        ok = (k >= 0) & (k < self.X.size)
        Y = np.zeros(NDS, complex)
        Y[ok] = self.X[k[ok]] * self.taper[ok]
        z = np.fft.ifft(Y) * (NDS / NBUF)
        return z, f0 - k0 * DF_BIG


_N = np.arange(NDS)
_TONE_REF = np.exp(-2j * np.pi * np.outer(np.arange(8), np.arange(SPS_DS)) / SPS_DS)
_CS_POS = np.array([s for s, _ in _COSTAS_SYMS])
_CS_TONE = np.array([t for _, t in _COSTAS_SYMS])


def symbol_spectra(z: np.ndarray, i0: int) -> np.ndarray:
    """(79, 8) complex tone amplitudes for a signal starting at sample i0."""
    seg = np.zeros(79 * SPS_DS, complex)
    a, b = max(i0, 0), min(i0 + 79 * SPS_DS, z.size)
    if b > a:
        seg[a - i0:b - i0] = z[a:b]
    return seg.reshape(79, SPS_DS) @ _TONE_REF.T


def fine_sync(z: np.ndarray, start_s: float, resid_hz: float,
              df_range=1.5, df_step=0.5, dt_samples=9):
    """Search freq offset and start sample. Returns (best_df_hz, i0, metric)."""
    dfs = np.arange(-df_range, df_range + 1e-9, df_step)
    i0c = int(round(start_s * DS_RATE))
    i0s = i0c + np.arange(-dt_samples, dt_samples + 1)
    # rotations for each df: (ndf, NDS)
    rot = np.exp(-2j * np.pi * np.outer(dfs + resid_hz, _N) / DS_RATE)
    zz = z[None, :] * rot
    pos = i0s[:, None, None] + (_CS_POS * SPS_DS)[None, :, None] + np.arange(SPS_DS)[None, None, :]
    valid = (pos >= 0) & (pos < NDS)
    posc = np.clip(pos, 0, NDS - 1)
    blocks = zz[:, posc] * valid[None]                          # (ndf, ni0, 21, 32)
    c = blocks @ _TONE_REF.T                                    # (ndf, ni0, 21, 8)
    pw = c.real ** 2 + c.imag ** 2
    on = np.take_along_axis(pw, _CS_TONE[None, None, :, None], axis=3)[..., 0].sum(-1)
    tot = pw.sum(-1).sum(-1)
    metric = on / ((tot - on) / 7 + 1e-20)
    k = np.unravel_index(np.argmax(metric), metric.shape)
    return float(dfs[k[0]] + resid_hz), int(i0s[k[1]]), float(metric[k])

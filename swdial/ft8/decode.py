"""FT8 receiver: slot buffer -> candidates -> soft bits -> LDPC -> CRC -> text."""
import wave

import numpy as np

from . import crc, ldpc
from .constants import GRAY_MAP, SAMPLE_RATE, SYMBOL_SAMPLES, TONE_SPACING
from .encode import DATA_POS, bits_to_tones
from .sync import (DS_RATE, NBUF, PRE_S, SPS_DS, Baseband, coarse_candidates,
                   fine_sync, symbol_spectra, waterfall)
from .unpack import GRID_RE, HashTable, unpack77

SLOT_S = 15.0
_INV_GRAY = np.argsort(GRAY_MAP)           # tone -> 3-bit value
_BIT_OF_TONE = np.array([[(_INV_GRAY[t] >> (2 - i)) & 1 for t in range(8)] for i in range(3)], bool)


def _llr_from_mag(S: np.ndarray) -> np.ndarray:
    """S: (58, 8) per-tone metric. Returns 174 LLRs (positive = bit 1)."""
    out = np.empty((58, 3))
    for i in range(3):
        m1 = np.where(_BIT_OF_TONE[i][None, :], S, -np.inf).max(1)
        m0 = np.where(~_BIT_OF_TONE[i][None, :], S, -np.inf).max(1)
        out[:, i] = m1 - m0
    llr = out.reshape(-1)
    sd = llr.std()
    return llr / (sd if sd > 0 else 1.0)


def _group_tables(n):
    seqs = np.array(np.meshgrid(*([np.arange(8)] * n), indexing="ij")).reshape(n, -1).T
    bits = np.concatenate([_BIT_OF_TONE[:, seqs[:, j]].T for j in range(n)], axis=1)
    return seqs, bits


_GROUPS = {n: _group_tables(n) for n in (1, 2, 3)}


def _llr_nsym(C: np.ndarray, nsym: int) -> np.ndarray:
    """Coherent multi-symbol soft bits (groups of nsym data symbols)."""
    D = C[DATA_POS].reshape(2, 29, 8)
    out = np.empty((2, 29 * 3))
    k = 0
    while k < 29:
        n = min(nsym, 29 - k)
        ng = (29 - k) // n
        seqs, bits = _GROUPS[n]
        rows = D[:, k:k + ng * n].reshape(2, ng, n, 8)
        amp = np.abs(sum(rows[:, :, j, seqs[:, j]] for j in range(n)))   # (2, ng, 8^n)
        res = np.empty((2, ng, 3 * n))
        for bb in range(3 * n):
            m = bits[:, bb]
            res[:, :, bb] = amp[:, :, m].max(-1) - amp[:, :, ~m].max(-1)
        out[:, k * 3:(k + ng * n) * 3] = res.reshape(2, -1)
        k += ng * n
    out = out.reshape(-1)
    sd = out.std()
    return out / (sd if sd > 0 else 1.0)


def _subtract(buf: np.ndarray, tones, f0: float, start_sample: float):
    """Remove a decoded signal from buf (12 kHz real) in place.

    Builds a continuous-phase reference with smoothed tone transitions, then
    estimates its slowly-varying complex amplitude (fading, phase drift) by
    block averaging and removes it.
    """
    nsps = SYMBOL_SAMPLES
    n = 79 * nsps
    s0 = int(round(start_sample))
    f = f0 + TONE_SPACING * np.asarray(tones, float)
    edge = nsps / 8
    kx = np.ravel(np.column_stack([np.arange(79) * nsps + edge, (np.arange(79) + 1) * nsps - edge]))
    freq = np.interp(np.arange(n), kx, np.repeat(f, 2))
    ref = np.exp(1j * 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE)
    a, b = max(0, s0), min(len(buf), s0 + n)
    if b - a < nsps:
        return
    r = ref[a - s0:b - s0]
    seg = buf[a:b]
    blk = 240
    nb = (b - a) // blk
    prod = (seg[:nb * blk] * np.conj(r[:nb * blk])).reshape(nb, blk).mean(1)
    w = np.hanning(18)[1:-1]
    w /= w.sum()
    num = np.convolve(prod, w, mode="same")
    den = np.convolve(np.ones(nb), w, mode="same")
    amp_b = 2 * num / den
    centers = (np.arange(nb) + 0.5) * blk
    t = np.arange(b - a)
    amp = np.interp(t, centers, amp_b.real) + 1j * np.interp(t, centers, amp_b.imag)
    buf[a:b] = seg - np.real(amp * r)


def _decode_pass(buf, hashes, seen, results, fmin, fmax, max_cand):
    P = waterfall(buf)
    cands = coarse_candidates(P, fmin=fmin, fmax=fmax, max_cand=max_cand)
    if not cands:
        return []
    bb = Baseband(buf)
    infos, llrs = [], []
    for score, start_s, f0 in cands:
        z, resid = bb.downconvert(f0)
        df, i0, metric = fine_sync(z, start_s, resid)
        # second, finer pass
        df2, i02, metric2 = fine_sync(z, i0 / DS_RATE, df, df_range=0.25, df_step=0.125, dt_samples=2)
        C = symbol_spectra(z * np.exp(-2j * np.pi * df2 * np.arange(z.size) / DS_RATE), i02)
        A = np.abs(C)
        freq = f0 - resid + df2
        info = dict(f0=freq, i0=i02, C=C)
        for variant in (
            lambda: _llr_from_mag(A[DATA_POS]),
            lambda: _llr_nsym(C, 2),
            lambda: _llr_nsym(C, 3),
        ):
            infos.append(info)
            llrs.append(2.83 * variant())
    bits, nerr = ldpc.bp_decode(np.array(llrs), max_iter=30)
    new = []
    for info, b, e in zip(infos, bits, nerr):
        if e != 0 or not b[:77].any() or not crc.check_crc(b[:91]):
            continue
        u = unpack77(b[:77], hashes)
        msg = u["msg"]
        if msg in seen:
            continue
        seen.add(msg)
        tones = bits_to_tones(b[:77])
        C = info["C"]
        pw = np.abs(C) ** 2
        sig = pw[np.arange(79), tones].mean()
        mask = np.ones_like(pw, bool)
        mask[np.arange(79), tones] = False
        noise = np.median(pw[mask])
        snr = 10 * np.log10(max(sig / max(noise, 1e-20) - 1, 1e-3)) - 26.0
        start_s = info["i0"] / DS_RATE
        call1, call2, grid = u["call1"], u["call2"], u["grid"]
        is_cq = call1.startswith("CQ")
        call = call2 if is_cq else ""
        r = dict(freq_hz=round(info["f0"], 1), dt=round(start_s - PRE_S - 0.5, 2),
                 snr=int(round(max(snr, -30))), msg=msg, is_cq=is_cq,
                 call=call, grid=grid if GRID_RE.match(grid or "") else "",
                 call1=call1, call2=call2, bits=b[:77].copy(), tones=tones)
        new.append(r)
        results.append(r)
        _subtract(buf, tones, info["f0"], start_s * SAMPLE_RATE)
    return new


def decode_samples(x: np.ndarray, sr: int = SAMPLE_RATE, fmin: float = 200.0,
                   fmax: float = 3000.0, passes: int = 3, max_cand: int = 250,
                   hashes: HashTable = None) -> list:
    """Decode one FT8 slot. Sample 0 of x is taken as the 15 s slot boundary.

    Returns a list of dicts: freq_hz (audio offset of tone 0), dt, snr, msg,
    is_cq, call (the CQing station, if a CQ), grid.
    """
    x = np.asarray(x, np.float64)
    if x.ndim > 1:
        x = x.mean(1)
    if abs(sr - SAMPLE_RATE) > 2:       # 11999 etc. are treated as 12000
        n = int(round(len(x) * SAMPLE_RATE / sr))
        x = np.interp(np.arange(n) * sr / SAMPLE_RATE, np.arange(len(x)), x)
    pre = int(PRE_S * SAMPLE_RATE)
    buf = np.zeros(NBUF)
    seg = x[:NBUF - pre]
    buf[pre:pre + seg.size] = seg
    rms = np.sqrt(np.mean(seg ** 2)) if seg.size else 0
    if rms > 0:
        buf /= rms
    hashes = hashes if hashes is not None else HashTable()
    results, seen = [], set()
    for _ in range(passes):
        if not _decode_pass(buf, hashes, seen, results, fmin, fmax, max_cand):
            break
    for r in results:
        r.pop("bits", None)
        r.pop("tones", None)
    results.sort(key=lambda r: r["freq_hz"])
    return results


def read_wav(path: str):
    with wave.open(path, "rb") as w:
        sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    if sw == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float64) / 32768.0
    elif sw == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float64) - 128) / 128.0
    elif sw == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float64) / 2 ** 31
    else:
        raise ValueError(f"unsupported sample width {sw}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    return x, sr


def decode_wav(path: str, start_offset_s: float = 0.0, **kw) -> list:
    """Decode every 15 s slot in a WAV.

    start_offset_s: how far past a slot boundary the first sample is (0..15).
    Each result also gets 'slot' (seconds from the first slot boundary).
    """
    x, sr = read_wav(path)
    if abs(sr - SAMPLE_RATE) <= 2:
        sr = SAMPLE_RATE
    else:
        n = int(round(len(x) * SAMPLE_RATE / sr))
        x = np.interp(np.arange(n) * sr / SAMPLE_RATE, np.arange(len(x)), x)
        sr = SAMPLE_RATE
    out = []
    hashes = HashTable()
    off = int(round(start_offset_s * sr))
    k = 0
    while True:
        b = int(k * SLOT_S * sr) - off            # slot boundary in file samples
        if b + int(10 * sr) > len(x):             # need most of the slot
            break
        if b < 0:
            seg = np.concatenate([np.zeros(-b), x[:max(0, b + NBUF)]])
        else:
            seg = x[b:]
        for r in decode_samples(seg, sr, hashes=hashes, **kw):
            r["slot"] = k * SLOT_S
            out.append(r)
        k += 1
    return out

"""What is this signal? Listen to a few seconds of the receiver audio and name the
kind of transmission from how it looks: how wide it is, how many tones, whether
it keys on and off, whether it repeats line by line, whether it pauses like speech.

A best guess from simple measurements, not a decoder. Answers are plain English.
"""
import subprocess
import threading

import numpy as np

SR = 12000
SECONDS = 8


def _stft(x, n, hop, window=np.hanning):
    """Power spectrogram (frames x bins) and bin frequencies."""
    w = window(n)
    count = (len(x) - n) // hop + 1
    idx = np.arange(n)[None, :] + hop * np.arange(count)[:, None]
    return np.abs(np.fft.rfft(x[idx] * w, axis=1)) ** 2, np.fft.rfftfreq(n, 1 / SR)


def _regions(snr_db, f, thresh=6.0, gap_hz=30.0):
    """Contiguous runs of bins above thresh (small gaps merged) -> [(lo_i, hi_i)]."""
    on = np.flatnonzero(snr_db > thresh)
    if not len(on):
        return []
    step = f[1] - f[0]
    out, lo, prev = [], on[0], on[0]
    for i in on[1:]:
        if (i - prev) * step > gap_hz:
            out.append((lo, prev))
            lo = i
        prev = i
    out.append((lo, prev))
    return out


def _autocorr_peak(s, lo, hi):
    """(lag, strength) of the best repeat in s between lags lo..hi frames."""
    k = max(3, int(hi * 1.5))  # take away slow changes (a signal starting or stopping)
    s = s - np.convolve(s, np.ones(k) / k, "same")
    if not s.any() or hi <= lo:
        return 0, 0.0
    ac = np.correlate(s, s, "full")[len(s) - 1:]
    ac = ac / ac[0]
    hi = min(hi, len(ac) - 1)
    lag = lo + int(np.argmax(ac[lo:hi]))
    return lag, float(ac[lag])


def _db(p):
    return 10 * np.log10(np.maximum(p, 1e-12))


def _answer(name, why, sure=True, centre=0.0, width=0.0):
    return {"name": name, "why": why, "sure": sure, "centre": round(centre), "width": round(width)}


def identify(x, mode="usb", on_ft8=False):
    """Best guess at what x (mono audio at SR) is. Returns {name, why, sure, centre, width}."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < SR * 2 or not np.any(x):
        return _answer("No audio", "Nothing came through from the receiver.", True)
    x = x[SR // 4:]  # the first moment of a recording is often silent or a click
    x = x / (np.abs(x).max() + 1e-9)

    # Fine view (6 Hz bins) for shape; fast view (5 ms steps) for keying and rhythm.
    P, f = _stft(x, 2048, 240, np.blackman)
    top = 4900 if mode == "am" else 3400
    band = (f >= 150) & (f <= top)
    fb, Pb = f[band], P[:, band]
    avg = Pb.mean(0)
    floor = np.percentile(avg, 20)
    snr = _db(np.convolve(avg, np.ones(3) / 3, "same") / floor)
    regs = _regions(snr, fb)
    if not regs or snr.max() < 8:
        return _answer("Just band noise", "No signal stands out above the background hiss.")

    def info(r):
        lo, hi = r
        sl = slice(lo, hi + 1)
        pk = lo + int(np.argmax(avg[sl]))
        inner = np.flatnonzero(snr[sl] > snr[pk] - 20) + lo  # -20 dB width: ignores weak skirts
        c = np.cumsum(np.maximum(avg[sl] - floor, 0))
        c = c / (c[-1] + 1e-12)
        occ = fb[lo + np.searchsorted(c, 0.98)] - fb[lo + np.searchsorted(c, 0.02)]  # 96% of the power
        return {"lo": fb[lo], "hi": fb[hi], "power": avg[sl].sum(), "peak": fb[pk], "pk": pk,
                "width": fb[inner[-1]] - fb[inner[0]] + (fb[1] - fb[0]), "occ": occ, "sl": sl}

    regs = sorted((info(r) for r in regs), key=lambda r: -r["power"])
    main = regs[0]
    centre = (main["lo"] + main["hi"]) / 2
    span = main["hi"] - main["lo"]

    F, ff = _stft(x, 256, 60)  # 47 Hz bins, 5 ms steps
    dt = 60 / SR

    def tone_series(lo_hz, hi_hz):
        sel = (ff >= lo_hz) & (ff <= hi_hz)
        return F[:, sel].sum(1)

    # ---- pictures: SSTV (1200 Hz sync each line) and weather fax (line repeats, no sync) ----
    pic = tone_series(1450, 2350)
    allp = tone_series(300, 3000) + 1e-12
    if (pic / allp).mean() > 0.6 and span > 400:
        sync = tone_series(1150, 1250) / allp
        lag_s, strength_s = _autocorr_peak(sync, int(0.1 / dt), int(0.7 / dt))
        # sync is short and strong, and 1200 Hz is quiet between pulses
        if strength_s > 0.3 and np.percentile(sync, 99) > 0.1 and np.median(sync) < 0.03:
            return _answer("Picture (SSTV)", f"Image tones 1500–2300 Hz with a 1200 Hz sync pulse "
                           f"every {lag_s * dt:.2f} s (one picture line).", True, centre, span)
        track = (F[:, (ff >= 1450) & (ff <= 2350)] * ff[(ff >= 1450) & (ff <= 2350)]).sum(1) / (
            F[:, (ff >= 1450) & (ff <= 2350)].sum(1) + 1e-12)
        lag_f, strength_f = _autocorr_peak(track, int(0.2 / dt), int(0.6 / dt))
        if strength_f > 0.4:
            lpm = 60 / (lag_f * dt)
            return _answer("Weather fax", f"Black/white tones 1500–2300 Hz repeating every "
                           f"{lag_f * dt:.2f} s, about {lpm:.0f} lines a minute.", True, centre, span)

    # ---- two tones taking turns: RTTY / FSK ----
    pks = [i for i in np.argsort(avg)[::-1][:40] if snr[i] > 10]
    pair = None
    for i in pks:
        for j in pks:
            sep = abs(fb[j] - fb[i])
            if 140 <= sep <= 900 and abs(snr[i] - snr[j]) < 10:
                pair = (min(fb[i], fb[j]), max(fb[i], fb[j]))
                break
        if pair:
            break
    if pair:
        a, b = tone_series(pair[0] - 20, pair[0] + 20), tone_series(pair[1] - 20, pair[1] + 20)
        mid = (fb > pair[0] + 40) & (fb < pair[1] - 40)  # FSK: little in between (unlike fax / music)
        quiet_mid = avg[mid].mean() < 0.1 * min(avg[np.searchsorted(fb, p)] for p in pair)
        with np.errstate(invalid="ignore", divide="ignore"):
            turns = np.corrcoef(_db(a), _db(b))[0, 1]
        if turns < -0.3 and quiet_mid:
            shift = pair[1] - pair[0]
            kind = "RTTY (radio teletype)" if shift < 250 else "RTTY / FSK data"
            note = "the usual amateur 170 Hz shift" if shift < 250 else "a wide shift, as used by weather and news services"
            return _answer(kind, f"Two tones {shift:.0f} Hz apart taking turns: {note}.",
                           True, sum(pair) / 2, shift)

    # ---- several narrow signals side by side ----
    narrow = [r for r in regs if r["width"] < 120 and r["power"] > main["power"] * 0.05]
    if len(narrow) >= 3:
        if on_ft8:
            return _answer("FT8", f"{len(narrow)} narrow digital signals across the FT8 channel.",
                           True, centre, span)
        return _answer("Narrow digital signals", f"{len(narrow)} separate narrow signals side by side "
                       "(FT8, FT4, PSK31 or similar).", False, centre, span)

    # ---- one narrow signal: Morse, carrier or narrow data ----
    if main["width"] < 120:
        s = _db(tone_series(main["peak"] - 25, main["peak"] + 25))
        on = s > np.percentile(s, 95) - 8
        flips = int(np.count_nonzero(np.diff(on.astype(int))))
        if 0.1 < on.mean() < 0.9 and flips >= 8:
            return _answer("Morse code (CW)", f"One tone at {main['peak']:.0f} Hz switching on and off.",
                           True, main["peak"], main["width"])
        if on_ft8:
            return _answer("FT8", "A narrow digital signal on the FT8 channel.", True, centre, main["width"])
        if main["width"] <= 50:
            return _answer("Steady carrier", f"A single unchanging tone at {main['peak']:.0f} Hz "
                           "(a carrier, beacon or tuning signal).", True, main["peak"], main["width"])
        return _answer("Narrow data (PSK31 or similar)", f"One steady signal about {main['width']:.0f} Hz wide "
                       f"at {main['peak']:.0f} Hz.", False, centre, main["width"])

    # ---- wide signals: speech, broadcast, or data ----
    e = _db(tone_series(250, 3000 if mode != "am" else 4500))
    low = np.r_[0, (e < np.percentile(e, 95) - 15).astype(int), 0]
    starts, ends = np.flatnonzero(np.diff(low) == 1), np.flatnonzero(np.diff(low) == -1)
    pauses = int(np.count_nonzero(((ends - starts) * dt >= 0.05) & ((ends - starts) * dt <= 1.5)))
    smooth = np.convolve(e, np.ones(8) / 8, "same")
    trend = np.convolve(smooth, np.ones(200) / 200, "same")  # 1 s: a signal stopping isn't a syllable
    mid = slice(100, -100) if len(smooth) > 400 else slice(None)
    wobble = float(np.percentile((smooth - trend)[mid], 90) - np.percentile((smooth - trend)[mid], 10))
    if pauses >= 3 or wobble > 12:  # speech: many short pauses, rising and falling
        if mode == "am":
            return _answer("Broadcast speech", "Pauses and rises like someone talking.", True, centre, span)
        return _answer("Speech (SSB voice)", "Pauses and rises like someone talking.", True, centre, span)
    if mode == "am":
        if span > 6000:
            return _answer("Digital radio (DRM)", "A flat, hiss-like block filling the whole channel.",
                           False, centre, span)
        return _answer("Broadcast (music or talk)", "Continuous wide audio with no long pauses.", False, centre, span)

    sl, wide = main["sl"], main["occ"]
    frames = F[:, (ff >= main["lo"]) & (ff <= main["hi"])]  # fast view: one MFSK symbol per frame
    here = frames.sum(1)
    frames = frames[here > np.percentile(here, 90) / 4]  # only while the signal is there
    srt = np.sort(frames, axis=1)
    one_tone = float(np.median(srt[:, -2:].sum(1) / (frames.sum(1) + 1e-12)))  # power in the top tone
    hops = ff[(ff >= main["lo"]) & (ff <= main["hi"])][frames.argmax(1)]
    if one_tone > 0.4 and np.percentile(hops, 90) - np.percentile(hops, 10) > wide * 0.4:
        return _answer("Multi-tone data (MFSK)", f"One tone at a time hopping across {wide:.0f} Hz "
                       "(Olivia, Contestia, MFSK or similar).", False, centre, wide)
    if wide > 1800:
        return _answer("Wide data modem", f"A steady, flat block {wide:.0f} Hz wide "
                       "(VARA, PACTOR, a military STANAG modem or similar).", False, centre, wide)
    return _answer("Data or digital voice", f"A steady, flat block {wide:.0f} Hz wide "
                   "(FreeDV digital voice, a PSK/OFDM data modem or similar).", False, centre, wide)


def listen_and_identify(pid, mode="usb", on_ft8=False, seconds=SECONDS, stop=None):
    """Record `seconds` of the kiwiclientd stream only (no second Kiwi connection), then identify."""
    from .audio import sink_input
    idx = sink_input(pid)[0] if pid else None
    if idx is None:
        return _answer("No audio", "The receiver isn't playing yet.", True)
    p = subprocess.Popen(["parec", f"--monitor-stream={idx}", f"--rate={SR}", "--channels=1",
                          "--format=s16le", "--raw", "--latency-msec=50"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timer = threading.Timer(seconds + 3, p.kill)  # never hang the caller
    timer.start()
    need, buf = int(seconds * SR) * 2, bytearray()
    try:
        while len(buf) < need and not (stop and stop.is_set()):
            chunk = p.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
    finally:
        timer.cancel()
        p.kill()
        p.wait()
    return identify(np.frombuffer(bytes(buf[:need - need % 2]), dtype="<i2"), mode, on_ft8)


if __name__ == "__main__":
    import sys
    import wave
    for path in sys.argv[1:]:
        w = wave.open(path)
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float)
        if w.getnchannels() > 1:
            a = a.reshape(-1, w.getnchannels()).mean(1)
        if w.getframerate() != SR:
            a = np.interp(np.arange(0, len(a), w.getframerate() / SR), np.arange(len(a)), a)
        print(path, identify(a))

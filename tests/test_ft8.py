import os
import wave

import numpy as np
import pytest

from swdial.ft8 import crc, ldpc
from swdial.ft8.decode import decode_samples, decode_wav
from swdial.ft8.encode import bits_to_tones, synth
from swdial.ft8.unpack import HashTable, pack_standard, unpack77
from swdial.ft8.constants import COSTAS

SR = 12000
PROOF = os.path.join(os.path.dirname(__file__), "..", "proof", "ft8-slot-7074.wav")


def _slot_with(signals, seed=0, noise=1.0):
    """signals: list of ((call1, call2, extra), audio_hz, snr_db_2500, start_s)."""
    rng = np.random.default_rng(seed)
    x = np.zeros(15 * SR)
    for msg, f, snr, st in signals:
        s = synth(bits_to_tones(pack_standard(*msg)), f)
        # sine power A^2/2 vs noise power in 2500 Hz of a unit-variance 6 kHz-wide noise
        amp = np.sqrt(2 * 10 ** (snr / 10) * 2500 / (SR / 2)) * noise
        i = int(st * SR)
        if i < 0:                       # started before the slot boundary
            s, i = s[-i:], 0
        s = s[: x.size - i]
        x[i:i + s.size] += amp * s
    return x + noise * rng.standard_normal(x.size)


# ---------- CRC ----------

def test_crc_roundtrip_and_detects_every_single_bit_error():
    rng = np.random.default_rng(1)
    a = crc.add_crc(rng.integers(0, 2, 77))
    assert crc.check_crc(a)
    for i in range(91):
        b = a.copy()
        b[i] ^= 1
        assert not crc.check_crc(b)


def test_crc_of_zero_message_is_zero():
    assert crc.crc14(np.zeros(82, np.uint8)) == 0


# ---------- LDPC ----------

def test_generator_matches_parity_check_matrix():
    rng = np.random.default_rng(2)
    for _ in range(20):
        cw = ldpc.encode(crc.add_crc(rng.integers(0, 2, 77)))
        assert cw.size == 174
        assert ldpc.syndrome_ok(cw)
    assert set(ldpc.H.sum(0)) == {3}


def test_bp_corrects_noisy_codewords():
    rng = np.random.default_rng(3)
    cw = np.array([ldpc.encode(crc.add_crc(rng.integers(0, 2, 77))) for _ in range(16)])
    sigma = 0.6
    s = np.where(cw == 1, 1.0, -1.0)
    llr = 2 * (s + sigma * rng.standard_normal(s.shape)) / sigma ** 2
    assert ((llr > 0) != cw).sum() > 50          # there really were errors
    bits, nerr = ldpc.bp_decode(llr)
    assert (nerr == 0).all()
    assert np.array_equal(bits, cw)


# ---------- message pack / unpack ----------

@pytest.mark.parametrize("parts,text", [
    (("CQ", "G7MJF", "IO90"), "CQ G7MJF IO90"),
    (("CQ DX", "K1ABC", "FN42"), "CQ DX K1ABC FN42"),
    (("CQ 123", "9A1A", "JN75"), "CQ 123 9A1A JN75"),
    (("G7MJF", "DL1ABC", "R-12"), "G7MJF DL1ABC R-12"),
    (("EA3XYZ", "G7MJF", "RR73"), "EA3XYZ G7MJF RR73"),
    (("K1ABC", "G7MJF", "+05"), "K1ABC G7MJF +05"),
    (("W1AW/R", "G7MJF", "73"), "W1AW/R G7MJF 73"),
    (("G7MJF", "K1ABC", "R FN42"), "G7MJF K1ABC R FN42"),
])
def test_pack_unpack_roundtrip(parts, text):
    assert unpack77(pack_standard(*parts))["msg"] == text


def test_hashed_call_shows_brackets_and_resolves_when_known():
    bits = pack_standard("PJ4/K1ABC", "G7MJF", "-05")
    assert unpack77(bits)["msg"] == "<...> G7MJF -05"
    h = HashTable()
    h.add("PJ4/K1ABC")
    assert unpack77(bits, h)["msg"] == "<PJ4/K1ABC> G7MJF -05"


def test_tones_have_costas_arrays():
    t = bits_to_tones(pack_standard("CQ", "G7MJF", "IO90"))
    assert t.size == 79 and t.min() >= 0 and t.max() <= 7
    for p in (0, 36, 72):
        assert tuple(t[p:p + 7]) == COSTAS


# ---------- full round trip ----------

def test_synthetic_cq_g7mjf_decodes_exactly():
    x = _slot_with([(("CQ", "G7MJF", "IO90"), 1234.5, -14.0, 0.8)], seed=4)
    res = decode_samples(x, SR)
    assert len(res) == 1
    r = res[0]
    assert r["msg"] == "CQ G7MJF IO90"
    assert r["is_cq"] and r["call"] == "G7MJF" and r["grid"] == "IO90"
    assert abs(r["freq_hz"] - 1234.5) < 1.0
    assert abs(r["dt"] - 0.3) < 0.05
    assert -18 <= r["snr"] <= -10


def test_synthetic_busy_slot_with_overlap():
    sigs = [
        (("CQ", "G7MJF", "IO90"), 1000.0, -10.0, 0.5),
        (("K1ABC", "G7MJF", "-05"), 1020.0, -12.0, 0.9),    # overlaps the first
        (("CQ DX", "EA8ZZ", "IL18"), 2400.0, -16.0, 1.6),
        (("DL1ABC", "VK2XY", "RR73"), 400.0, 0.0, -0.3),
    ]
    res = decode_samples(_slot_with(sigs, seed=5), SR)
    msgs = {r["msg"] for r in res}
    assert msgs == {"CQ G7MJF IO90", "K1ABC G7MJF -05", "CQ DX EA8ZZ IL18", "DL1ABC VK2XY RR73"}


def test_pure_noise_gives_no_decodes():
    rng = np.random.default_rng(6)
    assert decode_samples(rng.standard_normal(15 * SR), SR) == []


def test_cli_on_synthetic_wav(tmp_path, capsys):
    from swdial.ft8.__main__ import main
    x = _slot_with([(("CQ", "G7MJF", "IO90"), 1500.0, -8.0, 0.5)], seed=7)
    x = np.concatenate([x, 0.01 * np.zeros(SR)])
    p = tmp_path / "t.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(11999)           # Kiwi-style header; treated as 12000
        w.writeframes((np.clip(x / 8, -1, 1) * 32767).astype("<i2").tobytes())
    assert main([str(p), "--start", "120000", "--base-hz", "7074000"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert out[0].startswith("120000")
    assert "CQ G7MJF IO90" in out[0] and "7075.500 kHz" in out[0]


@pytest.mark.skipif(not os.path.exists(PROOF), reason="no real recording")
def test_real_wessex_recording_decodes():
    res = decode_wav(PROOF)
    assert len(res) >= 20
    assert any(r["is_cq"] for r in res)

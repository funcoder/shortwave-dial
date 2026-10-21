"""CRC-14 used by FT8 (polynomial 0x2757, init 0, no reflection).

The CRC is computed over the 77 message bits followed by 5 zero bits (82 bits),
and appended to the message to make the 91-bit LDPC payload.
"""
import numpy as np

POLY = 0x2757


def crc14(bits) -> int:
    reg = 0
    for b in bits:
        top = ((reg >> 13) & 1) ^ int(b)
        reg = (reg << 1) & 0x3FFF
        if top:
            reg ^= POLY
    return reg


def add_crc(msg77) -> np.ndarray:
    msg77 = np.asarray(msg77, dtype=np.uint8)
    assert msg77.size == 77
    c = crc14(np.concatenate([msg77, np.zeros(5, np.uint8)]))
    crc_bits = np.array([(c >> (13 - i)) & 1 for i in range(14)], np.uint8)
    return np.concatenate([msg77, crc_bits])


def check_crc(a91) -> bool:
    a91 = np.asarray(a91, dtype=np.uint8)
    return bool(np.array_equal(add_crc(a91[:77])[77:], a91[77:91]))

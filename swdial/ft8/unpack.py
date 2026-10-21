"""77-bit FT8 message packing / unpacking (standard messages, free text, i3=4).

Written from the protocol description: type 1/2 = c28 r1 c28 r1 R1 g15 i3.
"""
import re

import numpy as np

NTOKENS = 2063592
MAX22 = 4194304
MAXGRID4 = 32400

A1 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
A2 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
A3 = "0123456789"
A4 = " ABCDEFGHIJKLMNOPQRSTUVWXYZ"
A38 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ/"
A42 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ+-./?"

GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}$")


def bits_to_int(bits) -> int:
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v


def int_to_bits(v: int, n: int) -> list:
    return [(v >> (n - 1 - i)) & 1 for i in range(n)]


def ihashcall(call: str, m: int) -> int:
    c = call.strip().upper().ljust(11)[:11]
    n8 = 0
    for ch in c:
        n8 = 38 * n8 + max(A38.find(ch), 0)
    return ((47055833459 * n8) & 0xFFFFFFFFFFFFFFFF) >> (64 - m)


class HashTable:
    """Remembers calls seen in decodes so hashed calls can be shown."""

    def __init__(self):
        self.h22, self.h12, self.h10 = {}, {}, {}

    def add(self, call: str):
        call = call.strip()
        if not call or call.startswith("<") or call in ("CQ", "DE", "QRZ"):
            return
        self.h22[ihashcall(call, 22)] = call
        self.h12[ihashcall(call, 12)] = call
        self.h10[ihashcall(call, 10)] = call

    def lookup(self, table: dict, h: int) -> str:
        c = table.get(h)
        return f"<{c}>" if c else "<...>"


# ---------- callsign c28 ----------

def _std_call_6(call: str):
    """Return the 6-char aligned form of a standard callsign, or None."""
    c = call.strip().upper()
    if len(c) >= 3 and c[2].isdigit():
        pass
    elif len(c) >= 2 and c[1].isdigit():
        c = " " + c
    else:
        return None
    c = c.ljust(6)
    if len(c) != 6:
        return None
    if (c[0] in A1 and c[1] in A2 and c[2] in A3 and all(x in A4 for x in c[3:])):
        return c
    return None


def pack28(call: str) -> int:
    c = call.strip().upper()
    if c == "DE":
        return 0
    if c == "QRZ":
        return 1
    if c == "CQ":
        return 2
    m = re.fullmatch(r"CQ[ _](\d{3})", c)
    if m:
        return 3 + int(m.group(1))
    m = re.fullmatch(r"CQ[ _]([A-Z]{1,4})", c)
    if m:
        s = m.group(1).rjust(4)
        n = 0
        for ch in s:
            n = 27 * n + A4.index(ch)
        return 1003 + n
    s = _std_call_6(c)
    if s is None:
        return NTOKENS + ihashcall(c, 22)
    n = A1.index(s[0])
    n = n * 36 + A2.index(s[1])
    n = n * 10 + A3.index(s[2])
    n = n * 27 + A4.index(s[3])
    n = n * 27 + A4.index(s[4])
    n = n * 27 + A4.index(s[5])
    return NTOKENS + MAX22 + n


def unpack28(n28: int, hashes: HashTable = None):
    """Returns (text, is_standard_call)."""
    if n28 < NTOKENS:
        if n28 == 0:
            return "DE", False
        if n28 == 1:
            return "QRZ", False
        if n28 == 2:
            return "CQ", False
        if n28 <= 1002:
            return f"CQ {n28 - 3:03d}", False
        if n28 <= 532443:
            m = n28 - 1003
            s = ""
            for _ in range(4):
                s = A4[m % 27] + s
                m //= 27
            return "CQ " + s.strip(), False
        return "<?tok>", False
    n = n28 - NTOKENS
    if n < MAX22:
        return (hashes.lookup(hashes.h22, n) if hashes else "<...>"), False
    n -= MAX22
    c6 = A4[n % 27]; n //= 27
    c5 = A4[n % 27]; n //= 27
    c4 = A4[n % 27]; n //= 27
    c3 = A3[n % 10]; n //= 10
    c2 = A2[n % 36]; n //= 36
    if n >= len(A1):
        return "<?call>", False
    c1 = A1[n]
    return (c1 + c2 + c3 + c4 + c5 + c6).strip(), True


# ---------- pack (type 1 standard message; used for tests / encoder) ----------

def pack_standard(call1: str, call2: str, extra: str = "") -> np.ndarray:
    """Pack 'CALL1 CALL2 GRID|REPORT|RRR|RR73|73' as an i3=1 message."""
    r1 = r2 = 0
    if call1.endswith("/R"):
        call1, r1 = call1[:-2], 1
    if call2.endswith("/R"):
        call2, r2 = call2[:-2], 1
    ir = 0
    e = extra.strip().upper()
    if e.startswith("R ") or (e.startswith("R") and len(e) > 1 and e[1] in "+-"):
        ir, e = 1, e[1:].strip()
    if GRID_RE.match(e):
        g15 = ((ord(e[0]) - 65) * 18 + (ord(e[1]) - 65)) * 100 + int(e[2]) * 10 + int(e[3])
    elif e == "":
        g15 = MAXGRID4 + 1
    elif e == "RRR":
        g15 = MAXGRID4 + 2
    elif e == "RR73":
        g15 = MAXGRID4 + 3
    elif e == "73":
        g15 = MAXGRID4 + 4
    else:
        snr = int(e)
        g15 = MAXGRID4 + 35 + snr
    bits = (int_to_bits(pack28(call1), 28) + [r1] + int_to_bits(pack28(call2), 28) + [r2]
            + [ir] + int_to_bits(g15, 15) + int_to_bits(1, 3))
    return np.array(bits, np.uint8)


# ---------- unpack ----------

def unpack77(bits, hashes: HashTable = None) -> dict:
    """Returns dict(msg, call1, call2, grid, i3, n3). call1/2/grid may be ''."""
    bits = [int(b) for b in bits]
    i3 = bits_to_int(bits[74:77])
    n3 = bits_to_int(bits[71:74])
    out = {"i3": i3, "n3": n3, "call1": "", "call2": "", "grid": "", "msg": ""}
    if i3 in (1, 2):
        n28a = bits_to_int(bits[0:28]); ra = bits[28]
        n28b = bits_to_int(bits[29:57]); rb = bits[57]
        ir = bits[58]
        g15 = bits_to_int(bits[59:74])
        c1, std1 = unpack28(n28a, hashes)
        c2, std2 = unpack28(n28b, hashes)
        suffix = "/R" if i3 == 1 else "/P"
        if ra and std1:
            c1 += suffix
        if rb and std2:
            c2 += suffix
        if g15 <= MAXGRID4:
            g = g15
            j1, g = divmod(g, 1800)
            j2, g = divmod(g, 100)
            j3, j4 = divmod(g, 10)
            grid = chr(65 + j1) + chr(65 + j2) + str(j3) + str(j4)
            out["grid"] = grid
            tail = ("R " + grid) if ir else grid
        else:
            irpt = g15 - MAXGRID4
            tail = {1: "", 2: "RRR", 3: "RR73", 4: "73"}.get(irpt)
            if tail is None:
                isnr = irpt - 35
                if isnr > 50:
                    isnr -= 101
                tail = ("R" if ir else "") + f"{isnr:+03d}"
        out.update(call1=c1, call2=c2)
        out["msg"] = " ".join(x for x in (c1, c2, tail) if x)
        if hashes:
            if std1:
                hashes.add(c1.split("/")[0])
            if std2:
                hashes.add(c2.split("/")[0])
        return out
    if i3 == 0 and n3 == 0:
        v = bits_to_int(bits[0:71])
        s = ""
        for _ in range(13):
            s = A42[v % 42] + s
            v //= 42
        out["msg"] = s.strip()
        return out
    if i3 == 4:
        h12 = bits_to_int(bits[0:12])
        v = bits_to_int(bits[12:70])
        s = ""
        for _ in range(11):
            s = A38[v % 38] + s
            v //= 38
        call = s.strip()
        h1 = bits[70]
        r2 = bits_to_int(bits[71:73])
        cq = bits[73]
        hc = hashes.lookup(hashes.h12, h12) if hashes else "<...>"
        if hashes:
            hashes.add(call)
        if cq:
            out.update(msg=f"CQ {call}", call1="CQ", call2=call)
        else:
            a, b = (call, hc) if h1 else (hc, call)
            tail = {0: "", 1: "RRR", 2: "RR73", 3: "73"}[r2]
            out.update(msg=" ".join(x for x in (a, b, tail) if x), call1=a, call2=b)
        return out
    if i3 == 0 and n3 == 5:
        out["msg"] = "TLM " + format(bits_to_int(bits[0:71]), "018X")
        return out
    out["msg"] = f"<msg type i3={i3} n3={n3}>"
    return out

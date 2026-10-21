"""Tiny hamlib-rigctl client for kiwiclientd --enable-rigctl (default port 6400).

kiwiclientd understands: F <Hz>, f, M <mode> [passband], m, q.
It answers set commands with 'RPRT 0' (ok) or 'RPRT -1' (error).
"""
import socket

from .bands import MODES


class RigctlError(RuntimeError):
    pass


class Rigctl:
    def __init__(self, host: str = "127.0.0.1", port: int = 6400, timeout: float = 2.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._sock = None
        self._buf = b""

    def connect(self):
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        return self

    def close(self):
        if self._sock:
            try:
                self._sock.sendall(b"q\n")
            except OSError:
                pass
            self._sock.close()
            self._sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    def _readline(self) -> str:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(1024)
            if not chunk:
                raise RigctlError("rigctl connection closed")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("ascii").strip()

    def _drain(self):
        """Throw away late replies left over from a timed-out command, so the
        next reply we read really belongs to the next command."""
        self._buf = b""
        self._sock.setblocking(False)
        try:
            while self._sock.recv(4096):
                pass
        except (BlockingIOError, InterruptedError):
            pass
        finally:
            self._sock.settimeout(self.timeout)

    def _send(self, cmd: str):
        self._drain()
        self._sock.sendall((cmd + "\n").encode("ascii"))

    def _set(self, cmd: str):
        self._send(cmd)
        reply = self._readline()
        if reply != "RPRT 0":
            raise RigctlError(f"{cmd!r} failed: {reply}")

    def set_freq(self, hz: int):
        if not 0 < hz <= 32_000_000:
            raise ValueError("KiwiSDR covers 0-32 MHz")
        self._set(f"F {int(hz)}")

    def set_mode(self, mode: str):
        mode = mode.lower()
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self._set(f"M {mode}")

    def get_freq(self) -> int:
        self._send("f")
        reply = self._readline()
        if not reply.isdigit():
            raise RigctlError(f"'f' gave {reply!r}")
        return int(reply)

    def get_mode(self) -> str:
        self._send("m")
        mode = self._readline()
        self._readline()  # passband
        return mode.upper()

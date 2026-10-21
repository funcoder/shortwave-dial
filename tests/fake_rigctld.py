"""A stand-in for kiwiclientd's rigctl port, replying the way kiwi/rigctld.py does.
Used only for offline tests (the box cannot reach KiwiSDRs)."""
import socket
import threading


class FakeRigctld:
    def __init__(self):
        self.freq_khz, self.mode, self.log = 7074.0, "usb", []
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen()
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        f = conn.makefile("rwb")
        for raw in f:
            cmd = raw.decode().strip()
            self.log.append(cmd)
            if cmd.startswith("q"):
                break
            if cmd.startswith("F"):
                try:
                    self.freq_khz = float(cmd[2:]) / 1000
                    out = "RPRT 0\n"
                except ValueError:
                    out = "RPRT -1\n"
            elif cmd.startswith("M"):
                self.mode = cmd.split()[1]
                out = "RPRT 0\n"
            elif cmd.startswith("f"):
                out = f"{int(self.freq_khz * 1000)}\n"
            elif cmd.startswith("m"):
                out = f"{self.mode.upper()}\n2700\n"
            else:
                out = "RPRT 0\n"
            f.write(out.encode()); f.flush()
        conn.close()

    def close(self):
        self.srv.close()

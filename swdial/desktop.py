"""Put Shortwave Dial in the Omarchy app launcher (Super + Space).

Writes ~/.local/share/applications/shortwave-dial.desktop and the icon, only
when they are missing, so a hand-edited entry is never overwritten.
Run by the window at start-up, or by hand: python -m swdial.desktop
"""
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "org.funcoder.ShortwaveDial"
NAME = "shortwave-dial"


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")


def _quote(arg: str) -> str:
    """Quote one Exec argument the way the desktop entry spec wants."""
    if not any(c in arg for c in ' \t"\'\\$`'):
        return arg
    return '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'


def entry_text(python: str = sys.executable, root: Path = ROOT) -> str:
    exec_ = " ".join(_quote(a) for a in ("env", f"PYTHONPATH={root}", python, "-m", "swdial.gtk_app"))
    return f"""[Desktop Entry]
Type=Application
Name=Shortwave Dial
GenericName=Shortwave Radio
Comment=Tune a KiwiSDR with a retro dial
Exec={exec_}
Path={root}
Icon={NAME}
Terminal=false
Categories=AudioVideo;Audio;HamRadio;
Keywords=radio;shortwave;kiwisdr;ft8;ham;sdr;
StartupWMClass={APP_ID}
"""


def ensure_launcher(data_home: Path | None = None) -> list[Path]:
    """Create the launcher entry and icon if they don't exist. Returns files written."""
    data_home = data_home or _data_home()
    written = []
    icon = data_home / "icons/hicolor/scalable/apps" / f"{NAME}.svg"
    if not icon.exists():
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).with_name("icon.svg"), icon)
        written.append(icon)
    desktop = data_home / "applications" / f"{NAME}.desktop"
    if not desktop.exists():
        desktop.parent.mkdir(parents=True, exist_ok=True)
        tmp = desktop.with_suffix(".tmp")
        tmp.write_text(entry_text(), encoding="utf-8")
        tmp.replace(desktop)  # appears whole, never half-written
        written.append(desktop)
    return written


if __name__ == "__main__":
    done = ensure_launcher()
    print("\n".join(f"wrote {p}" for p in done) or "launcher entry already there")

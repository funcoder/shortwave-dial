import json

import pytest

from swdial import cli
from swdial.bands import clamp_to_band, format_mhz
from swdial.rigctl import Rigctl
from tests.fake_rigctld import FakeRigctld


@pytest.fixture
def rig():
    fake = FakeRigctld()
    yield fake
    fake.close()


def test_sends_F_and_M(rig):
    with Rigctl(port=rig.port) as r:
        r.set_mode("AM")
        r.set_freq(9_410_000)
        assert r.get_freq() == 9_410_000
        assert r.get_mode() == "AM"
    assert "F 9410000" in rig.log and "M am" in rig.log


def test_band_button(rig, capsys):
    assert cli.main(["--port", str(rig.port), "band", "20m"]) == 0
    assert capsys.readouterr().out.strip() == "14.074 MHz USB"


def test_scroll_step(rig, capsys):
    cli.main(["--port", str(rig.port), "step", "+1000"])
    assert capsys.readouterr().out.strip() == "7.075 MHz USB"


def test_waybar_pill(rig, capsys):
    cli.main(["--port", str(rig.port), "waybar"])
    assert json.loads(capsys.readouterr().out)["text"] == "📻 7.074 USB"


def test_waybar_when_offline(capsys):
    cli.main(["--port", "1", "waybar"])
    assert json.loads(capsys.readouterr().out)["class"] == "off"


def test_helpers():
    assert format_mhz(7_074_000) == "7.074"
    assert clamp_to_band("40m", 8_000_000) == 7_200_000


def test_rejects_bad_mode(rig):
    with Rigctl(port=rig.port) as r, pytest.raises(ValueError):
        r.set_mode("wfm")


def test_narrow_filter_sends_kiwi_narrow_mode_but_reads_back_plain(rig):
    with Rigctl(port=rig.port) as r:
        r.set_mode("usb", narrow=True)
        assert r.get_mode() == "USB"
        r.set_mode("cw", narrow=True)
        assert r.get_mode() == "CW"
    assert "M usn" in rig.log and "M cwn" in rig.log

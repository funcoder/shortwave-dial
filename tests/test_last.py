from swdial import last


def test_round_trip(tmp_path):
    p = tmp_path / "sub" / "last.json"
    last.save(14074000, "usb", "20m", "Wessex", path=p)
    assert last.load(p) == {"freq": 14074000, "mode": "usb", "band": "20m", "receiver": "Wessex"}


def test_missing_or_broken_file_gives_none(tmp_path):
    p = tmp_path / "last.json"
    assert last.load(p) is None
    p.write_text("{not json")
    assert last.load(p) is None
    p.write_text('{"freq": 7074000, "mode": "usb", "band": "6cm"}')  # band we don't know
    assert last.load(p) is None


def test_frequency_outside_band_is_pulled_back_in(tmp_path):
    p = tmp_path / "last.json"
    last.save(99_000_000, "usb", "40m", "Wessex", path=p)
    assert last.load(p)["freq"] == last.BANDS["40m"]["end"]

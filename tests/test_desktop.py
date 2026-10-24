import configparser

from swdial import desktop


def test_creates_entry_and_icon_once(tmp_path):
    written = desktop.ensure_launcher(tmp_path)
    entry = tmp_path / "applications" / "shortwave-dial.desktop"
    assert entry in written and (tmp_path / "icons/hicolor/scalable/apps/shortwave-dial.svg") in written
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(entry)
    e = cp["Desktop Entry"]
    assert e["Name"] == "Shortwave Dial" and e["Icon"] == "shortwave-dial"
    assert "-m swdial.gtk_app" in e["Exec"] and e["StartupWMClass"] == desktop.APP_ID
    assert desktop.ensure_launcher(tmp_path) == []  # already there: nothing written


def test_never_overwrites_a_hand_edited_entry(tmp_path):
    entry = tmp_path / "applications" / "shortwave-dial.desktop"
    entry.parent.mkdir(parents=True)
    entry.write_text("[Desktop Entry]\nName=My Radio\n")
    desktop.ensure_launcher(tmp_path)
    assert "My Radio" in entry.read_text()


def test_exec_quotes_paths_with_spaces(tmp_path):
    text = desktop.entry_text("/opt/my py/python", tmp_path / "a b")
    assert '"/opt/my py/python"' in text and f'"PYTHONPATH={tmp_path}/a b"' in text

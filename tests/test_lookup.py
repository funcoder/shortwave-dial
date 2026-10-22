from swdial import lookup as L

QRZ = """<?xml version="1.0" ?>
<QRZDatabase version="1.34" xmlns="http://xmldata.qrz.com">
  <Callsign><call>G7MJF</call><fname>JONATHAN</fname><name>BUCKLAND</name>
  <addr2>Bridport</addr2><grid>IO80sr</grid><country>England</country></Callsign>
  <Session><Key>abc</Key></Session>
</QRZDatabase>"""

HAMQTH = """<?xml version="1.0"?>
<HamQTH version="2.8" xmlns="https://www.hamqth.com">
<search><callsign>ok1rr</callsign><nick>Petr</nick><qth>Neratovice</qth>
<adr_name>Petr Hlozek</adr_name><grid>jo70fd</grid></search></HamQTH>"""

CALLOOK = """{"status": "VALID", "type": "PERSON", "name": "WELLS, JOSHUA D",
 "address": {"line1": "1 MAIN ST", "line2": "NEWINGTON, CT 06111"},
 "location": {"gridsquare": "FN31pr"}}"""


def test_parse_qrz():
    r = L.parse_qrz(QRZ)
    assert r["name"] == "Jonathan Buckland" and r["qth"] == "Bridport" and r["source"] == "QRZ.com"


def test_parse_hamqth():
    r = L.parse_hamqth(HAMQTH)
    assert r["name"] == "Petr Hlozek" and r["qth"] == "Neratovice"


def test_parse_callook():
    r = L.parse_callook(CALLOOK)
    assert r["name"] == "Joshua D Wells" and r["qth"] == "Newington"
    assert L.parse_callook('{"status": "INVALID"}') is None


def test_qrz_not_found_gives_none():
    assert L.parse_qrz('<QRZDatabase xmlns="http://xmldata.qrz.com"><Session>'
                       '<Error>Not found: X1XX</Error></Session></QRZDatabase>') is None


def test_us_calls_only_go_to_callook(monkeypatch):
    asked = []
    monkeypatch.setattr(L, "_get", lambda url, params, timeout: asked.append(url) or CALLOOK)
    L._names.clear()
    assert L.operator("W1ABC", accts={})["name"] == "Joshua D Wells"
    assert L.operator("G7MJF", accts={}) is None
    assert asked == ["https://callook.info/W1ABC/json"]


def test_accounts_file(tmp_path):
    f = tmp_path / "accounts.ini"
    f.write_text("[qrz]\nuser = G7MJF\npassword = secret\n[hamqth]\nuser =\n")
    assert L.accounts(f) == {"qrz": ("G7MJF", "secret")}


def test_name_with_suffix():
    assert L._tidy("TAYLOR, JR., JOSEPH H") == "Joseph H Taylor Jr."

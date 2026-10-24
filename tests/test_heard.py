from swdial import heard, worldmap
from swdial.lookup import load_cty

CTY = load_cty("""England:                  14:  27:  EU:   52.77:     1.47:     0.0:  G:
    G,M,2E;
Italy:                    15:  28:  EU:   42.82:   -12.58:    -1.0:  I:
    I;
""")


def test_add_save_load(tmp_path):
    p = tmp_path / "heard.json"
    h = heard.Heard(p)
    assert h.add("g4abc", "IO91", "40m", -12, when=100) is True
    assert h.add("G4ABC", "", "20m", -3, when=200) is False
    h.save()
    e = heard.Heard(p).calls["G4ABC"]
    assert e["grid"] == "IO91" and e["bands"] == ["40m", "20m"] and e["snr"] == -3
    assert (e["first"], e["last"], e["n"]) == (100, 200, 2) and e["logged"] is False


def test_broken_file_starts_empty(tmp_path):
    p = tmp_path / "heard.json"
    p.write_text("nope")
    assert heard.Heard(p).calls == {}


def test_log_backfill_marks_logged_without_counting(tmp_path):
    h = heard.Heard(tmp_path / "heard.json")
    h.add("G4ABC", when=50)
    h.merge_log([{"CALL": "G4ABC", "QSO_DATE": "20261020", "TIME_ON": "120000"},
                 {"CALL": "I2XYZ", "QSO_DATE": "20261021", "TIME_ON": "0930", "GRIDSQUARE": "JN45", "BAND": "20M"}])
    assert h.calls["G4ABC"]["logged"] and h.calls["G4ABC"]["n"] == 1
    i = h.calls["I2XYZ"]
    assert i["logged"] and i["n"] == 0 and i["grid"] == "JN45" and i["bands"] == ["20m"]
    assert [c for c, _ in h.since(0)][0] == "I2XYZ"  # newest first


def test_place_uses_grid_then_country():
    lat, lon, country, exact = heard.place("I2XYZ", {"grid": "JN45"}, CTY)
    assert (round(lat, 1), round(lon, 1), country, exact) == (45.5, 9.0, "Italy", True)
    lat, lon, country, exact = heard.place("G4ABC", {"grid": ""}, CTY)
    assert (lat, lon, country, exact) == (52.77, -1.47, "England", False)
    assert heard.place("ZZ9ZZ", {"grid": ""}, CTY) is None


def test_projection_and_hit_test():
    box = worldmap.fit(720, 600)  # too tall: map is full width, centred vertically
    assert box[2] == 720 and round(box[3]) == round(720 / worldmap.aspect())
    x, y = worldmap.project(0, 0, box)
    assert x == 360 and box[1] < y < box[1] + box[3]
    assert worldmap.project(89, -180, box) == (0, box[1])  # far north clamped to the top edge
    eu = worldmap.fit(700, 600, "europe")
    x, y = worldmap.project(51.5, -0.1, eu)  # London: a third of the way across, just below the middle
    assert 0.3 < (x - eu[0]) / eu[2] < 0.4 and 0.5 < (y - eu[1]) / eu[3] < 0.6
    assert worldmap.inside(51.5, -0.1, "europe") and not worldmap.inside(40.7, -74, "europe")
    pts = [(10, 10), (100, 100), (104, 103)]
    assert worldmap.nearest(pts, 103, 103) == 2 and worldmap.nearest(pts, 300, 300) is None
    assert len(worldmap.land_rings()) > 100

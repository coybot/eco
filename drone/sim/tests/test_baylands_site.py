"""baylands_site: frame conversion, the limits at known points, paths.

Reference data for simulation, not a legal determination (see the module).
"""
import math

import pytest

import baylands_site as bs

FT = 0.3048


@pytest.fixture(scope="module")
def site():
    return bs.load()


# ------------------------------------------------------------------ frame

def test_frame_matches_env_baylands_formula():
    # env_baylands.gd: m_lon = 111320 * cos(lat0); e = dlon * m_lon; n = dlat * 111132
    m_lon = 111320.0 * math.cos(math.radians(37.4125))
    e, n = bs.geo_to_enu(37.4125 + 0.01, -121.9970 + 0.01)
    assert e == pytest.approx(0.01 * m_lon, abs=1e-6)
    assert n == pytest.approx(0.01 * 111132.0, abs=1e-6)
    assert bs.geo_to_enu(37.4125, -121.9970) == (0.0, 0.0)


def test_frame_round_trip():
    for e, n in [(0, 0), (-2500, -2400), (2500, 2400), (123.4, -567.8)]:
        lat, lon = bs.enu_to_geo(e, n)
        e2, n2 = bs.geo_to_enu(lat, lon)
        assert e2 == pytest.approx(e, abs=1e-6) and n2 == pytest.approx(n, abs=1e-6)


def test_godot_pos_flips_north():
    assert bs.godot_pos(10.0, 20.0, 3.0) == (10.0, 3.0, -20.0)


def test_site_json_frame_agrees(site):
    f = site.data["frame"]
    assert f["datum_lat_lon"] == [bs.DATUM_LAT, bs.DATUM_LON]
    assert f["m_per_deg_lat"] == bs.M_PER_DEG_LAT
    assert f["m_per_deg_lon"] == pytest.approx(bs.M_PER_DEG_LON, rel=1e-12)


def test_uasfm_cell_corners_on_the_30_arcsec_grid(site):
    # Cell edges fall on multiples of 1/120 degree (the FAA's grid sits up to ~1.5e-5
    # deg, < 2 m, off them): the frame conversion must land back on it.
    for c in site.data["uasfm"]["cells"][:10]:
        for e, n in c["ring"]:
            lat, lon = bs.enu_to_geo(e, n)
            assert abs(lat * 120 - round(lat * 120)) < 3e-3
            assert abs(lon * 120 - round(lon * 120)) < 3e-3


def test_terrain_matches_pads(site):
    # The 2 m grid and the pads' 0.5 m lidar ground agree within ~0.3 m on flat lawn.
    for p in site.pads:
        assert site.ground_height(p["e"], p["n"]) == pytest.approx(p["ground_m"], abs=0.3)


# ------------------------------------------------------------- the limits

def test_disclaimer_present(site):
    assert "NOT a legal determination" in site.data["disclaimer"]
    assert "NOT a legal determination" in bs.DISCLAIMER
    assert "NOT a legal determination" in (bs.__doc__ or "")


def test_park_centre(site):
    r = site.check(0.0, 0.0, alt_agl=30.0)
    assert r["park"] and not r["preserve"]
    assert r["airspace"]["class"] == "C"
    assert r["uasfm"]["ceiling_ft"] == 400 and r["uasfm"]["laanc"]
    assert r["ok"]


# West strip of the park: the 300 ft cell (lon < -122.0).
WEST = (-280.0, 60.0)


def test_west_park_point_is_in_the_300ft_cell(site):
    e, n = WEST
    assert site.in_park(e, n)
    assert bs.enu_to_geo(e, n)[1] < -122.0
    assert site.uasfm_cell(e, n)["ceiling_ft"] == 300


def test_park_350ft_exceeds_300ft_cell(site):
    r = site.check(*WEST, alt_agl=350 * FT)
    assert not r["ok"]
    assert [v["code"] for v in r["violations"]] == ["uasfm_ceiling"]
    assert r["max_agl_m"] == pytest.approx(300 * FT)


def test_park_250ft_is_under_300ft_cell(site):
    r = site.check(*WEST, alt_agl=250 * FT)
    assert r["ok"], r["violations"]
    assert r["airspace"]["class"] == "C"


def test_alt_msl_equals_ground_plus_agl(site):
    g = site.ground_height(*WEST)
    a = site.check(*WEST, alt_agl=50.0)
    b = site.check(*WEST, alt_msl=g + 50.0)
    assert a["alt_msl_m"] == pytest.approx(b["alt_msl_m"]) and a["ok"] == b["ok"]


def test_dod_99_7_zone(site):
    # Lockheed Martin Space - Sunnyvale, just west of the scene: surface to 400 ft AGL.
    lms = next(s for s in site.data["security"] if s["name"].startswith("Lockheed"))
    ring = lms["polys"][0][0]
    e = sum(p[0] for p in ring) / len(ring)
    n = sum(p[1] for p in ring) / len(ring)
    assert bs.point_in_poly(e, n, lms["polys"][0])
    r = site.check(e, n, alt_agl=30.0)
    assert "security_99_7" in [v["code"] for v in r["violations"]]
    assert r["max_agl_m"] == 0.0
    above = site.check(e, n, alt_agl=410 * FT)
    assert "security_99_7" not in [v["code"] for v in above["violations"]]
    assert lms["ceiling"]["ft"] == 400 and lms["ceiling"]["m"] == pytest.approx(121.92)


def test_part107_ceiling(site):
    r = site.check(0.0, 0.0, alt_agl=401 * FT)
    assert "part107_ceiling" in [v["code"] for v in r["violations"]]


def test_stadium_tfr_only_on_event_days(site):
    r = site.check(0.0, 0.0, alt_agl=30.0)
    assert r["ok"] and r["tfr"][0]["inside"]
    on = site.check(0.0, 0.0, alt_agl=30.0, tfr_active=True)
    assert "tfr" in [v["code"] for v in on["violations"]]
    t = site.data["tfr"][0]
    assert t["radius_m"] == pytest.approx(3 * 1852.0) and t["ceiling"]["m"] == pytest.approx(914.4)


def test_preserve(site):
    pres = site.data["preserve"]["ring"]
    e = sum(p[0] for p in pres) / len(pres)
    n = sum(p[1] for p in pres) / len(pres)
    if not site.in_preserve(e, n):
        e, n = 500.0, 100.0
    r = site.check(e, n, alt_agl=20.0)
    assert r["preserve"] and any("preserve" in w for w in r["warnings"])


def test_units_ft_and_m_consistent(site):
    for c in site.data["uasfm"]["cells"]:
        assert c["ceiling_m"] == pytest.approx(c["ceiling_ft"] * FT, abs=0.01)
    for a in site.data["airspace"]:
        for k in ("floor", "ceiling"):
            assert a[k]["m"] == pytest.approx(a[k]["ft"] * FT, abs=0.01)


def test_pads_obey_their_rules(site):
    assert 3 <= len(site.pads) <= 6
    for p in site.pads:
        assert site.in_park(p["e"], p["n"]) and not site.in_preserve(p["e"], p["n"])
        assert p["clear_m"] >= 15 and p["power_line_m"] > 60 and p["slope_pct"] < 3
        assert site.check(p["e"], p["n"], alt_agl=1.0)["ok"]


# ------------------------------------------------------------------ paths

def test_path_crossing_into_the_300ft_cell(site):
    r = site.check_path([(0.0, 60.0, 100.0), (-280.0, 60.0, 100.0)], alt_ref="agl")
    assert not r["ok"]
    a, b = r["violations"]["uasfm_ceiling"][0]
    # The cell edge (lon -122.0) is at e = -0.003 deg * m_lon ~ -265 m.
    edge = (-122.0 - bs.DATUM_LON) * bs.M_PER_DEG_LON
    assert a == pytest.approx(-edge, abs=5.0)
    assert b == pytest.approx(280.0, abs=0.5)


def test_path_low_in_the_park_is_clean(site):
    r = site.check_path([(p["e"], p["n"], 20.0) for p in site.pads], alt_ref="agl")
    assert r["ok"] and r["classes"] == ["C"]


def test_wind_climatology(site):
    w = site.wind(7, 15)
    assert w["n"] > 50 and 6 <= w["mean_kt"] <= 18
    assert 300 <= w["from_deg"] <= 360                 # afternoon sea breeze from the NW-N
    assert w["mean_ms"] == pytest.approx(w["mean_kt"] * 0.514444, abs=0.01)

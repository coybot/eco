"""The daemon reports the sim's GPS receiver when asked (--report-gps)."""
import fw_gcs_daemon as d

DATUM = {"lat": 37.4, "lon": -122.1}
BASE = {"ok": True, "position": [10.0, 20.0, 80.0], "yaw": 0.0, "airspeed": 20.0, "agl": 70.0}


def test_truth_by_default():
    hb = d.build_heartbeat("a", True, dict(BASE, gps={"fix": "3d", "e": 15.0, "n": 25.0, "u": 81.0}), DATUM)
    assert hb["position_enu"]["east_m"] == 10.0 and "gps" not in hb


def test_receiver_fix_when_asked():
    hb = d.build_heartbeat("a", True, dict(BASE, gps={"fix": "3d", "e": 15.0, "n": 25.0, "u": 81.0, "sats": 9}),
                           DATUM, report_gps=True)
    assert hb["position_enu"]["east_m"] == 15.0 and hb["gps"]["fix"] == "3d"


def test_no_fix_no_position():
    hb = d.build_heartbeat("a", True, dict(BASE, gps={"fix": "none", "sats": 0}), DATUM, report_gps=True)
    assert "position" not in hb and "position_enu" not in hb and hb["gps"]["fix"] == "none"


def test_wind_reported_when_blowing():
    hb = d.build_heartbeat("a", True, dict(BASE, wind=[0.0, -8.0, 0.0], groundspeed=23.0), DATUM)
    assert hb["wind_enu_mps"] == [0.0, -8.0, 0.0] and hb["groundspeed_mps"] == 23.0
    assert "wind_enu_mps" not in d.build_heartbeat("a", True, dict(BASE, wind=[0.0, 0.0, 0.0]), DATUM)

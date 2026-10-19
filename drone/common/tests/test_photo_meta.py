"""Survey photos carry where and when they were taken.

Observed 2026-10-14: the JPEGs capture_photo uploaded had no EXIF at all (no GPS
IFD, no capture time), so a crop survey of 50 photos could not be placed on the
ground. These check the EXIF the aircraft now writes with Pillow's own reader (an
independent implementation, so a layout bug in photo_meta cannot hide), that the
survey reports each photo's position in a manifest, and that it waits for the
aircraft to be nearly still before shooting.

Run: cd eco && python3 -m pytest drone/common/tests/test_photo_meta.py -v
"""

import io
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import photo_meta  # noqa: E402
import reasoning_loop as rl  # noqa: E402
from vehicle_class import get_class  # noqa: E402

PIL = pytest.importorskip("PIL.Image")
from PIL import Image  # noqa: E402

WHEN = 1791999270.563  # 2026-10-14 17:34:30.563 UTC


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 120, 30)).save(buf, "JPEG")
    return buf.getvalue()


def _exif_of(data):
    return Image.open(io.BytesIO(data)).getexif()


def test_gps_time_and_heading_round_trip():
    tiff = photo_meta.build_exif(WHEN, lat=-35.3632584, lon=149.1652331, alt_msl_m=589.04,
                                 heading_deg=14.9, description="alt 5.0 m above home")
    ex = _exif_of(photo_meta.insert_exif(_jpeg(), tiff))
    gps = ex.get_ifd(0x8825)
    ifd = ex.get_ifd(0x8769)

    assert ex[0x0132] == "2026:10:14 17:34:30"
    assert ifd[0x9003] == "2026:10:14 17:34:30" and ifd[0x9291] == "563" and ifd[0x9011] == "+00:00"
    assert ex[0x010E] == "alt 5.0 m above home"

    def deg(v):
        return float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600

    assert gps[1] == "S" and gps[3] == "E"
    assert deg(gps[2]) == pytest.approx(35.3632584, abs=1e-6)
    assert deg(gps[4]) == pytest.approx(149.1652331, abs=1e-6)
    assert float(gps[6]) == pytest.approx(589.04, abs=0.001) and gps[5] == b"\x00"
    assert gps[16] == "T" and float(gps[17]) == pytest.approx(14.9, abs=0.01)
    assert gps[29] == "2026:10:14"
    h, m, s = (float(x) for x in gps[7])
    assert (h, m) == (17.0, 34.0) and s == pytest.approx(30.563, abs=0.001)


def test_no_fix_writes_a_time_but_no_gps_at_all():
    """A (0, 0) tag is a real place in the Atlantic - leave GPS out instead."""
    ex = _exif_of(photo_meta.insert_exif(_jpeg(), photo_meta.build_exif(WHEN)))
    assert ex.get_ifd(0x8769)[0x9003] == "2026:10:14 17:34:30"
    assert ex.get_ifd(0x8825) == {}


def test_northern_western_and_below_sea_level():
    tiff = photo_meta.build_exif(WHEN, lat=36.5, lon=-121.25, alt_msl_m=-3.5, heading_deg=-90)
    gps = _exif_of(photo_meta.insert_exif(_jpeg(), tiff)).get_ifd(0x8825)
    assert gps[1] == "N" and gps[3] == "W" and gps[5] == b"\x01" and float(gps[6]) == pytest.approx(3.5)
    assert float(gps[17]) == pytest.approx(270.0)   # heading wraps into 0-360


def test_restamping_replaces_instead_of_stacking_and_keeps_the_image():
    first = photo_meta.insert_exif(_jpeg(), photo_meta.build_exif(WHEN, lat=1.0, lon=2.0))
    second = photo_meta.insert_exif(first, photo_meta.build_exif(WHEN + 60, lat=3.0, lon=4.0))
    assert second.count(b"Exif\x00\x00") == 1
    im = Image.open(io.BytesIO(second))
    im.load()
    assert im.size == (64, 48)
    assert _exif_of(second).get_ifd(0x8769)[0x9003] == "2026:10:14 17:35:30"


def test_stamp_jpeg_file_rewrites_in_place(tmp_path):
    path = tmp_path / "p.jpg"
    path.write_bytes(_jpeg())
    photo_meta.stamp_jpeg_file(str(path), WHEN, lat=10.0, lon=20.0, alt_msl_m=5.0, heading_deg=1.0)
    assert Image.open(path).getexif().get_ifd(0x8825)[1] == "N"
    assert not list(tmp_path.glob("*.tmp"))


def test_not_a_jpeg_is_refused():
    with pytest.raises(ValueError):
        photo_meta.insert_exif(b"\x89PNG....", photo_meta.build_exif(WHEN))


def test_manifest_csv_has_a_row_per_photo_and_blanks_for_unknowns():
    rows = [{"n": 1, "file": "a.jpg", "lat": 1.5, "lon": 2.5, "gps": "yes"},
            {"n": 2, "file": "b.jpg", "gps": "no"}]
    lines = photo_meta.manifest_csv(rows).splitlines()
    assert lines[0].split(",") == photo_meta.MANIFEST_COLUMNS
    assert len(lines) == 3
    second = lines[2].split(",")
    assert second[photo_meta.MANIFEST_COLUMNS.index("lat")] == ""
    assert second[photo_meta.MANIFEST_COLUMNS.index("gps")] == "no"


# --- the aircraft side ------------------------------------------------------ #

def test_capture_photo_stamps_the_jpeg_with_the_fix_it_read(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import drone_sdk

    class Cam:
        def get_frame(self, timeout_ms=0):
            return types.SimpleNamespace(rgb=np.zeros((48, 64, 3), dtype="uint8"))

    fix = {"lat": -35.3632584, "lon": 149.1652331, "alt_msl_m": 589.0, "alt_above_home_m": 5.0,
           "heading_deg": 78.2, "ground_speed_mps": 0.1}
    monkeypatch.setattr(drone_sdk, "_get_camera", lambda: Cam())
    monkeypatch.setattr(drone_sdk, "release_camera", lambda: None)
    monkeypatch.setattr(drone_sdk, "_photo_fix", lambda max_age=1.0: fix)
    path = drone_sdk.capture_photo(save_path=str(tmp_path / "x.jpg"), upload=False)

    gps = Image.open(path).getexif().get_ifd(0x8825)
    assert gps[1] == "S" and float(gps[17]) == pytest.approx(78.2, abs=0.01)
    meta = drone_sdk.last_photo_meta()
    assert meta["fix"] == fix and abs(meta["when"] - __import__("time").time()) < 30


def test_capture_photo_with_no_gps_still_delivers_the_photo(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import drone_sdk

    class Cam:
        def get_frame(self, timeout_ms=0):
            return types.SimpleNamespace(rgb=np.zeros((48, 64, 3), dtype="uint8"))

    monkeypatch.setattr(drone_sdk, "_get_camera", lambda: Cam())
    monkeypatch.setattr(drone_sdk, "release_camera", lambda: None)
    monkeypatch.setattr(drone_sdk, "_photo_fix", lambda max_age=1.0: None)
    path = drone_sdk.capture_photo(save_path=str(tmp_path / "x.jpg"), upload=False)
    ex = Image.open(path).getexif()
    assert ex.get_ifd(0x8825) == {} and ex.get_ifd(0x8769)[0x9003]
    assert drone_sdk.last_photo_meta()["fix"] is None


def test_manifest_is_a_csv_the_sdk_will_upload_as_text():
    import drone_sdk
    assert drone_sdk._content_type_for("survey_manifest.csv") == "text/csv"


# --- the survey ------------------------------------------------------------ #

@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(rl, "DRONE_SDK_AVAILABLE", False)
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)
    monkeypatch.setattr(rl.MissionLoop, "_camera_problem", staticmethod(lambda: "camera unplugged"))


class Backend:
    def __init__(self):
        self.gotos = []

    def goto(self, north_m, east_m, alt_m):
        self.gotos.append((north_m, east_m, alt_m))
        return True

    def get_pose(self):
        n, e, a = self.gotos[-1] if self.gotos else (0.0, 0.0, 0.0)
        return (e, n, a, 0.0)

    def get_battery(self):
        return {"remaining": 100.0}

    def log_event(self, *a, **k):
        pass


class SDK:
    """capture_photo + the position the real SDK reports; records uploads."""

    def __init__(self, fix=True, upload_ok=True):
        self.fix, self.upload_ok = fix, upload_ok
        self.shots, self.uploads, self._meta = 0, [], None

    def capture_photo(self, upload=True):
        self.shots += 1
        self._meta = {"when": WHEN + self.shots,
                      "fix": ({"lat": -35.0 + self.shots * 1e-5, "lon": 149.0, "alt_msl_m": 590.0,
                               "alt_above_home_m": 5.0, "heading_deg": 12.0, "ground_speed_mps": 0.05}
                              if self.fix else None)}
        return f"https://b.s3.amazonaws.com/c/p{self.shots:02d}.jpg?X-Amz=1"

    def last_photo_meta(self):
        return self._meta

    def upload_media(self, path, conversation_id):
        self.uploads.append((Path(path).name, Path(path).read_text(), conversation_id))
        return "https://b.s3.amazonaws.com/c/survey_manifest.csv" if self.upload_ok else None


def _new_loop(sdk):
    loop = rl.MissionLoop(backend=Backend(), drone_sdk=sdk, conversation_id="c-1",
                          vehicle_class=get_class("quadcopter"))
    loop._home_yaw_rad = 0.0
    loop._media_warnings = []
    loop._photos = []
    return loop


def _fly(sdk, **phase):
    loop = _new_loop(sdk)
    p = {"forward_m": 3, "right_m": 2, "spacing_m": 1, "altitude_m": 5}
    p.update(phase)
    return loop, loop._exec_survey_rect(p)


def test_survey_uploads_a_manifest_with_every_photos_position_and_time():
    sdk = SDK()
    loop, result = _fly(sdk)
    assert result["success"] and sdk.shots == 6
    (name, text, conv), = sdk.uploads
    assert name.startswith("survey_manifest_") and name.endswith(".csv") and conv == "c-1"
    lines = text.splitlines()
    assert len(lines) == 1 + 6
    head = lines[0].split(",")
    first = dict(zip(head, lines[1].split(",")))
    assert first["file"] == "p01.jpg" and "X-Amz" not in first["url"]       # no signed query string
    assert first["utc_time"] == "2026-10-14T17:34:31.563Z" and first["gps"] == "yes"
    assert float(first["lat"]) == pytest.approx(-34.99999, abs=1e-6)
    assert first["heading_deg"] == "12.0" and first["cell"] == "1"
    assert [int(l.split(",")[head.index("cell")]) for l in lines[1:]] == [1, 2, 3, 4, 5, 6]
    assert not loop._media_warnings


def test_photos_taken_with_no_gps_fix_are_called_out_to_the_operator():
    sdk = SDK(fix=False)
    loop, result = _fly(sdk)
    assert result["success"]
    assert any("6 of 6 survey photos have no GPS position" in w for w in loop._media_warnings)
    rows = sdk.uploads[0][1].splitlines()
    gps_col = rows[0].split(",").index("gps")
    assert [r.split(",")[gps_col] for r in rows[1:]] == ["no"] * 6    # still a manifest, still a time


def test_a_manifest_that_cannot_upload_does_not_fail_the_survey():
    sdk = SDK(upload_ok=False)
    loop, result = _fly(sdk)
    assert result["success"] and sdk.shots == 6
    assert any("manifest" in w for w in loop._media_warnings)


def test_an_sdk_that_reports_no_position_at_all_is_not_blamed_for_it():
    class Plain:
        def __init__(self):
            self.n = 0

        def capture_photo(self, upload=True):
            self.n += 1
            return f"https://b.s3.amazonaws.com/c/{self.n}.jpg"

    loop, result = _fly(Plain())
    assert result["success"] and not loop._media_warnings


def test_the_manifest_is_still_uploaded_when_the_survey_is_stopped_part_way():
    sdk = SDK()
    loop = _new_loop(sdk)
    orig = sdk.capture_photo

    def shot_then_stop(upload=True):
        url = orig(upload)
        if sdk.shots == 3:
            loop._aborted = True
        return url

    sdk.capture_photo = shot_then_stop
    result = loop._exec_survey_rect({"forward_m": 3, "right_m": 2, "spacing_m": 1, "altitude_m": 5})
    assert result.get("aborted")
    assert len(sdk.uploads[0][1].splitlines()) == 1 + 3      # the three it took


def test_the_survey_waits_until_the_aircraft_is_nearly_still_before_shooting(monkeypatch):
    """Seen in SITL: 5 of 50 photos landed on a cell boundary because the
    aircraft passed through the cell at up to 0.9 m/s - near the point, but moving."""
    loop = _new_loop(SDK())
    clock = {"t": 1000.0}
    monkeypatch.setattr(rl.time, "time", lambda: clock["t"])
    monkeypatch.setattr(rl.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    # Within the 0.6 m tolerance of the point on every read, but creeping at
    # 0.6 m/s (0.12 m per 0.2 s) until the 5th read, then nearly stopped.
    track = iter([0.00, 0.12, 0.24, 0.36, 0.48, 0.50, 0.50])
    reads = []

    class B:
        def get_pose(self):
            reads.append(1)
            return (next(track), 0.0, 5.0, 0.0)

    loop._settle_over(B(), north_m=0.0, east_m=0.5, tol_m=0.6)
    assert len(reads) == 6   # read 6 is the first interval slower than SURVEY_STILL_MPS


# --- the SDK's own survey_rect (the code-generation path) -------------------- #

class _Flight:
    """drone_sdk's survey_rect, with the flight controller replaced by a point mass
    that is exactly where it was last told to go."""

    def __init__(self, armed=True, up=5.0):
        self.armed, self.up = armed, up
        self.pos = (0.0, 0.0)          # east, north
        self.legs, self.uploads, self.shots = [], [], 0

    def install(self, monkeypatch):
        import drone_sdk
        monkeypatch.setattr(drone_sdk.time, "sleep", lambda s: None)
        monkeypatch.setattr(drone_sdk, "get_local_pose", lambda: (self.pos[0], self.pos[1], self.up, 0.0))
        monkeypatch.setattr(drone_sdk, "is_armed", lambda: self.armed)
        monkeypatch.setattr(drone_sdk, "goto_offset", self._goto)
        monkeypatch.setattr(drone_sdk, "capture_photo", self._photo)
        monkeypatch.setattr(drone_sdk, "upload_media", self._upload)
        monkeypatch.setattr(drone_sdk, "CONVERSATION_ID", "conv-9", raising=False)
        return drone_sdk

    def _goto(self, north_m, east_m, alt_m, **kw):
        self.legs.append((north_m, east_m))
        self.pos = (east_m, north_m)
        return True

    def _photo(self, save_path=None, upload=True):
        import drone_sdk
        self.shots += 1
        drone_sdk._last_photo_meta = {"when": WHEN + self.shots, "fix": {
            "lat": -35.0, "lon": 149.0, "alt_msl_m": 590.0, "alt_above_home_m": 5.0,
            "heading_deg": 0.0, "ground_speed_mps": 0.0}}
        return f"https://b.s3.amazonaws.com/c/p{self.shots:02d}.jpg"

    def _upload(self, path, conversation_id, content_type=None):
        self.uploads.append((Path(path).name, Path(path).read_text(), conversation_id))
        return "https://b.s3.amazonaws.com/c/survey_manifest.csv"


def test_sdk_survey_refuses_on_the_ground_instead_of_surveying_from_0_m(monkeypatch, capsys):
    """Seen through the legacy path: generated code was just survey_rect(...), no takeoff, and the
    aircraft 'surveyed' 50 cells from the grass ('altitude=0.003 clamped to minimum 0.5')."""
    fl = _Flight(armed=False, up=0.0)
    sdk = fl.install(monkeypatch)
    assert sdk.survey_rect(10, 5, spacing_m=1) == []
    assert fl.legs == [] and fl.shots == 0 and not fl.uploads
    out = capsys.readouterr().out
    assert "SURVEY REFUSED" in out and "takeoff" in out


def test_sdk_survey_refuses_when_armed_but_not_climbed(monkeypatch):
    fl = _Flight(armed=True, up=0.2)
    sdk = fl.install(monkeypatch)
    assert sdk.survey_rect(2, 2) == [] and fl.legs == []


def test_sdk_survey_in_flight_takes_every_photo_and_uploads_the_manifest(monkeypatch):
    fl = _Flight()
    sdk = fl.install(monkeypatch)
    urls = sdk.survey_rect(3, 2, spacing_m=1, altitude_m=5)
    assert len(urls) == 6 and fl.shots == 6 and len(fl.legs) == 6
    (name, text, conv), = fl.uploads
    assert name.startswith("survey_manifest_") and name.endswith(".csv") and conv == "conv-9"
    lines = text.splitlines()
    head = lines[0].split(",")
    assert len(lines) == 7
    first = dict(zip(head, lines[1].split(",")))
    assert first["file"] == "p01.jpg" and first["gps"] == "yes" and first["utc_time"] == "2026-10-14T17:34:31.563Z"
    # Every cell once, in the shortest order that ends back by home: up the
    # left column and down the right (the serpentine 1..6 ends at the far end).
    assert [int(l.split(",")[head.index("cell")]) for l in lines[1:]] == [1, 4, 5, 6, 3, 2]


def test_sdk_survey_manifest_failure_never_loses_the_photos(monkeypatch):
    fl = _Flight()
    sdk = fl.install(monkeypatch)
    monkeypatch.setattr(sdk, "upload_media", lambda *a, **k: None)
    assert len(sdk.survey_rect(2, 2, spacing_m=1, altitude_m=5)) == 4

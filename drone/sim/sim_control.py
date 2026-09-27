"""Per-drone SDK namespace, backed by the sim engine over IPC.

Same cloud-generated SDK surface (arm/takeoff/land/goto/set_velocity/set_yaw/
capture_photo/look_around; rover drive/turn/move_forward), but every vehicle
action goes to the shared Isaac engine via EngineClient. Runs inside a per-drone
daemon process. Movement blocks by polling engine state while the engine moves
the vehicle, so multiple daemons act simultaneously.
"""

from __future__ import annotations

import contextlib
import io
import math
import time
import traceback

from sim_sdk import latlon_to_xy, xy_to_latlon, HOME_LAT, HOME_LON


def _upload_jpeg_to_gcs(upload_conf, drone_id, conversation_id, jpg_bytes) -> str:
    """GCS-mode counterpart of cloud_creds.upload_jpeg_to_s3: PUT to the local
    Ground Control Station's /images/{key} route (gcs/http_api.py) instead of
    S3, authenticated with this sim drone's own pairing token - same
    key-naming convention as drone_sdk.py's _upload_photo_gcs."""
    import time
    import urllib.request

    images_base_url = upload_conf.get("images_base_url")
    token = upload_conf.get("auth_token")
    if not images_base_url or not token:
        raise RuntimeError("gcs.images_base_url / drone pairing token not configured")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    key = f"drones/{drone_id}/conversations/{conversation_id}/{timestamp}_photo.jpg"
    url = f"{images_base_url.rstrip('/')}/images/{key}"

    req = urllib.request.Request(url, data=jpg_bytes, method="PUT")
    req.add_header("Content-Type", "image/jpeg")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GCS image upload failed: HTTP {resp.status}")
    return url


def build_namespace(engine, did, vtype, conversation_id, upload_conf, armed):
    is_rover = vtype == "rover"
    ROVER_ALT = 0.0
    images: list[str] = []
    videos: list[str] = []

    def _pos():
        return engine.state(did)["position"]

    def _yaw():
        return engine.state(did)["yaw"]

    def _dist(a, b):
        return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))

    def _goto_xyz(x, y, z, timeout=30.0):
        engine.set_goal(did, [x, y, z])
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _dist(_pos(), [x, y, z]) < 0.25:
                break
            time.sleep(0.1)

    def _set_yaw_rad(target, timeout=10.0):
        engine.set_yaw(did, target)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if abs((target - _yaw() + math.pi) % (2 * math.pi) - math.pi) < math.radians(2):
                break
            time.sleep(0.1)

    def arm():
        armed["v"] = True

    def safe_disarm():
        armed["v"] = False

    def wait(seconds):
        time.sleep(min(float(seconds), 30.0))

    def get_position():
        p = _pos()
        lat, lon = xy_to_latlon(float(p[0]), float(p[1]))
        return (lat, lon, float(p[2]))

    def get_attitude():
        return (0.0, 0.0, math.degrees(_yaw()))

    def set_velocity(vx, vy, vz=0.0):
        engine.set_velocity(did, [float(vx), float(vy), float(vz)])
        time.sleep(2.0)
        engine.clear_velocity(did)

    def set_yaw(angle_deg, relative=False):
        cur = _yaw()
        target = cur + math.radians(angle_deg) if relative else math.radians(angle_deg)
        _set_yaw_rad(target)

    def capture_photo(description="", upload=True, **kw):
        jpg = engine.grab_jpeg(did)
        if not jpg:
            return ""
        if not upload_conf:
            return "sim://image"
        try:
            if upload_conf.get("control_plane") == "gcs":
                url = _upload_jpeg_to_gcs(upload_conf, did, conversation_id, jpg)
            else:
                from cloud_creds import iot_credentials, upload_jpeg_to_s3
                c = upload_conf
                creds = iot_credentials(c["certs_dir"], c["credentials_endpoint"],
                                        c["s3_role_alias"], c["thing_name"])
                url = upload_jpeg_to_s3(creds, c["images_bucket"], c["region"],
                                        did, conversation_id, jpg)
            images.append(url)
            return url
        except Exception as e:
            print(f"[{did}] capture_photo failed: {e}", flush=True)
            return ""

    def look_around(directions=4):
        urls = []
        for _ in range(max(1, directions)):
            set_yaw(360.0 / max(1, directions), relative=True)
            u = capture_photo()
            if u:
                urls.append(u)
        return urls

    def record_video(seconds=5.0, description="", fps=15):
        """Record an mp4 from this drone's camera and return an S3 URL."""
        from video_record import record_mp4
        def _grab():
            jpg = engine.grab_jpeg(did)
            if not jpg:
                return None
            import cv2
            import numpy as np
            arr = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            return cv2.cvtColor(arr, cv2.COLOR_BGR2RGB) if arr is not None else None
        mp4 = record_mp4(_grab, seconds=float(seconds), fps=int(fps))
        if not mp4 or not upload_conf:
            return ""
        try:
            from cloud_creds import iot_credentials, upload_mp4_to_s3
            c = upload_conf
            creds = iot_credentials(c["certs_dir"], c["credentials_endpoint"],
                                    c["s3_role_alias"], c["thing_name"])
            url = upload_mp4_to_s3(creds, c["images_bucket"], c["region"],
                                   did, conversation_id, mp4, label="drone")
            videos.append(url)
            return url
        except Exception as e:
            print(f"[{did}] record_video upload failed: {e}", flush=True)
            return ""

    def send_video(seconds=5.0, **kw):
        return record_video(seconds=seconds)

    def record_vantage(name="overhead", seconds=5.0, fps=15):
        """Record an mp4 from a fixed vantage camera and return an S3 URL."""
        from video_record import record_mp4
        # Ensure the vantage exists (engine auto-places overhead)
        if name == "overhead":
            engine.auto_overhead(name)
        def _grab():
            jpg = engine.grab_vantage_jpeg(name)
            if not jpg:
                return None
            import cv2
            import numpy as np
            arr = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            return cv2.cvtColor(arr, cv2.COLOR_BGR2RGB) if arr is not None else None
        mp4 = record_mp4(_grab, seconds=float(seconds), fps=int(fps))
        if not mp4 or not upload_conf:
            return ""
        try:
            from cloud_creds import iot_credentials, upload_mp4_to_s3
            c = upload_conf
            creds = iot_credentials(c["certs_dir"], c["credentials_endpoint"],
                                    c["s3_role_alias"], c["thing_name"])
            url = upload_mp4_to_s3(creds, c["images_bucket"], c["region"],
                                   did, conversation_id, mp4, label=name)
            videos.append(url)
            return url
        except Exception as e:
            print(f"[{did}] record_vantage upload failed: {e}", flush=True)
            return ""

    def survey_rect(forward_m, right_m, origin_forward_m=0.0, origin_right_m=0.0,
                    spacing_m=1.0, altitude_m=None, settle_s=0.5):
        """One photo over the centre of every spacing_m cell of a rectangle in
        the vehicle's own frame; mirrors drone_sdk.survey_rect."""
        import search_patterns
        p = _pos()
        # Engine yaw is maths-convention (0 = east, counter-clockwise);
        # photo_grid wants a compass heading.
        heading = 90.0 - math.degrees(_yaw())
        alt = ROVER_ALT if is_rover else (float(altitude_m) if altitude_m is not None else float(p[2]))
        rel, used = search_patterns.capped_photo_grid(
            (float(forward_m), float(right_m)), (float(origin_forward_m), float(origin_right_m)),
            float(spacing_m or 1.0), heading_deg=heading)
        print(f"Survey: {len(rel)} photos, one per {used:g} m cell")
        urls = []
        for e, n in rel:
            _goto_xyz(float(p[0]) + e, float(p[1]) + n, alt)
            time.sleep(settle_s)
            u = capture_photo()
            if u:
                urls.append(u)
        return urls

    ns = {
        "arm": arm, "safe_disarm": safe_disarm, "wait": wait,
        "survey_rect": survey_rect,
        "get_position": get_position, "get_attitude": get_attitude,
        "set_velocity": set_velocity, "set_yaw": set_yaw,
        "capture_photo": capture_photo, "look_around": look_around,
        "take_photo": capture_photo, "photo": capture_photo,
        "take_picture": capture_photo, "snapshot": capture_photo,
        "start_ceiling_guard": lambda *a, **k: None,
        "stop_ceiling_guard": lambda *a, **k: None,
        "get_ceiling_distance": lambda *a, **k: None,
        "record_video": record_video, "send_video": send_video,
        "record_vantage": record_vantage,
        "CONVERSATION_ID": conversation_id,
        "home_lat": HOME_LAT, "home_lon": HOME_LON, "home_alt": 0.0,
        "math": math,
    }

    if is_rover:
        def move_forward(distance_m=1.0, *_, **__):
            y, p = _yaw(), _pos()
            _goto_xyz(p[0] + math.cos(y) * float(distance_m),
                      p[1] + math.sin(y) * float(distance_m), ROVER_ALT)

        def move_backward(distance_m=1.0, *_, **__):
            move_forward(-float(distance_m))

        def drive(speed_mps=0.5, duration_sec=2.0, *_, **__):
            y = _yaw()
            set_velocity(speed_mps * math.cos(y), speed_mps * math.sin(y), 0.0)

        def turn(angle_deg=90, *_, **__):
            set_yaw(angle_deg, relative=True)

        def goto(lat, lon, *_):
            x, y = latlon_to_xy(lat, lon)
            _goto_xyz(x, y, ROVER_ALT)

        def return_home(*_, **__):
            _goto_xyz(0.0, 0.0, ROVER_ALT)
            return True

        ns.update({"drive": drive, "turn": turn, "goto": goto, "return_home": return_home,
                   "move_forward": move_forward, "move_backward": move_backward,
                   "forward": move_forward, "go_forward": move_forward,
                   "move": move_forward, "backward": move_backward,
                   "turn_left": lambda a=90, *x, **k: turn(abs(float(a))),
                   "turn_right": lambda a=90, *x, **k: turn(-abs(float(a))),
                   "takeoff": lambda *a, **k: None, "land": lambda *a, **k: None})
    else:
        def takeoff(altitude_m=2.0, *_, **__):
            arm()
            p = _pos()
            _goto_xyz(float(p[0]), float(p[1]), float(altitude_m))

        def land(*_, **__):
            p = _pos()
            _goto_xyz(float(p[0]), float(p[1]), 0.15)
            safe_disarm()

        def goto(lat, lon, alt=2.0):
            x, y = latlon_to_xy(lat, lon)
            _goto_xyz(x, y, float(alt))

        def return_home(alt_m=None, *_, **__):
            z = float(alt_m) if alt_m is not None else float(_pos()[2])
            _goto_xyz(0.0, 0.0, z)
            return True

        ns.update({"takeoff": takeoff, "land": land, "goto": goto, "return_home": return_home,
                   "motor_test": lambda *a, **k: time.sleep(1),
                   "move_forward": lambda d=1.0, *a, **k: _goto_xyz(
                       _pos()[0] + math.cos(_yaw()) * float(d),
                       _pos()[1] + math.sin(_yaw()) * float(d), _pos()[2])})
    return ns, images, videos


def run_command(engine, did, vtype, conversation_id, code, upload_conf, armed) -> dict:
    t0 = time.time()
    ns, images, videos = build_namespace(engine, did, vtype, conversation_id, upload_conf, armed)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code, f"<{did}>", "exec"), ns, ns)  # noqa: S102
        result = {"success": True, "error": None}
    except Exception as e:
        traceback.print_exc()
        result = {"success": False, "error": f"{type(e).__name__}: {e}"}
    result["stdout"] = buf.getvalue()
    result["image_urls"] = list(images)
    result["video_urls"] = list(videos)
    result["elapsed_s"] = round(time.time() - t0, 1)
    return result

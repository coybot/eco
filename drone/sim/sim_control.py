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
        """Record mp4 from this drone's camera and upload to S3.

        For rovers (which are typically stationary when called): performs a slow
        180° yaw pan during recording so the video shows motion and sweeps the
        scene — a static parked rover is useless as a video.
        For quadcopters already in flight: no pan, they're moving naturally.
        """
        import threading
        import cv2
        import numpy as np
        from video_record import record_frames, encode_mp4

        stop_evt = threading.Event()

        def _pan():
            """Rotate 180° over the recording window (rover only)."""
            if not is_rover:
                return
            steps     = max(6, int(float(seconds) * 1.5))
            step_deg  = 180.0 / steps
            step_secs = float(seconds) / steps
            for _ in range(steps):
                if stop_evt.is_set():
                    break
                set_yaw(step_deg, relative=True)
                time.sleep(max(0.05, step_secs - 0.15))

        pan_t = threading.Thread(target=_pan, daemon=True)
        pan_t.start()

        def _grab():
            jpg = engine.grab_jpeg(did)
            if not jpg:
                return None
            arr = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            return cv2.cvtColor(arr, cv2.COLOR_BGR2RGB) if arr is not None else None

        frames = record_frames(_grab, seconds=float(seconds), fps=int(fps))
        stop_evt.set()
        pan_t.join(timeout=5)
        mp4 = encode_mp4(frames, fps=int(fps))
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
        """Record mp4 from a pre-created vantage camera and return an S3 URL.

        The overhead and side cameras exist from engine startup, so frames are
        available immediately. Falls back to auto-create if somehow missing.
        """
        from video_record import record_mp4
        import cv2, numpy as np
        # Verify camera exists; create if missing
        known = engine.list_vantages()
        if name not in known:
            engine.auto_overhead(name)
            time.sleep(1.0)  # let the FrameBus warm up with a few ticks
        def _grab():
            jpg = engine.grab_vantage_jpeg(name)
            if not jpg:
                return None
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

    ns = {
        "arm": arm, "safe_disarm": safe_disarm, "wait": wait,
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

        ns.update({"drive": drive, "turn": turn, "goto": goto,
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

        ns.update({"takeoff": takeoff, "land": land, "goto": goto,
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

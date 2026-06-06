"""Fleet worker: one Isaac thread driving many vehicles + per-drone SDK.

`FleetWorker` is the sole owner of the Isaac world (GPU thread): it continuously
integrates every vehicle toward its goal, steps/renders once per tick, grabs
camera frames for *watched* vehicles (live video) and serves one-shot frame
requests (capture_photo).

Per-drone commands run on their OWN threads (spawned by fleet_mqtt), never on the
Isaac thread. Their SDK functions only set thread-safe pose targets and read
state snapshots — blocking moves are done by polling state while the worker moves
the vehicle, so all drones move simultaneously. capture_photo requests a frame
from the worker and uploads it to S3 (IRL parity).
"""

from __future__ import annotations

import math
import queue
import threading
import time

import numpy as np

from isaac_vehicle import IsaacVehicleBridge
from sim_sdk import FrameBus, latlon_to_xy, xy_to_latlon, HOME_LAT, HOME_LON


class FleetWorker(threading.Thread):
    STEPS_PER_TICK = 4
    PHYSICS_DT = 1.0 / 240.0
    REACHED = 0.2  # meters

    def __init__(self, environment: str, roster: list[dict], headless: bool = True,
                 photoreal: bool = False):
        super().__init__(name="fleet-worker", daemon=True)
        self.environment = environment
        self.roster = roster
        self.headless = headless
        self.photoreal = photoreal
        self.bridge = IsaacVehicleBridge(headless=headless, physics_dt=self.PHYSICS_DT)
        self.ready = threading.Event()
        self._running = True
        self._frame_reqs: "queue.Queue[tuple]" = queue.Queue()
        self._isaac_ops: "queue.Queue[tuple]" = queue.Queue()  # vantage add/grab etc.
        self._watched: dict[str, FrameBus] = {}
        self._watch_lock = threading.Lock()

    # -- lifecycle --------------------------------------------------------------
    def run(self) -> None:
        self.bridge.setup(self.environment, self.roster)
        if self.photoreal:
            from ishmael.assets import set_rtx_path_tracing
            set_rtx_path_tracing(True)
            print("[fleet-worker] RTX path-tracing ON (photoreal mode)", flush=True)
        self.ready.set()
        print(f"[fleet-worker] ready: {len(self.roster)} vehicles in "
              f"{self.environment}", flush=True)
        while self._running:
            # one-shot frame requests (capture_photo)
            try:
                while True:
                    drone_id, holder, ev = self._frame_reqs.get_nowait()
                    holder["rgb"] = self.bridge.grab_frame(drone_id)
                    ev.set()
            except queue.Empty:
                pass
            # arbitrary Isaac-thread ops (add/grab vantage cameras)
            try:
                while True:
                    fn, holder, ev = self._isaac_ops.get_nowait()
                    try:
                        holder["r"] = fn()
                    except Exception as e:  # surface, don't kill the loop
                        holder["r"] = None
                        print(f"[fleet-worker] isaac op failed: {e}", flush=True)
                    ev.set()
            except queue.Empty:
                pass
            # continuous frames for watched vehicles (live video)
            with self._watch_lock:
                watched = list(self._watched.items())
            for drone_id, bus in watched:
                rgb = self.bridge.grab_frame(drone_id)
                if rgb is not None:
                    bus.set(rgb)
            self.bridge.step_simulation(self.STEPS_PER_TICK, render=True)
            # Yield the GIL so the awscrt MQTT event loop can invoke our Python
            # subscribe callbacks. Without this the tight loop starves message
            # delivery (the connection appears to go "deaf" after one message).
            time.sleep(0.01)

    def stop(self):
        self._running = False

    # -- frame access (called from other threads) -------------------------------
    def request_frame(self, drone_id: str, timeout: float = 5.0):
        holder, ev = {}, threading.Event()
        self._frame_reqs.put((drone_id, holder, ev))
        ev.wait(timeout=timeout)
        return holder.get("rgb")

    def _run_on_isaac(self, fn, timeout: float = 10.0):
        holder, ev = {}, threading.Event()
        self._isaac_ops.put((fn, holder, ev))
        ev.wait(timeout=timeout)
        return holder.get("r")

    def add_vantage(self, name, position, look_at) -> None:
        self._run_on_isaac(
            lambda: self.bridge.add_vantage_camera(name, position, look_at))

    def request_vantage_frame(self, name, timeout: float = 5.0):
        return self._run_on_isaac(lambda: self.bridge.grab_vantage_frame(name), timeout)

    def auto_overhead_vantage(self, name: str = "overhead"):
        """Add (once) an overhead camera framing all vehicles. Returns its name."""
        def _add():
            import numpy as _np
            center, radius = self.bridge.scene_center_and_extent()
            pos = center + _np.array([0.0, 0.0, max(8.0, radius * 1.5)])
            self.bridge.add_vantage_camera(name, pos, center)
            return True
        self._run_on_isaac(_add)
        return name

    def watch(self, drone_id: str) -> FrameBus:
        with self._watch_lock:
            if drone_id not in self._watched:
                self._watched[drone_id] = FrameBus()
            return self._watched[drone_id]

    def unwatch(self, drone_id: str) -> None:
        with self._watch_lock:
            self._watched.pop(drone_id, None)


def build_vehicle_namespace(worker: FleetWorker, drone_id: str,
                            conversation_id: str, upload_conf: dict | None) -> dict:
    """SDK namespace bound to one vehicle. Runs on a per-command thread."""
    bridge = worker.bridge
    veh = bridge.vehicles[drone_id]
    is_rover = veh.vtype == "rover"
    ROVER_ALT = 0.0
    images: list[str] = []   # S3 urls collected by capture_photo (also via look_around)
    videos: list[str] = []   # S3 urls collected by record_video / record_vantage

    def _pos():
        return bridge.get_drone_state(drone_id)["position"]

    def _yaw():
        return bridge.get_drone_state(drone_id)["yaw"]

    def _goto_xyz(x, y, z, timeout=30.0):
        bridge.set_drone_goal(drone_id, np.array([x, y, z]))
        deadline = time.time() + timeout
        target = np.array([x, y, z])
        while time.time() < deadline:
            if np.linalg.norm(_pos() - target) < worker.REACHED:
                break
            time.sleep(0.1)

    def _set_yaw_rad(target, timeout=10.0):
        bridge.set_drone_yaw(drone_id, target)
        deadline = time.time() + timeout
        while time.time() < deadline:
            dy = abs((target - _yaw() + math.pi) % (2 * math.pi) - math.pi)
            if dy < math.radians(2):
                break
            time.sleep(0.1)

    def arm():
        with veh.lock:
            veh.armed = True

    def safe_disarm():
        with veh.lock:
            veh.armed = False

    def wait(seconds):
        time.sleep(min(float(seconds), 30.0))

    def get_position():
        p = _pos()
        lat, lon = xy_to_latlon(float(p[0]), float(p[1]))
        return (lat, lon, float(p[2]))

    def get_attitude():
        return (0.0, 0.0, math.degrees(_yaw()))

    def set_velocity(vx, vy, vz=0.0):
        bridge.set_drone_velocity(drone_id, np.array([float(vx), float(vy), float(vz)]))
        time.sleep(2.0)
        bridge.clear_velocity(drone_id)

    def set_yaw(angle_deg, relative=False):
        cur = _yaw()
        target = cur + math.radians(angle_deg) if relative else math.radians(angle_deg)
        _set_yaw_rad(target)

    def capture_photo(description="", upload=True, **kw):
        import cv2
        from cloud_creds import iot_credentials, upload_jpeg_to_s3
        rgb = worker.request_frame(drone_id)
        if rgb is None:
            return ""
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if not upload_conf:
            return "sim://image"
        ok, jpg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return ""
        try:
            c = upload_conf
            creds = iot_credentials(c["certs_dir"], c["credentials_endpoint"],
                                    c["s3_role_alias"], c["thing_name"])
            url = upload_jpeg_to_s3(creds, c["images_bucket"], c["region"],
                                    drone_id, conversation_id, jpg.tobytes())
            images.append(url)
            return url
        except Exception as e:
            print(f"[{drone_id}] capture_photo upload failed: {e}", flush=True)
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
        """Record mp4 from this drone's camera and return an S3 URL."""
        from video_record import record_mp4
        def _grab():
            return worker.request_frame(drone_id)
        mp4 = record_mp4(_grab, seconds=float(seconds), fps=int(fps))
        if not mp4 or not upload_conf:
            return ""
        try:
            import cv2
            from cloud_creds import iot_credentials, upload_mp4_to_s3
            c = upload_conf
            creds = iot_credentials(c["certs_dir"], c["credentials_endpoint"],
                                    c["s3_role_alias"], c["thing_name"])
            url = upload_mp4_to_s3(creds, c["images_bucket"], c["region"],
                                   drone_id, conversation_id, mp4, label="drone")
            videos.append(url)
            return url
        except Exception as e:
            print(f"[{drone_id}] record_video upload failed: {e}", flush=True)
            return ""

    def send_video(seconds=5.0, **kw):
        return record_video(seconds=seconds)

    def record_vantage(name="overhead", seconds=5.0, fps=15):
        """Record mp4 from a fixed vantage camera and return an S3 URL."""
        from video_record import record_mp4
        if name == "overhead":
            worker.auto_overhead_vantage(name)
        def _grab():
            return worker.request_vantage_frame(name)
        mp4 = record_mp4(_grab, seconds=float(seconds), fps=int(fps))
        if not mp4 or not upload_conf:
            return ""
        try:
            from cloud_creds import iot_credentials, upload_mp4_to_s3
            c = upload_conf
            creds = iot_credentials(c["certs_dir"], c["credentials_endpoint"],
                                    c["s3_role_alias"], c["thing_name"])
            url = upload_mp4_to_s3(creds, c["images_bucket"], c["region"],
                                   drone_id, conversation_id, mp4, label=name)
            videos.append(url)
            return url
        except Exception as e:
            print(f"[{drone_id}] record_vantage upload failed: {e}", flush=True)
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
        "math": math, "np": np,
    }

    if is_rover:
        def move_forward(distance_m=1.0, *_, **__):
            y = _yaw(); p = _pos()
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
                       _pos()[1] + math.sin(_yaw()) * float(d),
                       _pos()[2])})
    return ns, images, videos


def run_command(worker: FleetWorker, drone_id: str, conversation_id: str,
                code: str, upload_conf: dict | None) -> dict:
    """Exec generated code for one vehicle (on a per-command thread)."""
    import contextlib
    import io
    import traceback
    t0 = time.time()
    ns, images, videos = build_vehicle_namespace(worker, drone_id, conversation_id, upload_conf)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code, f"<{drone_id}>", "exec"), ns, ns)  # noqa: S102
        result = {"success": True, "error": None}
    except Exception as e:
        traceback.print_exc()
        result = {"success": False, "error": f"{type(e).__name__}: {e}"}
    result["stdout"] = buf.getvalue()
    result["image_urls"] = list(images)
    result["video_urls"] = list(videos)
    result["elapsed_s"] = round(time.time() - t0, 1)
    return result

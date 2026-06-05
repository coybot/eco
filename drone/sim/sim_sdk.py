"""Eco sim SDK + single-vehicle Isaac Sim worker.

This module bridges the cloud-generated drone SDK code (see
``eco/aws/src/handler.py:SYSTEM_PROMPT`` and
``eco/aws/src/conversations.py:CODE_SYSTEM_PROMPT``) onto a single vehicle
running in Isaac Sim via the existing multi-drone ``IsaacSimBridge``
(``ishmael/swarm_eval/harness/isaac_sim_bridge.py``).

Concurrency model
-----------------
Isaac Sim must be driven from one thread. ``SimWorker`` is that thread: it owns
the ``IsaacSimBridge``, continuously ticks the simulation, serves the latest RGB
frame into a thread-safe ``FrameBus`` (consumed by the WebRTC video producer),
and executes generated code submitted from the HTTP server thread.

SDK functions never touch Isaac directly — they run *inside* the worker thread
(because the worker executes the submitted code) and step the world through the
worker's ``tick`` helper, so all sim access stays single-threaded.
"""

from __future__ import annotations

import io
import math
import queue
import threading
import time
import base64
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# --- GPS <-> local ENU mapping -------------------------------------------------
# The sim world is metric/local (x=east, y=north, z=up). The cloud talks GPS, so
# we anchor an arbitrary home and convert. The prompt's "+0.00001 deg ~= 1.1m"
# convention falls out of this (1 deg lat ~= 111320 m).
HOME_LAT = 37.0
HOME_LON = -122.0
_M_PER_DEG = 111319.9


def latlon_to_xy(lat: float, lon: float) -> tuple[float, float]:
    east = (lon - HOME_LON) * _M_PER_DEG * math.cos(math.radians(HOME_LAT))
    north = (lat - HOME_LAT) * _M_PER_DEG
    return east, north  # x, y


def xy_to_latlon(x: float, y: float) -> tuple[float, float]:
    lat = HOME_LAT + y / _M_PER_DEG
    lon = HOME_LON + x / (_M_PER_DEG * math.cos(math.radians(HOME_LAT)))
    return lat, lon


class FrameBus:
    """Thread-safe holder for the latest RGB frame (H, W, 3) uint8."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None

    def set(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame

    def get(self) -> Optional[np.ndarray]:
        with self._lock:
            return None if self._frame is None else self._frame.copy()


@dataclass
class _Job:
    code: str
    conversation_id: str
    done: threading.Event = field(default_factory=threading.Event)
    result: dict = field(default_factory=dict)


class SimWorker(threading.Thread):
    """Owns the Isaac Sim bridge; the only thread that touches the simulator."""

    # physics steps advanced per tick (one tick ~= 1/60s sim time at dt=1/240)
    STEPS_PER_TICK = 4
    PHYSICS_DT = 1.0 / 240.0
    DRONE_ID = 0
    REACHED_THRESHOLD = 0.6  # meters

    def __init__(self, environment: str, vehicle_type: str = "quadcopter",
                 headless: bool = True, upload_conf: dict | None = None):
        super().__init__(name="sim-worker", daemon=True)
        self.environment = environment
        self.vehicle_type = vehicle_type
        self.headless = headless
        # upload_conf: {certs_dir, credentials_endpoint, s3_role_alias,
        # images_bucket, region, thing_name, drone_id} — when set, capture_photo
        # uploads to S3 and returns a URL (IRL parity); else returns base64.
        self.upload_conf = upload_conf
        self.frame_bus = FrameBus()
        self.ready = threading.Event()
        self._jobs: "queue.Queue[_Job]" = queue.Queue()
        self._running = True
        self.bridge = None
        self._armed = False
        self._collected_images: list[dict] = []
        self._cur_conversation_id = "sim"

    # -- lifecycle --------------------------------------------------------------
    def run(self) -> None:
        # Import here so the module is importable without Isaac (e.g. on a Mac).
        from isaac_vehicle import IsaacVehicleBridge

        self.bridge = IsaacVehicleBridge(headless=self.headless,
                                         physics_dt=self.PHYSICS_DT,
                                         vehicle_type=self.vehicle_type)
        # Single vehicle spawned just above the floor at the local origin.
        spawn = [np.array([0.0, 0.0, 0.5])]
        self.bridge.setup(self.environment, num_drones=1, spawn_positions=spawn)
        self.ready.set()
        print(f"[sim-worker] ready: env={self.environment} "
              f"vehicle={self.vehicle_type}", flush=True)

        while self._running:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                self.tick()  # idle: keep physics + video frames live
                continue
            self._run_job(job)

    def stop(self) -> None:
        self._running = False

    # -- ticking / frame capture ------------------------------------------------
    def tick(self, render: bool = True) -> None:
        """Advance the sim a little and refresh the video frame."""
        self.bridge.step_simulation(self.STEPS_PER_TICK, render=render)
        self._grab_frame()

    def _grab_frame(self) -> None:
        try:
            cam = self.bridge._drones[self.DRONE_ID].camera
            if cam is None:
                return
            rgba = cam.get_rgba()
            if rgba is not None and rgba.size:
                self.frame_bus.set(np.ascontiguousarray(rgba[:, :, :3]))
        except Exception:
            pass

    def state(self) -> dict:
        return self.bridge.get_drone_state(self.DRONE_ID)

    # -- job execution ----------------------------------------------------------
    def submit(self, code: str, conversation_id: str, timeout: float = 180.0) -> dict:
        job = _Job(code=code, conversation_id=conversation_id)
        self._jobs.put(job)
        if not job.done.wait(timeout=timeout):
            return {"success": False, "error": "Sim execution timed out",
                    "images": [], "elapsed_s": timeout}
        return job.result

    def _run_job(self, job: _Job) -> None:
        import contextlib
        import io as _io
        t0 = time.time()
        self._collected_images = []
        self._cur_conversation_id = job.conversation_id
        ns = build_sdk_namespace(self, job.conversation_id)
        buf = _io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                exec(compile(job.code, "<sim_command>", "exec"), ns, ns)  # noqa: S102
            job.result = {"success": True, "error": None}
        except Exception as e:  # surface to the app via the cloud
            import traceback
            traceback.print_exc()
            job.result = {"success": False, "error": f"{type(e).__name__}: {e}"}
        job.result["stdout"] = buf.getvalue()
        job.result["images"] = list(self._collected_images)          # HTTP-debug shape
        job.result["image_urls"] = [i["url"] for i in self._collected_images
                                    if i.get("url") and i.get("media_type") != "video"]
        job.result["video_urls"] = [i["url"] for i in self._collected_images
                                    if i.get("url") and i.get("media_type") == "video"]
        job.result["elapsed_s"] = round(time.time() - t0, 1)
        job.done.set()

    # -- primitives used by the SDK (run on worker thread) ----------------------
    def _tick_for(self, seconds: float) -> None:
        ticks = max(1, int(seconds / (self.STEPS_PER_TICK * self.PHYSICS_DT)))
        for _ in range(ticks):
            self.tick()

    def _goto_xyz(self, x: float, y: float, z: float, timeout: float = 30.0) -> None:
        self.bridge.set_drone_goal(self.DRONE_ID, np.array([x, y, z]))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.tick()
            pos = self.state()["position"]
            if np.linalg.norm(pos - np.array([x, y, z])) < self.REACHED_THRESHOLD:
                break

    def add_image(self, description: str = "") -> str:
        """Capture the current frame.

        With upload_conf set (the MQTT/IRL path), upload a JPEG to S3 and return
        its URL — so the cloud's response carries image_urls exactly like a real
        drone. Without it (HTTP-debug), keep a base64 PNG in the reply.
        """
        import cv2
        frame = self.frame_bus.get()
        if frame is None:
            self._grab_frame()
            frame = self.frame_bus.get()
        if frame is None:
            return ""
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        if self.upload_conf:
            try:
                from cloud_creds import iot_credentials, upload_jpeg_to_s3
                ok, jpg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not ok:
                    return ""
                c = self.upload_conf
                creds = iot_credentials(c["certs_dir"], c["credentials_endpoint"],
                                        c["s3_role_alias"], c["thing_name"])
                url = upload_jpeg_to_s3(creds, c["images_bucket"], c["region"],
                                        c["drone_id"], self._cur_conversation_id,
                                        jpg.tobytes())
                self._collected_images.append({"url": url, "description": description})
                return url
            except Exception as e:
                print(f"[capture_photo] S3 upload failed: {e}")
                return ""

        ok, buf = cv2.imencode(".png", bgr)
        if not ok:
            return ""
        b64 = base64.b64encode(buf.tobytes()).decode()
        self._collected_images.append({"b64": b64, "description": description})
        return f"sim://image/{len(self._collected_images)}"


def build_sdk_namespace(worker: SimWorker, conversation_id: str) -> dict:
    """Construct the exec namespace exposing the SDK surface the cloud generates."""
    is_rover = worker.vehicle_type == "rover"
    # Rover approximation (phase 1): drive the same vehicle at a fixed low
    # altitude using horizontal velocity + yaw. Replaced by a wheeled asset later.
    ROVER_ALT = 0.5

    def _pos_xyz():
        return worker.state()["position"]

    # --- shared ---
    def arm():
        worker._armed = True

    def safe_disarm():
        worker._armed = False

    def wait(seconds):
        worker._tick_for(float(seconds))

    def get_position():
        p = _pos_xyz()
        lat, lon = xy_to_latlon(float(p[0]), float(p[1]))
        return (lat, lon, float(p[2]))

    def get_attitude():
        st = worker.state()
        return (0.0, 0.0, math.degrees(st["yaw"]))

    def set_velocity(vx, vy, vz=0.0):
        worker.bridge.set_drone_velocity(worker.DRONE_ID,
                                         np.array([float(vx), float(vy), float(vz)]))
        worker._tick_for(2.0)

    def set_yaw(angle_deg, relative=False):
        cur = worker.state()["yaw"]
        target = cur + math.radians(angle_deg) if relative else math.radians(angle_deg)
        worker.bridge.set_drone_yaw(worker.DRONE_ID, target)
        worker._tick_for(2.0)

    def capture_photo(description="", upload=True, **kwargs):
        # Tolerate the cloud's call shapes: capture_photo(), capture_photo(upload=True),
        # capture_photo("a description"). upload is implicit (always S3 in sim).
        return worker.add_image(description if isinstance(description, str) else "")

    def look_around(directions=4):
        urls = []
        step = 360.0 / max(1, directions)
        for _ in range(directions):
            set_yaw(step, relative=True)
            urls.append(capture_photo())
        return urls

    def record_video(seconds=5.0, description="", fps=15):
        """Record an mp4 from this drone's camera and return a URL."""
        from video_record import record_mp4
        mp4 = record_mp4(worker.frame_bus.get, seconds=float(seconds), fps=int(fps))
        if not mp4:
            return ""
        if worker.upload_conf:
            try:
                import cv2  # noqa: F401 (needed by cloud_creds path)
                from cloud_creds import iot_credentials, upload_mp4_to_s3
                c = worker.upload_conf
                creds = iot_credentials(c["certs_dir"], c["credentials_endpoint"],
                                        c["s3_role_alias"], c["thing_name"])
                url = upload_mp4_to_s3(creds, c["images_bucket"], c["region"],
                                       c["drone_id"], worker._cur_conversation_id,
                                       mp4, label="drone")
                worker._collected_images.append({"url": url, "description": description,
                                                  "media_type": "video"})
                return url
            except Exception as e:
                print(f"[record_video] S3 upload failed: {e}")
                return ""
        import base64 as _b64
        b64 = _b64.b64encode(mp4).decode()
        worker._collected_images.append({"b64_video": b64, "description": description})
        return f"sim://video/{len(worker._collected_images)}"

    def send_video(seconds=5.0, **kw):
        return record_video(seconds=seconds)

    # no-ops kept so generated code referencing them doesn't crash
    def start_ceiling_guard(min_clearance=0.5):
        return None

    def stop_ceiling_guard():
        return None

    def get_ceiling_distance():
        return None

    ns = {
        "arm": arm, "safe_disarm": safe_disarm, "wait": wait,
        "get_position": get_position, "get_attitude": get_attitude,
        "set_velocity": set_velocity, "set_yaw": set_yaw,
        "capture_photo": capture_photo, "look_around": look_around,
        "record_video": record_video, "send_video": send_video,
        # aliases — Bedrock occasionally emits these instead of capture_photo
        "take_photo": capture_photo, "photo": capture_photo,
        "take_picture": capture_photo, "snapshot": capture_photo,
        "start_ceiling_guard": start_ceiling_guard,
        "stop_ceiling_guard": stop_ceiling_guard,
        "get_ceiling_distance": get_ceiling_distance,
        "CONVERSATION_ID": conversation_id,
        "home_lat": HOME_LAT, "home_lon": HOME_LON, "home_alt": 0.0,
        # math/np available for generated relative-position arithmetic
        "math": math, "np": np,
    }

    if is_rover:
        def drive(speed_mps, duration_sec=2.0):
            yaw = worker.state()["yaw"]
            vx, vy = speed_mps * math.cos(yaw), speed_mps * math.sin(yaw)
            worker.bridge.set_drone_velocity(worker.DRONE_ID,
                                             np.array([vx, vy, 0.0]))
            worker._tick_for(float(duration_sec))

        def turn(angle_deg, speed_rad_s=0.5):
            set_yaw(angle_deg, relative=True)

        def goto(lat, lon, *_):
            x, y = latlon_to_xy(lat, lon)
            worker._goto_xyz(x, y, ROVER_ALT)

        def move_forward(distance_m=1.0, *_, **__):
            # drive a precise distance along the current heading (goal-based)
            yaw = worker.state()["yaw"]
            p = worker.state()["position"]
            tx = p[0] + math.cos(yaw) * float(distance_m)
            ty = p[1] + math.sin(yaw) * float(distance_m)
            worker._goto_xyz(tx, ty, ROVER_ALT)

        def move_backward(distance_m=1.0, *_, **__):
            move_forward(-float(distance_m))

        def turn_left(angle_deg=90, *_, **__):
            turn(abs(float(angle_deg)))

        def turn_right(angle_deg=90, *_, **__):
            turn(-abs(float(angle_deg)))

        # aliases — Bedrock's quad-centric prompt improvises rover verbs
        ns.update({
            "drive": drive, "turn": turn, "goto": goto,
            "move_forward": move_forward, "move_backward": move_backward,
            "forward": move_forward, "go_forward": move_forward,
            "move": move_forward, "backward": move_backward,
            "turn_left": turn_left, "turn_right": turn_right,
            # quad verbs are no-ops on a rover (tolerate stray takeoff/land)
            "takeoff": lambda *a, **k: None, "land": lambda *a, **k: None,
        })
    else:
        def takeoff(altitude_m):
            arm()
            p = _pos_xyz()
            worker._goto_xyz(float(p[0]), float(p[1]), float(altitude_m))

        def land():
            p = _pos_xyz()
            worker._goto_xyz(float(p[0]), float(p[1]), 0.15)
            worker._armed = False

        def goto(lat, lon, alt):
            x, y = latlon_to_xy(lat, lon)
            worker._goto_xyz(x, y, float(alt))

        def motor_test(motor_num=None, throttle_pct=15, duration_sec=2):
            worker._tick_for(float(duration_sec))

        ns.update({"takeoff": takeoff, "land": land, "goto": goto,
                   "motor_test": motor_test})

    return ns

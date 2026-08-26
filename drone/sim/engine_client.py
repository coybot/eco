"""Thin IPC client to the sim engine (Unix socket or TCP, newline-JSON).

Used by each per-drone daemon process to control its vehicle and grab camera
frames from the shared sim world. Keeps the daemon free of engine/GPU and of
the GIL contention that killed many MQTT connections in one process.

addr forms accepted by __init__:
  "/tmp/sim_engine.sock"   -- Unix socket (legacy / socat proxy)
  "tcp://127.0.0.1:9999"   -- direct TCP to Godot IPC server
"""

from __future__ import annotations

import base64
import json
import socket
import threading


class EngineClient:
    def __init__(self, addr: str = "/tmp/sim_engine.sock"):
        # addr can be a Unix socket path or a tcp://host:port string
        self._addr = addr
        self._is_tcp = addr.startswith("tcp://")
        self._lock = threading.Lock()
        self._connect()

    def _connect(self):
        if self._is_tcp:
            _, _, hostport = self._addr.partition("://")
            host, _, port = hostport.rpartition(":")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((host, int(port)))
        else:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(self._addr)
        self._sock = s
        self._f = s.makefile("rwb")

    def _call(self, req: dict) -> dict:
        with self._lock:
            try:
                self._f.write((json.dumps(req) + "\n").encode())
                self._f.flush()
                line = self._f.readline()
            except (BrokenPipeError, OSError):
                self._connect()
                self._f.write((json.dumps(req) + "\n").encode())
                self._f.flush()
                line = self._f.readline()
        if not line:
            return {"ok": False, "error": "no response"}
        return json.loads(line)

    def state(self, did: str) -> dict:
        r = self._call({"op": "get_state", "id": did})
        return {"position": r.get("position", [0, 0, 0]), "yaw": r.get("yaw", 0.0)}

    def camera_pose(self, did: str) -> dict:
        """ENU camera position + forward/up unit vectors for GT-box projection
        (godot_dataset_recorder.py). Read from the live Camera3D transform
        server-side, not recomputed from FleetManager's per-environment offset
        table client-side -- see fleet_manager.gd's get_camera_pose docstring."""
        r = self._call({"op": "get_camera_pose", "id": did})
        return {"position": r.get("position", [0, 0, 0]),
                "forward": r.get("forward", [1, 0, 0]),
                "up": r.get("up", [0, 0, 1])}

    def set_goal(self, did, xyz):
        self._call({"op": "set_goal", "id": did, "p": list(xyz)})

    def set_velocity(self, did, v):
        self._call({"op": "set_velocity", "id": did, "v": list(v)})

    def clear_velocity(self, did):
        self._call({"op": "clear_velocity", "id": did})

    def set_yaw(self, did, yaw_rad):
        self._call({"op": "set_yaw", "id": did, "yaw": float(yaw_rad)})

    def grab_jpeg(self, did) -> bytes | None:
        r = self._call({"op": "grab_frame", "id": did})
        j = r.get("jpg")
        return base64.b64decode(j) if j else None

    # -- vantage cameras (fixed world viewpoints) -------------------------------
    def add_vantage(self, name, position, look_at):
        self._call({"op": "add_vantage", "name": name,
                    "p": list(position), "look": list(look_at)})

    def auto_overhead(self, name="overhead") -> str:
        r = self._call({"op": "auto_overhead", "name": name})
        return r.get("name", name)

    def grab_vantage_jpeg(self, name) -> bytes | None:
        r = self._call({"op": "grab_vantage", "name": name})
        j = r.get("jpg")
        return base64.b64decode(j) if j else None

    # -- runtime reconfigure (hot session start without relaunching Godot) ------
    def spawn(self, did, vtype, position=None):
        req = {"op": "spawn", "id": did, "vtype": vtype}
        if position is not None:
            req["p"] = list(position)
        self._call(req)

    def despawn(self, did):
        self._call({"op": "despawn", "id": did})

    def load_env(self, env: str) -> str:
        r = self._call({"op": "load_env", "env": env})
        return r.get("env", env)

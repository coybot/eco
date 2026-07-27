"""Newline-JSON TCP client for the Depot Godot sim (PhroverManager IPC ops).

Connects directly to Godot's IPC server (see eco/drone/sim/godot/scripts/ipc_server.gd)
over TCP — no Unix-socket proxy needed for the Phrover sim harness, unlike the
per-drone-daemon fleet path in eco/drone/sim/engine_client.py.

All phrover ops speak the 2D ENU ground frame: x=east metres, y=north metres,
yaw radians CCW from +x (east). Units: metres, radians, m/s.
"""

from __future__ import annotations

import base64
import json
import socket
import threading


class DepotClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9999):
        self._sock = socket.create_connection((host, port), timeout=10)
        self._f = self._sock.makefile("rwb")
        self._lock = threading.Lock()

    def close(self) -> None:
        self._sock.close()

    def _call(self, req: dict) -> dict:
        with self._lock:
            self._f.write((json.dumps(req) + "\n").encode())
            self._f.flush()
            line = self._f.readline()
        if not line:
            return {"ok": False, "error": "no response"}
        return json.loads(line)

    # -- environment --------------------------------------------------------
    def reset(self, seed: int = 0) -> dict:
        return self._call({"op": "reset", "seed": seed})

    def inject(self, name: str, **params) -> dict:
        return self._call({"op": "inject", "name": name, "params": params})

    def get_events(self, since: float = 0.0) -> list[dict]:
        r = self._call({"op": "get_events", "since": since})
        return r.get("events", [])

    # -- phrover lifecycle ----------------------------------------------------
    def spawn(self, rid: str, pos: tuple[float, float], yaw: float = 0.0) -> dict:
        return self._call({"op": "phrover_spawn", "id": rid, "p": list(pos), "yaw": yaw})

    def despawn(self, rid: str) -> dict:
        return self._call({"op": "phrover_despawn", "id": rid})

    # -- phrover sense/act ----------------------------------------------------
    def state(self, rid: str) -> dict:
        return self._call({"op": "phrover_state", "id": rid})

    def detect(self, rid: str) -> list[dict]:
        r = self._call({"op": "phrover_detect", "id": rid})
        return r.get("objects", [])

    def unproject(self, rid: str, nx: float, ny: float) -> list[float] | None:
        r = self._call({"op": "phrover_unproject", "id": rid, "nx": nx, "ny": ny})
        return r.get("world")

    def grid(self, rid: str) -> dict | None:
        r = self._call({"op": "phrover_grid", "id": rid})
        if not r.get("ok"):
            return None
        occ = base64.b64decode(r["occ"])
        obs = base64.b64decode(r["obs"])
        return {"res": r["res"], "origin": r["origin"], "w": r["w"], "h": r["h"],
                "occ": occ, "obs": obs}

    def drive(self, rid: str, v: float, w: float) -> dict:
        return self._call({"op": "phrover_drive", "id": rid, "v": v, "w": w})

    def stop(self, rid: str) -> dict:
        return self._call({"op": "phrover_stop", "id": rid})

    # -- vantage cameras (fixed viewpoints, shared with FleetManager) --------
    def add_vantage(self, name: str, pos_enu: tuple[float, float, float], look_enu: tuple[float, float, float]) -> dict:
        return self._call({"op": "add_vantage", "name": name, "p": list(pos_enu), "look": list(look_enu)})

    def grab_vantage(self, name: str) -> bytes | None:
        r = self._call({"op": "grab_vantage", "name": name})
        jpg = r.get("jpg")
        return base64.b64decode(jpg) if jpg else None

    def remove_vantage(self, name: str) -> dict:
        return self._call({"op": "remove_vantage", "name": name})

    def move_vantage(self, name: str, pos_enu: tuple[float, float, float], look_enu: tuple[float, float, float]) -> dict:
        """Repositions an existing vantage in place (no SubViewport recreation) —
        for a chase cam that tracks a moving vehicle instead of a fixed high
        overhead shot that reduces the vehicle to a barely-visible dot."""
        return self._call({"op": "move_vantage", "name": name, "p": list(pos_enu), "look": list(look_enu)})

    # -- fixedwing lifecycle --------------------------------------------------
    def fw_spawn(self, rid: str, pos_enu: tuple[float, float, float], yaw: float = 0.0) -> dict:
        return self._call({"op": "fw_spawn", "id": rid, "p": list(pos_enu), "yaw": yaw})

    def fw_despawn(self, rid: str) -> dict:
        return self._call({"op": "fw_despawn", "id": rid})

    # -- fixedwing sense/act --------------------------------------------------
    def fw_state(self, rid: str) -> dict:
        return self._call({"op": "fw_state", "id": rid})

    def fw_detect(self, rid: str) -> list[dict]:
        r = self._call({"op": "fw_detect", "id": rid})
        return r.get("objects", [])

    def fw_grab_frame(self, rid: str) -> bytes | None:
        """Forward-camera JPEG for the on-device VLM loop — see
        backends.SimBackend.capture_frame. Requires gui=True (headless Godot's
        dummy renderer leaves the SubViewport texture blank)."""
        r = self._call({"op": "fw_grab_frame", "id": rid})
        jpg = r.get("jpg")
        return base64.b64decode(jpg) if jpg else None

    def fw_grid(self, rid: str) -> dict | None:
        r = self._call({"op": "fw_grid", "id": rid})
        if not r.get("ok"):
            return None
        return r.get("grid", {})

    def fw_unproject(self, rid: str, nx: float, ny: float) -> list[float] | None:
        r = self._call({"op": "fw_unproject", "id": rid, "nx": nx, "ny": ny})
        return r.get("world")

    def fw_drive(self, rid: str, airspeed: float, yaw_rate: float,
                 climb: float = 0.0) -> dict:
        return self._call({"op": "fw_drive", "id": rid, "airspeed": airspeed,
                           "yaw_rate": yaw_rate, "climb": climb})

    def fw_stop(self, rid: str) -> dict:
        return self._call({"op": "fw_stop", "id": rid})

    def fw_reset_camera(self, rid: str) -> dict:
        """Rebuild the forward-camera SubViewport after a frozen render target.

        See FixedWingManager.reset_camera — the capture can freeze permanently
        once the VLM starts using the GPU. Let a frame pass before the next
        fw_grab_frame, since the new viewport starts empty.
        """
        return self._call({"op": "fw_reset_camera", "id": rid})

    def fw_events(self, rid: str) -> list[dict]:
        r = self._call({"op": "fw_events", "id": rid})
        return r.get("events", [])

    def fw_reset(self, rid: str) -> dict:
        return self._call({"op": "fw_reset", "id": rid})

    def fw_all_states(self) -> list[dict]:
        """ENU pose of every live fixed-wing (id, position, yaw, altitude)."""
        r = self._call({"op": "fw_all_states"})
        return r.get("states", [])

    def fw_env_state(self) -> dict:
        """Scenario-specific scene truth from the loaded environment.

        Lets a harness assert on what the scene's actors are actually doing
        (which phase the target is in, where it really is) rather than
        inferring it from the drone's own detections — the same role
        fw_prop_truth plays for static props.
        """
        r = self._call({"op": "fw_env_state"})
        return r.get("env", {})

    def fw_prop_truth(self) -> list[dict]:
        """Ground-truth prop list (label, world pos, is_anomaly) — harness/scoring
        only, never fed to the agent's own sensing (use fw_detect for that)."""
        r = self._call({"op": "fw_prop_truth"})
        return r.get("props", [])

    def fw_log_event(self, rid: str, kind: str, data: dict) -> dict:
        """Push a structured event (clarification/replan/memory_landmark) into
        FixedWingManager's own event log — see backends.SimBackend.log_event."""
        return self._call({"op": "fw_log_event", "id": rid, "kind": kind, "data": data})

    def fw_inject(self, name: str, **params) -> dict:
        """FixedWingManager's own inject (e.g. raise_wall) — NOT the same as
        inject(), which is hard-wired to PhroverManager only."""
        return self._call({"op": "fw_inject", "name": name, "params": params})

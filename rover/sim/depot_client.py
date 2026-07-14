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

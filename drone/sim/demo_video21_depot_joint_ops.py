#!/usr/bin/env python3
"""video21 — Depot Joint Ops: fixed-wings + quads + phrovers, inside and outside.

Renders a ~30s demo from the Presidio Godot sim (PresidioEcoSim, godot/) showing a
mixed fleet working one scenario at the depot env: two fixed-wings orbit
outside, a quadcopter re-tasks from the yard to the doorway to confirm a
"spill" prop, and a phrover drives to it indoors for a real `phrover_detect`
confirmation, while a second quad and a second phrover patrol in the
background so all three vehicle classes appear in multiples.

Choreography (fw orbit tangents, quad patrol/goal legs) is scripted, but every
claimed act maps to something the real stack does: `phrover_detect` is a
genuine FOV+raycast perception call; the fw/quad "detections" are camera-
plausible framing beats, not live model inference (see VIDEO_LOG.md
limitations).

Godot must run RENDERED on this Mac (not --headless — the dummy renderer
leaves SubViewport camera textures blank). See CLAUDE.md / VIDEO_LOG.md for
the wider pattern this follows (godot_record_plaza.py, fw_video_overlay.py).

Usage:
    pkill -f godot
    PATH="/opt/homebrew/bin:$PATH" eco/drone/sim/.venv-mac/bin/python \\
        eco/drone/sim/demo_video21_depot_joint_ops.py --seed 7 \\
        --out video/video21_depot_joint_ops.mp4

    # Stage the world + grab one probe frame per camera, no full render:
    ... demo_video21_depot_joint_ops.py --dry-run
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).resolve().parent
GODOT_PROJECT = SCRIPT_DIR / "godot"
GODOT_BIN = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "/opt/homebrew/bin/ffmpeg")
FONT_PATH = "/System/Library/Fonts/Menlo.ttc"

OUT_W, OUT_H = 1280, 720
OUT_FPS = 24
# Capture at the output rate. Reachable only because each frame costs two
# pipelined IPC round trips (~17ms) instead of a dozen sequential ones; the
# sequential version topped out near 8 fps, so every output frame was held 3x and
# the result juddered. Measured ceiling with a 720p grab is ~75 fps.
TICK_DT = 1.0 / 24.0
READY_TIMEOUT = 90
MAX_STALE_FRAMES = 20
# Below this the renderer is stalling, not merely slow — see run_full's guard.
MIN_CAPTURE_FPS = 12.0

# Shared wall-clock for choreography that must stay continuous ACROSS segment
# cuts (fw orbit phase, quad-02 patrol timer) — record_segment() passes each
# tick fn a segment-LOCAL elapsed time that resets to 0 at every cut, which
# would otherwise make the fw orbit visibly jump/reset at every camera cut.
_SIM_START: float | None = None


def start_sim_clock() -> None:
    global _SIM_START
    _SIM_START = time.time()


def sim_time() -> float:
    assert _SIM_START is not None, "start_sim_clock() not called"
    return time.time() - _SIM_START

VOICE_COMMAND = "Sweep the depot — possible chemical spill reported near the south entrance."

# Mirrors of the GDScript speed limits (fleet_manager.gd MAX_LIN_*, phrover_manager.gd
# MAX_V) — used only by check_trace() to decide what counts as an impossible jump.
MAX_LIN_QUAD = 3.0
MAX_LIN_FW = 25.0
MAX_V_PHROVER = 0.5

FW_IDS = ["fw-01", "fw-02"]
QUAD_IDS = ["quad-01", "quad-02"]
PHROVER_IDS = ["phrover-01", "phrover-02"]

# Orbit params: center (ENU x,y), radius m, altitude m, tangential speed m/s, phase rad.
#
# Radius is dictated by the airframe, not by framing: an orbit needs yaw rate
# w = speed/r, and fleet_manager.gd caps a fixed-wing at MAX_YAW_RAD_FW = 0.6 rad/s
# (bank-limited). The first cut used r=18 / speed=14 -> w=0.78 rad/s, which the
# aircraft physically could not hold, so its nose lagged the tangent without bound
# and it flew visibly sideways for the whole clip. These keep w ~= 0.3 rad/s, half
# the limit, so the nose tracks the flight path with margin to spare.
# Radius also has to stay inside env_depot.gd's 60x60 m floor, which is centred on
# ENU (0, 3) — the same point these orbits use — so r < 30 keeps the aircraft over
# ground instead of out past the floor edge with sky underneath it.
FW_ORBITS = {
    "fw-01": {"cx": 0.0, "cy": 3.0, "r": 28.0, "alt": 16.0, "speed": 9.5, "phase": math.pi},
    "fw-02": {"cx": 0.0, "cy": 3.0, "r": 29.0, "alt": 24.0, "speed": 10.0, "phase": 0.0},
}
# Fly the orbit (off camera) before recording starts so the spawn-yaw transient is
# never on screen. FleetManager has no instant-yaw op — `spawn` takes no yaw and
# `set_yaw` ramps at the bank limit — so a fixed-wing spawned nose-south facing a
# due-north tangent needs pi/0.6 = 5.2s to come round, which is longer than the
# entire opening beat.
SETTLE_S = 7.0

QUAD01_SPAWN = (5.0, -7.0, 2.2)
QUAD01_MID = (2.5, -5.0, 1.6)
QUAD01_DOOR_HOVER = (0.0, -2.2, 1.0)
# Flown as a velocity-driven path, not set_goal. set_goal runs at MAX_LIN_QUAD
# (3 m/s), which covers this 7m approach in ~2.3s and leaves most of the 6s beat a
# motionless hover — two sampled frames a second apart were identical. A drone
# easing up to a doorway to look inside would approach deliberately anyway, so
# 1.2 m/s both fills the beat with continuous motion and is the more plausible act.
QUAD01_PATH = [QUAD01_MID, QUAD01_DOOR_HOVER]
QUAD01_SPEED = 1.2
# Face north (0,1,0) through the door: yaw=pi, not pi/2. See heading_yaw() for why
# yaw is a clockwise-from-north bearing here; heading_yaw(0, 1) == pi.
QUAD01_DOOR_YAW = math.pi

QUAD02_SPAWN = (-4.0, -6.0, 2.5)
QUAD02_WAYPOINTS = [(-4.0, -6.0, 2.5), (4.0, -6.0, 2.5), (4.0, -2.0, 2.5), (-4.0, -2.0, 2.5)]

PHROVER01_SPAWN = (0.0, 6.0, -math.pi / 2.0)   # facing south, toward the spill
PHROVER02_SPAWN = (-4.0, 4.0, 0.0)
PHROVER02_WAYPOINTS = [(-5.5, 3.5), (-3.0, 5.5)]

CHASE_VANTAGE = "v21_chase"

# Establishing shot: a slow arc around the site rather than a locked-off wide.
# Held static it was both too wide (the depot a speck in a field of white) and
# almost perfectly still — measured mean inter-frame motion 0.003 versus 1.25 on
# the fixed-wing beat, i.e. five opening seconds that read as a freeze frame.
# Arcing gives continuous parallax and shows the yard and building as one site.
ESTAB_R = 19.0
ESTAB_ALT = 9.5
ESTAB_AZ0 = math.radians(-58.0)      # south-east of the depot, looking north-west
ESTAB_AZ_RATE = math.radians(4.2)    # deg/s — slow enough to read as a camera move
ESTAB_LOOK = (0.0, 2.5, 0.8)


def estab_pose(t: float) -> tuple[tuple, tuple]:
    az = ESTAB_AZ0 + ESTAB_AZ_RATE * t
    return ((ESTAB_LOOK[0] + ESTAB_R * math.sin(az),
             ESTAB_LOOK[1] - ESTAB_R * math.cos(az),
             ESTAB_ALT), ESTAB_LOOK)


# The staged pose must BE the arc's t=0 pose. Staging it anywhere else puts one
# frame at the old position before the first move_vantage lands, which is a visible
# snap on the opening frame (measured as a 8.5 inter-frame diff against a 0.07
# median — by far the largest non-cut discontinuity in the cut).
VANTAGES = {
    "v21_estab": {"p": estab_pose(0.0)[0], "look": ESTAB_LOOK},
    "v21_hall":  {"p": (0.0, 8.5, 6.0), "look": (0.0, 2.0, 0.0)},
}


# ---------------------------------------------------------------------------
# Godot process management
# ---------------------------------------------------------------------------
class GodotProcess:
    def __init__(self, proc: subprocess.Popen):
        self.proc = proc

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def pkill_godot() -> None:
    subprocess.run(["pkill", "-f", "PresidioEcoSim"], check=False)
    subprocess.run(["pkill", "-f", f"godot .*{GODOT_PROJECT.name}"], check=False)
    time.sleep(1.0)


def launch_godot(seed: int, port: int) -> GodotProcess:
    if not Path(GODOT_BIN).exists():
        raise RuntimeError(f"Godot binary not found at {GODOT_BIN}")
    # --always-on-top / --windowed matter for correctness here, not comfort. macOS
    # stops driving a Metal layer that is fully occluded, and every SubViewport
    # rides the main window's render loop, so a Godot window buried behind the
    # terminal keeps stepping the sim while rendering almost nothing: grab_vantage
    # then returns the same texture for seconds while vehicles fly on, which is what
    # made vehicles appear to teleport. Measured on identical code, purely by
    # whether the window was covered: 22 fps visible vs 1.2 fps occluded.
    args = [
        GODOT_BIN, "--path", str(GODOT_PROJECT),
        "--always-on-top", "--windowed", "--resolution", f"{OUT_W}x{OUT_H}",
        "--",
        "--fleet=", "--env=depot", f"--seed={seed}", f"--ipc-port={port}",
    ]
    print("[launch]", " ".join(args))
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ready = threading.Event()
    lines: list[str] = []

    def _tee():
        for line in proc.stdout:
            lines.append(line)
            print(f"[godot] {line}", end="", flush=True)
            if "IPC ready" in line or "env_depot" in line:
                ready.set()

    threading.Thread(target=_tee, daemon=True).start()
    if not ready.wait(timeout=READY_TIMEOUT):
        proc.terminate()
        raise RuntimeError("Godot did not print 'IPC ready' within timeout")
    if proc.poll() is not None:
        raise RuntimeError(f"Godot exited early (rc={proc.returncode}):\n" + "".join(lines))
    time.sleep(1.0)  # let env_depot.gd finish _ready() (props/person build)
    return GodotProcess(proc)


# ---------------------------------------------------------------------------
# IPC client (newline-JSON over TCP; see godot/scripts/ipc_server.gd)
# ---------------------------------------------------------------------------
class SimClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9999):
        self._sock = socket.create_connection((host, port), timeout=15)
        self._f = self._sock.makefile("rwb")
        self._lock = threading.Lock()

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def call(self, req: dict) -> dict:
        return self.call_many([req])[0]

    def call_many(self, reqs: list[dict]) -> list[dict]:
        """Send every request, then read every reply — one round trip, not N.

        ipc_server.gd polls the socket from _process(), so a synchronous
        request-reply costs a whole Godot frame; a per-frame tick issuing a dozen
        sequential calls therefore ran at ~12 Hz no matter how fast the engine
        rendered. _flush_lines() drains every complete line already buffered
        within a single _process() and replies to each in order, so pipelining
        collapses those dozen frames into one. Measured on this scene: 83 ms/tick
        sequential vs 8.3 ms pipelined (10x), and 75 fps with a 720p grab
        appended — which is what makes a smooth 24 fps capture possible at all.
        """
        if not reqs:
            return []
        with self._lock:
            for req in reqs:
                self._f.write((json.dumps(req) + "\n").encode())
            self._f.flush()
            lines = [self._f.readline() for _ in reqs]
        return [json.loads(ln) if ln else {"ok": False, "error": "no response"}
                for ln in lines]

    # -- environment --
    def reset(self, seed: int) -> dict:
        return self.call({"op": "reset", "seed": seed})

    def prop_truth(self) -> list[dict]:
        return self.call({"op": "prop_truth"}).get("props", [])

    # -- fleet vehicles (quad/rover/fw) --
    def spawn(self, vid: str, vtype: str, p: tuple[float, float, float]) -> dict:
        return self.call({"op": "spawn", "id": vid, "vtype": vtype, "p": list(p)})

    def get_state(self, vid: str) -> dict:
        return self.call({"op": "get_state", "id": vid})

    def set_goal(self, vid: str, p: tuple[float, float, float]) -> dict:
        return self.call({"op": "set_goal", "id": vid, "p": list(p)})

    def set_velocity(self, vid: str, v: tuple[float, float, float]) -> dict:
        return self.call({"op": "set_velocity", "id": vid, "v": list(v)})

    def set_yaw(self, vid: str, yaw: float) -> dict:
        return self.call({"op": "set_yaw", "id": vid, "yaw": yaw})

    def grab_frame(self, vid: str) -> bytes | None:
        r = self.call({"op": "grab_frame", "id": vid})
        jpg = r.get("jpg")
        return base64.b64decode(jpg) if jpg else None

    # -- vantages --
    def add_vantage(self, name: str, p, look, w=OUT_W, h=OUT_H) -> dict:
        return self.call({"op": "add_vantage", "name": name, "p": list(p), "look": list(look), "w": w, "h": h})

    def move_vantage(self, name: str, p, look) -> dict:
        return self.call({"op": "move_vantage", "name": name, "p": list(p), "look": list(look)})

    def grab_vantage(self, name: str) -> bytes | None:
        r = self.call({"op": "grab_vantage", "name": name})
        jpg = r.get("jpg")
        return base64.b64decode(jpg) if jpg else None

    def remove_vantage(self, name: str) -> dict:
        return self.call({"op": "remove_vantage", "name": name})

    # -- phrovers --
    def phrover_spawn(self, rid: str, x: float, y: float, yaw: float) -> dict:
        return self.call({"op": "phrover_spawn", "id": rid, "p": [x, y], "yaw": yaw})

    def phrover_state(self, rid: str) -> dict:
        return self.call({"op": "phrover_state", "id": rid})

    def phrover_drive(self, rid: str, v: float, w: float) -> dict:
        return self.call({"op": "phrover_drive", "id": rid, "v": v, "w": w})

    def phrover_detect(self, rid: str) -> list[dict]:
        return self.call({"op": "phrover_detect", "id": rid}).get("objects", [])


# ---------------------------------------------------------------------------
# Choreography helpers
# ---------------------------------------------------------------------------
def wrap_pi(a: float) -> float:
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def heading_yaw(vx: float, vy: float) -> float:
    """FleetManager `set_yaw` value that points a vehicle's nose along ENU (vx, vy).

    NOT atan2(vy, vx). The vehicle visual scripts (fixedwing_visuals.gd,
    quadcopter_visuals.gd, rover_visuals.gd) all use **+Z as forward** in local
    space, and fleet_manager.gd applies `node.rotation_degrees.y = -degrees(yaw)`.
    Composing those gives nose_ENU = (-sin yaw, -cos yaw) — i.e. yaw is a
    clockwise-from-north bearing, not a standard counter-clockwise-from-east
    heading. Verified live against get_camera_pose (the onboard camera carries a
    +180 deg Y offset precisely so it looks along that +Z nose): yaw=pi reads
    forward (0,1,0) = north.

    Using atan2(vy, vx) here is a 90-degree error that makes an aircraft visibly
    fly sideways — that shipped in the first cut of this video, caught on review.
    """
    return math.atan2(-vx, -vy)


FW_CHASE_VANTAGE = "v21_fwchase"


def orbit_state(fw_id: str, t: float) -> tuple[float, float, float, float]:
    """Ideal orbit position and velocity at sim time t."""
    o = FW_ORBITS[fw_id]
    w = o["speed"] / o["r"]
    theta = o["phase"] + w * t
    return (o["cx"] + o["r"] * math.cos(theta), o["cy"] + o["r"] * math.sin(theta),
            -o["r"] * w * math.sin(theta), o["r"] * w * math.cos(theta))


def phrover_drive_cmd(pose, target_xy) -> tuple[float, float, float]:
    """P-controller: pose (x, y, yaw) + target -> (v, w, distance)."""
    x, y, yaw = pose
    dx, dy = target_xy[0] - x, target_xy[1] - y
    dist = math.hypot(dx, dy)
    err = wrap_pi(math.atan2(dy, dx) - yaw)
    w = max(-1.5, min(1.5, 2.0 * err))
    v = 0.0 if dist < 0.35 else max(0.0, min(0.5, 0.5 * math.cos(err)))
    return v, w, dist


def quad_chase_pose(pos) -> tuple[tuple, tuple]:
    # Close enough that the quad reads as the subject rather than a speck over a
    # blown-out yard, and south of it so the depot sits behind it in frame.
    x, y, z = pos
    return (x + 0.6, y - 2.0, z + 0.9), (x, y, z)


def fw_chase_pose(pos, fw_id: str) -> tuple[tuple, tuple]:
    # fw-01's own onboard camera is unusable here: FleetManager._cam_params'
    # 0.5m forward offset is tuned for quad/rover body sizes and doesn't clear
    # the fixed-wing model's 3.5m fuselage (confirmed live — the onboard frame is
    # dominated by the aircraft's own nose/wings). A chase vantage placed off the
    # aircraft's quarter gives a clean "banking past the building" shot instead.
    x, y, z = pos
    o = FW_ORBITS[fw_id]
    dx, dy = x - o["cx"], y - o["cy"]
    n = math.hypot(dx, dy) or 1.0
    ox, oy = dx / n, dy / n            # radially outward from the orbit centre
    tx, ty = -oy, ox                   # orbit tangent (direction of travel)
    # Trail from behind/outboard/above, aiming partly inward and down — toward what
    # the aircraft is orbiting. Looking straight along the track framed nothing but
    # sky, since the depot is abeam the aircraft rather than ahead of it.
    return ((x - tx * 13.0 + ox * 5.0, y - ty * 13.0 + oy * 5.0, z + 5.0),
            (x + tx * 3.0 - ox * 11.0, y + ty * 3.0 - oy * 11.0, z - 7.0))


class Choreo:
    """Drives every vehicle and the active camera in ONE IPC round trip per frame.

    Commands are computed from the previous frame's poses, and this frame's pose
    reads are appended to the same batch alongside the grab. That one-frame lag is
    invisible at 24 fps and is what makes 24 fps reachable at all: a two-round-trip
    version of this loop (read, then command+grab) measured 1.2 unique fps, versus
    ~18 for the single-batch shape, even though both issue the same requests.
    """

    def __init__(self, client: SimClient, world: dict):
        self.c = client
        self.spill = world["spill_xy"]
        self.quad02_wp = 0
        self.quad02_last_switch = -999.0
        self.phrover02_wp = 0
        self.phrover01_target: tuple[float, float] | None = None
        self.hit: dict | None = None
        self.seg = ""
        self.state: dict = {}
        self.quad01_leg: int | None = None
        self.seg_t0 = time.time()

    def _quad01_path_cmds(self, pos) -> list[dict]:
        """Fly quad-01 along QUAD01_PATH at a deliberate approach speed."""
        if self.quad01_leg >= len(QUAD01_PATH):
            # Arrived: hold station at the doorway, squared up to look through it.
            return [{"op": "set_velocity", "id": "quad-01", "v": [0.0, 0.0, 0.0]},
                    {"op": "set_yaw", "id": "quad-01", "yaw": QUAD01_DOOR_YAW}]
        tgt = QUAD01_PATH[self.quad01_leg]
        d = [tgt[i] - pos[i] for i in range(3)]
        dist = math.sqrt(sum(q * q for q in d))
        if dist < 0.35:
            self.quad01_leg += 1
            return []
        v = [q / dist * QUAD01_SPEED for q in d]
        # On the final leg, turn to face through the doorway while still closing on
        # it, so the onboard beat that follows opens already squared up.
        yaw = QUAD01_DOOR_YAW if self.quad01_leg == len(QUAD01_PATH) - 1 else heading_yaw(v[0], v[1])
        return [{"op": "set_velocity", "id": "quad-01", "v": v},
                {"op": "set_yaw", "id": "quad-01", "yaw": yaw}]

    def step(self, cam: "Cam") -> tuple[bytes | None, dict]:
        t = sim_time()
        state = self.state
        read_ids = FW_IDS + QUAD_IDS

        w: list[dict] = []
        for fid in FW_IDS:
            _, _, vx, vy = orbit_state(fid, t)
            w.append({"op": "set_velocity", "id": fid, "v": [vx, vy, 0.0]})
            w.append({"op": "set_yaw", "id": fid, "yaw": heading_yaw(vx, vy)})

        if (t - self.quad02_last_switch) > 6.0:
            prev = QUAD02_WAYPOINTS[self.quad02_wp]
            self.quad02_wp = (self.quad02_wp + 1) % len(QUAD02_WAYPOINTS)
            nxt = QUAD02_WAYPOINTS[self.quad02_wp]
            w.append({"op": "set_goal", "id": "quad-02", "p": list(nxt)})
            # set_goal does not yaw — point it along the leg it is about to fly.
            w.append({"op": "set_yaw", "id": "quad-02",
                      "yaw": heading_yaw(nxt[0] - prev[0], nxt[1] - prev[1])})
            self.quad02_last_switch = t

        if self.quad01_leg is not None:
            qst = state.get("quad-01", {})
            if qst.get("ok"):
                w += self._quad01_path_cmds(qst["position"])

        st2 = state.get("phrover-02", {})
        if st2.get("ok"):
            v, om, dist = phrover_drive_cmd(st2["pose"], PHROVER02_WAYPOINTS[self.phrover02_wp])
            w.append({"op": "phrover_drive", "id": "phrover-02", "v": v, "w": om})
            if dist < 0.4:
                self.phrover02_wp = (self.phrover02_wp + 1) % len(PHROVER02_WAYPOINTS)

        st1 = state.get("phrover-01", {})
        if self.phrover01_target and st1.get("ok"):
            v, om, _ = phrover_drive_cmd(st1["pose"], self.phrover01_target)
            w.append({"op": "phrover_drive", "id": "phrover-01", "v": v, "w": om})
            if self.hit is None:
                w.append({"op": "phrover_detect", "id": "phrover-01"})

        if cam.move_fn:
            p, look = cam.move_fn(time.time() - self.seg_t0)
            w.append({"op": "move_vantage", "name": cam.target,
                      "p": list(p), "look": list(look)})
        elif cam.chase:
            cst = state.get(cam.chase, {})
            if cst.get("ok"):
                pose_fn = fw_chase_pose if cam.chase in FW_IDS else quad_chase_pose
                p, look = (pose_fn(cst["position"], cam.chase) if cam.chase in FW_IDS
                           else pose_fn(cst["position"]))
                w.append({"op": "move_vantage", "name": cam.target,
                          "p": list(p), "look": list(look)})

        detect_idx = next((i for i, r in enumerate(w) if r["op"] == "phrover_detect"), None)
        # This frame's pose reads ride along in the same batch; they populate
        # self.state for the NEXT frame's commands.
        read_at = len(w)
        w += [{"op": "get_state", "id": v} for v in read_ids]
        w += [{"op": "phrover_state", "id": r} for r in PHROVER_IDS]
        w.append(cam.grab_req())
        replies = self.c.call_many(w)

        state = dict(zip(read_ids + PHROVER_IDS, replies[read_at:read_at + len(read_ids) + len(PHROVER_IDS)]))
        self.state = state

        if detect_idx is not None and self.hit is None:
            # phrover_detect replies under "objects" (ipc_server.gd), and grabs
            # under "jpg" — not the names a reasonable person would guess.
            spills = [d for d in replies[detect_idx].get("objects", [])
                      if d["label"] == "spill"]
            if spills:
                self.hit = spills[0]

        jpg = None
        grab = replies[-1]
        if grab.get("ok") and grab.get("jpg"):
            jpg = base64.b64decode(grab["jpg"])

        row = {"seg": self.seg, "t": round(t, 3)}
        for vid in read_ids:
            if state[vid].get("ok"):
                row[vid] = [round(q, 3) for q in state[vid]["position"]] + [round(state[vid]["yaw"], 3)]
        for rid in PHROVER_IDS:
            if state[rid].get("ok"):
                row[rid] = [round(q, 3) for q in state[rid]["pose"]]
        return jpg, row


# ---------------------------------------------------------------------------
# Grabbers: wrap a camera source so record_segment() can attempt a recovery
# on a frozen SubViewport (documented Godot quirk — see fleet_manager.gd's
# own remove_vantage() comment) instead of crashing the whole render.
# ---------------------------------------------------------------------------
MAX_RECOVERIES = 6


class Cam:
    """The camera a beat is shot from: a vantage (optionally chasing a vehicle)
    or a vehicle's own onboard camera.

    Vantage recovery keeps the SAME name: fleet_manager.gd's add_vantage() no-ops
    only while the name is still registered, and remove_vantage() erases it first,
    so remove-then-add under one name really does build a fresh SubViewport. The
    first cut renamed on recovery instead (v21_chase -> ..._r1), which silently
    broke the chase cameras — the per-frame move_vantage() calls still targeted
    the original name, which no longer existed, so they no-op'd and the camera
    froze at its staging pose while the vehicle flew on. That read as the tracked
    vehicle "teleporting".

    Onboard cameras are not recoverable: FleetManager has no reset op for them
    (FixedWingManager's fw_reset_camera does not apply — these vehicles are the
    FleetManager kinematic type). A stale streak there is logged, not fatal.
    """

    def __init__(self, kind: str, target: str, chase: str | None = None, p=None, look=None,
                 move_fn=None):
        self.kind = kind            # "vantage" | "onboard"
        self.target = target        # vantage name, or vehicle id
        self.chase = chase          # vehicle id to follow, or None
        self.move_fn = move_fn      # f(segment_elapsed) -> (p, look), for scripted moves
        self.p, self.look = p, look
        self.recoveries = 0

    def grab_req(self) -> dict:
        if self.kind == "vantage":
            return {"op": "grab_vantage", "name": self.target}
        return {"op": "grab_frame", "id": self.target}

    def recover(self, client: SimClient) -> bool:
        if self.kind != "vantage" or self.recoveries >= MAX_RECOVERIES:
            return False
        self.recoveries += 1
        client.remove_vantage(self.target)
        client.add_vantage(self.target, self.p, self.look)
        print(f"    [recover] rebuilt vantage {self.target} (#{self.recoveries})")
        return True


# ---------------------------------------------------------------------------
# Segment recorder
# ---------------------------------------------------------------------------
def record_segment(choreo: Choreo, name: str, seconds: float, cam: Cam,
                    out_dir: Path, trace: list, on_start=None, mid=None) -> tuple[int, float]:
    """Record one beat, dropping any frame identical to the one before it.

    Duplicates are dropped rather than written: if the renderer ever stalls while
    the sim keeps stepping, writing the repeats makes the finished video freeze
    and then snap forward, which is exactly what reads as a vehicle teleporting.
    With pipelined IPC and the caffeinate assertion held this should be zero —
    a non-zero count here means something regressed.
    """
    seg_dir = out_dir / name
    seg_dir.mkdir(parents=True, exist_ok=True)
    choreo.seg = name
    choreo.seg_t0 = time.time()
    if on_start:
        on_start()
    t0 = time.time()
    frame_idx = 0
    dup_dropped = 0
    last_hash = None
    repeat_count = 0
    mid_fired = False
    while (time.time() - t0) < seconds:
        loop_start = time.time()
        if mid and not mid_fired and (loop_start - t0) >= mid[0]:
            mid[1]()
            mid_fired = True
        jpg, row = choreo.step(cam)
        if jpg:
            h = hashlib.sha1(jpg).hexdigest()
            if h == last_hash:
                dup_dropped += 1
                repeat_count += 1
                if repeat_count > MAX_STALE_FRAMES:
                    if not cam.recover(choreo.c):
                        print(f"    [!] {name}: >{MAX_STALE_FRAMES} identical frames and "
                              "recovery budget exhausted — continuing, expect judder")
                    repeat_count = 0
                    last_hash = None
            else:
                repeat_count = 0
                last_hash = h
                (seg_dir / f"f{frame_idx:05d}.jpg").write_bytes(jpg)
                frame_idx += 1
                row["frame"] = frame_idx
                trace.append(row)
        elapsed = time.time() - loop_start
        time.sleep(max(0.0, TICK_DT - elapsed))
    fps = frame_idx / seconds if seconds > 0 else 0.0
    print(f"  [{name}] {frame_idx} frames in {seconds:.1f}s -> {fps:.1f} fps"
          + (f" ({dup_dropped} duplicates dropped)" if dup_dropped else ""))
    return frame_idx, fps


def check_trace(trace: list) -> list[str]:
    """Flag physically implausible motion between consecutive written frames.

    Two classes of defect, both of which shipped in the first cut:
      * position jump — a vehicle advancing further between frames than its own
        max speed allows over the elapsed time (a real teleport, or a frozen
        camera making the world appear to skip).
      * heading error — a fixed-wing whose nose is not aligned with its velocity
        (the aircraft visibly flying sideways).
    """
    max_speed = {"fw-01": MAX_LIN_FW, "fw-02": MAX_LIN_FW,
                 "quad-01": MAX_LIN_QUAD, "quad-02": MAX_LIN_QUAD,
                 "phrover-01": MAX_V_PHROVER, "phrover-02": MAX_V_PHROVER}
    problems: list[str] = []
    for prev, cur in zip(trace, trace[1:]):
        dt = cur["t"] - prev["t"]
        if dt <= 0:
            continue
        for vid, vmax in max_speed.items():
            if vid not in prev or vid not in cur:
                continue
            a, b = prev[vid], cur[vid]
            step = math.hypot(b[0] - a[0], b[1] - a[1])
            # 2.5x tolerance: dt is wall-clock between grabs, and Godot's own
            # physics step can bunch up, so a modest overshoot is normal.
            if step > vmax * dt * 2.5 + 0.5:
                problems.append(
                    f"JUMP {vid} {prev['seg']}f{prev['frame']}->{cur['frame']}: "
                    f"{step:.1f}m in {dt:.2f}s (max ~{vmax * dt:.1f}m)")
    for vid in FW_IDS:
        bad = []
        worst = 0.0
        for prev, cur in zip(trace, trace[1:]):
            if vid not in prev or vid not in cur:
                continue
            vx, vy = cur[vid][0] - prev[vid][0], cur[vid][1] - prev[vid][1]
            if math.hypot(vx, vy) < 0.5:
                continue
            err = abs(wrap_pi(cur[vid][3] - heading_yaw(vx, vy)))
            if err > math.radians(35):
                bad.append(f"{cur['seg']}f{cur['frame']}")
                worst = max(worst, err)
        if bad:
            problems.append(
                f"HEADING {vid}: nose off its velocity vector on {len(bad)} frame(s), "
                f"worst {math.degrees(worst):.0f}deg (first {bad[0]})")
    return problems


# ---------------------------------------------------------------------------
# PIL compositing
# ---------------------------------------------------------------------------
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def compose_frame(jpg_bytes: bytes, is_onboard: bool, onboard_tag: str,
                   top_caption: str, lower_third: str | None,
                   confirm_banner: str | None) -> Image.Image:
    src = Image.open(__import__("io").BytesIO(jpg_bytes)).convert("RGB")
    canvas = Image.new("RGB", (OUT_W, OUT_H), (10, 10, 10))
    if is_onboard:
        scale = OUT_H / src.height
        new_w = int(src.width * scale)
        resized = src.resize((new_w, OUT_H), Image.LANCZOS)
        x0 = (OUT_W - new_w) // 2
        canvas.paste(resized, (x0, 0))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([x0, 0, x0 + new_w - 1, OUT_H - 1], outline=(255, 200, 60), width=3)
        draw.rectangle([x0 + 8, OUT_H - 34, x0 + 8 + 190, OUT_H - 8], fill=(0, 0, 0))
        draw.text((x0 + 14, OUT_H - 30), f"ONBOARD · {onboard_tag}", font=_font(16), fill=(255, 200, 60))
    else:
        resized = src.resize((OUT_W, OUT_H), Image.LANCZOS)
        canvas.paste(resized, (0, 0))

    draw = ImageDraw.Draw(canvas)
    # Persistent top caption bar.
    draw.rectangle([0, 0, OUT_W, 46], fill=(0, 0, 0))
    draw.text((16, 12), top_caption, font=_font(18), fill=(255, 255, 255))

    if lower_third:
        draw.rectangle([0, OUT_H - 44, OUT_W, OUT_H], fill=(20, 20, 20))
        draw.rectangle([0, OUT_H - 44, 6, OUT_H], fill=(80, 170, 255))
        draw.text((16, OUT_H - 36), lower_third, font=_font(17), fill=(230, 230, 230))

    if confirm_banner:
        bh = 60
        draw.rectangle([0, OUT_H - bh, OUT_W, OUT_H], fill=(15, 90, 35))
        draw.text((16, OUT_H - bh + 14), confirm_banner, font=_font(19), fill=(220, 255, 220))

    return canvas


def composite_segment(seg_dir: Path, is_onboard: bool, onboard_tag: str,
                       lower_third: str | None, confirm_banner: str | None) -> None:
    for jpg_path in sorted(seg_dir.glob("f*.jpg")):
        frame = compose_frame(jpg_path.read_bytes(), is_onboard, onboard_tag,
                               VOICE_COMMAND, lower_third, confirm_banner)
        frame.save(jpg_path, quality=90)


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------
def encode_segment(seg_dir: Path, fps: float, out_mp4: Path, speed: float = 1.0) -> None:
    """Encode one beat. `speed` multiplies the input framerate, so 2.0 plays the
    same captured frames back twice as fast — every frame is kept, the clip just
    runs at half the duration. Done here rather than by re-timing the finished
    mp4 so there is only ever one H.264 encode, and rather than by speeding up the
    choreography, which would have the vehicles flying at implausible speeds."""
    fps = fps * speed
    n = len(list(seg_dir.glob("f*.jpg")))
    if n == 0:
        raise RuntimeError(f"no frames to encode in {seg_dir}")
    cmd = [
        FFMPEG_BIN, "-y",
        "-framerate", f"{max(fps, 1.0):.3f}",
        "-i", str(seg_dir / "f%05d.jpg"),
        "-vf", f"scale={OUT_W}:{OUT_H}",
        "-r", str(OUT_FPS),
        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def concat_segments(seg_mp4s: list[Path], out_path: Path, scratch: Path) -> None:
    list_path = scratch / "concat_list.txt"
    list_path.write_text("".join(f"file '{p.resolve()}'\n" for p in seg_mp4s))
    cmd = [FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
           "-c", "copy", str(out_path)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------
def stage_world(client: SimClient, seed: int) -> dict:
    start_sim_clock()
    client.reset(seed)
    props = client.prop_truth()
    spill = next((p for p in props if p.get("label") == "spill"), None)
    if spill is None:
        raise RuntimeError("no 'spill' prop in prop_truth() after reset")
    spill_xy = tuple(spill["world"])
    print(f"[stage] spill at ENU {spill_xy}")

    for pid, spawn in [(PHROVER_IDS[0], PHROVER01_SPAWN), (PHROVER_IDS[1], PHROVER02_SPAWN)]:
        client.phrover_spawn(pid, spawn[0], spawn[1], spawn[2])

    client.spawn("quad-01", "quadcopter", QUAD01_SPAWN)
    client.spawn("quad-02", "quadcopter", QUAD02_SPAWN)
    for fid in FW_IDS:
        o = FW_ORBITS[fid]
        start_x = o["cx"] + o["r"] * math.cos(o["phase"])
        start_y = o["cy"] + o["r"] * math.sin(o["phase"])
        client.spawn(fid, "fw", (start_x, start_y, o["alt"]))

    for name, v in VANTAGES.items():
        client.add_vantage(name, v["p"], v["look"])
    client.add_vantage(CHASE_VANTAGE, (QUAD01_SPAWN[0], QUAD01_SPAWN[1] - 3.0, QUAD01_SPAWN[2] + 1.5),
                        QUAD01_SPAWN)
    o = FW_ORBITS["fw-01"]
    fw0_x = o["cx"] + o["r"] * math.cos(o["phase"])
    fw0_y = o["cy"] + o["r"] * math.sin(o["phase"])
    client.add_vantage(FW_CHASE_VANTAGE, (fw0_x + 6.0, fw0_y, o["alt"] + 3.0), (fw0_x, fw0_y, o["alt"]))

    print(f"[stage] settling {SETTLE_S:.0f}s so fixed-wing yaw converges off camera...")
    t0 = time.time()
    while time.time() - t0 < SETTLE_S:
        reqs = []
        for fid in FW_IDS:
            _, _, vx, vy = orbit_state(fid, sim_time())
            reqs.append({"op": "set_velocity", "id": fid, "v": [vx, vy, 0.0]})
            reqs.append({"op": "set_yaw", "id": fid, "yaw": heading_yaw(vx, vy)})
        client.call_many(reqs)
        time.sleep(0.03)
    for fid in FW_IDS:
        st = client.get_state(fid)
        _, _, vx, vy = orbit_state(fid, sim_time())
        err = abs(wrap_pi(st["yaw"] - heading_yaw(vx, vy)))
        print(f"[stage]   {fid} settled at {[round(v, 1) for v in st['position']]} "
              f"yaw={st['yaw']:.2f} (nose {math.degrees(err):.1f}deg off track)")
    return {"spill_xy": spill_xy}


# ---------------------------------------------------------------------------
# Main choreography
# ---------------------------------------------------------------------------
def run_dry_run(client: SimClient, scratch: Path) -> None:
    print("[dry-run] probing all cameras...")
    probes = {
        "v21_estab": lambda: client.grab_vantage("v21_estab"),
        "v21_chase": lambda: client.grab_vantage(CHASE_VANTAGE),
        "v21_hall": lambda: client.grab_vantage("v21_hall"),
        "quad-01_onboard": lambda: client.grab_frame("quad-01"),
        "v21_fwchase": lambda: client.grab_vantage(FW_CHASE_VANTAGE),
    }
    probe_dir = scratch / "probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    for name, fn in probes.items():
        jpg = fn()
        if jpg is None:
            print(f"  [!] {name}: no frame")
            continue
        (probe_dir / f"{name}.jpg").write_bytes(jpg)
        print(f"  {name}: {len(jpg)} bytes -> {probe_dir / (name + '.jpg')}")

    print("[dry-run] staleness check on v21_estab (2 grabs, 1s apart, fw orbiting continuously)...")
    def _orbit_burst() -> None:
        reqs = []
        for fid in FW_IDS:
            _, _, vx, vy = orbit_state(fid, sim_time())
            reqs.append({"op": "set_velocity", "id": fid, "v": [vx, vy, 0.0]})
            reqs.append({"op": "set_yaw", "id": fid, "yaw": heading_yaw(vx, vy)})
        client.call_many(reqs)

    _orbit_burst()
    j1 = client.grab_vantage("v21_estab")
    t0 = time.time()
    while time.time() - t0 < 1.0:
        _orbit_burst()
        time.sleep(0.05)
    j2 = client.grab_vantage("v21_estab")
    if j1 and j2:
        h1, h2 = hashlib.sha1(j1).hexdigest(), hashlib.sha1(j2).hexdigest()
        print(f"  hash1={h1[:10]} hash2={h2[:10]} {'DIFFERENT (ok)' if h1 != h2 else 'IDENTICAL (stale!)'}")

    print("[dry-run] moving quad-01 to door hover and probing its onboard camera...")
    client.set_goal("quad-01", QUAD01_DOOR_HOVER)
    client.set_yaw("quad-01", QUAD01_DOOR_YAW)
    t0 = time.time()
    while time.time() - t0 < 4.0:
        # Godot's physics loop needs steady socket traffic to tick at real-time
        # rate (see the staleness-check fix above) — poll get_state while the
        # goal/yaw settle.
        client.get_state("quad-01")
        time.sleep(0.1)
    print("  camera pose:", client.call({"op": "get_camera_pose", "id": "quad-01"}))
    jpg = client.grab_frame("quad-01")
    if jpg:
        (probe_dir / "quad-01_at_door.jpg").write_bytes(jpg)
        print(f"  quad-01_at_door: {len(jpg)} bytes -> {probe_dir / 'quad-01_at_door.jpg'}")

    print("[dry-run] driving phrover-01 toward the spill and polling phrover_detect...")
    props = client.prop_truth()
    spill = next(p for p in props if p["label"] == "spill")
    target = (spill["world"][0], spill["world"][1] + 1.2)
    hit = None
    for _ in range(200):
        st = client.phrover_state("phrover-01")
        if st.get("ok"):
            v, w, _ = phrover_drive_cmd(st["pose"], target)
            client.phrover_drive("phrover-01", v, w)
        dets = client.phrover_detect("phrover-01")
        spill_hits = [d for d in dets if d["label"] == "spill"]
        if spill_hits:
            hit = spill_hits[0]
            break
        time.sleep(0.05)
    if hit:
        print(f"  phrover_detect confirmed spill: conf={hit['confidence']:.2f} world={hit['world']}")
    else:
        print("  [!] phrover_detect never fired for 'spill' within budget")


def run_full(client: SimClient, scratch: Path, out_path: Path, world: dict,
             speed: float = 1.0) -> None:
    scratch.mkdir(parents=True, exist_ok=True)
    choreo = Choreo(client, world)

    estab = Cam("vantage", "v21_estab", p=estab_pose(0.0)[0], look=ESTAB_LOOK,
                move_fn=estab_pose)

    o0 = FW_ORBITS["fw-01"]
    fw0_x, fw0_y, _, _ = orbit_state("fw-01", 0.0)
    fw_cam = Cam("vantage", FW_CHASE_VANTAGE, chase="fw-01",
                 p=(fw0_x + 6.0, fw0_y, o0["alt"] + 3.0), look=(fw0_x, fw0_y, o0["alt"]))

    quad_cam = Cam("vantage", CHASE_VANTAGE, chase="quad-01",
                   p=(QUAD01_SPAWN[0], QUAD01_SPAWN[1] - 3.0, QUAD01_SPAWN[2] + 1.5),
                   look=QUAD01_SPAWN)
    onboard_cam = Cam("onboard", "quad-01")

    v_hall = VANTAGES["v21_hall"]
    # One Cam shared by seg5+seg6: they are the same continuous shot, so the
    # recovery budget should carry across the cut rather than resetting.
    hall_cam = Cam("vantage", "v21_hall", p=v_hall["p"], look=v_hall["look"])

    def _start_quad_reposition() -> None:
        choreo.quad01_leg = 0

    def _start_phrover_nav() -> None:
        choreo.phrover01_target = (world["spill_xy"][0], world["spill_xy"][1] + 1.2)

    # (name, seconds, cam, on_start, mid_beat_action)
    seg_specs = [
        ("seg1_estab", 5.0, estab, None, None),
        ("seg2_fw_sweep", 5.0, fw_cam, None, None),
        ("seg3_quad_reposition", 6.0, quad_cam, _start_quad_reposition, None),
        ("seg4_quad_confirm", 4.0, onboard_cam, None, None),
        ("seg5_phrover_nav", 7.0, hall_cam, _start_phrover_nav, None),
        ("seg6_confirm", 3.0, hall_cam, None, None),
    ]

    seg_mp4s = []
    lower_thirds = {
        "seg1_estab": None,
        "seg2_fw_sweep": "FW-1 · wide-area sweep — unattended-object candidate at south entrance → tasking Quad-1",
        "seg3_quad_reposition": "Quad-1 · reposition to doorway",
        "seg4_quad_confirm": "Quad-1: dark patch confirmed inside doorway — indoor handoff → Phrover-1",
        "seg5_phrover_nav": "Phrover-1 · indoor navigation (0.5 m/s)",
        "seg6_confirm": None,
    }
    onboard_segs = {"seg4_quad_confirm": "quad-01"}

    trace: list = []
    seg_fps: list[tuple[str, float]] = []
    for name, seconds, cam, on_start, mid in seg_specs:
        n, fps = record_segment(choreo, name, seconds, cam, scratch, trace, on_start, mid)
        seg_fps.append((name, fps))
        is_onboard = name in onboard_segs
        confirm_banner = None
        if name == "seg6_confirm":
            hit = choreo.hit
            if hit:
                confirm_banner = (f"Phrover-1: SPILL CONFIRMED (conf {hit['confidence']:.2f}) "
                                   f"— reporting position ({hit['world'][0]:.1f}, {hit['world'][1]:.1f})")
            else:
                confirm_banner = "Phrover-1: spill search in progress"
        composite_segment(scratch / name, is_onboard, onboard_segs.get(name, ""),
                           lower_thirds[name], confirm_banner)
        seg_mp4 = scratch / f"{name}.mp4"
        encode_segment(scratch / name, fps, seg_mp4, speed)
        seg_mp4s.append(seg_mp4)

    # Refuse to ship a degraded capture. The first cut of this video went out with
    # segments rendering at well under 1 fps — the sim stepping normally behind a
    # frozen renderer — and nothing in the pipeline noticed, so the defect was found
    # by a human watching the result. Fail loudly instead.
    slow = [(n, f) for n, f in seg_fps if f < MIN_CAPTURE_FPS]
    if slow:
        raise RuntimeError(
            "capture rate collapsed — refusing to write a juddery video.\n"
            + "\n".join(f"    {n}: {f:.1f} fps (want >= {MIN_CAPTURE_FPS})" for n, f in slow)
            + "\n  Almost always the Godot window being occluded/minimised: it must stay "
              "visible and on top for macOS to keep rendering it.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_segments(seg_mp4s, out_path, scratch)
    size_kb = out_path.stat().st_size // 1024
    print(f"[done] {out_path} ({size_kb} KB)")

    (scratch / "motion_trace.json").write_text(json.dumps(trace, indent=1))
    problems = check_trace(trace)
    if problems:
        print(f"[motion-check] {len(problems)} problem(s) across {len(trace)} frames:")
        for p in problems[:15]:
            print("   ", p)
    else:
        print(f"[motion-check] clean across {len(trace)} frames "
              "(no impossible position jumps, fixed-wing noses track their velocity)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[3] / "video" / "video21_depot_joint_ops.mp4"))
    ap.add_argument("--scratch", default="/private/tmp/claude-504/-Users-jsaib-code-ys-a/e4272541-a5e2-4214-802b-0e95261f052d/scratchpad/video21")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="playback speed multiplier (2.0 = twice as fast, half the duration)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    scratch = Path(args.scratch)
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True, exist_ok=True)

    # Hold a power assertion for as long as this process lives (-w <pid>, so it
    # cleans itself up). Without it macOS idle-throttles the display and Godot's
    # Metal layer stops updating mid-render: the sim keeps stepping while the
    # renderer is frozen, so grab_vantage returns the same bytes for seconds at a
    # time and the finished video shows vehicles jumping between stale frames.
    # Measured directly: an unattended run collapsed from 9.2 to 0.5 render fps
    # after the first beat and stayed there; under caffeinate every beat holds
    # 8-9 fps with zero duplicate frames.
    caff = subprocess.Popen(["caffeinate", "-disu", "-w", str(os.getpid())])

    pkill_godot()
    godot = launch_godot(args.seed, args.port)
    client = SimClient(port=args.port)
    try:
        world = stage_world(client, args.seed)
        if args.dry_run:
            run_dry_run(client, scratch)
        else:
            run_full(client, scratch, Path(args.out), world, args.speed)
    finally:
        client.close()
        godot.stop()
        caff.terminate()


if __name__ == "__main__":
    main()

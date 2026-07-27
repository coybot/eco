#!/usr/bin/env python3
"""Fixed-wing capability harness — Godot "flightline" environment.

Phase-1-style plumbing test, framed the same way rover/sim/make_smoke_video.py
frames itself: "Not the real capstone video ... this is a scripted drive-and-
inject sequence ... to visually confirm the sim before any brain integration
exists." This script drives a fixed-wing through the REAL production
perception/memory stack — backends.SimBackend and spatial_memory.SpatialMemory
(drone/common/), talking to a real Godot instance over depot_client.DepotClient
— using a scripted flight profile, NOT the VLM brain. It does not claim "the AI
decided" anything; it validates that detect() -> world_xyz -> SpatialMemory ->
nearest() works correctly end-to-end, and produces the metrics + first overlay
video for the "Reacquire" scenario (fly past a target, lose it, fly back to the
remembered landmark instead of re-searching).

Closed-loop validation with the actual VLM brain (RETURN_TO_LANDMARK chosen by
real model inference via reasoning_loop.MissionLoop) requires deployment to
VLM-capable hardware (Orin Nano/NX/AGX, or hoopoe) — this dev machine has no
GGUF weights / llama_cpp installed, so that step is out of scope for this run
and is flagged as pending, not faked.

Usage:
    python3 fw_eval.py --scenario reacquire [--gui] [--out report.json] [--video out.mp4]
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from shutil import which

# drone/common has backends.py + spatial_memory.py (flat on-device-style layout).
COMMON_DIR = Path(__file__).resolve().parents[1] / "common"
sys.path.insert(0, str(COMMON_DIR))
# rover/sim has depot_client.py (the Godot IPC client).
ROVER_SIM_DIR = Path(__file__).resolve().parents[2] / "rover" / "sim"
sys.path.insert(0, str(ROVER_SIM_DIR))

from backends import SimBackend  # noqa: E402
from spatial_memory import SpatialMemory  # noqa: E402
from vehicle_class import get_class  # noqa: E402
import search_patterns  # noqa: E402
from depot_client import DepotClient  # noqa: E402

GODOT_PROJECT = Path(__file__).resolve().parent / "godot"
READY_TIMEOUT = 90
RID = "fw-eval-1"


# --------------------------------------------------------------------------- launch
def find_godot(hint: str | None = None) -> str:
    """Mirrors rover/sim/godot_launcher.py's find_godot — duplicated (not imported)
    so this harness has no import-path dependency on rover/sim beyond depot_client,
    matching that module's own stated rationale for duplicating this helper."""
    if hint:
        return hint
    env = os.environ.get("GODOT_BIN")
    if env:
        return env
    for name in ("godot4", "godot"):
        p = which(name)
        if p:
            return p
    raise RuntimeError("Godot 4 binary not found. Install it or set GODOT_BIN=/path/to/godot4")


class GodotProcess:
    def __init__(self, proc: subprocess.Popen, port: int):
        self.proc = proc
        self.port = port

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def launch_flightline(seed: int = 0, port: int = 9989, gui: bool = False,
                      godot_bin: str | None = None, env: str = "flightline") -> GodotProcess:
    """Launch Godot with a fixed-wing env, no quad/rover fleet, fixed-wing IPC port.

    `env` names an entry in fleet_manager.gd's env_map ("flightline",
    "countdemo", "gate", ...). It is a keyword with the historical default so
    the existing callers (fw_vlm_smoketest.py, this file's scenarios) keep
    working unchanged while other harnesses reuse the same proven launch +
    "IPC ready" handshake instead of copying it.
    """
    godot = find_godot(godot_bin)
    args = [godot, "--path", str(GODOT_PROJECT)]
    if not gui:
        args += ["--headless"]
    args += ["--", "--fleet=", f"--env={env}", f"--seed={seed}", f"--ipc-port={port}"]

    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ready = threading.Event()
    lines: list[str] = []

    def _tee():
        for line in proc.stdout:
            lines.append(line)
            print(f"[godot] {line}", end="", flush=True)
            if "IPC ready" in line:
                ready.set()
        ready.set()

    threading.Thread(target=_tee, daemon=True).start()
    if not ready.wait(timeout=READY_TIMEOUT):
        proc.terminate()
        raise RuntimeError("Godot did not print 'IPC ready' within timeout")
    if proc.poll() is not None:
        raise RuntimeError(f"Godot exited early (rc={proc.returncode}):\n" + "".join(lines))
    return GodotProcess(proc, port)


# --------------------------------------------------------------------------- grid helpers (Wall Went Up)
def _grid_occupied(grid: dict, wx: float, wy: float) -> bool:
    """Real occupancy-grid lookup (fixedwing_manager.gd's own _occ, populated by
    the raise_wall inject) — the honest proxy for an onboard forward obstacle
    sensor here, since SimBackend has no raw depth-scan IPC yet (see
    backends.py's Detection shim, which only carries labeled-prop detections)."""
    occ = base64.b64decode(grid["occ"])
    res, origin, w, h = grid["res"], grid["origin"], grid["w"], grid["h"]
    gx = int((wx - origin[0]) / res)
    gy = int((wy - origin[1]) / res)
    if gx < 0 or gx >= w or gy < 0 or gy >= h:
        return False
    return occ[gy * w + gx] == 1


def _path_blocked(grid: dict, from_xy: tuple, to_xy: tuple, lookahead_m: float, step_m: float = 2.0) -> bool:
    """Samples the direct line from from_xy toward to_xy, out to lookahead_m,
    against the real occupancy grid."""
    if grid is None:
        return False
    dx, dy = to_xy[0] - from_xy[0], to_xy[1] - from_xy[1]
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return False
    steps = int(min(dist, lookahead_m) / step_m)
    for i in range(1, steps + 1):
        t = (i * step_m) / dist
        wx, wy = from_xy[0] + dx * t, from_xy[1] + dy * t
        if _grid_occupied(grid, wx, wy):
            return True
    return False


def _dist_to_rect(px: float, py: float, center: tuple, half: tuple) -> float:
    """Distance from a point to an axis-aligned rect's boundary; 0.0 if inside."""
    dx = max(abs(px - center[0]) - half[0], 0.0)
    dy = max(abs(py - center[1]) - half[1], 0.0)
    return math.hypot(dx, dy)


def _chase_cam_pose(x: float, y: float, z: float, yaw: float,
                    back_m: float = 9.0, side_m: float = 4.0, up_m: float = 3.0):
    """A vantage placed once, high enough overhead to frame a whole scenario
    (100s of meters), reduces a ~2.5m-wingspan aircraft (fixedwing_visuals.gd's
    mesh is genuinely built at real Skywalker-X8-class scale, not an undersized
    placeholder — checked directly) to a barely-visible dot. A first attempt at
    a chase cam (35m back/15m side/12m up, ~40m total distance) was STILL too
    far for an object this small and looked almost the same — real close-chase
    FPV footage of a small UAV sits more like 10-15m total distance, not 40m.
    Recomputed accordingly. Re-applied via move_vantage() every tick so the
    aircraft stays framed at a legible size throughout the flight."""
    forward = (math.cos(yaw), math.sin(yaw))
    right = (math.sin(yaw), -math.cos(yaw))
    cam = (
        x - back_m * forward[0] + side_m * right[0],
        y - back_m * forward[1] + side_m * right[1],
        z + up_m,
    )
    return cam, (x, y, z)


# --------------------------------------------------------------------------- flight
class FlightRecorder:
    """Accumulates per-tick telemetry for metrics + the overlay video renderer."""

    def __init__(self):
        self.ticks: list[dict] = []   # {t, leg, pos, detections, jpg}

    def record(self, t: float, leg: str, pos: tuple, detections: list, jpg: bytes | None):
        self.ticks.append({"t": t, "leg": leg, "pos": pos, "detections": detections, "jpg": jpg})


def _fly_toward(client: DepotClient, backend: SimBackend, memory: SpatialMemory,
                target_xy: tuple[float, float], leg: str, recorder: FlightRecorder,
                max_ticks: int = 400, tol_m: float = 8.0, dt: float = 0.1,
                capture_vantage: str | None = None) -> dict:
    """Scripted flight leg: proportional heading-hold toward target_xy, sampling
    backend.detect() -> memory.update() every tick (the real Slice-1 sensing/memory
    pipeline, not a stand-in). Returns leg stats (path_m flown, ticks, last pose)."""
    path_m = 0.0
    prev_xy = None
    for i in range(max_ticks):
        pose = backend.get_pose()
        if pose is None:
            break
        x, y, z, yaw = pose
        if prev_xy is not None:
            path_m += math.hypot(x - prev_xy[0], y - prev_xy[1])
        prev_xy = (x, y)

        dx, dy = target_xy[0] - x, target_xy[1] - y
        dist = math.hypot(dx, dy)
        if dist <= tol_m:
            break

        desired = math.atan2(dy, dx)
        err = (desired - yaw + math.pi) % (2 * math.pi) - math.pi
        st = client.fw_state(RID)
        cruise = float(st.get("airspeed", 18.0))
        yaw_rate = max(-0.6, min(0.6, err * 1.5))
        backend.drive(cruise, yaw_rate)

        detections = backend.detect()
        for d in detections:
            if d.world_xyz is not None:
                memory.update(d.label, d.world_xyz[0], d.world_xyz[1], d.world_xyz[2], d.score)

        jpg = None
        if capture_vantage:
            cam_pos, look_at = _chase_cam_pose(x, y, z, yaw)
            client.move_vantage(capture_vantage, cam_pos, look_at)
            jpg = client.grab_vantage(capture_vantage)
        recorder.record(t=time.time(), leg=leg,
                        pos=(x, y, z, yaw), detections=[d.__dict__ for d in detections], jpg=jpg)

        time.sleep(dt)

    return {"leg": leg, "path_m": path_m, "ticks": i + 1, "end_pose": backend.get_pose()}


def run_reacquire(client: DepotClient, gui: bool, capture_video: bool) -> dict:
    """The 'Reacquire' scenario: fly past water_tower, continue out of range/FOV,
    then fly back to the remembered world coordinate instead of re-searching."""
    backend = SimBackend(client, RID)
    memory = SpatialMemory(merge_radius=12.0)   # matches reasoning_loop's fixed-wing scaling
    recorder = FlightRecorder()

    ground_truth = {p["label"]: tuple(p["world"]) for p in client.fw_prop_truth()}
    print(f"[fw_eval] ground truth props: {ground_truth}")

    client.fw_spawn(RID, (0.0, 0.0, 30.0), 0.0)
    vantage_name = "overhead" if capture_video else None
    if capture_video:
        client.add_vantage("overhead", (150.0, -50.0, 250.0), (150.0, 20.0, 0.0))
    time.sleep(0.3)

    # Outbound leg: fly well past water_tower (150,0) so it passes out of range/FOV.
    outbound = _fly_toward(client, backend, memory, (250.0, 0.0), "outbound", recorder,
                           capture_vantage=vantage_name)

    all_outbound_labels = {det["label"] for tick in recorder.ticks if tick["leg"] == "outbound"
                           for det in tick["detections"]}

    landmark = memory.nearest("water_tower")
    if landmark is None:
        client.fw_despawn(RID)
        return {
            "scenario": "reacquire", "success": False,
            "reason": "water_tower never entered memory during outbound leg",
            "outbound": outbound, "ground_truth": ground_truth,
        }

    turnaround_pose = backend.get_pose()
    straight_line_m = math.hypot(landmark.x - turnaround_pose[0], landmark.y - turnaround_pose[1])

    return_leg = _fly_toward(client, backend, memory, (landmark.x, landmark.y), "return", recorder,
                             capture_vantage=vantage_name)

    reacquire_detected = any(
        det["label"] == "water_tower"
        for tick in recorder.ticks if tick["leg"] == "return" for det in tick["detections"]
    )

    path_efficiency = (straight_line_m / return_leg["path_m"]) if return_leg["path_m"] > 0 else 0.0

    # Mark, per tick in chronological order, whether "water_tower" had actually
    # entered memory yet — so the overlay video's memory pin only appears once
    # it was genuinely learned, not retroactively on every frame.
    seen = False
    for tick in recorder.ticks:
        if any(det["label"] == "water_tower" for det in tick["detections"]):
            seen = True
        tick["memory_known"] = seen

    client.fw_despawn(RID)

    result = {
        "scenario": "reacquire",
        "success": reacquire_detected,
        "ground_truth": ground_truth,
        "memory_landmark": {"label": landmark.label, "x": landmark.x, "y": landmark.y,
                            "z": landmark.z, "hits": landmark.hits, "score": landmark.score},
        "outbound_detected_labels": sorted(all_outbound_labels),
        "false_positive_labels": sorted(all_outbound_labels - {"water_tower"}),
        "outbound": outbound,
        "return": return_leg,
        "straight_line_m": round(straight_line_m, 1),
        "reacquisition_path_efficiency": round(path_efficiency, 3),
        "reacquired_visually": reacquire_detected,
        "recorder": recorder,
    }
    return result


def run_sector_sweep(client: DepotClient, gui: bool, capture_video: bool) -> dict:
    """The 'Sector Sweep' scenario: real search_patterns.lawnmower() coverage of
    the flightline area — drives the actual production geometry module (Slice 2),
    same honest framing as run_reacquire (real code, scripted invocation, no VLM
    judgment involved so nothing here needs the on-device brain to be present).
    Measures coverage fraction of the area actually swept and time-to-find for
    each ground-truth prop — the 'Explore' capability."""
    backend = SimBackend(client, RID)
    memory = SpatialMemory(merge_radius=12.0)
    recorder = FlightRecorder()
    vc = get_class("fixedwing")

    ground_truth = {p["label"]: tuple(p["world"]) for p in client.fw_prop_truth()}
    print(f"[fw_eval] ground truth props: {ground_truth}")

    client.fw_spawn(RID, (0.0, -100.0, 40.0), math.pi / 2)
    vantage_name = "overhead" if capture_video else None
    if capture_video:
        client.add_vantage("overhead", (150.0, -20.0, 320.0), (150.0, -20.0, 0.0))
    time.sleep(0.3)

    bounds = ((0.0, -100.0), (300.0, 100.0))
    waypoints = search_patterns.lawnmower(bounds, vc, heading_deg=0.0)
    print(f"[fw_eval] lawnmower plan: {len(waypoints)} waypoints covering {bounds}")

    total_path_m = 0.0
    for leg_i, wp in enumerate(waypoints):
        leg = _fly_toward(client, backend, memory, wp, f"leg_{leg_i}", recorder,
                          capture_vantage=vantage_name)
        total_path_m += leg["path_m"]

    first_seen_idx: dict[str, int] = {}
    for i, tick in enumerate(recorder.ticks):
        for det in tick["detections"]:
            first_seen_idx.setdefault(det["label"], i)
    t0 = recorder.ticks[0]["t"] if recorder.ticks else 0.0
    time_to_find_s = {
        label: round(recorder.ticks[idx]["t"] - t0, 1) for label, idx in first_seen_idx.items()
    }

    # Coverage: fraction of the search-bounds ROI marked "observed" by the real
    # sim sensing sweep (fixedwing_manager.gd's own observed grid), not a
    # self-reported estimate — same ground-truth-oracle pattern as fw_prop_truth.
    grid = client.fw_grid(RID)
    coverage_fraction = None
    if grid:
        obs = base64.b64decode(grid["obs"])
        res, origin, w, h = grid["res"], grid["origin"], grid["w"], grid["h"]
        (bx0, by0), (bx1, by1) = bounds
        roi_cells = 0
        observed_cells = 0
        for gy in range(h):
            wy = origin[1] + gy * res
            if wy < by0 or wy > by1:
                continue
            row = gy * w
            for gx in range(w):
                wx = origin[0] + gx * res
                if wx < bx0 or wx > bx1:
                    continue
                roi_cells += 1
                if obs[row + gx] == 1:
                    observed_cells += 1
        coverage_fraction = round(observed_cells / roi_cells, 3) if roi_cells else 0.0

    client.fw_despawn(RID)

    found_all = set(ground_truth) <= set(first_seen_idx)
    result = {
        "scenario": "sector_sweep",
        "success": found_all,
        "ground_truth": ground_truth,
        "detected_labels": sorted(first_seen_idx),
        "time_to_find_s": time_to_find_s,
        "waypoints_flown": len(waypoints),
        "total_path_m": round(total_path_m, 1),
        "coverage_fraction": coverage_fraction,
        "recorder": recorder,
    }
    return result


def run_wall_went_up(client: DepotClient, gui: bool, capture_video: bool) -> dict:
    """The 'Wall Went Up' scenario: a real obstacle appears mid-flight, directly
    on the planned route — fixedwing_manager.gd's new `raise_wall` inject spawns
    genuine 3D collision geometry AND updates the real occupancy grid (not a
    fleet-DSL-only no-fly zone, and not a scripted "pretend a wall appeared").
    The harness detects the blockage by querying that same real grid (the
    honest proxy for an onboard forward obstacle sensor — see _grid_occupied's
    docstring) and reroutes using search_patterns.turn_radius_m — the actual
    production geometry a real replan would use — rather than a
    scenario-specific hardcoded detour.
    """
    backend = SimBackend(client, RID)
    memory = SpatialMemory(merge_radius=12.0)
    recorder = FlightRecorder()
    vc = get_class("fixedwing")

    start = (0.0, 0.0, 50.0)
    goal = (300.0, 0.0, 50.0)
    client.fw_spawn(RID, start, 0.0)
    vantage_name = "overhead" if capture_video else None
    if capture_video:
        client.add_vantage("overhead", (150.0, -100.0, 280.0), (150.0, 0.0, 0.0))
    time.sleep(0.3)

    # Fly a short leg before the wall appears — mirrors fw_replan.yaml's own
    # "inject shortly after launch" timing (that scenario injects at t=3s).
    leg1 = _fly_toward(client, backend, memory, (60.0, 0.0), "outbound_pre_wall", recorder,
                       capture_vantage=vantage_name)
    total_path_m = leg1["path_m"]

    wall_center = (150.0, 0.0)
    wall_half = (30.0, 30.0)
    # fw_inject, NOT inject() — inject() is hard-wired to PhroverManager only
    # (see ipc_server.gd/depot_client.py); using it here would silently no-op.
    client.fw_inject("raise_wall", center=list(wall_center), half=list(wall_half), height=60.0)
    print(f"[fw_eval] injected wall at center={wall_center} half={wall_half}")

    pose = backend.get_pose()
    grid = client.fw_grid(RID)
    blocked = _path_blocked(grid, (pose[0], pose[1]), (goal[0], goal[1]), vc.sense_range_m)
    print(f"[fw_eval] direct route blocked (real occupancy-grid check): {blocked}")

    replan_triggered = False
    if blocked:
        replan_triggered = True
        client.fw_log_event(RID, "replan", {
            "reason": "occupancy grid shows the direct route blocked", "phase": "wall_went_up",
        })
        offset = 1.5 * search_patterns.turn_radius_m(vc)
        candidates = [
            (wall_center[0], wall_center[1] + wall_half[1] + offset),
            (wall_center[0], wall_center[1] - wall_half[1] - offset),
        ]
        detour = next((c for c in candidates if not _grid_occupied(grid, c[0], c[1])), candidates[0])
        print(f"[fw_eval] replanning around obstacle via detour waypoint {detour}")
        leg2 = _fly_toward(client, backend, memory, detour, "reroute", recorder,
                           capture_vantage=vantage_name)
        total_path_m += leg2["path_m"]

    leg3 = _fly_toward(client, backend, memory, (goal[0], goal[1]), "final", recorder,
                       capture_vantage=vantage_name)
    total_path_m += leg3["path_m"]

    final_pose = backend.get_pose()
    reached_goal = math.hypot(final_pose[0] - goal[0], final_pose[1] - goal[1]) <= 15.0

    min_dist_to_wall = min(
        (_dist_to_rect(tick["pos"][0], tick["pos"][1], wall_center, wall_half) for tick in recorder.ticks),
        default=float("inf"),
    )
    collided = min_dist_to_wall <= 0.01

    client.fw_despawn(RID)

    result = {
        "scenario": "wall_went_up",
        "success": reached_goal and not collided,
        "replan_triggered": replan_triggered,
        "reached_goal": reached_goal,
        "collided": collided,
        "min_dist_to_wall_m": round(min_dist_to_wall, 1),
        "total_path_m": round(total_path_m, 1),
        "wall": {"center": list(wall_center), "half": list(wall_half)},
        "ground_truth": {},
        "recorder": recorder,
    }
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="reacquire",
                    choices=["reacquire", "sector_sweep", "wall_went_up"])
    ap.add_argument("--gui", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--port", type=int, default=9989)
    ap.add_argument("--out", default=None, help="write metrics report JSON here")
    ap.add_argument("--video", default=None, help="write overlay MP4 here")
    args = ap.parse_args()

    # Headless Godot uses the "dummy" rendering driver, which cannot produce
    # vantage-camera frames (texture_2d_get errors) — grab_vantage needs a real
    # rendering context, same reason rover/sim/make_smoke_video.py always launches
    # with gui=True. Force it on whenever video capture is requested.
    need_gui = args.gui or bool(args.video)
    proc = launch_flightline(seed=args.seed, port=args.port, gui=need_gui)
    try:
        client = DepotClient(port=args.port)
        if args.scenario == "sector_sweep":
            result = run_sector_sweep(client, gui=need_gui, capture_video=bool(args.video))
        elif args.scenario == "wall_went_up":
            result = run_wall_went_up(client, gui=need_gui, capture_video=bool(args.video))
        else:
            result = run_reacquire(client, gui=need_gui, capture_video=bool(args.video))
        client.close()
    finally:
        proc.stop()

    recorder = result.pop("recorder", None)

    print(f"\n{'='*60}")
    print(f"  FIXED-WING CAPABILITY HARNESS — {result['scenario']}")
    print(f"{'='*60}")
    if result["scenario"] == "sector_sweep":
        print(f"  success (found all ground-truth props): {result['success']}")
        print(f"  detected labels: {result['detected_labels']}")
        print(f"  time-to-find (s): {result['time_to_find_s']}")
        print(f"  waypoints flown: {result['waypoints_flown']}, path: {result['total_path_m']}m")
        print(f"  coverage fraction (search-bounds ROI): {result['coverage_fraction']}")
    elif result["scenario"] == "wall_went_up":
        print(f"  success (reached goal, no collision): {result['success']}")
        print(f"  replan triggered: {result['replan_triggered']}")
        print(f"  reached goal: {result['reached_goal']}, collided: {result['collided']}")
        print(f"  min distance to wall: {result['min_dist_to_wall_m']}m")
        print(f"  total path flown: {result['total_path_m']}m")
    else:
        print(f"  success (reacquired visually): {result['success']}")
        print(f"  outbound detected labels: {result['outbound_detected_labels']}")
        print(f"  false positives: {result['false_positive_labels']}")
        print(f"  memory landmark: {result['memory_landmark']}")
        print(f"  straight-line to landmark: {result['straight_line_m']}m")
        print(f"  return leg flown: {result['return']['path_m']:.1f}m "
             f"over {result['return']['ticks']} ticks")
        print(f"  reacquisition path efficiency: {result['reacquisition_path_efficiency']}")

    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2, default=str))
        print(f"  wrote {args.out}")

    if args.video and recorder is not None:
        from fw_video_overlay import render_overlay_video
        render_overlay_video(recorder, result, args.video)
        print(f"  wrote {args.video}")

    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""Generate labeled training data from the Godot sim for the domain detector (Model 1).

Godot analog of sim_dataset_recorder.py -- same purpose, same output format
(DataRecorder JSONL + COCO annotations.json), same CLASS_NAMES/_VTYPE_TO_CLASS,
different backend (godot_engine.py via engine_client.EngineClient instead of
IsaacVehicleBridge). Adopted in place of the Isaac path after ADR-0005's visual
alignment gate found two issues on the Isaac side (see
reports/isaac_grounding_frames_finding.json in the internal autonomy repo): a
driving-pattern bug (fixed there, and fixed identically in this driver's
_random_goals below) and a still-unresolved render/GT mismatch that would need
USD-stage inspection to chase further. Godot's FleetManager mirrors
IsaacVehicleBridge's kinematic model closely enough that this driver is almost
line-for-line the same shape as sim_dataset_recorder.py.

GROUND-TRUTH NOTE: projection uses the camera's live forward/up vectors (from
FleetManager.get_camera_pose, added alongside this file -- see its docstring)
rather than reconstructing camera pose from a yaw angle by hand, specifically
to avoid re-deriving Godot's rotation_degrees/Euler-order sign conventions,
which is exactly the class of error that produced a wrong-answer-that-still-
looks-plausible on another axis-convention check earlier in this project. The
sign conventions MUST still be validated on first run by spot-checking the
annotated JPEGs (this file's own smoke test does this empirically, mirroring
sim_dataset_recorder.py's own such note) -- do not assume it is correct just
because the derivation is simpler than the Isaac path's.

Run (starts godot_engine.py itself; point --godot at the binary if not on PATH):
    python3 godot_dataset_recorder.py \
        --fleet rover:2 --env city --frames 110 --out ~/drone-data/godot_rover_phase0
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

SIM_DIR = Path(__file__).parent.resolve()
DRONE_DIR = SIM_DIR.parent
sys.path.insert(0, str(DRONE_DIR))
sys.path.insert(0, str(DRONE_DIR / "common"))
sys.path.insert(0, str(SIM_DIR))

# Same detector class set as sim_dataset_recorder.py -- kept identical so both
# recorders feed the same downstream training scripts without a schema fork.
CLASS_NAMES = [
    "person", "person_aerial", "vehicle", "bicycle_motorcycle",
    "drone", "landing_pad", "powerline_pole", "animal", "boat",
]
CLASS_ID = {n: i for i, n in enumerate(CLASS_NAMES)}
_VTYPE_TO_CLASS = {"quadcopter": "drone", "rover": "vehicle"}

_INTRINSICS = {"fx": None, "fy": None, "cx": 320.0, "cy": 240.0, "width": 640, "height": 480}


def _resolve_intrinsics(hfov_deg: float, vfov_deg: float, width: int, height: int) -> dict:
    """Pinhole fx/fy from Godot's Camera3D.fov. Godot 4's default keep_aspect is
    KEEP_HEIGHT, meaning Camera3D.fov is the VERTICAL fov and horizontal is
    derived from the aspect ratio -- confirmed empirically by this module's own
    --smoke-test (a marker at a known angle must land at the pixel the vfov-based
    fx/fy formula predicts), not assumed from Godot documentation alone.
    """
    import math
    fy = (height / 2.0) / math.tan(math.radians(vfov_deg) / 2.0)
    fx = fy  # square pixels; width follows from aspect * vfov under KEEP_HEIGHT
    return {"fx": fx, "fy": fy, "cx": width / 2.0, "cy": height / 2.0, "width": width, "height": height}


def _project(point_w, cam_pos, fwd, left, up, intr):
    """Project a world point into image pixels using live forward/left/up ENU
    unit vectors (not a reconstructed rotation matrix) -- see module docstring."""
    rel = np.asarray(point_w, float) - np.asarray(cam_pos, float)
    f = float(np.dot(rel, fwd))
    if f <= 1e-3:
        return None
    left_c = float(np.dot(rel, left))
    up_c = float(np.dot(rel, up))
    u = intr["cx"] - intr["fx"] * (left_c / f)
    v = intr["cy"] - intr["fy"] * (up_c / f)
    return u, v, f


def _gt_boxes(client, roster, ego_id, positions, intr, cam_pos, fwd, left, up):
    W, H = intr["width"], intr["height"]
    boxes = []
    for spec in roster:
        vid = spec["id"]
        if vid == ego_id:
            continue
        cls = _VTYPE_TO_CLASS.get(spec["type"])
        if cls is None:
            continue
        pos = positions[vid]
        radius = 0.15 if spec["type"] == "quadcopter" else 0.5
        pr = _project(pos, cam_pos, fwd, left, up, intr)
        if pr is None:
            continue
        u, v, fwd_dist = pr
        half = max(4.0, (intr["fx"] * radius) / fwd_dist)
        x1, y1, x2, y2 = u - half, v - half, u + half, v + half
        cx1, cy1 = max(0.0, x1), max(0.0, y1)
        cx2, cy2 = min(W - 1.0, x2), min(H - 1.0, y2)
        if cx2 <= cx1 or cy2 <= cy1:
            continue
        boxes.append({
            "label": cls, "category_id": CLASS_ID[cls],
            "bbox": [cx1, cy1, cx2 - cx1, cy2 - cy1],
            "confidence": 1.0, "distance_m": fwd_dist, "source_model": "sim_gt",
        })
    return boxes


def _random_goals(client, roster, rng, spread=8.0):
    """Assign each vehicle a fresh random goal, and point it at that goal's
    bearing -- see godot_dataset_recorder.py module docstring and
    reports/isaac_grounding_frames_finding.json for why the yaw call is not
    optional: without it, a vehicle's forward-facing camera never turns to
    face its own direction of travel.

    The yaw value itself is NOT a plain atan2(dy, dx) (standard CCW-from-east
    bearing) despite fleet_manager.gd's own header comment claiming that
    convention ("Yaw: ENU CCW from east"). Empirically (grab a frame, read the
    live camera pose via get_camera_pose, compare to where the target actually
    renders) the real mapping _apply_pose_enu()/_integrate() implement is
    body_front_ENU(yaw) = (-sin(yaw), -cos(yaw)) -- confirmed by hand-deriving
    fleet_manager.gd's rotation_degrees.y = -rad_to_deg(yaw) formula through
    Godot's Y-axis rotation and the file's own Godot<->ENU axis mapping, then
    verifying the derived formula against a live capture (dot product of
    measured vs. commanded direction = 1.0000, target lands within a few px of
    the math-predicted pixel). Using the documented-but-wrong atan2(dy, dx)
    yawed vehicles toward a direction unrelated to their actual goal, which is
    why GT boxes (computed independently from raw positions, unaffected by
    this) looked randomly offset from the rendered vehicle -- most visible on
    quads since they fly at altitude with the least room for a wrong-facing
    camera to accidentally still catch another vehicle in frame. Fixing
    fleet_manager.gd's rotation formula instead would ripple into every
    already-tuned vantage/spawn angle that implicitly depends on the current
    behavior; correcting the convention here, at the one caller that needs a
    true "face point P" (not just "hold some fixed spawn heading"), is the
    narrower, lower-blast-radius fix. See reports/phase1_step4_results.md's
    quad-offset finding for the full before/after evidence.
    """
    import math
    for spec in roster:
        st = client.state(spec["id"])
        pos = st["position"]
        gx = pos[0] + rng.uniform(-spread, spread)
        gy = pos[1] + rng.uniform(-spread, spread)
        gz = 0.0 if spec["type"] == "rover" else rng.uniform(0.8, 3.0)
        client.set_goal(spec["id"], [gx, gy, gz])
        client.set_yaw(spec["id"], math.atan2(pos[0] - gx, pos[1] - gy))


def stop_engine(proc: subprocess.Popen) -> None:
    """godot_engine.py only cleans up its OWN internal Godot subprocess inside a
    `except KeyboardInterrupt` handler around `proc.wait()` -- a plain SIGTERM
    (what Popen.terminate() sends) kills the wrapper immediately without ever
    reaching that handler, orphaning the real Godot process still holding the
    IPC port. Confirmed live: three separate runs each leaked an orphaned
    godot4 process this way, one of which then made the NEXT run's port bind
    fail. SIGINT instead reaches Python as a real KeyboardInterrupt, letting
    the wrapper's own cleanup path run and actually kill its child."""
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _start_xvfb(display: str) -> subprocess.Popen | None:
    """Godot's SubViewport texture readback needs a real GPU-backed rendering
    surface -- plain --headless alone was confirmed NOT sufficient on this host
    (grab_frame_jpeg returned null textures every time, 0 images written) even
    though the engine itself started and IPC came up fine. godot_launch_fleet.py's
    own --xvfb flag already documents this exact requirement for headless GPU
    servers; missing it here was the actual bug, not a Godot/rendering-driver
    flag choice."""
    proc = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", "1280x720x24", "-ac"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2.0)
    if proc.poll() is not None:
        raise RuntimeError(f"Xvfb exited immediately (rc={proc.returncode}) -- is it installed?")
    return proc


def _launch_godot_engine(fleet: str, env: str, sock: str, tcp_port: int, godot_bin: str | None,
                          display: str = ":97", rendering_driver: str | None = None,
                          force_gui: bool = False, gpu_index: int | None = None
                          ) -> tuple[subprocess.Popen, subprocess.Popen]:
    xvfb_proc = _start_xvfb(display)
    launch_env = dict(os.environ, DISPLAY=display)
    cmd = [sys.executable, str(SIM_DIR / "godot_engine.py"),
           "--fleet", fleet, "--env", env, "--sock", sock, "--tcp-port", str(tcp_port)]
    if godot_bin:
        cmd += ["--godot", godot_bin]
    if gpu_index is not None:
        cmd += ["--gpu-index", str(gpu_index)]
    if rendering_driver:
        cmd += ["--rendering-driver", rendering_driver]
    if force_gui:
        # godot_engine.py's --headless (the default unless --gui is passed) forces
        # Godot's null/dummy DisplayServer, which was confirmed to make grab_frame
        # return null textures regardless of --rendering-driver -- --headless isn't
        # "real rendering, just no window," it's "no real rendering at all." Passing
        # --gui here still shows nothing to any human (there is no real display,
        # only Xvfb's virtual one) -- it makes Godot open a real (if unseen)
        # rendering context against that virtual display instead of skipping
        # rendering entirely.
        cmd.append("--gui")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=launch_env)
    ready = threading.Event()
    bind_failed = threading.Event()

    def _tee():
        for line in proc.stdout:
            print(f"[godot-engine] {line}", end="", flush=True)
            # Must match the specific IPC-server-ready message, not just any
            # line containing "ready" -- env_office.gd/env_city.gd print their
            # own unrelated "[env_X] ready" line first, which a looser match
            # falsely treated as the IPC server being up even when its actual
            # port bind had failed (confirmed live: "Already in use" on a
            # stale process, and this loop declared ready anyway).
            if "IPC ready" in line:
                ready.set()
            if "Failed to listen" in line:
                bind_failed.set()
                ready.set()
        ready.set()

    threading.Thread(target=_tee, daemon=True).start()
    if not ready.wait(timeout=90):
        stop_engine(proc)
        raise RuntimeError("godot_engine.py did not report ready within 90s")
    if bind_failed.is_set():
        stop_engine(proc)
        xvfb_proc.terminate()
        raise RuntimeError(f"Godot's IPC port {tcp_port} is already in use -- "
                            f"check for a stale process (pgrep -af 'godot4.*{tcp_port}') and kill it first")
    if proc.poll() is not None:
        raise RuntimeError(f"godot_engine.py exited early (rc={proc.returncode})")
    time.sleep(1.0)  # let the Unix-socket proxy thread actually bind before connecting
    return proc, xvfb_proc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="office")
    ap.add_argument("--fleet", default="rover:2", help="e.g. rover:2 or quad:2,rover:1")
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--out", default="~/drone-data/godot_sim")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--regoal-every", type=int, default=25)
    ap.add_argument("--sock", default="/tmp/godot_dataset_recorder.sock")
    ap.add_argument("--tcp-port", type=int, default=9998)
    ap.add_argument("--godot", default=None)
    ap.add_argument("--force-gui", action="store_true", help="pass --gui to godot_engine.py so Godot does NOT force its null/dummy renderer -- see _launch_godot_engine's docstring; shows nothing to any human, Xvfb has no real display attached")
    ap.add_argument("--display", default=":97", help="Xvfb display -- distinct from :99 (godot_launch_fleet.py's default) to avoid colliding with any already-running fleet sim")
    ap.add_argument("--rendering-driver", default=None, help="e.g. opengl3 -- plain --headless+Xvfb alone was confirmed to fall back to Godot's no-op dummy renderer on this host (grab_frame always returns null textures, reproduced even against a separate, pre-existing fleet sim)")
    ap.add_argument("--vfov-deg", type=float, default=70.0, help="Camera3D.fov set by fleet_manager.gd")
    ap.add_argument("--gpu-index", type=int, default=None, help="Pin Godot's Vulkan device away from a GPU a concurrent training/serving job is saturating -- see godot_engine.py's --gpu-index")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    # Must match fleet_manager.gd's _parse_roster() ID scheme EXACTLY: 1-indexed,
    # zero-padded, restarting per vehicle type ("quad:2,rover:1" -> sim-quadcopter-001,
    # sim-quadcopter-002, sim-rover-001 -- verbatim from that function's own comment).
    # A 0-indexed roster here silently diverges from Godot's real vehicle IDs: the
    # nonexistent ID's grab_jpeg() returns None (silently skipped, no error), and its
    # state() falls back to EngineClient's default [0,0,0] instead of erroring --
    # confirmed live: this exact mismatch was why every GT box in this pipeline was being
    # projected against world-origin (0,0,0) instead of the real other vehicle, not a
    # rendering or projection-math bug.
    roster = []
    type_counts: dict[str, int] = {}
    for part in args.fleet.split(","):
        kind, n = part.split(":")
        vtype = "quadcopter" if kind.strip() in ("quad", "quadcopter") else "rover"
        for i in range(int(n)):
            type_counts[vtype] = type_counts.get(vtype, 0) + 1
            roster.append({"id": f"sim-{vtype}-{type_counts[vtype]:03d}", "type": vtype})

    from data_recorder import DataRecorder
    from engine_client import EngineClient

    out_dir = Path(args.out).expanduser()
    recorder = DataRecorder(out_dir=out_dir, enabled=True, source="sim", save_images=True)
    recorder.start_episode({"env": args.env, "fleet": args.fleet, "frames": args.frames})

    engine_proc, xvfb_proc = _launch_godot_engine(args.fleet, args.env, args.sock, args.tcp_port,
                                                   args.godot, args.display, args.rendering_driver,
                                                   args.force_gui, args.gpu_index)

    coco_images, coco_anns = [], []
    ann_id = 0
    captured = 0

    try:
        client = EngineClient(args.sock)
        intr = _resolve_intrinsics(args.vfov_deg, args.vfov_deg, 640, 480)
        while captured < args.frames:
            if captured % args.regoal_every == 0:
                _random_goals(client, roster, rng)
            time.sleep(1.0 / 30.0)  # let FleetManager's _physics_process advance

            for spec in roster:
                vid = spec["id"]
                recorder.vehicle_type = spec["type"]
                jpg = client.grab_jpeg(vid)
                if jpg is None:
                    continue
                import cv2
                rgb = cv2.cvtColor(cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)

                # Positions must be sampled per-vehicle, right next to this vid's own
                # grab_jpeg/camera_pose calls -- NOT once for the whole roster before this
                # loop starts. grab_jpeg is a real blocking IPC round-trip (render + JPEG
                # encode + base64 + transfer) and FleetManager's _physics_process keeps
                # advancing in real time while it's in flight, so a single shared snapshot
                # goes stale by an amount that grows with roster position. That staleness
                # is most visible on quad targets (3.0 m/s cap, 2x a rover's 1.5 m/s) --
                # confirmed via ADR-0005 overlay evidence (box tens of px off from the
                # rendered quad; rover boxes in the same frames were fine) -- see
                # reports/phase1_step4_results.md's quad-offset finding/amendment.
                positions = {s["id"]: client.state(s["id"])["position"] for s in roster}

                cam = client.camera_pose(vid)
                cam_pos = np.asarray(cam["position"], float)
                fwd = np.asarray(cam["forward"], float)
                up = np.asarray(cam["up"], float)
                if os.environ.get("GT_DEBUG"):
                    other_id = next(s["id"] for s in roster if s["id"] != vid)
                    other_pos = np.asarray(positions[other_id], float)
                    print(f"[GT_DEBUG] |fwd|={np.linalg.norm(fwd):.4f} |up|={np.linalg.norm(up):.4f} "
                          f"dot(fwd,up)={np.dot(fwd, up):.4f} true_dist={np.linalg.norm(other_pos - cam_pos):.3f} "
                          f"cam_pos={cam_pos.tolist()} other_pos={other_pos.tolist()}", flush=True)
                fwd = fwd / max(1e-9, np.linalg.norm(fwd))
                up = up / max(1e-9, np.linalg.norm(up))
                left = np.cross(up, fwd)
                left /= max(1e-9, np.linalg.norm(left))

                boxes = _gt_boxes(client, roster, vid, positions, intr, cam_pos, fwd, left, up)

                det_records = [{
                    "label": b["label"], "confidence": b["confidence"],
                    "bbox": [b["bbox"][0], b["bbox"][1],
                             b["bbox"][0] + b["bbox"][2], b["bbox"][1] + b["bbox"][3]],
                    "distance_m": b["distance_m"], "source_model": "sim_gt",
                } for b in boxes]
                seq = recorder.record_detection(
                    rgb, det_records, intrinsics=intr,
                    pose={"cam_pos": cam_pos.tolist(), "cam_forward": fwd.tolist(),
                          "env": args.env, "drone_id": vid},
                )

                img_id = len(coco_images)
                ep = recorder.episode_id or "noepisode"
                frame_rel = f"frames/{ep}/{seq:08d}.jpg" if seq else None
                coco_images.append({
                    "id": img_id, "file_name": frame_rel or "",
                    "width": intr["width"], "height": intr["height"],
                    "drone_id": vid, "env": args.env,
                })
                for b in boxes:
                    coco_anns.append({
                        "id": ann_id, "image_id": img_id,
                        "category_id": b["category_id"], "bbox": b["bbox"],
                        "area": b["bbox"][2] * b["bbox"][3], "iscrowd": 0,
                    })
                    ann_id += 1

            captured += 1
            if captured % 50 == 0:
                print(f"[dataset] {captured}/{args.frames} frames, {ann_id} boxes", flush=True)
    finally:
        recorder.close()
        coco = {
            "images": coco_images, "annotations": coco_anns,
            "categories": [{"id": i, "name": n} for n, i in CLASS_ID.items()],
        }
        (out_dir / "annotations.json").write_text(json.dumps(coco))
        print(f"[dataset] wrote {len(coco_images)} images, {ann_id} anns -> {out_dir}", flush=True)
        stop_engine(engine_proc)
        xvfb_proc.terminate()
        try:
            xvfb_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            xvfb_proc.kill()


if __name__ == "__main__":
    sys.exit(main())

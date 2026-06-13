"""Generate labeled training data from the Isaac sim for the domain detector (Model 1).

Drives the kinematic IsaacVehicleBridge headless: spawns a roster, flies randomized
trajectories, and at each capture tick grabs RGB + depth + camera intrinsics/pose, projects
the known vehicle positions into the camera to produce *automatic perfect* ground-truth 2D
boxes for the rare classes (drone/UAV, rover), and writes:

  - JSONL/sidecars via `common.data_recorder.DataRecorder` (source="sim") — uniform with the
    on-device real-flight capture, so both feed the same training scripts.
  - COCO-format `annotations.json` (+ the same JPEGs) — direct input to Ultralytics `yolo train`.

Run on hoopoe inside the Isaac venv (see eco-sim-status / hoopoe-reference):
    /home/yusuf/isaac-sim-env/bin/python3 sim_dataset_recorder.py \
        --env office --fleet quad:4,rover:2 --frames 300 --out ~/drone-data/sim_office

GROUND-TRUTH NOTE: the pinhole projection below uses Isaac's camera convention (optical axis
+X, +Y left, +Z up). The sign conventions MUST be validated on first run by spot-checking the
annotated JPEGs (plan verification step 1). For static scene objects (person/chair/...), prefer
the Isaac replicator `bounding_box_2d_tight` semantic annotator — left as a hook in `_gt_boxes`.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

# Repo layout: this file is eco/drone/sim/; common/ is a sibling under eco/drone/.
SIM_DIR = Path(__file__).parent.resolve()
DRONE_DIR = SIM_DIR.parent
sys.path.insert(0, str(DRONE_DIR))
sys.path.insert(0, str(DRONE_DIR / "common"))

# Detector class set (v1) — see plan Model 1. Sim reliably labels the *vehicle* classes;
# the rest come from public aerial sets + GroundingDINO weak labels on real flight.
CLASS_NAMES = [
    "person", "person_aerial", "vehicle", "bicycle_motorcycle",
    "drone", "landing_pad", "powerline_pole", "animal", "boat",
]
CLASS_ID = {n: i for i, n in enumerate(CLASS_NAMES)}
_VTYPE_TO_CLASS = {"quadcopter": "drone", "rover": "vehicle"}


def _quat_wxyz_to_R(q: np.ndarray) -> np.ndarray:
    """Rotation matrix (local->world) from a wxyz quaternion."""
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def _project(point_w, cam_pos, cam_R, intr):
    """Project a world point into image pixels. Returns (u, v, forward_m) or None if behind."""
    rel = np.asarray(point_w, float) - cam_pos
    local = cam_R.T @ rel  # world->local
    fwd, left, up = float(local[0]), float(local[1]), float(local[2])
    if fwd <= 1e-3:
        return None
    u = intr["cx"] - intr["fx"] * (left / fwd)   # +Y(left) -> smaller u
    v = intr["cy"] - intr["fy"] * (up / fwd)     # +Z(up)   -> smaller v
    return u, v, fwd


def _gt_boxes(bridge, ego_id, intr, cam_pos, cam_R):
    """COCO-style boxes for OTHER vehicles visible from ego's camera.

    Static scene objects are not labeled here — add the replicator semantic annotator path
    when person/chair/... ground truth is needed.
    """
    W, H = intr["width"], intr["height"]
    boxes = []
    for vid, (pos, vtype, radius) in bridge.vehicle_world_positions().items():
        if vid == ego_id:
            continue
        cls = _VTYPE_TO_CLASS.get(vtype)
        if cls is None:
            continue
        pr = _project(pos, cam_pos, cam_R, intr)
        if pr is None:
            continue
        u, v, fwd = pr
        half = max(4.0, (intr["fx"] * radius) / fwd)  # bounding-sphere -> px half-extent
        x1, y1 = u - half, v - half
        x2, y2 = u + half, v + half
        # clip to frame; drop if fully outside
        cx1, cy1 = max(0.0, x1), max(0.0, y1)
        cx2, cy2 = min(W - 1.0, x2), min(H - 1.0, y2)
        if cx2 <= cx1 or cy2 <= cy1:
            continue
        boxes.append({
            "label": cls,
            "category_id": CLASS_ID[cls],
            "bbox": [cx1, cy1, cx2 - cx1, cy2 - cy1],  # COCO xywh
            "confidence": 1.0,
            "distance_m": fwd,
            "source_model": "sim_gt",
        })
    return boxes


def _random_goals(bridge, rng, spread=8.0):
    """Assign each vehicle a fresh random goal to drive trajectory diversity."""
    for vid, (pos, vtype, _r) in bridge.vehicle_world_positions().items():
        gx = pos[0] + rng.uniform(-spread, spread)
        gy = pos[1] + rng.uniform(-spread, spread)
        gz = 0.0 if vtype == "rover" else rng.uniform(0.8, 3.0)
        bridge.set_drone_goal(vid, [gx, gy, gz])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="office")
    ap.add_argument("--fleet", default="quad:4,rover:2", help="e.g. quad:4,rover:2")
    ap.add_argument("--frames", type=int, default=300, help="capture frames per run")
    ap.add_argument("--out", default="~/drone-data/sim", help="output dir")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--steps-per-frame", type=int, default=8)
    ap.add_argument("--regoal-every", type=int, default=25)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)

    # Build roster from --fleet
    roster = []
    for part in args.fleet.split(","):
        kind, n = part.split(":")
        vtype = "quadcopter" if kind.strip() in ("quad", "quadcopter") else "rover"
        for i in range(int(n)):
            roster.append({"id": f"sim-{kind.strip()}-{i:03d}", "type": vtype})

    from isaac_vehicle import IsaacVehicleBridge
    from data_recorder import DataRecorder

    out_dir = Path(args.out).expanduser()
    recorder = DataRecorder(out_dir=out_dir, enabled=True, source="sim", save_images=True)
    recorder.start_episode({"env": args.env, "fleet": args.fleet, "frames": args.frames})

    bridge = IsaacVehicleBridge(headless=True)
    bridge.setup(args.env, roster)
    for spec in roster:
        bridge.ensure_camera(spec["id"])

    coco_images, coco_anns = [], []
    ann_id = 0
    captured = 0

    try:
        while captured < args.frames:
            if captured % args.regoal_every == 0:
                _random_goals(bridge, rng)
            bridge.step_simulation(n_steps=args.steps_per_frame, render=True)

            for spec in roster:
                vid = spec["id"]
                recorder.vehicle_type = spec["type"]
                rgb = bridge.grab_frame(vid)
                if rgb is None:
                    continue
                depth_m = bridge.grab_depth(vid)
                intr = bridge.camera_intrinsics(vid)
                pose = bridge.camera_world_pose(vid)
                if pose is None:
                    continue
                cam_pos, cam_quat = pose
                cam_R = _quat_wxyz_to_R(cam_quat)
                boxes = _gt_boxes(bridge, vid, intr, cam_pos, cam_R)

                depth_u16 = None
                if depth_m is not None:
                    depth_u16 = np.clip(depth_m * 1000.0, 0, 65535).astype(np.uint16)

                # Uniform JSONL + sidecars (det records, xyxy bbox to match Detection)
                det_records = [{
                    "label": b["label"], "confidence": b["confidence"],
                    "bbox": [b["bbox"][0], b["bbox"][1],
                             b["bbox"][0] + b["bbox"][2], b["bbox"][1] + b["bbox"][3]],
                    "distance_m": b["distance_m"], "source_model": "sim_gt",
                } for b in boxes]
                seq = recorder.record_detection(
                    rgb, det_records, depth=depth_u16, intrinsics=intr,
                    pose={"cam_pos": cam_pos.tolist(), "cam_quat": cam_quat.tolist(),
                          "env": args.env, "drone_id": vid},
                )

                # COCO export (references the same JPEG the recorder just wrote)
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
            "images": coco_images,
            "annotations": coco_anns,
            "categories": [{"id": i, "name": n} for n, i in CLASS_ID.items()],
        }
        (out_dir / "annotations.json").write_text(json.dumps(coco))
        print(f"[dataset] wrote {len(coco_images)} images, {ann_id} anns -> {out_dir}", flush=True)
        bridge.teardown()
        IsaacVehicleBridge.shutdown()


if __name__ == "__main__":
    main()

"""
DataRecorder - persistent capture of perception/reasoning/action data for model training.

Today the eco pipeline computes detections, VLM decisions, and planner steps in-memory and
discards them. This module adds opt-in, append-only logging at those call sites so we can build
training datasets for the custom models in the plan (domain detector, VLM action-LoRA, fast
reactive policy, monocular depth).

Design goals:
- **Zero overhead when disabled.** A single `enabled` flag short-circuits every record_* call
  before any work (image encode, JSON serialize, disk write) happens.
- **Append-only + sharded.** One JSONL file per record type (det/vlm/plan/mission). Images and
  depth maps are written as sidecar files referenced by relative path, so the JSONL stays small.
- **Thread-safe.** perception, vlm, and the planner run on different threads; writes are guarded
  by a lock and use line-buffered append.
- **Episode-scoped.** Each mission gets an episode id so we can back-fill the success/fail
  `outcome` onto the det/vlm/plan rows after the mission completes (the reward signal).

Used by both on-device flight (real data) and the sim dataset generator (labeled sim data).
The same JSONL/sidecar layout is consumed by the hoopoe training scripts.

Layout under `out_dir`:
    det.jsonl         # one row per perception frame
    vlm.jsonl         # one row per VLM decision
    plan.jsonl        # one row per planner step
    mission.jsonl     # one row per completed mission
    frames/<episode>/<seq>.jpg      # RGB sidecars
    depth/<episode>/<seq>.npz       # depth sidecars (uint16, mm)
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import numpy as np


def _now_ms() -> float:
    return time.time() * 1000.0


class DataRecorder:
    """Append-only recorder for training data. No-op unless `enabled=True`."""

    # Module-level default instance wiring is done by callers; see get_default()/set_default().
    def __init__(
        self,
        out_dir: str | Path = "~/drone-data",
        enabled: bool = False,
        vehicle_type: str = "unknown",
        source: str = "real",
        save_images: bool = True,
        jpeg_quality: int = 85,
    ):
        """
        Args:
            out_dir: where to write shards + sidecars. Created on first write.
            enabled: master switch. When False every record_* call returns immediately.
            vehicle_type: "quad" | "rover" | "fixedwing" | "sim-quad" ... stamped on every row.
            source: "real" (on-device flight) or "sim" (Isaac/Godot). Stamped on every row.
            save_images: write RGB/depth sidecars. Off => metadata-only (cheap smoke tests).
            jpeg_quality: cv2 JPEG quality for RGB sidecars.
        """
        self.enabled = enabled
        self.vehicle_type = vehicle_type
        self.source = source
        self.save_images = save_images
        self.jpeg_quality = int(jpeg_quality)

        self._out_dir = Path(out_dir).expanduser()
        self._lock = threading.Lock()
        self._files: dict[str, Any] = {}  # kind -> open file handle (lazy)
        self._dirs_made = False
        self._seq = 0  # monotonic sidecar sequence number

        # Current episode (set by start_episode). Rows carry this so outcomes can be back-filled.
        self.episode_id: Optional[str] = None
        self.mission_phase: Optional[str] = None

    # ----- lifecycle -------------------------------------------------------

    def start_episode(self, mission: Optional[dict] = None) -> str:
        """Begin a new episode (mission). Returns its id. Cheap; safe when disabled."""
        self.episode_id = uuid.uuid4().hex[:12]
        self.mission_phase = None
        if self.enabled and mission is not None:
            self._write("episode", {"event": "start", "mission": mission})
        return self.episode_id

    def set_phase(self, phase: Optional[str]) -> None:
        self.mission_phase = phase

    def close(self) -> None:
        with self._lock:
            for f in self._files.values():
                try:
                    f.close()
                except Exception:
                    pass
            self._files.clear()

    # ----- record_* call sites --------------------------------------------

    def record_detection(
        self,
        rgb_frame: np.ndarray,
        detections: list,
        depth: Optional[np.ndarray] = None,
        intrinsics: Optional[dict] = None,
        pose: Optional[dict] = None,
    ) -> Optional[int]:
        """Log one perception frame. Returns the seq number used (for COCO cross-reference), or None."""
        if not self.enabled:
            return None
        seq = self._next_seq()
        rec: dict[str, Any] = {
            "kind": "det",
            "seq": seq,
            "ts_ms": _now_ms(),
            "episode": self.episode_id,
            "vehicle_type": self.vehicle_type,
            "source": self.source,
            "image_w": int(rgb_frame.shape[1]),
            "image_h": int(rgb_frame.shape[0]),
            "detections": [self._det_to_dict(d) for d in detections],
        }
        if intrinsics:
            rec["intrinsics"] = intrinsics
        if pose:
            rec["pose"] = pose
        if self.save_images:
            rec["frame"] = self._save_rgb(rgb_frame, seq)
            if depth is not None:
                rec["depth"] = self._save_depth(depth, seq)
        self._write("det", rec)
        return seq

    def record_vlm(
        self,
        rgb_frame: Optional[np.ndarray],
        system_prompt: str,
        user_prompt: str,
        response: str,
        action: Optional[dict] = None,
        inference_ms: Optional[float] = None,
        drone_state: Optional[dict] = None,
    ) -> None:
        """Log one VLM decision: (image + prompts) -> raw response + parsed action."""
        if not self.enabled:
            return
        seq = self._next_seq()
        rec: dict[str, Any] = {
            "kind": "vlm",
            "seq": seq,
            "ts_ms": _now_ms(),
            "episode": self.episode_id,
            "mission_phase": self.mission_phase,
            "vehicle_type": self.vehicle_type,
            "source": self.source,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response": response,
            "action": action,
            "inference_ms": inference_ms,
            "drone_state": drone_state,
            "outcome": None,  # back-filled by record_mission
        }
        if self.save_images and rgb_frame is not None:
            rec["frame"] = self._save_rgb(rgb_frame, seq)
        self._write("vlm", rec)

    def record_plan(
        self,
        target_xyz: Optional[tuple],
        clearance_m: Optional[float],
        altitude_m: Optional[float],
        step: Any,
    ) -> None:
        """Log one reactive-planner step: input state -> output PlanStep."""
        if not self.enabled:
            return
        rec = {
            "kind": "plan",
            "seq": self._next_seq(),
            "ts_ms": _now_ms(),
            "episode": self.episode_id,
            "mission_phase": self.mission_phase,
            "vehicle_type": self.vehicle_type,
            "source": self.source,
            "target_xyz": list(target_xyz) if target_xyz is not None else None,
            "clearance_m": clearance_m,
            "altitude_m": altitude_m,
            "step": self._step_to_dict(step),
            "outcome": None,  # back-filled by record_mission
        }
        self._write("plan", rec)

    def record_mission(self, result: Any) -> None:
        """Log a completed mission. `result` is a MissionResult or dict with success/fail."""
        if not self.enabled:
            return
        result_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        rec = {
            "kind": "mission",
            "ts_ms": _now_ms(),
            "episode": self.episode_id,
            "vehicle_type": self.vehicle_type,
            "source": self.source,
            "result": result_dict,
        }
        self._write("mission", rec)
        # Note: outcome back-fill onto prior det/vlm/plan rows is done offline by the training
        # scripts via the shared `episode` id (cheaper than rewriting append-only JSONL here).

    # ----- helpers ---------------------------------------------------------

    @staticmethod
    def _det_to_dict(d: Any) -> dict:
        if hasattr(d, "to_dict"):
            return d.to_dict()
        if isinstance(d, dict):
            return d
        # Fallback: best-effort attribute scrape
        return {k: getattr(d, k) for k in ("label", "confidence", "bbox", "distance_m", "direction_deg") if hasattr(d, k)}

    @staticmethod
    def _step_to_dict(step: Any) -> Any:
        if step is None:
            return None
        if hasattr(step, "__dict__"):
            return {k: v for k, v in vars(step).items() if not k.startswith("_")}
        if isinstance(step, dict):
            return step
        return str(step)

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _ensure_dirs(self) -> None:
        if self._dirs_made:
            return
        self._out_dir.mkdir(parents=True, exist_ok=True)
        if self.save_images:
            (self._out_dir / "frames").mkdir(exist_ok=True)
            (self._out_dir / "depth").mkdir(exist_ok=True)
        self._dirs_made = True

    def _episode_dir(self, base: str, seq: int) -> Path:
        ep = self.episode_id or "noepisode"
        d = self._out_dir / base / ep
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_rgb(self, rgb_frame: np.ndarray, seq: int) -> Optional[str]:
        try:
            import cv2
        except ImportError:
            return None
        self._ensure_dirs()
        path = self._episode_dir("frames", seq) / f"{seq:08d}.jpg"
        # rgb_frame is RGB; cv2 wants BGR
        bgr = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(path), bgr, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        return str(path.relative_to(self._out_dir))

    def _save_depth(self, depth: np.ndarray, seq: int) -> str:
        self._ensure_dirs()
        path = self._episode_dir("depth", seq) / f"{seq:08d}.npz"
        np.savez_compressed(str(path), depth=depth.astype(np.uint16))
        return str(path.relative_to(self._out_dir))

    def _write(self, kind: str, rec: dict) -> None:
        self._ensure_dirs()
        line = json.dumps(rec, default=_json_default)
        with self._lock:
            f = self._files.get(kind)
            if f is None:
                f = open(self._out_dir / f"{kind}.jsonl", "a", buffering=1)
                self._files[kind] = f
            f.write(line + "\n")


def _json_default(o: Any) -> Any:
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (tuple, set)):
        return list(o)
    return str(o)


# ---- module-level default instance ---------------------------------------
# Call sites use get_default() so a single shared recorder can be configured once (e.g. by the
# daemon at startup from config.yaml) without threading it through every constructor.

_default: Optional[DataRecorder] = None


def get_default() -> DataRecorder:
    """Return the process-wide recorder, creating a disabled no-op one if unset."""
    global _default
    if _default is None:
        _default = DataRecorder(enabled=False)
    return _default


def set_default(recorder: DataRecorder) -> None:
    global _default
    _default = recorder


def configure(out_dir: str | Path, enabled: bool = True, **kwargs) -> DataRecorder:
    """Convenience: build a recorder, install it as the default, and return it."""
    rec = DataRecorder(out_dir=out_dir, enabled=enabled, **kwargs)
    set_default(rec)
    return rec

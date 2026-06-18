"""Classical reactive planner (per the "Closing the Metric Gap" hardened Track A).

Rules implemented:
- Depth-conditioned speed ramp: linear 1.0 -> 3.0 m/s between 1.0 and 3.0 m clearance.
- Altitude floor: 0.8 m (vz clamp to non-descending if we'd go below).
- Max 3.0 m/step translational clamp.
- Stall detector: halt after N steps with progress < 0.3 m toward target.
- Confidence gate: reject targets outside [0.3, 15.0] m.

LearnedPlanner: same external API, backed by policy_v2.onnx (GRU + MLP, 78 KB).
Toggle with: planner = make_planner(use_learned=True, models_dir=...)
"""

from dataclasses import dataclass
from typing import Optional
import math


@dataclass
class PlanStep:
    vx: float        # body-frame forward (m/s)
    vy: float        # body-frame left (m/s)
    vz: float        # vertical (m/s, negative = up in body/NED)
    yaw_rate: float  # rad/s
    reason: str      # debug string
    reached: bool = False
    rejected: bool = False


class ReactivePlanner:
    def __init__(
        self,
        max_speed: float = 3.0,
        min_speed: float = 1.0,
        clear_near: float = 1.0,
        clear_far: float = 3.0,
        altitude_floor: float = 0.8,
        max_step_m: float = 3.0,
        reach_threshold: float = 1.0,
        range_gate: tuple = (0.3, 15.0),
        stall_steps: int = 5,
        stall_progress: float = 0.3,
    ):
        self.max_speed = max_speed
        self.min_speed = min_speed
        self.clear_near = clear_near
        self.clear_far = clear_far
        self.altitude_floor = altitude_floor
        self.max_step_m = max_step_m
        self.reach_threshold = reach_threshold
        self.range_gate = range_gate
        self.stall_steps = stall_steps
        self.stall_progress = stall_progress

        self._stall_buffer = []  # list of distances from recent steps

    def reset(self):
        self._stall_buffer.clear()

    def _speed_from_clearance(self, clearance: float) -> float:
        """Linear ramp: <=clear_near -> min_speed; >=clear_far -> max_speed."""
        if clearance is None or not math.isfinite(clearance):
            return self.min_speed
        if clearance <= self.clear_near:
            return self.min_speed
        if clearance >= self.clear_far:
            return self.max_speed
        frac = (clearance - self.clear_near) / (self.clear_far - self.clear_near)
        return self.min_speed + frac * (self.max_speed - self.min_speed)

    def step(
        self,
        target_xyz: Optional[tuple],
        clearance_m: Optional[float],
        altitude_m: Optional[float] = None,
        dt: float = 0.1,
    ) -> PlanStep:
        """Produce one velocity command toward target, applying all safety rules.

        `target_xyz`: (forward, left, up) in meters, body frame. None = no target seen this tick.
        `clearance_m`: depth directly ahead (straight in front of drone).
        `altitude_m`: current altitude above takeoff (for floor enforcement).
        """
        result = self._step_impl(target_xyz, clearance_m, altitude_m, dt)

        # Optional training-data capture (no-op unless a recorder is enabled).
        try:
            from data_recorder import get_default
            recorder = get_default()
            if recorder.enabled:
                recorder.record_plan(target_xyz, clearance_m, altitude_m, result)
        except Exception:
            pass

        return result

    def _step_impl(
        self,
        target_xyz: Optional[tuple],
        clearance_m: Optional[float],
        altitude_m: Optional[float],
        dt: float,
    ) -> PlanStep:
        if target_xyz is None:
            return PlanStep(0.0, 0.0, 0.0, 0.0, "no-target-visible")

        fx, fy, fz = target_xyz
        dist = math.sqrt(fx * fx + fy * fy + fz * fz)

        # Confidence / range gate
        if dist < self.range_gate[0] or dist > self.range_gate[1]:
            return PlanStep(0.0, 0.0, 0.0, 0.0,
                            f"range-gate dist={dist:.2f}m outside {self.range_gate}",
                            rejected=True)

        # Reach?
        if dist <= self.reach_threshold:
            return PlanStep(0.0, 0.0, 0.0, 0.0, f"reached dist={dist:.2f}m",
                            reached=True)

        # Stall detection
        self._stall_buffer.append(dist)
        if len(self._stall_buffer) > self.stall_steps:
            self._stall_buffer.pop(0)
        if len(self._stall_buffer) == self.stall_steps:
            progress = self._stall_buffer[0] - self._stall_buffer[-1]
            if progress < self.stall_progress:
                return PlanStep(0.0, 0.0, 0.0, 0.0,
                                f"stall progress={progress:.2f}m over {self.stall_steps} steps",
                                rejected=True)

        # Speed from clearance
        speed = self._speed_from_clearance(clearance_m)

        # Unit vector toward target (body frame)
        nx, ny, nz = fx / dist, fy / dist, fz / dist

        # Scale, then clamp per-step distance
        step_m = min(speed * dt, self.max_step_m)
        vx = nx * speed
        vy = ny * speed
        vz = nz * speed

        # Altitude floor: vz here is up-component (+ = climb, - = descend).
        # If below floor and descending, zero the descent.
        if altitude_m is not None and altitude_m < self.altitude_floor and vz < 0:
            vz = 0.0

        return PlanStep(
            vx=vx, vy=vy, vz=vz, yaw_rate=0.0,
            reason=f"dist={dist:.2f}m clear={clearance_m:.2f}m speed={speed:.2f}m/s step={step_m:.2f}m"
            if clearance_m is not None else
            f"dist={dist:.2f}m speed={speed:.2f}m/s step={step_m:.2f}m",
        )


# ---------------------------------------------------------------------------
# Learned planner — policy_v2.onnx (GRU, 78 KB) behind the same step() API
# ---------------------------------------------------------------------------

class LearnedPlanner:
    """Wraps policy_v3.onnx with the same step() signature as ReactivePlanner.

    policy_v3 sees a forward DEPTH FAN (9 rays across the 70° FOV) instead of a single
    clearance scalar, so it can *steer around* obstacles, not merely slow for them. Pass the
    fan via `depth_fan=`; a scalar `clearance_m` is still accepted (expanded to a flat fan) for
    drop-in compatibility with the rule planner's call sites.

    Safety decisions (range-gate, reach, stall, altitude floor) are still handled by the
    rule-based layer — the net only drives the velocity setpoint when the rule layer passes.

    models_dir: directory containing policy_v3.onnx + policy_v3_state_norm.npy.
    Falls back to a proportional command if onnxruntime is unavailable or models are missing.
    """

    SEQ_LEN = 16   # must match training (train.py SEQ_LEN)
    DEPTH_RAYS = 45   # policy_v4: 5x9 depth grid (was 9 for v3). 11 base + 45 = 56-dim state.
    STATE_DIM = 11 + DEPTH_RAYS   # 56
    ACTION_DIM = 4
    DEPTH_MAX = 10.0

    def __init__(
        self,
        models_dir: Optional[str] = None,
        reach_threshold: float = 1.0,
        max_speed: float = 3.0,
        altitude_floor: float = 0.8,
        range_gate: tuple = (0.3, 15.0),
        stall_steps: int = 30,   # lenient: model may rotate before translating
        stall_progress: float = 0.1,
        vehicle: float = 0.0,    # 0.0 = quad, 1.0 = rover
        onnx_name: str = "policy_v4_dr.onnx",   # 3D depth-grid policy; "policy_v3_rl.onnx" = old 2D
    ):
        import numpy as np
        from pathlib import Path

        self.reach_threshold = reach_threshold
        self.max_speed = max_speed
        self.altitude_floor = altitude_floor
        self.range_gate = range_gate
        self.stall_steps = stall_steps
        self.stall_progress = stall_progress
        self.vehicle = vehicle
        self._stall_buffer = []

        # locate models
        if models_dir is None:
            models_dir = Path(__file__).parent.parent / "models"
        else:
            models_dir = Path(models_dir)

        onnx_path = models_dir / onnx_name
        norm_path = models_dir / onnx_name.replace(".onnx", "_state_norm.npy")
        self._session = None

        try:
            import onnxruntime as ort
            if not onnx_path.exists():
                raise FileNotFoundError(f"{onnx_name} not found at {onnx_path}")
            self._session = ort.InferenceSession(
                str(onnx_path), providers=["CPUExecutionProvider"]
            )
            self._input_name = self._session.get_inputs()[0].name
            # The model normalizes internally (mean/std baked into ONNX buffers).
            # Do NOT pre-normalize inputs here.
        except Exception as e:
            print(f"[LearnedPlanner] fallback to rule-based: {e}")

        # ring buffer of raw state vectors for the GRU window
        self._history = np.zeros((self.SEQ_LEN, self.STATE_DIM), dtype=np.float32)
        self._cur_yaw_rate = 0.0   # carry last commanded yaw_rate for state assembly
        self._cur_vel = np.zeros(3, dtype=np.float32)  # carry last vel for state
        self._warmup_ticks = 0     # suppress stall detection while GRU warms up

    def reset(self) -> None:
        import numpy as np
        self._history = np.zeros((self.SEQ_LEN, self.STATE_DIM), dtype=np.float32)
        self._stall_buffer.clear()
        self._cur_yaw_rate = 0.0
        self._cur_vel = np.zeros(3, dtype=np.float32)
        self._warmup_ticks = 0

    def _resolve_fan(self, depth_fan, clearance_m) -> "np.ndarray":
        """Return a DEPTH_RAYS depth vector from an explicit fan or a scalar clearance."""
        import numpy as np
        if depth_fan is not None:
            fan = np.asarray(depth_fan, dtype=np.float32).reshape(-1)
            if fan.shape[0] != self.DEPTH_RAYS:
                raise ValueError(f"depth_fan must have {self.DEPTH_RAYS} rays")
        elif clearance_m is not None:
            fan = np.full(self.DEPTH_RAYS, float(clearance_m), dtype=np.float32)
        else:
            fan = np.full(self.DEPTH_RAYS, self.DEPTH_MAX, dtype=np.float32)
        return np.clip(fan, 0.0, self.DEPTH_MAX)

    def _build_state_vec(
        self,
        target_xyz: Optional[tuple],
        depth_fan: "np.ndarray",
        altitude_m: Optional[float],
    ) -> "np.ndarray":
        """Build raw (un-normalized) state vector. The ONNX model normalizes internally."""
        import numpy as np
        tf = tl = tu = 0.0
        dist = 0.0
        yaw_err = 0.0
        if target_xyz is not None:
            tf, tl, tu = (float(x) for x in target_xyz)
            dist = math.sqrt(tf*tf + tl*tl + tu*tu)
            yaw_err = (math.atan2(tl, tf) + math.pi) % (2*math.pi) - math.pi if (tf or tl) else 0.0

        altitude = float(altitude_m) if altitude_m is not None else 0.0

        base = np.array([
            tf, tl, tu, dist,
            self._cur_vel[0], self._cur_vel[1], self._cur_vel[2],
            yaw_err, self._cur_yaw_rate,
            altitude, self.vehicle,
        ], dtype=np.float32)
        return np.concatenate([base, depth_fan.astype(np.float32)])

    def step(
        self,
        target_xyz: Optional[tuple],
        clearance_m: Optional[float] = None,
        altitude_m: Optional[float] = None,
        dt: float = 0.1,
        depth_fan=None,
    ) -> PlanStep:
        import numpy as np

        # --- safety layer (identical to ReactivePlanner) ---
        if target_xyz is None:
            return PlanStep(0.0, 0.0, 0.0, 0.0, "no-target-visible")

        fx, fy, fz = target_xyz
        dist = math.sqrt(fx*fx + fy*fy + fz*fz)

        if dist < self.range_gate[0] or dist > self.range_gate[1]:
            return PlanStep(0.0, 0.0, 0.0, 0.0,
                            f"range-gate dist={dist:.2f}m", rejected=True)
        if dist <= self.reach_threshold:
            return PlanStep(0.0, 0.0, 0.0, 0.0, f"reached dist={dist:.2f}m", reached=True)

        # Suppress stall for the first SEQ_LEN ticks while the GRU warms up from zeros.
        self._warmup_ticks += 1
        if self._warmup_ticks == self.SEQ_LEN + 1:
            self._stall_buffer.clear()  # flush any pre-warmup drift from the buffer
        if self._warmup_ticks > self.SEQ_LEN:
            self._stall_buffer.append(dist)
            if len(self._stall_buffer) > self.stall_steps:
                self._stall_buffer.pop(0)
            if len(self._stall_buffer) == self.stall_steps:
                progress = self._stall_buffer[0] - self._stall_buffer[-1]
                if progress < self.stall_progress:
                    return PlanStep(0.0, 0.0, 0.0, 0.0,
                                    f"stall progress={progress:.2f}m", rejected=True)

        # --- learned velocity setpoint ---
        if self._session is None:
            # onnxruntime not available; emit a simple proportional command
            speed = min(self.max_speed, dist)
            nx, ny, nz = fx/dist, fy/dist, fz/dist
            return PlanStep(nx*speed, ny*speed, nz*speed, 0.0, "learned-fallback")

        fan = self._resolve_fan(depth_fan, clearance_m)
        state_vec = self._build_state_vec(target_xyz, fan, altitude_m)
        # shift history window and append latest state
        self._history = np.roll(self._history, -1, axis=0)
        self._history[-1] = state_vec

        inp = self._history[np.newaxis]   # (1, SEQ_LEN, STATE_DIM)
        out = self._session.run(None, {self._input_name: inp})[0][0]  # (ACTION_DIM,)

        vx, vy, vz, yaw_rate = (float(x) for x in out)

        # speed cap (policy_v3 was trained ≤ max_speed but clamp as safety net)
        spd = math.sqrt(vx*vx + vy*vy + vz*vz)
        if spd > self.max_speed and spd > 0:
            s = self.max_speed / spd
            vx, vy, vz = vx*s, vy*s, vz*s

        # altitude floor
        if altitude_m is not None and altitude_m < self.altitude_floor and vz < 0:
            vz = 0.0

        # carry state for next tick
        self._cur_vel[:] = [vx, vy, vz]
        self._cur_yaw_rate = yaw_rate

        return PlanStep(
            vx=vx, vy=vy, vz=vz, yaw_rate=yaw_rate,
            reason=f"learned dist={dist:.2f}m minclear={float(fan.min()):.2f}m",
        )


def make_planner(
    use_learned: bool = False,
    models_dir: Optional[str] = None,
    reach_threshold: float = 1.0,
    max_speed: float = 3.0,
    vehicle: float = 0.0,
) -> "ReactivePlanner | LearnedPlanner":
    """Factory: returns a LearnedPlanner or ReactivePlanner with matching params."""
    if use_learned:
        return LearnedPlanner(
            models_dir=models_dir,
            reach_threshold=reach_threshold,
            max_speed=max_speed,
            vehicle=vehicle,
        )
    return ReactivePlanner(reach_threshold=reach_threshold, max_speed=max_speed)

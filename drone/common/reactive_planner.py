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
        onnx_name: str = "policy_v26rnn_dr.onnx",  # 3D depth-grid GRU policy; "policy_v4_dr.onnx" = old non-recurrent
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
        self._recurrent = False

        try:
            import onnxruntime as ort
            if not onnx_path.exists():
                raise FileNotFoundError(f"{onnx_name} not found at {onnx_path}")
            self._session = ort.InferenceSession(
                str(onnx_path), providers=["CPUExecutionProvider"]
            )
            self._input_name = self._session.get_inputs()[0].name
            # Recurrent (step-mode) export carries a hidden state: inputs (state, h_in) ->
            # (action, h_out). Window export takes (state_window) -> (action). Detect by input name.
            in_names = [i.name for i in self._session.get_inputs()]
            self._recurrent = "h_in" in in_names
            if self._recurrent:
                h_shape = self._session.get_inputs()[in_names.index("h_in")].shape
                self._hidden = int(h_shape[-1]) if isinstance(h_shape[-1], int) else 128
            # The model normalizes internally (mean/std baked into ONNX buffers).
            # Do NOT pre-normalize inputs here.
        except Exception as e:
            print(f"[LearnedPlanner] fallback to rule-based: {e}")

        # ring buffer of raw state vectors for the GRU window (window-mode)
        self._history = np.zeros((self.SEQ_LEN, self.STATE_DIM), dtype=np.float32)
        self._h = np.zeros((1, 1, getattr(self, "_hidden", 1)), dtype=np.float32)  # recurrent state
        self._cur_yaw_rate = 0.0   # carry last commanded yaw_rate for state assembly
        self._cur_vel = np.zeros(3, dtype=np.float32)  # carry last vel for state
        self._warmup_ticks = 0     # suppress stall detection while GRU warms up

    def reset(self) -> None:
        import numpy as np
        self._history = np.zeros((self.SEQ_LEN, self.STATE_DIM), dtype=np.float32)
        if getattr(self, "_recurrent", False):
            self._h = np.zeros((1, 1, self._hidden), dtype=np.float32)
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

        if self._recurrent:
            # step-mode: feed one frame + carried hidden state, get action + next hidden state
            inp = state_vec[np.newaxis, np.newaxis]   # (1, 1, STATE_DIM)
            out = self._session.run(None, {"state": inp, "h_in": self._h})
            act = out[0][0]                            # (ACTION_DIM,)
            self._h = out[1]                           # (1, 1, hidden)
        else:
            # window-mode: shift history window and append latest state
            self._history = np.roll(self._history, -1, axis=0)
            self._history[-1] = state_vec
            inp = self._history[np.newaxis]            # (1, SEQ_LEN, STATE_DIM)
            act = self._session.run(None, {self._input_name: inp})[0][0]

        vx, vy, vz, yaw_rate = (float(x) for x in act)

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


# ---------------------------------------------------------------------------
# Hierarchical planner — global A* route + LearnedPlanner local tracking
# ---------------------------------------------------------------------------

class HierarchicalPlanner:
    """A* (global route) wrapped around LearnedPlanner (local smoothing + depth avoidance).

    The reactive policy alone can't sustain a multi-stage commitment (duck UNDER -> climb OVER ->
    thread THROUGH) because it only ever sees the far global goal plus the local depth grid: coming
    out of a duck it treats the next wall as a lateral obstacle and tries to skirt it instead of
    committing to the climb. This wrapper runs A* on an occupancy estimate to get a collision-free
    route, then feeds the policy a target **redirected along the next path segment** — turning the
    gauntlet into a sequence of the single-obstacle problems the policy already solves well.

    Key detail: the redirected target is placed at *cruise range* (`cruise_horizon`), not at the
    nearby waypoint, so the policy still sees a "far goal in this direction" and cruises instead of
    decelerating as if arriving. Goal-reach and stall are tracked HERE on the true goal distance;
    the inner policy's own reach/range/stall gates are disabled so it only emits a velocity.

    Occupancy source is pluggable via the `boxes` arg to step(): pass the known course geometry for
    the sim proof, or an accumulated local voxel map (from the depth grid) on the vehicle. The A*
    interface (planner3d.astar_path) is identical either way.
    """

    def __init__(
        self,
        models_dir: Optional[str] = None,
        onnx_name: str = "policy_v4_dr.onnx",
        vehicle: float = 0.0,
        max_speed: float = 3.0,
        reach_threshold: float = 1.0,
        altitude_floor: float = 0.8,
        lookahead: float = 2.0,
        cruise_horizon: float = 6.0,
        replan_every: int = 10,
        inflate: float = 0.55,
        stall_steps: int = 40,
        stall_progress: float = 0.2,
        online_occupancy: bool = True,
        occ_res: float = 0.4,
    ):
        # Inner policy: disable its reach/range/stall gates — this wrapper owns those decisions.
        self.policy = LearnedPlanner(
            models_dir=models_dir, onnx_name=onnx_name, vehicle=vehicle,
            max_speed=max_speed, altitude_floor=altitude_floor,
            reach_threshold=-1.0, range_gate=(0.0, 1e18), stall_steps=10**9,
        )
        self.reach_threshold = reach_threshold
        self.lookahead = lookahead
        self.cruise_horizon = cruise_horizon
        self.replan_every = replan_every
        self.inflate = inflate
        self.stall_steps = stall_steps
        self.stall_progress = stall_progress
        self.max_speed = max_speed
        self.online_occupancy = online_occupancy
        self._occ = None
        if online_occupancy:
            try:
                from occupancy import OccupancyMap
            except ImportError:
                from eco.drone.common.occupancy import OccupancyMap
            self._occ = OccupancyMap(res=occ_res)
        self._follower = None
        self._tick = 0
        self._stall = []

    @property
    def using_policy(self) -> bool:
        return self.policy._session is not None

    @property
    def n_voxels(self) -> int:
        return self._occ.n_voxels if self._occ is not None else 0

    def reset(self) -> None:
        self.policy.reset()
        if self._occ is not None:
            self._occ.reset()
        self._follower = None
        self._tick = 0
        self._stall = []

    def _replan(self, pos, goal, boxes) -> None:
        try:
            from planner3d import PathFollower
        except ImportError:
            from eco.drone.training.planner3d import PathFollower
        if self.online_occupancy:
            from occupancy import astar_occupancy
            path = astar_occupancy(tuple(pos), tuple(goal), self._occ, inflate=self.inflate)
        else:
            try:
                from planner3d import astar_path
            except ImportError:
                from eco.drone.training.planner3d import astar_path
            path = astar_path(tuple(pos), tuple(goal), boxes, inflate=self.inflate)
        self._follower = PathFollower(path, lookahead=self.lookahead) if path else None

    def step(self, pos, yaw, goal, depth_fan=None, dt: float = 0.1, boxes=None) -> PlanStep:
        """One velocity command. `pos`,`goal` are world (x,y,z); `yaw` world heading (rad).

        `depth_fan`: DEPTH_RAYS forward depth grid (the only obstacle sense on the vehicle).
        `boxes`: used ONLY when online_occupancy=False (privileged sim proof). In online mode the
        depth grid is accumulated into an internal voxel map and A* plans over that estimate.
        """
        import numpy as np
        pos = np.asarray(pos, dtype=float)
        goal_v = np.asarray(goal, dtype=float)
        goal_dist = float(np.linalg.norm(goal_v - pos))

        # --- accumulate perception every tick (continuous; replanning is periodic) ---
        if self.online_occupancy and depth_fan is not None:
            self._occ.integrate(pos, yaw, depth_fan)

        # --- outer reach (true goal) ---
        if goal_dist <= self.reach_threshold:
            return PlanStep(0.0, 0.0, 0.0, 0.0, f"reached dist={goal_dist:.2f}m", reached=True)

        # --- outer stall on true goal distance ---
        self._stall.append(goal_dist)
        if len(self._stall) > self.stall_steps:
            self._stall.pop(0)
        if len(self._stall) == self.stall_steps:
            prog = self._stall[0] - self._stall[-1]
            if prog < self.stall_progress:
                return PlanStep(0.0, 0.0, 0.0, 0.0,
                                f"stall progress={prog:.2f}m over {self.stall_steps} steps",
                                rejected=True)

        # --- (re)plan the global route ---
        boxes = boxes or []
        if self._follower is None or self._tick % self.replan_every == 0:
            self._replan(pos, goal_v, boxes)
        self._tick += 1

        # lookahead world point on the path (fall back to straight-at-goal if planning failed)
        if self._follower is None:
            look_w = goal_v
        else:
            look_w = np.asarray(self._follower.target(pos), dtype=float)

        dir_w = look_w - pos
        n = float(np.linalg.norm(dir_w))
        if n < 1e-6:
            dir_w = goal_v - pos
            n = max(float(np.linalg.norm(dir_w)), 1e-6)
        dir_w = dir_w / n

        # Redirect a CRUISE-RANGE target along the path direction so the policy keeps cruise speed
        # and reads the next segment as "the goal is that way" (e.g. up-and-over the wall).
        horizon = min(goal_dist, self.cruise_horizon)
        d = dir_w * horizon
        c, s = math.cos(-yaw), math.sin(-yaw)
        target_body = (c * d[0] - s * d[1], s * d[0] + c * d[1], d[2])

        plan = self.policy.step(target_body, depth_fan=depth_fan,
                                altitude_m=float(pos[2]), dt=dt)
        # the inner policy never decides reach/stall here
        plan.reached = False
        plan.rejected = False
        return plan


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

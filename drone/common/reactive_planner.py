"""Classical reactive planner (per the "Closing the Metric Gap" hardened Track A).

Rules implemented:
- Depth-conditioned speed ramp: linear 1.0 -> 3.0 m/s between 1.0 and 3.0 m clearance.
- Altitude floor: 0.8 m (vz clamp to non-descending if we'd go below).
- Max 3.0 m/step translational clamp.
- Stall detector: halt after N steps with progress < 0.3 m toward target.
- Confidence gate: reject targets outside [0.3, 15.0] m.
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

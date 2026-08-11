"""Smooth-expert teachers for behavioral cloning, with 3D obstacle avoidance.

These are the BC *teachers*: continuously varying, accel/jerk-limited velocity setpoints that
also find a path around/over/under obstacles seen in the forward depth grid — the behavior the
policy clones, then RL improves.

3D gap-following (quad, holonomic): when the corridor toward the goal is blocked, steer toward
the depth-grid direction that is both clear and most goal-aligned — which may be left, right,
up, down, or any blend. The quad keeps facing the goal (camera on the obstacle) and translates
freely. Rover stays planar (uses the horizontal centre row) and is nonholonomic.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from drone.common.contract import (
    State, ACTION_DIM, VEHICLE_ROVER, wrap_pi,
    RAY_DIRS, DEPTH_MAX, DEPTH_COLS, DEPTH_ROWS,
)

_RAYS = np.asarray(RAY_DIRS, dtype=np.float64)          # (R,3) fwd/left/up unit dirs
_CENTER_ROW = (DEPTH_ROWS // 2) * DEPTH_COLS            # index of leftmost ray in middle row


@dataclass
class ExpertLimits:
    max_speed: float = 3.0
    min_speed: float = 0.5
    max_accel: float = 2.6        # more agile -> faster duck/climb transitions
    max_jerk: float = 5.0
    max_yaw_rate: float = 1.5
    max_yaw_accel: float = 3.0
    reach_threshold: float = 0.4
    altitude_floor: float = 0.8
    influence_m: float = 6.0      # obstacles closer than this start diverting the path
    safe_m: float = 1.8           # nearest-return distance that forces ~min_speed
    k_align: float = 0.6          # weight on goal-alignment when scoring gaps
    k_commit: float = 1.3         # commitment strength toward the chosen gap when blocked


class _SmoothExpertBase:
    def __init__(self, limits: ExpertLimits | None = None):
        self.lim = limits or ExpertLimits()
        self.reset()

    def reset(self) -> None:
        self._prev_vel = np.zeros(3, dtype=np.float64)
        self._prev_dv = np.zeros(3, dtype=np.float64)
        self._prev_yaw_rate = 0.0
        self._reached = False

    def _is_reached(self, dist: float) -> bool:
        """Hysteresis around reach_threshold so a tiny post-capture overshoot (physically
        unavoidable: stopping distance at cruise speed exceeds reach_threshold under the
        jerk/accel limits) doesn't re-trigger full-speed pursuit and start a hunting
        oscillation that never damps. Once captured, stay in station-keep/decel mode until
        actually drifting well away, rather than flip-flopping on every micro-overshoot.
        """
        if self._reached:
            if dist > self.lim.reach_threshold * 2.5:
                self._reached = False
        elif dist <= self.lim.reach_threshold:
            self._reached = True
        return self._reached

    def _cruise_speed(self, min_clearance: float) -> float:
        lim = self.lim
        if min_clearance is None or not math.isfinite(min_clearance):
            return lim.max_speed
        if min_clearance <= lim.safe_m:
            return lim.min_speed
        if min_clearance >= lim.influence_m:
            return lim.max_speed
        frac = (min_clearance - lim.safe_m) / (lim.influence_m - lim.safe_m)
        return lim.min_speed + frac * (lim.max_speed - lim.min_speed)

    def _decel_limited_speed(self, dist: float, cruise: float) -> float:
        v_stop = math.sqrt(max(0.0, 2.0 * self.lim.max_accel * 0.4 * max(0.0, dist)))
        return min(cruise, v_stop)

    def _steer_dir(self, target, depth, altitude=10.0, planar=False):
        """Return a unit 3D steering direction (fwd, left, up) toward the goal that flies into
        the freest opening — which threads a WINDOW/gap as readily as it skirts a solid.

        Method: when the goal corridor is blocked, steer toward the depth-grid-weighted average
        of OPEN, goal-ward ray directions. A hole on the goal axis lights up those rays (clear +
        aligned) so we aim through it; a solid wall leaves only the over/under/around rays open
        so we divert that way. If the whole goal-ward view is walled, climb to find a way over.
        """
        lim = self.lim
        g = np.array(target, dtype=np.float64)
        if planar:
            g[2] = 0.0
        gn = float(np.linalg.norm(g))
        g = g / gn if gn > 1e-6 else np.array([1.0, 0.0, 0.0])

        d = np.asarray(depth, dtype=np.float64).reshape(-1)
        rays = _RAYS
        align = rays @ g                                    # cos angle of each ray to the goal
        # Hazard uses the GLOBAL nearest reading, not just the goal-aligned cone: a corner can
        # be dangerously close while sitting outside that cone (e.g. abeam while cutting past
        # it), and a cone-only check would miss it and never blend toward the escape direction
        # in time — the vehicle keeps closing on a surface it isn't "looking at".
        nearest = float(d.min())
        blocked = float(np.clip((lim.influence_m - nearest) /
                                max(lim.influence_m - lim.safe_m, 1e-6), 0.0, 1.0))
        if blocked <= 0.0:
            return g

        usable = np.ones(len(d), dtype=bool)
        if planar:
            usable = np.abs(rays[:, 2]) < 0.35              # rover: only near-horizontal rays

        # blur the depth grid so a ray surrounded by open space scores higher than one grazing
        # a wall edge -> the chosen direction has MARGIN (threads a hole through its centre,
        # rounds a solid with clearance) instead of cutting the corner.
        grid = d.reshape(DEPTH_ROWS, DEPTH_COLS)
        pad = np.pad(grid, 1, mode="edge")
        blur = sum(pad[i:i + DEPTH_ROWS, j:j + DEPTH_COLS]
                   for i in range(3) for j in range(3)) / 9.0
        blur = blur.reshape(-1)

        if float(np.where(usable, d, 0.0).max()) > lim.safe_m + 0.3:
            # a ray sees clearly past the wall (hole / over / under / side gap) -> GAP-FOLLOW on
            # the BLURRED clearance (argmax, not average -> symmetric openings don't cancel).
            # Veto on the BLURRED value too, not just the ray's own raw depth: a ray can be
            # clear along its own exact line while grazing a corner just outside it (its
            # neighbors read close) — blur is what actually captures "has margin", so it must
            # gate which rays are even eligible, not just break the score tie.
            #
            # Fade the goal-alignment bonus out as `blocked` rises toward full commitment:
            # without this, k_align keeps rewarding "closest-to-goal-heading ray that's still
            # nominally clear," which is a minimum-viable-diversion strategy — it picks a ray
            # that clears an obstacle's silhouette by the thinnest margin the fan can resolve
            # instead of decisively diverting. At full commitment the choice should be driven
            # by clearance alone; goal-seeking resumes once no longer blocked.
            k_eff = lim.k_align * (1.0 - blocked)
            score = blur / DEPTH_MAX + k_eff * align
            score[blur < lim.safe_m] = -1e9
            score[~usable] = -1e9
            score[align < -0.1] = -1e9                      # don't steer backwards
            cand = rays[int(np.argmax(score))].astype(np.float64)
        else:
            # fully walled within view -> commit decisively to the most-open quadrant by mean
            # clearance (works even when nothing is "clear" yet); climb if all sides walled.
            grid = d.reshape(DEPTH_ROWS, DEPTH_COLS)
            nc = DEPTH_COLS // 3 or 1
            nr = DEPTH_ROWS // 2 or 1
            opts = [(float(grid[:, :nc].mean()), (0.0, 1.0, 0.0)),
                    (float(grid[:, -nc:].mean()), (0.0, -1.0, 0.0))]
            if not planar:
                dn = float(grid[-nr:, :].mean())
                if altitude < lim.altitude_floor + 1.0:
                    dn = -1.0
                opts += [(float(grid[:nr, :].mean()), (0.0, 0.0, 1.0)), (dn, (0.0, 0.0, -1.0))]
            cand = np.array(max(opts, key=lambda o: o[0])[1], dtype=np.float64)

        if planar:
            cand[2] = 0.0
        cn = float(np.linalg.norm(cand))
        cand = cand / cn if cn > 1e-6 else g
        if altitude < lim.altitude_floor + 0.5 and cand[2] < 0:
            cand[2] = 0.0

        v = (1.0 - blocked) * g + blocked * lim.k_commit * cand
        n = float(np.linalg.norm(v))
        return v / n if n > 1e-6 else g

    def _limit_translation(self, v_des: np.ndarray, dt: float) -> np.ndarray:
        lim = self.lim
        snrm = float(np.linalg.norm(v_des))
        if snrm > lim.max_speed and snrm > 0:
            v_des = v_des * (lim.max_speed / snrm)
        dv_des = v_des - self._prev_vel
        d_dv = dv_des - self._prev_dv
        max_d_dv = lim.max_jerk * dt * dt
        nrm = float(np.linalg.norm(d_dv))
        if nrm > max_d_dv and nrm > 0:
            d_dv *= max_d_dv / nrm
        dv = self._prev_dv + d_dv
        max_dv = lim.max_accel * dt
        nrm = float(np.linalg.norm(dv))
        if nrm > max_dv and nrm > 0:
            dv *= max_dv / nrm
        vel = self._prev_vel + dv
        self._prev_dv = vel - self._prev_vel
        self._prev_vel = vel
        return vel

    def step_follow(self, pos, yaw, goal_xyz, follower, dt=0.1, planar=False, boxes=None):
        """Oracle action: follow an A* path (privileged) with jerk/accel/yaw limits.

        Returns [vx, vy, vz, yaw_rate] in BODY frame; faces the direction of travel so the
        forward depth grid the policy clones looks down the path. Slows near obstacles so the
        jerk-limited tracker hugs the path through tight turns instead of cutting the corner.
        """
        lim = self.lim
        pos = np.asarray(pos, dtype=np.float64)
        goal = np.asarray(goal_xyz, dtype=np.float64)
        action = np.zeros(ACTION_DIM, dtype=np.float64)
        dist_goal = float(np.linalg.norm(goal - pos))
        if dist_goal <= lim.reach_threshold:
            vel = self._limit_translation(np.zeros(3), dt)
            action[:3] = vel
            action[3] = self._limit_yaw(0.0, dt)
            return action.astype(np.float32)

        look = np.asarray(follower.target(pos), dtype=np.float64)
        dir_w = look - pos
        if planar:
            dir_w[2] = 0.0
        n = float(np.linalg.norm(dir_w))
        dir_w = dir_w / n if n > 1e-6 else (goal - pos) / max(dist_goal, 1e-6)

        speed = self._decel_limited_speed(dist_goal, lim.max_speed)
        if dist_goal > lim.reach_threshold * 3:
            speed = max(speed, lim.min_speed)
        if boxes:                                   # slow down near obstacles for tight tracking
            from .world3d import min_dist_to_boxes
            clr = min_dist_to_boxes(pos[0], pos[1], pos[2], boxes)
            speed *= float(np.clip((clr - 0.3) / 1.8, 0.25, 1.0))
        v_w = dir_w * speed

        c, s = math.cos(-yaw), math.sin(-yaw)
        v_body = np.array([c * v_w[0] - s * v_w[1], s * v_w[0] + c * v_w[1], v_w[2]])
        if pos[2] < lim.altitude_floor and v_body[2] < 0:
            v_body[2] = 0.0
        vel = self._limit_translation(v_body, dt)

        # Quad (holonomic): face the GOAL — a slowly-changing heading so body-frame velocity
        # doesn't spin (which would lag world-frame tracking and clip corners). Rover faces
        # its travel direction (it can only drive forward).
        if planar:
            face_yaw = math.atan2(dir_w[1], dir_w[0])
        else:
            face_yaw = math.atan2(goal[1] - pos[1], goal[0] - pos[0])
        yr = self._limit_yaw(wrap_pi(face_yaw - yaw) / max(dt, 1e-3), dt)
        action[:3] = vel
        if planar:
            action[1] = 0.0
            action[2] = 0.0
        action[3] = yr
        return action.astype(np.float32)

    def _limit_yaw(self, yaw_rate_des: float, dt: float) -> float:
        lim = self.lim
        dr = yaw_rate_des - self._prev_yaw_rate
        max_dr = lim.max_yaw_accel * dt
        dr = max(-max_dr, min(max_dr, dr))
        yr = self._prev_yaw_rate + dr
        yr = max(-lim.max_yaw_rate, min(lim.max_yaw_rate, yr))
        self._prev_yaw_rate = yr
        return yr


class QuadExpert(_SmoothExpertBase):
    """Holonomic 3D obstacle-avoiding velocity controller for a quadcopter."""

    def step(self, state: State, dt: float = 0.1) -> np.ndarray:
        lim = self.lim
        action = np.zeros(ACTION_DIM, dtype=np.float64)
        if self._is_reached(state.dist):
            vel = self._limit_translation(np.zeros(3), dt)
            action[:3] = vel
            action[3] = self._limit_yaw(0.0, dt)
            return action.astype(np.float32)

        speed = self._decel_limited_speed(state.dist, self._cruise_speed(state.min_clearance))
        if state.dist > lim.reach_threshold * 3:
            speed = max(speed, lim.min_speed)

        target = (state.target_fwd, state.target_left, state.target_up)
        dir3 = self._steer_dir(target, state.depth, altitude=state.altitude)
        v_des = dir3 * speed

        if state.altitude < lim.altitude_floor and v_des[2] < 0:
            v_des[2] = 0.0

        vel = self._limit_translation(v_des, dt)
        # face the goal (holonomic): keeps the forward grid on the path
        yr = self._limit_yaw(state.yaw_err / max(dt, 1e-3), dt)
        action[:3] = vel
        action[3] = yr
        return action.astype(np.float32)


class RoverExpert(_SmoothExpertBase):
    """Nonholonomic pure-pursuit controller with planar gap steering for a ground rover."""

    def __init__(self, limits: ExpertLimits | None = None):
        lim = limits or ExpertLimits(max_speed=1.5, min_speed=0.2, max_accel=1.0)
        super().__init__(lim)

    def step(self, state: State, dt: float = 0.1) -> np.ndarray:
        lim = self.lim
        action = np.zeros(ACTION_DIM, dtype=np.float64)
        if self._is_reached(state.dist):
            vel = self._limit_translation(np.zeros(3), dt)
            action[0] = vel[0]
            action[3] = self._limit_yaw(0.0, dt)
            return action.astype(np.float32)

        speed = self._decel_limited_speed(state.dist, self._cruise_speed(state.min_clearance))
        target = (state.target_fwd, state.target_left, 0.0)
        dir3 = self._steer_dir(target, state.depth, planar=True)
        travel_bearing = math.atan2(dir3[1], dir3[0])
        align = max(0.0, math.cos(travel_bearing))
        speed *= align ** 2
        vel = self._limit_translation(np.array([speed, 0.0, 0.0]), dt)
        yr = self._limit_yaw(travel_bearing / max(dt, 1e-3), dt)
        action[0] = vel[0]
        action[3] = yr
        return action.astype(np.float32)


def make_expert(vehicle: float, limits: ExpertLimits | None = None) -> _SmoothExpertBase:
    if vehicle == VEHICLE_ROVER:
        return RoverExpert(limits)
    return QuadExpert(limits)

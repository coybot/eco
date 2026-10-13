"""One follow controller with the five ideas as switches. All flags off = a model of
astral-sdk feat/follow-me @ 477a4b3 (constants cited inline from FollowMeConfiguration,
FollowTargetTracker, PersonBodyVerifier, FollowPersonProjection, PursuitController).

  f1  continuous velocity pursuit (20 Hz, rate-limited) instead of stop -> goal -> stop
  f2  constant-velocity Kalman track with Mahalanobis gating; coast through dropouts
  f3  body check only to LOCK; afterwards tolerate clipped / low-confidence / partial boxes,
      use person-pixel depth, and fall back to bearing-only updates
  f4  appearance gallery: veto swaps, break ambiguity, re-find by look
  f5  stand-off 1.8 m + 20 deg camera tilt + never treat "too close" as lost

Modelled start-up simplification: the real coordinator waits for the person to walk 0.30 m
away before the first move (departureRangeIncrease); that gate is skipped here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .sensor import Det, Frame

VMAX = 0.40          # m/s, both variants (sim MAX_V is 0.5; PursuitController default is 0.35)
WMAX = 1.5           # rad/s (sim MAX_W)
CONFIRM_S = 0.15     # assumed stop-confirmation latency (not measured); sweepable
LOOKAHEAD = 0.5
GOAL_TOL = 0.2
RECOVERY_S = 10.0    # FollowMeConfiguration.reacquisitionSeconds
REACQ_DIST = 1.5     # FollowMeConfiguration.reacquisitionDistance
SCAN_STEP = math.pi / 18
PULSE_S, SETTLE_S, OBSERVE_S = SCAN_STEP / WMAX, 0.30, 0.10


@dataclass
class Variant:
    name: str
    f1: bool = False
    f2: bool = False
    f3: bool = False
    f4: bool = False
    f5: bool = False
    confirm_s: float = CONFIRM_S

    @property
    def standoff(self) -> float:
        return 1.8 if self.f5 else 1.5

    @property
    def pitch_deg(self) -> float:
        return 20.0 if self.f5 else 15.0


@dataclass
class Obs:
    xy: tuple[float, float] | None
    bearing: float | None        # rover-frame bearing at capture (bearing-only updates)
    box: tuple[float, float, float, float]
    conf: float
    wfrac: float
    emb: np.ndarray
    t: float
    pose: tuple[float, float, float]
    cam_depth: float | None = None
    gt: str = ""


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def iou(a, b) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


# ---------------------------------------------------------------------------------------
# Perception: strict (today's) and tolerant (f3) candidate extraction
# ---------------------------------------------------------------------------------------
def body_verified(d: Det) -> bool:
    """PersonBodyVerifier.verify: four joints at >= 0.30, inside the box, minimum spreads."""
    j = d.joints
    need = ("leftShoulder", "rightShoulder", "leftHip", "rightHip")
    if not all(n in j and j[n][2] >= 0.30 for n in need):
        return False
    u0, v0, u1, v1 = d.box
    if not all(u0 - 0.02 <= j[n][0] <= u1 + 0.02 and v0 - 0.02 <= j[n][1] <= v1 + 0.02 for n in need):
        return False
    return (abs(j["leftShoulder"][0] - j["rightShoulder"][0]) >= 0.02
            and abs(j["leftHip"][0] - j["rightHip"][0]) >= 0.02
            and (j["leftShoulder"][1] + j["rightShoulder"][1]) / 2
            - (j["leftHip"][1] + j["rightHip"][1]) / 2 >= 0.05)


def strict_candidates(fr: Frame) -> list[Obs]:
    out = []
    for d in fr.dets:
        if d.conf < 0.50 or not body_verified(d):
            continue
        b = d.box
        if not (b[0] >= 0 and b[1] > 0 and b[2] <= 1 and b[3] <= 1):   # feet must be in frame
            continue
        if d.torso is None:
            continue
        xy = fr.cam.to_world(fr.pose, *d.torso)
        out.append(Obs(xy, None, b, d.conf, d.wfrac, d.emb, fr.t, fr.pose, d.torso[2], d.gt))
    return out


def tolerant_candidates(fr: Frame) -> list[Obs]:
    out = []
    for d in fr.dets:
        if d.conf < 0.25 or d.n_visible < 3:
            continue
        cu, cv = (d.box[0] + d.box[2]) / 2, (d.box[1] + d.box[3]) / 2
        bearing = fr.cam.bearing(cu, cv)
        xy = d.masked_xy if (d.masked_xy is not None and d.wfrac >= 0.5) else None
        out.append(Obs(xy, bearing, d.box, d.conf, d.wfrac, d.emb, fr.t, fr.pose, None, d.gt))
    return out


# ---------------------------------------------------------------------------------------
# Kalman track (f2)
# ---------------------------------------------------------------------------------------
class KF:
    SIGMA_A = 0.5   # m/s^2 process noise

    def __init__(self, xy, t):
        self.x = np.array([xy[0], xy[1], 0.0, 0.0])
        self.P = np.diag([0.10 ** 2, 0.10 ** 2, 0.5 ** 2, 0.5 ** 2])
        self.t = t

    @staticmethod
    def _FQ(dt):
        F = np.eye(4)
        F[0, 2] = F[1, 3] = dt
        q = KF.SIGMA_A ** 2
        Q = np.zeros((4, 4))
        for i in (0, 1):
            Q[i, i] = q * dt ** 4 / 4
            Q[i, i + 2] = Q[i + 2, i] = q * dt ** 3 / 2
            Q[i + 2, i + 2] = q * dt ** 2
        return F, Q

    def predict_to(self, t):
        dt = t - self.t
        if dt <= 0:
            return
        F, Q = self._FQ(dt)
        self.x, self.P, self.t = F @ self.x, F @ self.P @ F.T + Q, t

    def peek(self, t):
        dt = max(0.0, t - self.t)
        F, Q = self._FQ(dt)
        return F @ self.x, F @ self.P @ F.T + Q

    def gate_xy(self, z, sigma):
        H = np.zeros((2, 4)); H[0, 0] = H[1, 1] = 1
        S = H @ self.P @ H.T + np.eye(2) * sigma ** 2
        y = np.array(z) - H @ self.x
        return float(y @ np.linalg.solve(S, y))

    def update_xy(self, z, sigma):
        H = np.zeros((2, 4)); H[0, 0] = H[1, 1] = 1
        S = H @ self.P @ H.T + np.eye(2) * sigma ** 2
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (np.array(z) - H @ self.x)
        self.P = (np.eye(4) - K @ H) @ self.P

    def _bearing_h(self, pose):
        px, py, yaw = pose
        rx, ry = self.x[0] - px, self.x[1] - py
        f, l = rx * math.cos(yaw) + ry * math.sin(yaw), -rx * math.sin(yaw) + ry * math.cos(yaw)
        r2 = max(1e-6, f * f + l * l)
        H = np.zeros((1, 4))
        H[0, 0] = (-f * math.sin(yaw) - l * math.cos(yaw)) / r2
        H[0, 1] = (f * math.cos(yaw) - l * math.sin(yaw)) / r2
        return math.atan2(l, f), H

    def gate_bearing(self, b, pose, sigma):
        h, H = self._bearing_h(pose)
        S = float(H @ self.P @ H.T) + sigma ** 2
        return wrap(b - h) ** 2 / S

    def update_bearing(self, b, pose, sigma):
        h, H = self._bearing_h(pose)
        S = float(H @ self.P @ H.T) + sigma ** 2
        K = (self.P @ H.T) / S
        self.x = self.x + (K * wrap(b - h)).ravel()
        self.P = (np.eye(4) - K @ H) @ self.P


# ---------------------------------------------------------------------------------------
class Follower:
    def __init__(self, v: Variant):
        if (v.f2 or v.f3 or v.f4) and not v.f1:
            raise ValueError("f2/f3/f4 are defined on top of f1 (continuous motion)")
        if v.f4 and not v.f2:
            raise ValueError("f4 needs the f2 tracker")
        self.v = v
        self.state = "init"
        self.failed = False
        # baseline tracker
        self.locked: Obs | None = None
        self.anchor: Obs | None = None
        # kalman tracker
        self.kf: KF | None = None
        self.gallery: np.ndarray | None = None
        self.last_obs_now = -1e9
        self.first_loss: float | None = None
        # baseline motion
        self.mode = "idle"
        self.mode_until = 0.0
        self.goal: tuple[float, float] | None = None
        self.last_goal: tuple[float, float] | None = None
        self.last_goal_t = -1e9
        # scan machinery
        self.scan_phase = "to_target"
        self.scan_until = 0.0
        self.observe_from = 1e9
        self.scan_dir = 1.0
        self.scan_steps = 0
        self.scan_target: tuple[float, float] | None = None
        # velocity law
        self.cmd_v = 0.0
        self.cmd_w = 0.0
        self.last_t = 0.0
        self.log_state_changes: list[tuple[float, str]] = []

    # ---- bookkeeping -------------------------------------------------------------------
    def _set_state(self, now: float, s: str):
        if s != self.state:
            self.state = s
            self.log_state_changes.append((now, s))

    def est_xy(self, now: float):
        if self.v.f2 and self.kf is not None and self.state in ("tracking", "coasting", "search"):
            x, _ = self.kf.peek(now)
            return (float(x[0]), float(x[1]))
        if self.state == "follow" and self.locked is not None:
            return self.locked.xy
        return None

    @property
    def tracking_valid(self) -> bool:
        return self.state in ("follow", "tracking")

    # ---- observation -------------------------------------------------------------------
    def observe(self, fr: Frame, now: float):
        if self.failed:
            return
        if self.state == "init":
            self._try_lock(fr, now)
        elif self.v.f2:
            self._observe_kf(fr, now)
        else:
            self._observe_base(fr, now)

    def _try_lock(self, fr: Frame, now: float):
        strict = strict_candidates(fr)
        if not strict:
            return
        sel = min(strict, key=lambda o: ((o.box[0] + o.box[2]) / 2 - 0.5) ** 2 + ((o.box[1] + o.box[3]) / 2 - 0.5) ** 2)
        if self.v.f2:
            self.kf = KF(sel.xy, sel.t)
            self.gallery = sel.emb.copy()
            self.last_obs_now = now
            self._set_state(now, "tracking")
        else:
            self.locked = sel
            self.anchor = sel
            self._set_state(now, "follow")
            self._base_goal(sel, fr, now)

    # ---- baseline tracker (one-frame loss) ---------------------------------------------
    def _match_base(self, cands: list[Obs]) -> list[Obs]:
        prev = self.locked
        out = []
        for o in cands:
            if o.xy is None:
                continue
            if math.dist(o.xy, prev.xy) > 0.75:      # maximumWorldDistance
                continue
            if iou(o.box, prev.box) >= 0.10:          # minimumBoxIoU
                out.append(o); continue
            dx = (o.box[0] + o.box[2]) / 2 - (prev.box[0] + prev.box[2]) / 2
            dy = (o.box[1] + o.box[3]) / 2 - (prev.box[1] + prev.box[3]) / 2
            if math.hypot(dx, dy) <= 0.25:            # maximumScreenCenterDistance
                out.append(o)
        return out

    def _observe_base(self, fr: Frame, now: float):
        if self.state == "follow":
            cands = tolerant_candidates(fr) if self.v.f3 else strict_candidates(fr)
            m = self._match_base(cands)
            if len(m) == 1:
                self.locked = m[0]
                if m[0].wfrac > 0.5:
                    self.anchor = m[0]
                self._base_goal(m[0], fr, now)
            else:
                self._lose(now)
        elif self.state == "reacq":
            if self.scan_phase == "observe" and fr.t >= self.observe_from:
                near = [o for o in strict_candidates(fr) if math.dist(o.xy, self.anchor.xy) <= REACQ_DIST]
                if len(near) == 1:
                    self.locked = near[0]
                    self.first_loss = None
                    self._set_state(now, "follow")
                    self.last_goal = None
                    self._base_goal(near[0], fr, now)

    def _lose(self, now: float):
        if self.first_loss is None:
            self.first_loss = now
        self.mode, self.mode_until = "stopping", now + self.v.confirm_s
        self.scan_phase, self.scan_steps = "to_target", 0
        self.scan_target = self.anchor.xy if self.anchor else None
        self._set_state(now, "reacq")

    # ---- baseline goal logic (coordinator.follow) --------------------------------------
    def _base_goal(self, o: Obs, fr: Frame, now: float):
        if self.v.f1:
            return                                     # velocity law drives from the estimate
        rover = fr.pose[:2]
        dist = math.dist(rover, o.xy)
        if dist <= 1.75:                               # maximumHoldDistance
            if self.mode in ("drive", "confirm"):
                self.mode, self.mode_until = "stopping", now + self.v.confirm_s
            self.last_goal = None
            return
        travel = dist - self.v.standoff                # standOffDistance
        goal = (rover[0] + (o.xy[0] - rover[0]) / dist * travel, rover[1] + (o.xy[1] - rover[1]) / dist * travel)
        if self.last_goal is not None and (math.dist(goal, self.last_goal) < 0.30 or now - self.last_goal_t < 1 / 3):
            return
        # confirmStop in the coordinator, then again inside navigateForFollow.
        self.goal, self.last_goal, self.last_goal_t = goal, goal, now
        self.mode, self.mode_until = "confirm", now + 2 * self.v.confirm_s

    # ---- Kalman tracker (f2) -----------------------------------------------------------
    def _observe_kf(self, fr: Frame, now: float):
        kf = self.kf
        cands = tolerant_candidates(fr) if self.v.f3 else strict_candidates(fr)
        if fr.t > kf.t:
            kf.predict_to(fr.t)
        scored = []
        for o in cands:
            sig = 0.06 + 0.03 * (o.cam_depth or 2.0)
            if o.xy is not None:
                d2, kind = kf.gate_xy(o.xy, sig), "xy"
                ok = d2 <= 9.21
            elif o.bearing is not None:
                d2, kind = kf.gate_bearing(o.bearing, o.pose, 0.04), "bearing"
                ok = d2 <= 6.63
            else:
                continue
            if ok:
                scored.append([d2, kind, o, sig])
        if self.v.f4 and self.gallery is not None:
            kept = []
            for item in scored:
                o = item[2]
                sim = float(o.emb @ self.gallery)
                if o.wfrac >= 0.4 and sim < 0.45:
                    continue                                   # veto: confidently someone else
                item[0] += 6.0 * (1.0 - sim) if o.wfrac >= 0.4 else 0.0
                kept.append(item)
            scored = kept
        if not scored:
            return
        scored.sort(key=lambda s: s[0])
        if len(scored) > 1 and scored[1][0] < 2.5 * max(scored[0][0], 0.2):
            return                                             # ambiguous: coast, don't guess
        d2, kind, o, sig = scored[0]
        if kind == "xy":
            kf.update_xy(o.xy, sig)
        else:
            kf.update_bearing(o.bearing, o.pose, 0.04)
        self.last_obs_now = now
        if self.v.f4 and o.wfrac >= 0.6 and len(scored) == 1:
            self.gallery = 0.9 * self.gallery + 0.1 * o.emb
            self.gallery /= np.linalg.norm(self.gallery)
        if self.state in ("coasting", "search"):
            self.first_loss = None
            self.scan_phase = "to_target"
            self._set_state(now, "tracking")

    # ---- per-tick command --------------------------------------------------------------
    def command(self, now: float, pose, clearance: float):
        v, w = self._command(now, pose, clearance)
        # Forward-clearance slowdown (LiDAR / depth stand-in), same for every variant:
        # full speed at >= 1.0 m, zero at <= 0.6 m.
        if v > 0:
            v *= clip((clearance - 0.6) / 0.4, 0.0, 1.0)
        return v, w

    def _command(self, now: float, pose, clearance: float):
        dt = max(1e-3, now - self.last_t)
        self.last_t = now
        if self.failed or self.state == "init":
            return self._rate(0.0, 0.0, dt, hard=True)
        if self.v.f2:
            return self._cmd_kf(now, pose, dt)
        if self.state == "follow":
            if self.v.f1:
                return self._velocity(now, pose, self.locked.xy, (0.0, 0.0), 1.0, dt)
            return self._cmd_base_motion(now, pose)
        if self.state == "reacq":
            return self._cmd_scan(now, pose, dt)
        return 0.0, 0.0

    def _cmd_kf(self, now: float, pose, dt: float):
        age = now - self.last_obs_now
        if self.state == "tracking" and age > 0.35:
            self.first_loss = now - age
            self._set_state(now, "coasting")
        if self.state == "coasting" and age > 3.0:
            self.scan_phase, self.scan_steps = "to_target", 0
            self._set_state(now, "search")
        if self.state in ("coasting", "search") and self.first_loss is not None and now - self.first_loss > RECOVERY_S:
            self.failed = True
            self._set_state(now, "failed")
            return 0.0, 0.0
        x, _ = self.kf.peek(now)
        xy, vel = (float(x[0]), float(x[1])), (float(x[2]), float(x[3]))
        if self.state == "tracking":
            return self._velocity(now, pose, xy, vel, 1.0, dt)
        if self.state == "coasting":
            return self._velocity(now, pose, xy, vel, 0.6, dt)
        self.scan_target = xy
        return self._cmd_scan(now, pose, dt)

    # ---- baseline motion: stop -> confirm -> PursuitController -> stop -------------------
    def _cmd_base_motion(self, now: float, pose):
        if self.mode in ("confirm", "stopping") and now >= self.mode_until:
            self.mode = "drive" if self.mode == "confirm" else "idle"
        if self.mode != "drive":
            return 0.0, 0.0
        rx, ry, yaw = pose
        gx, gy = self.goal
        d = math.hypot(gx - rx, gy - ry)
        if d <= GOAL_TOL:
            self.mode, self.mode_until = "stopping", now + self.v.confirm_s
            return 0.0, 0.0
        tx, ty = (gx, gy) if d <= LOOKAHEAD else (rx + (gx - rx) / d * LOOKAHEAD, ry + (gy - ry) / d * LOOKAHEAD)
        ex, ey = tx - rx, ty - ry
        xr, yr = math.cos(yaw) * ex + math.sin(yaw) * ey, -math.sin(yaw) * ex + math.cos(yaw) * ey
        err = math.atan2(yr, xr)
        if abs(err) > 0.7:                              # rotateInPlaceAngle
            w = clip(err * 2.0, -WMAX, WMAX)
            return 0.0, math.copysign(max(1.0, abs(w)), w)
        ld = max(1e-3, math.hypot(xr, yr))
        v = VMAX * min(1.0, d / 0.6)                    # slowdownRadius
        return v, clip(v * 2.0 * yr / (ld * ld), -WMAX, WMAX)

    # ---- scan (shared): to last bearing, then 10-degree pulse/settle/observe steps -------
    def _cmd_scan(self, now: float, pose, dt: float):
        self._expire_check(now)
        if self.failed:
            return 0.0, 0.0
        if self.mode == "stopping" and now < self.mode_until:
            return self._rate(0.0, 0.0, dt, hard=True)
        rx, ry, yaw = pose
        if self.scan_phase == "to_target":
            if self.scan_target is None:
                self.scan_phase = "pulse"; self.scan_until = now
            else:
                err = wrap(math.atan2(self.scan_target[1] - ry, self.scan_target[0] - rx) - yaw)
                if abs(err) < 0.08:
                    self.scan_phase, self.scan_until = "settle", now + SETTLE_S
                    self.scan_dir = 1.0 if err >= 0 else -1.0
                    return self._rate(0.0, 0.0, dt, hard=True)
                w = clip(err * 2.0, -WMAX, WMAX)
                return self._rate(0.0, math.copysign(max(0.8, abs(w)), w), dt, hard=True)
        if self.scan_phase == "settle":
            if now >= self.scan_until:
                self.scan_phase, self.observe_from = "observe", now
                self.scan_until = now + OBSERVE_S
            return self._rate(0.0, 0.0, dt, hard=True)
        if self.scan_phase == "observe":
            if now >= self.scan_until:
                self.scan_phase, self.scan_until = "pulse", now + PULSE_S
                self.scan_steps += 1
                # expanding sweep: 5 steps one way, then 10 back, then 10 forward, ...
                if self.scan_steps in (5, 15, 25, 35, 45, 55):
                    self.scan_dir = -self.scan_dir
            else:
                return self._rate(0.0, 0.0, dt, hard=True)
        if self.scan_phase == "pulse":
            if now >= self.scan_until:
                self.scan_phase, self.scan_until = "settle", now + SETTLE_S
                self.observe_from = 1e9
                return self._rate(0.0, 0.0, dt, hard=True)
            return self._rate(0.0, self.scan_dir * WMAX, dt, hard=True)
        return 0.0, 0.0

    def _expire_check(self, now: float):
        if self.state == "reacq" and self.first_loss is not None and now - self.first_loss > RECOVERY_S:
            self.failed = True
            self._set_state(now, "failed")

    # ---- continuous velocity law (f1) --------------------------------------------------
    def _velocity(self, now: float, pose, xy, vel, scale: float, dt: float):
        rx, ry, yaw = pose
        dx, dy = xy[0] - rx, xy[1] - ry
        r = math.hypot(dx, dy)
        f, l = dx * math.cos(yaw) + dy * math.sin(yaw), -dx * math.sin(yaw) + dy * math.cos(yaw)
        th = math.atan2(l, f)
        w_des = clip(2.2 * th, -WMAX, WMAX)
        v_los = (vel[0] * dx + vel[1] * dy) / max(r, 1e-3)
        v_des = 0.8 * (r - self.v.standoff) + (max(0.0, v_los) if self.v.f2 else 0.0)
        if self.v.f5 and r < self.v.standoff - 0.3:
            v_des = 0.0                                  # too close: hold, never reverse
        v_des = clip(v_des, 0.0, VMAX * scale)
        v_des *= max(0.0, math.cos(th)) ** 2 if abs(th) < 1.0 else 0.0
        return self._rate(v_des, w_des, dt)

    def _rate(self, v: float, w: float, dt: float, hard: bool = False):
        if hard:
            self.cmd_v, self.cmd_w = v, w
            return v, w
        self.cmd_v += clip(v - self.cmd_v, -0.8 * dt, 0.4 * dt)
        self.cmd_w += clip(w - self.cmd_w, -8.0 * dt, 8.0 * dt)
        return self.cmd_v, self.cmd_w

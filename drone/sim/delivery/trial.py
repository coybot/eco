"""One delivery trial: release above the roofline, find the balcony, deliver, come back
to the pickup point (addendum section 3), scored per section 4.

    python -m delivery.trial --scenario S4 --seed 7 [--mount wall] [--method line] [-v]

Mission (all decisions from the hexacopter's own estimate, map and sensors):
  INBOUND   A* to the ENTRY point (``entry_standoff_m`` outside the railing, at the
            crossing height = beacon + ``cross_height_above_beacon_m``), replanned at 2 Hz;
            flies only along cells seen empty, facing the direction of travel, speed by
            distance to mapped structure (3 / 1 / 0.5 m/s). Stalls at the edge of what it
            has seen, looks around, marks unresolvable cells blocked; at a dead end goes
            back to the last recorded point with unexplored open space. Records the flown
            path every 0.5 m.
  CROSS     straight in over the railing at crossing height, 0.5 m/s, stops for anything
            the infrared ring sees ahead (a person).
  DELIVER   land: descend to touchdown, motors idle, release, take off. line: hover at
            crossing height, lower the cup on the line, release on slack, reel in.
  OUTBOUND  "go out before going up": reverse the recorded path, correcting drift by
            scan matching against the map; never climb unless the cells above are seen
            empty; replan around anything new. Ends at the pickup point.
  GIVE_UP   on time, energy, repeated contact or dead ends. Fallback order: open sky at the
            pickup point, then the roof area, then the balcony floor.
On any contact: stop, back away along the recorded path, mark the point blocked, replan.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import numpy as np

_SIM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SIM not in sys.path:
    sys.path.insert(0, _SIM)

from flight.frames import GRAVITY_M_S2, euler_from_rot, quat_to_rot, wrap_pi  # noqa: E402

from . import geom as G  # noqa: E402
from . import scenarios as SC  # noqa: E402
from .hex import Cup, HexNav, HexVehicle  # noqa: E402
from .mapping import C_SOLID, C_TREE, C_WIRE, OccupancyGrid  # noqa: E402
from .sensors_hex import (S_SOLID, S_TREE, S_WIRE, BaroDiff, ContactDetector, IRArray,  # noqa: E402
                          LongRangeRadio, Radar, RangingRadio)
from .urban_wind import UrbanWind  # noqa: E402

_CFG = None


def load_cfg():
    global _CFG
    if _CFG is None:
        import yaml
        _CFG = yaml.safe_load(open(os.path.join(_SIM, "docking", "vehicles.yaml")))
    return _CFG


CLS_MAP = {S_SOLID: C_SOLID, S_TREE: C_TREE, S_WIRE: C_WIRE}
SOFT_KINDS = {G.K_WIRE_METAL, G.K_WIRE_NONMETAL, G.K_NETTING, G.K_TREE, G.K_PERSON}   # any touch is a collision

LIMIT_TEXT = {
    G.K_WIRE_NONMETAL: "non-metal line: radar and infrared cannot see it",
    G.K_NETTING: "netting: radar and infrared cannot see it",
    G.K_WIRE_METAL: "metal wire: radar range at shallow angles / angular resolution",
    G.K_TREE: "foliage: weak radar return (35 %/scan), twigs below infrared size",
    G.K_GLASS: "glass: no infrared reading, radar only to 8 m",
    G.K_PERSON: "moving person",
}


def accel_limited_vel(err, vmax, amax, kp):
    d = float(np.linalg.norm(err))
    if d < 1e-6:
        return np.zeros(3)
    return err / d * min(vmax, math.sqrt(2 * amax * d), kp * d)


class Mission:
    def __init__(self, sc, cfg, opts, grid, nav, beacon_m, rng, prior_path=None):
        self.sc, self.c, self.o = sc, cfg["delivery"], opts
        h = cfg["hexacopter"]
        self.h = h
        self.grid, self.nav = grid, nav
        self.rng = rng
        self.beacon = beacon_m
        c = self.c
        # what the plan and the beacon's install data say (with survey errors)
        err = lambda s: rng.normal(0, s, 3)
        self.cross_z = sc.beacon_railing_m[2] + c["cross_height_above_beacon_m"] + rng.normal(0, 0.03)
        rail_y = sc.railing_y_m + rng.normal(0, 0.1)
        self.entry = np.array([sc.pad_m[0] + rng.normal(0, 0.1), rail_y - c["entry_standoff_m"], self.cross_z])
        self.rail_y_est = rail_y
        self.pad = sc.pad_m + np.array([*rng.normal(0, 0.1, 2), 0.0])
        self.over_pad = np.array([self.pad[0], self.pad[1], self.cross_z])
        self.entry0 = self.entry.copy()
        self.pickup = sc.release_m + err(0.0)        # the plan's pickup point (beacon frame, from the mothership)
        self.roof = sc.roof_fallback_m.copy()
        self.state = "INBOUND"
        self.t_state = 0.0
        self.path = None
        self.path_i = 0
        self.prior = prior_path
        if prior_path is not None:
            self.path = [np.asarray(p, float) for p in prior_path]
        self.goal = self.entry
        self.rec = [nav.pos.copy()]
        self.yaw_sp = None
        self.v_sp = np.zeros(3)
        self.t_last_plan = -1e9
        self.dead_ends = 0
        self.n_staging = 0
        self.t_dyn = -1e9
        self.t_waited = 0.0
        self.n_climb_guard = 0
        self.wait_t1 = None
        self.n_dyn_waits = 0
        self.stall_t0 = None
        self.best_goal_d = math.inf
        self.t_progress = 0.0
        self.sweep_until = None
        self.look_yaw0 = 0.0
        self.contacts_inbound = 0
        self.backoff_until = -1.0
        self.backoff_target = None
        self.gave_up = False
        self.give_up_reason = ""
        self.fallback = "none"
        self.delivered = False
        self.t_delivered = None
        self.energy_at_delivery = None
        self.done = False
        self.result = ""
        self.speed_cap = c["speed_open_m_s"]
        self.d_occ = math.inf
        self.t_dist = -1e9
        self.n_replans = 0
        self.n_plan_fail = 0
        self.max_expansions = 0
        self.line_L = 0.0
        self.cup_released_pos = None
        self.landed_t = None
        self.fallback_stage = 0
        self.wait_t0 = None
        self.notes = []

    # ------------------------------------------------------------- helpers
    def set_state(self, s, t, note=None):
        self.state = s
        self.t_state = t
        self.path_i = 0
        self.stall_t0 = None
        self.best_goal_d = math.inf
        self.t_progress = t
        if note:
            self.notes.append(f"{t:.1f}s {s}: {note}")

    def record(self):
        p = self.nav.pos
        if np.linalg.norm(p - self.rec[-1]) >= self.c["path_record_spacing_m"]:
            self.rec.append(p.copy())

    def speed_limit(self, t, p):
        c = self.c
        if t - self.t_dist > 0.5:
            self.d_occ = self.grid.nearest_occupied_m(p, c["near_m"] + 0.5)
            self.t_dist = t
        ijk = self.grid.idx(p)
        if self.d_occ < c["close_m"] or self.grid.in_abox(ijk):
            return c["speed_close_m_s"]
        if self.d_occ < c["near_m"]:
            return c["speed_near_m_s"]
        return c["speed_open_m_s"]

    def path_ok(self, q):
        if not self.grid.inside(q):
            return True                       # outside the map window: open sky above the street
        ijk = self.grid.idx(q)
        return self.grid.state[tuple(ijk)] == 1 and not self.grid.blocked(ijk)

    def seen_free(self, q):
        if not self.grid.inside(q):
            return True
        return self.grid.state[tuple(self.grid.idx(q))] == 1

    def path_passable(self, q):
        if not self.grid.inside(q):
            return True
        ijk = self.grid.idx(q)
        return not self.grid.blocked(ijk)

    def replan(self, t, goal):
        p = self.nav.pos
        path = self.grid.plan(p, goal, self.c["unseen_cost_nd"], w_h=1.25 * (1.0 + self.c["unseen_cost_nd"]))
        self.n_replans += 1
        self.max_expansions = max(self.max_expansions, self.grid.last_expansions)
        self.t_last_plan = t
        if path is None:
            self.n_plan_fail += 1
            return False
        path[-1] = goal
        self.path = list(path)
        self.path_i = 0
        return True

    def carrot(self, p, vmax):
        """Farthest point along the path within the lookahead whose cells are all seen empty."""
        if not self.path:
            return None, True
        # advance index to the closest point
        best_i, best_d = self.path_i, math.inf
        for i in range(self.path_i, min(len(self.path), self.path_i + 40)):
            d = np.linalg.norm(self.path[i] - p)
            if d < best_d:
                best_i, best_d = i, d
        self.path_i = best_i
        look = max(1.0, 1.5 * vmax)
        last = self.path[best_i]
        escape = self.grid.inside(p) and self.grid.blocked(self.grid.idx(p))   # inside a margin: may leave it
        blocked_ahead = False
        acc = 0.0
        for i in range(best_i, len(self.path)):
            q = self.path[i]
            if i > best_i:
                acc += np.linalg.norm(q - self.path[i - 1])
            if acc > look:
                break
            if not (self.path_ok(q) or (escape and np.linalg.norm(q - p) < 2.5 and self.seen_free(q))):
                blocked_ahead = True
                break
            last = q
        return last, blocked_ahead

    def adjust_entry(self):
        """The entry point is nominal: if a margin covers it, take the nearest free cell
        within 0.6 m (same railing, same side), preferring the nominal height."""
        g = self.grid
        if not g.inside(self.entry0) or not g.blocked(g.idx(self.entry0)):
            self.entry = self.entry0
            return
        best, bd = None, 1e9
        for dx in np.arange(-0.6, 0.61, 0.2):
            for dy in np.arange(-0.6, 0.21, 0.2):
                for dz in np.arange(-0.4, 0.41, 0.2):
                    q = self.entry0 + np.array([dx, dy, dz])
                    if g.inside(q) and not g.blocked(g.idx(q)):
                        d = dx * dx + dy * dy + 4 * dz * dz
                        if d < bd:
                            best, bd = q, d
        if best is not None:
            self.entry = best

    def frontier_point(self):
        """Most recent recorded point with unexplored, unblocked space within 2 m."""
        g = self.grid
        r = int(2.0 / g.cell)
        for q in reversed(self.rec[:-3]):
            i, j, k = g.idx(q)
            sl = (slice(max(0, i - r), i + r + 1), slice(max(0, j - r), j + r + 1), slice(max(0, k - r), k + r + 1))
            if (g.state[sl] == 0).sum() > 50:
                return q
        return None

    # ------------------------------------------------------------- main step
    def step(self, t, hexv, ir_ahead_min_m, contact_events, energy_j, e_usable_j, cup):
        c = self.c
        nav = self.nav
        p = nav.pos
        self.record()
        out = dict(v_sp=np.zeros(3), yaw=self.yaw_sp, motors_off=False)
        # contact handling (any state that is flying)
        for ev in contact_events:
            if self.state in ("DESCEND", "LANDED", "TAKEOFF") and ev["dir_world"][2] > 0.6:
                continue
            q = p - ev["dir_world"] * (self.h["cage_radius_m"] + 0.05)
            self.grid.mark_occupied(q, C_WIRE)
            if self.state in ("INBOUND", "CROSS"):
                self.contacts_inbound += 1
            self.backoff_until = t + 2.0
            if self.state in ("INBOUND", "OUTBOUND", "FALLBACK_SKY") and len(self.rec) >= 3:
                self.backoff_target = self.rec[-3]          # back along the recorded path
            else:                                           # on the balcony: straight away from the touch
                self.backoff_target = p + ev["dir_world"] * 0.4
            self.path = None
            self.notes.append(f"{t:.1f}s contact in {self.state}")
        if t < self.backoff_until and self.state not in ("LANDED",):
            out["v_sp"] = accel_limited_vel(self.backoff_target - p, 0.5, 1.0, 1.0)
            return out
        # give-up checks
        if not self.gave_up and self.state in ("INBOUND", "CROSS", "DELIVER_WAIT"):
            reason = None
            ret_len = sum(np.linalg.norm(a - b) for a, b in zip(self.rec[1:], self.rec[:-1]))
            t_ret = ret_len / 1.2 + 20.0
            P_h = 300.0
            e_ret = t_ret * P_h
            reserve = c["give_up_reserve_frac"] * e_usable_j + 3 * 30.0 * 180.0
            if self.contacts_inbound >= c["max_contacts_inbound_count"]:
                reason = "repeated contact"
            elif energy_j + e_ret + reserve > e_usable_j:
                reason = "energy"
            elif t + 1.3 * t_ret + 30.0 > c["time_window_s"]:
                reason = "time"
            elif self.dead_ends > 3:
                reason = "dead end"
            if reason:
                self.give_up(t, reason)
        s = self.state
        if s == "INBOUND":
            return self._inbound(t, p, out)
        if s == "CROSS":
            return self._cross(t, p, out, ir_ahead_min_m)
        if s == "DESCEND":
            return self._descend(t, p, out, hexv)
        if s == "LANDED":
            out["motors_off"] = True
            if t - self.landed_t > c["land_settle_s"] and not self.delivered:
                self.delivered = True
                self.t_delivered = t
                self.energy_at_delivery = energy_j
                hexv.set_mass(self.h["mass_empty_kg"])
                self.on_delivered(t)
            if t - self.landed_t > c["land_settle_s"] + 1.0:
                self.set_state("TAKEOFF", t)
            return out
        if s == "TAKEOFF":
            tgt = self.over_pad
            out["v_sp"] = accel_limited_vel(tgt - p, 0.4, 1.0, 1.0)
            if abs(p[2] - self.cross_z) < 0.1:
                self.start_outbound(t)
            return out
        if s == "LOWER":
            return self._lower(t, p, out, hexv, cup)
        if s in ("OUTBOUND", "FALLBACK_SKY"):
            return self._outbound(t, p, out)
        if s == "FALLBACK_ROOF":
            return self._to_point(t, p, out, self.roof + np.array([0, 0, 1.5]), land_after=True)
        if s == "FALLBACK_BALCONY":
            return self._to_point(t, p, out, self.over_pad, land_after=True)
        if s in ("HOLD", "DOWN"):
            out["v_sp"] = accel_limited_vel(self.hold_pt - p, 0.5, 1.0, 1.0) if s == "HOLD" else np.zeros(3)
            out["motors_off"] = s == "DOWN"
            return out
        return out

    def on_delivered(self, t):
        pass

    def give_up(self, t, reason):
        self.gave_up = True
        self.give_up_reason = reason
        self.fallback = "sky"
        self.start_outbound(t, fallback=True)

    def start_outbound(self, t, fallback=False):
        rev = list(reversed(self.rec))
        if self.state in ("TAKEOFF", "LOWER") or self.delivered:
            rev = [self.entry] + rev
        rev.append(self.pickup)
        self.path = rev
        self.goal = self.pickup
        self.set_state("FALLBACK_SKY" if fallback else "OUTBOUND", t)

    # ------------------------------------------------------------- states
    def _follow(self, t, p, out, goal, replan_if_blocked=True, allow_climb_check=False):
        c = self.c
        vmax = self.speed_limit(t, p)
        if (self.path is None or t - self.t_last_plan > 1.0 / c["replan_hz"]) and replan_if_blocked:
            if self.path is None or self.prior is None or not all(self.path_passable(q) for q in self.path[self.path_i:]):
                ok = self.replan(t, goal)
                if not ok:
                    return None
            else:
                self.t_last_plan = t
        cr, blocked = self.carrot(p, vmax)
        if cr is None:
            return None
        err = cr - p
        if allow_climb_check and err[2] > 0.25:
            # never climb unless the space above is seen empty
            above_ok = all(self.seen_free(p + np.array([0, 0, dz])) for dz in (0.3, 0.6, 0.9))
            if not above_ok:
                err[2] = 0.0
        v = accel_limited_vel(err, vmax, self.h["outer"]["a_max_m_s2"], self.h["outer"]["kp_pos_per_s"] * 1.5)
        out["v_sp"] = v
        hv = math.hypot(v[0], v[1])
        if hv > 0.15:
            self.yaw_sp = math.atan2(v[1], v[0])
        elif blocked and self.path_i + 1 < len(self.path):
            d = self.path[min(len(self.path) - 1, self.path_i + 3)] - p
            if math.hypot(d[0], d[1]) > 0.2:
                self.yaw_sp = math.atan2(d[1], d[0])
        out["yaw"] = self.yaw_sp
        # progress / stall
        gd = float(np.linalg.norm(goal - p))
        if gd < self.best_goal_d - 0.5:
            self.best_goal_d = gd
            self.t_progress = t
        return blocked

    def _inbound(self, t, p, out):
        c = self.c
        de = self.entry - p
        if math.hypot(de[0], de[1]) < 0.35 and abs(de[2]) < 0.12 and np.linalg.norm(self.nav.vel) < 0.3:
            self.set_state("CROSS", t)
            return out
        if self.sweep_until is not None:
            if t < self.sweep_until:
                self.yaw_sp = self.look_yaw0 + 2 * math.pi * (1 - (self.sweep_until - t) / 12.0)
                out["yaw"] = wrap_pi(self.yaw_sp)
                return out
            self.sweep_until = None
        if self.wait_t0 is not None:          # going to a frontier point after a dead end
            r = self._follow(t, p, out, self.frontier)
            if r is None or np.linalg.norm(self.frontier - p) < 0.6 or t - self.wait_t0 > 40:
                self.wait_t0 = None
                self.path = None
            return out
        if np.linalg.norm(self.entry - p) < 5.0:
            self.adjust_entry()
        r = self._follow(t, p, out, self.entry)
        if r is None and np.linalg.norm(self.entry - p) > 5.0 and self.n_staging < 3:
            # no plan from far away: often returns seen from far (radar angle error grows with
            # range) sit on the way in. Go to a staging point 3 m out from the entry and look.
            self.n_staging += 1
            st = self.entry + np.array([0.0, -3.0, 0.5])
            self.frontier = st
            self.wait_t0 = t
            self.path = None
            self.notes.append(f"{t:.1f}s staging {self.n_staging}")
            return out
        if r is None:                          # no plan: dead end
            self.dead_ends += 1
            fp = self.frontier_point()
            self.notes.append(f"{t:.1f}s dead end {self.dead_ends}")
            if fp is None:
                self.dead_ends = 99
                return out
            self.frontier = fp
            self.wait_t0 = t
            self.path = None
            return out
        if t - self.t_progress > 15.0:
            # stalled at the edge of what it has seen: look around once, then block that cell
            if self.stall_t0 is None:
                self.stall_t0 = t
                self.sweep_until = t + 12.0
                self.look_yaw0 = self.yaw_sp or 0.0
                self.t_progress = t
            else:
                if self.path and self.path_i + 1 < len(self.path):
                    for q in self.path[self.path_i: self.path_i + 6]:
                        if not self.path_ok(q):
                            self.grid.mark_occupied(q, C_SOLID)
                self.stall_t0 = None
                self.t_progress = t
                self.path = None
                self.dead_ends += 0.5
        return out

    def dyn_blocked(self, t):
        return t - self.t_dyn < 1.5

    def _cross(self, t, p, out, ir_ahead):
        c = self.c
        tgt = self.over_pad.copy()
        tgt[2] = max(tgt[2], min(p[2], self.cross_z + 0.5))      # keep height gained by the railing guard
        err = tgt - p
        if (ir_ahead < self.h["cage_radius_m"] + 0.25 and np.linalg.norm(err[:2]) > 0.3) or self.dyn_blocked(t):
            out["v_sp"] = np.zeros(3)
            if self.wait_t0 is None:
                self.wait_t0 = t
            if t - self.wait_t0 > 25.0:
                self.wait_t0 = None
                self.give_up(t, "blocked at the railing")
            return out
        self.wait_t0 = None
        out["v_sp"] = accel_limited_vel(err, c["speed_close_m_s"], 1.0, 1.0)
        if abs(err[2]) > 0.12:                 # get the crossing height right before moving in
            out["v_sp"][:2] *= 0.0
        # the railing (or anything) ahead is higher than the cage bottom + 0.2 m: stop and climb
        hh = self.h["cage_half_height_m"]
        before_rail = p[1] < self.rail_y_est + 0.3             # still on the street side of the railing
        if before_rail and t - self.t_low < 0.3 and self.low_ahead_dz > -(hh + 0.2) and p[2] < self.cross_z + 0.5:
            out["v_sp"] = np.array([0.0, 0.0, 0.25])
            self.n_climb_guard += 1
        if math.hypot(err[0], err[1]) > 0.2:
            self.yaw_sp = math.atan2(err[1], err[0])
        out["yaw"] = self.yaw_sp
        if np.linalg.norm(err) < 0.15 and np.linalg.norm(self.nav.vel) < 0.2:
            if self.o.get("method", "land") == "line":
                self.set_state("LOWER", t)
            else:
                self.set_state("DESCEND", t)
        return out

    def _hold_for_dynamic(self, t, p, out):
        """Something is moving close by (a person): climb back to crossing height and wait."""
        if self.wait_t0 is None:
            self.wait_t0 = t
            self.n_dyn_waits += 1
        out["v_sp"] = accel_limited_vel(np.array([p[0], p[1], self.cross_z]) - p, 0.4, 1.0, 1.0)
        if t - self.wait_t0 > 40.0:
            self.wait_t0 = None
            self.give_up(t, "person on the balcony")
        return out

    def _descend(self, t, p, out, hexv):
        if self.dyn_blocked(t):
            self.t_waited += 0.01
            return self._hold_for_dynamic(t, p, out)
        self.wait_t0 = None
        tgt_xy = self.pad[:2]
        v = np.zeros(3)
        v[:2] = accel_limited_vel(np.array([*(tgt_xy - p[:2]), 0.0]), 0.15, 0.5, 1.0)[:2]
        hh = self.h["cage_half_height_m"]
        v[2] = -0.3 if (self.down_range is None or self.down_range > hh + 0.4) else -0.12
        out["v_sp"] = v
        if self.down_range is not None and self.down_range < hh + 0.03:
            self.landed_t = t
            self.set_state("LANDED", t)
        elif t - self.t_state - self.t_waited > 25.0:
            self.give_up(t, "could not land")
        return out

    down_range = None
    low_ahead_dz = -9.0
    t_low = -1e9

    def _lower(self, t, p, out, hexv, cup):
        c = self.c
        out["v_sp"] = accel_limited_vel(self.over_pad - p, 0.3, 0.5, 1.0)
        if self.dyn_blocked(t) and not cup.released:
            # pause the winch while something moves close below or beside
            if self.wait_t1 is None:
                self.wait_t1 = t
            if t - self.wait_t1 > 40.0:
                self.give_up(t, "person on the balcony")
            return out
        self.wait_t1 = None
        if cup.released:
            self.line_L = max(0.0, self.line_L - 0.5 * 0.01)
            cup.L = self.line_L
            if self.line_L <= 0.05:
                self.start_outbound(t)
            return out
        self.line_L = min(c["line_length_m"], self.line_L + c["line_speed_m_s"] * 0.01)
        cup.L = self.line_L
        if cup.on_ground:
            if self.wait_t0 is None:
                self.wait_t0 = t
            if t - self.wait_t0 > 0.5:
                cup.released = True
                self.cup_released_pos = cup.pos.copy()
                self.delivered = True
                self.t_delivered = t
                self.wait_t0 = None
                self.on_delivered(t)
        else:
            self.wait_t0 = None
        if self.line_L >= c["line_length_m"] and not cup.on_ground and t - self.t_state > 15.0:
            cup.released = True                # line fully out, cup not down: drop it (counts as a failed placement)
            self.cup_released_pos = None
            self.delivered = False
            self.give_up(t, "line too short")
        return out

    def _outbound(self, t, p, out):
        if np.linalg.norm(self.pickup - p) < 0.6:
            self.hold_pt = self.pickup.copy()
            self.set_state("HOLD", t)
            self.done = True
            self.result = "pickup"
            return out
        r = self._follow(t, p, out, self.pickup, replan_if_blocked=False, allow_climb_check=True)
        # replan only when the reversed path is blocked ahead, or after a long stall
        need = False
        if self.path:
            ahead = self.path[self.path_i: self.path_i + 25]
            if any(not self.path_passable(q) for q in ahead):
                need = True
        if r is None or need or (t - self.t_progress > 20.0):
            if t - self.t_last_plan > 0.5:
                # target: a point further along the reversed path, else the pickup point
                tgt = self.pickup
                if self.path:
                    for q in self.path[min(len(self.path) - 1, self.path_i + 25):]:
                        if self.path_passable(q):
                            tgt = q
                            break
                ok = self.replan(t, tgt)
                if ok:
                    if tgt is not self.pickup:
                        self.path = self.path + [self.pickup]
                    self.t_progress = t
                else:
                    self.t_last_plan = t
                    if t - self.t_progress > 40.0:
                        self.next_fallback(t)
        return out

    def next_fallback(self, t):
        self.fallback_stage += 1
        self.gave_up = True
        if not self.give_up_reason:
            self.give_up_reason = "way out blocked"
        if self.fallback_stage == 1:
            self.fallback = "roof"
            self.path = None
            self.set_state("FALLBACK_ROOF", t)
        elif self.fallback_stage == 2:
            self.fallback = "balcony"
            self.path = None
            self.set_state("FALLBACK_BALCONY", t)
        else:
            self.fallback = "stuck"
            self.set_state("DOWN", t)
            self.done = True
            self.result = "stuck"

    def _to_point(self, t, p, out, tgt, land_after):
        if np.linalg.norm(tgt - p) < 0.5:
            self.set_state("DOWN", t)
            self.done = True
            self.result = self.fallback
            return out
        r = self._follow(t, p, out, tgt)
        if r is None and t - self.t_state > 30.0:
            self.next_fallback(t)
        if t - self.t_state > 120.0:
            self.next_fallback(t)
        return out


def run_trial(sid, seed, opts=None, prior_path=None, verbose=False, record=False, visit=1):
    opts = dict(opts or {})
    cfg = load_cfg()
    h, dcfg, env = cfg["hexacopter"], cfg["delivery"], cfg["environment"]
    sc = SC.make(sid, seed)
    rng = np.random.default_rng([seed, SC.SCENARIOS.index(sid), 77, visit])
    mount = opts.get("mount", dcfg["beacon_mount_label"])
    method = opts.get("method", dcfg["delivery_method_label"])
    opts["method"] = method
    beacon = sc.beacon_m(mount)
    wind = UrbanWind(sc, seed + 1000 * visit)
    scene = sc.scene
    yaw0 = math.atan2(sc.pad_m[1] - sc.release_m[1], sc.pad_m[0] - sc.release_m[0])
    hexv = HexVehicle(h, env, rng, sc.release_m, yaw0, h["mass_loaded_kg"])
    scfg = h["sensors"]
    rad_on = opts.get("radars", [0, 1])
    radars = [Radar(scfg["radar"], scfg["radar"]["mounts_pitch_deg"][i], rng, scene,
                    wire_scale=opts.get("wire_scale", 1.0)) for i in rad_on]
    ir = IRArray(scfg["ir"], rng, sc.sun_bool, sc.sun_dir)
    rr = RangingRadio(scfg["ranging_radio"], rng, beacon)
    lora = LongRangeRadio(scfg["long_range_radio"], rng, beacon)
    baro = BaroDiff(scfg["baro_diff"], rng)
    contact = ContactDetector(scfg["contact"], rng)
    p0 = sc.release_m + rng.normal(0, dcfg["release_pos_err_std_m"], 3) * np.array([1, 1, 0.3])
    nav = HexNav(p0, dcfg["release_pos_err_std_m"], scfg["radar"], scfg["baro_diff"])
    if opts.get("radar_velocity_only"):
        nav.use.update(ranging=False, lora=False, match=False)
    if opts.get("no_scan_match"):
        nav.use["match"] = False
    # approach box (beacon frame): balcony footprint +/-0.5 m, from the entry point to the back wall
    bx = sc.balcony_x_m
    ab = (np.array([bx[0] - 0.5, sc.railing_y_m - dcfg["entry_standoff_m"] - 0.8, sc.floor_z_m - 0.3]),
          np.array([bx[1] + 0.5, sc.balcony_y_m[1] + 0.3, sc.railing_top_z_m + dcfg["cross_height_above_beacon_m"] + 1.0]))
    grid = OccupancyGrid(sc.window_lo_m, sc.window_hi_m, dcfg, approach_box_m=ab)
    mis = Mission(sc, cfg, opts, grid, nav, beacon, rng, prior_path=prior_path)
    E_use = h["battery_wh"] * h["battery_usable_frac"] * 3600.0
    fail = opts.get("rotor_fail")                # (t_s, rotor index)
    dt = env["physics_dt_s"]
    gdt = env["guidance_dt_s"]
    nsub = int(round(gdt / dt))
    t = 0.0
    t_end = dcfg["time_window_s"] + 60.0
    kv, ki = h["outer"]["kv_per_s"], h["outer"]["ki_per_s2"]
    vi = np.zeros(3)
    yaw_cur = yaw0
    w = wind.velocity(hexv.body.pos, 0.0)
    cup = None
    active = {}
    events = []
    near_facade = 99.0
    nav_err_max = 0.0
    nav_err_sum, nav_n = 0.0, 0
    los_n = 0
    floor_box = None
    change_done = False
    person = sc.person
    trace = [] if record else None
    pickup_err = None
    land_pos = None
    baro_acc = []
    t_baro = 0.0
    floor_est = beacon[2] - (sc.beacon_m(mount)[2] - sc.floor_z_m) + rng.normal(0, 0.03)   # install data
    crashed = False
    t_wall = time.time()
    ground_z = sc.info["ground_z_m"]
    skip = np.zeros(G.N_MAT, np.bool_)
    skip[[G.NET, G.WIRE_METAL, G.WIRE_NONMETAL, G.FOLIAGE, G.WOOD, G.PERSON]] = True

    def floor_fn(pq):
        x0, x1 = sc.balcony_x_m
        y0, y1 = sorted(sc.balcony_y_m)
        if x0 <= pq[0] <= x1 and y0 <= pq[1] <= y1:
            return sc.floor_z_m
        return ground_z

    while t < t_end:
        # ---------------- guidance rate
        if person is not None:
            x = person["x0_m"] + person["amp_m"] * math.sin(2 * math.pi * t / person["period_s"] + person["phase_rad"])
            hw = person["half_w_m"]
            scene.move_box("person", [x - hw, person["y_m"] - 0.15, person["z_m"]],
                           [x + hw, person["y_m"] + 0.15, person["z_m"] + person["height_m"]])
        b = hexv.body
        w = wind.velocity(b.pos, t)
        R_est = quat_to_rot(hexv.sensors.ahrs.read(t).value) if hexv.sensors.ahrs.read(t) is not None else np.eye(3)
        am = hexv.sensors.acc.read(t)
        a_w = R_est @ (am.value if am is not None else np.array([0, 0, GRAVITY_M_S2])) - np.array([0, 0, GRAVITY_M_S2])
        nav.predict(a_w, gdt)
        if hexv.fc.motors_off:
            nav.x[3:6] *= 0.0
        # sensors (truth sampled here, delivered later)
        vel_body = b.R.T @ b.vel
        for rd in radars:
            if rd.due(t):
                rd.scan(t, b.pos, b.R, vel_body, scene)
        if ir.due(t):
            ir.scan(t, b.pos, b.R, scene)
        if rr.due(t):
            near_facade = G.nearest_distance(b.pos, scene.blo, scene.bhi, scene.bact, scene.bmat, scene.c0[:0],
                                             scene.c1[:0], scene.crad[:0], scene.cact[:0], scene.sc[:0], scene.sr[:0],
                                             scene.sact[:0], skip)
            rr.sample(t, b.pos, b.R, scene, near_facade)
        if lora.due(t):
            lora.sample(t, b.pos)
        if baro.due(t):
            baro.sample(t, b.pos[2], float(np.linalg.norm(w)) if near_facade < 5 else 0.0)
        # fuse
        p_est = nav.pos.copy()
        match_pts = []
        ir_ahead = math.inf
        ir_rep = None
        ir_close_dirs = None
        up_close = down_close = False
        for rd in radars:
            for tm, m in rd.read(t):
                if m["vel_body"] is not None:
                    nav.vel_update(R_est @ m["vel_body"], rd.c["velocity_noise_std_m_s"])
                d_w = m["beam_dir_body"] @ R_est.T
                hit = m["hit"]
                lens = np.where(hit, m["range_m"], 8.0)
                hcls = np.array([CLS_MAP[int(x)] for x in m["cls"]])
                grid.integrate(np.repeat(p_est[None, :], len(d_w), 0), d_w, lens, hit, np.zeros(len(d_w), bool),
                               hcls, np.full(len(d_w), 8.0), gain=1)
        for tm, m in ir.read(t):
            d_w = m["dir_body"] @ R_est.T
            v = m["valid"]
            lens = np.where(v, m["range_m"], m["max_range_m"])
            pts = p_est + d_w[v] * m["range_m"][v][:, None]
            match_pts.append(pts)
            if mis.state in ("OUTBOUND", "FALLBACK_SKY") and nav.use["match"] and len(pts) > 20 \
                    and int(t * dcfg["scan_match_hz"]) != int((t - gdt) * dcfg["scan_match_hz"]):
                off, best, s0 = grid.match(pts, dcfg["scan_match_search_m"])
                if best >= s0 + max(6, 0.15 * len(pts)) and np.linalg.norm(off) > 0:
                    nav.pos_update(p_est + off, 0.15)
            # anything the infrared sees standing INSIDE the balcony footprint (from the beacon's install
            # data: railing offset, width, depth) is not the balcony itself: a person or an object.
            # Wait while it is within 1.2 m of the hexacopter's descent column.
            if v.any() and mis.state in ("CROSS", "DESCEND", "LOWER", "TAKEOFF"):
                bx0, bx1 = sc.balcony_x_m[0] + 0.35, sc.balcony_x_m[1] - 0.35
                by0, by1 = min(sc.balcony_y_m) + 0.35, max(sc.balcony_y_m) - 0.35
                inside = ((pts[:, 0] > bx0) & (pts[:, 0] < bx1) & (pts[:, 1] > by0) & (pts[:, 1] < by1)
                          & (pts[:, 2] > sc.floor_z_m + 0.25) & (pts[:, 2] < sc.floor_z_m + 1.9)
                          & (np.hypot(pts[:, 0] - p_est[0], pts[:, 1] - p_est[1]) < 1.2))
                if inside.sum() >= 4:
                    mis.t_dyn = t
            # infrared readings can clear cells; no-reading zones only mark unseen cells empty
            ok = ~m["invalid"]
            grid.integrate(np.repeat(p_est[None, :], ok.sum(), 0), d_w[ok], lens[ok], v[ok], v[ok],
                           np.zeros(ok.sum(), np.int64), lens[ok])
            # nearest reading ahead (ring sensors within 60 deg of travel), and the down sensor
            vdir = mis.v_sp[:2] if np.linalg.norm(mis.v_sp[:2]) > 0.05 else None
            if vdir is not None and v.any():
                hd = d_w[:, :2]
                cosang = (hd @ vdir) / (np.linalg.norm(hd, axis=1) * np.linalg.norm(vdir) + 1e-9)
                sel = v & (cosang > 0.5) & (np.abs(d_w[:, 2]) < 0.5)
                if sel.any():
                    ir_ahead = float(m["range_m"][sel].min())
            close_ring = v & (ir.sensor_of_ray < 8) & (m["range_m"] < 0.5) & (np.abs(d_w[:, 2]) < 0.7)
            if close_ring.any():
                dh = d_w[close_ring].copy()
                dh[:, 2] = 0.0
                dh /= np.linalg.norm(dh, axis=1)[:, None] + 1e-9
                ir_close_dirs = dh[::4]
            up = v & (ir.sensor_of_ray == 8)
            dn = v & (ir.sensor_of_ray == 9)
            up_close = bool(up.any() and m["range_m"][up].min() < hexv.hh + 0.2)
            down_close = bool(dn.any() and np.percentile(m["range_m"][dn], 20) < hexv.hh + 0.2)
            # keep the cage off what the ring sees close by (balcony walls, railing, a person)
            ring = v & (ir.sensor_of_ray < 8) & (np.abs(d_w[:, 2]) < 0.6)
            if ring.any():
                rr_ = m["range_m"][ring]
                close = rr_ < 0.8
                if close.any():
                    dh = d_w[ring][close].copy()
                    dh[:, 2] = 0.0
                    wgt = ((0.8 - rr_[close]) / 0.5)[:, None]
                    rep = -(dh * wgt).sum(0) / max(1, close.sum()) * 0.8
                    n_ = np.linalg.norm(rep)
                    ir_rep = rep * (0.5 / n_) if n_ > 0.5 else rep
            # railing check while crossing: highest return ahead (within 0.9 m) relative to the hex
            vdir2 = mis.v_sp[:2] if np.linalg.norm(mis.v_sp[:2]) > 0.05 else None
            if vdir2 is not None and v.any():
                rel = pts - p_est
                hdist = np.hypot(rel[:, 0], rel[:, 1])
                ahead = ((rel[:, :2] @ vdir2) / (hdist * np.linalg.norm(vdir2) + 1e-9) > 0.3) & (hdist < 0.9) \
                    & (rel[:, 2] > -0.8) & (rel[:, 2] < -0.05)
                if ahead.any():
                    Mission.low_ahead_dz = float(rel[ahead, 2].max())
                    Mission.t_low = t
            # over the balcony floor (height known from the beacon's install data): down range = height
            dn_v = v & (ir.sensor_of_ray == 9)
            if dn_v.sum() > 20 and mis.state in ("CROSS", "DESCEND", "TAKEOFF", "LOWER"):
                bx0, bx1 = sc.balcony_x_m
                by0, by1 = sorted(sc.balcony_y_m)
                if bx0 + 0.3 < p_est[0] < bx1 - 0.3 and by0 + 0.3 < p_est[1] < by1 - 0.3:
                    tilt_c = max(0.5, float(R_est[2, 2]))
                    nav.z_update(floor_est + float(np.median(m["range_m"][dn_v])) * tilt_c, 0.05)
            down = ir.sensor_of_ray == 9
            dv = v & down
            Mission.down_range = float(np.percentile(m["range_m"][dv], 30)) if dv.sum() > 8 else None
        for tm, m in rr.read(t):
            d_b = m["range_m"] * np.array([math.cos(m["el_rad"]) * math.cos(m["az_rad"]),
                                           math.cos(m["el_rad"]) * math.sin(m["az_rad"]), math.sin(m["el_rad"])])
            if nav.ranging_update(beacon, R_est @ d_b, m["range_m"], m["sigma_r_m"], m["sigma_b_rad"]):
                los_n += 1
        for tm, m in lora.read(t):
            nav.range_update(beacon, m["range_m"], m["sigma_m"])
        # barometer: average over 0.5 s, fuse at 2 Hz with 0.3 m (its error near facades is a slowly
        # varying dynamic-pressure term, not white noise; fusing every 25 Hz sample as white made the
        # filter over-confident in height and reject the ranging radio's elevation)
        for tm, z in baro.read(t):
            # residual against the estimate at the sample time (the vehicle may be climbing at 3 m/s)
            baro_acc.append(z - (nav.x[2] - nav.x[5] * (t - tm) + nav.x[6]))
        if t - t_baro >= 0.5 and baro_acc:
            nav.baro_update(nav.x[2] + nav.x[6] + float(np.mean(baro_acc)), 0.3)
            baro_acc.clear()
            t_baro = t
        cev = [m for _, m in contact.read(t)]
        mis.v_sp = mis.v_sp
        o = mis.step(t, hexv, ir_ahead, cev, hexv.energy_j, E_use, cup)
        # infrared safety filter (every flying state): no velocity toward anything the ring sees
        # within 0.5 m of the centre (0.26 m from the cage), plus a push away; same vertically with the
        # up/down sensors (except when landing)
        if ir_close_dirs is not None and not hexv.fc.motors_off:
            v = o["v_sp"].copy()
            for dvec in ir_close_dirs:
                comp = float(v @ dvec)
                if comp > 0:
                    v = v - comp * dvec
            o["v_sp"] = v
        if mis.state in ("CROSS", "DESCEND", "TAKEOFF", "LOWER", "INBOUND", "OUTBOUND", "FALLBACK_SKY") \
                and ir_rep is not None:
            o["v_sp"] = o["v_sp"] + ir_rep
        if up_close and o["v_sp"][2] > 0:
            o["v_sp"][2] = 0.0
        if down_close and o["v_sp"][2] < 0 and mis.state not in ("DESCEND",):
            o["v_sp"][2] = 0.0
        # stay inside the map window (soft wall, 1 m)
        pe = nav.pos
        for ax in range(3):
            if pe[ax] < sc.window_lo_m[ax] + 1.0 and o["v_sp"][ax] < 0:
                o["v_sp"][ax] = 0.0
            if pe[ax] > sc.window_hi_m[ax] - 1.0 and o["v_sp"][ax] > 0:
                o["v_sp"][ax] = 0.0
        mis.v_sp = o["v_sp"]
        if mis.state == "LANDED" and land_pos is None:
            land_pos = b.pos.copy()
        # delivery side effects
        if mis.state == "LOWER" and cup is None:
            cup = Cup(0.4, dcfg["line_cd_area_m2"], env["air_density_kg_m3"], b.pos - np.array([0, 0, hexv.hh]))
            hexv.phys.set_mass(h["mass_empty_kg"])
        if mis.delivered and not change_done and sc.change is not None:
            for typ, i in sc.change["items"]:
                {"box": scene.bact, "cap": scene.cact, "sph": scene.sact}[typ][i] = True
            change_done = True
        # outer loop
        hexv.fc.motors_off = bool(o["motors_off"])
        if not hexv.fc.motors_off:
            ev = o["v_sp"] - nav.vel
            vi = np.clip(vi + ev * gdt, -3.0, 3.0)
            a = kv * ev + ki * vi
            an = float(np.linalg.norm(a))
            amax = h["outer"]["a_max_m_s2"]
            if an > amax:
                a *= amax / an
            if o["yaw"] is not None and not hexv.yaw_free:
                dy = wrap_pi(o["yaw"] - yaw_cur)
                lim = math.radians(h["outer"]["yaw_rate_max_deg_s"]) * gdt
                yaw_cur = wrap_pi(yaw_cur + max(-lim, min(lim, dy)))
            if hexv.yaw_free:
                yaw_cur = euler_from_rot(R_est)[2]
            hexv.fc.set_accel(t, a, yaw_cur)
        else:
            vi[:] = 0.0
        # truth metrics
        e = float(np.linalg.norm(nav.pos - b.pos))
        nav_err_max = max(nav_err_max, e)
        nav_err_sum += e
        nav_n += 1
        if record and nav_n % 10 == 0:
            trace.append([t, *b.pos, *nav.pos, mis.state, nav.n_acc["vel"], nav.n_acc["ranging"], nav.n_rej["vel"], nav.n_rej["ranging"], *nav.vel, *b.vel])
        if fail is not None and hexv.failed_rotor is None and t >= fail[0]:
            hexv.fail_rotor(t, fail[1])
        # ---------------- physics substeps
        for _ in range(nsub):
            fx = None
            if cup is not None and not cup.released:
                anchor = hexv.body.pos - np.array([0, 0, hexv.hh])
                fx = cup.step(anchor, hexv.body.vel, w, dt, floor_fn)
            hexv.step(t, w, scene, extra_force_w=fx)
            contact.step(t, hexv.contact_force_w, dt)
            # contact events (truth)
            now = {}
            for typ, idx, depth, vn in hexv.touching:
                key = (typ, idx)
                now[key] = True
                if key not in active:
                    kind = int((scene.bkind, scene.ckind, scene.skind)[typ][idx])
                    normal_up = False
                    if typ == 0 and kind in (G.K_STRUCTURE, G.K_GROUND) and \
                            hexv.body.pos[2] > scene.bhi[idx, 2] - 0.05:
                        normal_up = True
                    events.append(dict(t=t, kind=kind, speed=max(0.0, -vn), state=mis.state, pos=hexv.body.pos.copy(),
                                       prim=(typ, idx),
                                       nav_err=float(np.linalg.norm(nav.pos - hexv.body.pos)), landing=normal_up and
                                       mis.state in ("DESCEND", "LANDED", "TAKEOFF", "DOWN")))
            active = now
            t += dt
        if hexv.body.pos[2] < ground_z + 0.15 and not mis.done:
            crashed = True
            mis.result = "ground"
            break
        if hexv.energy_j > E_use:
            mis.result = "battery"
            break
        if mis.done:
            if mis.result == "pickup" and pickup_err is None:
                pickup_err = float(np.linalg.norm(hexv.body.pos - sc.release_m))
                break
            if mis.result != "pickup":
                break
    # ---------------- scoring
    rating = h["cage_impact_rating_m_s"]
    coll = {}
    cage = 0
    first_coll = None
    for ev in events:
        if ev["landing"]:
            continue
        is_coll = ev["kind"] in SOFT_KINDS or ev["speed"] > rating
        if is_coll:
            name = G.KIND_NAMES[ev["kind"]]
            coll[name] = coll.get(name, 0) + 1
            if first_coll is None:
                first_coll = ev
        else:
            cage += 1
    deliver_err = None
    if mis.delivered:
        if method == "line" and mis.cup_released_pos is not None:
            deliver_err = float(np.linalg.norm((mis.cup_released_pos - sc.pad_m)[:2]))
        elif method == "land" and land_pos is not None:
            deliver_err = float(np.linalg.norm((land_pos - sc.pad_m)[:2]))
    label = sc.corridor_3m_bool
    gave = mis.gave_up
    pickup_ok = mis.result == "pickup" and pickup_err is not None and pickup_err < dcfg["pickup_radius_m"]
    limit = ""
    if first_coll is not None:
        k = first_coll["kind"]
        limit = LIMIT_TEXT.get(k, "navigation drift" if first_coll["nav_err"] > 0.5 else "gust / control")
    elif gave and label:
        limit = {"dead end": "margins (2 m wire / 3 m tree) or unseen cells close the way",
                 "time": "slow exploration (0.5-1 m/s near structure, unseen cells)",
                 "energy": "battery", "repeated contact": "contacts with unseen objects",
                 "blocked at the railing": "person / obstacle on the balcony",
                 "person on the balcony": "person on the balcony (infrared sees it moving)"}.get(mis.give_up_reason, mis.give_up_reason)
    elif not pickup_ok and mis.result == "pickup":
        limit = "navigation drift at the pickup point"
    elif crashed:
        limit = "loss of control"
    row = dict(scenario_label=sid, seed_nd=seed, visit_count=visit, mount_label=mount, method_label=method,
               corridor_3m_bool=bool(label), delivered_bool=bool(mis.delivered), pickup_bool=bool(pickup_ok),
               gave_up_bool=bool(gave), give_up_reason_label=mis.give_up_reason.replace(" ", "_") or "none",
               correct_give_up_bool=bool(gave and not label), wrong_give_up_bool=bool(gave and label),
               fallback_label=mis.fallback, result_label=mis.result or "timeout", crashed_bool=crashed,
               collisions_count=sum(coll.values()), cage_contacts_count=cage,
               **{f"coll_{k}_count": coll.get(k, 0) for k in G.KIND_NAMES},
               time_to_delivery_s=mis.t_delivered if mis.t_delivered is not None else float("nan"),
               time_total_s=t, energy_total_j=hexv.energy_j,
               energy_at_delivery_j=mis.energy_at_delivery if mis.energy_at_delivery is not None else float("nan"),
               release_nav_err_m=float(np.linalg.norm(p0 - sc.release_m)),
               delivery_err_m=deliver_err if deliver_err is not None else float("nan"),
               pickup_err_m=pickup_err if pickup_err is not None else float("nan"),
               nav_err_max_m=nav_err_max, nav_err_mean_m=nav_err_sum / max(nav_n, 1),
               nav_err_final_m=float(np.linalg.norm(nav.pos - hexv.body.pos)),
               ranging_used_count=los_n, ranging_rejected_count=nav.n_rej["ranging"],
               ranging_false_count=rr.n_false, match_used_count=nav.n_acc["match"],
               dead_ends_count=int(mis.dead_ends) if mis.dead_ends < 90 else -1, replans_count=mis.n_replans,
               plan_fail_count=mis.n_plan_fail, astar_max_expansions_count=mis.max_expansions,
               sun_bool=bool(sc.sun_bool), wind_mean_m_s=float(np.linalg.norm(sc.wind_mean_m_s)),
               person_waits_count=mis.n_dyn_waits, sensor_limit_label=limit.replace(",", ";"), wall_s=time.time() - t_wall,
               cup_swing_max_m=(cup.max_swing_m if cup else float("nan")))
    if verbose:
        for n in mis.notes:
            print("  ", n)
        for ev in events[:30]:
            print(f"   contact t={ev['t']:.1f} {G.KIND_NAMES[ev['kind']]} prim={ev['prim']} v={ev['speed']:.2f} "
                  f"{ev['state']} pos={np.round(ev['pos'], 2)} landing={ev['landing']}")
        print("   pad", np.round(sc.pad_m, 2), "entry", np.round(mis.entry, 2), "over_pad", np.round(mis.over_pad, 2))
    out = dict(row=row, rec_path=[p.tolist() for p in mis.rec[: (len(mis.rec))]], trace=trace)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="S1")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mount", default=None)
    ap.add_argument("--method", default=None)
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    o = {}
    if a.mount:
        o["mount"] = a.mount
    if a.method:
        o["method"] = a.method
    r = run_trial(a.scenario, a.seed, o, verbose=True)
    print(json.dumps(r["row"], indent=1, default=float))


if __name__ == "__main__":
    main()

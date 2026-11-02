"""On-vehicle implicit-coordination allocator: `team_alloc.TeamAllocCore`
behind `onboard_l5.TeamAllocator`, so the vehicle runs exactly the decision
logic the simulator validated (ecobridge/implicit_coord.py imports the same
core; CLAIMS_ENVELOPE.md has the numbers).

What the vehicle needs that the simulator gives for free:

  * One clock. Plans are only identical across vehicles if they are computed
    at the same instant from the same inputs, so epochs are aligned to WALL
    time -- epoch k starts at k * epoch_s -- and every plan and STATE stamp
    uses the epoch's start time, never "now". Vehicle clocks must agree to
    well under one epoch (chrony/PTP over the mesh gives milliseconds; the
    team link's own replay window already assumes synchronised clocks).
  * One frame. Each vehicle flies in its own takeoff-origin ENU frame. STATE
    positions and jobs are in the MISSION frame; `to_mission` /
    `from_mission` convert (default: identity, i.e. every vehicle launched
    from the same point with the same heading). Wire a
    `shared_frame.TeamFrameEstimator` with `frame_from_estimator`; while this
    vehicle's own frame is unresolved, the allocator holds (returns None)
    instead of guessing.
  * Job completion. A visit job is done on arriving within the vehicle's
    reach (`RosterEntry.reach`); `on_arrive(job_id)` may veto that until the
    job's action (a photo, a dwell) has actually happened.

Search missions (unbriefed targets, sweep cells, sightings) are validated in
simulation only (ecobridge.implicit_coord.SearchCoordinationRuntime); this
adapter covers known-location jobs.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Callable, Dict, Optional

try:
    from .team_alloc import (BRIEFING_EFF, PERIODIC_REPLAN_EPOCHS, PLAN_RESTARTS, RosterEntry,
                             TeamAllocCore)
    from .vehicle_class import get_class
except ImportError:  # pragma: no cover - device flat-install path
    from team_alloc import (BRIEFING_EFF, PERIODIC_REPLAN_EPOCHS, PLAN_RESTARTS, RosterEntry,
                            TeamAllocCore)
    from vehicle_class import get_class

logger = logging.getLogger(__name__)

MSG_TYPE = "ALLOC_STATE"
DEFAULT_REACH_M = 2.0   # a visit job counts as reached inside this, unless briefed otherwise


def _xy(p) -> tuple:
    return (round(float(p[0]), 3), round(float(p[1]), 3))


def frame_from_estimator(estimator, self_id: str) -> tuple:
    """(to_mission, from_mission, frame_ok) callables over a
    shared_frame.TeamFrameEstimator, for ImplicitTeamAllocator."""
    def to_mission(p):
        q = estimator.to_mission(self_id, (float(p[0]), float(p[1]), 0.0))
        return (float(q[0]), float(q[1]))

    def from_mission(p):
        q = estimator.from_mission((float(p[0]), float(p[1]), 0.0))
        return (float(q[0]), float(q[1]))

    def frame_ok():
        return estimator.is_resolved(self_id)
    return to_mission, from_mission, frame_ok


class ImplicitTeamAllocator:
    """`onboard_l5.TeamAllocator`. Construct with the briefing: every
    roster member's RosterEntry (class, speed, reach, link range, home in the
    mission frame) and the job list (id -> (x, y) in the mission frame)."""

    def __init__(self, me: str, roster: Dict[str, RosterEntry], jobs: Dict[str, tuple], *,
                 planners: Optional[set] = None, epoch_s: float = 0.5,
                 clock: Callable[[], float] = time.time,
                 to_mission: Optional[Callable] = None, from_mission: Optional[Callable] = None,
                 frame_ok: Optional[Callable[[], bool]] = None,
                 on_arrive: Optional[Callable[[str], bool]] = None,
                 alt_m: Optional[float] = None, job_alt: Optional[Dict[str, float]] = None,
                 idle_done_s: float = 120.0, restarts: int = PLAN_RESTARTS):
        if me not in roster:
            raise ValueError(f"{me} is not on the briefed roster {sorted(roster)}")
        self.me = me
        self.epoch_s = float(epoch_s)
        self.clock = clock
        self.to_mission = to_mission or _xy
        self.from_mission = from_mission or _xy
        self.frame_ok = frame_ok or (lambda: True)
        self.on_arrive = on_arrive or (lambda job_id: True)
        self.alt_m = alt_m
        self.job_alt = dict(job_alt or {})
        self.idle_done_s = float(idle_done_s)
        self._idle_since: Optional[float] = None
        self.metrics: dict = {}
        self.core = TeamAllocCore(me, dict(roster), {k: _xy(v) for k, v in jobs.items()},
                                  set(roster) if planners is None else set(planners),
                                  restarts=restarts, epoch_s=self.epoch_s, metrics=self.metrics)
        self._last_epoch: Optional[int] = None
        self._last_t: Optional[float] = None

    @property
    def committed(self) -> Optional[str]:
        return self.core.committed

    @property
    def done_jobs(self) -> set:
        """Jobs this vehicle knows are done (its own, and every teammate's it has heard)."""
        return set(self.core.done)

    @property
    def done(self) -> bool:
        """onboard_l5 / autonomy_fallback contract: True when this vehicle has
        nothing left to do -- every job known done, or (when it can't know,
        e.g. in blackout) idle for `idle_done_s` with nothing on its route,
        long enough that any teammate's overdue job would have been released
        to it by now."""
        if set(self.core.jobs) <= self.core.done:
            return True
        return self._idle_since is not None and self.clock() - self._idle_since >= self.idle_done_s

    def step(self, now_s, me, peers, inbox):
        t = float(self.clock())
        for msg in inbox:
            if msg.get("type") != MSG_TYPE or msg.get("sender") in (None, self.me):
                continue
            try:
                self.core.ingest(str(msg["sender"]), msg, float(msg["epoch_t"]), t)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"team_alloc: dropping malformed {MSG_TYPE} from "
                               f"{msg.get('sender')!r}: {e}")
        if not self.frame_ok():
            return None, []   # never act on a position we can't place in the team's frame
        pos = _xy(self.to_mission(me.pos))
        c = self.core.committed
        if c is not None and c not in self.core.done:
            if math.dist(pos, self.core.job_pos(c)) <= self.core.roster[self.me].reach \
                    and self.on_arrive(c):
                self.core.mark_done(c)
        if self._last_t is not None and t > self._last_t:
            self.core.track_progress(pos, t - self._last_t)
        self._last_t = t

        out = []
        epoch = int(math.floor(t / self.epoch_s))
        if epoch != self._last_epoch:
            self._last_epoch = epoch
            te = round(epoch * self.epoch_s, 6)
            self.core.maybe_plan(te, pos, periodic=epoch % PERIODIC_REPLAN_EPOCHS == 0)
            self.core.advance()
            payload = self.core.state_payload(te, pos)
            out.append(dict(payload, type=MSG_TYPE, epoch_t=te))
        else:
            self.core.advance()

        c = self.core.committed
        if c is None:
            if self._idle_since is None:
                self._idle_since = t
            return None, out
        self._idle_since = None
        x, y = self.from_mission(self.core.job_pos(c))
        if c in self.job_alt:
            z = float(self.job_alt[c])
        else:
            z = float(me.pos[2]) if self.alt_m is None else float(self.alt_m)
        return [float(x), float(y), z], out


def frame_from_home(home) -> tuple:
    """(to_mission, from_mission) for a vehicle whose takeoff-origin ENU frame
    sits at `home` = (x, y[, yaw_deg]) in the mission frame -- yaw being the
    angle of its local +x axis from mission +x, counter-clockwise. With a
    compass- or GPS-referenced EKF yaw every vehicle's local axes point the
    same way and yaw is 0; with VIO yaw (EK3_SRC1_YAW = ExternalNav) the
    local frame turns with the vehicle's heading at boot, which must then be
    briefed or estimated (shared_frame.TeamFrameEstimator)."""
    hx, hy = float(home[0]), float(home[1])
    yaw = math.radians(float(home[2])) if len(home) > 2 else 0.0
    c, s = math.cos(yaw), math.sin(yaw)

    def to_mission(p):
        x, y = float(p[0]), float(p[1])
        return (hx + c * x - s * y, hy + s * x + c * y)

    def from_mission(p):
        x, y = float(p[0]) - hx, float(p[1]) - hy
        return (c * x + s * y, -s * x + c * y)
    return to_mission, from_mission


def roster_from_briefing(entries: list, class_table: Optional[dict] = None) -> Dict[str, RosterEntry]:
    """Build RosterEntry objects from a mission file's roster list
    (`{id, vclass, home: [x, y]}`), filling speed / link range from
    vehicle_class, reach from `class_table` (class name -> m, default
    DEFAULT_REACH_M) and eff from BRIEFING_EFF, unless the entry overrides
    them (`speed_mps`, `reach_m`, `comms_range_m`, `los_required`, `eff`)."""
    out = {}
    for e in entries:
        vc = get_class(str(e.get("vclass", "quadcopter")))
        reach = float(e.get("reach_m", (class_table or {}).get(vc.name, DEFAULT_REACH_M)))
        out[str(e["id"])] = RosterEntry(
            kind=vc.name, speed=float(e.get("speed_mps", vc.max_speed_mps)), reach=reach,
            comms_range_m=float(e.get("comms_range_m", vc.comms.range_m)),
            los_required=bool(e.get("los_required", vc.comms.los_required)),
            home=_xy(e.get("home", (0.0, 0.0))),
            eff=float(e.get("eff", BRIEFING_EFF.get(vc.name, 1.0))))
    return out

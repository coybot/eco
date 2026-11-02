"""Cloud-loss fallback: fly the pre-briefed mission onboard when the control plane goes quiet.

``daemon.py`` takes every order from its control plane (AWS IoT Core or a local
GCS). If that link dies mid-mission the aircraft is left flying whatever the last
command was, with nobody able to change it. With ``autonomy_fallback.enabled`` set
in config.yaml, this module lets the daemon hand the vehicle to the decentralised
onboard L5 loop (``onboard_l5`` + ``ardupilot_l5``) instead, flying a small mission
briefed before takeoff, and hand it back the moment an operator command arrives.

Default OFF. The daemon creates a :class:`FallbackManager` only when enabled, and
every daemon hook is a no-op without one.

When it engages — ALL of:
  * the control plane has been silent for ``cloud_silence_s``. "Silent" means no
    inbound message (command, video, shadow), no reconnect, and no broker
    acknowledgement of the daemon's own heartbeat (published at QoS 1 while this is
    enabled, so its PUBACK proves the broker is reachable). Silence of the operator
    alone is not silence: the heartbeat ack keeps arriving.
  * a mission is running (the daemon's MissionLoop), the vehicle is armed, the pilot
    has not taken over from the transmitter, and the vehicle is in GUIDED.

What it does: stops the MissionLoop (the daemon's own newest-command-wins stop),
zero-velocity holds, builds the onboard loop, and flies the briefed goals. It is
registered as the daemon's active mission, so the heartbeat reports it and a new
mission or a stop from the operator replaces it exactly as it would a MissionLoop.

How it ends:
  * an operator command arrives  -> zero velocity, control handed back; the
    command then runs as normal (a stop holds/lands, a mission flies, ...)
  * the briefed goals are done   -> ``on_complete`` (default rtl)
  * someone else takes the vehicle (mode leaves GUIDED: pilot, battery guard, FC
    failsafe) -> stops sending, changes nothing: no fight over the aircraft
  * ANY failure (cannot build, telemetry lost, confidence lost, left the briefed
    region, overran ``max_duration_s``, loop error) -> RTL, or LAND if the FC
    refuses RTL (rover: RTL, else HOLD) — the battery guard's own escalation.

Pre-briefed mission file (YAML, ENU metres from the takeoff point)::

    region: {min: [-40, -40], max: [40, 40], max_alt_m: 15}   # hard box: leaving it = RTL
    roster:                                                    # optional; if given, must list me
      - {id: drone-abc, vclass: quadcopter}
      - {id: rover-1, vclass: rover}
    tasks:                      # a list every member flies in order, or {drone_id: [goals]}
      - [10, 0, 5]
      - [10, 10, 5]
    search_area: {min: [0, 0], max: [20, 10], spacing_m: 5, alt_m: 5}  # alternative to tasks
    home: [0, 0, 5]             # appended as the last goal when return_home is true
    return_home: true
    on_complete: rtl            # rtl | land | hold
    sensor: {type: d435i, mount_pitch_deg: 0, serial: null}   # or {type: none} (blind)

The team link is config.yaml's own ``team_link:`` section (TeamLink.from_config;
see drone/platforms/orin/MESH.md), and it must name a ``key_file``: the fallback
refuses to run over an unsigned team link, because with no cloud a forged
TASK_CLAIM or POSITION from anyone on the radio would steer the vehicle.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

MIN_SILENCE_S = 10.0     # two missed 5 s heartbeats: shorter than this is a hiccup, not a loss
CONF_RTL = 0.30          # team_runtime.CONF_RTL: below this the vehicle is lost
CONF_SLOW = 0.6          # team_runtime.CONF_SLOW: below this, speed scales with confidence
BLIND_SCALE = 0.08       # team_runtime's blind-agent creep


class MissionError(ValueError):
    """The pre-briefed mission file is missing, malformed, or unsafe to fly."""


# --------------------------------------------------------------------------- config
@dataclass
class FallbackConfig:
    enabled: bool = False
    cloud_silence_s: float = 15.0
    mission_file: Optional[str] = None
    control_hz: float = 10.0
    max_duration_s: float = 900.0

    @classmethod
    def from_dict(cls, d, base_dir=None) -> "FallbackConfig":
        d = dict(d or {})
        cfg = cls(enabled=bool(d.get("enabled", False)),
                  cloud_silence_s=float(d.get("cloud_silence_s", 15.0)),
                  mission_file=d.get("mission_file"),
                  control_hz=float(d.get("control_hz", 10.0)),
                  max_duration_s=float(d.get("max_duration_s", 900.0)))
        if cfg.cloud_silence_s < MIN_SILENCE_S:
            logger.warning(f"autonomy_fallback.cloud_silence_s {cfg.cloud_silence_s:g} s is under "
                           f"{MIN_SILENCE_S:g} s; using {MIN_SILENCE_S:g} s")
            cfg.cloud_silence_s = MIN_SILENCE_S
        if cfg.mission_file and base_dir is not None and not Path(cfg.mission_file).is_absolute():
            cfg.mission_file = str(Path(base_dir) / cfg.mission_file)
        return cfg


# --------------------------------------------------------------------------- mission
@dataclass
class Region:
    min_xy: tuple
    max_xy: tuple
    max_alt_m: Optional[float] = None

    def contains(self, p) -> bool:
        x, y = float(p[0]), float(p[1])
        if not (self.min_xy[0] <= x <= self.max_xy[0] and self.min_xy[1] <= y <= self.max_xy[1]):
            return False
        return self.max_alt_m is None or float(p[2]) <= self.max_alt_m


def _vec3(v, what) -> list:
    try:
        out = [float(x) for x in v]
    except Exception:
        raise MissionError(f"{what}: expected [east, north, up], got {v!r}") from None
    if len(out) == 2:
        out.append(0.0)
    if len(out) != 3 or not all(math.isfinite(x) for x in out):
        raise MissionError(f"{what}: expected [east, north, up], got {v!r}")
    return out


def lawnmower(area: dict, lane: int = 0, lanes: int = 1) -> list:
    """Boustrophedon goals over area {min, max, spacing_m, alt_m}; with several
    vehicles each takes every ``lanes``-th east-west pass, starting at ``lane``."""
    lo, hi = _vec3(area["min"], "search_area.min"), _vec3(area["max"], "search_area.max")
    spacing = float(area.get("spacing_m", 5.0))
    alt = float(area.get("alt_m", 5.0))
    if spacing <= 0 or hi[0] < lo[0] or hi[1] < lo[1]:
        raise MissionError(f"search_area is empty or has spacing {spacing}")
    n_pass = int(math.floor((hi[1] - lo[1]) / spacing)) + 1
    goals = []
    for k, i in enumerate(range(lane, n_pass, max(1, lanes))):
        y = lo[1] + i * spacing
        xs = (lo[0], hi[0]) if k % 2 == 0 else (hi[0], lo[0])
        goals += [[xs[0], y, alt], [xs[1], y, alt]]
    return goals


@dataclass
class PrebriefedMission:
    region: Region
    tasks: object                      # list of goals, or {drone_id: [goals]}
    search_area: Optional[dict] = None
    roster: list = field(default_factory=list)
    home: Optional[list] = None
    return_home: bool = True
    on_complete: str = "rtl"
    sensor: dict = field(default_factory=lambda: {"type": "d435i"})
    # "static": every vehicle flies its goal list (tasks / tasks[drone_id]) in order.
    # "team": `tasks` is ONE shared job pool the roster splits between itself with
    # team_alloc_onboard.ImplicitTeamAllocator over the signed team link -- no two
    # vehicles on one job, a dead vehicle's jobs taken over. Coordinates are then
    # in the MISSION frame, and every roster entry needs its takeoff `home`.
    allocation: str = "static"

    @classmethod
    def load(cls, path) -> "PrebriefedMission":
        import yaml
        p = Path(path) if path else None
        if p is None or not p.exists():
            raise MissionError(f"autonomy_fallback.mission_file {path!r} does not exist")
        with open(p) as f:
            return cls.from_dict(yaml.safe_load(f) or {})

    @classmethod
    def from_dict(cls, d: dict) -> "PrebriefedMission":
        r = d.get("region")
        if not r:
            raise MissionError("mission has no region: refusing to fly unbounded")
        rmin, rmax = _vec3(r["min"], "region.min"), _vec3(r["max"], "region.max")
        region = Region((rmin[0], rmin[1]), (rmax[0], rmax[1]),
                        None if r.get("max_alt_m") is None else float(r["max_alt_m"]))
        if not d.get("tasks") and not d.get("search_area"):
            raise MissionError("mission has neither tasks nor search_area")
        on_complete = str(d.get("on_complete", "rtl")).lower()
        if on_complete not in ("rtl", "land", "hold"):
            raise MissionError(f"on_complete must be rtl, land or hold, not {on_complete!r}")
        roster = [dict(m) for m in (d.get("roster") or [])]
        for m in roster:
            if "id" not in m:
                raise MissionError(f"roster entry without an id: {m!r}")
        m = cls(region=region, tasks=d.get("tasks") or [], search_area=d.get("search_area"),
                roster=roster, home=None if d.get("home") is None else _vec3(d["home"], "home"),
                return_home=bool(d.get("return_home", True)), on_complete=on_complete,
                sensor=dict(d.get("sensor") or {"type": "d435i"}))
        allocation = str(d.get("allocation", "static")).lower()
        if allocation not in ("static", "team"):
            raise MissionError(f"allocation must be static or team, not {allocation!r}")
        m.allocation = allocation
        if allocation == "team":
            if isinstance(m.tasks, dict) or not m.tasks:
                raise MissionError("allocation: team needs `tasks` as one shared list of jobs")
            if m.search_area:
                raise MissionError("allocation: team does not take a search_area (sim-validated only)")
            if not m.roster:
                raise MissionError("allocation: team needs the full roster")
            for e in m.roster:
                if "home" not in e or len(e["home"]) < 2:
                    raise MissionError(f"allocation: team needs a home [x, y(, yaw_deg)] for {e['id']}")
        sensor_type = str(m.sensor.get("type", "d435i")).lower()
        if sensor_type not in ("d435i", "none"):
            raise MissionError(f"sensor.type must be d435i or none, not {sensor_type!r}")
        return m

    def roster_vclass(self) -> dict:
        return {str(e["id"]): str(e.get("vclass", "quadcopter")) for e in self.roster}

    def team_jobs(self, aerial: bool = True) -> tuple:
        """allocation: team -- ({job id: (x, y)}, {job id: up}) in the mission
        frame, every job validated against the region."""
        jobs, alt = {}, {}
        for i, g in enumerate(self.tasks):
            v = _vec3(g, f"task {i + 1}")
            if not self.region.contains(v):
                raise MissionError(f"task {i + 1} {v} is outside the briefed region")
            jobs[f"j{i}"] = (v[0], v[1])
            alt[f"j{i}"] = v[2] if aerial else 0.0
        return jobs, alt

    def goals_for(self, drone_id: str, aerial: bool = True) -> list:
        """This vehicle's goals, in order, validated against the region.
        A ground vehicle's goals are put on the ground (up = 0)."""
        ids = [str(e["id"]) for e in self.roster]
        if ids and drone_id not in ids:
            raise MissionError(f"{drone_id} is not on the mission roster {ids}")
        if isinstance(self.tasks, dict):
            raw = list(self.tasks.get(drone_id) or [])
        else:
            raw = list(self.tasks or [])
        goals = [_vec3(g, f"task {i + 1}") for i, g in enumerate(raw)]
        if self.search_area:
            lane = ids.index(drone_id) if ids else 0
            goals += lawnmower(self.search_area, lane, max(1, len(ids)))
        if self.return_home:
            goals.append(list(self.home) if self.home is not None else [0.0, 0.0, 5.0])
        if not aerial:
            goals = [[g[0], g[1], 0.0] for g in goals]
        if not goals:
            raise MissionError(f"no goals for {drone_id}")
        for i, g in enumerate(goals):
            if not self.region.contains(g):
                raise MissionError(f"goal {i + 1} {g} is outside the briefed region")
        return goals


# --------------------------------------------------------------------------- liveness
class ControlPlaneWatch:
    """When did the control plane last prove it was there?"""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._last = clock()

    def note_alive(self) -> None:
        self._last = self.clock()

    def silent_for(self) -> float:
        return max(0.0, self.clock() - self._last)


# --------------------------------------------------------------------------- safety
class ScaledActuator:
    """Applies team_runtime's per-agent speed modulation, which the sim's L5 runs
    always had and OnboardL5Runtime does not: below CONF_SLOW confidence, linear
    speed scales with it (floor 0.3); with the depth sensor blind, 0.08x creep.
    Yaw rate is left alone. Everything else is the wrapped actuator's."""

    def __init__(self, inner, telemetry):
        self.inner = inner
        self.telemetry = telemetry

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def scale(self) -> float:
        pose = self.telemetry.last_pose
        if pose is None:
            return 0.0
        s = 1.0
        if pose.loc_confidence < CONF_SLOW:
            s *= max(0.3, float(pose.loc_confidence))
        if not pose.sensor_ok:
            s *= BLIND_SCALE
        return s

    def send(self, action, vclass, dt):
        a = np.array(action, dtype=np.float64)
        s = self.scale()
        if a.size == 2:
            a[0] *= s
        else:
            a[:3] *= s
        return self.inner.send(a, vclass, dt)


def safe_recover(sdk, aerial: bool, reason: str) -> str:
    """The fallback's only failure path: the FC's own RTL; if refused, LAND (rover:
    HOLD). The same escalation drone_sdk's battery guard uses. Does nothing if the
    pilot has taken control from the transmitter: they are flying it."""
    logger.error(f"AUTONOMY FALLBACK: {reason} - returning to launch")
    try:
        if hasattr(sdk, "pilot_control") and sdk.pilot_control():
            logger.warning("AUTONOMY FALLBACK: the pilot has control; not commanding RTL")
            return "pilot"
    except Exception:
        pass
    try:
        if sdk._set_mode_confirmed("RTL"):
            return "rtl"
    except Exception as e:
        logger.error(f"AUTONOMY FALLBACK: RTL request failed: {e}")
    last = "LAND" if aerial else "HOLD"
    logger.error(f"AUTONOMY FALLBACK: RTL refused - switching to {last}")
    try:
        sdk._set_mode_confirmed(last)
    except Exception as e:
        logger.error(f"AUTONOMY FALLBACK: {last} request failed: {e}")
    return last.lower()


# --------------------------------------------------------------------------- run
class FallbackStatus:
    """What daemon.active_mission_status() reads off a mission: id, phases, phase."""

    def __init__(self, goals):
        self.mission_id = "autonomy-fallback"
        self.phases = [{"type": "onboard_l5", "objective": f"goal {g}"} for g in goals] or [{}]
        self.current_phase = 0


class FallbackRun:
    """The onboard loop, running until done, handed back, displaced, or failed.

    Has the ``abort()`` the daemon's stop path calls on its active mission.
    ``run()`` returns the outcome: 'handed_back', 'complete', 'yielded', or the
    safe_recover() result ('rtl' / 'land' / 'hold' / 'pilot') after a failure.
    """

    MAX_ERRORS = 10          # consecutive failed ticks (1 s at 10 Hz) before giving up
    REFUSE_S = 2.0           # setpoints refused this long = someone else has the vehicle
    LOST_S = 2.0             # confidence under CONF_RTL this long = lost

    def __init__(self, runtime, allocator, telemetry, actuator, region: Region, sdk,
                 aerial: bool, on_complete: str = "rtl", control_hz: float = 10.0,
                 max_duration_s: float = 900.0, goals=(),
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 cleanup: Optional[Callable[[], None]] = None):
        self.runtime, self.allocator, self.telemetry, self.actuator = runtime, allocator, telemetry, actuator
        self.region, self.sdk, self.aerial = region, sdk, aerial
        self.on_complete = on_complete
        self.period = 1.0 / max(1.0, control_hz)
        self.max_duration_s = max_duration_s
        self.clock, self.sleep = clock, sleep
        self.cleanup = cleanup
        self.cancel = threading.Event()
        self.status = FallbackStatus(list(goals))
        self.outcome: Optional[str] = None

    def abort(self) -> None:
        self.cancel.set()

    def _safe(self, reason: str) -> str:
        try:
            self.actuator.stop()
        except Exception:
            pass
        return safe_recover(self.sdk, self.aerial, reason)

    def _complete(self) -> str:
        logger.info(f"AUTONOMY FALLBACK: briefed goals done; {self.on_complete}")
        if self.on_complete == "hold":
            self.actuator.stop()
            if not self.aerial:
                self.sdk._set_mode_confirmed("HOLD")
            return "complete"
        if self.on_complete == "land" and self.aerial:
            self.sdk.land()
            return "complete"
        if not self.sdk._set_mode_confirmed("RTL"):
            return self._safe("RTL at the end of the briefed mission was refused")
        return "complete"

    def run(self) -> str:
        try:
            self.outcome = self._loop()
        except Exception as e:
            logger.exception("AUTONOMY FALLBACK: loop crashed")
            self.outcome = self._safe(f"loop crashed: {e}")
        finally:
            if self.cleanup is not None:
                try:
                    self.cleanup()
                except Exception as e:
                    logger.warning(f"AUTONOMY FALLBACK: cleanup: {e}")
        logger.info(f"AUTONOMY FALLBACK: ended ({self.outcome})")
        return self.outcome

    def _loop(self) -> str:
        t0 = self.clock()
        errors, refused_since, lost_since = 0, None, None
        while True:
            if self.cancel.is_set():
                self.actuator.stop()
                return "handed_back"
            now = self.clock()
            if now - t0 > self.max_duration_s:
                return self._safe(f"still flying after max_duration_s {self.max_duration_s:g} s")
            try:
                self.runtime.step(self.period)
                errors = 0
            except Exception as e:
                errors += 1
                logger.warning(f"AUTONOMY FALLBACK: tick failed ({errors}/{self.MAX_ERRORS}): {e}")
                try:
                    self.actuator.stop()
                except Exception:
                    pass
                if errors >= self.MAX_ERRORS:
                    return self._safe(f"{errors} failed ticks in a row, last: {e}")
                self.sleep(self.period)
                continue
            idx = getattr(self.allocator, "index", None)
            if idx is not None:
                self.status.current_phase = min(int(idx), len(self.status.phases) - 1)
            pose = self.telemetry.last_pose
            if pose is not None and not self.region.contains(pose.pos_enu):
                return self._safe(f"left the briefed region at {[round(float(x), 1) for x in pose.pos_enu]}")
            if pose is not None and pose.loc_confidence < CONF_RTL:
                lost_since = now if lost_since is None else lost_since
                if now - lost_since >= self.LOST_S:
                    return self._safe(f"localisation confidence {pose.loc_confidence:.2f} under {CONF_RTL}")
            else:
                lost_since = None
            if getattr(self.actuator, "last_refusal", None):
                refused_since = now if refused_since is None else refused_since
                if now - refused_since >= self.REFUSE_S:
                    logger.warning(f"AUTONOMY FALLBACK: setpoints refused ({self.actuator.last_refusal}); "
                                   f"something else has the vehicle - stopping, changing nothing")
                    return "yielded"
            else:
                refused_since = None
            if getattr(self.allocator, "done", False) and self.runtime.at_goal():
                return self._complete()
            self.sleep(self.period)


# --------------------------------------------------------------------------- build
def team_link_problem(team_link_cfg) -> Optional[str]:
    """Why this team_link config is not good enough for the fallback, or None.
    The fallback needs a signed link: a key_file, and not insecure_allow_unsigned."""
    cfg = team_link_cfg or {}
    if not cfg:
        return "config.yaml has no team_link section"
    if not cfg.get("key_file"):
        return "team_link.key_file is not configured"
    if cfg.get("insecure_allow_unsigned"):
        return "team_link.insecure_allow_unsigned is set"
    return None


def build_run(sdk, cfg: FallbackConfig, mission: PrebriefedMission, drone_id: str,
              vehicle_type: str, actuator=None, team_link_cfg=None,
              base_dir=None) -> FallbackRun:
    """Wire the real onboard loop: ArduPilot telemetry/actuator, depth scan, signed
    team link (``team_link_cfg`` is config.yaml's team_link section, relative paths
    against ``base_dir``). Raises on anything missing; the caller turns that into
    safe_recover()."""
    problem = team_link_problem(team_link_cfg)
    if problem:
        raise RuntimeError(f"refusing an unsigned team link: {problem}")
    try:
        from .onboard_l5 import OnboardL5Runtime, StaticGoalAllocator, TeamLinkPeers
        from .team_alloc_onboard import ImplicitTeamAllocator, frame_from_home, roster_from_briefing
        from .ardupilot_l5 import (ArduPilotTelemetry, ArduPilotActuator, DepthScanSensor,
                                   RealSenseDepthSource)
        from .vehicle_class import get_class
        from .team_link import TeamLink
    except ImportError:  # device flat-install path
        from onboard_l5 import OnboardL5Runtime, StaticGoalAllocator, TeamLinkPeers
        from team_alloc_onboard import ImplicitTeamAllocator, frame_from_home, roster_from_briefing
        from ardupilot_l5 import (ArduPilotTelemetry, ArduPilotActuator, DepthScanSensor,
                                  RealSenseDepthSource)
        from vehicle_class import get_class
        from team_link import TeamLink

    vc = get_class(vehicle_type)
    if mission.allocation == "team":
        jobs, job_alt = mission.team_jobs(aerial=vc.is_aerial)
        roster = roster_from_briefing(mission.roster)
        if drone_id not in roster:
            raise MissionError(f"{drone_id} is not on the mission roster {sorted(roster)}")
        home = next(e["home"] for e in mission.roster if str(e["id"]) == drone_id)
        goals = [[x, y, job_alt[j]] for j, (x, y) in sorted(jobs.items())]
    else:
        goals = mission.goals_for(drone_id, aerial=vc.is_aerial)
    cleanups = []
    try:
        st = str(mission.sensor.get("type", "d435i")).lower()
        source = None
        if st == "d435i":
            source = RealSenseDepthSource(serial=mission.sensor.get("serial")).start()
            cleanups.append(source.stop)
        telemetry = ArduPilotTelemetry(sdk=sdk)
        if source is not None:
            sensor = DepthScanSensor(
                vc.name, source, mount_pitch_deg=float(mission.sensor.get("mount_pitch_deg", 0.0)),
                mount_height_m=mission.sensor.get("mount_height_m"),
                tilt_fn=telemetry.tilt, altitude_fn=telemetry.altitude_m if vc.is_aerial else None)
            telemetry.sensor_health = sensor.healthy
        else:
            logger.warning("AUTONOMY FALLBACK: sensor.type none - flying with NO obstacle sensing")
            sensor = _BlindClearSensor(vc)
        if telemetry.resolve_origin() is None:
            raise RuntimeError("cannot place the takeoff point in the EKF frame "
                               "(no position fix, or no takeoff recorded)")
        telemetry.pose()   # fail here, before flying, rather than on the first tick
        if actuator is None:
            actuator = ArduPilotActuator(vc.name, sdk=sdk)
        actuator.altitude_fn = telemetry.altitude_m
        if mission.region.max_alt_m is not None:
            actuator.max_alt_m = min(actuator.max_alt_m, float(mission.region.max_alt_m))
        link = TeamLink.from_config(drone_id, team_link_cfg,
                                    base_dir=None if base_dir is None else str(base_dir))
        link.start()
        cleanups.append(link.stop)
        if mission.allocation == "team":
            to_mission, from_mission = frame_from_home(home)
            allocator = ImplicitTeamAllocator(drone_id, roster, jobs, to_mission=to_mission,
                                              from_mission=from_mission, job_alt=job_alt)
        else:
            allocator = StaticGoalAllocator(goals)
        runtime = OnboardL5Runtime(
            drone_id, vc.name, None, telemetry=telemetry, sensor=sensor,
            peers=TeamLinkPeers(link, mission.roster_vclass()),
            actuator=ScaledActuator(actuator, telemetry), allocator=allocator, team_link=link)
    except Exception:
        for c in reversed(cleanups):
            try:
                c()
            except Exception:
                pass
        raise

    def cleanup():
        for c in reversed(cleanups):
            c()

    return FallbackRun(runtime, allocator, telemetry, runtime.actuator, mission.region, sdk,
                       aerial=vc.is_aerial, on_complete=mission.on_complete,
                       control_hz=cfg.control_hz, max_duration_s=cfg.max_duration_s,
                       goals=goals, cleanup=cleanup)


class _BlindClearSensor:
    """sensor.type none: an all-clear scan of the right length. Explicitly opted into."""

    def __init__(self, vc):
        try:
            from .vehicle_class import ray_table
        except ImportError:  # pragma: no cover - device flat-install path
            from vehicle_class import ray_table
        self._scan = np.full(ray_table(vc.sensor).n_rays, vc.sense_range_m, dtype=np.float32)

    def scan(self):
        return self._scan.copy()


# --------------------------------------------------------------------------- manager
class FallbackManager:
    """The daemon's side: watches the control plane, engages, hands back.

    The daemon supplies its own mission bookkeeping as callables so the one-mission-
    at-a-time rule stays the daemon's:
      mission_running() -> bool         is a MissionLoop flying?
      stop_mission()                    stop it and wait (caller holds dispatch_lock)
      register(run, thread)             make the run the daemon's active mission
      unregister(run)                   ...and take it off again when it ends
      sdk() -> drone_sdk module
    ``build(sdk, actuator)`` -> FallbackRun is injectable for tests.
    """

    RETRY_S = 5.0   # after a declined engage (not armed / not GUIDED), look again this much later

    def __init__(self, cfg: FallbackConfig, drone_id: str, vehicle_type: str, *,
                 team_link_cfg=None, base_dir=None,
                 mission_running: Callable[[], bool], stop_mission: Callable[[], None],
                 register: Callable, unregister: Callable, sdk: Callable,
                 dispatch_lock, build: Optional[Callable] = None,
                 make_actuator: Optional[Callable] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.drone_id, self.vehicle_type = drone_id, vehicle_type
        self.mission_running, self.stop_mission = mission_running, stop_mission
        self.register, self.unregister, self.sdk = register, unregister, sdk
        self.dispatch_lock = dispatch_lock
        self.clock = clock
        self.team_link_cfg, self.base_dir = team_link_cfg, base_dir
        self.watch = ControlPlaneWatch(clock)
        self.mission = None
        self._lock = threading.Lock()
        self._engaging = False
        self._handback = False
        self._run: Optional[FallbackRun] = None
        self._thread: Optional[threading.Thread] = None
        self._not_before = 0.0
        self._build = build
        self._make_actuator = make_actuator
        self.last_outcome: Optional[str] = None
        if vehicle_type not in ("quadcopter", "quad", "rover"):
            logger.error(f"AUTONOMY FALLBACK: not supported on {vehicle_type!r}; it will never engage")
            self.supported = False
        else:
            self.supported = True
        try:
            self.mission = PrebriefedMission.load(cfg.mission_file)
            self.mission.goals_for(drone_id, aerial=vehicle_type != "rover")
            logger.info(f"AUTONOMY FALLBACK: armed with {cfg.mission_file} "
                        f"(engages after {cfg.cloud_silence_s:g} s of control-plane silence)")
        except Exception as e:
            # Loud now, at boot, rather than at the moment the link is lost. Engaging
            # with no usable mission is a failure, so it will RTL then.
            logger.error(f"AUTONOMY FALLBACK: mission file unusable: {e}")
            self.mission = None
            self._mission_error = str(e)

    # ------------------------------------------------------------------ evidence
    def note_alive(self) -> None:
        self.watch.note_alive()

    @property
    def engaged(self) -> bool:
        with self._lock:
            return self._engaging or (self._thread is not None and self._thread.is_alive())

    # ------------------------------------------------------------------ trigger
    def tick(self) -> bool:
        """Called once a second by the daemon's main loop. True if it started engaging."""
        if not self.supported or self.engaged:
            return False
        if self.watch.silent_for() < self.cfg.cloud_silence_s:
            return False
        if self.clock() < self._not_before or not self.mission_running():
            return False
        with self._lock:
            self._engaging, self._handback = True, False
        threading.Thread(target=self._engage, name="autonomy-fallback-engage", daemon=True).start()
        return True

    def _decline(self, why: str) -> None:
        logger.info(f"AUTONOMY FALLBACK: control plane silent {self.watch.silent_for():.0f} s, "
                    f"not engaging: {why}")
        self._not_before = self.clock() + self.RETRY_S
        with self._lock:
            self._engaging = False

    def _engage(self) -> None:
        sdk = self.sdk()
        aerial = self.vehicle_type != "rover"
        try:
            if sdk.pilot_control():
                return self._decline("the pilot has control")
            if not sdk.is_armed():
                return self._decline("not armed")
            actuator = self._new_actuator(sdk)
            ok, why = actuator.ready()
            if not ok:
                return self._decline(why)
        except Exception as e:
            return self._decline(f"could not check the vehicle: {e}")

        logger.warning(f"AUTONOMY FALLBACK: control plane silent {self.watch.silent_for():.0f} s "
                       f"mid-mission - taking over with the briefed mission")
        with self.dispatch_lock:
            try:
                self.stop_mission()
                sdk.clear_abort()
                actuator.stop()      # hold while the loop is built
                try:
                    sdk.release_camera()
                except Exception:
                    pass
                if self.mission is None:
                    raise MissionError(getattr(self, "_mission_error", "no mission"))
                build = self._build or (lambda s, a: build_run(
                    s, self.cfg, self.mission, self.drone_id, self.vehicle_type, actuator=a,
                    team_link_cfg=self.team_link_cfg, base_dir=self.base_dir))
                run = build(sdk, actuator)
            except Exception as e:
                logger.exception("AUTONOMY FALLBACK: could not start the onboard loop")
                with self._lock:
                    handed = self._handback
                if handed:
                    # The operator is back and their command may already be flying it.
                    logger.info("AUTONOMY FALLBACK: operator back; leaving the vehicle to them")
                    self.last_outcome = "handed_back"
                else:
                    self.last_outcome = safe_recover(sdk, aerial, f"could not start the onboard loop: {e}")
                with self._lock:
                    self._engaging = False
                return
            with self._lock:
                if self._handback:
                    # An operator command arrived while this was being built: theirs now.
                    self._engaging = False
                    handed = True
                else:
                    handed = False
                    self._run = run
                    self._thread = threading.Thread(target=self._fly, args=(run,),
                                                    name="autonomy-fallback", daemon=True)
                    self.register(run, self._thread)
                    self._thread.start()
                    self._engaging = False
            if handed:
                logger.info("AUTONOMY FALLBACK: operator back before the loop started; not flying it")
                try:
                    if run.cleanup is not None:
                        run.cleanup()
                finally:
                    self.last_outcome = "handed_back"

    def _new_actuator(self, sdk):
        if self._make_actuator is not None:
            return self._make_actuator(sdk)
        try:
            from .ardupilot_l5 import ArduPilotActuator
        except ImportError:  # device flat-install path
            from ardupilot_l5 import ArduPilotActuator
        return ArduPilotActuator(self.vehicle_type, sdk=sdk)

    def _fly(self, run: FallbackRun) -> None:
        try:
            self.last_outcome = run.run()
        finally:
            self.unregister(run)
            with self._lock:
                if self._run is run:
                    self._run = None

    # ------------------------------------------------------------------ hand back
    def handback(self, timeout_s: float = 5.0) -> bool:
        """An operator command arrived: stop flying, give the vehicle back. True if
        the fallback was flying (or about to). Call before the command runs."""
        with self._lock:
            self._handback = True
            run, thread, engaging = self._run, self._thread, self._engaging
        self.watch.note_alive()
        if run is None and not engaging:
            return False
        logger.warning("AUTONOMY FALLBACK: operator command received - handing control back")
        if run is not None:
            run.abort()
        if thread is not None:
            thread.join(timeout_s)
            if thread.is_alive():
                logger.error(f"AUTONOMY FALLBACK: loop still running {timeout_s:.0f} s after hand-back")
        return True

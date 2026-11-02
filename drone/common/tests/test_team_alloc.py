"""team_alloc (the shared implicit-coordination core) and team_alloc_onboard
(its onboard_l5.TeamAllocator adapter).

The closed-loop tests fly N point-mass vehicles at their briefed speed over a
fake broadcast link with injectable loss, blackout, partitions and deaths,
and check the claims CLAIMS_ENVELOPE.md makes about the protocol itself:
no two live vehicles ever head for the same open job while their snapshots
agree, every job gets done while any vehicle survives, and plans are a pure
function of their inputs.

Run: cd coybot && .venv/bin/python -m pytest eco/drone/common/tests/test_team_alloc.py -v
"""
import math
import random
import types

import pytest

from eco.drone.common.team_alloc import (
    PlanAgent, RosterEntry, TeamAllocCore, _plan_routes_uncached, plan_routes,
)
from eco.drone.common.team_alloc_onboard import MSG_TYPE, ImplicitTeamAllocator, roster_from_briefing


# --------------------------------------------------------------------------- planner

def _agents(n, seed=0):
    rng = random.Random(seed)
    return [PlanAgent(f"a{i}", (rng.uniform(-50, 50), rng.uniform(-50, 50)), 0.0,
                      rng.choice([1.5, 3.0]), None, 2.0) for i in range(n)]


def _jobs(n, seed=1):
    rng = random.Random(seed)
    return {f"j{i}": (rng.uniform(-100, 100), rng.uniform(-100, 100)) for i in range(n)}


def test_plan_is_a_pure_function_of_its_inputs():
    agents, jobs = _agents(4), _jobs(9)
    a = _plan_routes_uncached(agents, jobs, iters=200, restarts=10, svc={})
    b = _plan_routes_uncached(list(reversed(agents)), dict(reversed(list(jobs.items()))),
                              iters=200, restarts=10, svc={})
    assert a == b


def test_plan_assigns_every_job_exactly_once():
    routes, finish = plan_routes(_agents(5), _jobs(12))
    flat = [t for r in routes.values() for t in r]
    assert sorted(flat) == sorted(_jobs(12))
    for aid, r in routes.items():
        assert len(finish[aid]) == len(r)
        assert finish[aid] == sorted(finish[aid])


def test_plan_respects_eligibility_and_service_time():
    agents = [PlanAgent("fast", (0, 0), 0.0, 10.0, None, 0.0, "fixedwing"),
              PlanAgent("slow", (0, 0), 0.0, 1.0, None, 0.0, "rover")]
    jobs = {"confirm": (10, 0), "cell": (20, 0)}
    svc = {"confirm": {"rover": 0.0}, "cell": {"fixedwing": 5.0, "rover": 500.0}}
    routes, finish = plan_routes(agents, jobs, service=svc)
    assert routes["slow"] == ["confirm"]       # only the rover may confirm
    assert routes["fast"] == ["cell"]
    assert finish["fast"][0] == pytest.approx(2.0 + 5.0)


def test_plan_beats_greedy_on_a_known_trap():
    # Greedy hands both near jobs to the agent between them and leaves the far one
    # to whoever is left; the makespan-optimal split sends one agent each way.
    agents = [PlanAgent("a", (0, 0), 0.0, 1.0), PlanAgent("b", (100, 0), 0.0, 1.0)]
    jobs = {"j0": (10, 0), "j1": (-10, 0), "j2": (90, 0)}
    _, finish = plan_routes(agents, jobs)
    assert max(f[-1] for f in finish.values() if f) <= 30.0 + 1e-6


# --------------------------------------------------------------------------- closed loop

def _roster(n_quads=3, n_rovers=1, spread=30.0):
    r = {}
    for i in range(n_quads):
        r[f"quad_{i}"] = RosterEntry("quadcopter", 3.0, 2.0, 250.0, False, (i * spread, 0.0), 1.0)
    for i in range(n_rovers):
        r[f"rover_{i}"] = RosterEntry("rover", 1.5, 2.0, 120.0, True, (0.0, -i * spread - 20), 1.0)
    return r


class _Bus:
    """Fake broadcast link: delivers next tick, with per-message loss, a
    partition function and a blackout window."""

    def __init__(self, loss=0.0, seed=0, partition=None, blackout=None):
        self.rng = random.Random(seed)
        self.loss = loss
        self.partition = partition or (lambda a, b, t: False)
        self.blackout = blackout or (lambda t: False)
        self.pending = []

    def send(self, sender, msg, t):
        self.pending.append((sender, msg, t))

    def deliver(self, alive, t):
        out = {aid: [] for aid in alive}
        for sender, msg, ts in self.pending:
            if sender not in alive or self.blackout(ts):
                continue
            for r in alive:
                if r == sender or self.partition(sender, r, ts) or self.rng.random() < self.loss:
                    continue
                out[r].append(dict(msg, sender=sender, stamp=ts))
        self.pending = []
        return out


def _fly(roster, jobs, *, bus, kill=None, t_end=400.0, dt=0.1):
    """Point-mass vehicles at briefed speed. Returns (done_by, dup_agent_s, t_done)."""
    clock = {"t": 0.0}
    alloc = {aid: ImplicitTeamAllocator(aid, roster, jobs, clock=lambda: clock["t"]) for aid in roster}
    pos = {aid: list(r.home) for aid, r in roster.items()}
    alive = set(roster)
    done_by, inbox = {}, {aid: [] for aid in roster}
    dup_s = 0.0
    while clock["t"] < t_end:
        t = clock["t"]
        if kill and t >= kill[1] and kill[0] in alive:
            alive.discard(kill[0])
        heading = {}
        for aid in sorted(alive):
            me = types.SimpleNamespace(pos=[pos[aid][0], pos[aid][1], 5.0])
            goal, out = alloc[aid].step(t, me, {}, inbox[aid])
            for m in out:
                bus.send(aid, m, t)
            c = alloc[aid].committed
            if c is not None and c not in done_by:
                heading.setdefault(c, []).append(aid)
            if goal is not None:
                dx, dy = goal[0] - pos[aid][0], goal[1] - pos[aid][1]
                d = math.hypot(dx, dy)
                step = min(d, roster[aid].speed * dt)
                if d > 0:
                    pos[aid][0] += dx / d * step
                    pos[aid][1] += dy / d * step
            for jid, jp in jobs.items():   # referee: who got there first
                if jid not in done_by and math.dist(pos[aid], jp) <= roster[aid].reach:
                    done_by[jid] = (aid, t)
        dup_s += sum(len(v) - 1 for v in heading.values() if len(v) > 1) * dt
        inbox = bus.deliver(alive, t)
        for aid in roster:
            inbox.setdefault(aid, [])
        clock["t"] = round(t + dt, 6)
        if len(done_by) == len(jobs):
            break
    return done_by, dup_s, clock["t"]


JOBS = {f"j{i}": (x, y) for i, (x, y) in enumerate(
    [(40, 60), (-30, 50), (80, -20), (10, -70), (-60, -10), (100, 40), (55, 5), (-20, -40)])}


def test_full_comms_every_job_done_no_duplicates():
    done_by, dup, _ = _fly(_roster(), JOBS, bus=_Bus())
    assert set(done_by) == set(JOBS)
    assert dup == 0.0


def test_blackout_from_takeoff_no_duplicates_and_everything_done():
    done_by, dup, _ = _fly(_roster(), JOBS, bus=_Bus(blackout=lambda t: True))
    assert set(done_by) == set(JOBS)
    assert dup == 0.0


def test_lossy_comms_bounded_duplication():
    done_by, dup, _ = _fly(_roster(), JOBS, bus=_Bus(loss=0.5, seed=3))
    assert set(done_by) == set(JOBS)
    assert dup <= 2.0


def test_partition_mid_mission_no_duplicates():
    side = {"quad_0": 0, "quad_1": 0, "quad_2": 1, "rover_0": 1}
    done_by, dup, _ = _fly(_roster(), JOBS,
                           bus=_Bus(partition=lambda a, b, t: t > 10 and side[a] != side[b]))
    assert set(done_by) == set(JOBS)
    assert dup == 0.0


@pytest.mark.parametrize("blackout", [False, True])
def test_dead_vehicle_jobs_are_taken_over(blackout):
    bus = _Bus(blackout=(lambda t: True) if blackout else None)
    done_by, _, t_done = _fly(_roster(), JOBS, bus=bus, kill=("quad_1", 5.0), t_end=600.0)
    assert set(done_by) == set(JOBS), "a job was orphaned by the dead vehicle"
    assert all(who != "quad_1" or t <= 5.0 for who, t in done_by.values())


def test_dead_vehicle_is_presumed_lost_when_it_should_be_in_range():
    roster = _roster()
    clock = {"t": 0.0}
    alloc = {aid: ImplicitTeamAllocator(aid, roster, JOBS, clock=lambda: clock["t"]) for aid in roster}
    bus = _Bus()
    inbox = {aid: [] for aid in roster}
    alive = set(roster)
    for k in range(200):
        t = k * 0.1
        clock["t"] = t
        if t >= 5.0:
            alive.discard("quad_1")
        for aid in sorted(alive):
            me = types.SimpleNamespace(pos=[*roster[aid].home, 5.0])
            _, out = alloc[aid].step(t, me, {}, inbox.get(aid, []))
            for m in out:
                bus.send(aid, m, t)
        inbox = bus.deliver(alive, t)
    assert "quad_1" in alloc["quad_0"].core.lost
    assert "quad_1" in alloc["quad_2"].core.lost


def test_state_message_fits_the_team_link_frame():
    import json
    roster = _roster()
    a = ImplicitTeamAllocator("quad_0", roster, {f"job{i:02d}": (i, i) for i in range(20)},
                              clock=lambda: 0.0)
    a.core.done = {f"job{i:02d}" for i in range(20)}
    _, out = a.step(0.0, types.SimpleNamespace(pos=[0.0, 0.0, 5.0]), {}, [])
    assert out and out[0]["type"] == MSG_TYPE
    assert len(json.dumps(out[0], separators=(",", ":"))) < 400   # 500-byte frame incl. header


def test_unresolved_frame_holds():
    roster = _roster()
    a = ImplicitTeamAllocator("quad_0", roster, JOBS, clock=lambda: 0.0, frame_ok=lambda: False)
    goal, out = a.step(0.0, types.SimpleNamespace(pos=[0.0, 0.0, 5.0]), {}, [])
    assert goal is None and out == []


def test_malformed_state_is_dropped_not_fatal():
    roster = _roster()
    a = ImplicitTeamAllocator("quad_0", roster, JOBS, clock=lambda: 0.0)
    bad = {"type": MSG_TYPE, "sender": "quad_1", "epoch_t": "x"}
    goal, _ = a.step(0.0, types.SimpleNamespace(pos=[0.0, 0.0, 5.0]), {}, [bad])
    assert goal is not None


def test_roster_from_briefing_defaults():
    r = roster_from_briefing([{"id": "q", "vclass": "quad", "home": [1, 2]},
                              {"id": "r", "vclass": "rover", "reach_m": 1.0}])
    assert r["q"].kind == "quadcopter" and r["q"].home == (1.0, 2.0) and r["q"].eff == 1.0
    assert r["r"].reach == 1.0 and r["r"].los_required and r["r"].eff == 0.7


def test_two_cores_with_identical_snapshots_compute_identical_plans():
    roster = _roster()
    cores = [TeamAllocCore(aid, roster, JOBS, set(roster)) for aid in sorted(roster)]
    for c in cores:
        c.maybe_plan(0.0, roster[c.me].home, periodic=True)
    assert all(c.routes == cores[0].routes for c in cores)


# --------------------------------------------------------------------------- fallback wiring

def test_done_is_a_bool_true_only_when_nothing_is_left():
    roster = _roster(1, 0)
    clock = {"t": 0.0}
    a = ImplicitTeamAllocator("quad_0", roster, {"j0": (1.0, 0.0)}, clock=lambda: clock["t"],
                              idle_done_s=10.0)
    assert a.done is False
    a.step(0.0, types.SimpleNamespace(pos=[0.0, 0.0, 5.0]), {}, [])
    a.step(0.1, types.SimpleNamespace(pos=[1.0, 0.0, 5.0]), {}, [])
    assert a.done is True


def test_idle_vehicle_is_done_after_idle_window():
    roster = _roster(2, 0)
    clock = {"t": 0.0}
    a = ImplicitTeamAllocator("quad_0", roster, {"j0": (200.0, 0.0)}, clock=lambda: clock["t"],
                              idle_done_s=10.0)   # j0 is quad_1's; quad_0 never hears quad_1
    for k in range(150):
        clock["t"] = k * 0.1
        a.step(clock["t"], types.SimpleNamespace(pos=[0.0, 0.0, 5.0]), {}, [])
    assert a.committed is None and a.done is True


def test_frame_from_home_roundtrip_and_rotation():
    from eco.drone.common.team_alloc_onboard import frame_from_home
    to_m, from_m = frame_from_home([10.0, 5.0, 90.0])
    assert to_m((1.0, 0.0)) == pytest.approx((10.0, 6.0))
    assert from_m(to_m((3.0, -2.0))) == pytest.approx((3.0, -2.0))


def test_team_mission_parsing():
    from eco.drone.common.autonomy_fallback import MissionError, PrebriefedMission
    base = {"region": {"min": [-50, -50], "max": [50, 50]}, "tasks": [[10, 0, 5], [0, 10, 5]],
            "roster": [{"id": "a", "vclass": "quad", "home": [0, 0]},
                       {"id": "b", "vclass": "quad", "home": [5, 0, 0]}],
            "allocation": "team"}
    m = PrebriefedMission.from_dict(base)
    jobs, alt = m.team_jobs()
    assert jobs == {"j0": (10.0, 0.0), "j1": (0.0, 10.0)} and alt == {"j0": 5.0, "j1": 5.0}
    bad = dict(base, roster=[{"id": "a", "vclass": "quad"}])
    with pytest.raises(MissionError):
        PrebriefedMission.from_dict(bad)
    with pytest.raises(MissionError):
        PrebriefedMission.from_dict(dict(base, tasks={"a": [[1, 1, 1]]}))

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


def test_partition_from_takeoff_no_duplicates():
    # The halves never hear each other: each must reserve the other's share of
    # the briefing plan, not re-plan it from briefing positions.
    side = {"quad_0": 0, "quad_1": 1, "quad_2": 0, "rover_0": 1}
    done_by, dup, _ = _fly(_roster(), JOBS, bus=_Bus(partition=lambda a, b, t: side[a] != side[b]),
                           t_end=600.0)
    assert set(done_by) == set(JOBS)
    assert dup == 0.0


def test_one_way_link_no_duplicates():
    # rover_0 hears everyone; nobody hears rover_0 (it transmits less far).
    done_by, dup, _ = _fly(_roster(), JOBS, bus=_Bus(partition=lambda a, b, t: a == "rover_0"),
                           t_end=600.0)
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


# --------------------------------------------------------------------------- split teams

def _search_team():
    """Two fixed-wings that sweep but can't confirm (relay-dependent), three
    quads that can; six cells with per-class sweep times."""
    roster = {f"fw_{i}": RosterEntry("fixedwing", 25.0, 0.0, 500.0, False, (-200.0, i * 40.0), 1.0)
              for i in range(2)}
    roster.update({f"quad_{i}": RosterEntry("quadcopter", 3.0, 12.0, 250.0, False,
                                            (i * 30.0, 0.0), 1.0) for i in range(3)})
    cells = {f"s{i}": (i * 100.0 - 250.0, 50.0) for i in range(6)}
    svc = {cid: {"fixedwing": 8.0, "quadcopter": 190.0} for cid in cells}
    return roster, cells, svc


def _cores(roster, jobs, svc, relay):
    return {aid: TeamAllocCore(aid, roster, jobs, set(roster), svc=svc, relay_dependent=relay)
            for aid in sorted(roster)}


def _epochs(cores, t0, t1, deliver):
    """Run every core's epoch loop from t0 to t1 (0.5 s epochs); `deliver(s, r, t)`
    decides whether r hears s's STATE sent at t."""
    t = t0
    while t <= t1 + 1e-9:
        out = {}
        for aid, c in cores.items():
            c.maybe_plan(t, tuple(c.entries[aid].pos), periodic=round(t / 0.5) % 10 == 0)
            c.advance()
            out[aid] = c.state_payload(t, tuple(c.entries[aid].pos))
        for s_, p in out.items():
            for r, c in cores.items():
                if r != s_ and deliver(s_, r, t):
                    c.ingest(s_, p, t, t)
        t = round(t + 0.5, 6)


def test_blackout_handoff_splits_a_relay_dependent_peers_jobs_identically():
    roster, cells, svc = _search_team()
    relay = {"fw_0", "fw_1"}
    cores = _cores(roster, cells, svc, relay)
    _epochs(cores, 0.0, 0.0, lambda s, r, t: False)        # the shared t=0 plan
    fw_jobs = set(cores["quad_0"].routes["fw_0"]) | set(cores["quad_0"].routes["fw_1"])
    assert fw_jobs, "the all-fresh plan should give the fixed-wings the cells"
    _epochs(cores, 0.5, 8.0, lambda s, r, t: False)        # total blackout
    quads = [cores[q] for q in ("quad_0", "quad_1", "quad_2")]
    assert all(q.handoff == quads[0].handoff for q in quads), "handoff splits disagree"
    shares = [tid for split in quads[0].handoff.values() for route, _ in split.values() for tid in route]
    assert sorted(shares) == sorted(fw_jobs), "every fixed-wing cell handed to exactly one quad"
    assert cores["fw_0"].muted and cores["fw_0"].committed is None


def test_isolated_confirmers_resplit_all_work_identically():
    roster, cells, svc = _search_team()
    cores = _cores(roster, cells, svc, {"fw_0", "fw_1"})
    _epochs(cores, 0.0, 8.0, lambda s, r, t: False)        # blackout from takeoff
    quads = [cores[q] for q in ("quad_0", "quad_1", "quad_2")]
    assert quads[0].blind and all(q.blind == quads[0].blind for q in quads), "blind splits disagree"
    shares = [tid for route, _ in quads[0].blind.values() for tid in route]
    held = {q.me: {q.committed} | set(q.routes.get(q.me, [])) for q in quads}
    assert len(shares) == len(set(shares)), "a cell in two shares"
    for q in quads:                                         # nobody is handed what another is flying
        assert q.committed in quads[0].blind.get(q.me, ([None], []))[0], (q.me, q.committed)
    assert set(shares) | {q.committed for q in quads} >= set(cells), "a cell in nobody's share"
    for q in quads:                                         # each flies only its own share
        own = set(quads[0].blind.get(q.me, ([], []))[0]) | {q.committed}
        assert held[q.me] - {None} <= own, (q.me, held[q.me], own)


def test_no_blind_split_for_a_confirmer_that_still_hears_a_sub_team():
    roster, cells, svc = _search_team()
    cores = _cores(roster, cells, svc, {"fw_0", "fw_1"})
    _epochs(cores, 0.0, 3.0, lambda s, r, t: True)
    pair = {"quad_0", "quad_1"}
    _epochs(cores, 3.5, 15.0, lambda s, r, t: {s, r} <= pair)   # split; quad_2 alone
    assert cores["quad_0"].blind is None and cores["quad_1"].blind is None
    _epochs(cores, 15.5, 30.0, lambda s, r, t: False)           # then the pair loses its link too
    assert cores["quad_0"].blind is None, "it heard the pair after the shared plan: not a team-wide blackout"


def test_blind_split_never_hands_out_a_cell_someone_is_flying():
    # quad_a is flying A (its route head) although quad_b, which ends next to
    # A, would get A in a fresh split -- the failure seen on scene 9008.
    roster = {"fw": RosterEntry("fixedwing", 25.0, 0.0, 500.0, False, (-500.0, 0.0), 1.0),
              "quad_a": RosterEntry("quadcopter", 3.0, 12.0, 250.0, False, (0.0, 0.0), 1.0),
              "quad_b": RosterEntry("quadcopter", 3.0, 12.0, 250.0, False, (290.0, 0.0), 1.0)}
    cells = {"A": (300.0, 0.0), "B": (280.0, 0.0), "C": (0.0, 10.0)}
    svc = {cid: {"fixedwing": 8.0, "quadcopter": 10.0} for cid in cells}
    core = TeamAllocCore("quad_b", roster, cells, set(roster), svc=svc, relay_dependent={"fw"})
    agents = {aid: PlanAgent(aid, r.home, 0.0, r.speed, None, r.reach, r.kind) for aid, r in roster.items()}
    core.consistent = (agents, {"fw": ["C"], "quad_a": ["A"], "quad_b": ["B"]}, {}, frozenset())
    split = core._split_blind(cells)
    assert split["quad_a"][0][0] == "A" and split["quad_b"][0][0] == "B", split
    assert sorted(t for route, _ in split.values() for t in route) == ["A", "B", "C"], split


def test_relay_dependent_agent_keeps_working_while_anyone_hears_it():
    roster, cells, svc = _search_team()
    cores = _cores(roster, cells, svc, {"fw_0", "fw_1"})
    # fw_0 hears only fw_1 (out of the quads' range), and fw_1 relays to the quads.
    def link(s, r, t):
        return not ({s, r} & {"fw_0"}) or {s, r} == {"fw_0", "fw_1"}
    _epochs(cores, 0.0, 10.0, link)
    assert not cores["fw_0"].muted
    assert "fw_0" not in cores["quad_0"].handoff, "fw_0 is reachable through fw_1"


def test_a_sweeper_back_from_mute_leaves_cells_the_far_side_has_opened():
    # Scene 9002 seed 1, partition: fw_0 was muted on its own side, so it
    # did not sweep; the far side opened its cells to a quad of its own on
    # the shared plan's timing, and fw_0, heard again by its side, flew one
    # of them anyway.
    roster, cells, svc = _search_team()
    cores = _cores(roster, cells, svc, {"fw_0", "fw_1"})
    side = {"fw_0": 0, "quad_0": 0, "quad_1": 0, "fw_1": 1, "quad_2": 1}
    _epochs(cores, 0.0, 0.0, lambda s, r, t: True)
    _epochs(cores, 0.5, 150.0, lambda s, r, t: side[s] == side[r] and "fw_0" not in (s, r))
    split = cores["quad_2"].handoff.get("fw_0", {})
    far_cells = {tid for c, (route, _) in split.items() if side[c] == 1 for tid in route}
    assert far_cells, "the far side should have handed some of fw_0's cells to its own quads"
    _epochs(cores, 150.5, 160.0, lambda s, r, t: side[s] == side[r])   # fw_0 back with its side
    for aid in ("fw_0", "quad_0", "quad_1"):
        c = cores[aid]
        assert not c.muted
        mine = {c.committed} | set(c.routes.get(aid, []))
        assert not mine & far_cells, (aid, mine & far_cells)


def test_sighting_during_a_split_goes_to_one_owner():
    roster = _roster()
    cores = {aid: TeamAllocCore(aid, roster, JOBS, set(roster)) for aid in sorted(roster)}
    side = {"quad_0": 0, "quad_1": 0, "quad_2": 1, "rover_0": 1}
    _epochs(cores, 0.0, 0.0, lambda s, r, t: True)
    _epochs(cores, 0.5, 6.0, lambda s, r, t: side[s] == side[r])
    for c in cores.values():                               # both halves hear of it
        c.add_sighting("c:new", (5.0, 5.0))
        c.entries[c.me] = c.entries[c.me].__class__(**{**c.entries[c.me].__dict__,
                                                       "sightings": frozenset({("c:new", 5.0, 5.0)})})
        c.dirty = True
    # Settled state (presumed-lost verdicts take an epoch or two to gossip
    # around a sub-team; nobody commits to the sighting meanwhile).
    _epochs(cores, 6.5, 12.0, lambda s, r, t: side[s] == side[r])
    takers = {aid for aid, c in cores.items()
              if c.committed == "c:new" or "c:new" in c.routes.get(aid, [])}
    assert len(takers) == 1, takers


def test_sightings_reserved_to_one_stale_owner_are_due_one_after_another():
    # Scene 9010: the far side predicted each of a rover's five new confirm
    # jobs as its first, released the later ones as overdue, and sent its own
    # rover to a job the owner was about to reach.
    roster = _roster()
    cores = {aid: TeamAllocCore(aid, roster, JOBS, set(roster)) for aid in sorted(roster)}
    side = {"quad_0": 0, "quad_1": 0, "quad_2": 1, "rover_0": 1}
    _epochs(cores, 0.0, 0.0, lambda s, r, t: True)
    _epochs(cores, 0.5, 4.0, lambda s, r, t: side[s] == side[r])   # stale, not yet presumed lost
    new = {"c:a": (-10.0, -100.0), "c:b": (30.0, -100.0)}   # equally far past quad_2's last job
    for c in cores.values():
        for tid, xy in new.items():
            c.add_sighting(tid, xy)
        c.dirty = True
    _epochs(cores, 4.5, 5.0, lambda s, r, t: side[s] == side[r])
    checked = 0
    for c in cores.values():
        for owner, (route, fin, _) in c.reserved.items():
            if "c:a" in route and "c:b" in route:
                i, j = sorted((route.index("c:a"), route.index("c:b")))
                gap = fin[j] - fin[i]
                assert gap >= 0.9 * 40.0 / roster[owner].speed, (c.me, owner, route, fin)
                checked += 1
    assert checked, "both sightings should be reserved to one owner on the far side"


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

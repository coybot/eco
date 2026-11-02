"""Implicit-coordination team allocator -- the decision logic shared, byte for
byte, by the simulator (`ecobridge/implicit_coord.py`, where it was validated
against every condition in CLAIMS_ENVELOPE.md) and the vehicle
(`team_alloc_onboard.py`, behind `onboard_l5.TeamAllocator`).

Stdlib only and Python 3.8 compatible, so it installs flat into
`~/drone-api/` like the rest of `drone/common/`.

How it works (full rationale in ecobridge/implicit_coord.py's docstring):
every agent runs the SAME deterministic planner on the SAME inputs -- the
briefing plus the STATE messages everyone broadcast on the last shared epoch,
its own entry being what it SENT, not where it is -- so with full delivery
they compute identical plans and never duplicate a job, without negotiating.
A plan is only recomputed when its inputs change; a peer unheard for STALE_S
has its route reserved (it is presumably still flying it), so sub-teams and
isolated agents keep disjoint work; a reserved job overdue by
RELEASE_FACTOR/RELEASE_SLACK_S is offered to the non-owners in ETA order,
staggered, so no job is orphaned while a capable agent survives; a peer
silent although it should be in link range is presumed lost (gossiped), which
only brings its jobs' release forward to their planned finish times.

`TeamAllocCore` holds one agent's view and does everything except sensing and
flying: the caller feeds it decoded STATE payloads, its own completions and
sightings, its believed position and measured progress, and asks it each
epoch for the payload to broadcast and for the job it is committed to.
"""
from __future__ import annotations

import functools
import math
import random
from dataclasses import dataclass
from typing import Optional

EPOCH_TICKS = 5            # one STATE broadcast + one plan per 0.5 s at dt=0.1
STALE_S = 3.0              # = CLAIM_STALE_S; peer unheard this long -> route reserved
RELEASE_FACTOR = 1.5       # a reserved job is overdue at 1.5x its planned ETA ...
RELEASE_SLACK_S = 10.0     # ... plus this, before anyone else takes it
LOCAL_SEARCH_ITERS = 200   # max improving moves per local search
PLAN_RESTARTS = 40         # extra random-assignment starts per plan
PERIODIC_REPLAN_EPOCHS = 10  # unconditional replan every 5 s, on the same tick everywhere
LOST_S = 6.0               # a silent peer that should be in range is presumed dead after this
LOST_RANGE_FRACTION = 0.6  # "should be in range": predicted distance under 0.6x link range
LOST_RELEASE_SLACK_S = 2.0  # a presumed-lost peer's job is released at its planned finish + this
RELEASE_STAGGER_S = 20.0   # candidate k (by ETA) may take an overdue job k x this after release
STALL_S = 15.0             # no 1 m of progress on the committed job for this long -> give it up
STALL_PROGRESS_M = 1.0
EFF_TAU_S = 10.0           # time constant of each agent's measured progress efficiency
# Briefing default for progress efficiency (achieved speed toward the job /
# max_speed): what each class actually manages through clutter with the
# reactive controller. Calibrated on the swarm_hetero VAL pool, never the
# holdout -- see scripts/calibrate_progress_eff.py and CLAIMS_ENVELOPE.md.
BRIEFING_EFF = {"quadcopter": 1.0, "rover": 0.7}   # val pool, 30 scenes: means 0.997 / 0.724


# ----------------------------------------------------------------------------- planner

@dataclass(frozen=True)
class PlanAgent:
    """One agent as the planner sees it. `t_free` is the absolute time it
    finishes its committed job (or the snapshot time if it has none), and
    `pos` is where it will be then."""
    id: str
    pos: tuple
    t_free: float
    speed: float
    committed: Optional[str] = None
    reach: float = 0.0     # a job is serviced on entering this radius (detect radius)
    kind: str = ""         # vehicle class name, looked up in a plan's service table
    cant: frozenset = frozenset()   # jobs this agent reported it cannot do (abandoned)


def _leg(a: tuple, b: tuple, speed: float) -> float:
    return math.dist(a, b) / speed


def _reach_leg(a: tuple, b: tuple, speed: float, reach: float) -> tuple:
    """(travel time, arrival point) to get within `reach` of `b` from `a` in a
    straight line -- the mission services a job on entering the agent's
    detect radius, not on reaching its centre, so a quad's 12 m radius is
    worth 4 s per leg at 3 m/s."""
    d = math.dist(a, b)
    if d <= reach:
        return 0.0, a
    f = (d - reach) / d
    return (d - reach) / speed, (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


def _service(svc: dict, agent: PlanAgent, tid: str):
    """Seconds `agent` spends servicing `tid` once there, or None if its class
    can't do that job at all. Jobs absent from the table are instant visits
    open to every class (the known-location job contract)."""
    if tid in agent.cant:
        return None
    table = svc.get(tid)
    if table is None:
        return 0.0
    return table.get(agent.kind)


def _step(agent: PlanAgent, pos: tuple, tid: str, jobs: dict, svc: dict):
    """(elapsed, end position) for one route entry, or None if ineligible.
    A serviced job (a search cell) is entered at its centre's reach radius
    and left from its centre -- a deliberate simplification of the sweep."""
    s = _service(svc, agent, tid)
    if s is None:
        return None
    dt, arrive = _reach_leg(pos, jobs[tid], agent.speed, agent.reach if s == 0.0 else 0.0)
    return dt + s, (arrive if s == 0.0 else jobs[tid])


def _route_times(agent: PlanAgent, route: list, jobs: dict, svc: dict = None) -> list:
    """Absolute finish time of each job on `route`, starting from `agent`'s
    free state (inf from the first job its class can't service). The
    committed job, if any, is already accounted in t_free."""
    svc = svc or {}
    t, pos, out = agent.t_free, agent.pos, []
    for tid in route:
        st = _step(agent, pos, tid, jobs, svc)
        if st is None:
            t = math.inf
        else:
            t += st[0]
            pos = st[1]
        out.append(t)
    return out


def _greedy(ids: list, by_id: dict, jobs: dict, order: list, svc: dict) -> dict:
    """List scheduling: repeatedly append the job (scanned in `order`) that
    would finish soonest on any agent able to do it. A job no agent here can
    do is left unassigned."""
    routes: dict = {aid: [] for aid in ids}
    cur = {aid: by_id[aid].pos for aid in ids}
    acc = {aid: by_id[aid].t_free for aid in ids}
    remaining = list(order)
    while remaining:
        best = None
        for aid in ids:
            a = by_id[aid]
            for k, tid in enumerate(remaining):
                st = _step(a, cur[aid], tid, jobs, svc)
                if st is None:
                    continue
                key = (acc[aid] + st[0], k, aid)
                if best is None or key < best:
                    best = key
        if best is None:
            break
        fin, k, aid = best
        tid = remaining.pop(k)
        routes[aid].append(tid)
        cur[aid] = _step(by_id[aid], cur[aid], tid, jobs, svc)[1]
        acc[aid] = fin
    return routes


def _local_search(ids: list, by_id: dict, jobs: dict, routes: dict, max_moves: int,
                  svc: dict) -> tuple:
    """First-improvement relocate + swap on (makespan, total finish time),
    re-scoring only the routes a move touches. A move that hands a job to a
    class that can't do it scores inf and is never taken."""
    def score(aid, route):
        ts = _route_times(by_id[aid], route, jobs, svc)
        return (ts[-1] if ts else by_id[aid].t_free, sum(ts))

    sc = {aid: score(aid, routes[aid]) for aid in ids}

    def total(sc_):
        return (round(max(v[0] for v in sc_.values()), 9), round(sum(v[1] for v in sc_.values()), 9))

    best = total(sc)
    for _ in range(max_moves):
        found = None
        for src in ids:
            for i in range(len(routes[src])):
                for dst in ids:
                    if dst == src:
                        for j in range(len(routes[src])):
                            if j == i:
                                continue
                            r = list(routes[src])
                            r.insert(j, r.pop(i))
                            trial = dict(sc)
                            trial[src] = score(src, r)
                            if total(trial) < best:
                                found = ({src: r}, trial)
                                break
                    else:
                        for j in range(len(routes[dst]) + 1):
                            rs, rd = list(routes[src]), list(routes[dst])
                            rd.insert(j, rs.pop(i))
                            trial = dict(sc)
                            trial[src], trial[dst] = score(src, rs), score(dst, rd)
                            if total(trial) < best:
                                found = ({src: rs, dst: rd}, trial)
                                break
                        if found is None:
                            for j in range(len(routes[dst])):
                                rs, rd = list(routes[src]), list(routes[dst])
                                rs[i], rd[j] = rd[j], rs[i]
                                trial = dict(sc)
                                trial[src], trial[dst] = score(src, rs), score(dst, rd)
                                if total(trial) < best:
                                    found = ({src: rs, dst: rd}, trial)
                                    break
                    if found:
                        break
                if found:
                    break
            if found:
                break
        if found is None:
            break
        routes = {**routes, **found[0]}
        sc, best = found[1], total(found[1])
    return routes, best


def plan_routes(agents: list, jobs: dict, *, iters: int = LOCAL_SEARCH_ITERS,
                restarts: int = PLAN_RESTARTS, service: dict = None) -> tuple:
    """See _plan_routes_uncached. `service` (job id -> {class name: seconds})
    marks jobs that take time once reached and that only the listed classes
    can do; absent jobs are instant visits for anyone."""
    svc_key = tuple(sorted((tid, tuple(sorted(t.items()))) for tid, t in (service or {}).items()
                           if tid in jobs))
    key = (tuple(sorted(agents, key=lambda a: a.id)), tuple(sorted(jobs.items())), iters, restarts,
           svc_key)
    routes, finish = _plan_routes_cached(key)
    return {k: list(v) for k, v in routes.items()}, {k: list(v) for k, v in finish.items()}


@functools.lru_cache(maxsize=4096)
def _plan_routes_cached(key: tuple) -> tuple:
    """Memoised on the full input -- sound because planning is a pure
    function (see plan_routes). In the sim, agents that agree on their
    inputs share one computation; on a vehicle the cache only ever sees one
    agent's plans."""
    agents, jobs, iters, restarts, svc_key = key
    svc = {tid: dict(t) for tid, t in svc_key}
    routes, finish = _plan_routes_uncached(list(agents), dict(jobs), iters=iters, restarts=restarts,
                                           svc=svc)
    return ({k: tuple(v) for k, v in routes.items()}, {k: tuple(v) for k, v in finish.items()})


def _plan_routes_uncached(agents: list, jobs: dict, *, iters: int, restarts: int,
                          svc: dict) -> tuple:
    """Deterministic min-makespan routing of `jobs` (id -> (x, y)) over
    `agents` (PlanAgent): greedy list scheduling, then relocate/swap local
    search on the makespan (summed finish time as tie-break), repeated from
    `restarts` random job-to-agent assignments drawn from a FIXED-seed RNG;
    the best result wins, ties to the earliest start.

    Same inputs -> same output, bit for bit: every loop runs over sorted ids,
    every comparison has a total tie-break, and the restart RNG is seeded
    from nothing but a constant. That property, not optimality, is what the
    runtime depends on. (Quality, measured on the v2 holdout: see
    CLAIMS_ENVELOPE.md.)

    Returns (routes, finish) -- id -> [job ids] (committed job NOT included)
    and id -> [absolute finish times], one per route entry."""
    ids = sorted(a.id for a in agents)
    by_id = {a.id: a for a in agents}
    if not ids:
        return {}, {}
    order = sorted(jobs)
    best_routes, best_cost = _local_search(ids, by_id, jobs, _greedy(ids, by_id, jobs, order, svc),
                                           iters, svc)
    able = {tid: [aid for aid in ids if _service(svc, by_id[aid], tid) is not None] for tid in order}
    rng = random.Random(0)
    for _ in range(restarts if len(order) > 1 else 0):
        start = {aid: [] for aid in ids}
        for tid in order:
            if able[tid]:
                start[able[tid][rng.randrange(len(able[tid]))]].append(tid)
        for aid in ids:
            rng.shuffle(start[aid])
        routes, cost = _local_search(ids, by_id, jobs, start, iters, svc)
        if cost < best_cost:
            best_routes, best_cost = routes, cost
    finish = {aid: _route_times(by_id[aid], best_routes[aid], jobs, svc) for aid in ids}
    return best_routes, finish


# ----------------------------------------------------------------------------- per-agent state

@dataclass
class Entry:
    """Last STATE heard from (or, for self, sent by) one agent."""
    pos: tuple
    stamp: float
    committed: Optional[str] = None
    done: frozenset = frozenset()
    eff: float = 1.0
    lost: frozenset = frozenset()
    sightings: frozenset = frozenset()   # search mode: {(job id, x, y)} this agent has reported
    cant: frozenset = frozenset()        # jobs this agent gave up on


@dataclass(frozen=True)
class RosterEntry:
    """Briefing facts about one teammate, common knowledge at t=0."""
    kind: str                  # vehicle class name ("quadcopter", "rover", "fixedwing", ...)
    speed: float               # max speed, m/s
    reach: float               # detect radius, m: a visit job is done on entering it
    comms_range_m: float
    los_required: bool = False
    home: tuple = (0.0, 0.0)
    eff: float = 1.0           # briefing progress efficiency (BRIEFING_EFF)


def entry_from_payload(p: dict, t_sent: float) -> Entry:
    return Entry(pos=tuple(p["pos"]), stamp=t_sent, committed=p.get("task"),
                 done=frozenset(p.get("done", ())), eff=float(p.get("eff", 1.0)),
                 lost=frozenset(p.get("lost", ())),
                 sightings=frozenset(tuple(x) for x in p.get("sightings", ())),
                 cant=frozenset(p.get("cant", ())))


class TeamAllocCore:
    """One agent's coordination state. Attribute names are the simulator's
    historical belief fields; callers (and the sim's referee metrics) read
    `committed`, `done`, `entries`, `sightings`, `eff` directly."""

    def __init__(self, me: str, roster: dict, jobs: dict, planners: set, *,
                 svc: dict = None, restarts: int = PLAN_RESTARTS, epoch_s: float = 0.5,
                 metrics: dict = None):
        self.me = me
        self.roster = roster              # id -> RosterEntry
        self.jobs = jobs                  # briefing jobs, id -> (x, y)
        self.planners = planners          # ids that take jobs from the plan
        self.svc = {} if svc is None else svc   # shared, may grow (search sightings)
        self.restarts = restarts
        self.epoch_s = epoch_s
        self.metrics = {} if metrics is None else metrics
        for k in ("plans", "releases", "conflicts_resolved"):
            self.metrics.setdefault(k, 0)
        self.entries: dict = {aid: Entry(pos=tuple(r.home), stamp=0.0, eff=r.eff)
                              for aid, r in roster.items()}
        self.done: set = set()
        self.routes: dict = {}
        self.finish: dict = {}
        self.reserved: dict = {}
        self.released: set = set()
        self.lost: set = set()
        self.cant: set = set()
        self.eff: float = roster[me].eff if me in roster else 1.0
        self.prev_dist = None
        self.stall = None                 # (job id, best distance so far, seconds without progress)
        self.committed: Optional[str] = None
        self.dirty: bool = True
        self.last_delivery_t: float = 0.0
        self.plans: int = 0
        self.sightings: dict = {}

    # -- inputs -----------------------------------------------------------------
    def ingest(self, sender: str, payload: dict, t_sent: float, now: float) -> None:
        """One delivered STATE payload."""
        prev = self.entries.get(sender)
        if prev is None or t_sent >= prev.stamp:
            new = entry_from_payload(payload, t_sent)
            # Position-only updates refresh the snapshot without forcing a
            # replan: the team is already flying the plan. Commitment,
            # done-set or lost-set changes are events every planner must
            # react to -- and so is hearing a peer that had gone stale.
            if prev is None or new.committed != prev.committed or new.done != prev.done \
                    or new.lost != prev.lost or new.sightings != prev.sightings \
                    or new.cant != prev.cant \
                    or t_sent - prev.stamp > STALE_S:
                self.dirty = True
            self.entries[sender] = new
            for cid, x, y in new.sightings:
                self.sightings.setdefault(cid, (x, y))
        new_done = set(payload.get("done", ())) - self.done
        if new_done:
            self.done |= new_done
            self.dirty = True
        self.last_delivery_t = now

    def mark_done(self, tid: str) -> None:
        """My own outcome, or something I can see: used by advance() at once;
        plans only learn it through the entry done-sets."""
        self.done.add(tid)

    def add_sighting(self, cid: str, pos: tuple) -> None:
        self.sightings.setdefault(cid, (pos[0], pos[1]))

    def track_progress(self, my_pos: tuple, dt: float) -> None:
        """EMA of (progress toward the committed job per second) / max_speed --
        how fast this vehicle really closes on jobs through clutter, which the
        planner uses in place of the nominal top speed."""
        if self.committed is None or self.me not in self.planners:
            self.prev_dist = None
            self.stall = None
            return
        d = math.dist(my_pos, self.job_pos(self.committed))
        me = self.roster[self.me]
        if d > me.reach and self.prev_dist is not None and self.prev_dist[0] == self.committed:
            rate = (self.prev_dist[1] - d) / dt / me.speed
            k = dt / EFF_TAU_S
            self.eff = min(1.0, max(0.2, self.eff + k * (rate - self.eff)))
        self.prev_dist = (self.committed, d)
        # Stall: a vehicle pinned against an obstacle (a local minimum of the
        # reactive controller) keeps its commitment forever otherwise. Give the
        # job up and say so (`cant`, in STATE): every planner then routes it to
        # someone else, who may approach from another side.
        if self.stall is None or self.stall[0] != self.committed or d < self.stall[1] - STALL_PROGRESS_M:
            self.stall = (self.committed, d, 0.0)
        else:
            self.stall = (self.stall[0], self.stall[1], self.stall[2] + dt)
            if self.stall[2] >= STALL_S:
                self.cant.add(self.committed)
                self.metrics["stalls"] = self.metrics.get("stalls", 0) + 1
                self.committed = None
                self.stall = None
                self.dirty = True

    # -- outputs ----------------------------------------------------------------
    def state_payload(self, now: float, my_pos: tuple) -> dict:
        """The STATE to broadcast this epoch; also records it as my own entry
        (what the team will plan from), which is the point."""
        eff = round(self.eff * 20) / 20   # 0.05 steps: plan inputs change only on real changes
        lost = frozenset(self.lost).union(*(e.lost for e in self.entries.values()))
        sightings = frozenset((cid, xy[0], xy[1]) for cid, xy in self.sightings.items())
        self.entries[self.me] = Entry(pos=my_pos, stamp=now, committed=self.committed,
                                      done=frozenset(self.done), eff=eff, lost=lost,
                                      sightings=sightings, cant=frozenset(self.cant))
        payload = {"epoch_state": True, "pos": list(my_pos), "task": self.committed,
                   "done": sorted(self.done), "eff": eff, "lost": sorted(lost)}
        if sightings:
            payload["sightings"] = [list(x) for x in sorted(sightings)]
        if self.cant:
            payload["cant"] = sorted(self.cant)
        return payload

    def job_pos(self, tid: str) -> tuple:
        if tid in self.jobs:
            return self.jobs[tid]
        return self.sightings[tid]

    def plan_jobs(self) -> dict:
        """The job list plans are made over -- briefing jobs, plus (search
        mode) every sighting reported in a snapshot entry, so that agents with
        the same snapshot plan over the same jobs."""
        jobs = self.jobs
        extra = [(cid, x, y) for e in self.entries.values() for cid, x, y in e.sightings]
        if not extra:
            return jobs
        jobs = dict(jobs)
        for cid, x, y in extra:
            jobs.setdefault(cid, (x, y))
        return jobs

    # -- planning ---------------------------------------------------------------
    def plan_agent(self, aid: str, e: Entry, now: float, done=frozenset(), jobs=None) -> PlanAgent:
        """Dead-reckon one snapshot entry to `now` toward its committed job --
        the same arithmetic in every agent, so it cannot cause disagreement.
        A committed job already known done still predicts the agent's
        position (it went there); it just no longer holds the job."""
        jobs = self.jobs if jobs is None else jobs
        r = self.roster[aid]
        speed, reach, kind = r.speed * e.eff, r.reach, r.kind
        cant = e.cant
        c = e.committed if e.committed in jobs else None
        if c is None:
            return PlanAgent(aid, e.pos, max(now, e.stamp), speed, None, reach, kind, cant)
        free = PlanAgent(aid, e.pos, e.stamp, speed, None, reach, kind, cant)
        st = _step(free, e.pos, c, jobs, self.svc)
        if st is None:
            return PlanAgent(aid, e.pos, max(now, e.stamp), speed, None, reach, kind, cant)
        return PlanAgent(aid, st[1], max(now, e.stamp + st[0]), speed,
                         None if c in done else c, reach, kind, cant)

    def maybe_plan(self, now: float, my_pos: tuple, periodic: bool) -> None:
        """Run once per epoch, on the same epoch everywhere. `periodic` forces a
        replan (true on every PERIODIC_REPLAN_EPOCHS-th epoch)."""
        done = frozenset().union(*(e.done for e in self.entries.values()))
        self.detect_lost(now, my_pos)
        lost = frozenset(self.lost).union(*(e.lost for e in self.entries.values()))
        live = self.planners
        jobs = self.plan_jobs()
        stale = {aid for aid in live
                 if aid != self.me and now - self.entries[aid].stamp > STALE_S}
        # Reserve newly-stale peers' routes from the last plan where they were fresh.
        for aid in sorted(stale):
            if aid not in self.reserved and aid in self.routes:
                route = list(self.routes[aid])
                fin = list(self.finish.get(aid, []))
                ent = self.entries[aid]
                if ent.committed in jobs and ent.committed not in done:
                    route = [ent.committed] + route
                    fin = [self.plan_agent(aid, ent, ent.stamp, jobs=jobs).t_free] + fin
                self.reserved[aid] = (route, fin, now)
                self.dirty = True
        for aid in list(self.reserved):
            if aid not in stale:   # heard again: its jobs are plannable
                del self.reserved[aid]
                self.dirty = True

        fresh = sorted(aid for aid in live if aid not in stale)
        plan_agents = {aid: self.plan_agent(aid, self.entries[aid], now, done, jobs) for aid in fresh}

        # Overdue reserved jobs (agent loss we cannot observe directly). Being
        # presumed LOST is only a hint -- silence looks the same whether a peer
        # died or a partition cut it off -- so it never frees a job before that
        # job's own planned finish: a cut-off peer has normally done it by then,
        # and the worst case is a late second visit, not two vehicles on one job.
        # Each overdue job is offered to the non-owners in ETA order, candidate
        # k becoming eligible k x RELEASE_STAGGER_S after release, so no job is
        # orphaned while any candidate survives even if views of the ranking
        # differ. A sub-team takes it as soon as its best-ranked member may.
        cand_end = {}
        for aid in sorted(live):
            r = self.roster[aid]
            if aid in plan_agents:
                pa = plan_agents[aid]
                cand_end[aid] = (pa.pos, pa.t_free, pa.speed)
            elif aid in self.reserved:
                route, fin, _ = self.reserved[aid]
                pos = jobs[route[-1]] if route and route[-1] in jobs else self.entries[aid].pos
                cand_end[aid] = (pos, fin[-1] if fin else now, r.speed * self.entries[aid].eff)
        overdue_open = set()
        for owner, (route, fin, t_res) in sorted(self.reserved.items()):
            factor, slack = (1.0, LOST_RELEASE_SLACK_S) if owner in lost \
                else (RELEASE_FACTOR, RELEASE_SLACK_S)
            for tid, t_fin in zip(route, fin):
                if tid in done or tid not in jobs:
                    continue
                t_rel = t_res + factor * max(0.0, t_fin - t_res) + slack
                if now <= t_rel:
                    continue
                ranked = sorted(
                    (end_t + _reach_leg(pos, jobs[tid], spd, self.roster[aid].reach)[0], aid)
                    for aid, (pos, end_t, spd) in cand_end.items()
                    if aid != owner and _service(self.svc, PlanAgent(
                        aid, pos, end_t, spd, None, self.roster[aid].reach,
                        self.roster[aid].kind, self.entries[aid].cant), tid) is not None)
                ranks = [k for k, (_, aid) in enumerate(ranked) if aid in plan_agents]
                if ranks and now > t_rel + ranks[0] * RELEASE_STAGGER_S:
                    overdue_open.add(tid)
                    if tid not in self.released:
                        self.released.add(tid)
                        self.metrics["releases"] += 1
                        self.dirty = True
        if periodic:
            self.dirty = True   # same epoch for everyone: absorbs plan-vs-reality drift
        if not self.dirty:
            return
        self.dirty = False
        self.plans += 1
        self.metrics["plans"] += 1

        # Commitment conflicts (two fresh agents committed to one job): lowest
        # (arrival, id) keeps it, the other is planned uncommitted.
        owner: dict = {}
        for aid in fresh:
            pa = plan_agents[aid]
            if pa.committed is None:
                continue
            key = (pa.t_free, aid)
            if pa.committed not in owner or key < owner[pa.committed][0]:
                owner[pa.committed] = (key, aid)
        for aid in fresh:
            pa = plan_agents[aid]
            if pa.committed is None:
                continue
            if owner[pa.committed][1] != aid:
                self.metrics["conflicts_resolved"] += 1
                e = self.entries[aid]
                r = self.roster[aid]
                plan_agents[aid] = PlanAgent(aid, e.pos, max(now, e.stamp), r.speed * e.eff,
                                             None, r.reach, r.kind, e.cant)
        reserved_jobs = {tid for route, _, _ in self.reserved.values() for tid in route}
        committed_jobs = {pa.committed for pa in plan_agents.values() if pa.committed}
        open_jobs = {tid: pos for tid, pos in jobs.items()
                     if tid not in done and tid not in committed_jobs
                     and (tid not in reserved_jobs or tid in overdue_open)}
        routes, finish = plan_routes(list(plan_agents.values()), open_jobs, service=self.svc,
                                     restarts=self.restarts)
        self.routes, self.finish = routes, finish

        # My commitment is whatever the shared plan says it is: kept if my
        # entry's commitment survived conflict resolution, else re-taken from
        # the head of my new route by advance().
        mine = plan_agents.get(self.me)
        self.committed = mine.committed if mine is not None else None

    def detect_lost(self, now: float, my_pos: tuple) -> None:
        """Presume a peer dead when silence can't be explained by range: it
        has been silent LOST_S, my own link works (I heard someone else in
        the last epoch), and dead-reckoning puts it well inside both link
        ranges. Not attempted for line-of-sight-limited classes (an occluding
        building is indistinguishable from death) -- those fall back to the
        overdue-release rule. The verdict is broadcast and merged by union,
        so the team converges on one lost set within an epoch."""
        heard_recently = any(now - e.stamp <= self.epoch_s + 1e-6
                             for aid, e in self.entries.items() if aid != self.me)
        if not heard_recently:
            return
        my_range = self.roster[self.me].comms_range_m
        jobs = self.plan_jobs()
        for aid in sorted(self.planners - {self.me} - self.lost):
            peer = self.roster[aid]
            e = self.entries[aid]
            if now - e.stamp <= LOST_S or peer.los_required:
                continue
            predicted = self.plan_agent(aid, e, now, jobs=jobs).pos if e.committed in jobs else e.pos
            if math.dist(my_pos, predicted) < LOST_RANGE_FRACTION * min(my_range, peer.comms_range_m):
                self.lost.add(aid)
                self.metrics.setdefault("presumed_lost", 0)
                self.metrics["presumed_lost"] += 1
                self.dirty = True

    def advance(self) -> None:
        """Between epochs: when the committed job is done, take the next job on
        MY route from the last plan -- which every peer computed identically,
        so this needs no new agreement."""
        if self.committed is not None and self.committed not in self.done:
            return
        self.committed = None
        route = self.routes.get(self.me, [])
        while route and route[0] in self.done:
            route.pop(0)
        if route:
            self.committed = route.pop(0)

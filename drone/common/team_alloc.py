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
only brings its jobs' release forward (LOST_RELEASE_FACTOR of their planned
times).

While any live peer is stale, every agent keeps (pins) the route order of the
last plan in which the whole team was fresh -- that is what the stale peers
are predicting -- and plans anything new after it; released jobs come after
an agent's own work, never in place of it. Agents whose work only counts once
a confirmer hears about it (`relay_dependent`: a search fixed-wing's sweep is
worthless without its sightings) are handled differently: when one goes stale
its remaining jobs are split deterministically among the confirmers from the
same last plan, and a relay-dependent agent that hears no confirmer stops
taking jobs. A confirmer that has heard nobody at all since that plan (the
link went for everyone at once) instead re-splits ALL of the plan's open work
over the confirmers alone: behind each confirmer's own route, handed cells
would never be reached in a search's time.

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
# A presumed-lost peer's job is released at 1.25x its planned time plus this:
# lost is only a hint (a partition looks the same), and on the v2 holdout this
# halved split-team duplicates at no cost to agent-loss recovery (seed 0).
LOST_RELEASE_FACTOR = 1.25
LOST_RELEASE_SLACK_S = 2.0
RELEASE_STAGGER_S = 20.0   # candidate k (by ETA) may take an overdue job k x this after release
# Jobs that take time once reached (a search cell: ~195 s for a quad) vary by
# far more than RELEASE_SLACK_S. Their release slack, and the stagger between
# candidates, grow by this fraction of the job's service time (lost owners: half).
SERVICE_SLACK_FRACTION = 0.5
STALL_S = 15.0             # no 1 m of progress on the committed job for this long -> give it up
STALL_PROGRESS_M = 1.0
# A relay-dependent peer counts as cut off only when NOBODY the agent can hear
# has heard it within this: its results travel by gossip, not just direct links.
SEEN_S = STALE_S + 2.0
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


def _route_end(agent: PlanAgent, route: list, jobs: dict, svc: dict) -> tuple:
    """(position, absolute time) at which `agent` finishes `route` -- jobs not
    in `jobs` are skipped; (pos, inf) from the first one its class can't do."""
    t, pos = agent.t_free, agent.pos
    for tid in route:
        if tid not in jobs:
            continue
        st = _step(agent, pos, tid, jobs, svc)
        if st is None:
            return pos, math.inf
        t += st[0]
        pos = st[1]
    return pos, t


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
    hears: Optional[frozenset] = None    # peers it heard within STALE_S (None: not reported)


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
                 cant=frozenset(p.get("cant", ())),
                 hears=frozenset(p["hears"]) if "hears" in p else None)


class TeamAllocCore:
    """One agent's coordination state. Attribute names are the simulator's
    historical belief fields; callers (and the sim's referee metrics) read
    `committed`, `done`, `entries`, `sightings`, `eff` directly."""

    def __init__(self, me: str, roster: dict, jobs: dict, planners: set, *,
                 svc: dict = None, restarts: int = PLAN_RESTARTS, epoch_s: float = 0.5,
                 metrics: dict = None, relay_dependent: set = None):
        self.me = me
        self.roster = roster              # id -> RosterEntry
        self.jobs = jobs                  # briefing jobs, id -> (x, y)
        self.planners = planners          # ids that take jobs from the plan
        # Planners whose work only counts once a confirmer hears its result
        # (search fixed-wings); every other planner is a confirmer.
        self.relay_dependent = set(relay_dependent or ()) & set(planners)
        self.confirmers = set(planners) - self.relay_dependent
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
        # (agents, routes, finish by job, done) of the last all-fresh plan
        self.consistent: Optional[tuple] = None
        self.handoff: dict = {}           # stale relay-dependent id -> {assignee: [jobs]}
        self.inconsistent_since: Optional[float] = None
        self.handoff_opened: set = set()
        self.heard: set = set()           # peers this agent has ever received a STATE from
        self.virtual_fresh: dict = {}     # new job -> the in-touch owner (maybe me) only it may take
        self.releasing: set = set()       # reserved owners the overdue rule may release from
        self.muted: bool = False
        self.blind: Optional[dict] = None   # isolated confirmer's split: {confirmer: (route, finish)}
        self.t_heard: float = -math.inf     # latest send time of any STATE received
        self.t_consistent: float = -math.inf   # when the last all-fresh plan was made
        # id -> latest epoch stamp at which anyone heard it directly, merged by
        # max over gossip (relay_dependent teams only): who is connected to
        # the team at all, as opposed to who this agent hears itself.
        self.seen: dict = {}

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
        self.heard.add(sender)
        self.t_heard = max(self.t_heard, t_sent)
        if t_sent > self.seen.get(sender, -math.inf):
            self.seen[sender] = t_sent
        for aid, t in payload.get("seen", ()):
            if aid != self.me and float(t) > self.seen.get(aid, -math.inf):
                self.seen[aid] = float(t)
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
        hears_now = frozenset(aid for aid, e in self.entries.items()
                              if aid != self.me and aid in self.heard and now - e.stamp <= STALE_S)
        self.entries[self.me] = Entry(pos=my_pos, stamp=now, committed=self.committed,
                                      done=frozenset(self.done), eff=eff, lost=lost,
                                      sightings=sightings, cant=frozenset(self.cant), hears=hears_now)
        hears = sorted(self.entries[self.me].hears)
        payload = {"epoch_state": True, "pos": list(my_pos), "task": self.committed,
                   "done": sorted(self.done), "eff": eff, "lost": sorted(lost), "hears": hears}
        if sightings:
            payload["sightings"] = [list(x) for x in sorted(sightings)]
        if self.cant:
            payload["cant"] = sorted(self.cant)
        if self.relay_dependent:
            self.seen[self.me] = now
            payload["seen"] = [[aid, round(t, 3)] for aid, t in sorted(self.seen.items())]
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
        # Stale: unheard for STALE_S -- or, once the briefing plan exists, never
        # heard at all. Planning a never-heard peer as if it were at its
        # briefing position puts it in a plan it isn't flying.
        # A one-way link (I hear it, it doesn't hear me: a rover transmits
        # less far than a quad) counts as stale too, so both ends treat each
        # other the same way -- otherwise I plan it into a plan it can't know.
        stale = {aid for aid in live
                 if aid != self.me and (now - self.entries[aid].stamp > STALE_S
                                        or (self.consistent is not None and aid not in self.heard)
                                        or (self.entries[aid].hears is not None
                                            and self.me not in self.entries[aid].hears))}
        relay_stale = {aid for aid in stale & self.relay_dependent if self._cut_off(aid, now)}
        # Reserve newly-stale peers' routes from the last plan where they were
        # fresh. A stale relay-dependent peer's route is handed off instead
        # (below): whatever it does now, nobody can confirm it.
        for aid in sorted(stale - relay_stale):
            if aid in self.reserved:
                continue
            if aid in self.heard and aid in self.routes:
                route = list(self.routes[aid])
                fin = list(self.finish.get(aid, []))
                ent = self.entries[aid]
                if ent.committed in jobs and ent.committed not in done:
                    if route and route[0] == ent.committed:   # it took my plan's head job
                        route, fin = route[1:], fin[1:]
                    route = [ent.committed] + route
                    fin = [self.plan_agent(aid, ent, ent.stamp, jobs=jobs).t_free] + fin
            elif self.consistent is not None:
                # Never heard: whatever this agent planned for it since was
                # planned from the briefing alone, which IT never saw -- it
                # flies the last plan both sides shared (the t=0 one).
                c_agents, c_routes, c_fin, _ = self.consistent
                route = ([c_agents[aid].committed] if aid in c_agents and c_agents[aid].committed
                         else []) + list(c_routes.get(aid, []))
                fin = [c_fin.get(tid, now) for tid in route]
            else:
                continue
            self.reserved[aid] = (route, fin, now)
            self.dirty = True
        for aid in list(self.reserved):
            if aid not in stale:   # heard again: its jobs are plannable
                del self.reserved[aid]
                self.dirty = True
        for aid in sorted(relay_stale):
            if aid not in self.handoff:
                self.handoff[aid] = self._split_handoff(aid, jobs, lost)
                self.reserved.pop(aid, None)   # was only out of my range; now cut off
                self.metrics["handoffs"] = self.metrics.get("handoffs", 0) + 1
                # Stale assignees' shares join their reservations in the gate
                # pass below, with gate-aware finish times.
                self.dirty = True
        for aid in list(self.handoff):
            if aid not in relay_stale:
                del self.handoff[aid]
                self.dirty = True
        # A relay-dependent agent that has heard NOBODY for SEEN_S does
        # nothing useful by sweeping: the confirmers, equally cut off from it,
        # have split its jobs among themselves. Merely being out of the
        # confirmers' range is different -- its sightings travel by gossip,
        # or keep until it is back in range -- so it keeps working.
        muted = self.me in self.relay_dependent and all(
            self._cut_off(aid, now) for aid in self.planners - lost if aid != self.me)
        if muted != self.muted:
            self.muted = muted
            self.dirty = True
        # A confirmer that hears nobody (every live peer cut off) in a team
        # with relay-dependent sweepers: the cut-off sweepers' cells, handed
        # off behind each confirmer's own route, are never reached in time --
        # a quad sweeps ~4 cells in a 900 s search. Instead every such
        # confirmer re-splits ALL the last shared plan's open work over the
        # confirmers alone, the same split wherever it is computed, takes its
        # own share in any order, and holds the others' shares reserved to
        # them (the overdue rule still covers a dead one).
        # Only if it has heard NOBODY since that plan: then the link went for
        # everyone at once, as far as it can tell. One that still heard a
        # sub-team after the split (a partition) is not alone in being cut
        # off -- the others keep the handoff rules, and so must it.
        isolated = (self.me in self.confirmers and bool(self.relay_dependent)
                    and self.consistent is not None and self.t_heard <= self.t_consistent
                    and all(self._cut_off(aid, now) for aid in self.planners - lost if aid != self.me))
        if not isolated:
            self.blind = None
        elif self.blind is None:
            self.blind = self._split_blind(jobs)
            t_res = self.inconsistent_since if self.inconsistent_since is not None else now
            for c in sorted(self.confirmers - {self.me}):
                route, fin = self.blind.get(c, ([], []))
                if route:
                    self.reserved[c] = (list(route), list(fin), t_res)
                else:
                    self.reserved.pop(c, None)
            self.metrics["blind_splits"] = self.metrics.get("blind_splits", 0) + 1
            self.dirty = True

        fresh = sorted(aid for aid in live if aid not in stale and not (aid == self.me and muted))
        if self.consistent is None:
            # The briefing plan -- the shared reference every later rule falls
            # back on -- is made from the briefing alone, whatever has already
            # arrived: vehicles that start the fallback a little apart still
            # make the same one.
            brief = {aid: Entry(pos=tuple(r.home), stamp=0.0, eff=r.eff) for aid, r in self.roster.items()}
            plan_agents = {aid: self.plan_agent(aid, brief[aid], now, frozenset(), jobs) for aid in fresh}
        else:
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
        self.releasing = set()
        for owner, (route, fin, t_res) in sorted(self.reserved.items()):
            if owner not in lost and not self._cut_off(owner, now):
                # Out of MY range, but someone I hear has heard it recently: it
                # is alive, and its direct neighbours plan with it -- releasing
                # its jobs would only race it.
                continue
            self.releasing.add(owner)
            factor, slack, s_frac = (LOST_RELEASE_FACTOR, LOST_RELEASE_SLACK_S, 0.5 * SERVICE_SLACK_FRACTION) \
                if owner in lost else (RELEASE_FACTOR, RELEASE_SLACK_S, SERVICE_SLACK_FRACTION)
            for tid, t_fin in zip(route, fin):
                if tid in done or tid not in jobs:
                    continue
                svc_s = (self.svc.get(tid) or {}).get(self.roster[owner].kind) or 0.0
                t_rel = t_res + factor * max(0.0, t_fin - t_res) + slack + s_frac * svc_s
                if now <= t_rel:
                    continue
                ranked = sorted(
                    (end_t + _reach_leg(pos, jobs[tid], spd, self.roster[aid].reach)[0], aid)
                    for aid, (pos, end_t, spd) in cand_end.items()
                    if aid != owner and _service(self.svc, PlanAgent(
                        aid, pos, end_t, spd, None, self.roster[aid].reach,
                        self.roster[aid].kind, self.entries[aid].cant), tid) is not None)
                ranks = [k for k, (_, aid) in enumerate(ranked) if aid in plan_agents]
                # Candidate k waits out the stagger AND the time the candidate
                # ahead of it should take to get there (x RELEASE_FACTOR): a
                # slow first taker (a rover) is not a missing one.
                k0 = ranks[0] if ranks else 0
                ahead = RELEASE_FACTOR * max(0.0, ranked[k0 - 1][0] - t_rel) if k0 > 0 else 0.0
                if ranks and now > t_rel + k0 * (RELEASE_STAGGER_S + svc_s) + ahead:
                    overdue_open.add(tid)
                    if tid not in self.released:
                        self.released.add(tid)
                        self.metrics["releases"] += 1
                        self.dirty = True
        # A cut-off relay-dependent peer's cells handed to this sub-team open
        # only when they would be overdue from it (the overdue rule's timing),
        # queue behind own work: if it is merely on the other side of a
        # partition with confirmers of its own, it has swept them by then, and
        # the cost is a later re-check, not two vehicles on one cell. Under
        # blackout the confirmers have nothing else to do, so they still start
        # them promptly.
        held = set()
        handed = set()
        if self.handoff and self.consistent is not None and self.blind is None:
            c_fin = self.consistent[2]
            for aid_n, split in sorted(self.handoff.items()):
                for assignee, (route, fin) in split.items():
                    for tid, t_fin in zip(route, fin):
                        handed.add(tid)
                        if tid in done or tid not in jobs:
                            continue
                        t0 = self.inconsistent_since if self.inconsistent_since is not None else 0.0
                        svc_s = (self.svc.get(tid) or {}).get(self.roster[aid_n].kind) or 0.0
                        t_open = t0 + RELEASE_FACTOR * max(0.0, c_fin.get(tid, 0.0) - t0) \
                            + RELEASE_SLACK_S + SERVICE_SLACK_FRACTION * svc_s
                        if assignee in stale:
                            # Its share stays in its reservation (re-added if the
                            # reservation was rebuilt since), so the overdue rule's
                            # ranked, staggered release applies -- predicted to
                            # finish no earlier than the gate lets it start.
                            own_s = (self.svc.get(tid) or {}).get(self.roster[assignee].kind) or 0.0
                            r_route, r_fin, t_res = self.reserved.get(assignee, ([], [], now))
                            if tid not in r_route:
                                self.reserved[assignee] = (r_route + [tid],
                                                           r_fin + [max(t_fin, t_open + own_s)], t_res)
                            continue
                        if now <= t_open:
                            held.add(tid)
                        else:
                            overdue_open.add(tid)
                            if tid not in self.handoff_opened:
                                self.handoff_opened.add(tid)
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
        # Handed-off cells are for confirmers: another relay-dependent sweeper
        # taking one would only race the (maybe live) original.
        if handed:
            for aid, pa in list(plan_agents.items()):
                if aid in self.relay_dependent:
                    plan_agents[aid] = PlanAgent(pa.id, pa.pos, pa.t_free, pa.speed, pa.committed,
                                                 pa.reach, pa.kind, pa.cant | frozenset(handed))
        # A peer I hear but that doesn't hear me (stale only by the one-way
        # rule) can't know my commitments, but I know its: its committed job
        # is taken, and I yield it if I hold the same one.
        one_way = {aid for aid in stale if now - self.entries[aid].stamp <= STALE_S}
        theirs = {self.entries[aid].committed for aid in one_way
                  if self.entries[aid].committed in jobs and self.entries[aid].committed not in done}
        for aid, pa in list(plan_agents.items()):
            if pa.committed in theirs:
                plan_agents[aid] = PlanAgent(aid, pa.pos, pa.t_free, pa.speed, None, pa.reach, pa.kind,
                                             pa.cant)
                self.metrics["one_way_yields"] = self.metrics.get("one_way_yields", 0) + 1
        committed_jobs = {pa.committed for pa in plan_agents.values() if pa.committed} | theirs
        self.virtual_fresh = {}
        self._assign_new_jobs(now, jobs, done, stale, lost, committed_jobs)
        if self.virtual_fresh:
            for aid, pa in list(plan_agents.items()):
                barred = frozenset(t for t, w in self.virtual_fresh.items() if w != aid)
                if barred:
                    plan_agents[aid] = PlanAgent(pa.id, pa.pos, pa.t_free, pa.speed, pa.committed,
                                                 pa.reach, pa.kind, pa.cant | barred)
        reserved_jobs = {tid for route, _, _ in self.reserved.values() for tid in route}
        open_jobs = {tid: pos for tid, pos in jobs.items()
                     if tid not in done and tid not in committed_jobs and tid not in held
                     and (tid not in reserved_jobs or tid in overdue_open)}

        # With a live peer stale, that peer predicts this sub-team's jobs from
        # the last all-fresh plan and releases each one it believes overdue.
        # Re-plan freely (the sub-team may rebalance), unless that would finish
        # more of those jobs past the stale side's release time than keeping
        # the all-fresh order (pinning) would.
        # A plan is shared only when every live peer has actually been heard:
        # before that, a peer's entry is the briefing, which is not what that
        # peer is planning from (it knows its own sent state). The first plan,
        # from the briefing alone, is the exception -- everyone makes it.
        # Presumed-lost peers count too: loss is only a hint (a partition
        # looks the same), and a sub-team that took it as fact would re-plan
        # the whole mission as if it were the team.
        consistent = not stale and (
            self.consistent is None or (self.planners - {self.me}) <= self.heard)
        if consistent:
            self.inconsistent_since = None
        elif self.inconsistent_since is None:
            self.inconsistent_since = now
        routes, finish = self._build_routes(plan_agents, open_jobs, overdue_open, {})
        if not consistent and self.consistent is not None and self.blind is None:
            c_agents, c_routes, c_fin, _ = self.consistent
            pinned = {aid: [] for aid in plan_agents}
            for aid, pa in plan_agents.items():
                full_c = ([c_agents[aid].committed] if aid in c_agents and c_agents[aid].committed
                          else []) + list(c_routes.get(aid, ()))
                for tid in full_c:
                    if tid in open_jobs and tid not in overdue_open and tid != pa.committed \
                            and tid not in pa.cant and all(tid not in p for p in pinned.values()):
                        if _step(pa, pa.pos, tid, open_jobs, self.svc) is None:
                            break
                        pinned[aid].append(tid)
            if any(pinned.values()):
                t0 = self.inconsistent_since
                deadline = {tid: t0 + RELEASE_FACTOR * max(0.0, t - t0) + RELEASE_SLACK_S - 2.0
                            for tid, t in c_fin.items()}

                def late_count(rs, fs):
                    n = 0
                    for aid, pa in plan_agents.items():
                        if pa.committed in deadline and pa.t_free > deadline[pa.committed]:
                            n += 1
                        for tid, t in zip(rs[aid], fs[aid]):
                            if tid in deadline and t > deadline[tid]:
                                n += 1
                    return n
                p_routes, p_finish = self._build_routes(plan_agents, open_jobs, overdue_open, pinned)
                if late_count(p_routes, p_finish) < late_count(routes, finish):
                    routes, finish = p_routes, p_finish
                    self.metrics["pinned_plans"] = self.metrics.get("pinned_plans", 0) + 1
        self.routes, self.finish = routes, finish
        if consistent:
            c_fin = {}
            for aid, pa in plan_agents.items():
                if pa.committed:
                    c_fin[pa.committed] = pa.t_free
                for tid, t in zip(routes[aid], finish[aid]):
                    c_fin[tid] = t
            self.consistent = (dict(plan_agents), {aid: list(r) for aid, r in routes.items()}, c_fin,
                               done)
            self.t_consistent = now

        # My commitment is whatever the shared plan says it is: kept if my
        # entry's commitment survived conflict resolution, else re-taken from
        # the head of my new route by advance().
        mine = plan_agents.get(self.me)
        self.committed = mine.committed if mine is not None else None

    def _build_routes(self, plan_agents: dict, open_jobs: dict, overdue_open: set,
                      pinned: dict) -> tuple:
        """Routes for the fresh agents: each one's `pinned` prefix, then its
        share of the remaining own work, then -- released jobs go after
        everyone's own work, since a second visit to a job whose owner may
        simply be out of touch must not delay first visits -- its share of
        the overdue ones."""
        starts = {}
        for aid, pa in plan_agents.items():
            pos, t = _route_end(pa, pinned.get(aid, []), open_jobs, self.svc)
            starts[aid] = PlanAgent(aid, pos, t, pa.speed, None, pa.reach, pa.kind, pa.cant)
        taken = {tid for r in pinned.values() for tid in r}
        own = {tid: p for tid, p in open_jobs.items() if tid not in taken and tid not in overdue_open}
        routes1, _ = plan_routes(list(starts.values()), own, service=self.svc, restarts=self.restarts)
        late = {tid: p for tid, p in open_jobs.items() if tid in overdue_open and tid not in taken}
        routes2 = {}
        if late:
            ends = {}
            for aid, st in starts.items():
                pos, t = _route_end(st, routes1.get(aid, []), own, self.svc)
                ends[aid] = PlanAgent(aid, pos, t, st.speed, None, st.reach, st.kind, st.cant)
            routes2, _ = plan_routes(list(ends.values()), late, service=self.svc,
                                     restarts=self.restarts)
        routes, finish = {}, {}
        for aid, pa in plan_agents.items():
            routes[aid] = list(pinned.get(aid, [])) + routes1.get(aid, []) + routes2.get(aid, [])
            finish[aid] = _route_times(pa, routes[aid], open_jobs, self.svc)
        return routes, finish

    def idle_target(self) -> Optional[str]:
        """For an idle agent: the open job it could do that a peer the overdue
        rule may release from (lost or cut off) holds in reservation and is
        expected to finish LAST -- where a released job is likeliest to come
        from. Waiting near it makes this agent the natural taker if that job is
        ever released. Nobody else's jobs are ever released -- a peer merely
        out of my range, or one-way, is still flying them -- so with no such
        peer there is nothing to wait for (None): the agent stays put rather
        than crossing the area for no gain."""
        if self.committed is not None or self.me not in self.planners:
            return None
        r = self.roster[self.me]
        me_pa = PlanAgent(self.me, tuple(self.entries[self.me].pos), 0.0, r.speed, None,
                          r.reach, r.kind, frozenset(self.cant))
        expect = {}
        for owner in sorted(self.releasing & set(self.reserved)):
            route, fin, _ = self.reserved[owner]
            for tid, t in zip(route, fin):
                expect[tid] = max(expect.get(tid, -math.inf), t)
        jobs = self.plan_jobs()
        best = None
        for tid, t in sorted(expect.items()):
            if tid in self.done or tid not in jobs or _service(self.svc, me_pa, tid) is None:
                continue
            if best is None or t > best[0]:
                best = (t, tid)
        return best[1] if best else None

    def _cut_off(self, aid: str, now: float) -> bool:
        """Nobody I can hear has heard `aid` within SEEN_S (stale stamps
        count: a briefing-only entry is stamp 0)."""
        t = max(self.seen.get(aid, -math.inf), self.entries[aid].stamp)
        return now - t > SEEN_S

    def _assign_new_jobs(self, now: float, jobs: dict, done: frozenset, stale: set,
                         lost: frozenset, committed_jobs: set) -> None:
        """A job that appeared after the last all-fresh plan (a sighting) while
        some peer is stale would otherwise be taken by every sub-team that
        hears of it. Give it to the planner that the LAST ALL-FRESH PLAN says
        reaches it first -- data every agent holds identically -- stale or
        not. If that agent is stale here, the job is reserved to it with its
        predicted finish, so the overdue rule still covers an owner who never
        heard of it."""
        if not stale or self.consistent is None:
            return
        c_agents, c_routes, _, _ = self.consistent
        known = {tid for r in c_routes.values() for tid in r} | \
            {pa.committed for pa in c_agents.values() if pa.committed}
        held = {tid for route, _, _ in self.reserved.values() for tid in route}
        ends = {}
        for aid in sorted(self.planners - lost):
            if aid in c_agents and not (aid in stale and aid in self.relay_dependent):
                pa = c_agents[aid]
                ends[aid] = (pa, _route_end(pa, c_routes.get(aid, []), jobs, self.svc))
        for tid in sorted(jobs):
            if tid in done or tid in known or tid in held or tid in committed_jobs:
                continue
            best = None
            for aid, (pa, (pos, t_end)) in ends.items():
                st = _step(PlanAgent(aid, pos, t_end, pa.speed, None, pa.reach, pa.kind, pa.cant),
                           pos, tid, jobs, self.svc)
                if st is not None and (best is None or (t_end + st[0], aid) < best):
                    best = (t_end + st[0], aid)
            if best is None:
                continue
            eta, owner = best
            if owner not in stale:
                # The owner is me or in touch with me: only it may take the
                # job -- another sub-team that also hears the owner would
                # otherwise plan it to one of its own members.
                self.virtual_fresh[tid] = owner
                continue
            route, fin, t_res = self.reserved.get(owner, ([], [], now))
            # It does them one after another: predicted from its route's end
            # alone, every new job an owner holds would look due at once, and
            # the later ones would be released while it is still on the first.
            if route and fin and route[-1] in jobs:
                pa = ends[owner][0]
                last = jobs[route[-1]]
                st = _step(PlanAgent(owner, last, fin[-1], pa.speed, None, pa.reach, pa.kind, pa.cant),
                           last, tid, jobs, self.svc)
                if st is not None:
                    eta = max(eta, fin[-1] + st[0])
            self.reserved[owner] = (route + [tid], fin + [max(now, eta)], t_res)
            self.metrics["virtual_owners"] = self.metrics.get("virtual_owners", 0) + 1

    def _split_handoff(self, aid: str, jobs: dict, lost: frozenset) -> dict:
        """Split a cut-off relay-dependent peer's remaining jobs among the
        confirmers, from the last ALL-FRESH plan only -- the one plan every
        confirmer holds identically, however each has replanned on its own
        since -- so they agree on the split without talking.
        {confirmer: (route, finish times)}."""
        if self.consistent is None:
            return {}
        agents, routes, _, done = self.consistent
        theirs = ([agents[aid].committed] if aid in agents and agents[aid].committed else []) \
            + list(routes.get(aid, []))
        if aid not in agents and aid in self.reserved:
            theirs = list(self.reserved[aid][0])
        work = {tid: jobs[tid] for tid in theirs if tid in jobs and tid not in done}
        if not work:
            return {}
        # Every confirmer of the shared plan is a candidate, lost or not: who
        # is presumed lost differs between agents and over time, and the split
        # must be the same wherever and whenever it is computed. A dead
        # assignee's share is reserved to it and released by the overdue rule.
        cands = []
        for c in sorted(self.confirmers - {aid}):
            if c in agents:
                pa = agents[c]
                pos, t = _route_end(pa, routes.get(c, []), jobs, self.svc)
                cands.append(PlanAgent(c, pos, t, pa.speed, None, pa.reach, pa.kind, pa.cant))
            elif c in self.reserved:
                r = self.roster[c]
                route, fin, _ = self.reserved[c]
                pos = jobs[route[-1]] if route and route[-1] in jobs else self.entries[c].pos
                cands.append(PlanAgent(c, pos, fin[-1] if fin else 0.0, r.speed * self.entries[c].eff,
                                       None, r.reach, r.kind, self.entries[c].cant))
        if not cands:
            return {}
        split, fin = plan_routes(cands, work, service=self.svc, restarts=self.restarts)
        return {c: (list(r), list(fin[c])) for c, r in split.items() if r}

    def _split_blind(self, jobs: dict) -> dict:
        """Every job open in the last ALL-FRESH plan, routed over the
        confirmers alone from their state in that plan -- the plan the team
        would have made had it known the sweepers were cut off. A confirmer
        keeps the job it is flying (its commitment, else its route's head,
        which it has taken by the time it is isolated): if the split gives
        that job to someone else, it moves back to the front of its own share.
        From shared data only, so isolated confirmers agree on it without
        talking. {confirmer: (route, finish times)}, kept job first."""
        agents, routes, _, done = self.consistent
        work, keep = {}, {}
        for aid in sorted(agents):
            pa = agents[aid]
            mine = list(routes.get(aid, []))
            if aid in self.relay_dependent and pa.committed:
                mine = [pa.committed] + mine
            mine = [tid for tid in mine if tid in jobs and tid not in done]
            if aid in self.confirmers and not pa.committed and mine:
                keep[aid] = mine[0]
            for tid in mine:
                work[tid] = jobs[tid]
        cands = {c: PlanAgent(c, agents[c].pos, agents[c].t_free, agents[c].speed, None, agents[c].reach,
                              agents[c].kind, agents[c].cant)
                 for c in sorted(self.confirmers) if c in agents}
        if not cands:
            return {}
        split = {}
        if work:
            split, _ = plan_routes(list(cands.values()), work, service=self.svc, restarts=self.restarts)
        split = {c: [tid for tid in split.get(c, []) if tid not in keep.values()] for c in cands}
        out = {}
        for c in sorted(cands):
            pa = agents[c]
            route = ([keep[c]] if c in keep else []) + split[c]
            if pa.committed in jobs and pa.committed not in done:
                route = [pa.committed] + route
                times = [pa.t_free] + _route_times(cands[c], route[1:], jobs, self.svc)
            else:
                times = _route_times(cands[c], route, jobs, self.svc)
            if route:
                out[c] = (route, times)
        return out

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
            heard = max(e.stamp, self.seen.get(aid, -math.inf))   # by me, or (gossip) anyone
            if now - heard <= LOST_S or peer.los_required:
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

"""Run one (variant, scenario, seed) against a live headless Depot sim and score it."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

from rover.sim.depot_client import DepotClient

from .camera import Camera
from .controllers import Follower, Variant
from .scenarios import Scenario
from .sensor import CAPTURE_HZ, SensorModel

RID = "bench"
TICK = 0.05          # control tick (sim s)
LATENCY = 0.10       # capture -> controller (inference + transport)


@dataclass
class Row:
    t: float
    rover: tuple
    cmd: tuple
    gt: dict           # name -> (x, y)
    est: tuple | None
    state: str
    valid: bool
    nvis: dict         # name -> visible & in-frame samples (of 18)
    nhid: dict = None  # name -> samples visible regardless of frame (0 = hidden by an obstacle)


def _wait_sim_time(c: DepotClient, last_t: float):
    while True:
        view = c.person_view(RID, 0.3)
        if view["t"] - last_t >= TICK - 1e-6:
            return view
        time.sleep(0.004)


def run(c: DepotClient, variant: Variant, sc: Scenario, seed: int, cam_h: float = 0.30,
        pitch_deg: float | None = None, sweep_cam: Camera | None = None, drop: float = 0.01,
        reid_cos: float = 0.3) -> dict:
    cam = sweep_cam or Camera(height=cam_h, pitch_deg=variant.pitch_deg if pitch_deg is None else pitch_deg)
    sensor = SensorModel(cam, seed, drop=drop, reid_cos=reid_cos)
    ctl = Follower(variant)

    c.reset(seed)
    c.inject("person_governor", on=False)
    for ob in sc.obstacles:
        c.inject("place_obstacle", **ob)
    c.spawn(RID, sc.rover[:2], sc.rover[2])
    for p in sc.persons:
        params = dict(on=True, speed=p["speed"], waypoints=p["waypoints"])
        if p["name"] != "PersonActor":
            params["person"] = p["name"]
        c.inject("person_walk", **params)
    time.sleep(0.3)
    events = sorted(sc.events, key=lambda e: e[0])

    pending: list[tuple[float, object]] = []
    rows: list[Row] = []
    t0 = None
    last_cap = -1e9
    last_t = -1e9
    view = c.person_view(RID, cam.height)
    wall_deadline = time.time() + sc.T * 3 + 30
    while True:
        view = _wait_sim_time(c, last_t if last_t > -1e8 else view["t"] - TICK)
        t = view["t"]
        last_t = t
        if t0 is None:
            t0 = t
        ts = t - t0
        while events and events[0][0] <= ts:
            params = dict(on=True, **events.pop(0)[1])
            c.inject("person_walk", **params)
        if t - last_cap >= 1.0 / CAPTURE_HZ - 1e-6:
            last_cap = t
            fr = sensor.frame(view)
            pending.append((t + LATENCY, fr))
        while pending and pending[0][0] <= t:
            _, fr = pending.pop(0)
            ctl.observe(fr, t)
        pose = tuple(view["pose"])
        v, w = ctl.command(t, pose, view["clearance"])
        c.drive(RID, v, w)
        gt = {p["name"]: tuple(p["pos"]) for p in view["persons"]}
        nvis = {}
        for p in view["persons"]:
            nvis[p["name"]] = sum(1 for r in p["samples"]
                                  if r[3] and (lambda q: q is not None and cam.in_frame(q[0], q[1]))(cam.project(pose, r[0], r[1], r[2])))
        nhid = {p["name"]: sum(1 for r in p["samples"] if r[3]) for p in view["persons"]}
        rows.append(Row(ts, pose, (v, w), gt, ctl.est_xy(t), ctl.state, ctl.tracking_valid, nvis, nhid))
        if ts >= sc.T or ctl.failed or time.time() > wall_deadline:
            break
    c.stop(RID)
    events_log = c.get_events(0.0)
    return score(rows, sc, variant, seed, events_log, ctl)


def score(rows: list[Row], sc: Scenario, variant: Variant, seed: int, events_log: list, ctl: Follower) -> dict:
    n = len(rows)
    T = sc.T
    dt = TICK
    target = "PersonActor"
    rng_to = [math.dist(r.rover[:2], r.gt[target]) for r in rows]
    v = np.array([r.cmd[0] for r in rows])
    walking = np.array([math.hypot(*(np.array(rows[i].gt[target]) - np.array(rows[max(0, i - 4)].gt[target]))) / (4 * dt) > 0.1
                        for i in range(n)])
    should_move = np.array([walking[i] and rng_to[i] > variant.standoff + 0.25 for i in range(n)])
    stops = 0
    for i in range(1, n):
        if should_move[i] and v[i] < 0.02 and v[i - 1] >= 0.02:
            stops += 1
    still = float(np.mean([v[i] < 0.02 for i in range(n) if should_move[i]])) if should_move.any() else float("nan")
    acc = np.diff(v) / dt
    rms_acc = float(np.sqrt(np.mean(acc[should_move[1:]] ** 2))) if should_move[1:].any() else float("nan")

    valid = np.array([r.valid for r in rows])
    coast = np.array([r.state == "coasting" for r in rows])
    lost = np.array([r.state in ("reacq", "search", "failed") for r in rows])
    losses = sum(1 for i in range(1, n) if lost[i] and not lost[i - 1])
    errs = []
    wrong = 0
    for r in rows:
        if r.est is None:
            continue
        d_t = math.dist(r.est, r.gt[target])
        errs.append(d_t)
        others = [math.dist(r.est, p) for k, p in r.gt.items() if k != target]
        if others and min(others) < d_t and d_t > 0.6:
            wrong += 1
    # time with the target actually seen by the sensor (>=5 of 18 samples in frame and visible)
    seen = np.array([r.nvis.get(target, 0) >= 5 for r in rows])
    gap = best = 0
    for i in range(n):
        gap = gap + 1 if (not valid[i] and not coast[i]) else 0
        best = max(best, gap)
    occl = np.array([r.nhid.get(target, 18) == 0 for r in rows])     # hidden by an obstacle
    occl_s = float(occl.sum() * dt)
    unseen_s = float(sum(1 for r in rows if r.nvis.get(target, 0) == 0) * dt)  # hidden OR out of frame
    after = [i for i in range(1, n) if occl[i - 1] and not occl[i]]
    reacq_times = []
    for i in after:
        for j in range(i, n):
            if (valid[j] or coast[j]) and rows[j].est is not None and math.dist(rows[j].est, rows[j].gt[target]) < 0.8:
                reacq_times.append((j - i) * dt); break
        else:
            reacq_times.append(float("inf"))
    steady = [i for i in range(n) if rows[i].t >= 8.0 and rows[i].valid is not None]
    band = [abs(rng_to[i] - variant.standoff) for i in steady if valid[i] or coast[i]]
    collisions = [e for e in events_log if e.get("kind") == "collision"]
    return dict(
        variant=variant.name, scenario=sc.name, seed=seed, T=float(rows[-1].t), steps=n,
        aborted=bool(ctl.failed), full_stops=stops, still_frac=still, rms_accel=rms_acc,
        valid_frac=float(valid.mean()), coast_frac=float(coast.mean()), lost_frac=float(lost.mean()),
        losses=losses, longest_gap_s=best * dt, est_err_rms=float(np.sqrt(np.mean(np.square(errs)))) if errs else float("nan"),
        wrong_target_s=wrong * dt, occluded_s=occl_s, unseen_s=unseen_s, seen_frac=float(seen.mean()),
        reacq_after_occlusion_s=(max(reacq_times) if reacq_times else None),
        range_err_rms=float(np.sqrt(np.mean(np.square(band)))) if band else float("nan"),
        person_collisions=sum(1 for e in collisions if (e.get("data") or {}).get("with") == "person"),
        other_collisions=sum(1 for e in collisions if (e.get("data") or {}).get("with") != "person"),
        min_range=float(min(rng_to)), final_state=ctl.state)

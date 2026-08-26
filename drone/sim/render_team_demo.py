"""Render a multi-vehicle scenario run to video.

Runs a scenario through the **real** stack — ``reactive_goto_controller`` +
``TeamRuntime`` (bid-based task allocation over the comms fabric) + ``RuleBasedSmart`` — and
records every tick, then animates it. Nothing here scripts a flight path: the renderer only
draws state the simulator produced, so what you see is what the controller actually did.

    PYTHONPATH=. python -m eco.drone.sim.render_team_demo \\
        --scenario eco/drone/sim/scenarios_demo/cf_team_search.yaml \\
        --out /tmp/cf_team_search.mp4

Falls back to GIF if ffmpeg is unavailable.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

try:
    from .scenario import Scenario, ScenarioRunner
    from .team_runtime import TeamRuntime
    from .smart_layer import RuleBasedSmart, build_world_state
    from .vehicle_class import get_class
except ImportError:  # pragma: no cover - flat path
    from scenario import Scenario, ScenarioRunner
    from team_runtime import TeamRuntime
    from smart_layer import RuleBasedSmart, build_world_state
    from vehicle_class import get_class

# dark technical palette; one accent per vehicle
BG = "#0b0f14"
PANEL = "#121821"
GRID = "#1e2836"
WALL = "#2a3646"
WALL_EDGE = "#3d4d63"
TEXT = "#c8d3e0"
DIM = "#6b7a8d"
ACCENT = ["#22d3ee", "#fb923c", "#e879f9", "#a3e635"]
GOLD = "#fbbf24"


def record(scenario_path: str, max_s: float | None = None) -> dict:
    """Run the scenario with the real controller stack, capturing per-tick state."""
    sc = Scenario.from_yaml(scenario_path)
    r = ScenarioRunner(sc)
    rt = TeamRuntime(r)
    smart = RuleBasedSmart()
    base = rt.controller

    cache: dict = {"tick": -1, "ws": None}

    def ctl(agent, obs):
        a = np.asarray(base(agent, obs), dtype=np.float32).copy()
        if cache["tick"] != r.world.tick:      # one world state per tick, not per agent
            cache["tick"] = r.world.tick
            cache["ws"] = build_world_state(r, 0)
        for d in smart.tick(cache["ws"]):
            if d.agent_id in (None, agent.id):
                a = d.apply(agent, a)
        return a

    r.controller = ctl
    ids = [a.id for a in r.world.team_agents()]
    frames: list[dict] = []
    events: list[tuple[float, str]] = []
    found_prev = [False] * len(r.mission.targets)

    ticks = int((max_s or sc.duration_s) / r.world.dt)
    for _ in range(ticks):
        r.step()
        w = r.world
        # comms topology: who can actually hear whom right now (los_required indoors)
        links = []
        for i, aid in enumerate(ids):
            try:
                reach = set(r.comms.reachable(w, aid))
            except Exception:
                reach = set()
            for bid in ids[i + 1:]:
                if bid in reach:
                    links.append((aid, bid))
        agents = {}
        for a in w.team_agents():
            agents[a.id] = {
                "pos": a.pos.copy(),
                "goal": None if a.goal is None else a.goal.copy(),
                "speed": float(np.linalg.norm(a.vel)),
                "conf": float(getattr(a, "loc_confidence", 1.0)),
                "sensor_ok": bool(getattr(a, "sensor_ok", True)),
                "isolated": len(
                    [1 for x, y in links if aid_in((x, y), a.id)]) == 0,
            }
        found = [bool(t.found) for t in r.mission.targets]
        for i, (f, p) in enumerate(zip(found, found_prev)):
            if f and not p:
                closest = min(w.team_agents(),
                              key=lambda ag: np.linalg.norm(ag.pos - r.mission.targets[i].pos))
                events.append((w.t, f"{closest.id} found target {i + 1}"))
        found_prev = found
        frames.append({
            "t": w.t, "agents": agents, "found": found, "links": links,
            "reassign": rt.metrics["reassignments"],
        })
        if r.mission.complete(w):
            events.append((w.t, "all targets found — mission complete"))
            break

    return {
        "scenario": sc,
        "ids": ids,
        "frames": frames,
        "events": events,
        "targets": [t.pos.copy() for t in r.mission.targets],
        "vclass": get_class(sc.team[0]["type"]),
        "metrics": dict(rt.metrics),
    }


def aid_in(pair, agent_id):
    return agent_id in pair


def _boxes(sc, drop_ceiling_above=1.9):
    """Scenario obstacle boxes, minus the ceiling slab (it would black out a top view)."""
    out = []
    for o in sc.obstacles:
        cx, cy, cz, hx, hy, hz = (float(v) for v in o)
        if cz - hz >= drop_ceiling_above:      # ceiling
            continue
        out.append((cx, cy, cz, hx, hy, hz))
    return out


def interpolate(frames: list[dict], k: int) -> list[dict]:
    """Insert k-1 linearly-interpolated sub-frames between ticks.

    The simulator runs at 10 Hz; played back at 10 fps that looks like a slideshow, and
    played faster it is no longer real time. Interpolating positions lets the video run at
    real speed AND smoothly. Only continuous quantities are interpolated — discrete state
    (target found, comms links, claimed goal, counters) holds from the enclosing tick, so
    nothing is invented that the simulator did not produce.
    """
    if k <= 1:
        return frames
    out: list[dict] = []
    for i, f in enumerate(frames):
        nxt = frames[min(i + 1, len(frames) - 1)]
        for s in range(k):
            u = s / k
            agents = {}
            for aid, a in f["agents"].items():
                b = nxt["agents"][aid]
                agents[aid] = dict(a)
                agents[aid]["pos"] = a["pos"] * (1 - u) + b["pos"] * u
                agents[aid]["speed"] = a["speed"] * (1 - u) + b["speed"] * u
            g = dict(f)
            g["agents"] = agents
            g["t"] = f["t"] * (1 - u) + nxt["t"] * u
            out.append(g)
    return out


def render(rec: dict, out_path: str, fps: int = 25, dpi: int = 96,
           interp: int = 1) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, Circle
    from matplotlib.animation import FuncAnimation, FFMpegWriter, PillowWriter

    sc = rec["scenario"]
    vc = rec["vclass"]
    frames = interpolate(rec["frames"], interp)
    trail_ticks = 70 * max(1, interp)
    ids = rec["ids"]
    boxes = _boxes(sc)
    room = sc.room or {}
    sx, sy = (float(v) for v in room.get("size", [8.0, 6.0]))
    height = float(room.get("height", 2.4))
    hx, hy = sx / 2.0, sy / 2.0

    fig = plt.figure(figsize=(1920 / dpi, 1080 / dpi), dpi=dpi, facecolor=BG)
    gs = fig.add_gridspec(3, 2, width_ratios=[1.55, 1.0], height_ratios=[1.15, 1.0, 1.0],
                          left=0.045, right=0.975, top=0.885, bottom=0.055,
                          wspace=0.16, hspace=0.34)
    ax = fig.add_subplot(gs[:, 0], facecolor=PANEL)        # top-down
    ax_side = fig.add_subplot(gs[0, 1], facecolor=PANEL)   # elevation
    ax_stat = fig.add_subplot(gs[1, 1], facecolor=PANEL)   # per-drone table
    ax_log = fig.add_subplot(gs[2, 1], facecolor=PANEL)    # event log

    fig.text(0.045, 0.955, "4x Crazyflie 2.1 Brushless — cooperative room clearing",
             color=TEXT, fontsize=21, fontweight="bold", va="center")
    sub = (f"real stack: reactive_goto_controller + TeamRuntime bid allocation + "
           f"RuleBasedSmart  |  sensing: monocular-depth fan + 5x ToF, "
           f"{vc.sense_range_m:.0f} m range, GPS-denied")
    fig.text(0.045, 0.918, sub, color=DIM, fontsize=11.5, va="center")

    # ---- top-down ----
    ax.set_xlim(-hx - 0.35, hx + 0.35)
    ax.set_ylim(-hy - 0.35, hy + 0.35)
    ax.set_aspect("equal")
    ax.set_title("top view", color=DIM, fontsize=11, loc="left", pad=7)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=DIM, labelsize=8)
    ax.set_xlabel("x (m)", color=DIM, fontsize=9)
    ax.set_ylabel("y (m)", color=DIM, fontsize=9)
    ax.grid(True, color=GRID, lw=0.5, alpha=0.5)
    for cx, cy, cz, bx, by, bz in boxes:
        ax.add_patch(Rectangle((cx - bx, cy - by), 2 * bx, 2 * by,
                               facecolor=WALL, edgecolor=WALL_EDGE, lw=0.9, zorder=2))

    # ---- elevation (x-z), shows altitude separation and the ceiling clamp ----
    ax_side.set_xlim(-hx - 0.35, hx + 0.35)
    ax_side.set_ylim(0, height + 0.2)
    ax_side.set_title("elevation (x–z)  •  ceiling clamp at "
                      f"{vc.ceiling_m:.1f} m", color=DIM, fontsize=11, loc="left", pad=7)
    for s in ax_side.spines.values():
        s.set_color(GRID)
    ax_side.tick_params(colors=DIM, labelsize=8)
    ax_side.grid(True, color=GRID, lw=0.5, alpha=0.5)
    for cx, cy, cz, bx, by, bz in boxes:
        ax_side.add_patch(Rectangle((cx - bx, cz - bz), 2 * bx, 2 * bz,
                                    facecolor=WALL, edgecolor="none", alpha=0.45, zorder=2))
    ax_side.axhline(vc.ceiling_m, color=GOLD, lw=1.0, ls=":", alpha=0.75, zorder=3)
    ax_side.axhline(height, color=WALL_EDGE, lw=1.4, zorder=3)

    for a in (ax_stat, ax_log):
        a.set_xticks([]); a.set_yticks([])
        for s in a.spines.values():
            s.set_color(GRID)
    ax_stat.set_title("per-vehicle state", color=DIM, fontsize=11, loc="left", pad=7)
    ax_log.set_title("events", color=DIM, fontsize=11, loc="left", pad=7)

    # ---- artists ----
    trails, heads, goal_lines, side_pts, rings = [], [], [], [], []
    for i, _ in enumerate(ids):
        c = ACCENT[i % len(ACCENT)]
        trails.append(ax.plot([], [], color=c, lw=1.7, alpha=0.85, zorder=5)[0])
        heads.append(ax.plot([], [], "o", color=c, ms=9, zorder=7,
                             markeredgecolor="white", markeredgewidth=0.8)[0])
        rings.append(Circle((0, 0), vc.sense_range_m, facecolor="none", edgecolor=c,
                            lw=0.6, alpha=0.10, zorder=3))
        ax.add_patch(rings[-1])
        goal_lines.append(ax.plot([], [], color=c, lw=0.9, ls="--", alpha=0.45, zorder=4)[0])
        side_pts.append(ax_side.plot([], [], "o", color=c, ms=7, zorder=6)[0])
    link_lines = [ax.plot([], [], color="#38bdf8", lw=0.8, alpha=0.30, zorder=4)[0]
                  for _ in range(len(ids) * (len(ids) - 1) // 2)]
    tgt_hollow = ax.scatter([], [], marker="*", s=210, facecolor="none",
                            edgecolor=GOLD, lw=1.3, zorder=6)
    tgt_found = ax.scatter([], [], marker="*", s=240, color=GOLD, zorder=6)

    clock = fig.text(0.955, 0.955, "", color=TEXT, fontsize=17, ha="right",
                     va="center", family="monospace", fontweight="bold")
    # One text row per vehicle, drawn in that vehicle's own accent colour — otherwise there
    # is no way to tell which coloured dot on the map is which row in the table.
    ax_stat.text(0.04, 0.93, f"{'id':7s}{'alt':>7s}{'spd':>7s}{'conf':>7s}  link  target",
                 color=DIM, fontsize=11.5, va="top", family="monospace",
                 transform=ax_stat.transAxes)
    row_txt = [ax_stat.text(0.04, 0.93 - 0.115 * (i + 1), "", color=ACCENT[i % len(ACCENT)],
                            fontsize=11.5, va="top", family="monospace",
                            transform=ax_stat.transAxes)
               for i in range(len(ids))]
    stat_txt = ax_stat.text(0.04, 0.93 - 0.115 * (len(ids) + 1.6), "", color=TEXT,
                            fontsize=11.5, va="top", family="monospace",
                            transform=ax_stat.transAxes)
    log_txt = ax_log.text(0.03, 0.93, "", color=TEXT, fontsize=11, va="top",
                          family="monospace", transform=ax_log.transAxes)

    targets = rec["targets"]
    events = rec["events"]
    trail_len = trail_ticks

    def update(k):
        f = frames[k]
        for i, aid in enumerate(ids):
            a = f["agents"][aid]
            xs = [frames[j]["agents"][aid]["pos"][0]
                  for j in range(max(0, k - trail_len), k + 1)]
            ys = [frames[j]["agents"][aid]["pos"][1]
                  for j in range(max(0, k - trail_len), k + 1)]
            trails[i].set_data(xs, ys)
            heads[i].set_data([a["pos"][0]], [a["pos"][1]])
            rings[i].center = (a["pos"][0], a["pos"][1])
            side_pts[i].set_data([a["pos"][0]], [a["pos"][2]])
            if a["goal"] is not None:
                goal_lines[i].set_data([a["pos"][0], a["goal"][0]],
                                       [a["pos"][1], a["goal"][1]])
            else:
                goal_lines[i].set_data([], [])
        for li in link_lines:
            li.set_data([], [])
        for n, (x, y) in enumerate(f["links"][:len(link_lines)]):
            pa, pb = f["agents"][x]["pos"], f["agents"][y]["pos"]
            link_lines[n].set_data([pa[0], pb[0]], [pa[1], pb[1]])

        hollow = [t[:2] for t, fo in zip(targets, f["found"]) if not fo]
        done = [t[:2] for t, fo in zip(targets, f["found"]) if fo]
        tgt_hollow.set_offsets(np.array(hollow) if hollow else np.empty((0, 2)))
        tgt_found.set_offsets(np.array(done) if done else np.empty((0, 2)))

        clock.set_text(f"t = {f['t']:5.1f} s")
        for i, aid in enumerate(ids):
            a = f["agents"][aid]
            g = a["goal"]
            gs_ = "  --  " if g is None else f"({g[0]:+.1f},{g[1]:+.1f})"
            link = "solo" if a["isolated"] else " net"
            row_txt[i].set_text(f"{aid:7s}{a['pos'][2]:5.2f}m{a['speed']:6.2f}"
                                f"{a['conf']:7.2f}  {link}  {gs_}")
        nf = sum(f["found"])
        stat_txt.set_text(
            f"targets found   {nf}/{len(targets)}\n"
            f"reassignments   {f['reassign']}\n"
            f"comms links up  {len(f['links'])}/{len(ids)*(len(ids)-1)//2}")

        recent = [e for e in events if e[0] <= f["t"]][-9:]
        log_txt.set_text("\n".join(f"{t:5.1f}s  {m}" for t, m in recent) or "—")
        return []

    anim = FuncAnimation(fig, update, frames=len(frames), interval=1000 / fps, blit=False)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        anim.save(str(out), writer=FFMpegWriter(fps=fps, bitrate=6000,
                                                codec="libx264",
                                                extra_args=["-pix_fmt", "yuv420p"]))
    except Exception as exc:                      # pragma: no cover
        print(f"ffmpeg unavailable ({exc}); writing GIF instead")
        out = out.with_suffix(".gif")
        anim.save(str(out), writer=PillowWriter(fps=min(fps, 20)))
    plt.close(fig)
    return str(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--out", default="cf_team_demo.mp4")
    ap.add_argument("--interp", type=int, default=3,
                    help="sub-frames per 10 Hz tick; 3 gives smooth 30 fps at real speed")
    ap.add_argument("--fps", type=int, default=None,
                    help="output fps (default: real-time, i.e. interp / dt)")
    ap.add_argument("--max-s", type=float, default=None)
    args = ap.parse_args()

    rec = record(args.scenario, max_s=args.max_s)
    n = len(rec["frames"])
    sim_s = rec["frames"][-1]["t"]
    fps = args.fps or int(round(args.interp / 0.1))     # real-time playback
    print(f"recorded {n} ticks ({sim_s:.1f} s sim), "
          f"{sum(rec['frames'][-1]['found'])}/{len(rec['targets'])} targets found, "
          f"{rec['metrics']['reassignments']} reassignments")
    path = render(rec, args.out, fps=fps, interp=args.interp)
    print(f"wrote {path}  —  {n * args.interp} frames at {fps} fps "
          f"= {n * args.interp / fps:.1f} s (sim was {sim_s:.1f} s)")


if __name__ == "__main__":
    main()

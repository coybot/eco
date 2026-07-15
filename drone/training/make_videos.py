"""Render comparison videos: rule-based planner vs learned policy_v2.

Produces three MP4 files in --out-dir:
  1. topdown_all.mp4   — 2D top-down overlay of all 7 goals, both planners animated
  2. trajectories.mp4  — 3×3 grid, one cell per goal, xy-plane + speed trace
  3. smoothness.mp4    — side-by-side jerk / acceleration time-series for each goal

Run on hoopoe (has matplotlib + ffmpeg):
    cd ~/astral-training
    PYTHONPATH=. /opt/ml/isaac-sim-env/bin/python3 \
        -m eco.drone.training.make_videos \
        --models-dir eco/drone/models \
        --out-dir /tmp/astral_videos
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.collections import LineCollection
from matplotlib.patches import FancyArrowPatch
import matplotlib.gridspec as gridspec

_here = Path(__file__).parent
sys.path.insert(0, str(_here.parent / "common"))
sys.path.insert(0, str(_here))

from reactive_planner import make_planner        # noqa: E402
from contract import VEHICLE_QUAD, VEHICLE_ROVER, wrap_pi  # noqa: E402

DT = 0.1
MAX_TICKS = 3000
CLEARANCE = 8.0

GOALS = [
    (8.0,  0.0,  0.0,  VEHICLE_QUAD,  "quad: 8m fwd"),
    (8.0,  4.0,  0.0,  VEHICLE_QUAD,  "quad: diagonal"),
    (5.0, -3.0,  1.5,  VEHICLE_QUAD,  "quad: diag+climb"),
    (0.0,  6.0,  0.0,  VEHICLE_QUAD,  "quad: pure lateral"),
    (10.0, 0.0,  0.0,  VEHICLE_QUAD,  "quad: 10m fwd"),
    (4.0,  4.0,  0.0,  VEHICLE_ROVER, "rover: diagonal"),
    (8.0, -4.0,  0.0,  VEHICLE_ROVER, "rover: neg-diag"),
]

RULE_COLOR    = "#E87722"   # amber
LEARNED_COLOR = "#00B4D8"   # cyan
GOAL_COLOR    = "#90EE90"   # light green

# ── trajectory collector ────────────────────────────────────────────────────

def collect(planner, gx, gy, gz, vehicle):
    """Run planner and return dict of trajectory arrays."""
    x = y = 0.0
    z = 2.0 if vehicle == VEHICLE_QUAD else 0.0
    yaw = 0.0
    goal_alt = z + gz

    xs, ys, zs, yaws = [x], [y], [z], [yaw]
    vxs, vys, vzs, yrs = [], [], [], []
    reached = False

    if hasattr(planner, "vehicle"):
        planner.vehicle = float(vehicle)
    if hasattr(planner, "reset"):
        planner.reset()

    for _ in range(MAX_TICKS):
        dx, dy, dz = gx - x, gy - y, goal_alt - z
        c, s = math.cos(-yaw), math.sin(-yaw)
        tf, tl, tu = c*dx - s*dy, s*dx + c*dy, dz

        plan = planner.step((tf, tl, tu), CLEARANCE, altitude_m=z, dt=DT)

        bvx, bvy, bvz, yr = plan.vx, plan.vy, plan.vz, plan.yaw_rate
        vxs.append(bvx); vys.append(bvy); vzs.append(bvz); yrs.append(yr)

        cc, ss = math.cos(yaw), math.sin(yaw)
        x += (cc*bvx - ss*bvy) * DT
        y += (ss*bvx + cc*bvy) * DT
        z += bvz * DT
        yaw = wrap_pi(yaw + yr * DT)

        xs.append(x); ys.append(y); zs.append(z); yaws.append(yaw)

        if plan.reached:
            reached = True
            break

    vxs = np.array(vxs, dtype=np.float32)
    vys = np.array(vys, dtype=np.float32)
    vzs = np.array(vzs, dtype=np.float32)
    yrs = np.array(yrs, dtype=np.float32)
    speed = np.sqrt(vxs**2 + vys**2 + vzs**2)
    accel = np.diff(speed) / DT
    jerk  = np.diff(accel) / DT

    return dict(
        x=np.array(xs), y=np.array(ys), z=np.array(zs), yaw=np.array(yaws),
        vx=vxs, vy=vys, vz=vzs, yr=yrs, speed=speed,
        accel=accel, jerk=jerk, reached=reached,
    )


def collect_all(models_dir):
    rule_p    = make_planner(use_learned=False, reach_threshold=1.0, max_speed=3.0)
    learned_p = make_planner(use_learned=True,  models_dir=models_dir,
                             reach_threshold=1.0, max_speed=3.0)
    data = []
    for gx, gy, gz, vehicle, label in GOALS:
        r = collect(rule_p,    gx, gy, gz, vehicle)
        l = collect(learned_p, gx, gy, gz, vehicle)
        data.append((label, (gx, gy, gz), r, l))
    return data


# ── video 1: top-down all goals ──────────────────────────────────────────────

def make_topdown_video(data, out_path: Path, fps=30, speed_factor=4):
    """Animate all goals simultaneously, top-down XY view."""
    fig, axes = plt.subplots(3, 3, figsize=(13, 13), facecolor="#111")
    fig.suptitle("Rule Planner  vs  Learned Policy (policy_v2)", color="white",
                 fontsize=14, fontweight="bold")
    axes_flat = axes.flatten()
    # hide unused cell
    axes_flat[-1].set_visible(False)
    axes_flat[-2].set_visible(False)

    n = len(GOALS)
    rule_lines, learned_lines, arrows_r, arrows_l, goal_marks = [], [], [], [], []

    for i, (label, (gx, gy, gz), r, l) in enumerate(data):
        ax = axes_flat[i]
        ax.set_facecolor("#1a1a2e")
        ax.tick_params(colors="gray", labelsize=7)
        for spine in ax.spines.values():
            spine.set_edgecolor("#333")
        ax.set_title(label, color="white", fontsize=9, pad=3)

        margin = max(abs(gx), abs(gy), 2.0) * 1.3
        ax.set_xlim(-margin * 0.3, margin * 1.1)
        ax.set_ylim(-margin * 0.6, margin * 0.6)
        ax.set_aspect("equal")
        ax.axhline(0, color="#333", lw=0.5)
        ax.axvline(0, color="#333", lw=0.5)

        # full ghost trajectories (faded)
        ax.plot(r["x"], r["y"], color=RULE_COLOR,    alpha=0.15, lw=1)
        ax.plot(l["x"], l["y"], color=LEARNED_COLOR, alpha=0.15, lw=1)

        # goal star
        ax.plot(gx, gy, "*", color=GOAL_COLOR, ms=12, zorder=10)

        # animated heads
        rl,  = ax.plot([], [], "-", color=RULE_COLOR,    lw=2,   alpha=0.9, label="rule")
        ll,  = ax.plot([], [], "-", color=LEARNED_COLOR, lw=2,   alpha=0.9, label="learned")
        rpt, = ax.plot([], [], "o", color=RULE_COLOR,    ms=6,   zorder=5)
        lpt, = ax.plot([], [], "o", color=LEARNED_COLOR, ms=6,   zorder=5)
        rule_lines.append((rl, rpt))
        learned_lines.append((ll, lpt))

        if i == 0:
            ax.legend(loc="lower right", fontsize=7, facecolor="#111",
                      edgecolor="#444", labelcolor="white")
        ax.set_xlabel("fwd (m)", color="gray", fontsize=7)
        ax.set_ylabel("left (m)", color="gray", fontsize=7)

    # find max ticks across all trajectories for animation length
    max_t = max(
        max(len(r["x"]), len(l["x"]))
        for _, _, r, l in data
    )
    n_frames = max_t // speed_factor + 1

    def init():
        for rl, rpt in rule_lines:
            rl.set_data([], [])
            rpt.set_data([], [])
        for ll, lpt in learned_lines:
            ll.set_data([], [])
            lpt.set_data([], [])
        return [a for pair in rule_lines + learned_lines for a in pair]

    def update(frame):
        t = frame * speed_factor
        artists = []
        for i, (_, _, r, l) in enumerate(data):
            rl, rpt = rule_lines[i]
            ll, lpt = learned_lines[i]

            tr = min(t, len(r["x"]) - 1)
            tl = min(t, len(l["x"]) - 1)

            rl.set_data(r["x"][:tr+1], r["y"][:tr+1])
            rpt.set_data([r["x"][tr]], [r["y"][tr]])
            ll.set_data(l["x"][:tl+1], l["y"][:tl+1])
            lpt.set_data([l["x"][tl]], [l["y"][tl]])
            artists += [rl, rpt, ll, lpt]
        return artists

    ani = animation.FuncAnimation(
        fig, update, frames=n_frames, init_func=init, blit=True, interval=1000/fps
    )
    writer = animation.FFMpegWriter(fps=fps, bitrate=2000,
                                    extra_args=["-vcodec", "libx264", "-pix_fmt", "yuv420p"])
    ani.save(str(out_path), writer=writer)
    plt.close(fig)
    print(f"  saved {out_path}")


# ── video 2: speed + jerk panels ────────────────────────────────────────────

def make_smoothness_video(data, out_path: Path, fps=30, speed_factor=4):
    """Side-by-side speed and jerk traces for each goal."""
    fig, axes = plt.subplots(len(data), 2, figsize=(12, 2.0 * len(data)),
                             facecolor="#111")
    fig.suptitle("Speed and Jerk: Rule (amber) vs Learned (cyan)", color="white",
                 fontsize=13, fontweight="bold")

    lines = []
    for i, (label, (gx, gy, gz), r, l) in enumerate(data):
        ax_spd = axes[i, 0]
        ax_jrk = axes[i, 1]
        t_r = np.arange(len(r["speed"])) * DT
        t_l = np.arange(len(l["speed"])) * DT
        t_jr = np.arange(len(r["jerk"])) * DT
        t_jl = np.arange(len(l["jerk"])) * DT

        for ax in (ax_spd, ax_jrk):
            ax.set_facecolor("#1a1a2e")
            ax.tick_params(colors="gray", labelsize=7)
            for spine in ax.spines.values():
                spine.set_edgecolor("#333")

        ax_spd.set_xlim(0, max(t_r[-1], t_l[-1]) * 1.02)
        ax_spd.set_ylim(-0.2, 3.8)
        ax_spd.set_ylabel(label, color="white", fontsize=7, labelpad=2)
        ax_spd.set_xlabel("time (s)", color="gray", fontsize=7)

        jmax = max(np.abs(r["jerk"]).max(), np.abs(l["jerk"]).max(), 1.0) * 1.1
        ax_jrk.set_xlim(0, max(t_jr[-1], t_jl[-1]) * 1.02)
        ax_jrk.set_ylim(-jmax, jmax)
        ax_jrk.axhline(0, color="#444", lw=0.5)
        ax_jrk.set_xlabel("time (s)", color="gray", fontsize=7)

        if i == 0:
            ax_spd.set_title("Speed (m/s)", color="white", fontsize=8)
            ax_jrk.set_title("Jerk (m/s³)", color="white", fontsize=8)

        # ghost full traces
        ax_spd.plot(t_r, r["speed"], color=RULE_COLOR,    alpha=0.12, lw=1)
        ax_spd.plot(t_l, l["speed"], color=LEARNED_COLOR, alpha=0.12, lw=1)
        ax_jrk.plot(t_jr, r["jerk"], color=RULE_COLOR,    alpha=0.12, lw=1)
        ax_jrk.plot(t_jl, l["jerk"], color=LEARNED_COLOR, alpha=0.12, lw=1)

        rsl, = ax_spd.plot([], [], color=RULE_COLOR,    lw=1.5, alpha=0.9)
        lsl, = ax_spd.plot([], [], color=LEARNED_COLOR, lw=1.5, alpha=0.9)
        rjl, = ax_jrk.plot([], [], color=RULE_COLOR,    lw=1.5, alpha=0.9)
        ljl, = ax_jrk.plot([], [], color=LEARNED_COLOR, lw=1.5, alpha=0.9)
        lines.append((rsl, lsl, rjl, ljl, t_r, t_l, t_jr, t_jl,
                       r["speed"], l["speed"], r["jerk"], l["jerk"]))

    plt.tight_layout(rect=[0, 0, 1, 0.96])

    max_t = max(
        max(len(r["speed"]), len(l["speed"]))
        for _, _, r, l in data
    )
    n_frames = max_t // speed_factor + 1

    def init():
        artists = []
        for rsl, lsl, rjl, ljl, *_ in lines:
            for a in (rsl, lsl, rjl, ljl):
                a.set_data([], [])
                artists.append(a)
        return artists

    def update(frame):
        t = frame * speed_factor
        artists = []
        for (rsl, lsl, rjl, ljl,
             t_r, t_l, t_jr, t_jl,
             spd_r, spd_l, jrk_r, jrk_l) in lines:
            tr = min(t, len(spd_r) - 1)
            tl = min(t, len(spd_l) - 1)
            tjr = min(t, len(jrk_r) - 1)
            tjl = min(t, len(jrk_l) - 1)
            rsl.set_data(t_r[:tr+1], spd_r[:tr+1])
            lsl.set_data(t_l[:tl+1], spd_l[:tl+1])
            rjl.set_data(t_jr[:tjr+1], jrk_r[:tjr+1])
            ljl.set_data(t_jl[:tjl+1], jrk_l[:tjl+1])
            artists += [rsl, lsl, rjl, ljl]
        return artists

    ani = animation.FuncAnimation(
        fig, update, frames=n_frames, init_func=init, blit=True, interval=1000/fps
    )
    writer = animation.FFMpegWriter(fps=fps, bitrate=2000,
                                    extra_args=["-vcodec", "libx264", "-pix_fmt", "yuv420p"])
    ani.save(str(out_path), writer=writer)
    plt.close(fig)
    print(f"  saved {out_path}")


# ── video 3: 3D trajectory ───────────────────────────────────────────────────

def make_3d_video(data, out_path: Path, fps=30, speed_factor=4):
    """Animated 3D trajectories for all goals."""
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

    n_goals = len(data)
    ncols = 4
    nrows = math.ceil(n_goals / ncols)
    fig = plt.figure(figsize=(ncols * 4, nrows * 4), facecolor="#111")
    fig.suptitle("3D Trajectories: Rule (amber) vs Learned (cyan)",
                 color="white", fontsize=13, fontweight="bold")

    axs, rule_lines, learned_lines = [], [], []

    for i, (label, (gx, gy, gz), r, l) in enumerate(data):
        ax = fig.add_subplot(nrows, ncols, i + 1, projection="3d")
        ax.set_facecolor("#1a1a2e")
        ax.xaxis.pane.fill = ax.yaxis.pane.fill = ax.zaxis.pane.fill = False
        ax.tick_params(colors="gray", labelsize=6)
        ax.set_title(label, color="white", fontsize=8, pad=2)

        goal_alt = (2.0 if (GOALS[i][3] == VEHICLE_QUAD) else 0.0) + gz
        ax.scatter([gx], [gy], [goal_alt], color=GOAL_COLOR, s=60, zorder=10)

        # ghost
        ax.plot(r["x"], r["y"], r["z"], color=RULE_COLOR,    alpha=0.1, lw=1)
        ax.plot(l["x"], l["y"], l["z"], color=LEARNED_COLOR, alpha=0.1, lw=1)

        rl, = ax.plot([], [], [], "-", color=RULE_COLOR,    lw=2)
        ll, = ax.plot([], [], [], "-", color=LEARNED_COLOR, lw=2)
        rule_lines.append(rl)
        learned_lines.append(ll)
        axs.append(ax)

    plt.tight_layout(rect=[0, 0, 1, 0.94])

    max_t = max(
        max(len(r["x"]), len(l["x"]))
        for _, _, r, l in data
    )
    n_frames = max_t // speed_factor + 1

    def init():
        for rl, ll in zip(rule_lines, learned_lines):
            rl.set_data([], []); rl.set_3d_properties([])
            ll.set_data([], []); ll.set_3d_properties([])
        return rule_lines + learned_lines

    def update(frame):
        t = frame * speed_factor
        for i, (_, _, r, l) in enumerate(data):
            tr = min(t, len(r["x"]) - 1)
            tl = min(t, len(l["x"]) - 1)
            rule_lines[i].set_data(r["x"][:tr+1], r["y"][:tr+1])
            rule_lines[i].set_3d_properties(r["z"][:tr+1])
            learned_lines[i].set_data(l["x"][:tl+1], l["y"][:tl+1])
            learned_lines[i].set_3d_properties(l["z"][:tl+1])
        return rule_lines + learned_lines

    ani = animation.FuncAnimation(
        fig, update, frames=n_frames, init_func=init, blit=False, interval=1000/fps
    )
    writer = animation.FFMpegWriter(fps=fps, bitrate=2000,
                                    extra_args=["-vcodec", "libx264", "-pix_fmt", "yuv420p"])
    ani.save(str(out_path), writer=writer)
    plt.close(fig)
    print(f"  saved {out_path}")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--out-dir", default="/tmp/astral_videos")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--speed", type=int, default=4,
                    help="Playback speed multiplier (default 4 = 4× real time).")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    print("Collecting trajectories...")
    data = collect_all(args.models_dir)
    print(f"  {len(data)} goals collected")

    print("Rendering topdown_all.mp4...")
    make_topdown_video(data, out / "topdown_all.mp4", fps=args.fps, speed_factor=args.speed)

    print("Rendering smoothness.mp4...")
    make_smoothness_video(data, out / "smoothness.mp4", fps=args.fps, speed_factor=args.speed)

    print("Rendering trajectories_3d.mp4...")
    make_3d_video(data, out / "trajectories_3d.mp4", fps=args.fps, speed_factor=args.speed)

    print(f"\nAll videos written to {out}/")


if __name__ == "__main__":
    main()

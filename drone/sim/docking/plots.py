"""Plots and clips for the docking reports. Needs matplotlib (not a simulator dependency):

    <python-with-matplotlib> drone/sim/docking/plots.py phase1|phase2|phase3|phase4|clips

Reads the result CSVs under results/ and writes PNGs (and MP4 clips) next to them.
Colours: the validated categorical palette, in fixed order; thin marks; one y-axis.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"

plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "axes.edgecolor": INK2, "axes.labelcolor": INK,
    "xtick.color": INK2, "ytick.color": INK2, "text.color": INK, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 2.0,
    "font.size": 10, "axes.titlesize": 11, "legend.frameon": False, "savefig.dpi": 140,
})


def rows(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def save(fig, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    print("wrote", path)


# ------------------------------------------------------------------ phase 1
def phase1():
    d = os.path.join(RES, "phase1")
    R = rows(os.path.join(d, "turbulence_psd.csv"))
    f = np.array([fnum(r["freq_hz"]) for r in R])
    sel = f > 0
    fig, axs = plt.subplots(1, 2, figsize=(9, 3.6), sharey=False)
    for ax, comp, title in ((axs[0], "u", "Longitudinal (u)"), (axs[1], "w", "Vertical (w)")):
        P = np.array([fnum(r[f"psd_{comp}_m2_s"]) for r in R])
        T = np.array([fnum(r[f"psd_{comp}_theory_m2_s"]) for r in R])
        # third-octave averages of the simulated spectrum
        edges = 0.005 * 2 ** (np.arange(0, 40) / 3.0)
        xc, yc = [], []
        for lo, hi in zip(edges[:-1], edges[1:]):
            m = (f >= lo) & (f < hi)
            if m.sum():
                xc.append(np.sqrt(lo * hi))
                yc.append(P[m].mean())
        ax.loglog(f[sel], T[sel], color=SERIES[1], label="von Karman (theory)")
        ax.loglog(xc, yc, "o", color=SERIES[0], ms=4, label="simulated, third-octave mean")
        ax.set_xlim(0.01, 8)
        ax.set_xlabel("frequency (Hz) at 20 m/s")
        ax.set_title(title)
    axs[0].set_ylabel("PSD (m^2/s^2 per Hz)")
    axs[0].legend(loc="lower left")
    save(fig, os.path.join(d, "plots", "turbulence_psd.png"))


# ------------------------------------------------------------------ phase 2
def phase2():
    d = os.path.join(RES, "phase2")
    R = rows(os.path.join(d, "min_safe_sweep.csv"))
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    for ax, w20, title in ((axs[0], "7.7", "Light turbulence (W20 15 kt)"), (axs[1], "15.4", "Moderate turbulence (W20 30 kt)")):
        for i, n in enumerate(("2", "1", "0")):
            sub = [r for r in R if r["clmax_case_label"] == "baseline" and r["wind20_m_s"] == w20 and r["quads_count"] == n]
            by = defaultdict(list)
            for r in sub:
                by[fnum(r["airspeed_cmd_m_s"])].append(fnum(r["alpha_max_deg"]))
            V = sorted(by)
            ax.plot(V, [max(by[v]) for v in V], color=SERIES[i], label=f"{n} quads aboard")
        amax = fnum(sub[0]["alpha_clmax_deg"])
        ax.axhline(amax, color=INK2, lw=1, ls="--")
        ax.text(29.8, amax + 0.3, "CLmax angle", ha="right", color=INK2, fontsize=9)
        ax.axhline(amax - 3, color=INK2, lw=1, ls=":")
        ax.text(29.8, amax - 3 + 0.3, "safe limit (3 deg margin)", ha="right", color=INK2, fontsize=9)
        ax.set_ylim(0, 25)
        ax.set_xlabel("commanded airspeed (m/s)")
        ax.set_title(title)
    axs[0].set_ylabel("peak angle of attack, worst of 10 seeds (deg)")
    axs[0].legend(loc="upper right")
    save(fig, os.path.join(d, "plots", "min_safe_speed_sweep.png"))


# ------------------------------------------------------------------ phase 3
def bar_experiment(exp, title, ylabel="capture rate within 3 attempts", metric="capture_frac"):
    s = json.load(open(os.path.join(RES, "phase3", "summary.json")))[exp]
    names = list(s)
    vals = [s[n][metric] for n in names]
    strikes = [s[n]["strikes_count"] for n in names]
    fig, ax = plt.subplots(figsize=(max(5, 1.3 * len(names) + 1.5), 3.6))
    x = np.arange(len(names))
    ax.bar(x, vals, width=0.6, color=SERIES[0], edgecolor=SURF, linewidth=2)
    for xi, v, st in zip(x, vals, strikes):
        ax.text(xi, v + 0.02, f"{v:.0%}" + (f"\n{st} strike(s)" if st else ""), ha="center", va="bottom", fontsize=9, color=INK)
    ax.set_xticks(x)
    ax.set_xticklabels([n.replace("_", "\n") for n in names], fontsize=8)
    ax.set_ylim(0, 1.25)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="x", visible=False)
    save(fig, os.path.join(RES, "phase3", "plots", f"{exp}.png"))


def phase3():
    titles = {
        "E1_dock_pitch": "H1: dock pitched to the quad's tilt vs levelling out",
        "E2_location_faults": "H2: dock under vs on top, nominal and with faults",
        "E3_probe_compliance": "Gimballed vs rigid probe",
        "E4_mouth_radius": "Funnel mouth radius",
        "E4b_cone_half_angle": "Funnel cone half-angle",
        "E5_relnav_source": "Relative navigation source",
        "E6_disturbance_observer": "Disturbance observer",
        "E7_mothership_speed": "Mothership airspeed during docking",
        "E7b_mothership_stiffness": "Mothership altitude/speed loop stiffness",
        "E8_thrust_margin": "Quad thrust margin options",
        "E9_second_quad_clearance": "Front dock with the rear dock occupied",
        "E10_moderate_turbulence": "Moderate turbulence at 23 m/s",
    }
    s = json.load(open(os.path.join(RES, "phase3", "summary.json")))
    for e in s:
        bar_experiment(e, titles.get(e, e))


# ------------------------------------------------------------------ phase 4
def phase4():
    d = os.path.join(RES, "phase4")
    S = json.load(open(os.path.join(d, "summary.json")))
    for key, xlabel in (("sweep_airspeed", "mothership airspeed (m/s)"), ("sweep_gust", "W20 (m/s)"),
                        ("sweep_sensor_delay", "sensor delay scale (x nominal)"), ("sweep_mouth_radius", "funnel mouth radius (m)"),
                        ("wake_margin", "near-body flow gain (x nominal, unvalidated)")):
        if key not in S:
            continue
        pts = S[key]
        xs = [p["x_nd"] for p in pts]
        fig, ax = plt.subplots(figsize=(5.2, 3.4))
        ax.plot(xs, [p["capture_frac"] for p in pts], "o-", color=SERIES[0], ms=6, label="captured within 3 attempts")
        ax.axhline(0.95, color=INK2, lw=1, ls="--")
        ax.text(xs[0], 0.955, "95 % pass line", color=INK2, fontsize=9)
        for p in pts:
            if p["strikes_count"]:
                ax.annotate(f"{p['strikes_count']} strike", (p["x_nd"], p["capture_frac"]), textcoords="offset points",
                            xytext=(0, -14), ha="center", fontsize=8, color=INK)
        ax.set_ylim(0, 1.05)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("capture rate")
        save(fig, os.path.join(d, "plots", f"{key}.png"))
    mc = rows(os.path.join(d, "monte_carlo.csv"))
    t = [fnum(r["time_to_capture_s"]) for r in mc if r["captured_bool"] == "1"]
    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    ax.hist(t, bins=30, color=SERIES[0], edgecolor=SURF, linewidth=1)
    ax.set_xlabel("time from station to latch (s)")
    ax.set_ylabel("trials")
    ax.set_title(f"Monte Carlo: {len(t)} captures of {len(mc)} trials")
    save(fig, os.path.join(d, "plots", "mc_time_to_capture.png"))
    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    ax.hist([fnum(r["peak_contact_force_n"]) for r in mc], bins=30, color=SERIES[0], edgecolor=SURF, linewidth=1)
    ax.set_xlabel("peak funnel contact force (N)")
    ax.set_ylabel("trials")
    save(fig, os.path.join(d, "plots", "mc_peak_contact_force.png"))


# ------------------------------------------------------------------ clips
def clip(record, path, title):
    import matplotlib.animation as anim
    from matplotlib.patches import Polygon, Rectangle
    rws = record["rows"]
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    g = record["geom"]
    # mothership side view (x forward, z up), body frame
    f = g["fuselage"]
    ax.add_patch(Rectangle((f["center_x_m"] - f["length_m"] / 2, f["center_z_m"] - f["height_m"] / 2), f["length_m"], f["height_m"],
                           fc="#d9d8d3", ec=INK2, lw=1))
    w = g["wing"]
    ax.add_patch(Rectangle((w["center_x_m"] - w["chord_m"] / 2, w["center_z_m"] - 0.02), w["chord_m"], 0.04, fc="#bdbcb6", ec=INK2, lw=1))
    v = g["ventral_fin"]
    ax.add_patch(Rectangle((v["x_m"] - v["chord_m"] / 2, v["bottom_z_m"]), v["chord_m"], v["top_z_m"] - v["bottom_z_m"], fc="#e34948", alpha=0.5, ec=INK2))
    t = g["tailplane"]
    ax.add_patch(Rectangle((t["x_m"] - t["chord_m"] / 2, t["z_m"] - 0.015), t["chord_m"], 0.03, fc="#e34948", alpha=0.5, ec=INK2))
    pu = g["pusher"]
    ax.plot([pu["x_m"], pu["x_m"]], [pu["z_m"] - pu["diameter_m"] / 2, pu["z_m"] + pu["diameter_m"] / 2], color="#e34948", lw=3)
    lr = g["lift_rotors"]
    for x in (lr["front_x_m"], lr["rear_x_m"]):
        ax.plot([x - lr["diameter_m"] / 2, x + lr["diameter_m"] / 2], [lr["z_m"]] * 2, color="#e34948", lw=3)
    fn = record["funnel"]
    ax.add_patch(Polygon(fn, closed=True, fc="#1baf7a", alpha=0.35, ec="#1baf7a"))
    ax.text(-1.05, 0.33, "red = strike parts", color=INK2, fontsize=8)
    (body,) = ax.plot([], [], "o", ms=14, mfc="none", mec=SERIES[0], mew=2)
    (probe,) = ax.plot([], [], "-", color=SERIES[0], lw=2)
    (trail,) = ax.plot([], [], "-", color=SERIES[0], lw=1, alpha=0.4)
    txt = ax.text(0.02, 0.95, "", transform=ax.transAxes, va="top", fontsize=9)
    ax.set_xlim(-2.6, 1.2)
    ax.set_ylim(-3.2, 0.6)
    ax.set_aspect("equal")
    ax.set_xlabel("forward (m), mothership frame")
    ax.set_ylabel("up (m)")
    ax.set_title(title)

    def upd(i):
        r = rws[i]
        body.set_data([r["x_rel_m"]], [r["z_rel_m"]])
        probe.set_data([r["x_rel_m"], r["tip_x_rel_m"]], [r["z_rel_m"], r["tip_z_rel_m"]])
        k = max(0, i - 150)
        trail.set_data([q["x_rel_m"] for q in rws[k:i + 1]], [q["z_rel_m"] for q in rws[k:i + 1]])
        txt.set_text(f"t = {r['t_s']:.1f} s   {r['state_label']}")
        return body, probe, trail, txt
    a = anim.FuncAnimation(fig, upd, frames=len(rws), interval=40, blit=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    a.save(path, writer=anim.FFMpegWriter(fps=25, bitrate=1800))
    plt.close(fig)
    print("wrote", path)


def clips():
    d = os.path.join(RES, "phase4", "clips")
    for name in os.listdir(d):
        if name.endswith(".json"):
            rec = json.load(open(os.path.join(d, name)))
            clip(rec, os.path.join(d, name.replace(".json", ".mp4")), rec["title"])


if __name__ == "__main__":
    {"phase1": phase1, "phase2": phase2, "phase3": phase3, "phase4": phase4, "clips": clips}[sys.argv[1]]()

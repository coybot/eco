"""Dimensioned drawing of the channel dock variant, from the values in docking/vehicles.yaml.

    python docking/figures/draw_channel_schematic.py      # writes docking/figures/channel_dock_drawing.png
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Rectangle

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from docksim.config import channel_geometry, effective_cfg, load_cfg   # noqa: E402

cfg = load_cfg()
cfg["channel_dock"]["enabled"] = True
cfg = effective_cfg(cfg)
ch, b = cfg["channel_dock"], cfg["bee35"]
g = channel_geometry(cfg)
L = b["size_m"][0]; H = b["size_m"][1]
iw = ch["inner_half_width_m"]; iu = iw + ch["upper_extra_clearance_m"]; wt = ch["wall_thickness_m"]
rh = ch["rail_height_m"]; hw = ch["wall_height_m"]; mw = ch["mouth_half_width_m"]
xf = ch["front_stop_x_m"]; xe, xm = g["x_entry"], g["x_mouth"]; slots = g["slots"]
rb, yb = ch["nose_bar_radius_m"], ch["nose_bar_y_m"]; ptop = ch["bumper_top_y_m"]; yin = ch["entry_y_m"]

DOCK, WALL, DRONE, PLATE, AIR = "#c9d3df", "#7f93ad", "#33363b", "#e07b1a", "#ffffff"
INK = "#222222"


def dim(ax, p0, p1, text, off=(0, 0), fs=9, rot=0):
    ax.annotate("", xy=p0, xytext=p1, arrowprops=dict(arrowstyle="<->", color=INK, lw=0.8, shrinkA=0, shrinkB=0))
    ax.text((p0[0] + p1[0]) / 2 + off[0], (p0[1] + p1[1]) / 2 + off[1], text, ha="center", va="center", fontsize=fs,
            color=INK, rotation=rot, bbox=dict(fc="white", ec="none", pad=0.6))


def drone_top(ax, x, z, alpha=1.0, ls="-"):
    ax.add_patch(Rectangle((x - L / 2, z - L / 2), L, L, fc=DRONE, ec=INK, lw=0.6, alpha=alpha, ls=ls))
    ax.add_patch(Rectangle((x - L / 2 - 0.003, z - 0.09), 0.003, 0.18, fc=PLATE, ec="none", alpha=alpha))  # rear plate
    ax.add_patch(Rectangle((x + L / 2, z - (L / 2 - 0.02)), 2 * rb, L - 0.04, fc=PLATE, ec="none", alpha=alpha))  # nose bar
    for sx in (-1, 1):
        for sz in (-1, 1):
            ax.add_patch(plt.Circle((x + sx * 0.0575, z + sz * 0.0575), 0.0445, fc="#d9d9d9", ec="none", alpha=0.9 * alpha))


fig = plt.figure(figsize=(14, 10.5))
gs = fig.add_gridspec(2, 2, height_ratios=[1.05, 1], width_ratios=[1.6, 1], hspace=0.28, wspace=0.12)
fig.suptitle("Channel dock variant: dimensions as simulated (metres; nose to the right)", fontsize=14, x=0.02, ha="left")

# ---- plan view
ax = fig.add_subplot(gs[0, :])
ax.set_title("Plan view", loc="left", fontsize=11)
ax.add_patch(Polygon([(xm, -mw - wt), (xe, -iu - wt), (xf, -iu - wt), (xf, iu + wt), (xe, iu + wt), (xm, mw + wt)],
                     fc=DOCK, ec="none"))
for s in (1, -1):
    ax.add_patch(Polygon([(xm, s * mw), (xe, s * iu), (xe, s * (iu + wt)), (xm, s * (mw + wt))], fc=WALL, ec=INK, lw=0.6))
    ax.add_patch(Rectangle((xe, s * iu if s > 0 else -iu - wt), xf - xe, wt, fc=WALL, ec=INK, lw=0.6))
    ax.add_patch(Rectangle((xe, s * iw if s > 0 else -iu), xf - xe, iu - iw, fc="#9fb0c6", ec="none"))  # guide rail
ax.add_patch(Rectangle((xf, -iu - wt), 0.012, 2 * (iu + wt), fc=WALL, ec=INK, lw=0.6))
for x in slots:
    ax.add_patch(Rectangle((x - 0.065, -0.065), 0.13, 0.13, fc="white", ec="#888888", lw=0.6))
drone_top(ax, slots[0], 0); drone_top(ax, slots[1], 0)
drone_top(ax, xm + 0.15, 0.06, alpha=0.55, ls="--")
ax.annotate("", xy=(xe + 0.02, 0.0), xytext=(xm + 0.28, 0.05), arrowprops=dict(arrowstyle="->", color=PLATE, lw=2))
ax.text(xm + 0.15, -0.16, "arriving drone,\nflying in at 0.10 m", ha="center", fontsize=9)
dim(ax, (xm - 0.06, -mw), (xm - 0.06, mw), f"mouth {2 * mw:.2f}", rot=90, off=(-0.0, 0))
dim(ax, (xm, -mw - 0.08), (xe, -mw - 0.08), f"funnel {xe - xm:.2f}")
dim(ax, (xe, -mw - 0.08), (xf, -mw - 0.08), f"lane {xf - xe:.2f}")
dim(ax, (slots[1], 0.17), (slots[0], 0.17), f"slot pitch {slots[0] - slots[1]:.2f}")
dim(ax, (xf + 0.06, -iu), (xf + 0.06, iu), f"{2 * iu:.3f} upper\n{2 * iw:.3f} at the rail", rot=0, off=(0.1, 0), fs=8)
ax.text(xf + 0.02, -iu - 0.05, "front stop", fontsize=9)
ax.text(slots[0], -0.16, "P1", ha="center", fontsize=9); ax.text(slots[1], -0.16, "P2", ha="center", fontsize=9)
ax.text(slots[2], -0.16, "P3", ha="center", fontsize=9)
ax.text((slots[2] + slots[3]) / 2, -0.21, "empty slots show their box wells", ha="center", fontsize=8)
ax.text(slots[3], -0.16, "P4", ha="center", fontsize=9)
ax.set_xlim(xm - 0.15, xf + 0.32); ax.set_ylim(-mw - 0.14, mw + 0.06); ax.set_aspect("equal"); ax.axis("off")

# ---- side elevation
ax = fig.add_subplot(gs[1, 0])
ax.set_title("Side view along the lane", loc="left", fontsize=11)
ax.add_patch(Rectangle((xm, -0.006), xf - xm + 0.012, 0.006, fc=DOCK, ec=INK, lw=0.6))
ax.add_patch(Polygon([(xm, 0), (xe, 0), (xe, hw), (xm, hw)], fc=WALL, ec=INK, lw=0.6, alpha=0.25))
ax.add_patch(Rectangle((xe, 0), xf - xe, hw, fc=WALL, ec=INK, lw=0.6, alpha=0.35))
ax.add_patch(Rectangle((xf, 0), 0.012, hw, fc=WALL, ec=INK, lw=0.6))
for x, y, a in ((slots[0], 0, 1), (slots[1], 0, 1), (slots[2] - 0.03, yin, 0.6)):
    ax.add_patch(Rectangle((x - L / 2, y + 0.002), L, H - 0.002, fc=DRONE, ec=INK, lw=0.6, alpha=a))
    ax.add_patch(Rectangle((x - L / 2, y + H), 0.006, ptop - H, fc=PLATE, ec="none", alpha=a))
    ax.add_patch(plt.Circle((x + L / 2 + rb, y + yb), rb, fc=PLATE, ec="none", alpha=a))
ax.annotate("", xy=(slots[1] - L / 2 - 0.13, yin + 0.03), xytext=(slots[2] - 0.2, yin + 0.03),
            arrowprops=dict(arrowstyle="->", color=PLATE, lw=2))
dim(ax, (xf + 0.04, 0), (xf + 0.04, hw), f"walls {hw:.2f}", rot=90, off=(0.025, 0))
dim(ax, (slots[0] - L / 2 + 0.03, 0), (slots[0] - L / 2 + 0.03, ptop), f"rear plate {ptop:.2f}", rot=90, off=(0.022, 0))
dim(ax, (slots[2] - 0.03 - L / 2 - 0.03, 0), (slots[2] - 0.03 - L / 2 - 0.03, yin), f"{yin:.2f}", rot=90, off=(-0.02, 0))
ax.text(slots[0], -0.04, "nose bar at 0.015 m", ha="center", fontsize=8)
ax.text(slots[2] - 0.03, yin + 0.08, "arriving", ha="center", fontsize=9)
ax.set_xlim(xe - 0.12, xf + 0.12); ax.set_ylim(-0.06, 0.29); ax.set_aspect("equal"); ax.axis("off")

# ---- cross section
ax = fig.add_subplot(gs[1, 1])
ax.set_title("Cross-section across the lane", loc="left", fontsize=11)
ax.add_patch(Rectangle((-iu - wt - 0.01, -0.006), 2 * (iu + wt + 0.01), 0.006, fc=DOCK, ec=INK, lw=0.6))
for s in (1, -1):
    ax.add_patch(Polygon([(s * iw, 0), (s * iw, rh), (s * iu, rh + (iu - iw)), (s * iu, hw), (s * (iu + wt), hw),
                          (s * (iu + wt), 0)], fc=WALL, ec=INK, lw=0.6))
ax.add_patch(Rectangle((-0.065, -0.006), 0.13, 0.006, fc="white", ec="#888888", lw=0.6))
ax.add_patch(Rectangle((-L / 2, 0.002), L, H - 0.002, fc=DRONE, ec=INK, lw=0.6))
ax.add_patch(Rectangle((-L / 2, yin + 0.002), L, H - 0.002, fc="none", ec=INK, lw=0.9, ls="--"))
ax.plot([b["down_sensor_pos_m"][2]] * 2, [0.0, -0.0], "o", color=PLATE, ms=4)
dim(ax, (-iw, 0.015), (iw, 0.015), f"{2 * iw:.3f}", fs=8, off=(0, 0.012))
dim(ax, (-iu, 0.15), (iu, 0.15), f"{2 * iu:.3f}", fs=8, off=(0, 0.012))
dim(ax, (iu + wt + 0.015, 0), (iu + wt + 0.015, rh), f"rail {rh:.2f}", fs=8, rot=90, off=(0.018, 0))
ax.text(0, yin + H + 0.012, "flying in (0.10 m)", ha="center", fontsize=8)
ax.text(0, -0.03, "box well", ha="center", fontsize=8)
ax.text(b["down_sensor_pos_m"][2], -0.05, "down sensor", ha="center", fontsize=8, color=PLATE)
ax.set_xlim(-0.17, 0.2); ax.set_ylim(-0.065, 0.25); ax.set_aspect("equal"); ax.axis("off")

out = os.path.join(HERE, "channel_dock_drawing.png")
fig.savefig(out, dpi=130, bbox_inches="tight")
print(out)

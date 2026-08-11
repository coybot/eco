"""Overhead renderer for autonomy scorecard scenarios.

Pure matplotlib — no GPU, no Isaac required. Produces per-scenario MP4s that
can be used as proof of fleet behavior. Pairs with scorecard.py's --record-video flag.

Usage (from scorecard CLI):
    python -m drone.sim.scorecard --rover-policy policy.onnx --record-video ./videos/
"""
from __future__ import annotations

import io
import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .team_world import Agent, Box


# agent colours by type + status
_COLORS = {
    ("quad", True):   "#4c9be8",   # blue: live quad
    ("quad", False):  "#c0392b",   # red: dead quad
    ("rover", True):  "#27ae60",   # green: live rover
    ("rover", False): "#e67e22",   # orange: dead rover
    ("intruder", True): "#8e44ad", # purple: intruder
}


class OverheadRenderer:
    """Matplotlib overhead renderer. Call reset() before each scenario, then
    capture_frame() once per tick, then save_mp4() at the end."""

    def __init__(self, world_extent: float = 35.0, figsize: tuple = (8, 8),
                 trail_s: float = 5.0, fps: int = 20):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        self._plt = plt
        self._mpatches = mpatches
        self._extent = world_extent
        self._figsize = figsize
        self._trail_s = trail_s
        self._fps = fps
        self._fig = None
        self._ax = None
        self._scenario_name = ""
        self._obstacles: list = []
        self._trails: dict[str, list] = {}   # agent_id → [(x,y,t), ...]
        self._dt = 0.1

    def reset(self, scenario_name: str, obstacles: list) -> None:
        """Call before each new scenario run."""
        if self._fig is not None:
            self._plt.close(self._fig)
        self._scenario_name = scenario_name
        self._obstacles = obstacles
        self._trails = {}
        self._fig, self._ax = self._plt.subplots(figsize=self._figsize, facecolor="#1a1a2e")
        self._ax.set_facecolor("#1a1a2e")
        e = self._extent
        self._ax.set_xlim(-e, e)
        self._ax.set_ylim(-e, e)
        self._ax.set_aspect("equal")
        self._ax.axis("off")

    def capture_frame(self, agents: list, interventions: int, t: float) -> np.ndarray:
        """Render one tick to an RGB numpy array (H×W×3, uint8)."""
        ax = self._ax
        ax.clear()
        ax.set_facecolor("#1a1a2e")
        e = self._extent
        ax.set_xlim(-e, e)
        ax.set_ylim(-e, e)
        ax.set_aspect("equal")
        ax.axis("off")

        # Obstacles
        for box in self._obstacles:
            cx, cy = float(box.center[0]), float(box.center[1])
            hx, hy = float(box.half[0]), float(box.half[1])
            rect = self._mpatches.FancyBboxPatch(
                (cx - hx, cy - hy), 2 * hx, 2 * hy,
                boxstyle="square,pad=0", linewidth=0,
                facecolor="#4a4a5a", alpha=0.8)
            ax.add_patch(rect)

        trail_window = self._trail_s / self._dt

        for agent in agents:
            aid = agent.id
            px, py = float(agent.pos[0]), float(agent.pos[1])
            is_team = getattr(agent, "team", True)
            is_alive = getattr(agent, "alive", True)
            vname = agent.vclass.name.lower()
            if not is_team:
                color_key = ("intruder", True)
            else:
                color_key = (vname if vname in ("quad", "rover") else "rover", is_alive)
            color = _COLORS.get(color_key, "#ffffff")
            r = agent.vclass.radius_m

            # Trail
            if is_team:
                trail = self._trails.setdefault(aid, [])
                trail.append((px, py, t))
                cutoff = t - self._trail_s
                self._trails[aid] = [(x, y, ts) for x, y, ts in trail if ts >= cutoff]
                if len(trail) >= 2:
                    xs = [x for x, _, _ in self._trails[aid]]
                    ys = [y for _, y, _ in self._trails[aid]]
                    ax.plot(xs, ys, color=color, alpha=0.25, linewidth=1.0, zorder=1)

            # Goal marker
            if is_team and agent.goal is not None and is_alive:
                gx, gy = float(agent.goal[0]), float(agent.goal[1])
                ax.plot(gx, gy, "x", color=color, markersize=8, markeredgewidth=1.5,
                        alpha=0.6, zorder=2)
                ax.plot([px, gx], [py, gy], "--", color=color, alpha=0.12,
                        linewidth=0.7, zorder=1)

            # Agent circle
            circle = self._plt.Circle((px, py), max(r, 0.4), color=color,
                                       alpha=0.9 if is_alive else 0.35, zorder=3)
            ax.add_patch(circle)

            # Heading arrow
            if is_alive:
                yaw = float(agent.yaw)
                al = max(r * 2.5, 1.0)
                ax.annotate("", xy=(px + math.cos(yaw) * al, py + math.sin(yaw) * al),
                            xytext=(px, py),
                            arrowprops=dict(arrowstyle="-|>", color=color,
                                            lw=1.2, mutation_scale=10),
                            zorder=4)

            # Label
            ax.text(px, py - r - 0.6, aid, ha="center", va="top",
                    fontsize=6, color=color, alpha=0.8, zorder=5)

        # HUD
        ax.text(-e + 0.8, e - 1.0,
                f"{self._scenario_name}\nt={t:.1f}s  intv={interventions}",
                ha="left", va="top", fontsize=8, color="#e0e0e0",
                fontfamily="monospace", zorder=6)

        self._fig.tight_layout(pad=0)
        buf = io.BytesIO()
        self._fig.savefig(buf, format="rgba", dpi=100)
        buf.seek(0)
        w, h = self._fig.canvas.get_width_height()
        rgba = np.frombuffer(buf.read(), dtype=np.uint8).reshape(h, w, 4)
        return rgba[:, :, :3]

    def save_mp4(self, frames: list[np.ndarray], path: str, fps: int | None = None) -> None:
        """Encode frames to MP4 using video_record.encode_mp4 (PyAV-based)."""
        if not frames:
            return
        fps = fps or self._fps
        try:
            from .video_record import encode_mp4
            mp4_bytes = encode_mp4(frames, fps=fps)
            with open(path, "wb") as f:
                f.write(mp4_bytes)
        except Exception as exc:
            # Fallback: save as animated GIF via matplotlib if PyAV unavailable
            try:
                from matplotlib.animation import FuncAnimation, PillowWriter
                import matplotlib.pyplot as plt
                fig, ax = plt.subplots(
                    figsize=(frames[0].shape[1] / 100, frames[0].shape[0] / 100))
                ax.axis("off")
                im = ax.imshow(frames[0])

                def _update(i):
                    im.set_data(frames[i])
                    return [im]

                gif_path = path.replace(".mp4", ".gif")
                anim = FuncAnimation(fig, _update, frames=len(frames), interval=1000 // fps)
                anim.save(gif_path, writer=PillowWriter(fps=fps))
                plt.close(fig)
                print(f"  [render] PyAV unavailable ({exc}); saved GIF → {gif_path}")
            except Exception as gif_exc:
                print(f"  [render] could not save video: {gif_exc}")

"""Load the dock/Bee35 config blocks from docking/vehicles.yaml."""
from __future__ import annotations

import copy
import os
from functools import lru_cache

import yaml

CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docking", "vehicles.yaml")
G_M_S2 = 9.80665
RHO_KG_M3 = 1.225


@lru_cache(maxsize=1)
def _load():
    with open(CFG_PATH) as f:
        return yaml.safe_load(f)


def load_cfg() -> dict:
    """Deep copy of the whole config (callers may modify it for variants)."""
    return copy.deepcopy(_load())


def channel_pitch(cfg) -> float:
    """Slot pitch: drone length plus the nose bar (each drone stops with its nose bar on the one ahead)."""
    return cfg["bee35"]["size_m"][0] + 2 * cfg["channel_dock"]["nose_bar_radius_m"]


def channel_slots(cfg) -> list:
    ch = cfg["channel_dock"]
    p = channel_pitch(cfg)
    x0 = ch["front_stop_x_m"] - (p - cfg["bee35"]["size_m"][0] / 2)
    return [x0 - p * k for k in range(4)]


def channel_geometry(cfg) -> dict:
    """Lane geometry in frame D: slot centres, start of the straight walls (x_entry) and the mouth (x_mouth)."""
    ch = cfg["channel_dock"]
    xs = channel_slots(cfg)
    x_entry = xs[-1] - cfg["bee35"]["size_m"][0] / 2 - ch["straight_extra_m"]
    return dict(slots=xs, x_entry=x_entry, x_mouth=x_entry - ch["funnel_length_m"])


def effective_cfg(cfg) -> dict:
    """Apply the channel dock variant: slot positions replace the pockets (magnets, wells, beacons and every
    pocket index follow), and the single fin becomes twin fins with A3 on the starboard one."""
    ch = cfg.get("channel_dock", {})
    if not ch.get("enabled", False) or cfg.get("_channel_applied"):
        return cfg
    cfg = copy.deepcopy(cfg)
    cfg["dock"]["pocket_x_m"] = channel_slots(cfg)
    ma = cfg["mothership_airframe"]
    lo, hi = ma["fin_box_m"]
    zf = ch["twin_fin_z_m"]
    ma["fin_boxes_m"] = [[[lo[0], lo[1], s * zf + lo[2]], [hi[0], hi[1], s * zf + hi[2]]] for s in (1, -1)]
    a3 = ma["anchors_m"]["A3"]
    ma["anchors_m"]["A3"] = [a3[0], a3[1], zf]
    ds = cfg["bee35"]["down_sensor_pos_m"]                # range/flow sensor midway between the box well (|z| 0.065)
    cfg["bee35"]["down_sensor_pos_m"] = [ds[0], ds[1], ch["down_sensor_z_m"]]   # and the guide rail (|z| 0.108)
    cfg["_channel_applied"] = True
    return cfg

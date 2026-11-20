"""Picklable trial wrappers and named designs for batch experiments (Phases 3 and 4)."""
from __future__ import annotations

import copy

from .dock import DockDesign
from .docking_trial import run_docking

# Named designs compared in Phase 3 (see DESIGN_LOG.md). Throat positions are in the
# mothership frame; "under" designs hang the funnel below the fuselage near the CG,
# ahead of the ventral fin and pusher; the "top" design sits on the wing centre section,
# between the four (stopped) lift rotors.
DESIGNS = {
    "pitched_under": DockDesign(label="pitched_under"),
    "level_under_levelout": DockDesign(label="level_under_levelout", axis_pitch_deg=0.0, level_out=True,
                                       throat_body_m=(0.30, 0.0, -0.39)),
    "level_under_angled_probe": DockDesign(label="level_under_angled_probe", axis_pitch_deg=0.0,
                                           probe_mount_deg=-48.0, throat_body_m=(0.30, 0.0, -0.39)),
    "pitched_top": DockDesign(label="pitched_top", location="top", throat_body_m=(0.0, 0.0, 0.33)),
}


def design(name: str, **over) -> DockDesign:
    d = copy.deepcopy(DESIGNS[name])
    for k, v in over.items():
        setattr(d, k, v)
    if over:
        d.label = name + "|" + ",".join(f"{k}={v}" for k, v in sorted(over.items()))
    return d


def trial(seed: int, design_name: str = "pitched_under", design_over: dict | None = None, **kw) -> dict:
    r = run_docking(seed, design(design_name, **(design_over or {})), **kw)
    r["design_name_label"] = design_name
    return r

"""Diagnostics: plan on the ground-truth map (perfect sensing) to separate planning rules
from sensing limits.  python -m delivery.diag S3 2"""
import sys

import numpy as np

from . import geom as G
from . import scenarios as S
from .mapping import C_SOLID, C_TREE, C_WIRE, OccupancyGrid
from .trial import load_cfg


def truth_grid(sc, cfg, include_invisible=True):
    d = cfg["delivery"]
    ab = (np.array([sc.balcony_x_m[0] - 0.5, sc.railing_y_m - d["entry_standoff_m"] - 0.8, sc.floor_z_m - 0.3]),
          np.array([sc.balcony_x_m[1] + 0.5, sc.balcony_y_m[1] + 0.3, sc.railing_top_z_m + d["cross_height_above_beacon_m"] + 1.0]))
    g = OccupancyGrid(sc.window_lo_m, sc.window_hi_m, d, approach_box_m=ab)
    s = sc.scene
    for kind_sel, cls in ((None, None),):
        pass
    shape = np.array(g.shape)
    for typ, arrs in (("box", (s.blo, s.bhi, s.bkind, s.bact)),):
        pass
    # rasterise each primitive class separately to get the margin class right
    def stamp(mask, cls):
        idx = np.argwhere(mask)
        for i, j, k in idx:
            g.mark_occupied(g.center((i, j, k)), cls)
    z = lambda a: a[:0]
    tree = s.skind == G.K_TREE
    occ_s = G.rasterize(g.lo, g.cell, shape, s.blo, s.bhi, s.bact & (s.bkind != G.K_NETTING if not include_invisible else s.bact),
                        z(s.c0), z(s.c1), z(s.crad), z(s.cact), z(s.sc), z(s.sr), z(s.sact), False)
    wires = (s.ckind == G.K_WIRE_METAL) | (s.ckind == G.K_WIRE_NONMETAL)
    occ_w = G.rasterize(g.lo, g.cell, shape, z(s.blo), z(s.bhi), z(s.bact), s.c0, s.c1, s.crad, s.cact & wires,
                        z(s.sc), z(s.sr), z(s.sact), False)
    occ_t = G.rasterize(g.lo, g.cell, shape, z(s.blo), z(s.bhi), z(s.bact), s.c0, s.c1, s.crad, s.cact & ~wires,
                        s.sc, s.sr, s.sact & tree, False)
    # surface cells only (interior cells add nothing to margins)
    from scipy import ndimage
    for occ, cls in ((occ_s, C_SOLID), (occ_w, C_WIRE), (occ_t, C_TREE)):
        surf = occ.astype(bool) & ~ndimage.binary_erosion(occ.astype(bool))
        stamp(surf, cls)
    g.state[g.state == 0] = 1
    return g


def main():
    sid, seed = sys.argv[1], int(sys.argv[2])
    cfg = load_cfg()
    sc = S.make(sid, seed)
    g = truth_grid(sc, cfg)
    d = cfg["delivery"]
    entry = np.array([sc.pad_m[0], sc.railing_y_m - d["entry_standoff_m"], sc.railing_top_z_m + d["cross_height_above_beacon_m"]])
    print("label", sc.corridor_3m_bool, "entry blocked", g.blocked(g.idx(entry)), "release blocked", g.blocked(g.idx(sc.release_m)))
    p = g.plan(sc.release_m, entry, 0.6, w_h=2.0)
    print("truth-map plan:", None if p is None else f"{len(p)} cells", "expansions", g.last_expansions)


if __name__ == "__main__":
    main()

"""MJCF for the dock, the mothership shapes around it and up to four Bee35 drones.

Everything is in frame D (+x forward, +y up, +z starboard), MuJoCo world = D. The dock and
airframe are static world geoms; each drone is a free body whose origin is the centre of its
underside (frame B). Pairwise friction follows the spec table through geom priority: dock
geoms (priority 1) impose their own friction on whatever touches them; drone-drone contacts
use the drone value (body-on-body).

Geom groups: 0 dock, 1 airframe (fuselage, wing, fin: UWB blockers), 2 drone bodies,
3 drone bracket, 4 carried box.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import mujoco
import numpy as np


def _v(a):
    return " ".join(f"{x:.6g}" for x in a)


def restitution_to_dampratio(e):
    """Damping ratio of a linear spring-damper whose bounce has restitution e."""
    if e <= 0:
        return 1.0
    l = math.log(e)
    return -l / math.sqrt(math.pi ** 2 + l * l)


def prism_mesh(name, pts_xy, z0, z1):
    """Convex prism: polygon pts_xy in the x-y plane, extruded from z0 to z1."""
    verts = [(x, y, z0) for x, y in pts_xy] + [(x, y, z1) for x, y in pts_xy]
    return f'<mesh name="{name}" vertex="{_v([c for v in verts for c in v])}"/>'


def prism_mesh_zy(name, pts_zy, x0, x1):
    """Convex prism: polygon in the z-y plane, extruded along x."""
    verts = [(x0, y, z) for z, y in pts_zy] + [(x1, y, z) for z, y in pts_zy]
    return f'<mesh name="{name}" vertex="{_v([c for v in verts for c in v])}"/>'


@dataclass
class DroneSpec:
    name: str
    pocket: int                      # 0..3
    pos_m: tuple                     # initial origin position in D
    yaw_deg: float = 0.0
    box: bool = False
    active: bool = False


@dataclass
class SceneInfo:
    xml: str
    drones: list = field(default_factory=list)


def drone_inertial(cfg, box: bool):
    b = cfg["bee35"]
    m0 = b["mass_kg"] + b["bracket_mass_kg"]
    c0 = np.array([0.0, b["size_m"][1] / 2, 0.0])
    I0 = np.diag(b["inertia_kg_m2"]).astype(float)
    if not box:
        return m0, c0, I0
    bx = b["box"]
    mb = bx["mass_kg"]
    cb = np.array([0.0, -bx["rim_drop_m"] - bx["size_m"][1] / 2, 0.0])
    sx, sy, sz = bx["size_m"]
    Ib = mb / 12 * np.diag([sy * sy + sz * sz, sx * sx + sz * sz, sx * sx + sy * sy])
    m = m0 + mb
    c = (m0 * c0 + mb * cb) / m
    I = np.zeros((3, 3))
    for mi, ci, Ii in [(m0, c0, I0), (mb, cb, Ib)]:
        d = ci - c
        I += Ii + mi * (np.dot(d, d) * np.eye(3) - np.outer(d, d))
    return m, c, I


def build_xml(cfg, drones: list[DroneSpec]) -> str:
    d, b, ma = cfg["dock"], cfg["bee35"], cfg["mothership_airframe"]
    ct = d["contact"]
    tc = ct["contact_timeconst_s"]
    sr_skid = f'{tc} {restitution_to_dampratio(ct["restitution_skid_nd"]):.3f}'
    sr_body = f'{tc} {restitution_to_dampratio(ct["restitution_body_nd"]):.3f}'
    dt = 1.0 / d["physics_hz"]
    assets, world = [], []

    def dgeom(name, typ, group=0, mu=None, solref=sr_skid, priority=1, **kw):
        mu = ct["skid_seat_mu"] if mu is None else mu
        attrs = " ".join(f'{k}="{v}"' for k, v in kw.items())
        world.append(f'<geom name="{name}" type="{typ}" group="{group}" priority="{priority}" '
                     f'friction="{mu} 0.005 0.0001" solref="{solref}" {attrs}/>')

    # floor plate with the four box wells
    fl = d["floor"]
    t = fl["thickness_m"]
    zh = fl["z_half_m"]
    wh = d["well_size_m"] / 2
    yc = -t / 2

    def fbox(name, x0, x1, z0, z1):
        dgeom(name, "box", pos=_v([(x0 + x1) / 2, yc, (z0 + z1) / 2]), size=_v([(x1 - x0) / 2, t / 2, (z1 - z0) / 2]))

    fbox("floor_stbd", fl["x_min_m"], fl["x_max_m"], wh, zh)
    fbox("floor_port", fl["x_min_m"], fl["x_max_m"], -zh, -wh)
    xs = sorted(d["pocket_x_m"])
    edges = [fl["x_min_m"]] + [v for x in xs for v in (x - wh, x + wh)] + [fl["x_max_m"]]
    for i in range(0, len(edges), 2):
        fbox(f"floor_mid{i // 2}", edges[i], edges[i + 1], -wh, wh)

    # ridges between pockets and end wedges (centre the drone along x)
    r = d["ridge"]
    for i, xr in enumerate(r["x_m"]):
        hb = r["base_m"] / 2
        assets.append(prism_mesh(f"ridge{i}", [(xr - hb, 0), (xr + hb, 0), (xr, r["height_m"])], -r["z_half_m"], r["z_half_m"]))
        dgeom(f"ridge{i}", "mesh", mesh=f"ridge{i}", mu=ct["skid_ridge_mu"])
    ew = d["end_wedge"]
    for s, nm in [(1, "front"), (-1, "rear")]:
        pts = [(s * ew["x_inner_m"], 0), (s * ew["x_outer_m"], ew["height_m"]), (s * ew["x_outer_m"], 0)]
        assets.append(prism_mesh(f"wedge_{nm}", pts, -r["z_half_m"], r["z_half_m"]))
        dgeom(f"wedge_{nm}", "mesh", mesh=f"wedge_{nm}", mu=ct["skid_ridge_mu"])
    lp = d["starboard_lip"]
    assets.append(prism_mesh_zy("lip", [(lp["z_inner_m"], 0), (lp["z_outer_m"], lp["height_m"]), (lp["z_outer_m"], 0)],
                                -ew["x_outer_m"], ew["x_outer_m"]))
    dgeom("lip", "mesh", mesh="lip", mu=ct["skid_ridge_mu"])

    # guide posts: capsule shaft + soft tip sphere
    p = d["post"]
    pr = p["radius_sim_m"]
    for i, xc in enumerate(d["pocket_x_m"]):
        for k, dx in enumerate(p.get("x_offsets_m", [0.0])):      # variant: two posts per pocket
            xp = xc + dx
            dgeom(f"post{i}_{k}", "capsule", mu=ct["post_bracket_mu"],
                  fromto=_v([xp, pr, p["z_m"], xp, p["height_m"] - pr, p["z_m"]]), size=f"{pr}")
            dgeom(f"post_tip{i}_{k}", "sphere", mu=ct["post_bracket_mu"], solref="0.02 1.0",
                  pos=_v([xp, p["height_m"], p["z_m"]]), size=f'{p["tip_radius_m"]}')

    # support, fairing, airframe shapes
    py = d["pylon"]
    sx, sy, sz = py["size_m"]
    for i, xp in enumerate(py["x_m"]):
        dgeom(f"pylon{i}", "box", pos=_v([xp, py["top_y_m"] - sy / 2, 0]), size=_v([sx / 2, sy / 2, sz / 2]))
    fa = d["fairing"]
    assets.append(prism_mesh("fairing", [(fa["x_min_m"], 0), (fa["x_min_m"], fa["top_y_m"]), (fa["x_max_m"], 0)],
                             -fa["z_half_m"], fa["z_half_m"]))
    dgeom("fairing", "mesh", mesh="fairing")
    for nm in ("fuselage", "wing", "fin"):
        lo, hi = np.array(ma[f"{nm}_box_m"][0]), np.array(ma[f"{nm}_box_m"][1])
        dgeom(nm, "box", group=1, pos=_v((lo + hi) / 2), size=_v((hi - lo) / 2))

    # drones
    bodies = []
    bs = b["size_m"]
    br = b["bracket"]
    th = br["thickness_m"]
    yb = (br["y_min_m"] + br["y_max_m"]) / 2
    hb_y = (br["y_max_m"] - br["y_min_m"]) / 2
    sw = br["slot_width_m"] / 2
    for ds in drones:
        m, c, I = drone_inertial(cfg, ds.box)
        ev, evec = np.linalg.eigh(I)
        q = np.zeros(4)
        if np.linalg.det(evec) < 0:
            evec[:, 0] *= -1
        mujoco.mju_mat2Quat(q, evec.flatten())
        yaw = math.radians(ds.yaw_deg)
        qb = [math.cos(yaw / 2), 0, math.sin(yaw / 2), 0]
        nm = ds.name
        g = []

        def bgeom(gname, typ, group, mu, solref=sr_body, **kw):
            attrs = " ".join(f'{k}="{v}"' for k, v in kw.items())
            g.append(f'<geom name="{nm}_{gname}" type="{typ}" group="{group}" priority="0" mass="0" '
                     f'friction="{mu} 0.005 0.0001" solref="{solref}" {attrs}/>')

        pad_h = 0.001
        bgeom("body", "box", 2, ct["body_body_mu"], pos=_v([0, (bs[1] + 2 * pad_h) / 2, 0]),
              size=_v([bs[0] / 2, (bs[1] - 2 * pad_h) / 2, bs[2] / 2]))
        o = b["skid_pad_offset_m"]
        for k, (sx_, sz_) in enumerate([(1, 1), (1, -1), (-1, 1), (-1, -1)]):
            bgeom(f"pad{k}", "box", 2, ct["skid_seat_mu"], solref=sr_skid, pos=_v([sx_ * o, pad_h, sz_ * o]),
                  size=_v([0.012, pad_h, 0.012]))
        # bracket(s): end plate, slot walls, fences (variant: one bracket per post offset)
        ze = br["slot_end_z_m"]
        zm = br["slot_mouth_z_m"]
        for kb, bx0 in enumerate(br.get("x_offsets_m", [0.0])):
            bgeom(f"br_end{kb}", "box", 3, ct["post_bracket_mu"], pos=_v([bx0, yb, ze + th / 2]),
                  size=_v([sw + th, hb_y, th / 2]))
            for s, sn in [(1, "p"), (-1, "n")]:
                bgeom(f"br_wall{sn}{kb}", "box", 3, ct["post_bracket_mu"], pos=_v([bx0 + s * (sw + th / 2), yb, (ze + zm) / 2]),
                      size=_v([th / 2, hb_y, (ze - zm) / 2]))
                a = np.array([s * sw, zm])
                e = np.array([s * br["mouth_half_width_m"], br["mouth_z_m"]])
                dv = e - a
                L = float(np.linalg.norm(dv))
                u = dv / L
                n1 = np.array([-u[1], u[0]])               # pick the normal away from the channel centre
                if np.dot(n1, np.array([0.0, br["mouth_z_m"]]) - a) > 0:
                    n1 = -n1
                mid = (a + e) / 2 + n1 * th / 2
                ang = math.atan2(-u[1], u[0])             # rotation about +y mapping local x onto (u_x, ., u_z)
                bgeom(f"br_fence{sn}{kb}", "box", 3, ct["post_bracket_mu"], pos=_v([bx0 + mid[0], yb, mid[1]]),
                      size=_v([L / 2, hb_y, th / 2]), euler=f"0 {math.degrees(ang):.4f} 0")
        if ds.box:
            bx = b["box"]
            bgeom("box", "box", 4, ct["body_body_mu"], pos=_v([0, -bx["rim_drop_m"] - bx["size_m"][1] / 2, 0]),
                  size=_v(np.array(bx["size_m"]) / 2))
            bgeom("rim", "box", 4, ct["body_body_mu"], pos=_v([0, -bx["rim_drop_m"] - bx["rim_m"][1] / 2, 0]),
                  size=_v(np.array(bx["rim_m"]) / 2))
        g.append(f'<site name="{nm}_tag" pos="{_v(b["tag_pos_m"])}" size="0.005"/>')
        g.append(f'<site name="{nm}_down" pos="{_v(b["down_sensor_pos_m"])}" size="0.005"/>')
        bodies.append(
            f'<body name="{nm}" pos="{_v(ds.pos_m)}" quat="{_v(qb)}">'
            f'<freejoint name="{nm}_free"/>'
            f'<inertial pos="{_v(c)}" quat="{_v(q)}" mass="{m:.6g}" diaginertia="{_v(ev)}"/>'
            + "".join(g) + "</body>")

    for k, a in ma["anchors_m"].items():
        world.append(f'<site name="anchor_{k}" pos="{_v(a)}" size="0.02"/>')

    return f"""<mujoco model="passive_dock">
  <compiler angle="degree" inertiafromgeom="false"/>
  <option timestep="{dt}" gravity="0 -9.80665 0" integrator="implicitfast" cone="elliptic" impratio="1">
    <flag energy="disable"/>
  </option>
  <size memory="8M"/>
  <asset>{''.join(assets)}</asset>
  <worldbody>
    {''.join(world)}
    {''.join(bodies)}
  </worldbody>
</mujoco>"""


def build(cfg, drones: list[DroneSpec]):
    xml = build_xml(cfg, drones)
    model = mujoco.MjModel.from_xml_string(xml)
    return model, xml

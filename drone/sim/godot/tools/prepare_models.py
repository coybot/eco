"""Turn Sketchfab downloads into the sim's models (assets/models/*.glb).

Run in Blender (4.5), with the folder holding the original downloads:

    blender -b --python drone/sim/godot/tools/prepare_models.py -- <downloads dir> [name ...]

Credits and licences: assets/models/CREDITS.md. Each output faces the same way,
so Godot can place them all alike:

    glTF/Godot +Z = forward (bow, nose, boom out to sea), +Y = up.
    In Blender, before export, that is -Y forward and +Z up.

Origins: vehicles stand on z = 0, cranes too, centred between their feet, aircraft
are centred on their bounding box, and ships sit with the waterline at z = 0.
Sizes are real ones in metres; Godot scales again where it must fit a box.

Textures go out as WebP (EXT_texture_webp, which Godot reads), at most 512-1024 px.

A material named PAINT (the largest light-coloured one on a vehicle) is the
one Godot recolours to each asset's own paint.
"""
from __future__ import annotations

import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "assets", "models")


# ------------------------------------------------------------------ helpers
def load(path: str) -> list:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    for o in meshes:
        mw = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = mw
    for o in [o for o in bpy.context.scene.objects if o.type != "MESH"]:
        bpy.data.objects.remove(o)
    for o in meshes:
        if o.data.users > 1:
            o.data = o.data.copy()
    _apply(meshes)
    # Drop line-only meshes (edge overlays some exporters add).
    for o in [o for o in meshes if len(o.data.polygons) == 0]:
        meshes.remove(o)
        bpy.data.objects.remove(o)
    return meshes


def _apply(objs: list) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def verts(objs: list) -> list:
    return [o.matrix_world @ v.co for o in objs for v in o.data.vertices]


def bbox(objs: list) -> tuple[Vector, Vector]:
    vs = verts(objs)
    lo = Vector((min(v.x for v in vs), min(v.y for v in vs), min(v.z for v in vs)))
    hi = Vector((max(v.x for v in vs), max(v.y for v in vs), max(v.z for v in vs)))
    return lo, hi


def transform(objs: list, m: Matrix) -> None:
    for o in objs:
        o.data.transform(m)
        o.data.update()


def remove(objs: list, pred) -> list:
    keep = []
    for o in objs:
        if pred(o):
            bpy.data.objects.remove(o)
        else:
            keep.append(o)
    return keep


def mats(o) -> list[str]:
    return [s.material.name for s in o.material_slots if s.material]


def long_axis_to_y(objs: list) -> None:
    """Turn about Z so the horizontal principal axis runs along Y."""
    vs = verts(objs)
    n = len(vs)
    cx = sum(v.x for v in vs) / n
    cy = sum(v.y for v in vs) / n
    sxx = sum((v.x - cx) ** 2 for v in vs) / n
    syy = sum((v.y - cy) ** 2 for v in vs) / n
    sxy = sum((v.x - cx) * (v.y - cy) for v in vs) / n
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)    # principal axis from +X
    transform(objs, Matrix.Rotation(math.pi / 2 - ang, 4, "Z"))


def end_width(objs: list, sign: float) -> float:
    """Mean |x| of the vertices in the last 15 % of the length towards sign*Y."""
    lo, hi = bbox(objs)
    L = hi.y - lo.y
    vs = [v for v in verts(objs) if (v.y > hi.y - 0.15 * L if sign > 0 else v.y < lo.y + 0.15 * L)]
    cx = (lo.x + hi.x) / 2
    return sum(abs(v.x - cx) for v in vs) / max(1, len(vs))


def bow_to_minus_y(objs: list) -> None:
    """A hull's bow is its narrower end; turn it to -Y."""
    if end_width(objs, -1) > end_width(objs, +1):
        transform(objs, Matrix.Rotation(math.pi, 4, "Z"))


def gun_forward(objs: list) -> None:
    """Point the end the bounding box reaches furthest past the mass (a gun) to -Y."""
    lo, hi = bbox(objs)
    vs = verts(objs)
    my = sum(v.y for v in vs) / len(vs)
    if (lo.y + hi.y) / 2 > my:
        transform(objs, Matrix.Rotation(math.pi, 4, "Z"))


def nose_to_minus_y(objs: list) -> None:
    """A flying wing: the centre section reaches further forward than the tips."""
    lo, hi = bbox(objs)
    span = hi.x - lo.x
    cx = (lo.x + hi.x) / 2
    vs = verts(objs)
    centre = [v.y for v in vs if abs(v.x - cx) < 0.08 * span]
    tips = [v.y for v in vs if abs(v.x - cx) > 0.4 * span]
    if sum(centre) / len(centre) > sum(tips) / len(tips):
        transform(objs, Matrix.Rotation(math.pi, 4, "Z"))


def scale_to(objs: list, size: float, what: str) -> None:
    lo, hi = bbox(objs)
    d = hi - lo
    cur = {"length": d.y, "span": max(d.x, d.y), "height": d.z, "width": d.x}[what]
    transform(objs, Matrix.Scale(size / cur, 4))


def origin(objs: list, how) -> None:
    lo, hi = bbox(objs)
    c = (lo + hi) / 2
    if how == "feet":          # a crane: centred between the parts that touch the ground
        low = [v for v in verts(objs) if v.z < lo.z + 1.0]
        c = Vector((sum(v.x for v in low) / len(low), sum(v.y for v in low) / len(low), 0.0))
        z = lo.z
    elif how == "ground":
        z = lo.z
    elif how == "centre":
        z = c.z
    else:                      # ("water", fraction of the height below the waterline)
        z = lo.z + how[1] * (hi.z - lo.z)
    transform(objs, Matrix.Translation((-c.x, -c.y, -z)))


def decimate(objs: list, target: int) -> None:
    total = sum(len(o.data.polygons) for o in objs)
    if total <= target:
        return
    ratio = target / total
    for o in objs:
        if len(o.data.polygons) < 150:
            continue
        m = o.modifiers.new("dec", "DECIMATE")
        m.ratio = max(0.02, ratio)
        m.use_collapse_triangulate = True
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.modifier_apply(modifier=m.name)


def shrink_textures(px: int) -> None:
    for img in bpy.data.images:
        w, h = img.size
        if max(w, h) > px:
            k = px / max(w, h)
            img.scale(max(1, int(w * k)), max(1, int(h * k)))
            img.pack()             # the exporter writes packed bytes: repack the smaller pixels


def _base(mat) -> tuple[float, bool]:
    for nd in (mat.node_tree.nodes if mat.node_tree else []):
        if nd.type == "BSDF_PRINCIPLED":
            c = nd.inputs["Base Color"]
            return (sum(c.default_value[:3]) / 3, bool(c.links))
    return (0.0, False)


def tag_paint(objs: list, name_hint: str | None = None) -> None:
    """Rename the vehicle's paint material PAINT: a named one, else the largest light one."""
    area: dict = {}
    for o in objs:
        for p in o.data.polygons:
            if p.material_index < len(o.material_slots) and o.material_slots[p.material_index].material:
                m = o.material_slots[p.material_index].material
                area[m] = area.get(m, 0.0) + p.area
    cands = [m for m in area if (name_hint and name_hint in m.name.lower())] or \
            [m for m in area if _base(m)[0] > 0.3 or _base(m)[1]]
    if cands:
        max(cands, key=lambda m: area[m]).name = "PAINT"


def export(objs: list, name: str, tex: int) -> None:
    shrink_textures(tex)
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    path = os.path.abspath(os.path.join(OUT, name + ".glb"))
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_yup=True,
                              export_apply=True, export_image_format="WEBP", export_image_quality=85, export_animations=False,
                              export_draco_mesh_compression_enable=False)
    fix_textures(path)
    lo, hi = bbox(objs)
    print(f"WROTE {name}.glb  {sum(len(o.data.polygons) for o in objs)} faces  "
          f"size {tuple(round(v, 2) for v in hi - lo)}  {os.path.getsize(path) / 1e6:.1f} MB")


def fix_textures(path: str) -> None:
    """Drop textures the exporter left without an image (a node it could not
    convert), and every material slot that used them: Godot refuses the whole
    file over one such texture."""
    import json
    import struct
    b = open(path, "rb").read()
    jlen = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20:20 + jlen])
    tex = j.get("textures", [])
    ok = [("source" in t) or ("source" in t.get("extensions", {}).get("EXT_texture_webp", {})) for t in tex]
    if all(ok):
        return
    remap = {}
    for i, good in enumerate(ok):
        if good:
            remap[i] = len(remap)
    j["textures"] = [t for t, good in zip(tex, ok) if good]

    def walk(node):
        if isinstance(node, dict):
            for k in list(node):
                v = node[k]
                if isinstance(v, dict) and "index" in v and k.endswith("Texture"):
                    if v["index"] in remap:
                        v["index"] = remap[v["index"]]
                    else:
                        del node[k]
                        continue
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(j.get("materials", []))
    js = json.dumps(j, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    rest = b[20 + jlen:]
    total = 12 + 8 + len(js) + len(rest)
    open(path, "wb").write(b[:8] + struct.pack("<I", total) + struct.pack("<I", len(js)) + b"JSON" + js + rest)
    print(f"  {os.path.basename(path)}: dropped {ok.count(False)} texture(s) with no image")


ROT180 = Matrix.Rotation(math.pi, 4, "Z")
ROT90 = Matrix.Rotation(math.pi / 2, 4, "Z")


# ------------------------------------------------------------------ models
def tank(src):
    o = load(src)
    long_axis_to_y(o); gun_forward(o)
    scale_to(o, 9.77, "length")            # M1A2 overall, gun forward
    origin(o, "ground"); decimate(o, 20000); export(o, "tank_m1a2", 1024)


def pickup(src):
    o = load(src)                          # already faces -Y
    scale_to(o, 5.26, "length")            # Hilux double cab
    origin(o, "ground"); tag_paint(o); decimate(o, 24000); export(o, "pickup_hilux", 1024)


CARS = {"compact": 3.9, "coupe": 4.4, "hatchback": 4.1, "minivan": 4.8, "offroad": 4.4,
        "sedan": 4.75, "sport": 4.4, "suv": 4.7, "wagon": 4.8}
#: Cars whose front the length axis alone gets backwards (checked by eye).
CAR_FLIP: set[str] = set()


def cars(src):
    allm = load(src)
    allm = remove(allm, lambda o: o.name.startswith("Cylinder"))
    key = lambda o: o.name.lower().split(" body")[0] if " body" in o.name.lower() else None
    groups: dict = {}
    for o in allm:
        if key(o):
            groups.setdefault(key(o), []).append(o)
    centre = {k: sum(bbox(v), Vector()) / 2 for k, v in groups.items()}
    for o in allm:
        if not key(o):
            c = sum(bbox([o]), Vector()) / 2
            groups[min(centre, key=lambda k: (centre[k] - c).xy.length)].append(o)
    for kind, objs in groups.items():
        if kind not in CARS:
            continue
        long_axis_to_y(objs)
        if kind in CAR_FLIP:
            transform(objs, ROT180)
        scale_to(objs, CARS[kind], "length"); origin(objs, "ground")
        tag_paint(objs, "body")
        export(objs, f"car_{kind}", 512)
        for m in bpy.data.materials:
            if m.name == "PAINT":
                m.name = "PAINT_done"


def crane_gantry(src):
    o = load(src)
    transform(o, ROT180)                   # its long outreach to -Y (seaward)
    # At 28 m high it is 27 m
    # along the rails, so two neighbouring cranes 27 m apart don't overlap.
    scale_to(o, 28.0, "height")
    origin(o, "feet"); decimate(o, 40000); export(o, "crane_gantry", 1024)


def crane_kpp(src):
    o = load(src)                          # boom already out along -Y; modelled in metres
    origin(o, "feet"); export(o, "crane_kpp", 1024)


def m300(src):
    o = load(src)
    o = remove(o, lambda x: bbox([x])[1].z < 4.3)    # the carry case under it
    scale_to(o, 1.67, "span")              # rotor tip to rotor tip
    origin(o, "centre"); decimate(o, 24000); export(o, "drone_m300", 512)


def hexa(src):
    o = load(src)
    scale_to(o, 1.9, "span"); origin(o, "centre"); decimate(o, 20000); export(o, "drone_hexa", 512)


def heli(src):
    o = load(src)
    scale_to(o, 3.4, "span"); origin(o, "centre"); export(o, "drone_heli", 512)


def x8(src):
    o = load(src)
    transform(o, ROT90); nose_to_minus_y(o)
    scale_to(o, 2.12, "width"); origin(o, "centre"); decimate(o, 10000); export(o, "drone_x8", 512)


def vtol(src):
    o = load(src)                          # nose already -Y
    scale_to(o, 3.0, "width"); origin(o, "centre"); decimate(o, 20000); export(o, "drone_vtol", 512)


def ship(src, name, length, draft, faces, drop=None, flip=False, tex=1024):
    o = load(src)
    if drop:
        o = remove(o, drop)
    long_axis_to_y(o); bow_to_minus_y(o)
    if flip:
        transform(o, ROT180)
    scale_to(o, length, "length"); origin(o, ("water", draft)); decimate(o, faces)
    export(o, name, tex)


JOBS = {
    "tank_m1a2": ("general_dynamics_m1a2_abrams_main_battle_tank.glb", tank),
    "pickup_hilux": ("toyota_hilux_2007.glb", pickup),
    "cars": ("generic_passenger_car_pack.glb", cars),
    "crane_gantry": ("crane_harbour.glb", crane_gantry),
    "crane_kpp": ("harbour_crane_kpp-10.12.5.glb", crane_kpp),
    "drone_m300": ("quadcopter_dji_matrice_300_rtk.glb", m300),
    "drone_hexa": ("skyvision_3d_next-gen_aerial_precision.glb", hexa),
    "drone_heli": ("camcopter_s_100.glb", heli),
    "drone_x8": ("skywalker_x8_1300mm.glb", x8),
    "drone_vtol": ("doosan_hydrogen_powered_vtol_drone.glb", vtol),
    "ship_cargo": ("cargo_ship.glb", lambda s: ship(s, "ship_cargo", 80.0, 0.12, 30000)),
    "ship_dhow": ("dhow01.glb", lambda s: ship(s, "ship_dhow", 22.0, 0.22, 16000)),
    "boat_cabin": ("fisher_boat.glb", lambda s: ship(s, "boat_cabin", 12.0, 0.16, 10000, tex=512)),
    "boat_skiff": ("fishing_boat.glb", lambda s: ship(
        s, "boat_skiff", 6.5, 0.25, 6000,
        drop=lambda o: any(m in ("Sand", "Water", "Rock") for m in mats(o)))),
    "boat_trawler": ("fishing_ship_game-ready_asset.glb", lambda s: ship(s, "boat_trawler", 18.0, 0.16, 16000, tex=512)),
    "boat_rusty": ("low_poly_old_rusty_fishing_boat.glb", lambda s: ship(s, "boat_rusty", 13.0, 0.18, 10000, tex=512)),
    "ship_tug": ("svitzer_gelliswick_-_fishing_boat_3d_scan.glb", lambda s: ship(s, "ship_tug", 30.0, 0.17, 20000)),
}


def main() -> None:
    args = sys.argv[sys.argv.index("--") + 1:]
    src_dir, only = args[0], set(args[1:])
    os.makedirs(OUT, exist_ok=True)
    for name, (src, fn) in JOBS.items():
        if only and name not in only:
            continue
        fn(os.path.join(src_dir, src))


main()

## The bridges round Manhattan, built procedurally from assets/manhattan/bridges.json
## (tools/fetch_manhattan_bridges.py: alignments from OpenStreetMap, spans and
## heights from the bridges' published dimensions).
##
## A child of env_manhattan_photo. Every bridge is a handful of meshes built
## here in its own local frame (s along the deck, lateral across it, y up from
## street level): towers, decks, trusses, arches, main cables, suspenders and
## stays, approach viaducts and anchorages, each in the bridge's own paint.
##
## BRIDGES ARE OBSTACLES, in both of the sim's structure models:
##   * physics: one StaticBody3D per bridge on collision layer 1 (the
##     aircraft's strike sweep, fixedwing_manager._sweep_structure), made of
##     plain boxes: tower legs and blocks, deck segments, arch ribs, and thin
##     boxes standing in for the cable-and-suspender curtain of a suspension
##     span (a curtain of 0.3 m hangers every 12 m is not something to fly
##     through). Never a trimesh of every cable.
##   * analytic: every tower's footprint goes into the env's box model
##     (_rects/_heights/WALLS and its tile index), so routing, occlusion and
##     the envelope guard see it, but only where it falls inside the occupancy
##     grid (the loaded slice). The decks do NOT: the box model is a prism from
##     the street up, and a deck as a prism would wall off the river at every
##     height, under the deck included. Decks are physics only.
##
## For traffic (or anything that wants to drive or stand on a deck): decks()
## lists every deck centreline as sim (e, n, y) points, deck_height(id) is a
## bridge's main deck level, deck_y_at(e, n) the deck height under a sim point
## (NAN where there is none). y is metres above street level.
##
## Night: necklace lights along the suspension cables, deck lamps and red
## beacons on the tall towers, one MultiMesh of camera-facing points driven by
## env.night_amount(). --far-night=<0..1> forces it, for checking shots.
extends Node3D

const DATA := "res://assets/manhattan/bridges.json"
const EnvManhattan = preload("res://scripts/env_manhattan.gd")

## Hanger spacing and the sampling step for curves (cables, arches, decks).
const HANGER_STEP := 12.0
const CURVE_STEP := 20.0
const PIER_STEP := 45.0
## How far the fine parts are drawn. Suspenders and truss lacing alias into
## shimmer past a few km and are invisible there anyway.
const FINE_RANGE := 3500.0
const MAIN_RANGE := 30000.0

var env: Node3D
var _decks: Array = []           # [{id, name, width, y, pts: PackedVector3Array (sim e, n, y)}]
var _deck_y := {}                # id -> main deck level
var _lights: Array = []          # [Vector3 global, Color]
var _light_mat: ShaderMaterial
var _night_forced := -1.0
var _night_last := -1.0
var _walls_added := 0

# Per bridge while building.
var _a := Vector2.ZERO           # dataset-frame origin
var _u := Vector2.RIGHT
var _v := Vector2.UP
var _origin := Vector3.ZERO      # Godot position of s = 0
var _st := {}                    # class -> SurfaceTool
var _boxes: Array = []           # [Transform3D, Vector3 size] for collision
var _towers: Array = []          # [Rect2 dataset-frame footprint AABB, top y]
var _cur_col := Color.WHITE

var _mat := {}


func _ready() -> void:
	env = get_parent()
	name = "bridges"
	var arg := str(IpcServer._get_launch_arg("--far-night", ""))
	if arg != "":
		_night_forced = clampf(float(arg), 0.0, 1.0)
	if not FileAccess.file_exists(DATA):
		push_warning("[bridges] no " + DATA + " — run drone/sim/tools/fetch_manhattan_bridges.py")
		return
	var doc: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(DATA))
	_make_materials()
	var n := 0
	for b in doc.get("bridges", []):
		_build(b)
		n += 1
	_commit_clusters()
	_build_lights()
	_add_walls()
	print("[bridges] %d bridges, %d tower footprints in the structure model, %d lights"
		% [n, _walls_added, _lights.size()])


func _process(_dt: float) -> void:
	if _light_mat == null:
		return
	var nt := _night_forced if _night_forced >= 0.0 else float(env.call("night_amount"))
	if absf(nt - _night_last) > 0.01:
		_night_last = nt
		_light_mat.set_shader_parameter("night", nt)


# ------------------------------------------------------------------ public API

func decks() -> Array:
	return _decks


func deck_height(id: String) -> float:
	return float(_deck_y.get(id, NAN))


## Deck height under sim (e, n), or NAN. Nearest centreline within its half width.
func deck_y_at(e: float, n: float) -> float:
	var p := Vector2(e, n)
	var best := NAN
	var best_d := INF
	for d in _decks:
		var pts: PackedVector3Array = d["pts"]
		var hw: float = float(d["width"]) * 0.5
		for i in range(pts.size() - 1):
			var a := Vector2(pts[i].x, pts[i].y)
			var b := Vector2(pts[i + 1].x, pts[i + 1].y)
			var ab := b - a
			var t := clampf((p - a).dot(ab) / maxf(ab.length_squared(), 1e-6), 0.0, 1.0)
			var dist := p.distance_to(a + ab * t)
			if dist <= hw and dist < best_d:
				best_d = dist
				best = lerpf(pts[i].z, pts[i + 1].z, t)
	return best


# ------------------------------------------------------------------ materials

## One material for every bridge part, so a cluster of bridges is one draw:
## the paint is the vertex colour and the vertex alpha says what the part is
## (1 painted steel, 0.5 masonry, 0 asphalt), which sets its finish here and
## its heat in the thermal band (native: thermal_kit leaves it alone).
func _make_materials() -> void:
	var sh := Shader.new()
	sh.code = BRIDGE_SHADER
	var m := ShaderMaterial.new()
	m.shader = sh
	_mat = {"main": m, "fine": m}


const BRIDGE_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"

void fragment() {
    float cls = COLOR.a;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th;
        if (cls > 0.75) {
            // Painted steel: thin, so it warms fast in the sun and falls below
            // the air under a clear night sky; bare faces show the cold sky.
            th = th_surface(0.5, 20.0, 0.2, 0.0);
            float up = clamp((INV_VIEW_MATRIX * vec4(NORMAL, 0.0)).y, 0.0, 1.0);
            th.y = mix(th.y, th_enc(mix(thermal_air_c, thermal_sky_c, up)), 0.3);
        } else if (cls > 0.25) {
            th = th_surface(0.5, 18.0, 0.7, 0.0);      // granite and concrete piers
        } else {
            th = th_surface(0.85, 26.0, 0.6, 0.0);     // the asphalt roadway
        }
        TH_OUT(th);
    } else {
        ALBEDO = COLOR.rgb;
        ROUGHNESS = cls > 0.75 ? 0.6 : (cls > 0.25 ? 0.95 : 0.9);
        METALLIC = cls > 0.75 ? 0.25 : 0.0;
    }
}
"""


# ------------------------------------------------------------------ frame

## Local position on the current bridge: s along it, lat across (+ left), y up.
func _p(s: float, lat: float, y: float) -> Vector3:
	var d := _u * s + _v * lat
	return Vector3(d.x, y, -d.y)


func _dataset(s: float, lat: float) -> Vector2:
	return _a + _u * s + _v * lat


func _along() -> Vector3:
	return Vector3(_u.x, 0.0, -_u.y)


func _across() -> Vector3:
	return Vector3(_v.x, 0.0, -_v.y)


## Parts go in one of two meshes per bridge: "fine" (hangers, stays, truss
## lacing: range-culled, no shadows) or "main" (everything else).
func _tool(cls: String) -> SurfaceTool:
	var key := "fine" if cls == "fine" else "main"
	if not _st.has(key):
		var st := SurfaceTool.new()
		st.begin(Mesh.PRIMITIVE_TRIANGLES)
		_st[key] = st
	return _st[key]


const ROAD := Color(0.11, 0.11, 0.12)


## An oriented box: centre c, unit axes x/y/z, full size.
func _box(cls: String, c: Vector3, x: Vector3, y: Vector3, z: Vector3, size: Vector3,
		col: Color, solid: bool = false, caps: bool = true) -> void:
	var st := _tool(cls)
	var hx := x * size.x * 0.5
	var hy := y * size.y * 0.5
	var hz := z * size.z * 0.5
	if cls == "deck":
		col = Color(ROAD, 0.0)
	else:
		col.a = 0.5 if cls == "stone" else 1.0
	st.set_color(col)
	var faces := [[y, hy, hz, hx], [-y, -hy, -hz, hx], [z, hz, hx, hy], [-z, -hz, -hx, hy]]
	if caps:
		faces += [[x, hx, hy, hz], [-x, -hx, hy, -hz]]
	for f in faces:
		var nrm: Vector3 = f[0]
		var o: Vector3 = c + f[1]
		var a: Vector3 = f[2]
		var b: Vector3 = f[3]
		# Winding: Godot front faces are clockwise seen from outside.
		var q := [o - a - b, o + a - b, o + a + b, o - a + b]
		if a.cross(b).dot(nrm) > 0.0:
			q = [q[0], q[3], q[2], q[1]]
		st.set_normal(nrm)
		for k in [0, 1, 2, 0, 2, 3]:
			st.add_vertex(q[k])
	if solid:
		_boxes.append([Transform3D(Basis(x, y, z), c), size])


## A box aligned with the bridge, centred at (s, lat, y).
func _block(cls: String, s: float, lat: float, y: float, size: Vector3, col: Color,
		solid: bool = true) -> void:
	_box(cls, _p(s, lat, y), _along(), Vector3.UP, _across(), size, col, solid)


## A square-section beam from p to q (local positions).
func _beam(cls: String, p: Vector3, q: Vector3, w: float, col: Color, solid: bool = false) -> void:
	var d := q - p
	var len := d.length()
	if len < 0.01:
		return
	var x := d / len
	var hint := Vector3.UP if absf(x.y) < 0.9 else _along()
	var z := x.cross(hint).normalized()
	var y := z.cross(x).normalized()
	# No end caps: a beam's ends are hidden in the joints it meets, and
	# they were a third of every bridge's triangles.
	_box(cls, (p + q) * 0.5, x, y, z, Vector3(len, w, w), col, solid, false)


## A vertical tower element: footprint along x across, from y0 to y1; also
## recorded as a tower for the analytic structure model when it stands tall.
func _tower_block(cls: String, s: float, lat: float, y0: float, y1: float,
		along: float, across: float, col: Color) -> void:
	_block(cls, s, lat, (y0 + y1) * 0.5, Vector3(along, y1 - y0, across), col)
	_record_tower(s, lat, along, across, y1)


func _record_tower(s: float, lat: float, along: float, across: float, top: float) -> void:
	var pts := []
	for i in [-1.0, 1.0]:
		for j in [-1.0, 1.0]:
			pts.append(_dataset(s + i * along * 0.5, lat + j * across * 0.5))
	var r := Rect2(pts[0], Vector2.ZERO)
	for p in pts:
		r = r.expand(p)
	_towers.append([r, top])


# ------------------------------------------------------------------ build

func _build(b: Dictionary) -> void:
	_a = Vector2(b["a"][0], b["a"][1])
	_u = Vector2(b["u"][0], b["u"][1]).normalized()
	_v = Vector2(-_u.y, _u.x)
	var sim_a: Vector2 = env.call("to_sim", _a)
	_origin = Vector3(sim_a.x, 0.0, -sim_a.y)
	_st = {}
	_boxes = []
	var col := _srgb(b.get("colour", [0.55, 0.57, 0.58]))
	var stone := _srgb(b.get("stone", [0.62, 0.60, 0.56]))
	var kind := str(b["kind"])
	var id := str(b["id"])

	if kind == "tram":
		_build_tram(b, col)
	else:
		var prof := _deck_profile(b)
		_build_deck(b, prof, col, stone)
		match kind:
			"suspension": _build_suspension(b, prof, col, stone)
			"cantilever": _build_cantilever(b, prof, col, stone)
			"arch_through": _build_arch_through(b, prof, col, stone)
			"arch_under": _build_arch_under(b, prof, col, stone)
			"swing": _build_swing(b, prof, col)
			"lift": _build_lift(b, prof, col)
		_record_deck(b, prof)
	_commit(id)


## Paint colours are as seen; as albedo under the scene's full sun they
## tonemap to near white (the first build had chalk-white bridges), so they
## are darkened the way the orthophoto and the roofs are.
func _srgb(c: Array) -> Color:
	return Color(float(c[0]), float(c[1]), float(c[2])) * ALBEDO_GAIN


const ALBEDO_GAIN := 0.38


## The deck's centreline height as [s, y] pairs: the main structure between the
## anchors (with its camber), then each end's approach — a ramp down to the
## street at the end's grade, or a level run onto an abutment on high ground.
func _deck_profile(b: Dictionary) -> Array:
	var a0: float = float(b["anchors"][0])
	var a1: float = float(b["anchors"][1])
	var dy: float = float(b["deck_y"])
	var camber: float = float(b.get("camber", 0.0))
	var ends: Array = b.get("ends", [["ramp", 0.05], ["ramp", 0.05]])
	var out: Array = []
	# End A, walking toward the anchor.
	var e0: Array = ends[0]
	var l0 := _approach_len(e0, dy)
	var n0 := maxi(1, int(ceil(l0 / CURVE_STEP)))
	for i in n0:
		var t := float(i) / n0
		out.append([a0 - l0 * (1.0 - t), _approach_y(e0, dy, t)])
	var n := maxi(2, int(ceil((a1 - a0) / CURVE_STEP)))
	for i in n + 1:
		var t := float(i) / n
		var x := t * 2.0 - 1.0
		out.append([lerpf(a0, a1, t), dy + camber * (1.0 - x * x)])
	var e1: Array = ends[1]
	var l1 := _approach_len(e1, dy)
	var n1 := maxi(1, int(ceil(l1 / CURVE_STEP)))
	for i in range(1, n1 + 1):
		var t := float(i) / n1
		out.append([a1 + l1 * t, _approach_y(e1, dy, 1.0 - t)])
	return out


func _approach_len(e: Array, dy: float) -> float:
	if str(e[0]) == "abut":
		return float(e[1])
	return clampf(dy / float(e[1]), 20.0, 1600.0)


## t = 0 at the far end of the approach, 1 at the anchor.
func _approach_y(e: Array, dy: float, t: float) -> float:
	if str(e[0]) == "abut":
		return dy
	# Eased at the foot so the ramp meets the street flush, not at a kink.
	return dy * smoothstep(0.0, 1.0, t * 0.85 + 0.15) if t > 0.0 else 0.0


func _profile_y(prof: Array, s: float) -> float:
	if s <= float(prof[0][0]):
		return float(prof[0][1])
	for i in range(prof.size() - 1):
		var s0: float = prof[i][0]
		var s1: float = prof[i + 1][0]
		if s <= s1:
			return lerpf(float(prof[i][1]), float(prof[i + 1][1]), (s - s0) / maxf(s1 - s0, 1e-3))
	return float(prof[-1][1])


## Deck: a box per profile segment (girder in the bridge's paint, roadway on
## top, parapets), piers under the approaches, abutment blocks at "abut" ends.
func _build_deck(b: Dictionary, prof: Array, col: Color, stone: Color) -> void:
	var w: float = float(b["width"])
	var depth := 2.5 if float(b["deck_y"]) > 20.0 else 1.6
	var lower: float = float(b.get("lower_deck", 0.0))
	var a0: float = float(b["anchors"][0])
	var a1: float = float(b["anchors"][1])
	var water: Array = b.get("water", [0.0, 0.0])
	for i in range(prof.size() - 1):
		var s0: float = prof[i][0]
		var s1: float = prof[i + 1][0]
		var y0: float = prof[i][1]
		var y1: float = prof[i + 1][1]
		if maxf(y0, y1) < 0.3:
			continue
		var p0 := _p(s0, 0.0, y0)
		var p1 := _p(s1, 0.0, y1)
		var x := (p1 - p0).normalized()
		var z := _across()
		var y := z.cross(x).normalized()
		if y.y < 0.0:
			y = -y
		var mid := (p0 + p1) * 0.5
		var len := p0.distance_to(p1) + 0.05
		var main := s1 > a0 and s0 < a1
		var d := depth + (lower if main else 0.0)
		_box("steel", mid - y * d * 0.5, x, y, z, Vector3(len, d, w), col, true)
		_box("deck", mid + y * 0.06, x, y, z, Vector3(len, 0.12, w - 1.2), Color.WHITE)
		for side in [-1.0, 1.0]:
			_box("steel", mid + y * 0.6 + z * side * (w * 0.5 - 0.25), x, y, z,
				Vector3(len, 1.2, 0.5), col)
	# Piers under the approaches (and under the main deck of the low and arch
	# kinds, where the spans are short): a pair of columns or a masonry wall.
	var kind := str(b["kind"])
	var s := float(prof[0][0]) + PIER_STEP * 0.5
	var masonry := b.has("stone")
	while s < float(prof[-1][0]):
		var on_main := s > a0 + 5.0 and s < a1 - 5.0
		var skip := on_main and kind in ["suspension", "cantilever", "arch_through", "arch_under"]
		if kind in ["swing", "lift"] and on_main:
			var sp: Array = b["span"]
			skip = s > float(sp[0]) - 8.0 and s < float(sp[1]) + 8.0
		var y := _profile_y(prof, s)
		if not skip and y > 4.0:
			var over_water := s > float(water[0]) and s < float(water[1])
			var base := -1.6 if over_water else 0.0
			var top := y - depth
			if masonry and not on_main:
				_block("stone", s, 0.0, (base + top) * 0.5, Vector3(6.0, top - base, w * 0.8), stone)
			else:
				for side in [-1.0, 1.0]:
					_block("steel" if not over_water else "stone", s, side * w * 0.3,
						(base + top) * 0.5, Vector3(2.4, top - base, 2.4),
						col.darkened(0.15) if not over_water else stone)
				_block("steel", s, 0.0, top - 0.8, Vector3(2.0, 1.6, w * 0.7), col.darkened(0.1), false)
		s += PIER_STEP
	# Abutments: masonry down to the street where the deck meets high ground.
	var ends: Array = b.get("ends", [])
	for k in ends.size():
		if str(ends[k][0]) != "abut":
			continue
		var se := float(prof[0][0]) if k == 0 else float(prof[-1][0])
		var y := _profile_y(prof, se)
		_block("stone", se, 0.0, y * 0.5 - 1.0, Vector3(20.0, y, w + 6.0), stone)
	# Lamps along both edges.
	var sl := float(prof[0][0])
	while sl < float(prof[-1][0]):
		var y := _profile_y(prof, sl)
		if y > 4.0:
			for side in [-1.0, 1.0]:
				_lights.append([_origin + _p(sl, side * (w * 0.5 - 1.0), y + 7.0),
					Color(1.0, 0.78, 0.48)])
		sl += 36.0


func _record_deck(b: Dictionary, prof: Array) -> void:
	var pts := PackedVector3Array()
	for pr in prof:
		var q: Vector2 = env.call("to_sim", _dataset(float(pr[0]), 0.0))
		pts.append(Vector3(q.x, q.y, float(pr[1])))
	_decks.append({"id": b["id"], "name": b["name"], "width": float(b["width"]),
		"y": float(b["deck_y"]), "pts": pts})
	_deck_y[str(b["id"])] = float(b["deck_y"])


# ------------------------------------------------------------------ suspension

func _build_suspension(b: Dictionary, prof: Array, col: Color, stone: Color) -> void:
	var w: float = float(b["width"])
	var t0: float = float(b["towers"][0])
	var t1: float = float(b["towers"][1])
	var a0: float = float(b["anchors"][0])
	var a1: float = float(b["anchors"][1])
	var top: float = float(b["tower_y"])
	var dy: float = float(b["deck_y"])
	var style := str(b.get("tower", "portal"))
	var water: Array = b["water"]
	var cable_lat := w * 0.5 + 0.8
	for t in [t0, t1]:
		var base := -1.6 if (t > float(water[0]) and t < float(water[1])) else 0.0
		match style:
			"gothic": _tower_gothic(t, base, dy, top, w, stone)
			"lattice": _tower_lattice(t, base, dy, top, w, col)
			_: _tower_portal(t, base, dy, top, w, col, stone)
		# Aviation beacons on the tower tops.
		for side in [-1.0, 1.0]:
			_lights.append([_origin + _p(t, side * cable_lat, top + 1.5), Color(1.0, 0.12, 0.08)])
	# Anchorages: masonry blocks the cables run down into.
	for an in [a0, a1]:
		var y := _profile_y(prof, an)
		_block("stone", an, 0.0, (y + 6.0) * 0.5, Vector3(40.0, y + 6.0, w + 12.0), stone)
	var n_cab := int(b.get("cables", 1))
	var cy_low := dy + float(b.get("camber", 0.0)) + 2.5
	for side in [-1.0, 1.0]:
		for k in n_cab:
			var lat: float = side * (cable_lat + k * 1.6)
			var pts := _cable_points(a0, t0, t1, a1, top - 1.5, cy_low, _profile_y(prof, a0) + 2.0,
				_profile_y(prof, a1) + 2.0)
			for i in range(pts.size() - 1):
				_beam("cables", _p(pts[i].x, lat, pts[i].y), _p(pts[i + 1].x, lat, pts[i + 1].y),
					1.1, col.darkened(0.1))
		# Suspenders (one line per side; the paired cables share them), and the
		# necklace lights strung along the cable.
		var s := a0 + HANGER_STEP
		while s < a1 - 4.0:
			if absf(s - t0) > 4.0 and absf(s - t1) > 4.0:
				var cy := _cable_y(s, a0, t0, t1, a1, top - 1.5, cy_low, _profile_y(prof, a0) + 2.0,
					_profile_y(prof, a1) + 2.0)
				var dk := _profile_y(prof, s) + 0.5
				if cy - dk > 1.0:
					_beam("fine", _p(s, side * cable_lat, dk), _p(s, side * cable_lat, cy), 0.35,
						col.darkened(0.2))
				if int(round(s / HANGER_STEP)) % 2 == 0:
					_lights.append([_origin + _p(s, side * cable_lat, cy + 0.8), Color(1.0, 0.93, 0.80)])
			s += HANGER_STEP
		# The cable curtain as solid boxes: deck to cable, stepped.
		for span in [[a0, t0], [t0, t1], [t1, a1]]:
			var n := maxi(1, int(ceil((span[1] - span[0]) / 40.0)))
			for i in n:
				var sa: float = lerpf(span[0], span[1], float(i) / n)
				var sb: float = lerpf(span[0], span[1], float(i + 1) / n)
				var sm := (sa + sb) * 0.5
				var dk := _profile_y(prof, sm)
				var cy := minf(_cable_y(sa, a0, t0, t1, a1, top, cy_low, dk, dk),
					_cable_y(sb, a0, t0, t1, a1, top, cy_low, dk, dk))
				if cy - dk > 2.0:
					_boxes.append([Transform3D(Basis(_along(), Vector3.UP, _across()),
						_p(sm, side * cable_lat, (dk + cy) * 0.5)), Vector3(sb - sa, cy - dk, 1.0)])
	# Brooklyn's diagonal stays: the web fanning from each tower to the deck.
	if b.get("stays", false):
		for t in [t0, t1]:
			for dir in [-1.0, 1.0]:
				for k in range(1, 13):
					var sd: float = t + dir * k * 15.0
					for side in [-1.0, 1.0]:
						_beam("fine", _p(t + dir * 4.0, side * cable_lat, top - 6.0),
							_p(sd, side * cable_lat, _profile_y(prof, sd) + 0.5), 0.3, col.darkened(0.15))
	# Stiffening truss along each side of the deck (the lower deck's frame).
	var lower: float = float(b.get("lower_deck", 0.0))
	if lower > 0.0:
		_side_truss(prof, a0, a1, w, lower, col)


## Cable height at s: a parabola between the towers, a sagging run from each
## tower top down to its anchorage.
func _cable_y(s: float, a0: float, t0: float, t1: float, a1: float, top: float, low: float,
		ya0: float, ya1: float) -> float:
	if s <= t0:
		var t := clampf((s - a0) / maxf(t0 - a0, 1.0), 0.0, 1.0)
		return ya0 + (top - ya0) * (0.35 * t + 0.65 * t * t)
	if s >= t1:
		var t := clampf((a1 - s) / maxf(a1 - t1, 1.0), 0.0, 1.0)
		return ya1 + (top - ya1) * (0.35 * t + 0.65 * t * t)
	var x := (s - (t0 + t1) * 0.5) / ((t1 - t0) * 0.5)
	return low + (top - low) * x * x


func _cable_points(a0: float, t0: float, t1: float, a1: float, top: float, low: float,
		ya0: float, ya1: float) -> Array:
	var out: Array = []
	for span in [[a0, t0], [t0, t1], [t1, a1]]:
		var n := maxi(2, int(ceil((span[1] - span[0]) / CURVE_STEP)))
		for i in n:
			var s: float = lerpf(span[0], span[1], float(i) / n)
			out.append(Vector2(s, _cable_y(s, a0, t0, t1, a1, top, low, ya0, ya1)))
	out.append(Vector2(a1, ya1))
	return out


func _side_truss(prof: Array, s0: float, s1: float, w: float, depth: float, col: Color) -> void:
	var step := 12.0
	var n := maxi(1, int((s1 - s0) / step))
	for side in [-1.0, 1.0]:
		var lat: float = side * (w * 0.5 - 0.4)
		for i in n:
			var sa := s0 + i * step
			var sb := sa + step
			var ya := _profile_y(prof, sa) - 2.5
			var yb := _profile_y(prof, sb) - 2.5
			_beam("fine", _p(sa, lat, ya - depth), _p(sb, lat, yb - depth), 0.9, col)
			_beam("fine", _p(sa, lat, ya - depth), _p(sa, lat, ya), 0.6, col)
			_beam("fine", _p(sa, lat, ya), _p(sb, lat, yb - depth), 0.5, col)


## GWB/Williamsburg: two open steel legs either side of the deck, lattice
## you can see through, tied by portal girders.
func _tower_lattice(t: float, base: float, dy: float, top: float, w: float, col: Color) -> void:
	var leg_a := 16.0      # along the bridge
	var leg_x := 13.0      # across
	var gap := w * 0.5 + leg_x * 0.5 + 1.0
	var h := top - base
	var levels := maxi(4, int(h / 12.0))
	var dark := col.darkened(0.12)
	for side in [-1.0, 1.0]:
		var lat: float = side * gap
		# Masonry pier to just above the water, then steel.
		_block("stone", t, lat, base + 2.5, Vector3(leg_a + 4.0, 7.0, leg_x + 4.0), Color(0.6, 0.58, 0.54) * ALBEDO_GAIN)
		var corners := []
		for i in [-0.5, 0.5]:
			for j in [-0.5, 0.5]:
				corners.append(Vector2(i * leg_a, j * leg_x))
		var taper := 0.72
		for c in corners:
			_beam("steel", _p(t + c.x, lat + c.y, base), _p(t + c.x * taper, lat + c.y * taper, top),
				3.0, col)
		for L in range(levels + 1):
			var y := base + h * L / float(levels)
			var k := lerpf(1.0, taper, float(L) / levels)
			for e in 4:
				var c0: Vector2 = corners[[0, 1, 3, 2][e]] * k
				var c1: Vector2 = corners[[1, 3, 2, 0][e]] * k
				_beam("steel", _p(t + c0.x, lat + c0.y, y), _p(t + c1.x, lat + c1.y, y),
					1.6 if L % 3 == 0 else 1.0, dark)
				if L < levels:
					var y2 := base + h * (L + 1) / float(levels)
					var k2 := lerpf(1.0, taper, float(L + 1) / levels)
					var c2: Vector2 = corners[[1, 3, 2, 0][e]] * k2
					var c3: Vector2 = corners[[0, 1, 3, 2][e]] * k2
					_beam("steel", _p(t + c0.x, lat + c0.y, y), _p(t + c2.x, lat + c2.y, y2), 1.2, dark)
					_beam("steel", _p(t + c1.x, lat + c1.y, y), _p(t + c3.x, lat + c3.y, y2), 1.2, dark)
		# Solid for strikes: each leg as one box (a lattice this dense is a wall
		# to an aircraft).
		_boxes.append([Transform3D(Basis(_along(), Vector3.UP, _across()),
			_p(t, lat, (base + top) * 0.5)), Vector3(leg_a, h, leg_x)])
		_record_tower(t, lat, leg_a, leg_x, top)
	# Portal girders: under the deck, two above it and a deep one at the top.
	for y in [dy - 9.0, lerpf(dy, top, 0.45), lerpf(dy, top, 0.75), top - 5.0]:
		var deep := 9.0 if y > top - 6.0 else 5.0
		_block("steel", t, 0.0, y, Vector3(leg_a * 0.6, deep, gap * 2.0 - leg_x * 0.5), col)


## Manhattan/RFK: solid steel columns, tapering, tied by struts, with a
## cap and a saddle housing on top.
func _tower_portal(t: float, base: float, dy: float, top: float, w: float, col: Color,
		stone: Color) -> void:
	var gap := w * 0.5 + 3.0
	_block("stone", t, 0.0, (base + dy - 12.0) * 0.5, Vector3(16.0, dy - 12.0 - base, gap * 2.0 + 10.0), stone)
	var y0 := dy - 12.0
	for side in [-1.0, 1.0]:
		var lat: float = side * gap
		# Two stacked boxes give the taper.
		var ym := lerpf(y0, top, 0.5)
		_block("steel", t, lat, (y0 + ym) * 0.5, Vector3(9.0, ym - y0, 6.5), col)
		_block("steel", t, lat, (ym + top) * 0.5, Vector3(7.0, top - ym, 5.0), col)
		_block("steel", t, lat, top + 1.5, Vector3(8.0, 3.0, 6.0), col.darkened(0.1))
		_record_tower(t, lat, 9.0, 6.5, top + 3.0)
	for f in [0.42, 0.7, 0.97]:
		var y := lerpf(dy, top, f)
		_block("steel", t, 0.0, y, Vector3(5.0, 4.0 if f < 0.9 else 6.0, gap * 2.0), col)
	# The arched portal between the struts: a few short beams.
	var ya := lerpf(dy, top, 0.7) - 2.0
	for i in 6:
		var x0 := -gap + gap * 2.0 * i / 6.0
		var x1 := -gap + gap * 2.0 * (i + 1) / 6.0
		var dip := func(x: float) -> float: return ya - 8.0 * (1.0 - (x / gap) * (x / gap))
		_beam("steel", _p(t, x0, dip.call(x0)), _p(t, x1, dip.call(x1)), 2.0, col)


## Brooklyn: granite, a base pier from the water to the deck, then three
## piers rising round two pointed (Gothic) arches the roadways run through,
## and a solid top above them.
func _tower_gothic(t: float, base: float, dy: float, top: float, w: float, stone: Color) -> void:
	var along := 20.0
	var across := 42.0
	var open_w := 10.4
	var spring := dy + 26.0
	var apex := dy + 35.5
	_tower_block("stone", t, 0.0, base, dy - 1.0, along + 2.0, across + 2.0, stone)
	_block("stone", t, 0.0, dy - 1.5, Vector3(along + 3.0, 1.5, across + 3.0), stone.darkened(0.06))
	# Columns: outer pair and the centre one, between and beside the openings.
	var centre_w := 7.0
	var outer_w := (across - centre_w - 2.0 * open_w) * 0.5
	var xs := [-across * 0.5 + outer_w * 0.5, 0.0, across * 0.5 - outer_w * 0.5]
	var ws := [outer_w, centre_w, outer_w]
	for i in 3:
		_block("stone", t, xs[i], (dy + apex) * 0.5, Vector3(along, apex - dy, ws[i]), stone)
	# The arch heads: vertical strips under the solid top, following a pointed
	# arch (two circular arcs meeting at the apex).
	for o in [-1.0, 1.0]:
		var c: float = o * (centre_w * 0.5 + open_w * 0.5)
		var n := 6
		for k in n:
			var x0: float = -open_w * 0.5 + open_w * k / n
			var x1: float = -open_w * 0.5 + open_w * (k + 1) / n
			var xm := (x0 + x1) * 0.5
			var r := absf(xm) / (open_w * 0.5)
			var yc := spring + (apex - spring) * sqrt(maxf(0.0, 1.0 - r * r * 0.82)) * (1.0 - r * 0.25)
			yc = clampf(yc, spring, apex)
			_block("stone", t, c + xm, (yc + apex) * 0.5 + 0.01, Vector3(along - 1.0, apex - yc, x1 - x0 + 0.02),
				stone, false)
	# The top: solid from the arches up, with a cornice.
	_tower_block("stone", t, 0.0, apex, top, along, across, stone)
	_block("stone", t, 0.0, top - 4.0, Vector3(along + 1.6, 1.4, across + 1.6), stone.darkened(0.08), false)
	_block("stone", t, 0.0, top + 0.6, Vector3(along - 2.0, 1.2, across - 2.0), stone.darkened(0.04), false)


# ------------------------------------------------------------------ cantilever

## Queensboro: masonry piers to the deck, steel towers with finials on them,
## and two deep side trusses whose top chord rises over every river pier and
## falls toward mid-span, the cantilever silhouette.
func _build_cantilever(b: Dictionary, prof: Array, col: Color, stone: Color) -> void:
	var w: float = float(b["width"])
	var piers: Array = b["piers"]
	var ptop: float = float(b["pier_top_y"])
	var dy: float = float(b["deck_y"])
	var truss: float = float(b.get("truss", 16.0))
	var water: Array = b["water"]
	var lat_t := w * 0.5 + 0.5
	var bottom := func(s: float) -> float: return _profile_y(prof, s) - 9.0
	var top_chord := func(s: float) -> float:
		# Highest over the four river piers, lowest at mid-span and the ends.
		var y := dy + 6.0
		for i in range(1, piers.size() - 1):
			var d: float = absf(s - float(piers[i]))
			var reach: float = 0.45 * minf(float(piers[i]) - float(piers[i - 1]),
				float(piers[i + 1]) - float(piers[i]))
			var k := clampf(1.0 - d / maxf(reach, 1.0), 0.0, 1.0)
			y = maxf(y, lerpf(dy + truss, ptop - 6.0, k * k * (3.0 - 2.0 * k)))
		return y
	for i in piers.size():
		var s: float = float(piers[i])
		var river := i > 0 and i < piers.size() - 1
		var base := -1.6 if (s > float(water[0]) - 30.0 and s < float(water[1]) + 30.0) else 0.0
		_tower_block("stone", s, 0.0, base, bottom.call(s), 14.0, w + 8.0, stone)
		if river:
			for side in [-1.0, 1.0]:
				_tower_block("steel", s, side * lat_t, bottom.call(s), ptop, 6.0, 3.0, col)
				# Finials.
				_beam("steel", _p(s, side * lat_t, ptop), _p(s, side * lat_t, ptop + 7.0), 1.0, col)
				_lights.append([_origin + _p(s, side * lat_t, ptop + 7.5), Color(1.0, 0.12, 0.08)])
			_block("steel", s, 0.0, ptop - 2.0, Vector3(4.0, 4.0, w + 1.0), col)
	var s0: float = float(piers[0])
	var s1: float = float(piers[-1])
	var step := 10.0
	var n := int((s1 - s0) / step)
	for side in [-1.0, 1.0]:
		var lat: float = side * lat_t
		for i in n:
			var sa := s0 + i * step
			var sb := sa + step
			var ta: float = top_chord.call(sa)
			var tb: float = top_chord.call(sb)
			var ba: float = bottom.call(sa)
			var bb: float = bottom.call(sb)
			_beam("steel", _p(sa, lat, ta), _p(sb, lat, tb), 2.6, col)
			_beam("steel", _p(sa, lat, ba), _p(sb, lat, bb), 2.2, col)
			_beam("steel", _p(sa, lat, ba), _p(sa, lat, ta), 1.2, col)
			# X lacing in each panel: the Queensboro is a dense web, not a line drawing.
			_beam("steel", _p(sa, lat, ta), _p(sb, lat, bb), 0.9, col)
			_beam("steel", _p(sa, lat, ba), _p(sb, lat, tb), 0.9, col)
			if i % 4 == 0:
				_lights.append([_origin + _p(sa, lat, ta + 1.5), Color(1.0, 0.93, 0.80)])
			# Solid: the truss plane, as a box per panel.
			_boxes.append([Transform3D(Basis(_along(), Vector3.UP, _across()),
				_p((sa + sb) * 0.5, lat, (minf(ba, bb) + maxf(ta, tb)) * 0.5)),
				Vector3(step, maxf(ta, tb) - minf(ba, bb), 1.2)])
		# Top lateral bracing between the trusses, every other panel.
	for i in range(0, n, 2):
		var sa := s0 + i * step
		var ta: float = top_chord.call(sa)
		_beam("fine", _p(sa, -lat_t, ta), _p(sa, lat_t, ta), 0.7, col)


# ------------------------------------------------------------------ arches

## Hell Gate: two stone towers and between them a steel through arch, the
## lower chord springing from the towers' feet, the upper one sweeping high
## to meet them, the deck hung through the middle.
func _build_arch_through(b: Dictionary, prof: Array, col: Color, stone: Color) -> void:
	var w: float = float(b["width"])
	var t0: float = float(b["towers"][0])
	var t1: float = float(b["towers"][1])
	var crown: float = float(b["crown_y"])
	var ttop: float = float(b["tower_y"])
	var water: Array = b["water"]
	for t in [t0, t1]:
		var base := -1.6 if (t > float(water[0]) and t < float(water[1])) else 0.0
		_tower_block("stone", t, 0.0, base, ttop, 22.0, w + 12.0, stone)
		_block("stone", t, 0.0, ttop + 1.0, Vector3(24.0, 2.0, w + 14.0), stone.darkened(0.08), false)
		# Its four corner turrets.
		for i in [-1.0, 1.0]:
			for j in [-1.0, 1.0]:
				_block("stone", t + i * 9.0, j * (w * 0.5 + 4.0), ttop + 4.0, Vector3(4.0, 6.0, 4.0), stone, false)
	var lat := w * 0.5 - 1.0
	var spring := 4.0
	var n := 24
	var half := (t1 - t0) * 0.5 - 11.0
	var mid := (t0 + t1) * 0.5
	var lower := func(x: float) -> float: return spring + (crown - 12.0 - spring) * (1.0 - x * x)
	var upper := func(x: float) -> float:
		return lerpf(ttop - 6.0, crown, 1.0 - x * x) if absf(x) < 1.0 else ttop - 6.0
	for side in [-1.0, 1.0]:
		var ls: float = side * lat
		for i in n:
			var xa := -1.0 + 2.0 * i / n
			var xb := -1.0 + 2.0 * (i + 1) / n
			var sa := mid + xa * half
			var sb := mid + xb * half
			_beam("steel", _p(sa, ls, lower.call(xa)), _p(sb, ls, lower.call(xb)), 3.0, col, true)
			_beam("steel", _p(sa, ls, upper.call(xa)), _p(sb, ls, upper.call(xb)), 2.2, col)
			_beam("fine", _p(sa, ls, lower.call(xa)), _p(sa, ls, upper.call(xa)), 1.0, col)
			_beam("fine", _p(sa, ls, lower.call(xa)) if i % 2 == 0 else _p(sa, ls, upper.call(xa)),
				_p(sb, ls, upper.call(xb)) if i % 2 == 0 else _p(sb, ls, lower.call(xb)), 0.8, col)
			# Hangers from the lower chord to the deck where it is above it.
			var dk := _profile_y(prof, sa)
			if lower.call(xa) > dk + 2.0:
				_beam("fine", _p(sa, ls, dk), _p(sa, ls, lower.call(xa)), 0.5, col)
	for i in range(1, n, 2):
		var x := -1.0 + 2.0 * i / n
		_beam("fine", _p(mid + x * half, -lat, upper.call(x)), _p(mid + x * half, lat, upper.call(x)), 0.8, col)


## Deck-arch bridges: steel arch ribs under the deck, spandrel posts up to it.
func _build_arch_under(b: Dictionary, prof: Array, col: Color, stone: Color) -> void:
	var w: float = float(b["width"])
	var water: Array = b["water"]
	var masonry := b.has("stone")
	for ar in b["arches"]:
		var s0: float = float(ar[0])
		var s1: float = float(ar[1])
		var mid := (s0 + s1) * 0.5
		var half := (s1 - s0) * 0.5
		var base0 := -1.6 if (s0 > float(water[0]) and s0 < float(water[1])) else 0.0
		var crown := _profile_y(prof, mid) - 3.5
		var rib := func(x: float) -> float: return base0 + 2.0 + (crown - base0 - 2.0) * (1.0 - x * x)
		for sp in [s0, s1]:
			_block("stone", sp, 0.0, (base0 + 8.0) * 0.5, Vector3(14.0, 10.0, w + 4.0), stone)
		var n := 16
		for side in [-1.0, 1.0]:
			var lat: float = side * (w * 0.5 - 2.5)
			for i in n:
				var xa := -1.0 + 2.0 * i / n
				var xb := -1.0 + 2.0 * (i + 1) / n
				_beam("steel", _p(mid + xa * half, lat, rib.call(xa)), _p(mid + xb * half, lat, rib.call(xb)),
					3.2, col, true)
				var dk := _profile_y(prof, mid + xa * half) - 2.5
				if dk - rib.call(xa) > 1.5 and i > 0:
					_beam("fine", _p(mid + xa * half, lat, rib.call(xa)), _p(mid + xa * half, lat, dk), 0.9, col)
		for i in range(0, n, 2):
			var x := -1.0 + 2.0 * i / n
			_beam("fine", _p(mid + x * half, -(w * 0.5 - 2.5), rib.call(x)),
				_p(mid + x * half, w * 0.5 - 2.5, rib.call(x)), 0.8, col)
	# Masonry arcade on the land spans (High Bridge, Washington Bridge).
	if masonry:
		var step := float(b.get("arcade", 30.0))
		var arches: Array = b["arches"]
		var s := float(prof[0][0]) + step * 0.5
		while s < float(prof[-1][0]):
			var inside := false
			for ar in arches:
				if s > float(ar[0]) - 6.0 and s < float(ar[1]) + 6.0:
					inside = true
			var y := _profile_y(prof, s) - 2.5
			if not inside and y > 4.0:
				_block("stone", s, 0.0, y * 0.5, Vector3(5.0, y, w + 1.0), stone)
				_block("stone", s + step * 0.5, 0.0, y - 2.5, Vector3(step - 5.0, 5.0, w + 0.6), stone, false)
			s += step


# ------------------------------------------------------------------ movable

## Swing span: a through truss over the channel on a centre pivot pier.
func _build_swing(b: Dictionary, prof: Array, col: Color) -> void:
	var w: float = float(b["width"])
	var sp: Array = b["span"]
	var s0: float = float(sp[0])
	var s1: float = float(sp[1])
	var th: float = float(b.get("truss_h", 12.0))
	var mid := (s0 + s1) * 0.5
	var dk := _profile_y(prof, mid)
	_block("stone", mid, 0.0, (dk - 3.6) * 0.5, Vector3(14.0, dk - 0.4, w + 2.0),
		Color(0.55, 0.54, 0.52) * ALBEDO_GAIN)
	_through_truss(prof, s0, s1, w, th, col, true)
	# Fender piers at the span ends.
	for s in [s0, s1]:
		_block("stone", s, 0.0, (_profile_y(prof, s) - 3.6) * 0.5,
			Vector3(6.0, _profile_y(prof, s) - 0.4, w), Color(0.55, 0.54, 0.52) * ALBEDO_GAIN)


## Vertical lift: a truss span between two pairs of steel towers, the
## machinery houses on top.
func _build_lift(b: Dictionary, prof: Array, col: Color) -> void:
	var w: float = float(b["width"])
	var sp: Array = b["span"]
	var s0: float = float(sp[0])
	var s1: float = float(sp[1])
	var top: float = float(b["tower_y"])
	_through_truss(prof, s0, s1, w, 8.0, col, false)
	for s in [s0, s1]:
		var dk := _profile_y(prof, s)
		_block("stone", s, 0.0, (dk - 3.6) * 0.5, Vector3(9.0, dk - 0.4, w + 4.0),
			Color(0.55, 0.54, 0.52) * ALBEDO_GAIN)
		for side in [-1.0, 1.0]:
			var lat: float = side * (w * 0.5 + 1.8)
			_tower_block("steel", s, lat, dk - 2.0, top, 4.0, 3.0, col)
			_beam("fine", _p(s - 2.0, lat, dk), _p(s + 2.0, lat, top - 2.0), 0.4, col)
			_lights.append([_origin + _p(s, lat, top + 0.6), Color(1.0, 0.12, 0.08)])
		_block("steel", s, 0.0, top - 1.0, Vector3(6.0, 4.0, w + 7.0), col.darkened(0.1))


func _through_truss(prof: Array, s0: float, s1: float, w: float, th: float, col: Color,
		camel: bool) -> void:
	var n := maxi(4, int((s1 - s0) / 10.0))
	var lat := w * 0.5 + 0.3
	for side in [-1.0, 1.0]:
		var l: float = side * lat
		for i in n:
			var sa := lerpf(s0, s1, float(i) / n)
			var sb := lerpf(s0, s1, float(i + 1) / n)
			var xa := float(i) / n * 2.0 - 1.0
			var xb := float(i + 1) / n * 2.0 - 1.0
			var ha := th * (1.0 - 0.35 * xa * xa) if camel else th
			var hb := th * (1.0 - 0.35 * xb * xb) if camel else th
			var ya := _profile_y(prof, sa)
			var yb := _profile_y(prof, sb)
			_beam("steel", _p(sa, l, ya + ha), _p(sb, l, yb + hb), 0.9, col)
			_beam("steel", _p(sa, l, ya), _p(sb, l, yb), 0.9, col)
			_beam("fine", _p(sa, l, ya), _p(sa, l, ya + ha), 0.6, col)
			_beam("fine", _p(sa, l, ya + ha) if xa < 0.0 else _p(sa, l, ya),
				_p(sb, l, yb) if xa < 0.0 else _p(sb, l, yb + hb), 0.5, col)
			_boxes.append([Transform3D(Basis(_along(), Vector3.UP, _across()),
				_p((sa + sb) * 0.5, l, (ya + yb) * 0.5 + maxf(ha, hb) * 0.5)), Vector3(sb - sa, maxf(ha, hb), 0.8)])
	for i in range(0, n + 1, 2):
		var s := lerpf(s0, s1, float(i) / n)
		var x := float(i) / n * 2.0 - 1.0
		var h := th * (1.0 - 0.35 * x * x) if camel else th
		_beam("fine", _p(s, -lat, _profile_y(prof, s) + h), _p(s, lat, _profile_y(prof, s) + h), 0.5, col)


# ------------------------------------------------------------------ tram

## Roosevelt Island Tramway: two stations, three towers, the track ropes
## sagging between them and its two red cabins parked on the line.
func _build_tram(b: Dictionary, col: Color) -> void:
	var length: float = float(b["length"])
	var sh: float = float(b.get("station_h", 18.0))
	var cab := _srgb(b.get("cabin", [0.8, 0.1, 0.1]))
	var sup: Array = [[0.0, sh]]
	for t in b["towers"]:
		sup.append([float(t[0]), float(t[1])])
	sup.append([length, sh])
	_block("steel", 0.0, 0.0, sh * 0.5, Vector3(30.0, sh, 24.0), Color(0.62, 0.62, 0.62) * ALBEDO_GAIN)
	_block("steel", length, 0.0, sh * 0.5, Vector3(30.0, sh, 24.0), Color(0.62, 0.62, 0.62) * ALBEDO_GAIN)
	for i in range(1, sup.size() - 1):
		var s: float = sup[i][0]
		var h: float = sup[i][1]
		# A tapering steel column with a crosshead carrying both pairs of ropes.
		_tower_block("steel", s, 0.0, 0.0, h * 0.5, 7.0, 5.0, col)
		_block("steel", s, 0.0, h * 0.75, Vector3(5.0, h * 0.5, 3.6), col)
		_block("steel", s, 0.0, h, Vector3(3.0, 2.0, 18.0), col.darkened(0.1))
		_lights.append([_origin + _p(s, 0.0, h + 1.5), Color(1.0, 0.12, 0.08)])
	for lat in [-7.0, -6.0, 6.0, 7.0]:
		for i in range(sup.size() - 1):
			var sa: float = sup[i][0]
			var sb: float = sup[i + 1][0]
			var ya: float = float(sup[i][1]) + 0.5
			var yb: float = float(sup[i + 1][1]) + 0.5
			var n := maxi(2, int((sb - sa) / CURVE_STEP))
			var sag := (sb - sa) * 0.03
			for k in n:
				var ta := float(k) / n
				var tb := float(k + 1) / n
				_beam("cables", _p(lerpf(sa, sb, ta), lat, lerpf(ya, yb, ta) - sag * 4.0 * ta * (1.0 - ta)),
					_p(lerpf(sa, sb, tb), lat, lerpf(ya, yb, tb) - sag * 4.0 * tb * (1.0 - tb)), 0.25,
					Color(0.2, 0.2, 0.2))
	# Cabins, one each way.
	for c in [[360.0, -6.5], [700.0, 6.5]]:
		var s: float = c[0]
		var y := _tram_rope_y(sup, s) - 6.0
		_block("steel", s, c[1], y, Vector3(9.0, 3.6, 4.2), cab)
		_beam("fine", _p(s, c[1], y + 1.8), _p(s, c[1], y + 6.0), 0.3, Color(0.25, 0.25, 0.25))


func _tram_rope_y(sup: Array, s: float) -> float:
	for i in range(sup.size() - 1):
		var sa: float = sup[i][0]
		var sb: float = sup[i + 1][0]
		if s >= sa and s <= sb:
			var t := (s - sa) / (sb - sa)
			return lerpf(float(sup[i][1]), float(sup[i + 1][1]), t) - (sb - sa) * 0.03 * 4.0 * t * (1.0 - t)
	return 0.0


# ------------------------------------------------------------------ commit

## Draw calls: the first build gave every bridge its own mesh per material,
## ~100 draws plus their shadow passes for 24 bridges (island_wide went from
## 58 to 270 draws with the far shore). The full-range parts are now one
## mesh per CLUSTER_M cell of the city (six draws for all the bridges);
## only the fine parts (hangers, stays, truss lacing), which are range-culled
## at FINE_RANGE, stay per bridge, so just the bridges near the camera draw them.
const CLUSTER_M := 4000.0
var _clusters := {}     # Vector2i -> {"origin": Vector3, "st": {cls: SurfaceTool}}


func _commit(id: String) -> void:
	var holder := Node3D.new()
	holder.name = "bridge_" + id
	holder.position = _origin
	add_child(holder)
	var key := Vector2i(int(floor(_origin.x / CLUSTER_M)), int(floor(-_origin.z / CLUSTER_M)))
	if not _clusters.has(key):
		_clusters[key] = {"origin": _origin, "st": {}}
	var cl: Dictionary = _clusters[key]
	var shift := Transform3D(Basis.IDENTITY, _origin - (cl["origin"] as Vector3))
	for cls in _st:
		var st: SurfaceTool = _st[cls]
		var mesh := st.commit()
		if mesh.get_surface_count() == 0:
			continue
		if cls == "fine":
			mesh.surface_set_material(0, _mat[cls])
			var mi := MeshInstance3D.new()
			mi.name = "bridge_fine_" + id
			mi.mesh = mesh
			mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
			mi.visibility_range_end = FINE_RANGE
			mi.visibility_range_end_margin = 300.0
			holder.add_child(mi)
			continue
		var into: String = cls
		var sts: Dictionary = cl["st"]
		if not sts.has(into):
			var cst := SurfaceTool.new()
			cst.begin(Mesh.PRIMITIVE_TRIANGLES)
			sts[into] = cst
		(sts[into] as SurfaceTool).append_from(mesh, 0, shift)
	if not _boxes.is_empty():
		var body := StaticBody3D.new()
		body.name = "bridge_" + id
		body.collision_layer = 1     # LAYER_STRUCTURE (fixedwing_manager.gd)
		body.collision_mask = 0
		body.set_meta("bridge", id)
		for bx in _boxes:
			var cs := CollisionShape3D.new()
			var shape := BoxShape3D.new()
			shape.size = bx[1]
			cs.shape = shape
			var xf: Transform3D = bx[0]
			cs.transform = Transform3D(xf.basis.orthonormalized(), xf.origin)
			body.add_child(cs)
		holder.add_child(body)
	_boxes = []


func _commit_clusters() -> void:
	var k := 0
	for key in _clusters:
		var cl: Dictionary = _clusters[key]
		for cls in cl["st"]:
			var mesh: ArrayMesh = (cl["st"][cls] as SurfaceTool).commit()
			mesh.surface_set_material(0, _mat[cls])
			var mi := MeshInstance3D.new()
			mi.name = "bridge_c%d" % k
			mi.mesh = mesh
			mi.position = cl["origin"]
			mi.visibility_range_end = MAIN_RANGE
			add_child(mi)
		k += 1
	_clusters.clear()


## Night lights: one MultiMesh of camera-facing points, sized up with range so
## a necklace still reads as a string of points from across the river.
func _build_lights() -> void:
	if _lights.is_empty():
		return
	var quad := QuadMesh.new()
	quad.size = Vector2.ONE
	var sh := Shader.new()
	sh.code = LIGHT_SHADER
	_light_mat = ShaderMaterial.new()
	_light_mat.shader = sh
	_light_mat.set_shader_parameter("night", 0.0)
	quad.material = _light_mat
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_colors = true
	mm.mesh = quad
	mm.instance_count = _lights.size()
	for i in _lights.size():
		mm.set_instance_transform(i, Transform3D(Basis.IDENTITY, _lights[i][0]))
		mm.set_instance_color(i, _lights[i][1])
	var mmi := MultiMeshInstance3D.new()
	mmi.name = "bridge_lights"
	mmi.multimesh = mm
	mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mmi)


const LIGHT_SHADER := """
shader_type spatial;
render_mode unshaded, blend_add, depth_draw_never, cull_disabled, shadows_disabled, fog_disabled;
uniform float night = 0.0;
varying vec4 v_col;
void vertex() {
    v_col = COLOR;
    vec3 c = (VIEW_MATRIX * vec4(MODEL_MATRIX[3].xyz, 1.0)).xyz;
    float d = length(c);
    // ~3.5 px at any range, never under 1.2 m.
    float size = max(1.2, d * 0.004);
    MODELVIEW_MATRIX = VIEW_MATRIX * mat4(INV_VIEW_MATRIX[0], INV_VIEW_MATRIX[1],
        INV_VIEW_MATRIX[2], MODEL_MATRIX[3]);
    VERTEX *= size;
}
void fragment() {
    if (night < 0.02) { discard; }
    float r = length(UV - 0.5) * 2.0;
    float a = clamp(1.0 - r, 0.0, 1.0);
    a = a * a;
    ALBEDO = v_col.rgb * a * night * 3.0;
}
"""


# ------------------------------------------------------------------ structure model

## Tower footprints into the env's analytic box model, where they fall inside
## its occupancy grid. Appends to _rects/_heights/WALLS and indexes only the
## new entries (env._build_index() appends rather than rebuilding, so calling
## it, or add_wall() per tower, would index every building again each time),
## then has FixedWingManager rebuild the grid once.
##
## These rects are NOT buildings: their indices start at
## env.get_meta("bridge_wall_first"); anything walking env._rects for scenery
## (rooftop kit and the like) should stop there.
func _add_walls() -> void:
	if _towers.is_empty() or not ("_rects" in env):
		return
	var grid: Rect2 = env.call("sim_bounds")
	var first: int = env._rects.size()
	for t in _towers:
		var r: Rect2 = t[0]
		var sim := Rect2(env.call("to_sim", r.position), r.size)
		if not grid.intersects(sim):
			continue
		var h: float = float(t[1])
		var i: int = env._rects.size()
		env._rects.append(sim)
		# Packed arrays are values: modify a copy and assign it back, never
		# env._heights.append() through the property.
		var hs: PackedFloat32Array = env._heights
		hs.append(h)
		env._heights = hs
		env.WALLS.append(sim)
		env._tallest = maxf(env._tallest, h)
		var tile: float = EnvManhattan.TILE
		for tx in range(int(floor(sim.position.x / tile)), int(floor(sim.end.x / tile)) + 1):
			for ty in range(int(floor(sim.position.y / tile)), int(floor(sim.end.y / tile)) + 1):
				var key := Vector2i(tx, ty)
				var lst: PackedInt32Array = env._index.get(key, PackedInt32Array())
				lst.append(i)
				env._index[key] = lst
				env._tile_max_h[key] = maxf(float(env._tile_max_h.get(key, 0.0)), h)
		_walls_added += 1
	env.set_meta("bridge_wall_first", first)
	if _walls_added > 0:
		FixedWingManager.register_env(env)

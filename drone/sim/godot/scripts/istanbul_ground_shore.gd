## Istanbul's edge with the sea: the dressed-stone quays (rihtim) of the
## Bosphorus and the Golden Horn, the rock armour of the Marmara shore, the
## ferry piers, their bollards and the promenade railings. A child of
## istanbul_ground.gd.
##
## The ground is cut at the shoreline field's zero, which is the OSM
## coastline that assets/istanbul/coast.json holds as runs with the water on
## their right (tools/fetch_istanbul_ground.py). Along each run:
##   quay    a limestone face from the seabed up to a pale cope 15 cm proud of
##           the ground, 0.9 m wide, hiding the cut; a dark, weedy tidal band
##           (the Bosphorus barely has a tide: ~0.3 m); railings along the
##           promenades, bollards near the ferry landings
##   rocks   rock armour: a band of grey limestone boulders sloping from the
##           promenade's edge 6 m out and 2.5 m down into the sea (Kennedy
##           Caddesi, Yenikapi's reclaimed land, Moda), wet and dark below the
##           waterline
## Piers (roads.json, OSM man_made=pier): concrete decks 1.4 m above the sea
## on their edge faces; a pier mapped as a line is a 4 m walkway.
##
## Visual only. Everything here is at or below the promenade (piers 1.4 m
## above the sea): an aircraft that low has already struck the water or the
## ground, so none of it changes strike detection.
extends Node3D

const FOOT := -8.0
const COPE_UP := 0.15
const COPE_IN := 0.9
const FACE_OUT := 0.1
const RAIL_H := 1.0
const DECK_Y := 1.4
const CHUNK := 1000.0
const PIER_CLEAR := 70.0

var ground: Node3D
var env: Node3D
var lengths := {"quay": 0.0, "rocks": 0.0, "railing": 0.0, "piers": 0}
var _piers: Array = []          # PackedVector2Array outlines
var _pier_pts := PackedVector2Array()


func _ready() -> void:
	name = "shore"
	env = ground.get("env")
	var t0 := Time.get_ticks_msec()
	_load_piers()
	var quay := {}
	var rocks := {}
	var rails := {}
	var bollards: Array[Transform3D] = []
	var path := "res://assets/istanbul/coast.json"
	if FileAccess.file_exists(path):
		var doc: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(path))
		for line: Dictionary in doc.get("lines", []):
			var pts := PackedVector2Array()
			for q: Array in line["pts"]:
				pts.append(Vector2(float(q[0]), float(q[1])))
			if pts.size() < 2:
				continue
			if str(line["kind"]) == "rocks":
				_rocks(pts, rocks)
			else:
				_quay(pts, quay)
				_quay_dress(pts, rails, bollards)
	var decks := {}
	for outline: PackedVector2Array in _piers:
		_deck(outline, decks, bollards)
	_emit(quay, "quay", _stone_material(), 6000.0)
	_emit(rocks, "rocks", _rock_material(), 6000.0)
	_emit(rails, "railing", _rail_material(), 800.0)
	_emit(decks, "pier", _deck_material(), 6000.0)
	_bollards(bollards)
	print("[istanbul_ground_shore] %.1f km quay, %.1f km rocks, %.1f km railing, %d piers, %d bollards, %d ms" %
		[lengths["quay"] / 1000.0, lengths["rocks"] / 1000.0, lengths["railing"] / 1000.0,
		 lengths["piers"], bollards.size(), Time.get_ticks_msec() - t0])


func _load_piers() -> void:
	var path := "res://assets/istanbul/roads.json"
	if not FileAccess.file_exists(path):
		return
	var doc: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(path))
	for p: Dictionary in doc.get("piers", []):
		var pts := PackedVector2Array()
		for q: Array in p["pts"]:
			pts.append(Vector2(float(q[0]), float(q[1])))
		if pts.size() < 2:
			continue
		if bool(p["closed"]) and pts.size() >= 4:
			pts.remove_at(pts.size() - 1)
			if Geometry2D.is_polygon_clockwise(pts):
				pts.reverse()
		else:
			# A walkway: the line grown 2 m each side.
			var poly := Geometry2D.offset_polyline(pts, 2.0, Geometry2D.JOIN_MITER, Geometry2D.END_SQUARE)
			if poly.is_empty():
				continue
			pts = poly[0]
			if Geometry2D.is_polygon_clockwise(pts):
				pts.reverse()
		_piers.append(pts)
		_pier_pts.append_array(pts)


func _near_pier(p: Vector2, r: float) -> bool:
	for q in _pier_pts:
		if q.distance_squared_to(p) < r * r:
			return true
	return false


func _st(acc: Dictionary, p: Vector2) -> SurfaceTool:
	var k := Vector2i(int(floor(p.x / CHUNK)), int(floor(p.y / CHUNK)))
	if not acc.has(k):
		var st := SurfaceTool.new()
		st.begin(Mesh.PRIMITIVE_TRIANGLES)
		acc[k] = st
	return acc[k]


func _emit(acc: Dictionary, nm: String, mat: Material, far: float) -> void:
	for k: Vector2i in acc:
		var mi := MeshInstance3D.new()
		mi.name = "%s_%d_%d" % [nm, k.x, k.y]
		var st: SurfaceTool = acc[k]
		st.index()
		mi.mesh = st.commit()
		mi.material_override = mat
		mi.visibility_range_end = far
		mi.visibility_range_end_margin = far * 0.1
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(mi)


## Seaward unit normals per vertex (the water is on the right of the run),
## mitred so offset lines stay parallel, capped at sharp corners.
static func _normals(pts: PackedVector2Array) -> PackedVector2Array:
	var out := PackedVector2Array()
	out.resize(pts.size())
	for i in pts.size():
		var acc := Vector2.ZERO
		if i > 0:
			var t := (pts[i] - pts[i - 1]).normalized()
			acc += Vector2(t.y, -t.x)
		if i < pts.size() - 1:
			var t := (pts[i + 1] - pts[i]).normalized()
			acc += Vector2(t.y, -t.x)
		var c := acc.length() * 0.5
		out[i] = acc.normalized() / maxf(c, 0.5)
	return out


static func _v(p: Vector2, y: float) -> Vector3:
	return Vector3(p.x, y, -p.y)


## a-b-c-d counter-clockwise seen from the side it faces; UVs in metres.
static func _quad(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, d: Vector3,
		ua: Vector2, ub: Vector2, uc: Vector2, ud: Vector2) -> void:
	var n := (b - a).cross(d - a).normalized()
	for v in [[a, ua], [c, uc], [b, ub], [a, ua], [d, ud], [c, uc]]:
		st.set_normal(n)
		st.set_uv(v[1])
		st.add_vertex(v[0])


## The ground at the shoreline: the drawn mesh a little inland.
func _top(p: Vector2, s: Vector2) -> float:
	return maxf(float(ground.call("mesh_height", p.x - s.x * 1.5, p.y - s.y * 1.5)), 0.6)


func _quay(pts: PackedVector2Array, acc: Dictionary) -> void:
	var s := _normals(pts)
	var tops := PackedFloat32Array()
	for i in pts.size():
		tops.append(_top(pts[i], s[i]) + COPE_UP)
	var along := 0.0
	for i in range(1, pts.size()):
		var a := pts[i - 1]
		var b := pts[i]
		var d := a.distance_to(b)
		var st := _st(acc, (a + b) * 0.5)
		var ta := tops[i - 1]
		var tb := tops[i]
		var fa := a + s[i - 1] * FACE_OUT
		var fb := b + s[i] * FACE_OUT
		var ia := a - s[i - 1] * COPE_IN
		var ib := b - s[i] * COPE_IN
		# Face, seen from the sea (the run goes left to right from there).
		_quad(st, _v(fa, FOOT), _v(fb, FOOT), _v(fb, tb), _v(fa, ta),
			Vector2(along, FOOT), Vector2(along + d, FOOT), Vector2(along + d, tb), Vector2(along, ta))
		# Cope top (UV.y < -10 marks a top for the shader).
		_quad(st, _v(fa, ta), _v(fb, tb), _v(ib, tb), _v(ia, ta),
			Vector2(along, -20.0), Vector2(along + d, -20.0), Vector2(along + d, -21.0), Vector2(along, -21.0))
		# The cope's landward step down into the ground.
		_quad(st, _v(ia, ta), _v(ib, tb), _v(ib, tb - COPE_UP - 0.3), _v(ia, ta - COPE_UP - 0.3),
			Vector2(along, ta), Vector2(along + d, tb), Vector2(along + d, tb - 0.45), Vector2(along, ta - 0.45))
		along += d
	lengths["quay"] += along


## Railings along the promenade quays (not at the ferry landings, where
## boats tie up), bollards at the landings.
func _quay_dress(pts: PackedVector2Array, rails: Dictionary, bollards: Array[Transform3D]) -> void:
	var s := _normals(pts)
	const STEP := 3.0
	var along := 0.0
	var next_bollard := 0.0
	for i in range(1, pts.size()):
		var a := pts[i - 1]
		var b := pts[i]
		var d := a.distance_to(b)
		var n := maxi(1, int(ceil(d / STEP)))
		for k in n:
			var t0 := float(k) / n
			var t1 := float(k + 1) / n
			var p0 := a.lerp(b, t0) - s[i - 1].lerp(s[i], t0) * 0.35
			var p1 := a.lerp(b, t1) - s[i - 1].lerp(s[i], t1) * 0.35
			var mid := (p0 + p1) * 0.5
			var seg := d / n
			var sea := s[i - 1].lerp(s[i], (t0 + t1) * 0.5)
			var landing := _near_pier(mid, PIER_CLEAR)
			if landing:
				if along >= next_bollard and _near_pier(mid, 250.0):
					var y := _top(mid, sea) + COPE_UP
					bollards.append(Transform3D(Basis(), _v(mid, y)))
					next_bollard = along + 12.0
			elif int(env.call("building_at", mid.x - sea.x * 2.0, mid.y - sea.y * 2.0)) < 0:
				var y0 := _top(p0, sea) + COPE_UP
				var y1 := _top(p1, sea) + COPE_UP
				_quad(_st(rails, mid), _v(p0, y0), _v(p1, y1), _v(p1, y1 + RAIL_H), _v(p0, y0 + RAIL_H),
					Vector2(along, 0.0), Vector2(along + seg, 0.0), Vector2(along + seg, RAIL_H), Vector2(along, RAIL_H))
				lengths["railing"] += seg
			along += seg


## Rock armour: four rows of vertices from just inland of the shoreline
## (the promenade's level) down and out into the sea: offsets seaward (m).
const ROCK_OFF := [-0.6, 1.0, 3.8, 6.5]


func _rock_y(top: float, row: int) -> float:
	match row:
		0:
			return top
		1:
			return maxf(minf(top - 0.5, 1.6), 0.7)
		2:
			return 0.05
	return -2.8


func _rocks(pts: PackedVector2Array, acc: Dictionary) -> void:
	var s := _normals(pts)
	var along := 0.0
	for i in range(1, pts.size()):
		var a := pts[i - 1]
		var b := pts[i]
		var d := a.distance_to(b)
		var st := _st(acc, (a + b) * 0.5)
		var ta := _top(a, s[i - 1]) + 0.1
		var tb := _top(b, s[i]) + 0.1
		for r in range(ROCK_OFF.size() - 1):
			var o0: float = ROCK_OFF[r]
			var o1: float = ROCK_OFF[r + 1]
			var pa0 := a + s[i - 1] * o0
			var pb0 := b + s[i] * o0
			var pa1 := a + s[i - 1] * o1
			var pb1 := b + s[i] * o1
			_quad(st, _v(pa1, _rock_y(ta, r + 1)), _v(pb1, _rock_y(tb, r + 1)),
				_v(pb0, _rock_y(tb, r)), _v(pa0, _rock_y(ta, r)),
				Vector2(along, o1), Vector2(along + d, o1), Vector2(along + d, o0), Vector2(along, o0))
		along += d
	lengths["rocks"] += along


## A pier: its deck (triangulated outline) and its edge faces down into the sea.
func _deck(outline: PackedVector2Array, acc: Dictionary, bollards: Array[Transform3D]) -> void:
	var tri := Geometry2D.triangulate_polygon(outline)
	if tri.is_empty():
		return
	var c := Vector2.ZERO
	for p in outline:
		c += p
	c /= outline.size()
	var st := _st(acc, c)
	# The deck meets the land at the land's height where it starts ashore.
	var land := 0.0
	for p in outline:
		if not env.call("is_water", p.x, p.y):
			land = maxf(land, float(ground.call("mesh_height", p.x, p.y)))
	var y := maxf(DECK_Y, minf(land, 3.0))
	for k in range(0, tri.size(), 3):
		var p0 := outline[tri[k]]
		var p1 := outline[tri[k + 1]]
		var p2 := outline[tri[k + 2]]
		var verts := [_v(p0, y), _v(p1, y), _v(p2, y)]
		# Clockwise from above: the outline is CCW in ENU, which is clockwise
		# seen from above in Godot's (x, -n) only if reversed.
		if (verts[1] - verts[0]).cross(verts[2] - verts[0]).y > 0.0:
			verts = [verts[0], verts[2], verts[1]]
		for v: Vector3 in verts:
			st.set_normal(Vector3.UP)
			st.set_uv(Vector2(v.x, v.z))
			st.add_vertex(v)
	var along := 0.0
	var next_b := 4.0
	for i in outline.size():
		var a := outline[i]
		var b := outline[(i + 1) % outline.size()]
		var d := a.distance_to(b)
		# CCW outline: the outside is on the right of a -> b.
		_quad(st, _v(a, -2.0), _v(b, -2.0), _v(b, y), _v(a, y),
			Vector2(along, -2.0), Vector2(along + d, -2.0), Vector2(along + d, y), Vector2(along, y))
		var t := (b - a) / maxf(d, 0.01)
		var out := Vector2(t.y, -t.x)
		var s := next_b - along
		while s < d:
			var p := a + t * s - out * 0.5
			if env.call("is_water", p.x + out.x * 2.0, p.y + out.y * 2.0):
				bollards.append(Transform3D(Basis(), _v(p, y)))
			s += 9.0
		next_b = along + s
		along += d
	lengths["piers"] += 1


func _bollards(list: Array[Transform3D]) -> void:
	if list.is_empty():
		return
	var cyl := CylinderMesh.new()
	cyl.top_radius = 0.16
	cyl.bottom_radius = 0.2
	cyl.height = 0.6
	cyl.radial_segments = 8
	cyl.rings = 1
	var m := StandardMaterial3D.new()
	m.albedo_color = Color(0.06, 0.06, 0.065)
	m.roughness = 0.5
	m.metallic = 0.4
	cyl.material = m
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.mesh = cyl
	mm.instance_count = list.size()
	for i in list.size():
		var xf := list[i]
		xf.origin.y += 0.3
		mm.set_instance_transform(i, xf)
	var mmi := MultiMeshInstance3D.new()
	mmi.name = "bollards"
	mmi.multimesh = mm
	mmi.visibility_range_end = 900.0
	mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mmi)


# ------------------------------------------------------------------ materials

const NOISE := """
float h2(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
float vn(vec2 p) {
    vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(h2(i), h2(i + vec2(1, 0)), f.x), mix(h2(i + vec2(0, 1)), h2(i + vec2(1, 1)), f.x), f.y);
}
"""


## Coursed limestone, world-space so runs join: 0.5 m courses of 0.9-1.5 m
## blocks, staggered; a pale dressed cope; dark and weedy at the waterline.
func _stone_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode cull_back;
#include "res://shaders/thermal.gdshaderinc"
varying vec3 w;
""" + NOISE + """
void vertex() { w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
    vec3 c;
    if (UV.y < -10.0) {
        c = vec3(0.46, 0.44, 0.40) * (0.85 + 0.25 * vn(w.xz * 1.7));
        float joint = 1.0 - smoothstep(0.0, fwidth(UV.x) + 0.01, abs(fract(UV.x / 1.6) - 0.5) * 1.6 - 0.77);
        c *= 1.0 - 0.3 * joint;
    } else {
        float along = UV.x;
        float y = UV.y;
        float course = floor(y / 0.5);
        float len = mix(0.9, 1.5, h2(vec2(course, 3.0)));
        float x = along / len + 0.5 * mod(course, 2.0);
        float block = floor(x);
        vec2 f = vec2(fract(x) * len, fract(y / 0.5) * 0.5);
        float px = fwidth(along) + 0.005;
        float mortar = 1.0 - smoothstep(0.0, px + 0.02, min(min(f.x, len - f.x), min(f.y, 0.5 - f.y)) - 0.012);
        c = vec3(0.43, 0.41, 0.37) * (0.8 + 0.3 * h2(vec2(block, course)));
        c *= 0.85 + 0.3 * vn(vec2(along, y) * 2.3);
        c *= 1.0 - 0.4 * mortar * (1.0 - smoothstep(0.05, 0.3, px));
        c *= 1.0 - 0.15 * smoothstep(0.55, 0.9, vn(vec2(along * 1.5, 0.0))) * smoothstep(-0.5, 1.0, y);
        float wet = 1.0 - smoothstep(0.25, 0.6 + 0.2 * vn(vec2(along * 0.7, 1.0)), y);
        c = mix(c, c * vec3(0.45, 0.48, 0.40), wet);
        c = mix(c, vec3(0.05, 0.07, 0.045), (1.0 - smoothstep(-0.2, 0.25, y)) * 0.75);
    }
    ALBEDO = c;
    ROUGHNESS = 0.9;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.55, 18.0, 0.85, 0.0);
        th = th_air(th, length(CAMERA_POSITION_WORLD - w));
        TH_OUT(th);
    }
}
"""
	var m := ShaderMaterial.new()
	m.shader = sh
	return m


## Boulders: Voronoi cells ~1.3 m, each its own grey limestone tone and tilt,
## dark gaps between them, wet and green-black below the waterline.
func _rock_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode cull_disabled;
#include "res://shaders/thermal.gdshaderinc"
varying vec3 w;
""" + NOISE + """
vec2 hash2(vec2 p) { return fract(sin(vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)))) * 43758.5453); }
void vertex() { w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
    vec2 p = vec2(w.x, w.z) / 1.3;
    vec2 g = floor(p);
    float d1 = 9.0; float d2 = 9.0; vec2 id = vec2(0.0); vec2 off = vec2(0.0);
    for (int j = -1; j <= 1; j++) {
        for (int i = -1; i <= 1; i++) {
            vec2 o = vec2(float(i), float(j));
            vec2 r = o + hash2(g + o) - fract(p);
            float d = dot(r, r);
            if (d < d1) { d2 = d1; d1 = d; id = g + o; off = r; }
            else if (d < d2) { d2 = d; }
        }
    }
    float edge = sqrt(d2) - sqrt(d1);
    float px = fwidth(p.x) + 0.002;
    float gap = 1.0 - smoothstep(0.0, 0.08 + px, edge);
    vec3 c = mix(vec3(0.46, 0.44, 0.40), vec3(0.62, 0.60, 0.55), hash2(id).x) * (0.8 + 0.3 * vn(w.xz * 2.1));
    // Each boulder domed: the side toward the sun lighter.
    vec3 n = normalize(vec3(-off.x, 1.6, off.y));
    NORMAL = normalize((VIEW_MATRIX * vec4(n, 0.0)).xyz);
    c *= 1.0 - 0.75 * gap * (1.0 - smoothstep(0.05, 0.4, px));
    float wet = 1.0 - smoothstep(0.1, 0.6, w.y);
    c = mix(c, c * vec3(0.42, 0.45, 0.38), wet);
    c = mix(c, vec3(0.05, 0.065, 0.045), (1.0 - smoothstep(-0.4, 0.1, w.y)) * 0.7);
    ALBEDO = c;
    ROUGHNESS = mix(0.9, 0.5, wet);
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.5, 20.0, 0.9, 0.0);
        th = th_air(th, length(CAMERA_POSITION_WORLD - w));
        TH_OUT(th);
    }
}
"""
	var m := ShaderMaterial.new()
	m.shader = sh
	return m


## Concrete decks with board-marked joints every 2 m; the edge faces darker
## and weedy at the water.
func _deck_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode cull_back;
#include "res://shaders/thermal.gdshaderinc"
varying vec3 w;
varying vec3 n_world;
""" + NOISE + """
void vertex() {
    w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
    n_world = (MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz;
}
void fragment() {
    vec3 c;
    if (n_world.y > 0.5) {
        c = vec3(0.36, 0.35, 0.33) * (0.85 + 0.25 * vn(w.xz / 3.0)) * (0.9 + 0.2 * vn(w.xz * 3.0));
        vec2 j = abs(fract(w.xz / 2.0) - 0.5) * 2.0;
        float fw = fwidth(w.x) + 0.004;
        c *= 1.0 - 0.25 * (1.0 - smoothstep(0.0, fw * 1.5, 1.0 - max(j.x, j.y) - 0.01));
    } else {
        c = vec3(0.30, 0.29, 0.27) * (0.85 + 0.25 * vn(vec2(w.x + w.z, w.y) * 2.0));
        c = mix(c, vec3(0.05, 0.07, 0.045), (1.0 - smoothstep(-0.2, 0.4, w.y)) * 0.75);
    }
    ALBEDO = c;
    ROUGHNESS = 0.9;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.6, 20.0, 0.8, 0.0);
        th = th_air(th, length(CAMERA_POSITION_WORLD - w));
        TH_OUT(th);
    }
}
"""
	var m := ShaderMaterial.new()
	m.shader = sh
	return m


## Promenade railing: dark painted steel, a top and a mid rail, posts every
## 2 m and balusters every 0.15 m, cut from one quad strip (as
## manhattan_coast's); balusters go once they are under a pixel.
func _rail_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode cull_disabled;
#include "res://shaders/thermal.gdshaderinc"
varying vec3 w;
void vertex() { w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
    float x = UV.x;
    float y = UV.y;
    float fw = fwidth(x);
    float rw = max(0.035, fw * 0.8);
    bool rail = abs(y - 0.97) < rw || abs(y - 0.1) < max(0.025, fw * 0.6);
    bool post = abs(fract(x / 2.0) - 0.5) * 2.0 > 1.0 - max(0.04, fw * 0.8);
    bool bal = fw < 0.03 && abs(fract(x / 0.15) - 0.5) * 0.15 > 0.075 - 0.01 && y > 0.1 && y < 0.97;
    if (!(rail || post || bal)) { discard; }
    ALBEDO = vec3(0.05, 0.07, 0.065);
    METALLIC = 0.5;
    ROUGHNESS = 0.45;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.6, 16.0, 0.15, 0.0);
        th = th_air(th, length(CAMERA_POSITION_WORLD - w));
        TH_OUT(th);
    }
}
"""
	var m := ShaderMaterial.new()
	m.shader = sh
	return m

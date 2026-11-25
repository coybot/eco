## The edge between the land and the rivers: Manhattan's bulkheads.
##
## The ground is cut at the shoreline field's 0 isoline (manhattan_shore.gd)
## and the rivers lie SEA_Y = 1.6 m below it, so without this the city would
## end in a paper-thin edge over a gap. coast.json (tools/bake_manhattan_shore.py)
## is that same isoline as polylines with the water on their right; along
## each one this builds:
##   a stone face   from 1 m under the water up to a granite cope 12 cm proud
##                  of the street, coursed blocks, dark and weedy in the tidal
##                  band (NY harbour's ~1.4 m range)
##   a cope         1 m wide on top, hiding where the ground's cut lies
##   railings       black steel pickets along Manhattan's own esplanades
##                  (Hudson River Park, Battery Park City, the East River
##                  Greenway): everywhere on its own shore except where a
##                  building stands on the edge or a pier meets it
## Ported from a harbour scene's coastline.gd (its quay/seawall/natural
## kinds collapse to one here: Manhattan's edge is bulkhead nearly all round).
##
## Only within the shoreline field's fine box (Manhattan and the facing
## shores): farther out the cut is seen from kilometres away, and the far
## mask's 400 km of creeks and marsh would cost draws for nothing.
## Not solid: it is at street level and below; an aircraft that low over
## the river has already struck the water.
extends Node3D

const FOOT_BELOW := 1.0      # face foot this far under SEA_Y
const COPE_TOP := 0.12
const COPE_IN := 1.0
const FACE_OUT := 0.12       # the face stands this far out over the water
const RAIL_IN := 0.45
const RAIL_H := 1.07
const CHUNK := 1000.0

var env: Node3D
var _shore: RefCounted
var lengths := {"wall": 0.0, "railing": 0.0}


func _ready() -> void:
	name = "coastline"
	env = get_parent().get_parent() as Node3D
	_shore = env.call("shore")
	var path := "res://assets/manhattan/coast.json"
	if not FileAccess.file_exists(path):
		return
	var t0 := Time.get_ticks_msec()
	var doc: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(path))
	var off: Vector2 = env.call("data_offset")
	var box: Rect2 = _shore.get("fine_rect")
	box.position -= off
	var sea_y := float(env.get("SEA_Y"))
	var walls := {}
	var rails := {}
	for line: Dictionary in doc.get("lines", []):
		var pts := PackedVector2Array()
		for q: Array in line["pts"]:
			pts.append(Vector2(float(q[0]), float(q[1])) - off)
		if pts.size() < 2 or not box.has_point(pts[0]):
			continue
		_wall(pts, sea_y, walls)
		if bool(line.get("own", false)):
			_railing(pts, rails)
	var wm := _wall_material()
	var rm := _rail_material()
	for k in walls:
		_emit("wall_%d_%d" % [k.x, k.y], walls[k], wm, 5000.0)
	for k in rails:
		_emit("railing_%d_%d" % [k.x, k.y], rails[k], rm, 700.0)
	print("[manhattan_coast] %.1f km of bulkhead, %.1f km of railing in %d+%d draws, %d ms" %
		[lengths["wall"] / 1000.0, lengths["railing"] / 1000.0, walls.size(), rails.size(),
		 Time.get_ticks_msec() - t0])


func _emit(nm: String, st: SurfaceTool, mat: Material, far: float) -> void:
	var mi := MeshInstance3D.new()
	mi.name = nm
	st.index()
	mi.mesh = st.commit()
	mi.material_override = mat
	mi.visibility_range_end = far
	mi.visibility_range_end_margin = far * 0.1
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mi)


func _st(acc: Dictionary, p: Vector2) -> SurfaceTool:
	var k := Vector2i(int(floor(p.x / CHUNK)), int(floor(p.y / CHUNK)))
	if not acc.has(k):
		var st := SurfaceTool.new()
		st.begin(Mesh.PRIMITIVE_TRIANGLES)
		acc[k] = st
	return acc[k]


## Seaward unit normals at each vertex (water on the right of the run),
## averaged across the corner so the strip has no gaps or overlaps.
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
		var nrm := acc.normalized()
		# Miter: stretch so the offset line stays parallel, but cap it at
		# sharp corners where it would shoot off.
		var c := acc.length() * 0.5
		out[i] = nrm / maxf(c, 0.5)
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


func _wall(pts: PackedVector2Array, sea_y: float, acc: Dictionary) -> void:
	var s := _normals(pts)
	var foot := sea_y - FOOT_BELOW
	var along := 0.0
	for i in range(1, pts.size()):
		var a := pts[i - 1]
		var b := pts[i]
		var d := a.distance_to(b)
		var st := _st(acc, (a + b) * 0.5)
		var fa := a + s[i - 1] * FACE_OUT
		var fb := b + s[i] * FACE_OUT
		var ia := a - s[i - 1] * COPE_IN
		var ib := b - s[i] * COPE_IN
		# Face, seen from the river (water on the run's right, so the run
		# goes left to right as seen from the water).
		_quad(st, _v(fa, foot), _v(fb, foot), _v(fb, COPE_TOP), _v(fa, COPE_TOP),
			Vector2(along, foot), Vector2(along + d, foot), Vector2(along + d, COPE_TOP), Vector2(along, COPE_TOP))
		# Cope top (UV.y < -10 tells the shader it is a top).
		_quad(st, _v(fa, COPE_TOP), _v(fb, COPE_TOP), _v(ib, COPE_TOP), _v(ia, COPE_TOP),
			Vector2(along, -20.0), Vector2(along + d, -20.0), Vector2(along + d, -21.0), Vector2(along, -21.0))
		# The cope's landward step down to the street.
		_quad(st, _v(ia, COPE_TOP), _v(ib, COPE_TOP), _v(ib, -0.05), _v(ia, -0.05),
			Vector2(along, COPE_TOP), Vector2(along + d, COPE_TOP), Vector2(along + d, -0.05), Vector2(along, -0.05))
		along += d
	lengths["wall"] += along


func _railing(pts: PackedVector2Array, acc: Dictionary) -> void:
	var s := _normals(pts)
	var piers_ok := _shore != null
	const STEP := 3.0
	var along := 0.0
	for i in range(1, pts.size()):
		var a := pts[i - 1]
		var b := pts[i]
		var d := a.distance_to(b)
		var n := maxi(1, int(ceil(d / STEP)))
		for k in n:
			var t0 := float(k) / n
			var t1 := float(k + 1) / n
			var p0 := a.lerp(b, t0) - s[i - 1].lerp(s[i], t0) * RAIL_IN
			var p1 := a.lerp(b, t1) - s[i - 1].lerp(s[i], t1) * RAIL_IN
			var mid := (p0 + p1) * 0.5
			var seg := d / n
			var sea := s[i - 1].lerp(s[i], (t0 + t1) * 0.5)
			if _building_near(mid, 2.5) or (piers_ok and int(_shore.call("pier_at", mid.x + sea.x * 2.0, mid.y + sea.y * 2.0)) >= 0):
				along += seg
				continue
			var st := _st(acc, mid)
			_quad(st, _v(p0, COPE_TOP), _v(p1, COPE_TOP), _v(p1, COPE_TOP + RAIL_H), _v(p0, COPE_TOP + RAIL_H),
				Vector2(along, 0.0), Vector2(along + seg, 0.0), Vector2(along + seg, RAIL_H), Vector2(along, RAIL_H))
			along += seg
			lengths["railing"] += seg


func _building_near(p: Vector2, r: float) -> bool:
	var tile := float(env.get("TILE"))
	var index: Dictionary = env.get("_index")
	var rects: Array = env.get("_rects")
	var key := Vector2i(int(floor(p.x / tile)), int(floor(p.y / tile)))
	for dx in [-1, 0, 1]:
		for dy in [-1, 0, 1]:
			var k: Vector2i = key + Vector2i(dx, dy)
			if not index.has(k):
				continue
			for i: int in index[k]:
				if (rects[i] as Rect2).grow(r).has_point(p):
					return true
	return false


# ------------------------------------------------------------------ materials
## Coursed granite (the old bulkheads) in world space so runs join without
## seams: 0.6 m courses of 1.2-1.9 m blocks, staggered; the tidal band dark
## and green-black. The cope a paler dressed granite.
func _wall_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode cull_back;
#include "res://shaders/thermal.gdshaderinc"
uniform float sea_y = -1.6;
varying vec3 w;
float h2(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
float vn(vec2 p) {
    vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(h2(i), h2(i + vec2(1, 0)), f.x), mix(h2(i + vec2(0, 1)), h2(i + vec2(1, 1)), f.x), f.y);
}
void vertex() { w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
    vec3 c;
    if (UV.y < -10.0) {
        vec2 q = w.xz;
        c = vec3(0.37, 0.36, 0.34) * (0.85 + 0.25 * vn(q * 1.7));
        float joint = 1.0 - smoothstep(0.0, fwidth(UV.x) + 0.01, abs(fract(UV.x / 1.8) - 0.5) * 1.8 - 0.88);
        c *= 1.0 - 0.3 * joint;
    } else {
        float along = UV.x;
        float y = UV.y;
        float course = floor(y / 0.6);
        float len = mix(1.2, 1.9, h2(vec2(course, 3.0)));
        float x = along / len + 0.5 * mod(course, 2.0);
        float block = floor(x);
        vec2 f = vec2(fract(x) * len, fract(y / 0.6) * 0.6);
        float px = fwidth(along) + 0.005;
        float mortar = 1.0 - smoothstep(0.0, px + 0.02, min(min(f.x, len - f.x), min(f.y, 0.6 - f.y)) - 0.015);
        c = vec3(0.40, 0.39, 0.37) * (0.78 + 0.35 * h2(vec2(block, course)));
        c *= 0.85 + 0.3 * vn(vec2(along, y) * 2.3);
        c *= 1.0 - 0.45 * mortar * (1.0 - smoothstep(0.05, 0.3, px));
        // Streaks of run-off down from the cope.
        c *= 1.0 - 0.18 * smoothstep(0.55, 0.9, vn(vec2(along * 1.5, 0.0))) * smoothstep(-1.4, 0.1, y);
        // Tidal band: wet to ~0.7 m above mean water, weed below it.
        float wet = 1.0 - smoothstep(sea_y + 0.4, sea_y + 0.9 + 0.2 * vn(vec2(along * 0.7, 1.0)), y);
        c = mix(c, c * vec3(0.5, 0.52, 0.45), wet);
        c = mix(c, vec3(0.05, 0.07, 0.045), (1.0 - smoothstep(sea_y - 0.1, sea_y + 0.45, y)) * 0.75);
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
	m.set_shader_parameter("sea_y", float(env.get("SEA_Y")))
	return m


## Black-painted steel: a top rail, a bottom rail, posts every 2.4 m and
## pickets every 12.5 cm, cut out in the shader from one quad strip. Pickets
## go once they are under a pixel (they would only shimmer); the rails stay.
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
    bool rail = abs(y - 1.03) < rw || abs(y - 0.12) < max(0.025, fw * 0.6);
    bool post = abs(fract(x / 2.4) - 0.5) * 2.4 > 1.2 - max(0.04, fw * 0.8);
    bool picket = fw < 0.03 && abs(fract(x / 0.125) - 0.5) * 0.125 > 0.0625 - 0.009 && y > 0.12 && y < 1.03;
    if (!(rail || post || picket)) { discard; }
    ALBEDO = vec3(0.035, 0.04, 0.038);
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

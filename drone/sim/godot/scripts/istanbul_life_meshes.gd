## Meshes for Istanbul's moving world (istanbul_life.gd): the vehicles the
## Manhattan kit (life_meshes.gd) does not have, its trams, its gulls and the
## anglers on the Galata Bridge. Built from boxes with life_meshes' helpers,
## so they draw with the same vehicle and person shaders: one surface, the
## material class in vertex alpha, the paint per instance.
##
## Liveries are from photographs, approximate: the T1's low-floor trams
## (Bombardier Flexity Swift A32 and Alstom Citadis X04, run in coupled
## pairs) are white with a dark glazing band and a red skirt line; the
## nostalgic trams on Istiklal (T2) and in Moda (T3) are red under cream;
## IETT's city buses white over a blue-green band; the dolmus minibuses and
## the taxis Istanbul's yellow.
extends RefCounted

const LM = preload("res://scripts/life_meshes.gd")

## Turkish taxi / dolmus yellow (under the scene's tonemap).
const TAXI_YELLOW := Color(0.98, 0.78, 0.05)
const IETT_WHITE := Color(0.93, 0.94, 0.94)
## Paints on Turkish streets: white far ahead, then greys, black, a few colours.
const PAINTS := [Color(0.92, 0.92, 0.91), Color(0.9, 0.9, 0.89), Color(0.88, 0.89, 0.89),
	Color(0.86, 0.87, 0.87), Color(0.5, 0.51, 0.53), Color(0.62, 0.63, 0.64), Color(0.35, 0.36, 0.38),
	Color(0.06, 0.06, 0.065), Color(0.09, 0.09, 0.1), Color(0.55, 0.08, 0.07), Color(0.12, 0.2, 0.42),
	Color(0.42, 0.38, 0.33), Color(0.72, 0.72, 0.7), Color(0.18, 0.3, 0.42)]
const TRAM_WHITE := Color(0.94, 0.94, 0.93)
const HERITAGE_RED := Color(0.62, 0.07, 0.06)

## Tram dimensions (m): module length, width, roof.
const MODERN_L := 9.6
const MODERN_W := 2.65
const HERITAGE_L := 11.0
const HERITAGE_W := 2.3

static var _cache: Dictionary = {}
static var _gull_material: ShaderMaterial = null


## {"lod0", "lod1", "lod2", "dims"} as LM.kind(): Istanbul's own kinds, else
## the Manhattan kit's.
static func kind(name: String) -> Dictionary:
	if _cache.has(name):
		return _cache[name]
	var out := {}
	match name:
		"dolmus":
			out = _dolmus()
		"iett":
			out = _iett()
		_:
			return LM.kind(name)
	_cache[name] = out
	return out


static func _commit(st: SurfaceTool, dims: Vector3) -> Dictionary:
	LM._blob(st, dims.x, dims.z, Vector3(2.0 / dims.x, 1.0 / dims.y, 0.0))
	var m := st.commit()
	return {"lod0": m, "lod1": m, "lod2": m, "dims": dims}


## A dolmus: a high-roof minibus, glazed all along, the roof sign lit at night.
static func _dolmus() -> Dictionary:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var white := Color(1, 1, 1)
	var L := 7.0
	var H := 2.7
	var W := 2.05
	var ax := Vector3(2.0 / L, 1.0 / H, 0.0)
	_b(st, Vector3(0, 0.82, L * 0.5 - 0.55), Vector3(W * 0.96, 0.85, 1.1), white, LM.CLS_PAINT, ax)
	_b(st, Vector3(0, 0.95, -0.55), Vector3(W, 1.1, L - 1.1), white, LM.CLS_PAINT, ax)
	_b(st, Vector3(0, 1.85, -0.55), Vector3(W * 1.004, 0.7, L - 1.3), white, LM.CLS_GLASS, ax)
	_b(st, Vector3(0, 2.42, -0.55), Vector3(W, 0.46, L - 1.1), white, LM.CLS_PAINT, ax)
	_b(st, Vector3(0, 1.62, L * 0.5 - 1.02), Vector3(W * 0.9, 0.75, 0.12), white, LM.CLS_GLASS, ax)
	_b(st, Vector3(0, 1.32, 0.0), Vector3(W * 1.008, 0.1, L * 0.9), Color(0.08, 0.08, 0.08), LM.CLS_OTHER, ax)
	_b(st, Vector3(0, H + 0.12, L * 0.5 - 1.6), Vector3(0.9, 0.24, 0.3), Color(0.98, 0.93, 0.7), LM.CLS_SIGN, ax)
	LM._wheels(st, W, L, 0.36, [-0.33, 0.33], ax)
	LM._lamps(st, W, L, 0.8, 1.0, ax)
	return _commit(st, Vector3(L, H, W))


## An IETT city bus: white over a blue-green band, dark glazing, roof pods,
## the amber destination sign in front. Engine at the back.
static func _iett() -> Dictionary:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var white := Color(1, 1, 1)
	var L := 12.0
	var H := 3.15
	var W := 2.55
	var ax := Vector3(-2.0 / L, 1.0 / H, 0.0)
	_b(st, Vector3(0, 0.3 + 0.5, 0), Vector3(W, 1.0, L), white, LM.CLS_PAINT, ax)
	_b(st, Vector3(0, 0.62, 0), Vector3(W * 1.006, 0.5, L * 0.995), Color(0.05, 0.42, 0.5), LM.CLS_OTHER, ax)
	_b(st, Vector3(0, 1.3 + 0.6, 0), Vector3(W * 1.004, 1.2, L * 0.995), white, LM.CLS_GLASS, ax)
	_b(st, Vector3(0, 2.5 + 0.32, 0), Vector3(W, 0.65, L), white, LM.CLS_PAINT, ax)
	_b(st, Vector3(0, H + 0.17, -L * 0.28), Vector3(W * 0.7, 0.34, 3.0), Color(0.78, 0.8, 0.8), LM.CLS_OTHER, ax)
	_b(st, Vector3(0, H + 0.12, L * 0.18), Vector3(W * 0.55, 0.24, 2.2), Color(0.7, 0.72, 0.72), LM.CLS_OTHER, ax)
	_b(st, Vector3(0, 1.8, L * 0.5 + 0.01), Vector3(W * 0.92, 1.55, 0.04), white, LM.CLS_GLASS, ax)
	_b(st, Vector3(0, 2.83, L * 0.5 + 0.02), Vector3(W * 0.74, 0.26, 0.04), Color(1.0, 0.62, 0.12), LM.CLS_SIGN, ax)
	LM._wheels(st, W, L, 0.5, [-0.3, 0.28], ax)
	LM._lamps(st, W, L, 0.72, 0.9, ax)
	return _commit(st, Vector3(L, H, W))


static func _b(st: SurfaceTool, c: Vector3, s: Vector3, col: Color, cls: float, ax: Vector3) -> void:
	LM._box(st, c, s, col, cls, ax)


# ------------------------------------------------------------------ trams

## A module of a modern low-floor tram, +Z forward, rails at y 0, centred.
## part: "cab" (driving end at +Z, headlamps), "cab_rear" (the same end with
## tail lamps: a cab turned round at the back of the train), "mid" (with the
## pantograph). The body is CLS_PAINT (the instance's paint: white).
static func tram_modern(part: String) -> ArrayMesh:
	var key := "tram_" + part
	if _cache.has(key):
		return _cache[key]
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var white := Color(1, 1, 1)
	var dark := Color(0.07, 0.07, 0.08)
	var red := Color(0.78, 0.08, 0.06)
	var L := MODERN_L
	var W := MODERN_W
	var ax := Vector3(2.0 / L, 1.0 / 3.4, 0.0)
	var cab := part.begins_with("cab")
	var body_l := L - (0.9 if cab else 0.0)
	var bz := -0.45 if cab else 0.0
	_b(st, Vector3(0, 0.25, 0), Vector3(W * 0.9, 0.3, L * 0.86), dark, LM.CLS_OTHER, ax)
	_b(st, Vector3(0, 0.75, bz), Vector3(W, 0.8, body_l), white, LM.CLS_PAINT, ax)
	_b(st, Vector3(0, 0.5, bz), Vector3(W * 1.008, 0.16, body_l * 0.995), red, LM.CLS_OTHER, ax)
	_b(st, Vector3(0, 1.85, bz), Vector3(W * 1.004, 1.4, body_l * 0.97), white, LM.CLS_GLASS, ax)
	_b(st, Vector3(0, 2.95, bz), Vector3(W, 0.8, body_l), white, LM.CLS_PAINT, ax)
	# The saloon lights seen through the top of the glazing: lit at night.
	_b(st, Vector3(0, 2.47, bz), Vector3(W * 1.008, 0.1, body_l * 0.95), Color(0.95, 0.9, 0.75), LM.CLS_SIGN, ax)
	# Door leaves: darker panels breaking the glazing, two a side.
	for zf in [-0.25, 0.22]:
		_b(st, Vector3(0, 1.5, bz + zf * body_l), Vector3(W * 1.01, 2.1, 1.3), Color(0.75, 0.76, 0.77), LM.CLS_OTHER, ax)
	# Roof equipment.
	_b(st, Vector3(0, 3.5, bz), Vector3(W * 0.62, 0.3, body_l * 0.55), Color(0.62, 0.63, 0.64), LM.CLS_OTHER, ax)
	# Bellows at the rear end (the joint to the next module).
	if part != "cab_rear":
		_b(st, Vector3(0, 1.75, -L * 0.5 + 0.05), Vector3(W * 0.86, 2.9, 0.5), dark, LM.CLS_OTHER, ax)
	if cab:
		# The nose: a sloped windscreen block ahead of the body.
		var nz := L * 0.5 - 0.45
		_b(st, Vector3(0, 0.8, nz), Vector3(W * 0.96, 0.9, 0.9), white, LM.CLS_PAINT, ax)
		_b(st, Vector3(0, 0.48, nz + 0.02), Vector3(W * 0.98, 0.16, 0.9), red, LM.CLS_OTHER, ax)
		_b(st, Vector3(0, 1.95, nz - 0.1), Vector3(W * 0.94, 1.4, 0.7), white, LM.CLS_GLASS, ax)
		_b(st, Vector3(0, 2.95, nz - 0.2), Vector3(W * 0.96, 0.8, 0.5), white, LM.CLS_PAINT, ax)
		_b(st, Vector3(0, 2.92, nz + 0.07), Vector3(W * 0.7, 0.28, 0.06), Color(1.0, 0.62, 0.12), LM.CLS_SIGN, ax)
		var lamp := LM.CLS_TAIL if part == "cab_rear" else LM.CLS_HEAD
		for sx in [-1.0, 1.0]:
			_b(st, Vector3(sx * W * 0.36, 0.95, L * 0.5 + 0.02), Vector3(0.32, 0.14, 0.06), white, lamp, ax)
	else:
		# Pantograph: a frame on the roof and its collector bar.
		_b(st, Vector3(0, 3.72, 0.0), Vector3(1.2, 0.12, 1.6), Color(0.3, 0.3, 0.32), LM.CLS_OTHER, ax)
		_b(st, Vector3(0, 4.05, 0.25), Vector3(0.08, 0.7, 0.08), Color(0.25, 0.25, 0.27), LM.CLS_OTHER, ax)
		_b(st, Vector3(0, 4.4, 0.0), Vector3(1.7, 0.06, 0.12), Color(0.2, 0.2, 0.2), LM.CLS_OTHER, ax)
	LM._blob(st, L, W, ax)
	var m := st.commit()
	_cache[key] = m
	return m


## A nostalgic tram car (T2 Istiklal, T3 Moda): red lower body (CLS_PAINT),
## cream window posts and cornice, a clerestory roof and open platforms at
## both ends. Headlamp at +Z, tail at -Z.
static func tram_heritage() -> ArrayMesh:
	if _cache.has("tram_heritage"):
		return _cache["tram_heritage"]
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var white := Color(1, 1, 1)
	var cream := Color(0.93, 0.86, 0.68)
	var dark := Color(0.08, 0.07, 0.07)
	var L := HERITAGE_L
	var W := HERITAGE_W
	var ax := Vector3(2.0 / L, 1.0 / 3.4, 0.0)
	_b(st, Vector3(0, 0.38, 0), Vector3(W * 0.8, 0.55, L * 0.6), dark, LM.CLS_OTHER, ax)
	_b(st, Vector3(0, 1.1, 0), Vector3(W, 0.95, L - 2.4), white, LM.CLS_PAINT, ax)
	_b(st, Vector3(0, 2.05, 0), Vector3(W * 1.002, 0.95, L - 2.5), white, LM.CLS_GLASS, ax)
	for k in 7:
		var z := (float(k) / 6.0 - 0.5) * (L - 2.6)
		_b(st, Vector3(0, 2.05, z), Vector3(W * 1.01, 0.95, 0.14), cream, LM.CLS_OTHER, ax)
	_b(st, Vector3(0, 2.68, 0), Vector3(W * 1.01, 0.32, L - 2.4), cream, LM.CLS_OTHER, ax)
	_b(st, Vector3(0, 2.47, 0), Vector3(W * 1.012, 0.08, L - 2.6), Color(0.98, 0.85, 0.6), LM.CLS_SIGN, ax)
	_b(st, Vector3(0, 2.95, 0), Vector3(W * 0.98, 0.22, L - 0.3), Color(0.32, 0.3, 0.29), LM.CLS_OTHER, ax)
	_b(st, Vector3(0, 3.15, 0), Vector3(W * 0.5, 0.2, L - 3.0), cream, LM.CLS_OTHER, ax)
	_b(st, Vector3(0, 3.27, 0), Vector3(W * 0.56, 0.06, L - 2.9), Color(0.32, 0.3, 0.29), LM.CLS_OTHER, ax)
	for sz in [-1.0, 1.0]:
		# Platform: red dash, glazed vestibule, the number board on top.
		var z: float = sz * (L * 0.5 - 0.6)
		_b(st, Vector3(0, 1.05, z), Vector3(W * 0.92, 0.85, 1.2), white, LM.CLS_PAINT, ax)
		_b(st, Vector3(0, 2.05, z), Vector3(W * 0.9, 0.95, 1.1), white, LM.CLS_GLASS, ax)
		_b(st, Vector3(0, 2.68, z), Vector3(W * 0.92, 0.32, 1.2), cream, LM.CLS_OTHER, ax)
		_b(st, Vector3(0, 0.95, sz * (L * 0.5 + 0.01)), Vector3(0.3, 0.2, 0.04), white,
			LM.CLS_HEAD if sz > 0.0 else LM.CLS_TAIL, ax)
		_b(st, Vector3(0, 2.68, sz * (L * 0.5 + 0.01)), Vector3(0.8, 0.22, 0.04), Color(0.98, 0.93, 0.7), LM.CLS_SIGN, ax)
	# Trolley pantograph.
	_b(st, Vector3(0, 3.45, 0), Vector3(0.9, 0.1, 1.2), Color(0.25, 0.25, 0.26), LM.CLS_OTHER, ax)
	_b(st, Vector3(0, 3.95, 0), Vector3(1.2, 0.06, 0.1), Color(0.2, 0.2, 0.2), LM.CLS_OTHER, ax)
	LM._blob(st, L, W, ax)
	var m := st.commit()
	_cache["tram_heritage"] = m
	return m


# ------------------------------------------------------------------ anglers

## A man fishing off the bridge rail: life_meshes' walker standing, a rod held
## out over the water (+Z) and a bucket at his feet. Drawn with the person
## shader (vertex alpha 1: fixed colours; the shirt from the instance).
static func angler_mesh() -> ArrayMesh:
	if _cache.has("angler"):
		return _cache["angler"]
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var src := LM.person_mesh()
	var arr := src.surface_get_arrays(0)
	var pv: PackedVector3Array = arr[Mesh.ARRAY_VERTEX]
	var pn: PackedVector3Array = arr[Mesh.ARRAY_NORMAL]
	var pc: PackedColorArray = arr[Mesh.ARRAY_COLOR]
	var idx = arr[Mesh.ARRAY_INDEX]
	var order: PackedInt32Array = idx if idx != null else PackedInt32Array(range(pv.size()))
	for k in order:
		st.set_normal(pn[k])
		st.set_color(pc[k])
		st.set_uv(Vector2.ZERO)               # no walk swing
		st.add_vertex(pv[k])
	var rod := Color(0.1, 0.1, 0.1, 1.0)
	_rod(st, Vector3(0.12, 1.1, 0.25), Vector3(0.05, 3.1, 3.3), 0.035, rod)
	_rod(st, Vector3(0.05, 3.1, 3.3), Vector3(0.05, 0.0, 3.9), 0.008, Color(0.75, 0.75, 0.75, 1.0))
	_pb(st, Vector3(-0.35, 0.15, 0.15), Vector3(0.28, 0.3, 0.28), Color(0.2, 0.35, 0.6, 1.0))
	var m := st.commit()
	_cache["angler"] = m
	return m


## A thin square rod from a to b.
static func _rod(st: SurfaceTool, a: Vector3, b: Vector3, r: float, col: Color) -> void:
	var ax := (b - a).normalized()
	var u := ax.cross(Vector3.RIGHT if absf(ax.x) < 0.9 else Vector3.UP).normalized() * r
	var v := ax.cross(u).normalized() * r
	var ring := [u + v, u - v, -u - v, -u + v]
	for k in 4:
		var p0: Vector3 = ring[k]
		var p1: Vector3 = ring[(k + 1) % 4]
		var n := (p0 + p1).normalized()
		for p: Vector3 in [a + p0, b + p0, b + p1, a + p0, b + p1, a + p1]:
			st.set_normal(n)
			st.set_color(col)
			st.set_uv(Vector2.ZERO)
			st.add_vertex(p)


static func _pb(st: SurfaceTool, c: Vector3, s: Vector3, col: Color) -> void:
	var h := s * 0.5
	for axis in 3:
		for sgn in [-1.0, 1.0]:
			var nrm := Vector3.ZERO
			nrm[axis] = sgn
			var u := Vector3.ZERO
			u[(axis + 1) % 3] = h[(axis + 1) % 3]
			var v := Vector3.ZERO
			v[(axis + 2) % 3] = h[(axis + 2) % 3]
			var o := c + nrm * h[axis]
			var q := [o - u - v, o + u - v, o + u + v, o - u + v]
			for t: Array in [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]:
				var a: Vector3 = t[0]
				var b: Vector3 = t[1]
				var cc: Vector3 = t[2]
				if (b - a).cross(cc - a).dot(nrm) > 0.0:
					var tmp := b
					b = cc
					cc = tmp
				for p: Vector3 in [a, b, cc]:
					st.set_normal(nrm)
					st.set_color(col)
					st.set_uv(Vector2.ZERO)
					st.add_vertex(p)


# ------------------------------------------------------------------ gulls

## A gull, +Z forward, wings along X (span 1.4 m). UV.x is the signed span
## fraction (0 on the body) for the flap in the shader. Vertex colour: white
## body, grey mantle, black tips, yellow bill.
static func gull_mesh() -> ArrayMesh:
	if _cache.has("gull"):
		return _cache["gull"]
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var white := Color(0.95, 0.95, 0.94)
	var grey := Color(0.62, 0.65, 0.68)
	var black := Color(0.08, 0.08, 0.09)
	var bill := Color(0.95, 0.75, 0.15)
	_pb_tag(st, Vector3(0, 0, 0.02), Vector3(0.13, 0.12, 0.5), white)
	_pb_tag(st, Vector3(0, 0.03, 0.31), Vector3(0.09, 0.09, 0.12), white)
	_pb_tag(st, Vector3(0, 0.02, 0.4), Vector3(0.03, 0.03, 0.07), bill)
	_tri2(st, Vector3(-0.07, 0.0, -0.2), Vector3(0.07, 0.0, -0.2), Vector3(0.0, 0.0, -0.36), white, 0.0)
	for sx in [-1.0, 1.0]:
		var r0 := Vector3(sx * 0.06, 0.03, 0.12)
		var r1 := Vector3(sx * 0.06, 0.03, -0.12)
		var m0 := Vector3(sx * 0.42, 0.03, 0.1)
		var m1 := Vector3(sx * 0.42, 0.03, -0.12)
		var t0 := Vector3(sx * 0.7, 0.03, 0.0)
		var t1 := Vector3(sx * 0.68, 0.03, -0.1)
		_quad2(st, r0, m0, m1, r1, grey, [0.0, sx * 0.6, sx * 0.6, 0.0])
		_quad2(st, m0, t0, t1, m1, black, [sx * 0.6, sx * 1.0, sx * 1.0, sx * 0.6])
	var m := st.commit()
	_cache["gull"] = m
	return m


static func _pb_tag(st: SurfaceTool, c: Vector3, s: Vector3, col: Color) -> void:
	var h := s * 0.5
	for axis in 3:
		for sgn in [-1.0, 1.0]:
			var nrm := Vector3.ZERO
			nrm[axis] = sgn
			var u := Vector3.ZERO
			u[(axis + 1) % 3] = h[(axis + 1) % 3]
			var v := Vector3.ZERO
			v[(axis + 2) % 3] = h[(axis + 2) % 3]
			var o := c + nrm * h[axis]
			_quad2(st, o - u - v, o + u - v, o + u + v, o - u + v, col, [0.0, 0.0, 0.0, 0.0])


## Double-sided quad (the gull shader draws both faces; normals up).
static func _quad2(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, d: Vector3, col: Color, tags: Array) -> void:
	var pts := [a, b, c, d]
	for t: Array in [[0, 1, 2], [0, 2, 3]]:
		for k: int in t:
			st.set_normal(Vector3.UP)
			st.set_color(col)
			st.set_uv(Vector2(float(tags[k]), 0.0))
			st.add_vertex(pts[k])


static func _tri2(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, col: Color, tag: float) -> void:
	for p: Vector3 in [a, b, c]:
		st.set_normal(Vector3.UP)
		st.set_color(col)
		st.set_uv(Vector2(tag, 0.0))
		st.add_vertex(p)


static func gull_material() -> ShaderMaterial:
	if _gull_material == null:
		var sh := Shader.new()
		sh.code = GULL_SHADER
		_gull_material = ShaderMaterial.new()
		_gull_material.shader = sh
	return _gull_material


## Each gull circles its instance origin: INSTANCE_CUSTOM x radius (m), y
## angular speed (rad/s, sign = turn), z phase (rad), w seed (0..1). The
## circle breathes and drifts, the bird banks into the turn, glides mostly and
## flaps in bursts. Nothing runs on the CPU per gull.
const GULL_SHADER := """
shader_type spatial;
render_mode cull_disabled, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"

// 0..1 of the flock up: fewer at night.
uniform float flock = 1.0;

varying vec3 v_col;

vec3 to_linear(vec3 c) {
    return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c));
}

void vertex() {
    vec4 cu = INSTANCE_CUSTOM;
    v_col = to_linear(COLOR.rgb);
    float seed = cu.w;
    if (seed > flock) {
        VERTEX = vec3(0.0);
    } else {
        float t = TIME;
        float r = cu.x * (1.0 + 0.25 * sin(t * 0.11 + seed * 20.0));
        float w = cu.y;
        float a = cu.z + w * t;
        vec3 c = vec3(sin(t * 0.021 + seed * 6.0), 0.0, cos(t * 0.017 + seed * 9.0)) * cu.x * 0.7;
        vec3 pos = c + vec3(cos(a) * r, 3.0 * sin(t * 0.23 + seed * 13.0), sin(a) * r);
        vec3 fwd = sign(w) * vec3(-sin(a), 0.0, cos(a));
        // Flap in bursts, glide between with the wings a little up.
        float burst = smoothstep(0.55, 0.7, sin(t * 0.31 + seed * 31.0));
        float flap = mix(0.1, 0.55 * sin(t * 15.0 + seed * 40.0), burst);
        float span = UV.x;
        vec3 v = VERTEX;
        if (span != 0.0) {
            v.y += abs(v.x) * tan(flap);
        }
        // Bank into the turn.
        float bank = -sign(w) * atan(w * w * r / 9.8);
        float cb = cos(bank);
        float sb = sin(bank);
        v = vec3(v.x * cb - v.y * sb, v.x * sb + v.y * cb, v.z);
        float h = atan(fwd.x, fwd.z);
        float ch = cos(h);
        float sh = sin(h);
        VERTEX = vec3(v.x * ch + v.z * sh, v.y, -v.x * sh + v.z * ch) + pos;
        NORMAL = vec3(-sb * ch, cb, sb * sh);
    }
}

void fragment() {
    ALBEDO = v_col;
    ROUGHNESS = 0.8;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        // Feathers insulate: a gull's outside runs a few degrees over the air.
        TH_OUT(th_surface(0.4, 4.0, 0.2, 4.0));
    }
}
"""

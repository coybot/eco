## Paint and steel on Istanbul's streets, from roads.json (OpenStreetMap):
## lane lines, centre lines, zebra crossings and stop lines on the painted
## roads; the rails of the T1 tram (grooved, flush in the street) and of the
## Sirkeci line (on sleepers and ballast); and the nosings of the city's
## street stairs. A child of istanbul_ground.gd, which draws the asphalt,
## setts and track beds these sit on.
##
## Geometry, not texture: paint lines are 10-15 cm wide and rail heads 7 cm,
## far below the 1 m street field. Every quad follows the drawn ground
## (ground.mesh_height, the mesh's own triangles), cut into <= 6 m pieces so
## it hugs the hills, 3.5 cm proud. Culled past 1.5 km (paint) and 2.5 km
## (rails), where a 10 cm line is a tenth of a pixel.
##
## Turkish practice as far as the centreline data can say it (simplified):
##   lane lines    white, 3 m paint, 5 m gap
##   centre line   two-way roads: solid white on main roads, dashed on streets
##   edge lines    solid white 0.3 m in from the kerb on the boulevards
##   zebra         white bars 0.5 m wide, 0.5 m apart, 4 m long, across every
##                 painted approach to a junction of three or more ways
##   stop line     0.5 m, behind the zebra, across the approaching side
## Only at-grade ways (flags & 15 == 0); the old quarters' setts are not
## painted.
extends Node3D

const CHUNK := 500.0
const LIFT := 0.035
const PIECE := 6.0
const DASH := 3.0
const GAP := 5.0
const WHITE := Color(0.86, 0.86, 0.83)
const STEEL := Color(0.55, 0.55, 0.56)
const GROOVE := Color(0.03, 0.03, 0.03)
const SLEEPER := Color(0.30, 0.28, 0.25)
const NOSING := Color(0.05, 0.05, 0.05)
const PAINTED := {"motorway": 1, "motorway_link": 1, "primary": 1, "secondary": 1}
const DRIVE := {"motorway": 1, "motorway_link": 1, "primary": 1, "secondary": 1, "street": 1, "service": 1}
const GAUGE := 1.435

var ground: Node3D
var env: Node3D
var counts := {"paint": 0, "rail": 0, "steps": 0}
var _acc := {}          # "kind:x:y" -> SurfaceTool


func _ready() -> void:
	name = "marks"
	env = ground.get("env")
	var path := "res://assets/istanbul/roads.json"
	if not FileAccess.file_exists(path):
		return
	var t0 := Time.get_ticks_msec()
	var doc: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(path))
	var nodes := PackedVector2Array()
	for q: Array in doc["nodes"]:
		nodes.append(Vector2(float(q[0]), float(q[1])))
	var deg := PackedInt32Array()
	deg.resize(nodes.size())
	var widest := PackedFloat32Array()
	widest.resize(nodes.size())
	var edges: Array = doc["edges"]
	for e: Array in edges:
		if not DRIVE.has(str(e[2])) or int(e[8]) & 15 != 0:
			continue
		for k in [int(e[0]), int(e[1])]:
			deg[k] += 1
			widest[k] = maxf(widest[k], float(e[5]))
	for e: Array in edges:
		var cls := str(e[2])
		var flags := int(e[8])
		if flags & 15 != 0:
			continue
		var pa := nodes[int(e[0])]
		var pb := nodes[int(e[1])]
		if flags & 32 != 0:
			_steps(pa, pb, float(e[5]))
		elif PAINTED.has(cls) and flags & 16 == 0:
			_street(pa, pb, e, deg[int(e[0])] >= 3, deg[int(e[1])] >= 3,
				widest[int(e[0])], widest[int(e[1])])
	for r: Dictionary in doc.get("rails", []):
		if bool(r["tunnel"]) or bool(r["bridge"]):
			continue
		var typ := str(r["type"])
		if typ != "tram" and typ != "rail":
			continue
		var pts := PackedVector2Array()
		for q: Array in r["pts"]:
			pts.append(Vector2(float(q[0]), float(q[1])))
		_rails(pts, typ == "tram")
	var paint := _paint_material()
	var steel := _steel_material()
	for key: String in _acc:
		var mi := MeshInstance3D.new()
		mi.name = key.replace(":", "_")
		(_acc[key] as SurfaceTool).index()
		mi.mesh = (_acc[key] as SurfaceTool).commit()
		var rail := key.begins_with("rail")
		mi.material_override = steel if rail else paint
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		mi.visibility_range_end = 2500.0 if rail else 1500.0
		mi.visibility_range_end_margin = 200.0
		add_child(mi)
	print("[istanbul_ground_marks] %d paint, %d rail, %d step quads in %d meshes, %d ms" %
		[counts["paint"], counts["rail"], counts["steps"], _acc.size(), Time.get_ticks_msec() - t0])
	_acc.clear()


func _street(pa: Vector2, pb: Vector2, e: Array, int_a: bool, int_b: bool, wa: float, wb: float) -> void:
	var L := pa.distance_to(pb)
	if L < 4.0:
		return
	var t := (pb - pa) / L
	var r := Vector2(t.y, -t.x)
	var W := maxf(float(e[5]), 5.0)
	var lanes := maxi(1, int(e[4]))
	var oneway := int(e[3]) == 1
	var cls := str(e[2])
	var lw := W / lanes
	var s0 := 0.0
	var s1 := L
	if int_a:
		var k := wa * 0.5 + 1.5
		s0 = k + 4.0 + 2.5
		if W >= 6.0 and k + 6.0 < L * 0.5:
			_zebra(pa, t, r, k, W)
	if int_b:
		var k := wb * 0.5 + 1.5
		s1 = L - (k + 4.0 + 2.5)
		if W >= 6.0 and k + 6.0 < L * 0.5:
			_zebra(pb, -t, -r, k, W)
			_bar(pb - t * (k + 5.0), r, -W * 0.5 if oneway else 0.0, W * 0.5)
	if s1 - s0 < 1.0:
		return
	var main := cls != "street"
	if not oneway:
		_line(pa, t, r, s0, s1, 0.0, 0.15, WHITE, not main)
		var per := lanes / 2
		for k in range(1, per):
			_line(pa, t, r, s0, s1, k * lw, 0.12, WHITE, true)
			_line(pa, t, r, s0, s1, -k * lw, 0.12, WHITE, true)
	else:
		for k in range(1, lanes):
			_line(pa, t, r, s0, s1, -W * 0.5 + k * lw, 0.12, WHITE, true)
	if cls == "motorway" and W >= 10.0:
		_line(pa, t, r, s0, s1, W * 0.5 - 0.3, 0.15, WHITE, false)
		_line(pa, t, r, s0, s1, -W * 0.5 + 0.3, 0.15, WHITE, false)


func _line(p: Vector2, t: Vector2, r: Vector2, s0: float, s1: float, o: float, w: float,
		col: Color, dashed: bool) -> void:
	if not dashed:
		_strip("paint", p + t * s0 + r * o, t, r, s1 - s0, w, col)
		return
	var s := s0
	while s < s1:
		var e := minf(s + DASH, s1)
		if e - s > 0.5:
			_strip("paint", p + t * s + r * o, t, r, e - s, w, col)
		s += DASH + GAP


func _zebra(p: Vector2, t: Vector2, r: Vector2, setback: float, W: float) -> void:
	var o := -W * 0.5 + 0.5
	while o + 0.5 <= W * 0.5 - 0.4:
		_strip("paint", p + t * setback + r * (o + 0.25), t, r, 4.0, 0.5, WHITE)
		o += 1.0


## A stop line across offsets [o0, o1] (along r) at p.
func _bar(p: Vector2, r: Vector2, o0: float, o1: float) -> void:
	var t := Vector2(-r.y, r.x)
	_strip("paint", p + r * o0, r, t, o1 - o0, 0.5, WHITE)


## Two rails per track, standard gauge. The tram's are grooved and flush in
## the paving; the main line's sit on sleepers on the ballast.
func _rails(pts: PackedVector2Array, tram: bool) -> void:
	var along := 0.0
	for i in range(1, pts.size()):
		var a := pts[i - 1]
		var b := pts[i]
		var L := a.distance_to(b)
		if L < 0.05:
			continue
		var t := (b - a) / L
		var r := Vector2(t.y, -t.x)
		for side: float in [-1.0, 1.0]:
			var o := side * (GAUGE + 0.065) * 0.5
			_strip("rail", a + r * o, t, r, L, 0.07, STEEL, 0.02 if tram else 0.16)
			if tram:
				_strip("rail", a + r * (o - side * 0.055), t, r, L, 0.04, GROOVE, 0.021)
		if not tram:
			var s := fposmod(-along, 0.65)
			while s < L:
				_strip("rail", a + t * s - r * 1.3, r, t, 2.6, 0.24, SLEEPER, 0.1)
				s += 0.65
		along += L


## Stair nosings: a dark line at each riser, every 0.32 m.
func _steps(pa: Vector2, pb: Vector2, W: float) -> void:
	var L := pa.distance_to(pb)
	if L < 0.8:
		return
	var t := (pb - pa) / L
	var r := Vector2(t.y, -t.x)
	W = clampf(W, 1.5, 8.0)
	var s := 0.2
	while s < L - 0.1:
		_strip("steps", pa + t * s - r * (W * 0.5), r, t, W, 0.07, NOSING)
		s += 0.32


## A strip starting at `start` (the middle of its first end), running `len`
## along t, `w` wide across r, cut into PIECE pieces each laid on the ground.
func _strip(kind: String, start: Vector2, t: Vector2, r: Vector2, len: float, w: float, col: Color,
		lift: float = LIFT) -> void:
	if len <= 0.0:
		return
	var n := maxi(1, int(ceil(len / PIECE)))
	var mid := start + t * (len * 0.5)
	var key := "%s:%d:%d" % [kind, int(floor(mid.x / CHUNK)), int(floor(mid.y / CHUNK))]
	if not _acc.has(key):
		var st0 := SurfaceTool.new()
		st0.begin(Mesh.PRIMITIVE_TRIANGLES)
		_acc[key] = st0
	var st: SurfaceTool = _acc[key]
	var h := r * (w * 0.5)
	var p0 := start - h
	var p1 := start + h
	var y0 := _y(p0) + lift
	var y1 := _y(p1) + lift
	for k in n:
		var s := len * float(k + 1) / n
		var q1 := start + t * s + h
		var q0 := start + t * s - h
		var z1 := _y(q1) + lift
		var z0 := _y(q0) + lift
		var A := Vector3(p0.x, y0, -p0.y)
		var B := Vector3(p1.x, y1, -p1.y)
		var C := Vector3(q1.x, z1, -q1.y)
		var D := Vector3(q0.x, z0, -q0.y)
		# Wind clockwise seen from above (Godot's front) whichever way t, r turn.
		var up := (B - A).cross(C - A).y
		var tri := [A, C, B, A, D, C] if up > 0.0 else [A, B, C, A, C, D]
		for v: Vector3 in tri:
			st.set_color(col)
			st.add_vertex(v)
		p0 = q0
		p1 = q1
		y0 = z0
		y1 = z1
		counts[kind] += 1


func _y(p: Vector2) -> float:
	return float(ground.call("mesh_height", p.x, p.y))


## Thermoplastic paint, worn by tyres and broken at the edges (as
## manhattan_markings'); the stair nosings a flat dark line.
func _paint_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode cull_back;
#include "res://shaders/thermal.gdshaderinc"
varying vec3 w;
float h2(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
float vn(vec2 p) {
    vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(h2(i), h2(i + vec2(1, 0)), f.x), mix(h2(i + vec2(0, 1)), h2(i + vec2(1, 1)), f.x), f.y);
}
void vertex() { w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
    float fw = length(fwidth(w.xz));
    bool paint = COLOR.r > 0.3;
    float wear = vn(w.xz * 1.3) * 0.6 + vn(w.xz * 5.1) * 0.4;
    float cover = mix(0.7, smoothstep(0.2, 0.5, wear), 1.0 - smoothstep(0.05, 0.3, fw));
    cover *= 0.7 + 0.3 * vn(w.xz / 9.0);
    if (!paint) { cover = 0.6; }
    NORMAL = normalize((VIEW_MATRIX * vec4(0.0, 1.0, 0.0, 0.0)).xyz);
    ALBEDO = mix(vec3(0.09, 0.09, 0.095), COLOR.rgb * 0.82, cover);
    ROUGHNESS = 0.75;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(mix(0.85, 0.35, cover), 22.0, 0.75, 0.0);
        th = th_air(th, length(CAMERA_POSITION_WORLD - w));
        TH_OUT(th);
    }
}
"""
	var m := ShaderMaterial.new()
	m.shader = sh
	return m


## Rail heads polished bright; grooves black; sleepers concrete.
func _steel_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode cull_back;
#include "res://shaders/thermal.gdshaderinc"
varying vec3 w;
void vertex() { w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
    NORMAL = normalize((VIEW_MATRIX * vec4(0.0, 1.0, 0.0, 0.0)).xyz);
    bool head = COLOR.r > 0.5;
    ALBEDO = COLOR.rgb;
    METALLIC = head ? 0.85 : 0.0;
    ROUGHNESS = head ? 0.3 : 0.9;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = head ? th_surface(0.4, 20.0, 0.6, 0.0) : th_surface(0.8, 20.0, 0.7, 0.0);
        th = th_air(th, length(CAMERA_POSITION_WORLD - w));
        TH_OUT(th);
    }
}
"""
	var m := ShaderMaterial.new()
	m.shader = sh
	return m

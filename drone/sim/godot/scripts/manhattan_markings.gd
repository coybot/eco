## Lane markings on Manhattan's streets, from roads.json (NYC Centerline,
## tools/fetch_manhattan_roads.py): white dashed lane lines, a double yellow
## down two-way streets, high-visibility ("continental") crosswalks across
## every approach to an intersection, and a stop bar before each.
##
## Geometry, not texture: paint lines are 10-15 cm wide, far below the 2 m
## street field and the 0.78 m photo, and a texture fine enough for them over
## 22 km of island would not fit any GPU budget. As quads they are ~200k
## triangles in ~500 m chunks, culled past 1.5 km (beyond that a 15 cm line is
## a tenth of a pixel).
##
## Rules, NYC's as far as the centreline data can say them:
##   lanes        CSCL travel lanes over the kerb-to-kerb width less 2.4 m per
##                parking lane, centred (parking both sides is the norm)
##   dashes       3 m paint, 9 m gap (10 ft / 30 ft)
##   two-way      double yellow on the centreline, 10 cm lines 10 cm apart
##   crosswalks   0.6 m bars, 0.6 m apart, 3 m long, across the full width,
##                set back from the cross street's kerb; only where three or
##                more streets meet
##   stop bar     0.6 m, 1.2 m behind the crosswalk, across the approaching
##                lanes (the right half on a two-way street)
## Only at-grade streets (flags 0): bridges, tunnels and the elevated FDR are
## not on the ground. Footpaths and service roads are not painted.
extends Node3D

const CHUNK := 500.0
const Y := 0.03
const DASH := 3.0
const GAP := 9.0
const PARK_W := 2.4
const DRIVE := {"motorway": 1, "motorway_link": 1, "primary": 1, "secondary": 1, "street": 1}
const DEFAULT_W := {"motorway": 20.0, "motorway_link": 8.0, "primary": 18.0, "secondary": 12.0, "street": 9.0}
const WHITE := Color(0.86, 0.86, 0.84)
const YELLOW := Color(0.85, 0.62, 0.12)

var env: Node3D
var quads := 0
var _acc := {}


func _ready() -> void:
	name = "markings"
	env = get_parent().get_parent() as Node3D
	var path := "res://assets/manhattan/roads.json"
	if not FileAccess.file_exists(path):
		return
	var t0 := Time.get_ticks_msec()
	var doc: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(path))
	var off: Vector2 = env.call("data_offset")
	var b: Rect2 = env.call("sim_bounds")
	b = b.grow(200.0)
	var nodes := PackedVector2Array()
	for q: Array in doc["nodes"]:
		nodes.append(Vector2(float(q[0]), float(q[1])) - off)
	# Driveable degree and widest street at each node.
	var deg := PackedInt32Array()
	deg.resize(nodes.size())
	var widest := PackedFloat32Array()
	widest.resize(nodes.size())
	var edges: Array = doc["edges"]
	for e: Array in edges:
		if not DRIVE.has(str(e[2])):
			continue
		var w := _width(e)
		for k in [int(e[0]), int(e[1])]:
			deg[k] += 1
			widest[k] = maxf(widest[k], w)
	for e: Array in edges:
		var cls := str(e[2])
		if not DRIVE.has(cls) or int(e[8]) != 0:
			continue
		var a := int(e[0])
		var bb := int(e[1])
		var pa := nodes[a]
		var pb := nodes[bb]
		if not b.has_point(pa):
			continue
		_street(pa, pb, e, deg[a] >= 3, deg[bb] >= 3, widest[a], widest[bb])
	var mat := _material()
	for k in _acc:
		var mi := MeshInstance3D.new()
		mi.name = "markings_%d_%d" % [k.x, k.y]
		# Shared corners: 4 vertices a quad instead of 6 (the mesh is most
		# of this scene's vertex memory).
		(_acc[k] as SurfaceTool).index()
		mi.mesh = (_acc[k] as SurfaceTool).commit()
		mi.material_override = mat
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		mi.visibility_range_end = 1500.0
		mi.visibility_range_end_margin = 150.0
		add_child(mi)
	print("[manhattan_markings] %d quads in %d chunks, %d ms" % [quads, _acc.size(), Time.get_ticks_msec() - t0])
	# The SurfaceTools hold every vertex again at ~200 bytes each: 250 MB of
	# RAM for nothing once committed.
	_acc.clear()


static func _width(e: Array) -> float:
	var w := float(e[5])
	if w < 4.0:
		w = float(DEFAULT_W.get(str(e[2]), 9.0))
	return w


func _street(pa: Vector2, pb: Vector2, e: Array, int_a: bool, int_b: bool, wa: float, wb: float) -> void:
	var L := pa.distance_to(pb)
	if L < 4.0:
		return
	var t := (pb - pa) / L
	var r := Vector2(t.y, -t.x)       # right of a -> b
	var W := _width(e)
	var lanes := maxi(1, int(e[4]))
	var oneway := int(e[3]) == 1
	var park := clampi(int(e[6]), 0, 2)
	var travel := maxf(W - PARK_W * park, 2.7 * lanes)
	travel = minf(travel, W)
	var lw := travel / lanes
	# Where the street's own markings start and end: past the cross street's
	# kerb (half its width), the crosswalk and the stop bar.
	var s0 := 0.0
	var s1 := L
	if int_a:
		var k := wa * 0.5 + 1.0
		s0 = k + 3.0 + 2.0
		if W >= 6.0 and k + 4.0 < L * 0.5:
			_crosswalk(pa, t, r, k, W)
			if not oneway:
				_bar(pa + t * (k + 4.2), r, -W * 0.5, 0.0)
	if int_b:
		var k := wb * 0.5 + 1.0
		s1 = L - (k + 3.0 + 2.0)
		if W >= 6.0 and k + 4.0 < L * 0.5:
			_crosswalk(pb, -t, -r, k, W)
			_bar(pb - t * (k + 4.2), r, 0.0 if not oneway else -W * 0.5, W * 0.5)
	if s1 - s0 < 1.0:
		return
	if not oneway:
		for o in [-0.1, 0.1]:
			_line(pa, t, r, s0, s1, o, 0.1, YELLOW, false)
		var per := lanes / 2
		for k in range(1, per):
			_line(pa, t, r, s0, s1, k * lw, 0.12, WHITE, true)
			_line(pa, t, r, s0, s1, -k * lw, 0.12, WHITE, true)
	else:
		for k in range(1, lanes):
			_line(pa, t, r, s0, s1, -travel * 0.5 + k * lw, 0.12, WHITE, true)


func _line(p: Vector2, t: Vector2, r: Vector2, s0: float, s1: float, o: float, w: float,
		col: Color, dashed: bool) -> void:
	if not dashed:
		_rect(p + t * s0 + r * o, t, r, s1 - s0, w, col)
		return
	# The rhythm restarts on each CSCL segment (one per block), which is also
	# where the paint crews restart it: past each crosswalk.
	var s := s0
	while s < s1:
		var e := minf(s + DASH, s1)
		if e - s > 0.5:
			_rect(p + t * s + r * o, t, r, e - s, w, col)
		s += DASH + GAP


func _crosswalk(p: Vector2, t: Vector2, r: Vector2, setback: float, W: float) -> void:
	var o := -W * 0.5 + 0.4
	while o + 0.6 <= W * 0.5 - 0.3:
		_rect(p + t * setback + r * (o + 0.3), t, r, 3.0, 0.6, WHITE)
		o += 1.2


## A stop bar across offsets [o0, o1] at p.
func _bar(p: Vector2, r: Vector2, o0: float, o1: float) -> void:
	var t := Vector2(-r.y, r.x)
	var c := p + r * ((o0 + o1) * 0.5) - t * 0.3
	# A rect "along" r: length o1-o0, width 0.6 along t.
	_rect(c - r * ((o1 - o0) * 0.5), r, -t, o1 - o0, 0.6, WHITE)


## A rectangle starting at `start` (its centre-line start), running `len`
## along t, `w` wide across r.
func _rect(start: Vector2, t: Vector2, r: Vector2, len: float, w: float, col: Color) -> void:
	var a := start - r * (w * 0.5)
	var b := start + r * (w * 0.5)
	var c := b + t * len
	var d := a + t * len
	var mid := (a + c) * 0.5
	var k := Vector2i(int(floor(mid.x / CHUNK)), int(floor(mid.y / CHUNK)))
	if not _acc.has(k):
		var st := SurfaceTool.new()
		st.begin(Mesh.PRIMITIVE_TRIANGLES)
		_acc[k] = st
	var st: SurfaceTool = _acc[k]
	# Wind each triangle clockwise seen from above whichever way t and r turn.
	var flip := (t.x * r.y - t.y * r.x) > 0.0
	var tri := [a, b, c, a, c, d] if flip else [a, c, b, a, d, c]
	# No normal array: the shader knows the paint faces up, and position +
	# colour alone is 16 bytes a vertex instead of 32.
	for v: Vector2 in tri:
		st.set_color(col)
		st.add_vertex(Vector3(v.x, Y, -v.y))
	quads += 1


## Thermoplastic paint, worn: thinner in the wheel paths and broken up at the
## edges, read from world-space noise so no two dashes wear alike.
func _material() -> ShaderMaterial:
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
    float wear = vn(w.xz * 1.3) * 0.6 + vn(w.xz * 5.1) * 0.4;
    float cover = mix(0.75, smoothstep(0.18, 0.5, wear), 1.0 - smoothstep(0.05, 0.3, fw));
    cover *= 0.75 + 0.25 * vn(w.xz / 9.0);
    vec3 asphalt = vec3(0.1, 0.1, 0.105);
    NORMAL = normalize((VIEW_MATRIX * vec4(0.0, 1.0, 0.0, 0.0)).xyz);
    ALBEDO = mix(asphalt, COLOR.rgb * 0.82, cover);
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

## The bridges of historic Istanbul, built procedurally at their real
## positions (alignments from OpenStreetMap's bridge ways in roads.json) and
## to their published dimensions:
##
##   Galata Bridge (1994)       490 m, 42 m wide, 80 m double-leaf bascule in
##                              the middle. Two levels: the road and the T1
##                              tram on top, its rails lined with anglers'
##                              rods; below, the restaurant arcade along both
##                              sides, a walkway outside it. Four control
##                              houses stand at the bascule's corners.
##   Ataturk (Unkapani) Bridge  477 m over the water, 25 m wide, low.
##   Golden Horn Metro Bridge   (Halic Metro Koprusu, 2014) the M2 line;
##                              cable-stayed, 180 m main span between two
##                              65 m pylons, 13 m over the water, Halic
##                              station on the deck under a white canopy.
##   15 July Martyrs Bridge     (Bosphorus Bridge, 1973) suspension, 1074 m
##                              main span, towers 165 m above the sea, deck
##                              64 m over it, 33.4 m wide, inclined (zig-zag)
##                              hangers; unsuspended side spans on piers.
##   Halic Bridge (1974)        the motorway across the upper Golden Horn,
##                              ~995 m, 32 m wide, ~22 m over the water.
##
## BRIDGES ARE OBSTACLES (as Manhattan's bridges.gd): one StaticBody3D per
## bridge on collision layer 1 (fixedwing_manager's strike sweep), built of
## plain boxes: decks, piers, towers, pylons, and thin boxes standing for the
## cable curtain and the stay fans. The tall parts (the suspension towers'
## legs, the metro pylons) also go into the env's structure model through
## env.add_obstacle, so raycasts and occlusion see them. Decks do not: as a
## prism from the ground up a deck would wall off the water under it.
##
## Night: lights() lists every deck lamp, necklace light and beacon as
## [Vector3 Godot position, Color]. By default they are drawn here as one
## MultiMesh of camera-facing points driven by env.night_amount() (the
## Bosphorus bridge's necklace slowly cycles its colours, as its LED show
## does); a night script that draws them itself calls set_own_lights(false).
## The Galata arcade's restaurant glass and signs glow at night in the
## bridge material.
extends Node3D

const FINE_RANGE := 2200.0
const MAIN_RANGE := 30000.0

var env: Node3D
var _lights: Array = []          # [Vector3 global, Color, show (bool)]
var _mat: ShaderMaterial
var _light_mat: ShaderMaterial
var _light_mmi: MultiMeshInstance3D
var _own_lights := true
var _night_last := -1.0
var _rng := RandomNumberGenerator.new()
var _decks: Array = []           # [{id, width, pts: PackedVector3Array (e, n, y)}]
var _obstacles := 0

# Per bridge while building.
var _id := ""
var _a := Vector2.ZERO           # ENU start of the axis
var _u := Vector2.RIGHT          # along
var _v := Vector2.UP             # across, + left
var _len := 0.0
var _origin := Vector3.ZERO      # Godot position of s = 0
var _prof: PackedVector2Array    # (s, deck top y) knots
var _st := {}                    # "main" / "fine" -> SurfaceTool
var _boxes: Array = []           # [Transform3D (local), Vector3 size]


func _ready() -> void:
	env = get_parent()
	name = "istanbul_bridges"
	_rng.seed = 1453
	_make_material()
	_galata()
	_ataturk()
	_metro()
	_martyrs()
	_halic()
	_build_lights()
	print("[istanbul_bridges] 5 bridges, %d tall parts in the structure model, %d lights"
		% [_obstacles, _lights.size()])


func _process(_dt: float) -> void:
	var nt := clampf(float(env.call("night_amount")), 0.0, 1.0)
	if absf(nt - _night_last) < 0.01:
		return
	_night_last = nt
	_mat.set_shader_parameter("night", nt)
	if _light_mat != null:
		_light_mat.set_shader_parameter("night", nt)


# ------------------------------------------------------------------ public API

## Every light on the bridges: [Vector3 Godot position, Color].
func lights() -> Array:
	var out: Array = []
	for l: Array in _lights:
		out.append([l[0], l[1]])
	return out


## false: a night script draws lights() itself; hide the ones drawn here.
func set_own_lights(on: bool) -> void:
	_own_lights = on
	if _light_mmi != null:
		_light_mmi.visible = on


## Deck centrelines: [{id, width, pts: PackedVector3Array (e, n, y)}].
func decks() -> Array:
	return _decks


# ------------------------------------------------------------------ material

## One material for every bridge part: the paint is the vertex colour, its
## alpha says what the part is (and so its finish, its night glow and its
## heat in the thermal band).
const CLS_ASPHALT := 0.0
const CLS_GLASS := 0.2       # restaurant / station glazing: dark by day, warm at night
const CLS_SIGN := 0.35       # shop signs: their colour by day, lit at night
const CLS_STONE := 0.5       # concrete and masonry
const CLS_PAINT := 0.7       # painted, not metallic (pylons, canopies)
const CLS_STEEL := 1.0

const SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"
uniform float night = 0.0;
varying vec3 v_world;
void vertex() { v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
    float cls = COLOR.a;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th;
        if (cls > 0.85) {
            // Painted steel: thin, warms fast in the sun, falls below the air
            // under a clear night sky.
            th = th_surface(0.5, 20.0, 0.2, 0.0);
            float up = clamp((INV_VIEW_MATRIX * vec4(NORMAL, 0.0)).y, 0.0, 1.0);
            th.y = mix(th.y, th_enc(mix(thermal_air_c, thermal_sky_c, up)), 0.3);
        } else if (cls > 0.6) {
            th = th_surface(0.4, 16.0, 0.4, 0.0);
        } else if (cls > 0.425) {
            th = th_surface(0.5, 18.0, 0.7, 0.0);      // concrete piers
        } else if (cls > 0.1) {
            th = th_surface(0.3, 8.0, 0.3, 6.0);       // lit, heated restaurants
        } else {
            th = th_surface(0.85, 26.0, 0.6, 0.0);     // the roadway
        }
        TH_OUT(th);
    } else {
        vec3 c = COLOR.rgb;
        if (cls < 0.1) {
            ALBEDO = c; ROUGHNESS = 0.9;
        } else if (cls < 0.275) {
            // Glass: dark by day; warm and a little uneven (one room lit
            // brighter than the next) at night.
            float k = fract(sin(dot(floor(v_world.xz / 5.0), vec2(12.9898, 78.233))) * 43758.5);
            ALBEDO = c * 0.25; ROUGHNESS = 0.12; METALLIC = 0.2;
            EMISSION = vec3(1.0, 0.74, 0.46) * night * (0.9 + 1.4 * k);
        } else if (cls < 0.425) {
            ALBEDO = c; ROUGHNESS = 0.5;
            EMISSION = c * night * 2.2;
        } else if (cls < 0.6) {
            ALBEDO = c; ROUGHNESS = 0.95;
        } else if (cls < 0.85) {
            ALBEDO = c; ROUGHNESS = 0.55;
        } else {
            ALBEDO = c; ROUGHNESS = 0.6; METALLIC = 0.25;
        }
    }
}
"""


func _make_material() -> void:
	var sh := Shader.new()
	sh.code = SHADER
	_mat = ShaderMaterial.new()
	_mat.shader = sh


# ------------------------------------------------------------------ frame

func _begin(id: String, a: Vector2, b: Vector2, prof: Array) -> void:
	_id = id
	_a = a
	_len = a.distance_to(b)
	_u = (b - a) / _len
	_v = Vector2(-_u.y, _u.x)
	_origin = Vector3(a.x, 0.0, -a.y)
	_prof = PackedVector2Array(prof)
	_st = {}
	_boxes = []


## Local position (relative to the bridge's holder) at s along, lat across
## (+ left), y up (sea-level metres).
func _p(s: float, lat: float, y: float) -> Vector3:
	var d := _u * s + _v * lat
	return Vector3(d.x, y, -d.y)


func _en(s: float, lat: float) -> Vector2:
	return _a + _u * s + _v * lat


func _along() -> Vector3:
	return Vector3(_u.x, 0.0, -_u.y)


func _across() -> Vector3:
	return Vector3(_v.x, 0.0, -_v.y)


func _ground(s: float, lat: float = 0.0) -> float:
	var q := _en(s, lat)
	if bool(env.call("is_water", q.x, q.y)):
		return -6.0
	return float(env.call("ground_height", q.x, q.y))


## Deck top (road surface) at s: the profile's knots, linear between.
func _py(s: float) -> float:
	if s <= _prof[0].x:
		return _prof[0].y
	for i in range(1, _prof.size()):
		if s <= _prof[i].x:
			var t := (s - _prof[i - 1].x) / maxf(_prof[i].x - _prof[i - 1].x, 0.01)
			return lerpf(_prof[i - 1].y, _prof[i].y, t)
	return _prof[_prof.size() - 1].y


func _tool(fine: bool) -> SurfaceTool:
	var key := "fine" if fine else "main"
	if not _st.has(key):
		var st := SurfaceTool.new()
		st.begin(Mesh.PRIMITIVE_TRIANGLES)
		_st[key] = st
	return _st[key]


## An oriented box: centre c, axis x (unit) with a hint for up; size along
## (x, up, the third axis). Colour is sRGB; cls goes in alpha.
func _obox(c: Vector3, x: Vector3, up_hint: Vector3, size: Vector3, col: Color, cls: float,
		solid: bool = false, fine: bool = false, caps: bool = true) -> void:
	var z := x.cross(up_hint).normalized()
	if z.length() < 0.5:
		z = x.cross(_across()).normalized()
	var y := z.cross(x).normalized()
	var st := _tool(fine)
	var lc := col.srgb_to_linear()
	lc.a = cls
	st.set_color(lc)
	var hx := x * size.x * 0.5
	var hy := y * size.y * 0.5
	var hz := z * size.z * 0.5
	var faces := [[y, hy, hz, hx], [-y, -hy, -hz, hx], [z, hz, hx, hy], [-z, -hz, -hx, hy]]
	if caps:
		faces += [[x, hx, hy, hz], [-x, -hx, hy, -hz]]
	for f in faces:
		var nrm: Vector3 = f[0]
		var o: Vector3 = c + f[1]
		var a: Vector3 = f[2]
		var b: Vector3 = f[3]
		var q := [o - a - b, o + a - b, o + a + b, o - a + b]
		# Godot front faces are clockwise seen from outside.
		if a.cross(b).dot(nrm) > 0.0:
			q = [q[0], q[3], q[2], q[1]]
		st.set_normal(nrm)
		for k in [0, 1, 2, 0, 2, 3]:
			st.add_vertex(q[k])
	if solid:
		_boxes.append([Transform3D(Basis(x, y, z), c), size])


## A box aligned with the bridge: centre (s, lat, y), size (along, up, across).
func _block(s: float, lat: float, y: float, size: Vector3, col: Color, cls: float,
		solid: bool = true, fine: bool = false) -> void:
	_obox(_p(s, lat, y), _along(), Vector3.UP, Vector3(size.x, size.y, size.z), col, cls, solid, fine)


## A square-section member from p to q (local).
func _beam(p: Vector3, q: Vector3, w: float, col: Color, cls: float, fine: bool = true,
		solid: bool = false) -> void:
	var d := q - p
	var l := d.length()
	if l < 0.01:
		return
	var x := d / l
	var hint := Vector3.UP if absf(x.y) < 0.95 else _along()
	_obox((p + q) * 0.5, x, hint, Vector3(l, w, w), col, cls, solid, fine, false)


## A strip following the deck profile from s0 to s1, lat0..lat1 across, its
## top `top` above the deck surface, `thick` deep. Pieces of `step` m.
func _strip(s0: float, s1: float, lat0: float, lat1: float, top: float, thick: float,
		col: Color, cls: float, solid: bool = false, step: float = 20.0, fine: bool = false) -> void:
	var n := maxi(1, int(ceil((s1 - s0) / step)))
	var lat := (lat0 + lat1) * 0.5
	var w := absf(lat1 - lat0)
	for i in n:
		var sa := lerpf(s0, s1, float(i) / n)
		var sb := lerpf(s0, s1, float(i + 1) / n)
		var pa := _p(sa, lat, _py(sa) + top - thick * 0.5)
		var pb := _p(sb, lat, _py(sb) + top - thick * 0.5)
		var d := pb - pa
		_obox((pa + pb) * 0.5, d.normalized(), Vector3.UP, Vector3(d.length() + 0.02, thick, w),
			col, cls, solid, fine)


## A vertical post (along x across) from y0 to y1.
func _post(s: float, lat: float, y0: float, y1: float, along: float, across: float,
		col: Color, cls: float, solid: bool = true, fine: bool = false) -> void:
	if y1 - y0 < 0.05:
		return
	_block(s, lat, (y0 + y1) * 0.5, Vector3(along, y1 - y0, across), col, cls, solid, fine)


func _light(s: float, lat: float, y: float, col: Color, show: bool = false) -> void:
	_lights.append([_origin + _p(s, lat, y), col, show])


## A rectangle (s0..s1, lat0..lat1) as an ENU ring into the env's structure
## model, bottom..top absolute metres.
func _obstacle(s0: float, s1: float, lat0: float, lat1: float, bottom: float, top: float) -> void:
	if not env.has_method("add_obstacle"):
		return
	var ring := PackedVector2Array([_en(s0, lat0), _en(s1, lat0), _en(s1, lat1), _en(s0, lat1)])
	# CCW (env's footprints are).
	var area := 0.0
	for i in 4:
		area += ring[i].x * ring[(i + 1) % 4].y - ring[(i + 1) % 4].x * ring[i].y
	if area < 0.0:
		ring.reverse()
	env.call("add_obstacle", ring, bottom, top)
	_obstacles += 1


func _record_deck(width: float, s0: float, s1: float) -> void:
	var pts := PackedVector3Array()
	var s := s0
	while s < s1:
		var q := _en(s, 0.0)
		pts.append(Vector3(q.x, q.y, _py(s)))
		s += 20.0
	var q1 := _en(s1, 0.0)
	pts.append(Vector3(q1.x, q1.y, _py(s1)))
	_decks.append({"id": _id, "width": width, "pts": pts})


func _commit() -> void:
	var holder := Node3D.new()
	holder.name = "bridge_" + _id
	holder.position = _origin
	add_child(holder)
	for key in _st:
		var mesh: ArrayMesh = (_st[key] as SurfaceTool).commit()
		if mesh.get_surface_count() == 0:
			continue
		mesh.surface_set_material(0, _mat)
		var mi := MeshInstance3D.new()
		mi.name = "%s_%s" % [_id, key]
		mi.mesh = mesh
		if key == "fine":
			mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
			mi.visibility_range_end = FINE_RANGE
			mi.visibility_range_end_margin = 200.0
			mi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
		else:
			mi.visibility_range_end = MAIN_RANGE
		holder.add_child(mi)
	if not _boxes.is_empty():
		var body := StaticBody3D.new()
		body.name = "solid_" + _id
		body.collision_layer = 1     # LAYER_STRUCTURE (fixedwing_manager.gd)
		body.collision_mask = 0
		body.set_meta("bridge", _id)
		for bx in _boxes:
			var cs := CollisionShape3D.new()
			var shape := BoxShape3D.new()
			shape.size = bx[1]
			cs.shape = shape
			var xf: Transform3D = bx[0]
			cs.transform = Transform3D(xf.basis.orthonormalized(), xf.origin)
			body.add_child(cs)
		holder.add_child(body)
	_st = {}
	_boxes = []


## Along the axis, where the water starts and ends (sampled every 2 m).
func _water_span() -> Vector2:
	var w0 := -1.0
	var w1 := -1.0
	var s := 0.0
	while s <= _len:
		var q := _en(s, 0.0)
		if bool(env.call("is_water", q.x, q.y)):
			if w0 < 0.0:
				w0 = s
			w1 = s
		s += 2.0
	return Vector2(w0, w1) if w0 >= 0.0 else Vector2(0.0, _len)


# ------------------------------------------------------------------ common parts

const C_ROAD := Color(0.16, 0.16, 0.17)
const C_WALK := Color(0.60, 0.58, 0.54)
const C_CONC := Color(0.62, 0.61, 0.58)
const C_RAIL := Color(0.30, 0.31, 0.33)
const C_LAMP := Color(0.25, 0.26, 0.27)
const L_WARM := Color(1.0, 0.82, 0.58)
const L_WHITE := Color(0.95, 0.95, 1.0)
const L_RED := Color(1.0, 0.12, 0.08)


## Railing along one side: a top rail and a mid rail on posts.
func _railing(s0: float, s1: float, lat: float, col: Color) -> void:
	_strip(s0, s1, lat - 0.06, lat + 0.06, 1.1, 0.12, col, CLS_STEEL, false, 20.0, true)
	_strip(s0, s1, lat - 0.04, lat + 0.04, 0.55, 0.06, col, CLS_STEEL, false, 20.0, true)
	var s := s0
	while s <= s1:
		_post(s, lat, _py(s), _py(s) + 1.1, 0.08, 0.08, col, CLS_STEEL, false, true)
		s += 2.5


## Lamp posts every `step` along s0..s1 at lat, arm toward the road.
func _lamps(s0: float, s1: float, lat: float, step: float, h: float, col: Color = L_WARM) -> void:
	var s := s0
	var inward := -signf(lat)
	while s <= s1:
		var y := _py(s)
		_post(s, lat, y, y + h, 0.25, 0.25, C_LAMP, CLS_STEEL, false, true)
		_beam(_p(s, lat, y + h), _p(s, lat + inward * 1.6, y + h + 0.3), 0.15, C_LAMP, CLS_STEEL)
		_light(s, lat + inward * 1.6, y + h + 0.1, col)
		s += step


## Concrete piers (pairs across) every `step` from the ground/seabed to the
## deck's underside.
func _piers(s0: float, s1: float, step: float, lats: Array, under: float, size: Vector2,
		abs_top: float = NAN) -> void:
	var s := s0
	while s <= s1 + 0.1:
		for lat: float in lats:
			var g := _ground(s, lat)
			var top := abs_top if not is_nan(abs_top) else _py(s) - under
			_post(s, lat, g, top, size.x, size.y, C_CONC, CLS_STONE)
		s += step


# ------------------------------------------------------------------ Galata

func _galata() -> void:
	var a := Vector2(-699.0, 1070.0)       # Eminonu (south)
	var b := Vector2(-479.0, 1473.0)       # Karakoy (north)
	_begin("galata", a, b, [])
	var w := _water_span()
	var s0 := -14.0
	var s1 := _len + 14.0
	var ya := _ground(s0) + 0.3
	var yb := _ground(s1) + 0.3
	var top := 7.0
	_prof = PackedVector2Array([Vector2(s0, ya), Vector2(w.x + 45.0, top), Vector2(w.y - 45.0, top),
		Vector2(s1, yb)])
	var mid := (w.x + w.y) * 0.5
	var bas0 := mid - 40.0
	var bas1 := mid + 40.0
	var hw := 21.0
	var grey := Color(0.66, 0.68, 0.70)
	var girder := Color(0.50, 0.53, 0.56)
	# The upper deck: girder, road, tram median, pavements.
	_strip(s0, bas0, -hw, hw, 0.0, 1.2, grey, CLS_STEEL, true)
	_strip(bas1, s1, -hw, hw, 0.0, 1.2, grey, CLS_STEEL, true)
	# The bascule: two deeper steel leaves, a seam between them.
	_strip(bas0, mid - 0.15, -hw, hw, 0.0, 2.8, girder, CLS_STEEL, true, 20.0)
	_strip(mid + 0.15, bas1, -hw, hw, 0.0, 2.8, girder, CLS_STEEL, true, 20.0)
	_strip(s0, s1, -14.0, 14.0, 0.06, 0.1, C_ROAD, CLS_ASPHALT, false, 20.0)
	_strip(s0, s1, -3.6, 3.6, 0.14, 0.12, Color(0.45, 0.44, 0.42), CLS_STONE, false, 20.0)
	for side in [-1.0, 1.0]:
		_strip(s0, s1, side * 14.0, side * hw, 0.22, 0.22, C_WALK, CLS_STONE, false, 20.0)
		_strip(s0, s1, side * 13.9, side * 14.3, 0.7, 0.6, C_RAIL, CLS_STEEL, false, 20.0, true)
		_railing(s0, s1, side * (hw - 0.1), C_RAIL)
		# The T1's rails, two tracks.
		for tr in [-1.75, 1.75]:
			for r in [-0.72, 0.72]:
				_strip(s0, s1, tr + r - 0.04, tr + r + 0.04, 0.26, 0.08, Color(0.55, 0.55, 0.55),
					CLS_STEEL, false, 20.0, true)
		_lamps(s0 + 8.0, s1 - 8.0, side * (hw - 0.6), 32.0, 9.0)
	# Tram catenary masts down the median.
	var s := s0 + 12.0
	while s < s1 - 6.0:
		var y := _py(s)
		_post(s, 0.0, y, y + 7.2, 0.3, 0.3, C_LAMP, CLS_STEEL, false, true)
		_beam(_p(s, -3.2, y + 6.9), _p(s, 3.2, y + 6.9), 0.12, C_LAMP, CLS_STEEL)
		s += 38.0
	# The lower level, side spans only: floor, the restaurants' glazed fronts
	# facing the water, their signs under the deck's edge, the walkway
	# outside them, and the piers under it all.
	var floor_y := 1.9
	var under := top - 1.2
	for span in [[w.x + 4.0, bas0 - 4.0], [bas1 + 4.0, w.y - 4.0]]:
		var a0: float = span[0]
		var a1: float = span[1]
		if a1 - a0 < 20.0:
			continue
		var lc := (a0 + a1) * 0.5
		var ll := a1 - a0
		_block(lc, 0.0, floor_y - 0.35, Vector3(ll, 0.7, 2.0 * hw + 1.0), C_CONC, CLS_STONE)
		for side in [-1.0, 1.0]:
			# The restaurants' block (solid), its glass toward the water.
			_block(lc, side * 11.0, (floor_y + under) * 0.5, Vector3(ll, under - floor_y, 12.0),
				Color(0.42, 0.40, 0.37), CLS_STONE)
			var q := a0
			var k := 0
			while q < a1 - 4.0:
				var seg := minf(6.0, a1 - q)
				_block(q + seg * 0.5 - 0.3, side * 17.05, (floor_y + under) * 0.5 - 0.2,
					Vector3(seg - 0.6, under - floor_y - 0.9, 0.12), Color(0.55, 0.60, 0.62), CLS_GLASS,
					false, true)
				_post(q, side * 17.1, floor_y, under, 0.3, 0.3, Color(0.3, 0.3, 0.3), CLS_STEEL, false, true)
				# A sign every other bay.
				if k % 2 == 0:
					var sc: Color = [Color(0.85, 0.15, 0.12), Color(0.95, 0.75, 0.2), Color(0.15, 0.45, 0.8),
						Color(0.9, 0.9, 0.85), Color(0.2, 0.6, 0.35)][_rng.randi() % 5]
					_block(q + 6.0, side * 21.1, under - 0.4, Vector3(10.0, 0.7, 0.15), sc, CLS_SIGN,
						false, true)
				q += 6.0
				k += 1
			# The walkway's outer rail.
			_block(lc, side * 21.4, floor_y + 0.55, Vector3(ll, 1.1, 0.08), C_RAIL, CLS_STEEL, false, true)
			# Under-deck lights over the walkway.
			var t := a0 + 6.0
			while t < a1 - 3.0:
				_light(t, side * 19.0, under - 0.3, L_WARM)
				t += 12.0
		# The piers stop under the floor.
		_piers(a0 + 3.0, a1 - 3.0, 30.0, [-15.0, -6.0, 6.0, 15.0], 0.0, Vector2(1.6, 1.6), floor_y - 0.7)
	# Piers of the bascule's fixed ends: heavy blocks the leaves pivot on.
	for e in [bas0 - 2.0, bas1 + 2.0]:
		_post(e, 0.0, -6.0, top - 2.8, 6.0, 2.0 * hw, C_CONC, CLS_STONE)
	# The four control houses at the bascule's corners: a glazed cab on a
	# shaft, a pyramid cap.
	for e in [bas0 - 3.0, bas1 + 3.0]:
		for side in [-1.0, 1.0]:
			var lat: float = side * (hw - 2.5)
			var y := _py(e)
			_post(e, lat, floor_y, y + 7.0, 5.0, 5.0, Color(0.84, 0.84, 0.82), CLS_PAINT)
			_block(e, lat, y + 5.0, Vector3(5.2, 2.2, 5.2), Color(0.5, 0.58, 0.62), CLS_GLASS, false)
			for k in 4:
				var f := 1.0 - float(k) / 4.0
				_block(e, lat, y + 7.4 + k * 0.7, Vector3(5.6 * f, 0.7, 5.6 * f), Color(0.42, 0.45, 0.48),
					CLS_STEEL, k == 0)
			_light(e, lat, y + 10.6, L_RED)
	_anglers(s0 + 6.0, bas0 - 6.0, hw)
	_anglers(bas1 + 6.0, s1 - 6.0, hw)
	_record_deck(2.0 * hw, s0, s1)
	_commit()


## The anglers along the upper rails, their rods out over the water.
func _anglers(s0: float, s1: float, hw: float) -> void:
	for side in [-1.0, 1.0]:
		var s := s0
		while s < s1:
			if _rng.randf() < 0.62:
				var y := _py(s)
				var lat: float = side * (hw - 0.6)
				var coat: Color = [Color(0.15, 0.16, 0.2), Color(0.3, 0.25, 0.2), Color(0.12, 0.12, 0.12),
					Color(0.35, 0.35, 0.38), Color(0.45, 0.2, 0.15)][_rng.randi() % 5]
				_post(s, lat, y + 0.2, y + 1.45, 0.42, 0.32, coat, CLS_PAINT, false, true)
				_post(s, lat, y + 1.45, y + 1.72, 0.22, 0.22, Color(0.55, 0.42, 0.34), CLS_PAINT, false, true)
				var reach := _rng.randf_range(3.2, 4.6)
				var lift := _rng.randf_range(1.2, 2.4)
				var tip := _p(s + _rng.randf_range(-0.8, 0.8), side * (hw + reach), y + 1.4 + lift)
				_beam(_p(s, side * (hw - 0.3), y + 1.2), tip, 0.035, Color(0.1, 0.1, 0.1), CLS_STEEL)
			s += 2.2


# ------------------------------------------------------------------ Ataturk

func _ataturk() -> void:
	var a := Vector2(-1562.0, 1554.0)      # Unkapani (south)
	var b := Vector2(-1048.0, 1866.0)      # Azapkapi (north)
	_begin("ataturk", a, b, [])
	var w := _water_span()
	var ya := _ground(0.0) + 0.3
	var yb := _ground(_len) + 0.3
	var top := 6.0
	_prof = PackedVector2Array([Vector2(0.0, ya), Vector2(w.x - 10.0, top - 0.6), Vector2(w.x + 30.0, top),
		Vector2(w.y - 30.0, top), Vector2(w.y + 10.0, top - 0.6), Vector2(_len, yb)])
	var hw := 12.5
	var paint := Color(0.56, 0.60, 0.62)
	_strip(0.0, _len, -hw, hw, 0.0, 1.6, paint, CLS_STEEL, true)
	_strip(0.0, _len, -8.5, 8.5, 0.06, 0.1, C_ROAD, CLS_ASPHALT)
	for side in [-1.0, 1.0]:
		_strip(0.0, _len, side * 8.5, side * hw, 0.2, 0.2, C_WALK, CLS_STONE)
		_railing(0.0, _len, side * (hw - 0.1), C_RAIL)
		_lamps(10.0, _len - 10.0, side * (hw - 0.5), 30.0, 8.0)
	_strip(0.0, _len, -0.3, 0.3, 0.6, 0.6, Color(0.7, 0.7, 0.68), CLS_STONE, false, 20.0, true)
	# Its pontoon piers: broad low blocks at the waterline, columns above.
	var s := w.x + 20.0
	while s < w.y - 10.0:
		_block(s, 0.0, -1.0, Vector3(8.0, 3.0, 2.0 * hw - 2.0), Color(0.36, 0.36, 0.35), CLS_STONE)
		for lat in [-7.0, 0.0, 7.0]:
			_post(s, lat, 0.5, _py(s) - 1.6, 1.4, 1.4, C_CONC, CLS_STONE)
		s += 44.0
	_record_deck(2.0 * hw, 0.0, _len)
	_commit()


# ------------------------------------------------------------------ Golden Horn Metro

## The metro bridge's two pylons, ENU: OpenStreetMap maps each as a 65 m
## building standing in the water.
const METRO_PYLONS := [Vector2(-1183.0, 1502.0), Vector2(-1076.0, 1649.0)]


## Before the buildings are drawn (istanbul_sea.build calls this): the OSM
## footprints these bridges draw themselves (the metro pylons) are claimed,
## so the base buildings do not draw a plain box over them. They stay solid.
static func claim(env_node: Node3D) -> void:
	# Packed arrays are values: change a copy, assign it back.
	var skip: PackedByteArray = env_node.b_skip
	for c: Vector2 in METRO_PYLONS:
		var ids: PackedInt32Array = env_node.call("buildings_in", Rect2(c - Vector2(20, 20), Vector2(40, 40)))
		for i in ids:
			var bc: Vector2 = env_node.b_centre[i]
			if bc.distance_to(c) < 15.0 and float(env_node.b_h[i]) > 30.0:
				skip[i] = 1
	env_node.b_skip = skip

func _metro() -> void:
	var a := Vector2(-1405.9, 1191.4)      # Unkapani (south portal)
	var b := Vector2(-864.8, 1934.3)       # Sishane (north portal)
	_begin("halic_metro", a, b, [])
	var w := _water_span()
	var top := 13.0
	var ya := maxf(_ground(0.0), 1.0) + 0.5
	var yb := maxf(_ground(_len), 1.0) + 0.5
	_prof = PackedVector2Array([Vector2(0.0, ya), Vector2(w.x, top), Vector2(w.y, top), Vector2(_len, yb)])
	# The pylons where OpenStreetMap has them (METRO_PYLONS), 182 m apart.
	var t0 := (METRO_PYLONS[0] - a).dot(_u)
	var t1 := (METRO_PYLONS[1] - a).dot(_u)
	var white := Color(0.90, 0.91, 0.91)
	var hw := 7.5
	# Deck: a box girder, ballast, two tracks.
	_strip(0.0, _len, -hw, hw, 0.0, 2.8, white, CLS_PAINT, true)
	_strip(0.0, _len, -5.0, 5.0, 0.15, 0.3, Color(0.40, 0.38, 0.36), CLS_STONE)
	for tr in [-2.2, 2.2]:
		for r in [-0.72, 0.72]:
			_strip(0.0, _len, tr + r - 0.04, tr + r + 0.04, 0.4, 0.1, Color(0.55, 0.55, 0.55),
				CLS_STEEL, false, 20.0, true)
	for side in [-1.0, 1.0]:
		_railing(0.0, _len, side * (hw - 0.1), Color(0.75, 0.76, 0.76))
	# Halic station on the main span: side platforms under a white canopy
	# of fins.
	var st0 := t0 + 10.0
	var st1 := t1 - 10.0
	for side in [-1.0, 1.0]:
		_strip(st0, st1, side * hw, side * (hw + 5.5), 1.0, 3.8, white, CLS_PAINT, true)
		_railing(st0, st1, side * (hw + 5.4), Color(0.75, 0.76, 0.76))
		_strip(st0, st1, side * (hw + 0.3), side * (hw + 5.0), 3.6, 2.4, Color(0.55, 0.62, 0.66),
			CLS_GLASS, false, 20.0)
	var s := st0
	while s <= st1:
		var y := _py(s)
		for side in [-1.0, 1.0]:
			_post(s, side * (hw + 4.8), y + 1.0, y + 7.5, 0.4, 0.4, white, CLS_PAINT, false, true)
		_block(s, 0.0, y + 7.6, Vector3(1.0, 0.4, 2.0 * hw + 11.0), white, CLS_PAINT, false, true)
		_block(s, 0.0, y + 8.6, Vector3(0.35, 1.8, 2.0 * hw + 9.0), white, CLS_PAINT, false, true)
		_light(s, hw + 3.0, y + 7.0, L_WHITE)
		_light(s, -hw - 3.0, y + 7.0, L_WHITE)
		s += 4.0
	_strip(st0, st1, -hw - 5.5, hw + 5.5, 7.8, 0.3, Color(0.86, 0.87, 0.87), CLS_PAINT, true)
	# Approach piers, and the main bridge's piers over the water.
	_piers(20.0, w.x - 5.0, 40.0, [0.0], 2.8, Vector2(3.0, 5.0))
	_piers(w.y + 5.0, _len - 20.0, 40.0, [0.0], 2.8, Vector2(3.0, 5.0))
	for e in [w.x + 25.0, t0 - 85.0, t1 + 85.0, w.y - 25.0]:
		if e > w.x and e < w.y:
			_post(e, 0.0, -6.0, top - 2.8, 4.0, 8.0, C_CONC, CLS_STONE)
	# The pylons: a white shaft through the deck to 65 m, stays in one plane
	# fanned both ways onto the deck's centre line.
	var ptop := 65.0
	for t in [t0, t1]:
		_post(t, 0.0, -6.0, top - 2.8, 7.0, 2.0 * hw + 1.0, C_CONC, CLS_STONE)
		var nseg := 6
		for k in nseg:
			var f0 := float(k) / nseg
			var f1 := float(k + 1) / nseg
			var y0 := lerpf(top - 2.8, ptop, f0)
			var y1 := lerpf(top - 2.8, ptop, f1)
			var sz := lerpf(5.5, 2.4, (f0 + f1) * 0.5)
			_post(t, 0.0, y0, y1, sz * 0.8, sz, white, CLS_PAINT)
		_obstacle(t - 2.8, t + 2.8, -2.8, 2.8, top - 3.0, ptop)
		_light(t, 0.0, ptop + 0.8, L_RED)
		for dir in [-1.0, 1.0]:
			for k in 9:
				var ya2 := ptop - 3.0 - k * 2.6
				var reach := 102.0 - k * 10.0
				var sd: float = t + dir * reach
				_beam(_p(t + dir * 0.9, 0.0, ya2), _p(sd, 0.0, _py(sd) + 0.5), 0.22,
					Color(0.85, 0.86, 0.86), CLS_STEEL)
				if k % 2 == 0:
					var mp := _p((t + sd) * 0.5, 0.0, (ya2 + _py(sd)) * 0.5)
					_lights.append([_origin + mp, L_WHITE, false])
			# The fan as solid: stepped thin boxes under its outer stay.
			var n := 7
			for i in n:
				var da := 102.0 * float(i) / n
				var db := 102.0 * float(i + 1) / n
				var dm := (da + db) * 0.5
				var yt := lerpf(ptop - 3.0, top, db / 102.0)
				if yt - top > 1.0:
					_boxes.append([Transform3D(Basis(_along(), Vector3.UP, _along().cross(Vector3.UP)),
						_p(t + dir * dm, 0.0, (top + yt) * 0.5)), Vector3(db - da, yt - top, 0.8)])
	_lamps(20.0, _len - 20.0, hw - 0.4, 36.0, 6.0, L_WHITE)
	_record_deck(2.0 * hw, 0.0, _len)
	_commit()


# ------------------------------------------------------------------ 15 July Martyrs

func _martyrs() -> void:
	var a := Vector2(4071.0, 4710.0)       # Ortakoy (Europe)
	var b := Vector2(5033.0, 3489.0)       # Beylerbeyi (Asia)
	_begin("bosphorus", a, b, [])
	var t0 := 230.0                        # side spans 231 / 255 m
	var t1 := t0 + 1074.0
	var mid := (t0 + t1) * 0.5
	var tower_top := 165.0
	var ya := _ground(0.0) + 6.0
	var yb := _ground(_len) + 6.0
	var prof: Array = [Vector2(-10.0, ya), Vector2(0.0, ya)]
	for k in 11:
		var s := lerpf(t0, t1, float(k) / 10.0)
		var x := (s - mid) / (t1 - t0) * 2.0
		prof.append(Vector2(s, 68.0 - 3.5 * x * x))     # 64 m clear under a 3 m girder at the towers
	prof.append(Vector2(_len, yb))
	prof.append(Vector2(_len + 10.0, yb))
	_prof = PackedVector2Array(prof)
	var grey := Color(0.78, 0.79, 0.80)
	var hw := 16.7
	# The deck: an aerodynamic steel box, the road on it.
	_strip(-10.0, _len + 10.0, -hw, hw, 0.0, 3.0, grey, CLS_STEEL, true, 30.0)
	_strip(-10.0, _len + 10.0, -hw + 0.6, -hw + 1.6, -3.0, 0.6, grey.darkened(0.15), CLS_STEEL, false, 30.0)
	_strip(-10.0, _len + 10.0, hw - 1.6, hw - 0.6, -3.0, 0.6, grey.darkened(0.15), CLS_STEEL, false, 30.0)
	_strip(-10.0, _len + 10.0, -14.5, 14.5, 0.06, 0.1, C_ROAD, CLS_ASPHALT, false, 30.0)
	_strip(-10.0, _len + 10.0, -0.4, 0.4, 0.9, 0.85, Color(0.7, 0.7, 0.68), CLS_STONE, false, 30.0, true)
	for side in [-1.0, 1.0]:
		_railing(-10.0, _len + 10.0, side * (hw - 0.2), C_RAIL)
	var cl := 18.5           # cable planes
	# Towers: two tapering steel legs, three portal beams.
	for t in [t0, t1]:
		var g := _ground(t, 0.0)
		var base := minf(g, _ground(t, -20.0))
		base = minf(base, _ground(t, 20.0))
		var nseg := 10
		for side in [-1.0, 1.0]:
			for k in nseg:
				var f0 := float(k) / nseg
				var f1 := float(k + 1) / nseg
				var y0 := lerpf(base, tower_top, f0)
				var y1 := lerpf(base, tower_top, f1)
				var fm := (f0 + f1) * 0.5
				var lat: float = side * lerpf(21.0, cl, fm)
				_post(t, lat, y0, y1, lerpf(7.0, 5.0, fm), lerpf(5.0, 4.0, fm), grey, CLS_STEEL)
			_obstacle(t - 3.5, t + 3.5, side * 23.5, side * 16.5, base, tower_top)
			_light(t, side * cl, tower_top + 1.0, L_RED)
			# The LED show runs up the legs too.
			var yy := _py(t) + 12.0
			while yy < tower_top - 4.0:
				_light(t, side * (lerpf(21.0, cl, (yy - base) / (tower_top - base)) - side * 2.6), yy,
					Color(0.35, 0.55, 1.0), true)
				yy += 14.0
		for yb2: float in [_py(t) - 6.0, 112.0, tower_top - 3.0]:
			var f := (yb2 - base) / (tower_top - base)
			var inner := lerpf(21.0, cl, f) - 2.4
			_block(t, 0.0, yb2, Vector3(5.0, 6.0, 2.0 * inner), grey, CLS_STEEL)
		# Plinths at the waterline.
		_post(t, 0.0, base - 1.0, base + 4.0, 14.0, 52.0, C_CONC, CLS_STONE)
	# Anchorages behind the side spans.
	var anchors := [-6.0, _len + 6.0]
	var cy_a := []
	for an: float in anchors:
		var g := _ground(an, 0.0)
		_block(an, 0.0, g + 9.0, Vector3(34.0, 22.0, 46.0), C_CONC, CLS_STONE)
		cy_a.append(g + 16.0)
	# Main cables: parabola over the main span, a sag down to each anchorage.
	var low := _py(mid) + 3.0
	for side in [-1.0, 1.0]:
		var lat: float = side * cl
		var pts := PackedVector2Array()
		var s := anchors[0] as float
		while s < anchors[1] + 0.1:
			pts.append(Vector2(s, _cable_y(s, anchors[0], t0, t1, anchors[1], tower_top - 1.5, low,
				cy_a[0], cy_a[1])))
			s += 18.0 if (s > t0 and s < t1) else 15.0
		pts.append(Vector2(anchors[1], cy_a[1]))
		for i in range(pts.size() - 1):
			_beam(_p(pts[i].x, lat, pts[i].y), _p(pts[i + 1].x, lat, pts[i + 1].y), 0.9,
				grey.darkened(0.1), CLS_STEEL, false)
		# Necklace: a light every 18 m along the cable, the LED show.
		var q := anchors[0] as float + 9.0
		while q < anchors[1] - 4.0:
			if absf(q - t0) > 6.0 and absf(q - t1) > 6.0:
				var cy := _cable_y(q, anchors[0], t0, t1, anchors[1], tower_top - 1.5, low, cy_a[0], cy_a[1])
				_light(q, lat, cy + 0.9, Color(0.35, 0.55, 1.0), true)
			q += 18.0
		# Inclined hangers, main span only, in a zig-zag: each pair meets at
		# the deck.
		var j := 0
		var sc := t0 + 18.0
		while sc < t1 - 9.0:
			var sd: float = sc + (9.0 if j % 2 == 0 else -9.0)
			var cy := _cable_y(sc, anchors[0], t0, t1, anchors[1], tower_top - 1.5, low, cy_a[0], cy_a[1])
			if cy - _py(sd) > 1.5:
				_beam(_p(sc, lat, cy), _p(sd, side * (hw - 0.2), _py(sd)), 0.18, grey.darkened(0.2), CLS_STEEL)
			sc += 18.0
			j += 1
		# The cable curtain as solid boxes, deck to cable, stepped.
		var n := int(ceil((t1 - t0) / 40.0))
		for i in n:
			var sa := lerpf(t0, t1, float(i) / n)
			var sb := lerpf(t0, t1, float(i + 1) / n)
			var sm := (sa + sb) * 0.5
			var dk := _py(sm)
			var cy := minf(_cable_y(sa, anchors[0], t0, t1, anchors[1], tower_top, low, cy_a[0], cy_a[1]),
				_cable_y(sb, anchors[0], t0, t1, anchors[1], tower_top, low, cy_a[0], cy_a[1]))
			if cy - dk > 2.0:
				_boxes.append([Transform3D(Basis(_along(), Vector3.UP, _along().cross(Vector3.UP)),
					_p(sm, lat, (dk + cy) * 0.5)), Vector3(sb - sa, cy - dk, 1.0)])
	# Side spans on piers; the main span hangs.
	_piers(20.0, t0 - 25.0, 45.0, [-9.0, 9.0], 3.0, Vector2(3.0, 3.0))
	_piers(t1 + 25.0, _len - 15.0, 45.0, [-9.0, 9.0], 3.0, Vector2(3.0, 3.0))
	# Deck lamps down the median.
	var s2 := 0.0
	while s2 < _len:
		_post(s2, 0.0, _py(s2) + 0.9, _py(s2) + 9.0, 0.25, 0.25, C_LAMP, CLS_STEEL, false, true)
		_light(s2, 1.2, _py(s2) + 9.0, L_WARM)
		_light(s2, -1.2, _py(s2) + 9.0, L_WARM)
		s2 += 40.0
	_record_deck(2.0 * hw, -10.0, _len + 10.0)
	_commit()


## Cable height at s: a parabola between the towers, a sagging run from
## each tower top down to its anchorage (bridges.gd's curve).
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


# ------------------------------------------------------------------ Halic

func _halic() -> void:
	var a := Vector2(-3484.0, 3484.0)      # Ayvansaray (south-west)
	var b := Vector2(-2944.0, 4315.0)      # Halicioglu (north-east)
	_begin("halic", a, b, [])
	var w := _water_span()
	var top := 22.0
	var ya := _ground(0.0) + 0.5
	var yb := _ground(_len) + 0.5
	_prof = PackedVector2Array([Vector2(0.0, ya), Vector2(w.x, top), Vector2(w.y, top), Vector2(_len, yb)])
	var hw := 16.0
	var conc := Color(0.70, 0.69, 0.66)
	_strip(0.0, _len, -hw, hw, 0.0, 3.0, conc, CLS_STONE, true)
	_strip(0.0, _len, -15.0, -1.0, 0.06, 0.1, C_ROAD, CLS_ASPHALT)
	_strip(0.0, _len, 1.0, 15.0, 0.06, 0.1, C_ROAD, CLS_ASPHALT)
	_strip(0.0, _len, -0.4, 0.4, 0.9, 0.85, Color(0.72, 0.72, 0.7), CLS_STONE, false, 20.0, true)
	for side in [-1.0, 1.0]:
		_railing(0.0, _len, side * (hw - 0.1), C_RAIL)
		_lamps(10.0, _len - 10.0, side * (hw - 0.5), 40.0, 10.0)
	_piers(30.0, _len - 30.0, 55.0, [-8.0, 8.0], 3.0, Vector2(3.0, 4.0))
	_record_deck(2.0 * hw, 0.0, _len)
	_commit()


# ------------------------------------------------------------------ lights

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
	mm.use_custom_data = true
	mm.mesh = quad
	mm.instance_count = _lights.size()
	for i in _lights.size():
		var l: Array = _lights[i]
		mm.set_instance_transform(i, Transform3D(Basis.IDENTITY, l[0]))
		mm.set_instance_color(i, l[1])
		# Custom: r = 1 for the LED show's colour cycle, g = its phase.
		var p: Vector3 = l[0]
		mm.set_instance_custom_data(i, Color(1.0 if bool(l[2]) else 0.0, fposmod(p.x * 0.0007 - p.z * 0.0005, 1.0), 0, 0))
	_light_mmi = MultiMeshInstance3D.new()
	_light_mmi.name = "bridge_lights"
	_light_mmi.multimesh = mm
	_light_mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	_light_mmi.visible = _own_lights
	add_child(_light_mmi)


const LIGHT_SHADER := """
shader_type spatial;
render_mode unshaded, blend_add, depth_draw_never, cull_disabled, shadows_disabled, fog_disabled;
uniform float night = 0.0;
varying vec4 v_col;
varying vec4 v_cust;
vec3 hue(float h) {
    return clamp(abs(fract(h + vec3(0.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0) - 1.0, 0.0, 1.0);
}
void vertex() {
    v_col = COLOR;
    v_cust = INSTANCE_CUSTOM;
    vec3 c = (VIEW_MATRIX * vec4(MODEL_MATRIX[3].xyz, 1.0)).xyz;
    float d = length(c);
    // ~3.5 px at any range, never under 1.0 m.
    float size = max(1.0, d * 0.004);
    MODELVIEW_MATRIX = VIEW_MATRIX * mat4(INV_VIEW_MATRIX[0], INV_VIEW_MATRIX[1],
        INV_VIEW_MATRIX[2], MODEL_MATRIX[3]);
    VERTEX *= size;
}
void fragment() {
    if (night < 0.02) { discard; }
    float r = length(UV - 0.5) * 2.0;
    float a = clamp(1.0 - r, 0.0, 1.0);
    a = a * a;
    vec3 c = v_col.rgb;
    if (v_cust.r > 0.5) {
        // The Bosphorus bridge's LED show: a slow wave of colour along it.
        c = mix(hue(TIME * 0.02 + v_cust.g), vec3(1.0), 0.25);
    }
    ALBEDO = c * a * night * 3.0;
}
"""

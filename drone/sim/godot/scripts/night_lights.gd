## The city's own lights at night, for the photoreal Manhattan scene
## (env_manhattan_photo): street lamps along the mapped streets, red aircraft-
## obstruction lights on the tall buildings, and a hook for other scenery
## (bridges' cable and tower lights) to add its own.
##
## Driven by env.night_amount() (world_clock.gd's: 0 by day, 1 once the sun is
## 4 deg down). By day all of it is hidden and no light is on, so the day scene
## is exactly as before. Lit windows are the buildings' own shader, not here.
##
## STREET LAMPS (roads.json, NYC's street centreline). New York finished
## converting its ~250,000 street lights to LED in 2019: cobra-heads on
## avenues and highways (4000 K, both sides), warmer 3000 K on the side
## streets after residents complained about the first blue-white ones, one
## side and staggered. Spacing ~30-45 m by class. No lamps on bridges (the far
## shores' script owns those decks), tunnels, below-grade cuts or elevated
## roadways (deck height is not in the data; a lamp at street level under the
## FDR viaduct would be wrong), and none inside a building footprint.
##
## OBSTRUCTION LIGHTS (FAA AC 70/7460-1, red system): every building >= 60 m
## (~200 ft) gets L-864 red flashers on its roof corners, 30 flashes a minute,
## in step within a building (the AC asks for that) and out of step between
## buildings; >= 107 m (350 ft) adds steady L-810s half way up; >= 152 m
## (500 ft) adds a flashing level half way up and steady ones at 1/4 and 3/4.
##
## COST. Emissive geometry in MultiMeshes, one set per 500 m chunk with
## visibility ranges, drawn only at night:
##   lamp_poles / lamp_heads   real geometry, near only (450 / 900 m)
##   lamp_pools                light on the street: a box volume per lamp that
##                             lights whatever surface the depth buffer has
##                             under it (street, car roof, storefront), so the
##                             pools sit on the ground whatever height the
##                             ground scripts draw it at. 2.2 km.
##   lamp_glare                the lamp itself as a camera-facing sprite that
##                             never shrinks below ~2 px: lines of lights to
##                             the far end of the island. 1 km chunks, 14 km.
## Real lights: LAMP_LIGHTS OmniLights (no shadows), moved every LIGHT_PICK_S
## to the lamps nearest the active cameras, for the light a lamp throws on
## walls. --night-lights=off leaves all of it out.
##
## THE HOOK, for bridges and anything else with lights of its own:
##   var nl = env.get_node_or_null("night_lights")
##   nl.add_lights("bridge_cable_lights", points, Color(...), 0.6, 0)
##     points: Array or PackedVector3Array of Godot positions; size_m: the
##     bulb's apparent size near; blink: 0 steady, 1 L-864 red flash, 2 slow
##     beacon. Returns the MultiMeshInstance3D (shown at night, hidden by day).
##   nl.add_pools("bridge_deck_pools", feet, Color(...), radius_m)
##     light on the surface around each foot (Godot positions).
## Name them "*_lights"/"lamp_*" so thermal_kit.gd treats them as lamps.
##
## Frame: sim metres; data is in the dataset frame and goes through env.to_sim().
extends Node3D

const CHUNK := 500.0
const GLARE_CHUNK := 1000.0
const LAMP_LIGHTS := 16
const LIGHT_PICK_S := 0.25
const LIGHT_RANGE_M := 320.0
const OBSTRUCTION_MIN_H := 60.0
## Colour temperatures as linear RGB (approximate blackbody, white-balanced
## to daylight so they read warm against the day-tuned tonemap).
const LED_4000K := Color(1.0, 0.86, 0.70)
const LED_3000K := Color(1.0, 0.72, 0.46)
const RED := Color(1.0, 0.06, 0.03)
## By road class: [spacing m, head height m, colour, both sides].
const ROAD_LAMPS := {
	"motorway": [45.0, 12.0, LED_4000K, true],
	"motorway_link": [40.0, 10.0, LED_4000K, false],
	"primary": [36.0, 9.5, LED_4000K, true],
	"secondary": [36.0, 9.0, LED_4000K, true],
	"street": [30.0, 8.0, LED_3000K, false],
}
const SKIP_FLAGS := 1 | 2 | 4 | 8   # bridge, tunnel, elevated, below grade

var env: Node3D
var _night := -1.0
var _root: Node3D
var _mats: Array[ShaderMaterial] = []
var _cells := {}                # Vector2i (100 m) -> Array of [Vector3 head, Color]
var _pool: Array[OmniLight3D] = []
var _pick_t := 0.0
var _cams: Array[Camera3D] = []
var _cams_t := 0.0
var counts := {}


func _init() -> void:
	name = "night_lights"


func _ready() -> void:
	if "--night-lights=off" in OS.get_cmdline_user_args():
		set_process(false)
		return
	env = get_parent()
	_root = Node3D.new()
	_root.name = "lit"
	_root.visible = false
	add_child(_root)
	_build.call_deferred()


func _night_amount() -> float:
	if env.has_method("night_amount"):
		return float(env.call("night_amount"))
	return 0.0


func _build() -> void:
	var t0 := Time.get_ticks_msec()
	_build_street_lamps()
	_build_obstruction_lights()
	for i in LAMP_LIGHTS:
		var l := OmniLight3D.new()
		l.name = "lamp_light_%d" % i
		l.omni_range = 26.0
		l.omni_attenuation = 1.3
		l.light_energy = 2.2
		l.shadow_enabled = false
		l.visible = false
		l.light_bake_mode = Light3D.BAKE_DISABLED
		l.set_meta("e0", 2.2)
		_root.add_child(l)
		_pool.append(l)
	print("[night_lights] %s (%d ms)" % [counts, Time.get_ticks_msec() - t0])


# ------------------------------------------------------------------ the hook
func add_lights(draw_name: String, points, col: Color, size_m: float = 0.6, blink: int = 0,
		phases: Array = []) -> MultiMeshInstance3D:
	var xfs: Array = []
	var cust: Array = []
	var i := 0
	for p: Vector3 in points:
		xfs.append(Transform3D(Basis(), p))
		cust.append(Color(col.r, col.g, col.b, float(phases[i]) if i < phases.size() else 0.0))
		i += 1
	return _draw(draw_name, _quad(), xfs, cust, _glare_material(size_m, 5.0, blink))


func add_pools(draw_name: String, feet, col: Color, radius_m: float = 14.0) -> MultiMeshInstance3D:
	var xfs: Array = []
	var cust: Array = []
	for p: Vector3 in feet:
		xfs.append(_pool_xf(p, radius_m))
		cust.append(Color(col.r, col.g, col.b, 0.0))
	return _draw(draw_name, _pool_box(), xfs, cust, _pool_material(0.5))


# ------------------------------------------------------------------ materials
## Glare: the lamp as a sprite facing the camera, in view space, never smaller
## than MIN_PX pixels. A lamp 8 km away is far below a pixel; drawn true size
## it would flicker in and out between frames, which is worse than drawing it
## a little large. Past its true size it dims, so a far lamp is a point and
## not a blob, but with a floor so the lines of streets still read.
func _glare_material(size_m: float, gain: float, blink: int = 0) -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode unshaded, blend_add, depth_draw_never, cull_disabled, shadows_disabled, fog_disabled, skip_vertex_transform;
uniform float night = 0.0;
uniform float gain = 5.0;
uniform float size_m = 0.6;
uniform float min_px = 3.2;
uniform int blink = 0;
varying vec3 v_col;
varying float v_k;
void vertex() {
    v_col = INSTANCE_CUSTOM.rgb;
    vec4 c = MODELVIEW_MATRIX * vec4(0.0, 0.0, 0.0, 1.0);
    float px = max(-c.z, 0.1) * 2.0 / (PROJECTION_MATRIX[1][1] * VIEWPORT_SIZE.y);
    float sz = max(size_m, px * min_px);
    v_k = mix(0.45, 1.0, clamp(size_m / sz, 0.0, 1.0));
    float on = 1.0;
    float f = fract(TIME * 0.5 + INSTANCE_CUSTOM.a);
    // L-864: 30 flashes a minute, about a third of the cycle on.
    if (blink == 1) { on = smoothstep(0.0, 0.04, f) * (1.0 - smoothstep(0.30, 0.38, f)); }
    if (blink == 2) { on = 0.25 + 0.75 * smoothstep(0.0, 0.1, f) * (1.0 - smoothstep(0.4, 0.55, f)); }
    v_k *= on;
    // Pull the sprite a little toward the camera so it is not swallowed by
    // the pole or housing it sits in.
    vec3 toward = -normalize(c.xyz) * min(sz, 2.0);
    VERTEX = c.xyz + toward + vec3(VERTEX.xy * sz, 0.0);
}
void fragment() {
    float r = length(UV * 2.0 - 1.0);
    float core = exp(-r * r * 12.0);
    float halo = exp(-r * r * 3.0) * 0.18;
    ALBEDO = v_col * gain * night * v_k * (core + halo);
}
"""
	var m := ShaderMaterial.new()
	m.shader = sh
	m.set_shader_parameter("gain", gain)
	m.set_shader_parameter("size_m", size_m)
	m.set_shader_parameter("blink", blink)
	_mats.append(m)
	return m


## Light on whatever surface lies within a lamp's box: the depth buffer gives
## each pixel's world position, and it gets the lamp's light by its distance
## from the lamp's foot. No depth test (the box's back faces are under the
## street from above); a pixel the box covers but the lamp cannot reach (a
## roof, a wall far above) is simply too far from the foot to light.
func _pool_material(strength: float) -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode unshaded, blend_add, depth_draw_never, depth_test_disabled, cull_front, shadows_disabled, fog_disabled;
uniform sampler2D depth_tex : hint_depth_texture, filter_nearest;
uniform float night = 0.0;
uniform float strength = 0.5;
varying vec3 v_foot;
varying float v_r;
varying vec3 v_col;
void vertex() {
    v_foot = (MODEL_MATRIX * vec4(0.0, -0.5, 0.0, 1.0)).xyz + vec3(0.0, 3.0, 0.0);
    v_r = length(MODEL_MATRIX[0].xyz) * 0.5;
    v_col = INSTANCE_CUSTOM.rgb;
}
void fragment() {
    float depth = texture(depth_tex, SCREEN_UV).r;
    vec4 view = INV_PROJECTION_MATRIX * vec4(SCREEN_UV * 2.0 - 1.0, depth, 1.0);
    view.xyz /= view.w;
    vec3 p = (INV_VIEW_MATRIX * vec4(view.xyz, 1.0)).xyz;
    vec3 q = p - v_foot;
    float rr = length(q.xz) / v_r;
    float a = exp(-rr * rr * 3.2) * (1.0 - smoothstep(0.75, 1.0, rr)) * (1.0 - smoothstep(1.5, 5.5, abs(q.y)));
    ALBEDO = v_col * strength * night * a;
}
"""
	var m := ShaderMaterial.new()
	m.shader = sh
	m.set_shader_parameter("strength", strength)
	_mats.append(m)
	return m


## Cobra-head housing and arm: grey by day, the lens on its underside lit at
## night. Vertex colour alpha marks the lens.
func _head_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode shadows_disabled;
uniform float night = 0.0;
varying vec3 v_col;
varying float v_lens;
void vertex() {
    v_col = INSTANCE_CUSTOM.rgb;
    v_lens = COLOR.a;
}
void fragment() {
    ALBEDO = mix(vec3(0.36, 0.37, 0.38), vec3(0.8), v_lens);
    ROUGHNESS = 0.5;
    METALLIC = 0.4 * (1.0 - v_lens);
    EMISSION = v_col * 7.0 * night * v_lens;
}
"""
	var m := ShaderMaterial.new()
	m.shader = sh
	_mats.append(m)
	return m


func _pole_material() -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = Color(0.30, 0.31, 0.32)
	m.roughness = 0.6
	m.metallic = 0.5
	return m


# ------------------------------------------------------------------ meshes
func _quad() -> QuadMesh:
	var q := QuadMesh.new()
	q.size = Vector2(1.0, 1.0)
	# Sprites are moved in the vertex shader; the AABB must cover where they go.
	q.custom_aabb = AABB(Vector3(-4, -4, -4), Vector3(8, 8, 8))
	return q


func _pool_box() -> BoxMesh:
	var b := BoxMesh.new()
	b.size = Vector3.ONE
	return b


func _pool_xf(foot: Vector3, r: float) -> Transform3D:
	# Box 2r wide, 9 m tall, from 3 m under the foot to 6 m over it.
	return Transform3D(Basis().scaled(Vector3(2.0 * r, 9.0, 2.0 * r)), foot + Vector3(0.0, 1.5, 0.0))


## Arm (2.2 m, rising slightly) and the cobra-head at its end, along +Z from
## the pole's top at the origin.
func _head_mesh() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	_box(st, Vector3(0, 0.05, 1.1), Vector3(0.08, 0.08, 2.2), 0.0)
	_box(st, Vector3(0, 0.02, 2.45), Vector3(0.38, 0.16, 0.85), 0.0)
	_box(st, Vector3(0, -0.07, 2.45), Vector3(0.30, 0.02, 0.70), 1.0)   # the lens
	st.generate_normals()
	return st.commit()


static func _box(st: SurfaceTool, c: Vector3, s: Vector3, lens: float) -> void:
	var h := s * 0.5
	var col := Color(1, 1, 1, lens)
	var faces := [
		[Vector3(1, 0, 0), Vector3(0, 1, 0), Vector3(0, 0, 1)],
		[Vector3(-1, 0, 0), Vector3(0, 0, 1), Vector3(0, 1, 0)],
		[Vector3(0, 1, 0), Vector3(0, 0, 1), Vector3(1, 0, 0)],
		[Vector3(0, -1, 0), Vector3(1, 0, 0), Vector3(0, 0, 1)],
		[Vector3(0, 0, 1), Vector3(1, 0, 0), Vector3(0, 1, 0)],
		[Vector3(0, 0, -1), Vector3(0, 1, 0), Vector3(1, 0, 0)],
	]
	for f: Array in faces:
		var n: Vector3 = f[0]
		var u: Vector3 = f[1]
		var v: Vector3 = f[2]
		var o := c + n * h
		var du := u * h
		var dv := v * h
		var p0 := o - du - dv
		var p1 := o + du - dv
		var p2 := o + du + dv
		var p3 := o - du + dv
		for p: Vector3 in [p0, p2, p1, p0, p3, p2]:
			st.set_color(col)
			st.add_vertex(p)


# ------------------------------------------------------------------ drawing
## One MultiMesh draw of `mesh`: transforms (world) and per-instance custom
## data (rgb colour, a phase). The node sits at its instances' centre and
## holds them relative to it: a visibility range is measured to the node, and
## at the world origin every chunk would be "near" a camera at the origin and
## "far" from one anywhere else, whatever it contained.
func _draw(draw_name: String, mesh: Mesh, xfs: Array, cust: Array, mat: Material,
		vis_end := 0.0, parent: Node3D = null) -> MultiMeshInstance3D:
	if xfs.is_empty():
		return null
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_custom_data = true
	mm.mesh = mesh
	mm.instance_count = xfs.size()
	var centre := Vector3.ZERO
	for xf: Transform3D in xfs:
		centre += xf.origin
	centre /= float(xfs.size())
	centre.y = 0.0
	for i in xfs.size():
		var xf: Transform3D = xfs[i]
		mm.set_instance_transform(i, Transform3D(xf.basis, xf.origin - centre))
		mm.set_instance_custom_data(i, cust[i] if i < cust.size() else Color.WHITE)
	var mmi := MultiMeshInstance3D.new()
	mmi.name = draw_name
	mmi.multimesh = mm
	mmi.material_override = mat
	mmi.position = centre
	mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	if vis_end > 0.0:
		mmi.visibility_range_end = vis_end
		mmi.visibility_range_end_margin = 100.0
		mmi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_DISABLED
	(parent if parent != null else _root).add_child(mmi)
	return mmi


# ------------------------------------------------------------------ streets
func _build_street_lamps() -> void:
	var f := FileAccess.open("res://assets/manhattan/roads.json", FileAccess.READ)
	if f == null:
		return
	var doc: Dictionary = JSON.parse_string(f.get_as_text())
	f.close()
	var nodes: Array = doc.get("nodes", [])
	# chunk key -> [poles, heads, pools, cust]; glare chunk key -> [xfs, cust]
	var ch := {}
	var gch := {}
	var seen := {}
	var n_lamps := 0
	for e: Array in doc.get("edges", []):
		var cls := str(e[2])
		if not ROAD_LAMPS.has(cls) or (int(e[8]) & SKIP_FLAGS) != 0:
			continue
		var a := int(e[0])
		var b := int(e[1])
		var key := Vector2i(mini(a, b), maxi(a, b))
		if seen.has(key):
			continue
		seen[key] = true
		var spec: Array = ROAD_LAMPS[cls]
		var pa: Vector2 = env.to_sim(Vector2(float(nodes[a][0]), float(nodes[a][1])))
		var pb: Vector2 = env.to_sim(Vector2(float(nodes[b][0]), float(nodes[b][1])))
		var L := pa.distance_to(pb)
		if L < 6.0:
			continue
		var dir := (pb - pa) / L
		var nrm := Vector2(-dir.y, dir.x)
		var width := float(e[5])
		var off := width * 0.5 + 0.7 if width > 2.0 else maxi(1, int(e[4])) * 1.7 + 1.6
		var spacing: float = spec[0]
		var h: float = spec[1]
		var col: Color = spec[2]
		var both: bool = spec[3]
		# Stagger by a hash of the edge, so lamps on neighbouring blocks don't line up.
		var s := fposmod(float(hash(key)) * 0.618, spacing) * 0.8 + spacing * 0.1
		var k := hash(key) & 1
		while s < L - 2.0:
			var sides := [1.0, -1.0] if both else [1.0 if k % 2 == 0 else -1.0]
			for sd: float in sides:
				# Both sides: the far side half a span on, staggered, as most
				# avenues are; opposite pairs paint the street in bars.
				var sa := s + (spacing * 0.5 if both and sd < 0.0 else 0.0)
				if sa >= L - 2.0:
					continue
				var p := pa + dir * sa + nrm * off * sd
				if _in_building(p):
					p = pa + dir * sa + nrm * (off * 0.7) * sd    # a narrow sidewalk
				if env.is_water(p.x, p.y) or _in_building(p):
					continue
				var g: float = env.ground_height(p.x, p.y)
				var to_road := -nrm * sd
				var yaw := atan2(to_road.x, to_road.y)
				var basis := Basis(Vector3.UP, PI - yaw)     # +Z (the arm) toward the road
				var ck := Vector2i(int(floor(p.x / CHUNK)), int(floor(p.y / CHUNK)))
				if not ch.has(ck):
					ch[ck] = [[], [], [], []]
				var c: Array = ch[ck]
				var top := Vector3(p.x, g + h, -p.y)
				(c[0] as Array).append(Transform3D(Basis().scaled(Vector3(1.0, h, 1.0)), Vector3(p.x, g + h * 0.5, -p.y)))
				(c[1] as Array).append(Transform3D(basis, top))
				var lens: Vector3 = Transform3D(basis, top) * Vector3(0, -0.12, 2.45)
				var foot := Vector3(lens.x, g, lens.z)
				(c[2] as Array).append(_pool_xf(foot, h * 2.3))
				(c[3] as Array).append(Color(col.r, col.g, col.b, 0.0))
				var gk := Vector2i(int(floor(p.x / GLARE_CHUNK)), int(floor(p.y / GLARE_CHUNK)))
				if not gch.has(gk):
					gch[gk] = [[], []]
				(gch[gk][0] as Array).append(Transform3D(Basis(), lens))
				(gch[gk][1] as Array).append(Color(col.r, col.g, col.b, 0.0))
				var cell := Vector2i(int(floor(p.x / 100.0)), int(floor(p.y / 100.0)))
				if not _cells.has(cell):
					_cells[cell] = []
				(_cells[cell] as Array).append([lens + Vector3(0, -0.6, 0), col])
				n_lamps += 1
				counts[cls] = int(counts.get(cls, 0)) + 1
			s += spacing
			k += 1
	var pole := CylinderMesh.new()
	pole.top_radius = 0.07
	pole.bottom_radius = 0.12
	pole.height = 1.0
	pole.radial_segments = 6
	pole.rings = 1
	var head := _head_mesh()
	var box := _pool_box()
	var quad := _quad()
	var pole_mat := _pole_material()
	var head_mat := _head_material()
	var pool_mat := _pool_material(float(IpcServer._get_launch_arg("--night-pool-gain", "0.02")))
	var glare_mat := _glare_material(0.8, 14.0)
	var half_diag := CHUNK * 0.7071
	for ck: Vector2i in ch:
		var c: Array = ch[ck]
		var holder := Node3D.new()
		holder.name = "lamps_%d_%d" % [ck.x, ck.y]
		_root.add_child(holder)
		var poles: Array = c[0]
		var empty: Array = []
		_draw("lamp_poles", pole, poles, empty, pole_mat, 450.0 + half_diag, holder)
		_draw("lamp_heads", head, c[1], c[3], head_mat, 900.0 + half_diag, holder)
		_draw("lamp_pools", box, c[2], c[3], pool_mat, 2200.0 + half_diag, holder)
	for gk: Vector2i in gch:
		# No range: 31k sprites are 62k triangles in all, and the lines of lights
		# to the far end of the island are the point. Chunked for frustum culling.
		_draw("lamp_glare_%d_%d" % [gk.x, gk.y], quad, gch[gk][0], gch[gk][1], glare_mat)
	counts["street_lamps"] = n_lamps
	counts["lamp_chunks"] = ch.size()


## Whether a sim point stands inside (or within 0.5 m of) a building, from
## the env's own 100 m tile index of footprints.
func _in_building(p: Vector2) -> bool:
	var idx: Dictionary = env.get("_index")
	var rects: Array = env.get("_rects")
	var tile := float(env.get("TILE"))
	var key := Vector2i(int(floor(p.x / tile)), int(floor(p.y / tile)))
	if not idx.has(key):
		return false
	for i: int in idx[key]:
		var r: Rect2 = rects[i]
		if r.grow(0.5).has_point(p):
			return true
	return false


# ------------------------------------------------------------------ obstruction
func _build_obstruction_lights() -> void:
	var rects: Array = env.get("_rects")
	var heights: PackedFloat32Array = env.get("_heights")
	var flash: Array = []
	var flash_c: Array = []
	var steady: Array = []
	var steady_c: Array = []
	var n_bld := 0
	for i in rects.size():
		var h: float = heights[i]
		if h < OBSTRUCTION_MIN_H:
			continue
		n_bld += 1
		var r: Rect2 = rects[i]
		# One phase per building: its flashers fire together.
		var phase := fposmod(sin(r.position.x * 0.0131 + r.position.y * 0.0071) * 4373.17, 1.0)
		var corners := _corners(r)
		var rc := Color(RED.r, RED.g, RED.b, phase)
		for q: Vector2 in corners:
			flash.append(Transform3D(Basis(), Vector3(q.x, h + 0.6, -q.y)))
			flash_c.append(rc)
		var levels_flash: Array = []
		var levels_steady: Array = []
		if h >= 152.0:
			levels_flash = [0.5]
			levels_steady = [0.25, 0.75]
		elif h >= 107.0:
			levels_steady = [0.5]
		for fr: float in levels_flash:
			for q: Vector2 in corners:
				flash.append(Transform3D(Basis(), Vector3(q.x, h * fr, -q.y)))
				flash_c.append(rc)
		for fr: float in levels_steady:
			for q: Vector2 in corners:
				steady.append(Transform3D(Basis(), Vector3(q.x, h * fr, -q.y)))
				steady_c.append(Color(RED.r, RED.g, RED.b, 0.0))
	var quad := _quad()
	_draw("obstruction_lights_flash", quad, flash, flash_c, _glare_material(1.1, 7.0, 1))
	_draw("obstruction_lights_steady", quad, steady, steady_c, _glare_material(0.8, 4.0, 0))
	counts["obstruction_buildings"] = n_bld
	counts["obstruction_lights"] = flash.size() + steady.size()


## Roof corners, a metre in, for the lights; two opposite ones on a narrow roof.
func _corners(r: Rect2) -> Array:
	var a := r.grow(-1.0) if r.size.x > 4.0 and r.size.y > 4.0 else r
	var c := [a.position, a.position + a.size,
			  Vector2(a.position.x + a.size.x, a.position.y), Vector2(a.position.x, a.position.y + a.size.y)]
	if minf(r.size.x, r.size.y) < 25.0:
		return [c[0], c[1]]
	return c


# ------------------------------------------------------------------ per frame
func _process(delta: float) -> void:
	var n := clampf(_night_amount(), 0.0, 1.0)
	if absf(n - _night) > 0.001:
		_night = n
		var on := n > 0.05
		_root.visible = on
		for m in _mats:
			m.set_shader_parameter("night", n)
		if not on:
			for l in _pool:
				l.visible = false
	if _night <= 0.05:
		return
	_pick_t -= delta
	if _pick_t > 0.0:
		return
	_pick_t = LIGHT_PICK_S
	_cams_t -= LIGHT_PICK_S
	if _cams_t <= 0.0 or _cams.is_empty():
		_cams_t = 2.0
		_cams.clear()
		for c in get_tree().root.find_children("*", "Camera3D", true, false):
			var cam := c as Camera3D
			if cam.current and cam.is_visible_in_tree() and (cam.cull_mask & (1 << 19)) != 0:
				_cams.append(cam)
	_assign()


## Put the pool's lights on the lamps nearest the active cameras, shared out
## between them; a camera high above the street gets none (its lamps are
## sprites and pools at that range).
func _assign() -> void:
	var best: Array = []
	var md2 := LIGHT_RANGE_M * LIGHT_RANGE_M
	var per := maxi(1, LAMP_LIGHTS / maxi(1, _cams.size()))
	for cam in _cams:
		if not is_instance_valid(cam):
			continue
		var eye := cam.global_position
		var mine: Array = []
		var c0 := Vector2i(int(floor(eye.x / 100.0)), int(floor(-eye.z / 100.0)))
		for dx in range(-3, 4):
			for dy in range(-3, 4):
				for s: Array in _cells.get(c0 + Vector2i(dx, dy), []):
					var d2 := (s[0] as Vector3).distance_squared_to(eye)
					if d2 < md2:
						mine.append([d2, s])
		mine.sort_custom(func(a, b): return a[0] < b[0])
		for i in mini(per, mine.size()):
			best.append(mine[i][1])
	for i in _pool.size():
		var l := _pool[i]
		if i < best.size():
			l.global_position = best[i][0]
			l.light_color = best[i][1]
			l.light_energy = float(l.get_meta("e0")) * _night
			l.visible = true
		else:
			l.visible = false

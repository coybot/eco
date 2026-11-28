## The world past Baylands' detailed 5 x 4.8 km, out to ~64 km: the horizon
## a drone sees. Without it every view past the region's edge ended in a blue
## void, and the horizon is a cue an attitude estimator (and a pilot) leans on.
##
## WHAT IS WHERE (all measured; tools/fetch_baylands_far.py):
##   * terrain: USGS 3DEP 1 arc-second DEM (public domain), two grids in the
##     TRUE local tangent plane of the datum (real distance and bearing from
##     the park): +-16 km at 32 m, +-64 km at 128 m. The Santa Cruz Mountains
##     to the SW (Black Mountain, Loma Prieta), the Diablo Range E and SE
##     (Mission Peak, Monument Peak, Mt Hamilton with Lick Observatory), the
##     East Bay hills north, the bay and the Santa Clara Valley flats.
##   * colour: Sentinel-2 L2A medians (dry Jul-Sep, green Feb-Mar), mixed by
##     env.look.season like the region's own grass; fitted to the region's
##     NAIP colours so the edge has no colour step. "Contains modified
##     Copernicus Sentinel data 2023-2025".
##   * water: Sentinel-2's scene classification: the bay, the sloughs and the
##     salt ponds (in their real colours) at env.look.tide, with the sky's
##     reflection and the sun's glint from the PBR specular.
##   * skyline (baylands_far_city.gd, when its data exists): OSM buildings
##     past the region, the San Jose towers, the Dumbarton and San Mateo
##     bridges, the Lick Observatory domes.
##
## EARTH CURVATURE is applied in the vertex shader, per camera: each vertex
## drops by d^2 (1 - k) / 2R, d its horizontal distance from the camera,
## R 6371 km, k 0.13 (standard refraction). At 15 km that is 15 m, at 32 km
## (Mt Hamilton) 70 m, at 60 km 245 m: what really hides the far bay shore.
## It fades in over the 3 km past the region so the seam does not step.
##
## RINGS. Five square rings, each twice the post spacing of the one inside
## (40, 80, 160, 320, 640 m), from the region's edge out to +-64 km; the
## heights come from the grids in the vertex shader (no per-vertex CPU work),
## a finer ring's outer odd vertices take the mean of their neighbours so
## there are no T-junction cracks, and the innermost ring's inner edge is
## pinned to env.ground_height so it meets the lidar terrain exactly (a skirt
## hides the 4 m vs 40 m post mismatch). Each ring is cut into four sides for
## frustum culling: 20 draws, ~0.66 M triangles.
##
## CAMERAS. The sim's cameras clip at 40 km (fleet_manager/fixedwing_manager).
## This node raises the far plane of every camera in the tree to 64 km while
## the Baylands env is loaded (reversed-Z depth: no precision cost). Other
## envs are untouched.
##
## Not solid: past the region nothing is in the router's grid or the strike
## sweep (an aircraft flown 10 km out is outside the simulated site).
extends Node3D

const DIR := "res://assets/baylands/"
const FAR_PLANE := 64000.0
const BASE_STEP := 40.0
const RINGS := 5
## Rings are rects with these half-sizes (E, N), aligned so each ring's inner
## edge lies on its own post grid (see header).
const RING_HALF := [Vector2(6500, 6400), Vector2(13060, 12800), Vector2(25860, 25600),
	Vector2(51460, 51200), Vector2(64260, 64000)]

var env: Node3D
var _mat: Array[ShaderMaterial] = []
var _meta := {}


func _ready() -> void:
	name = "baylands_far"
	env = get_parent() as Node3D
	_raise_far_planes()
	get_tree().node_added.connect(_on_node_added)
	var t0 := Time.get_ticks_msec()
	_meta = _read_json(DIR + "far.json")
	if _meta.is_empty():
		push_warning("[baylands_far] no far.json: run drone/sim/tools/fetch_baylands_far.py")
		return
	var tex := _textures()
	if tex.is_empty():
		return
	var region: Rect2 = env.call("sim_bounds")
	var inner := Rect2(region)
	for k in RINGS:
		var half: Vector2 = RING_HALF[k]
		var step := BASE_STEP * pow(2.0, k)
		var mat := _material(tex, step, half)
		_mat.append(mat)
		for side in 4:
			var mi := _ring_side(inner, half, step, side, k == 0)
			if mi == null:
				continue
			mi.material_override = mat
			add_child(mi)
		inner = Rect2(-half, half * 2.0)
	_build_city()
	_build_bridges()
	if env.has_method("register_op"):
		env.register_op("far", op_far)
	if env.has_signal("look_changed"):
		env.look_changed.connect(_on_look)
	_on_look()
	print("[baylands_far] %d rings to %.0f km in %d ms" % [RINGS, RING_HALF[RINGS - 1].x / 1000.0,
		Time.get_ticks_msec() - t0])


## env_op "far": {"visible": bool, "city": bool} (for A/B measurements).
func op_far(a: Dictionary) -> Dictionary:
	if a.has("visible"):
		visible = bool(a["visible"])
	if a.has("city"):
		for c in get_children():
			if c is MultiMeshInstance3D:
				(c as Node3D).visible = bool(a["city"])
	return {"ok": true, "visible": visible, "city_buildings": city_count}


func _read_json(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var d = JSON.parse_string(FileAccess.get_file_as_string(path))
	return d if d is Dictionary else {}


# ------------------------------------------------------------------ cameras
func _raise_far_planes() -> void:
	for c in get_tree().root.find_children("*", "Camera3D", true, false):
		_on_node_added(c)


func _on_node_added(n: Node) -> void:
	if n is Camera3D and (n as Camera3D).far < FAR_PLANE:
		(n as Camera3D).far = FAR_PLANE


func _exit_tree() -> void:
	if get_tree().node_added.is_connected(_on_node_added):
		get_tree().node_added.disconnect(_on_node_added)


# ----------------------------------------------------------------- textures
func _textures() -> Dictionary:
	var out := {}
	for g in ["near", "wide"]:
		var gm: Dictionary = (_meta["grids"] as Dictionary).get(g, {})
		if gm.is_empty():
			return {}
		var n := int(gm["posts"])
		var raw := FileAccess.get_file_as_bytes(DIR + str(gm["file"]))
		var bytes := raw.decompress(n * n * 2, FileAccess.COMPRESSION_DEFLATE)
		if bytes.size() != n * n * 2:
			bytes = raw.slice(2).decompress(n * n * 2, FileAccess.COMPRESSION_DEFLATE)
		if bytes.size() != n * n * 2:
			push_warning("[baylands_far] bad %s" % gm["file"])
			return {}
		var f := PackedFloat32Array()
		f.resize(n * n)
		for k in n * n:
			f[k] = float(bytes.decode_s16(k * 2)) * 0.1
		var img := Image.create_from_data(n, n, false, Image.FORMAT_RF, f.to_byte_array())
		img.generate_mipmaps()
		out[g + "_h"] = ImageTexture.create_from_image(img)
		out[g + "_half"] = float(gm["half_m"])
		for k in ["dry", "green", "water"]:
			var p := DIR + str(gm[k])
			var im := Image.new()
			var buf := FileAccess.get_file_as_bytes(p)
			var ok := not buf.is_empty()
			if ok:
				ok = (im.load_png_from_buffer(buf) if p.ends_with(".png") else im.load_jpg_from_buffer(buf)) == OK
			if not ok:
				push_warning("[baylands_far] missing %s" % p)
				return {}
			if k == "water":
				im.convert(Image.FORMAT_L8)
			im.generate_mipmaps()
			out[g + "_" + k] = ImageTexture.create_from_image(im)
	return out


func _on_look() -> void:
	var lk: Dictionary = env.get("look")
	for m in _mat:
		m.set_shader_parameter("season", float(lk.get("season", 0.8)))
		m.set_shader_parameter("tide", float(lk.get("tide", 1.0)))
		m.set_shader_parameter("wetness", float(lk.get("wetness", 0.0)))


# --------------------------------------------------------------------- mesh
## One side (0 S, 1 N, 2 W, 3 E) of the ring between `inner` and +-half, a
## post grid of `step` anchored at the ring's outer corner. COLOR.r = 1 marks
## vertices pinned to their baked y (the innermost ring's inner edge).
func _ring_side(inner: Rect2, half: Vector2, step: float, side: int, pin: bool) -> MeshInstance3D:
	var x0 := -half.x
	var y0 := -half.y
	var nx := int(round(2.0 * half.x / step))
	var ny := int(round(2.0 * half.y / step))
	# Quad ranges of this side (S/N take the full width, W/E the middle band).
	var i0 := 0
	var i1 := nx
	var j0 := 0
	var j1 := ny
	var ji0 := int(round((inner.position.y - y0) / step))
	var ji1 := int(round((inner.end.y - y0) / step))
	var ii0 := int(round((inner.position.x - x0) / step))
	var ii1 := int(round((inner.end.x - x0) / step))
	match side:
		0:
			j1 = ji0
		1:
			j0 = ji1
		2:
			j0 = ji0
			j1 = ji1
			i1 = ii0
		3:
			j0 = ji0
			j1 = ji1
			i0 = ii1
	if i1 <= i0 or j1 <= j0:
		return null
	var w := i1 - i0 + 1
	var pos := PackedVector3Array()
	var col := PackedColorArray()
	var idx := PackedInt32Array()
	pos.resize(w * (j1 - j0 + 1))
	col.resize(pos.size())
	for j in range(j0, j1 + 1):
		for i in range(i0, i1 + 1):
			var e := x0 + i * step
			var n := y0 + j * step
			var k := (j - j0) * w + (i - i0)
			var y := 0.0
			var c := Color(0, 0, 0, 1)
			if pin and inner.grow(0.5).has_point(Vector2(e, n)):
				y = float(env.call("ground_height", e, n))
				c.r = 1.0
			pos[k] = Vector3(e, y, -n)
			col[k] = c
	for j in range(j1 - j0):
		for i in range(i1 - i0):
			var a := j * w + i
			var b := a + 1
			var c2 := a + w
			var d := c2 + 1
			idx.append_array(PackedInt32Array([a, c2, b, b, c2, d]))
	if pin:
		_skirt(pos, col, idx, inner, side)
	var nrm := PackedVector3Array()
	nrm.resize(pos.size())
	nrm.fill(Vector3.UP)
	var arr := []
	arr.resize(Mesh.ARRAY_MAX)
	arr[Mesh.ARRAY_VERTEX] = pos
	arr[Mesh.ARRAY_NORMAL] = nrm
	arr[Mesh.ARRAY_COLOR] = col
	arr[Mesh.ARRAY_INDEX] = idx
	var m := ArrayMesh.new()
	m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
	var mi := MeshInstance3D.new()
	mi.name = "far_%d_%d" % [int(step), side]
	mi.mesh = m
	# The vertex shader moves vertices up to ~1.3 km and down by the curvature.
	m.custom_aabb = _aabb(i0, i1, j0, j1, x0, y0, step)
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	return mi


func _aabb(i0: int, i1: int, j0: int, j1: int, x0: float, y0: float, step: float) -> AABB:
	var e0 := x0 + i0 * step
	var e1 := x0 + i1 * step
	var n0 := y0 + j0 * step
	var n1 := y0 + j1 * step
	return AABB(Vector3(e0, -400.0, -n1), Vector3(e1 - e0, 2000.0, n1 - n0))


## A 25 m curtain hanging under the pinned inner edge on this side, so a
## crack between the 40 m and the region's 4 m posts shows ground, not sky.
func _skirt(pos: PackedVector3Array, col: PackedColorArray, idx: PackedInt32Array, inner: Rect2, side: int) -> void:
	var edge: Array = []
	for k in pos.size():
		if col[k].r > 0.5:
			var p := Vector2(pos[k].x, -pos[k].z)
			var on := false
			match side:
				0:
					on = absf(p.y - inner.position.y) < 0.5
				1:
					on = absf(p.y - inner.end.y) < 0.5
				2:
					on = absf(p.x - inner.position.x) < 0.5
				3:
					on = absf(p.x - inner.end.x) < 0.5
			if on:
				edge.append(k)
	edge.sort_custom(func(a, b): return pos[a].x + pos[a].z < pos[b].x + pos[b].z)
	for t in edge.size() - 1:
		var a: int = edge[t]
		var b: int = edge[t + 1]
		var a2 := pos.size()
		pos.append(pos[a] - Vector3(0, 25, 0))
		col.append(Color(1, 0, 0, 1))
		pos.append(pos[b] - Vector3(0, 25, 0))
		col.append(Color(1, 0, 0, 1))
		idx.append_array(PackedInt32Array([a, b, a2, b, a2 + 1, a2]))


# ------------------------------------------------------------------- shader
func _material(tex: Dictionary, step: float, half: Vector2) -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = FAR_SHADER
	var m := ShaderMaterial.new()
	m.shader = sh
	for k in tex:
		if tex[k] is Texture2D:
			m.set_shader_parameter(k, tex[k])
	m.set_shader_parameter("near_half", tex["near_half"])
	m.set_shader_parameter("wide_half", tex["wide_half"])
	m.set_shader_parameter("step", step)
	m.set_shader_parameter("ring_half", half)
	var region: Rect2 = env.call("sim_bounds")
	m.set_shader_parameter("region_half", region.size * 0.5)
	return m


const FAR_SHADER := """
shader_type spatial;
render_mode diffuse_burley, cull_disabled;
#include "res://shaders/thermal.gdshaderinc"
uniform sampler2D near_h : filter_linear_mipmap, repeat_disable;
uniform sampler2D wide_h : filter_linear_mipmap, repeat_disable;
uniform sampler2D near_dry : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2D near_green : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2D near_water : filter_linear_mipmap, repeat_disable;
uniform sampler2D wide_dry : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2D wide_green : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2D wide_water : filter_linear_mipmap, repeat_disable;
uniform float near_half = 16000.0;
uniform float wide_half = 64000.0;
uniform float step = 40.0;
uniform vec2 ring_half = vec2(6500.0, 6400.0);
uniform vec2 region_half = vec2(2500.0, 2400.0);
uniform float season = 0.8;
uniform float tide = 1.0;
uniform float wetness = 0.0;
varying vec3 v_world;
varying float v_water;
varying float v_dist;

// ENU (e, n) -> uv of a grid with half-size h (row 0 north).
vec2 guv(vec2 en, float h) { return vec2((en.x + h) / (2.0 * h), (h - en.y) / (2.0 * h)); }

float near_w(vec2 en) {
    vec2 a = abs(en) / near_half;
    return 1.0 - smoothstep(0.86, 0.97, max(a.x, a.y));
}

float height(vec2 en) {
    // Coarser rings read a filtered level (a post covers `step` metres).
    float ln = max(log2(step / (2.0 * near_half / 1000.0)), 0.0);
    float lw = max(log2(step / (2.0 * wide_half / 1000.0)), 0.0);
    float hw = textureLod(wide_h, guv(en, wide_half), lw).r;
    float wn = near_w(en);
    if (wn > 0.0) {
        float hn = textureLod(near_h, guv(en, near_half), ln).r;
        return mix(hw, hn, wn);
    }
    return hw;
}

float water(vec2 en) {
    float wn = near_w(en);
    float w = textureLod(wide_water, guv(en, wide_half), 0.0).r;
    if (wn > 0.0) { w = mix(w, textureLod(near_water, guv(en, near_half), 0.0).r, wn); }
    return w;
}

void vertex() {
    vec2 en = vec2(VERTEX.x, -VERTEX.z);
    float h;
    if (COLOR.r > 0.5) {
        h = VERTEX.y;
    } else {
        // A finer ring's outer edge: odd posts sit between the coarser
        // ring's; take the mean of the two neighbours so the edge is the
        // coarse ring's straight line (no crack).
        vec2 a = abs(en);
        bool on_x = abs(a.x - ring_half.x) < 0.5;
        bool on_y = abs(a.y - ring_half.y) < 0.5;
        h = height(en);
        if (on_y && !on_x) {
            float i = round((en.x + ring_half.x) / step);
            if (mod(i, 2.0) > 0.5) { h = 0.5 * (height(en - vec2(step, 0.0)) + height(en + vec2(step, 0.0))); }
        } else if (on_x && !on_y) {
            float j = round((en.y + ring_half.y) / step);
            if (mod(j, 2.0) > 0.5) { h = 0.5 * (height(en - vec2(0.0, step)) + height(en + vec2(0.0, step))); }
        }
        float wa = water(en);
        if (wa > 0.5) { h = max(h, tide); }
        v_water = wa;
    }
    if (COLOR.r > 0.5) { v_water = water(en); }
    // Normal from the grid.
    float d = max(step, 32.0);
    float hx = height(en + vec2(d, 0.0)) - height(en - vec2(d, 0.0));
    float hy = height(en + vec2(0.0, d)) - height(en - vec2(0.0, d));
    NORMAL = normalize(vec3(-hx, 2.0 * d, hy));
    // Earth curvature from this camera, faded in past the region.
    vec2 out_r = max(abs(en) - region_half, vec2(0.0));
    float fade = smoothstep(0.0, 3000.0, length(out_r));
    vec2 dc = VERTEX.xz - CAMERA_POSITION_WORLD.xz;
    float d2 = dot(dc, dc);
    h -= fade * d2 * (1.0 - 0.13) / (2.0 * 6371000.0);
    VERTEX.y = h;
    v_world = VERTEX;
    v_dist = sqrt(d2);
}

void fragment() {
    vec2 en = vec2(v_world.x, -v_world.z);
    float wn = near_w(en);
    vec2 uw = guv(en, wide_half);
    vec3 cw = mix(texture(wide_green, uw).rgb, texture(wide_dry, uw).rgb, season);
    vec3 c = cw;
    float wa = texture(wide_water, uw).r;
    if (wn > 0.0) {
        vec2 un = guv(en, near_half);
        vec3 cn = mix(texture(near_green, un).rgb, texture(near_dry, un).rgb, season);
        c = mix(cw, cn, wn);
        wa = mix(wa, texture(near_water, un).r, wn);
    }
    float is_w = smoothstep(0.35, 0.65, wa);
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = mix(th_ground(c), th_water(0.0, clamp(dot(NORMAL, VIEW), 0.0, 1.0)), is_w);
        th = th_air(th, v_dist);
        TH_OUT(th);
    } else {
        // Wet ground after rain reads darker.
        c *= 1.0 - 0.25 * wetness * (1.0 - is_w);
        ALBEDO = mix(c, c * 0.55, is_w);
        ROUGHNESS = mix(0.95, 0.12, is_w);
        SPECULAR = mix(0.3, 0.5, is_w);
    }
}
"""


# --------------------------------------------------------------------- city
## OSM buildings past the region (tools/fetch_baylands_far.py, step city) as
## oriented boxes in MultiMeshes, by tier and chunk so whole chunks are
## frustum- and range-culled:
##   small (< 14 m)   2 km chunks, drawn to 7 km   (only in _sky/far_city_all)
##   mid (14-35 m)    8 km chunks, to 30 km
##   tall (>= 35 m, Lick's domes)  16 km chunks, to 64 km: San Jose's towers
## Same curvature drop as the terrain. Visual only (outside the site).
const CITY_TIERS := [[0.0, 2000.0, 7000.0], [14.0, 8000.0, 30000.0], [35.0, 16000.0, 64000.0]]
var city_count := 0


func _unzip(path: String) -> PackedByteArray:
	var z := FileAccess.get_file_as_bytes(path)
	return z.decompress_dynamic(-1, FileAccess.COMPRESSION_DEFLATE)


func _city_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = CITY_SHADER
	var mat := ShaderMaterial.new()
	mat.shader = sh
	var region: Rect2 = env.call("sim_bounds")
	mat.set_shader_parameter("region_half", region.size * 0.5)
	return mat


func _city_chunk(name_: String, tier: int, mm: MultiMesh, mat: ShaderMaterial) -> void:
	var mi := MultiMeshInstance3D.new()
	mi.name = name_
	mi.multimesh = mm
	mi.material_override = mat
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	mi.visibility_range_end = CITY_TIERS[tier][2]
	mi.visibility_range_end_margin = 500.0
	mi.extra_cull_margin = 300.0
	add_child(mi)


func _build_city() -> void:
	# Every building (gitignored, ready MultiMesh buffers) when the tool has
	# been run; else the committed lean set (tall and large ones).
	var all := DIR + "_sky/far_city_all.zbin"
	if FileAccess.file_exists(all):
		var raw := _unzip(all)
		if raw.size() > 8 and raw.slice(0, 4).get_string_from_ascii() == "BFC2":
			var mat := _city_material()
			var mesh := _unit_box()
			var nc := raw.decode_u32(4)
			var o := 8
			for c in nc:
				var tier := raw[o]
				var cx := raw.decode_s32(o + 1)
				var cy := raw.decode_s32(o + 5)
				var n := raw.decode_u32(o + 9)
				o += 13
				var mm := MultiMesh.new()
				mm.transform_format = MultiMesh.TRANSFORM_3D
				mm.use_custom_data = true
				mm.mesh = mesh
				mm.instance_count = n
				mm.buffer = raw.slice(o, o + n * 64).to_float32_array()
				o += n * 64
				city_count += n
				_city_chunk("city_%d_%d_%d" % [cx, cy, tier], tier, mm, mat)
			print("[baylands_far] city: %d buildings in %d chunks (far_city_all)" % [city_count, nc])
			return
	var path := DIR + "far_city.zbin"
	if not FileAccess.file_exists(path):
		return
	var raw := _unzip(path)
	if raw.size() < 8 or raw.slice(0, 4).get_string_from_ascii() != "BFC1":
		push_warning("[baylands_far] bad %s" % path)
		return
	var n := raw.decode_u32(4)
	var chunks := {}      # Vector3i(cx, cy, tier) -> Array of [Transform3D, Color]
	for i in n:
		var o := 8 + i * 19
		var e := raw.decode_s32(o) * 0.1
		var nn := raw.decode_s32(o + 4) * 0.1
		var w := raw.decode_u16(o + 8) * 0.1
		var d := raw.decode_u16(o + 10) * 0.1
		var yaw := deg_to_rad(raw.decode_u16(o + 12) * 0.01)
		var h := raw.decode_u16(o + 14) * 0.1
		var base := raw.decode_s16(o + 16) * 0.1
		var kind := raw[o + 18]
		if w < 1.0 or d < 1.0 or h < 1.5:
			continue
		var tier := 2 if (h >= 35.0 or kind == 5) else (1 if h >= 14.0 else 0)
		var ch: float = CITY_TIERS[tier][1]
		var key := Vector3i(floori(e / ch), floori(nn / ch), tier)
		if not chunks.has(key):
			chunks[key] = []
		# Sunk 1 m into the ground (the DEM's 128 m posts are not the lot).
		var b := Basis(Vector3.UP, yaw) * Basis.from_scale(Vector3(w, h + 1.0, d))
		var hsh := float(absi(int(e * 3.7 + nn * 1.3)) % 97) / 97.0
		(chunks[key] as Array).append([Transform3D(b, Vector3(e, base - 1.0, -nn)), Color(float(kind) / 8.0, hsh, h / 300.0, 1.0)])
		city_count += 1
	var mesh := _unit_box()
	var mat := _city_material()
	for key: Vector3i in chunks:
		var arr: Array = chunks[key]
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.use_custom_data = true
		mm.mesh = mesh
		mm.instance_count = arr.size()
		for k in arr.size():
			mm.set_instance_transform(k, arr[k][0])
			mm.set_instance_custom_data(k, arr[k][1])
		_city_chunk("city_%d_%d_%d" % [key.x, key.y, key.z], key.z, mm, mat)
	print("[baylands_far] city: %d buildings in %d chunks (%s)" % [city_count, chunks.size(), path.get_file()])


## A unit box, bottom centre at the origin, no floor.
func _unit_box() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var c := [Vector3(-0.5, 0, -0.5), Vector3(0.5, 0, -0.5), Vector3(0.5, 0, 0.5), Vector3(-0.5, 0, 0.5)]
	for k in 4:
		var a: Vector3 = c[k]
		var b: Vector3 = c[(k + 1) % 4]
		var nrm := ((a + b) * 0.5).normalized()
		st.set_normal(nrm)
		for v in [a, a + Vector3.UP, b, b, a + Vector3.UP, b + Vector3.UP]:
			st.add_vertex(v)
	st.set_normal(Vector3.UP)
	for v in [c[0] + Vector3.UP, c[2] + Vector3.UP, c[1] + Vector3.UP, c[0] + Vector3.UP, c[3] + Vector3.UP, c[2] + Vector3.UP]:
		st.add_vertex(v)
	st.index()
	return st.commit()


const CITY_SHADER := """
shader_type spatial;
render_mode world_vertex_coords, cull_disabled, diffuse_burley;
#include "res://shaders/thermal.gdshaderinc"
uniform vec2 region_half = vec2(2500.0, 2400.0);
varying vec4 v_c;
varying float v_dist;
varying float v_up;
void vertex() {
    v_c = INSTANCE_CUSTOM;
    v_up = NORMAL.y;
    vec2 en = vec2(VERTEX.x, -VERTEX.z);
    vec2 out_r = max(abs(en) - region_half, vec2(0.0));
    float fade = smoothstep(0.0, 3000.0, length(out_r));
    vec2 dc = VERTEX.xz - CAMERA_POSITION_WORLD.xz;
    float d2 = dot(dc, dc);
    VERTEX.y -= fade * d2 * (1.0 - 0.13) / (2.0 * 6371000.0);
    v_dist = sqrt(d2);
}
void fragment() {
    int kind = int(round(v_c.r * 8.0));
    float hs = v_c.g;
    float hgt = v_c.b * 300.0;
    // South Bay palette: stucco houses (beige, cream, grey-white) under grey
    // composite or terracotta tile; white and pale-grey flat commercial and
    // industrial roofs; office and apartment walls; blue-grey glass towers.
    vec3 wall = mix(vec3(0.80, 0.74, 0.64), vec3(0.88, 0.86, 0.80), hs);
    vec3 roof = hs > 0.68 ? vec3(0.60, 0.36, 0.27) : mix(vec3(0.38, 0.37, 0.36), vec3(0.52, 0.50, 0.47), hs);
    if (kind == 1 || kind == 2) { wall = mix(vec3(0.78, 0.76, 0.72), vec3(0.62, 0.62, 0.62), hs); roof = vec3(0.72, 0.72, 0.70); }
    if (kind == 3) { wall = mix(vec3(0.74, 0.73, 0.70), vec3(0.82, 0.80, 0.74), hs); roof = vec3(0.80, 0.80, 0.78); }
    if (kind == 6) { wall = vec3(0.6); roof = vec3(0.5); }
    if (hgt > 35.0 && kind != 5) { wall = mix(vec3(0.32, 0.38, 0.44), vec3(0.62, 0.62, 0.60), hs * hs); roof = vec3(0.55); }
    if (kind == 5) { wall = vec3(0.90); roof = vec3(0.92); }
    vec3 c = v_up > 0.5 ? roof : wall;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.6, 16.0, 0.7, 0.0);
        th = th_air(th, v_dist);
        TH_OUT(th);
    } else {
        ALBEDO = c;
        ROUGHNESS = hgt > 35.0 ? 0.45 : 0.9;
    }
}
"""


# ------------------------------------------------------------------ bridges
## The Dumbarton and San Mateo-Hayward bridges and the disused Dumbarton rail
## bridge (OSM ways, far_bridges.json) as deck boxes. Deck heights are not in
## the data: drawn flat, ~9 m above NAVD88 (rail 5 m). UNVERIFIED: the real
## high-rise spans over the ship channels (published clearances Dumbarton
## 85 ft = 26 m, San Mateo 135 ft = 41 m) are not modelled; OSM does not
## split them out. At 15-25 km a deck is 1-3 pixels tall.
func _build_bridges() -> void:
	var path := DIR + "far_bridges.json"
	if not FileAccess.file_exists(path):
		return
	var arr = JSON.parse_string(FileAccess.get_file_as_string(path))
	if not (arr is Array):
		return
	var xs: Array = []
	for b: Dictionary in arr:
		var pts: Array = b["pts"]
		var tags: Dictionary = b.get("tags", {})
		var top := 9.0 if tags.has("highway") else 5.0
		var width := 14.0 if tags.has("highway") else 6.0
		for k in pts.size() - 1:
			var a := Vector2(pts[k][0], pts[k][1])
			var c := Vector2(pts[k + 1][0], pts[k + 1][1])
			var mid := (a + c) * 0.5
			var ln := a.distance_to(c)
			if ln < 0.5:
				continue
			var yaw := atan2(c.y - a.y, c.x - a.x)
			var bs := Basis(Vector3.UP, yaw) * Basis.from_scale(Vector3(ln + 0.5, 2.2, width))
			xs.append(Transform3D(bs, Vector3(mid.x, top - 2.2, -mid.y)))
	if xs.is_empty():
		return
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_custom_data = true
	mm.mesh = _unit_box()
	mm.instance_count = xs.size()
	for k in xs.size():
		mm.set_instance_transform(k, xs[k])
		mm.set_instance_custom_data(k, Color(2.0 / 8.0, 0.6, 0.01, 1.0))
	var mi := MultiMeshInstance3D.new()
	mi.name = "far_bridges"
	mi.multimesh = mm
	var sh := Shader.new()
	sh.code = CITY_SHADER
	var mat := ShaderMaterial.new()
	mat.shader = sh
	mi.material_override = mat
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	mi.extra_cull_margin = 300.0
	add_child(mi)

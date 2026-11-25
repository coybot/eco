## Manhattan's trees and the 3D half of its parks, for env_manhattan_photo.
##
## Data: assets/manhattan/trees.dat + trees.json, baked by
## tools/fetch_manhattan_trees.py (NYC's 2015 Street Tree Census at each
## tree's surveyed spot; park canopy where the USGS photo shows crowns inside
## NYC Parks and OSM park polygons; OSM's mapped trees; lamp posts and benches
## along the park paths). Without the files this does nothing.
##
## TREES are procedural, low-poly broadleaf trees in four crown families
## (round, vase, columnar, conical). There are no broadleaf models in
## assets/models and the palms there are wrong for New York, so the trees are
## built here: a trunk and branches, and a crown of six lumps (shared by the
## near and mid LODs, so a tree keeps its outline across the switch) with leaf
## cards on them, cut by an alpha-tested leaf texture made at start-up. Each
## crown takes its colour from the photo at its spot (trees.dat tint).
##
## ONE MESH FOR EVERY FAMILY. Each crown vertex stores an "anchor", a point in
## a canonical unit ball (CUSTOM0), plus its offset from it (VERTEX). The vertex
## shader maps the anchor through the family's crown profile (crown_map), so a
## family is a uniform per instance, not a mesh: a chunk draws all its trees in
## one MultiMesh per LOD instead of one per family. Size comes from the
## instance transform (crown width 2r on x/z, height h on y); colour and family
## from INSTANCE_CUSTOM.
##
## LODS, handed off per TREE in the vertex shader by distance to the camera,
## not per chunk: a chunk's MultiMesh holds every tree in it at every LOD and
## each LOD's shader collapses the trees outside its band. Chunk visibility
## ranges are only the coarse cull, padded by the chunk's half-diagonal. A
## per-chunk switch would put a 177 m step in the LOD line, which shows from
## the air as a square of trees changing at once.
##   near (< LOD_NEAR_M): lumps solid face-on and leafy at the rim, leaf
##                        cards, trunk, limbs; ~330 triangles; casts shadows
##   mid  (< LOD_MID_M):  the same lumps opaque (20 faces each) + trunk
##   far  (< far end):    a camera-facing disc lit as a sphere, in 1 km chunks
## The sun's shadow pass draws with an orthographic projection and the
## shader sees the sun's camera there, so the per-tree cut is skipped for
## orthographic projections (the chunk ranges still apply).
##
## Scenery only: no colliders (the tallest tree is ~30 m, under every flight
## floor), no labels, never in env.props. Thermal: the tree shaders draw
## themselves in the LWIR band (vegetation: transpiring, a little under the
## air), so thermal_kit does not need to overlay them.
extends Node3D

const TREES_BIN := "res://assets/manhattan/trees.dat"
const TREES_JSON := "res://assets/manhattan/trees.json"
const INC := "res://shaders/thermal.gdshaderinc"

const CHUNK_M := 250.0
const MID_CHUNK_M := 500.0
const FAR_CHUNK_M := 1000.0
const LOD_NEAR_M := 240.0
const LOD_MID_M := 800.0
const FAR_END_M := 5500.0
## Half-diagonals of the chunks: how far a tree can be from its chunk's centre.
const HALF_DIAG := 177.0
const MID_HALF_DIAG := 354.0
const FAR_HALF_DIAG := 708.0

## Crown colour: trees.dat carries each crown's colour from the photo (sRGB).
## The imagery is hazy and flat, so it is pushed back toward the saturation of
## a sunlit summer crown, then scaled to sit with the orthophoto ground (which
## env_manhattan_photo draws at 0.55 of the photo).
const TINT_SAT := 1.45
const TINT_GAIN := 0.62

## Lamp posts and benches: chunked like the trees, drawn only close.
const LAMP_DRAW_M := 450.0
const BENCH_DRAW_M := 380.0

var env: Node3D
var count := 0
var _lamp_mat: StandardMaterial3D
var _night := -1.0
var _night_t := 0.0
## Chunks that may cast shadows: [MultiMeshInstance3D, centre (Godot), reach].
## Only those within reach of a camera do, switched here rather than left to
## the chunks' visibility ranges: in the sun's pass the per-tree LOD cut is
## off, so a casting chunk draws every tree in it at full detail, and whether
## Godot bounds that pass by the CAMERA's visibility range was not something
## to bet ~50M triangles a cascade on. (A frozen window seen while building
## this was macOS occluding the window, not the GPU; see the report.)
var _shadow_chunks: Array = []
var _shadow_t := 0.0
var _cams: Array[Camera3D] = []
var _cams_t := 0.0


func _ready() -> void:
	env = get_parent()
	if IpcServer._get_launch_arg("--trees", "1") == "0":
		return
	if not FileAccess.file_exists(TREES_BIN) or not FileAccess.file_exists(TREES_JSON):
		push_warning("[nature] no trees: run drone/sim/tools/fetch_manhattan_trees.py")
		return
	# Deferred one idle frame: trees stand on env.ground_height(), and a
	# sibling added after this one in the env's _ready (Central Park's terrain)
	# must be up before that answers with the relief.
	_build.call_deferred()


func _build() -> void:
	var t0 := Time.get_ticks_msec()
	var meta: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(TREES_JSON))
	var lods := IpcServer._get_launch_arg("--tree-lod", "").split(",")
	var near_m := float(lods[0]) if lods.size() > 0 and lods[0] != "" else LOD_NEAR_M
	var mid_m := float(lods[1]) if lods.size() > 1 else LOD_MID_M
	var far_m := float(lods[2]) if lods.size() > 2 else FAR_END_M
	# Shadows: "near" (default) the detailed trees only; "mid" the plain crowns
	# out to mid range too; "off".
	var shadows := IpcServer._get_launch_arg("--tree-shadows", "near")
	_build_trees(meta, near_m, mid_m, far_m, shadows)
	_build_furniture(meta)
	print("[nature] %d trees (%s), %d lamps, %d benches in %d ms; LOD %.0f/%.0f/%.0f m, shadows %s" %
		[count, str(meta.get("kinds", {})), meta.get("lamps", []).size(), meta.get("benches", []).size(),
		Time.get_ticks_msec() - t0, near_m, mid_m, far_m, shadows])


func _process(delta: float) -> void:
	_shadow_t -= delta
	if _shadow_t <= 0.0 and not _shadow_chunks.is_empty():
		_shadow_t = 0.25
		_update_shadows()
	_night_t -= delta
	if _night_t > 0.0 or _lamp_mat == null:
		return
	_night_t = 0.5
	var n := float(env.call("night_amount")) if env.has_method("night_amount") else 0.0
	if absf(n - _night) > 0.01:
		_night = n
		_lamp_mat.emission_energy_multiplier = 4.0 * n


# ------------------------------------------------------------------ data

## Trees as parallel arrays in SIM coordinates, clipped to the loaded slice
## (trees.dat's layout is in tools/fetch_manhattan_trees.py's header).
func _load(meta: Dictionary) -> Dictionary:
	var b := FileAccess.get_file_as_bytes(TREES_BIN)
	var n := b.decode_u32(0)
	var q := float(meta.get("quant_m", 0.4))
	var org: Array = meta.get("origin", [0.0, 0.0])
	var off: Vector2 = env.call("data_offset")
	var o := Vector2(float(org[0]), float(org[1])) - off
	var bounds: Rect2 = env.call("sim_bounds")
	bounds = bounds.grow(600.0)
	var pe := 4
	var pn := pe + 2 * n
	var col := pn + 2 * n          # then u8 columns h, r, family, palette, kind, flags, tint rgb, i8 lean e, n
	# Packed arrays are values in GDScript: append to locals, not through a Dictionary.
	var ps := PackedVector2Array()
	var hs := PackedFloat32Array()
	var rs := PackedFloat32Array()
	var codes := PackedByteArray()
	var tints := PackedColorArray()
	var leans := PackedVector2Array()
	for i in n:
		var p := o + Vector2(b.decode_u16(pe + 2 * i), b.decode_u16(pn + 2 * i)) * q
		if not bounds.has_point(p):
			continue
		ps.append(p)
		hs.append(b[col + i] * 0.2)
		rs.append(b[col + n + i] * 0.1)
		codes.append(b[col + 2 * n + i] * 2 + (b[col + 5 * n + i] & 1))
		tints.append(Color8(b[col + 6 * n + i], b[col + 7 * n + i], b[col + 8 * n + i]))
		leans.append(Vector2(b.decode_s8(col + 9 * n + i), b.decode_s8(col + 10 * n + i)) * 0.1)
	return {"p": ps, "h": hs, "r": rs, "code": codes, "tint": tints, "lean": leans}


static func _hash(p: Vector2, k: float) -> float:
	return fposmod(sin(p.x * 12.9898 + p.y * 78.233 + k * 37.719) * 43758.5453, 1.0)


# ------------------------------------------------------------------ trees

func _build_trees(meta: Dictionary, near_m: float, mid_m: float, far_m: float, shadows: String) -> void:
	var d := _load(meta)
	var pts: PackedVector2Array = d["p"]
	count = pts.size()
	if count == 0:
		return
	# Per-tree instance data, once: 12 floats of transform (row-major 3x4,
	# origin filled per chunk) + 4 of custom (tint, family code).
	var basis_rows := PackedFloat32Array()
	basis_rows.resize(count * 9)
	var custom := PackedFloat32Array()
	custom.resize(count * 4)
	var ys := PackedFloat32Array()
	ys.resize(count)
	var hs: PackedFloat32Array = d["h"]
	var rs: PackedFloat32Array = d["r"]
	var codes: PackedByteArray = d["code"]
	var tints: PackedColorArray = d["tint"]
	var leans: PackedVector2Array = d["lean"]
	for i in count:
		var p := pts[i]
		var a := _hash(p, 1.0) * TAU
		# Drawn 12 % wider than the baked radius: the lumps fill ~80 % of their
		# disc, and at the baked width the woodland showed ground between
		# every crown from above where the photo shows a closed canopy.
		var w := 2.0 * rs[i] * 1.12
		var h := hs[i]
		var c := cos(a)
		var s := sin(a)
		# The crown's centre is ~0.65 of the way up: lean the y axis so it
		# lands `lean` off the trunk (a shear; the trunk tilts with it).
		var ln := leans[i] / 0.65
		# Basis columns x = (c, 0, -s) w, y = (ln.e, h, -ln.n), z = (s, 0, c) w, as rows.
		var k := i * 9
		basis_rows[k] = c * w;  basis_rows[k + 1] = ln.x; basis_rows[k + 2] = s * w
		basis_rows[k + 3] = 0.0; basis_rows[k + 4] = h;  basis_rows[k + 5] = 0.0
		basis_rows[k + 6] = -s * w; basis_rows[k + 7] = -ln.y; basis_rows[k + 8] = c * w
		var t := tints[i].srgb_to_linear()
		var lum := t.r * 0.2126 + t.g * 0.7152 + t.b * 0.0722
		var lt := TINT_GAIN * (0.9 + 0.2 * _hash(p, 2.0))
		custom[i * 4] = clampf((lum + (t.r - lum) * TINT_SAT) * lt, 0.0, 1.0)
		custom[i * 4 + 1] = clampf((lum + (t.g - lum) * TINT_SAT) * lt, 0.0, 1.0)
		custom[i * 4 + 2] = clampf((lum + (t.b - lum) * TINT_SAT) * lt, 0.0, 1.0)
		custom[i * 4 + 3] = (float(codes[i]) + 0.5) / 8.0
		ys[i] = float(env.call("ground_height", p.x, p.y)) - 0.15

	# Bucket by chunk. Near chunks are small (the detailed trees are the
	# expensive ones and are culled tightest); mid and far chunks are bigger,
	# since the per-tree cut in the shader does the fine work and every chunk
	# is a draw call (times four shadow cascades where it casts).
	var near_chunks := _bucket(pts, CHUNK_M)
	var mid_chunks := _bucket(pts, MID_CHUNK_M)
	var far_chunks := _bucket(pts, FAR_CHUNK_M)

	var leaf_tex := _leaf_texture()
	var near_mesh := _near_mesh(11, leaf_tex)
	var mid_mesh := _mid_mesh()
	var far_mesh := _far_mesh()
	for sidx in near_mesh.get_surface_count():
		var sm := near_mesh.surface_get_material(sidx) as ShaderMaterial
		sm.set_shader_parameter("lod_begin", 0.0)
		sm.set_shader_parameter("lod_end", near_m)
	var mm_mat := mid_mesh.surface_get_material(0) as ShaderMaterial
	mm_mat.set_shader_parameter("lod_begin", near_m)
	mm_mat.set_shader_parameter("lod_end", mid_m)
	var fm_mat := far_mesh.surface_get_material(0) as ShaderMaterial
	fm_mat.set_shader_parameter("lod_begin", mid_m)
	fm_mat.set_shader_parameter("lod_end", far_m)

	# One near layout for every tree. A second one (different lumps and limbs)
	# doubled the near draw calls and, with each tree turned and shaped by its
	# family, was not visible from the air.
	for key: Vector2i in near_chunks:
		var centre := (Vector2(key) + Vector2(0.5, 0.5)) * CHUNK_M
		var nm := _mmi("trees_near_%d_%d" % [key.x, key.y], near_mesh, near_chunks[key], pts, ys, basis_rows,
			custom, centre, 0.0, near_m + HALF_DIAG + 20.0, false)
		if shadows != "off":
			_shadow_chunks.append([nm, Vector3(centre.x, 0.0, -centre.y), near_m + HALF_DIAG])
	for key: Vector2i in mid_chunks:
		var centre := (Vector2(key) + Vector2(0.5, 0.5)) * MID_CHUNK_M
		var mi := _mmi("trees_mid_%d_%d" % [key.x, key.y], mid_mesh, mid_chunks[key], pts, ys, basis_rows,
			custom, centre, 0.0, mid_m + MID_HALF_DIAG + 20.0, false)
		if shadows == "mid":
			_shadow_chunks.append([mi, Vector3(centre.x, 0.0, -centre.y), mid_m + MID_HALF_DIAG])
	for key: Vector2i in far_chunks:
		var centre := (Vector2(key) + Vector2(0.5, 0.5)) * FAR_CHUNK_M
		_mmi("trees_far_%d_%d" % [key.x, key.y], far_mesh, far_chunks[key], pts, ys, basis_rows, custom, centre,
			maxf(0.0, mid_m - FAR_HALF_DIAG - 20.0), far_m + FAR_HALF_DIAG, false)


## Tree indices by square chunk of side `size`.
static func _bucket(pts: PackedVector2Array, size: float) -> Dictionary:
	var lists := {}
	for i in pts.size():
		var k := Vector2i(floori(pts[i].x / size), floori(pts[i].y / size))
		if not lists.has(k):
			lists[k] = []
		(lists[k] as Array).append(i)
	var out := {}
	for k: Vector2i in lists:
		out[k] = PackedInt32Array(lists[k])
	return out


func _mmi(name_: String, mesh: Mesh, idx: PackedInt32Array, pts: PackedVector2Array, ys: PackedFloat32Array,
		basis_rows: PackedFloat32Array, custom: PackedFloat32Array, centre: Vector2,
		begin: float, end: float, shadows: bool) -> MultiMeshInstance3D:
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_custom_data = true
	mm.mesh = mesh
	mm.instance_count = idx.size()
	var buf := PackedFloat32Array()
	buf.resize(idx.size() * 16)
	var o := 0
	for i in idx:
		var k := i * 9
		var p := pts[i] - centre
		buf[o] = basis_rows[k]; buf[o + 1] = basis_rows[k + 1]; buf[o + 2] = basis_rows[k + 2]; buf[o + 3] = p.x
		buf[o + 4] = basis_rows[k + 3]; buf[o + 5] = basis_rows[k + 4]; buf[o + 6] = basis_rows[k + 5]; buf[o + 7] = ys[i]
		buf[o + 8] = basis_rows[k + 6]; buf[o + 9] = basis_rows[k + 7]; buf[o + 10] = basis_rows[k + 8]; buf[o + 11] = -p.y
		buf[o + 12] = custom[i * 4]; buf[o + 13] = custom[i * 4 + 1]
		buf[o + 14] = custom[i * 4 + 2]; buf[o + 15] = custom[i * 4 + 3]
		o += 16
	mm.buffer = buf
	var mi := MultiMeshInstance3D.new()
	mi.name = name_
	mi.multimesh = mm
	mi.position = Vector3(centre.x, 0.0, -centre.y)
	mi.visibility_range_begin = begin
	mi.visibility_range_end = end
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON if shadows \
		else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mi)
	return mi


## Near chunks cast shadows while a camera is within the near LOD's reach of
## them (every viewport's current camera: the window, vantages, aircraft).
func _update_shadows() -> void:
	_cams_t -= 0.25
	if _cams_t <= 0.0:
		_cams_t = 2.0
		_cams.clear()
		for c in get_tree().root.find_children("*", "Camera3D", true, false):
			_cams.append(c as Camera3D)
	var eyes: Array[Vector3] = []
	for c in _cams:
		if is_instance_valid(c) and c.current and c.is_inside_tree():
			eyes.append(c.global_position)
	for e: Array in _shadow_chunks:
		var mi := e[0] as MultiMeshInstance3D
		var at: Vector3 = e[1]
		var reach: float = e[2]
		var on := false
		for eye in eyes:
			if Vector2(eye.x - at.x, eye.z - at.z).length() < reach:
				on = true
				break
		var want := GeometryInstance3D.SHADOW_CASTING_SETTING_ON if on else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		if mi.cast_shadow != want:
			mi.cast_shadow = want


# ------------------------------------------------------------------ shaders

## Shared by every tree shader: the crown families, the canonical-ball mapping,
## wind, and the per-tree LOD cut.
const COMMON := """
uniform float lod_begin = 0.0;
uniform float lod_end = 240.0;
uniform float wind_amp = 1.0;

float t_hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

// Clear trunk, as a fraction of the height, by family.
float crown_base(float fam) {
    return fam < 0.5 ? 0.30 : (fam < 1.5 ? 0.36 : (fam < 2.5 ? 0.14 : 0.08));
}
// Crown half-width (0..1 of the widest) at height t (0 crown bottom .. 1 top).
float crown_profile(float t, float fam) {
    float e = sqrt(max(1.0 - (2.0 * t - 1.0) * (2.0 * t - 1.0), 0.0));
    if (fam < 0.5) return e;                                   // round
    if (fam < 1.5) {                                           // vase: narrow foot, wide flat top
        float w = mix(0.28, 1.0, smoothstep(0.0, 0.72, t));
        float k = max(t - 0.72, 0.0) / 0.28;
        return w * sqrt(max(1.0 - k * k, 0.0));
    }
    if (fam < 2.5) return pow(e, 0.55);                        // columnar: a full oval
    return pow(1.0 - t, 0.85) * sqrt(smoothstep(0.0, 0.18, t)) * 1.12;   // conical
}
vec3 crown_centre(float fam) {
    float cb = crown_base(fam);
    return vec3(0.0, cb + (1.0 - cb) * 0.5, 0.0);
}
// Canonical point -> unit tree space (crown 1 wide, tree 1 tall, base at 0).
// w = 1: a crown point. Its direction from the ball's centre picks the spot
// on the family's crown surface and its length scales out to it, so lumps
// that stick out of the unit ball stick out of every crown shape alike.
// w = 0: a trunk point, y = fraction of the clear trunk.
vec3 crown_map(vec4 a, float fam) {
    float cb = crown_base(fam);
    if (a.w < 0.5) return vec3(0.0, a.y * cb, 0.0);
    float m = length(a.xyz);
    vec3 d = m > 1e-4 ? a.xyz / m : vec3(0.0, 1.0, 0.0);
    float t = d.y * 0.5 + 0.5;
    float s = sqrt(max(1.0 - d.y * d.y, 1e-4));
    vec2 xz = d.xz / s * crown_profile(t, fam) * 0.5;
    vec3 c = crown_centre(fam);
    return c + (vec3(xz.x, cb + t * (1.0 - cb), xz.y) - c) * m;
}
// Outside this LOD's band for the camera drawing it? Never in an
// orthographic (the sun's shadow) pass, whose 'camera' is the sun's.
bool lod_out(vec3 base, vec3 cam, mat4 proj) {
    if (proj[3][3] > 0.5) return false;
    float d = distance(cam, base);
    return d < lod_begin || d >= lod_end;
}
// Sway: the whole crown leans with the gusts, more at the top.
vec3 t_wind(vec3 p, vec3 base, float time) {
    float ph = t_hash(base.xz) * 6.2832;
    float g = sin(time * 0.8 + ph) * 0.65 + sin(time * 2.3 + ph * 1.7) * 0.25;
    return p + vec3(0.8, 0.0, 0.45) * g * 0.012 * wind_amp * p.y * p.y;
}
"""

const LEAF_SHADER := """
shader_type spatial;
render_mode cull_disabled, diffuse_lambert_wrap;
#include "%s"
%s
uniform sampler2D leaf_tex : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
varying vec3 v_tint;
varying float v_airy;
varying float v_ao;
varying float v_shell;

void vertex() {
    vec3 base = MODEL_MATRIX[3].xyz;
    float code = floor(INSTANCE_CUSTOM.a * 8.0);
    float fam = floor(code * 0.5);
    v_airy = mod(code, 2.0);
    v_tint = INSTANCE_CUSTOM.rgb;
    v_shell = step(1.5, CUSTOM0.w);
    vec3 anchor = crown_map(CUSTOM0, fam);
    vec3 centre = crown_centre(fam);
    // Normals bent out from the crown's centre: a crown shades as one mass,
    // the cards and lumps only add texture to it.
    vec3 sn = normalize((anchor - centre) * vec3(1.0, 1.6, 1.0) + vec3(0.0, 0.12, 0.0));
    NORMAL = normalize(mix(NORMAL, sn, 0.7));
    float depth = length(CUSTOM0.xyz);
    float t = clamp(CUSTOM0.y * 0.5 + 0.5, 0.0, 1.0);
    v_ao = mix(0.55, 1.0, smoothstep(0.2, 1.0, depth)) * mix(0.72, 1.08, t);
    vec3 p = t_wind(anchor + VERTEX, base, TIME);
    // Leaves flutter a little on their own.
    p += NORMAL * sin(TIME * 5.0 + dot(p, vec3(31.0, 17.0, 23.0))) * 0.004 * (1.0 - v_shell);
    VERTEX = p;
    if (lod_out(base, CAMERA_POSITION_WORLD, PROJECTION_MATRIX)) { VERTEX = vec3(0.0); }
}

void fragment() {
    vec4 tx = texture(leaf_tex, UV);
    if (!FRONT_FACING) { NORMAL = -NORMAL; }
    // The lumps are solid where they face the camera and leafy at their
    // silhouette, so a crown has a ragged outline but no see-through middle.
    // Airy crowns (honeylocust) cut deeper: their sky shows through.
    float ndv = abs(dot(NORMAL, VIEW));
    float solid = v_shell * smoothstep(0.2, 0.55, ndv) * (1.0 - 0.6 * v_airy);
    ALPHA = max(tx.a, solid);
    ALPHA_SCISSOR_THRESHOLD = v_airy > 0.5 ? 0.62 : 0.5;
    ALBEDO = v_tint * tx.rgb * 1.25 * v_ao * (v_shell > 0.5 ? 0.85 : 1.0);
    ROUGHNESS = 0.8;
    SPECULAR = 0.15;
    BACKLIGHT = v_tint * 0.6;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.45, 5.0, 0.55, -1.5);
        TH_OUT(th);
        BACKLIGHT = vec3(0.0);
    }
}
"""

const WOOD_SHADER := """
shader_type spatial;
render_mode cull_back;
#include "%s"
%s
varying float v_shade;
void vertex() {
    vec3 base = MODEL_MATRIX[3].xyz;
    float code = floor(INSTANCE_CUSTOM.a * 8.0);
    float fam = floor(code * 0.5);
    v_shade = 0.75 + 0.5 * t_hash(base.xz + 3.0);
    VERTEX = t_wind(crown_map(CUSTOM0, fam) + VERTEX, base, TIME);
    if (lod_out(base, CAMERA_POSITION_WORLD, PROJECTION_MATRIX)) { VERTEX = vec3(0.0); }
}
void fragment() {
    // Darker than bark really is: trunks stand in their crown's shadow, but
    // the sun's shadow map (normal bias 2 m, sized for kilometres of city)
    // misses anything 0.3 m thick, so they were lit full and came out white.
    ALBEDO = vec3(0.032, 0.028, 0.024) * v_shade;
    ROUGHNESS = 1.0;
    SPECULAR = 0.05;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.5, 8.0, 0.6, -0.5);
        TH_OUT(th);
    }
}
"""

## Mid LOD: one opaque lumpy crown, shaded with a world-space clump pattern so
## it is not a smooth ball.
const MID_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_lambert_wrap;
#include "%s"
%s
varying vec3 v_tint;
varying float v_ao;
varying vec3 v_obj;
varying float v_wood;
float vn(vec3 p) {
    vec3 i = floor(p); vec3 f = fract(p); f = f * f * (3.0 - 2.0 * f);
    float n = dot(i, vec3(1.0, 57.0, 113.0));
    vec4 a = fract(sin(vec4(n, n + 1.0, n + 57.0, n + 58.0)) * 43758.5453);
    vec4 b = fract(sin(vec4(n + 113.0, n + 114.0, n + 170.0, n + 171.0)) * 43758.5453);
    vec4 m = mix(a, b, f.z);
    vec2 r = mix(m.xy, m.zw, f.y);
    return mix(r.x, r.y, f.x);
}
void vertex() {
    vec3 base = MODEL_MATRIX[3].xyz;
    float code = floor(INSTANCE_CUSTOM.a * 8.0);
    float fam = floor(code * 0.5);
    v_tint = INSTANCE_CUSTOM.rgb;
    v_wood = 1.0 - CUSTOM0.w;
    vec3 anchor = crown_map(CUSTOM0, fam);
    vec3 centre = crown_centre(fam);
    if (CUSTOM0.w > 0.5) {
        vec3 sn = normalize((anchor - centre) * vec3(1.0, 1.6, 1.0) + vec3(0.0, 0.1, 0.0));
        NORMAL = normalize(mix(NORMAL, sn, 0.5));
    }
    float t = clamp(CUSTOM0.y * 0.5 + 0.5, 0.0, 1.0);
    v_ao = mix(0.62, 1.05, t);
    v_obj = (anchor + VERTEX) * vec3(6.0, 5.0, 6.0) + base * 0.05;
    VERTEX = t_wind(anchor + VERTEX, base, TIME);
    if (lod_out(base, CAMERA_POSITION_WORLD, PROJECTION_MATRIX)) { VERTEX = vec3(0.0); }
}
void fragment() {
    float n = vn(v_obj) * 0.6 + vn(v_obj * 2.3) * 0.4;
    vec3 leaf = v_tint * v_ao * mix(0.55, 1.15, n);
    ALBEDO = mix(leaf, vec3(0.032, 0.028, 0.024), v_wood);
    ROUGHNESS = 0.85;
    SPECULAR = 0.25;
    BACKLIGHT = v_tint * 0.25 * (1.0 - v_wood);
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.45, 5.0, 0.55, -1.5);
        TH_OUT(th);
        BACKLIGHT = vec3(0.0);
    }
}
"""

## Far LOD: a disc facing the camera, as wide as the crown and as tall as it
## looks from this elevation, lit as the front of a sphere, with a lumpy rim.
const FAR_SHADER := """
shader_type spatial;
render_mode skip_vertex_transform, cull_disabled, diffuse_lambert_wrap;
#include "%s"
%s
varying vec3 v_tint;
varying float v_seed;
void vertex() {
    vec3 base = MODEL_MATRIX[3].xyz;
    float code = floor(INSTANCE_CUSTOM.a * 8.0);
    float fam = floor(code * 0.5);
    v_tint = INSTANCE_CUSTOM.rgb;
    v_seed = t_hash(base.xz);
    float r = length(MODEL_MATRIX[0].xyz) * 0.5 * (fam > 1.5 ? 0.8 : 0.95);
    float h = length(MODEL_MATRIX[1].xyz);
    float cb = crown_base(fam);
    float hh = h * (1.0 - cb) * 0.5;
    vec3 cw = (MODEL_MATRIX * vec4(crown_centre(fam), 1.0)).xyz;   // with the lean
    vec3 cv = (VIEW_MATRIX * vec4(cw, 1.0)).xyz;
    vec3 upv = (VIEW_MATRIX * vec4(0.0, 1.0, 0.0, 0.0)).xyz;
    float ry = mix(r, hh, clamp(abs(upv.y), 0.0, 1.0));
    vec2 c = UV * 2.0 - 1.0;
    VERTEX = cv + vec3(c.x * r, -c.y * ry, 0.0);
    NORMAL = vec3(0.0, 0.0, 1.0);
    if (lod_out(base, CAMERA_POSITION_WORLD, PROJECTION_MATRIX)) { VERTEX = vec3(0.0); }
}
void fragment() {
    vec2 c = UV * 2.0 - 1.0;
    float a = atan(c.y, c.x);
    float rim = 0.86 + 0.1 * sin(a * 5.0 + v_seed * 40.0) * sin(a * 3.0 + v_seed * 17.0);
    float rr = length(c);
    ALPHA = rr < rim ? 1.0 : 0.0;
    ALPHA_SCISSOR_THRESHOLD = 0.5;
    vec3 n = vec3(c.x, -c.y, sqrt(max(1.0 - rr * rr, 0.05)));
    NORMAL = normalize(n);
    float spec = fract(sin(dot(floor(c * 4.0), vec2(12.9, 78.2)) + v_seed) * 43758.5);
    ALBEDO = v_tint * mix(0.62, 1.0, 1.0 - rr * 0.5) * (0.85 + 0.3 * spec);
    ROUGHNESS = 0.9;
    SPECULAR = 0.2;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.45, 5.0, 0.55, -1.5);
        TH_OUT(th);
    }
}
"""


func _shader(code: String) -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = code % [INC, COMMON]
	var m := ShaderMaterial.new()
	m.shader = sh
	return m


# ------------------------------------------------------------------ meshes

## A leaf-cluster card texture: a few hundred leaves, greyscale (the shader
## tints per tree), thinning toward the card edge so cards have no outline.
## Mips are built by hand so the alpha COVERAGE holds at every level: plain
## averaging thins alpha below the cut-out threshold and crowns go bald with
## distance.
func _leaf_texture() -> ImageTexture:
	const S := 256
	var img := Image.create(S, S, false, Image.FORMAT_RGBA8)
	# Between the leaves: the crown's own mid-tone, since the solid face of a
	# lump shows it (a dark fill made every lump a black ball with leaves on).
	img.fill(Color(0.62, 0.64, 0.55, 0.0))
	var rng := RandomNumberGenerator.new()
	rng.seed = 4507
	# Leaves in a few overlapping sprays, not one disc: a card whose leaves
	# fill a circle reads as a round paddle from a few metres off.
	var sprays: Array[Vector3] = []
	for k in 7:
		var a := rng.randf() * TAU
		var r := rng.randf_range(0.0, 0.25) * S
		sprays.append(Vector3(S * 0.5 + cos(a) * r, S * 0.5 + sin(a) * r, rng.randf_range(0.14, 0.24) * S))
	for k in 2600:
		var sp: Vector3 = sprays[k % sprays.size()]
		var ang := rng.randf() * TAU
		var rad := sqrt(rng.randf()) * sp.z
		var cx := clampf(sp.x + cos(ang) * rad, 6.0, S - 6.0)
		var cy := clampf(sp.y + sin(ang) * rad, 6.0, S - 6.0)
		var ln := rng.randf_range(4.0, 7.0)
		var wd := ln * rng.randf_range(0.42, 0.6)
		var rot := rng.randf() * TAU
		var shade := rng.randf_range(0.6, 1.0)
		var ca := cos(rot)
		var sa := sin(rot)
		var ext := int(ceil(ln))
		for y in range(int(cy) - ext, int(cy) + ext + 1):
			for x in range(int(cx) - ext, int(cx) + ext + 1):
				if x < 0 or y < 0 or x >= S or y >= S:
					continue
				var dx := x - cx
				var dy := y - cy
				var u := (dx * ca + dy * sa) / ln
				var v := (-dx * sa + dy * ca) / wd
				# A leaf: an ellipse pointed at the tip.
				if u * u + v * v * (1.0 + maxf(u, 0.0) * 1.5) <= 1.0:
					var vein := 0.85 if absf(v) < 0.12 else 1.0
					var light := shade * vein * (0.85 + 0.15 * u)
					img.set_pixel(x, y, Color(light, light * 1.02, light * 0.9, 1.0))
	var levels: Array[Image] = [img]
	var cov0 := _coverage(img)
	var cur := img
	while cur.get_width() > 1:
		var nxt := cur.duplicate() as Image
		nxt.resize(maxi(1, cur.get_width() / 2), maxi(1, cur.get_height() / 2), Image.INTERPOLATE_BILINEAR)
		_fit_coverage(nxt, cov0)
		levels.append(nxt)
		cur = nxt
	var data := PackedByteArray()
	for l in levels:
		data.append_array(l.get_data())
	var full := Image.create_from_data(S, S, true, Image.FORMAT_RGBA8, data)
	return ImageTexture.create_from_image(full)


func _coverage(img: Image) -> float:
	var n := 0
	var w := img.get_width()
	var h := img.get_height()
	var d := img.get_data()
	for i in w * h:
		if d[i * 4 + 3] >= 128:
			n += 1
	return float(n) / float(w * h)


## Scale alpha about the threshold until the covered fraction matches `target`.
func _fit_coverage(img: Image, target: float) -> void:
	var d := img.get_data()
	var n := img.get_width() * img.get_height()
	var lo := 0.5
	var hi := 4.0
	var best := 1.0
	for it in 10:
		var s := (lo + hi) * 0.5
		var c := 0
		for i in n:
			if float(d[i * 4 + 3]) * s >= 128.0:
				c += 1
		if float(c) / float(n) < target:
			lo = s
		else:
			hi = s
		best = s
	for i in n:
		d[i * 4 + 3] = mini(255, int(float(d[i * 4 + 3]) * best))
	img.set_data(img.get_width(), img.get_height(), false, Image.FORMAT_RGBA8, d)


## The crown's lumps, shared by the near and mid LODs so a tree keeps its
## silhouette when it changes LOD: a central blob and five around it, in the
## canonical ball. [centre, radius] pairs.
func _blobs(seed_: int) -> Array:
	var rng := RandomNumberGenerator.new()
	rng.seed = seed_
	var out := [[Vector3(0, 0.05, 0), 0.62]]
	var a0 := rng.randf() * TAU
	for k in 5:
		var az := a0 + TAU * float(k) / 5.0 + rng.randf_range(-0.4, 0.4)
		var el := rng.randf_range(-0.35, 0.55)
		var dist := rng.randf_range(0.38, 0.5)
		var dir := Vector3(cos(az) * cos(el), sin(el), sin(az) * cos(el))
		out.append([dir * dist, rng.randf_range(0.42, 0.56)])
	return out


## Near LOD: trunk + branches (surface 0, wood), lumps + leaf cards (surface 1,
## leaves). Every vertex carries its canonical anchor in CUSTOM0.
func _near_mesh(seed_: int, leaf_tex: Texture2D) -> ArrayMesh:
	var rng := RandomNumberGenerator.new()
	rng.seed = seed_ * 7 + 1
	var blobs := _blobs(seed_)

	var wood := SurfaceTool.new()
	wood.begin(Mesh.PRIMITIVE_TRIANGLES)
	wood.set_custom_format(0, SurfaceTool.CUSTOM_RGBA_FLOAT)
	# Trunk: ground to crown base (trunk mode), then up into the crown.
	# Radii are fractions of the crown's width: 0.022 gives a 20 m elm a 0.9 m
	# trunk and an 8 m street tree a 0.35 m one.
	_limb(wood, Vector4(0, 0, 0, 0), Vector4(0, 1, 0, 0), 0.022, 0.018, 6, 1.0)
	_limb(wood, Vector4(0, -1, 0, 1), Vector4(0, 0.3, 0, 1), 0.018, 0.008, 5, 0.9)
	# Primary limbs out to the lumps around the centre, from staggered heights.
	for k in range(1, blobs.size()):
		var c: Vector3 = blobs[k][0]
		var from := Vector4(0, lerpf(-0.9, -0.1, float(k) / 5.0), 0, 1)
		_limb(wood, from, Vector4(c.x * 1.1, c.y * 1.1, c.z * 1.1, 1), 0.011, 0.004, 4, 0.85)
	var mesh := wood.commit()
	mesh.surface_set_material(0, _wood_material())

	var leaf := SurfaceTool.new()
	leaf.begin(Mesh.PRIMITIVE_TRIANGLES)
	leaf.set_custom_format(0, SurfaceTool.CUSTOM_RGBA_FLOAT)
	# Lumps, shrunk: the crown's shaded depth, so you never look straight through.
	for bl: Array in blobs:
		_blob(leaf, bl[0], float(bl[1]) * 0.94, 0, rng, 1.0)
	# Leaf cards in clusters on the lumps' surfaces, facing out with scatter.
	for k in 18:
		var bl: Array = blobs[k % blobs.size()]
		var dir := Vector3(rng.randf_range(-1, 1), rng.randf_range(-0.6, 1), rng.randf_range(-1, 1)).normalized()
		var c: Vector3 = bl[0] + dir * float(bl[1]) * 0.85
		for j in 4:
			var out := (c + Vector3(rng.randf_range(-0.6, 0.6), rng.randf_range(-0.4, 0.8),
				rng.randf_range(-0.6, 0.6))).normalized()
			var size := rng.randf_range(0.24, 0.34)
			var off := Vector3(rng.randf_range(-0.05, 0.05), rng.randf_range(-0.04, 0.04),
				rng.randf_range(-0.05, 0.05))
			_card(leaf, c, off, out, size, rng.randf() * TAU)
	leaf.commit(mesh)
	var lm := _shader(LEAF_SHADER)
	lm.set_shader_parameter("leaf_tex", leaf_tex)
	mesh.surface_set_material(1, lm)
	mesh.custom_aabb = AABB(Vector3(-0.8, -0.1, -0.8), Vector3(1.6, 1.3, 1.6))
	return mesh


var _wood_mat: ShaderMaterial = null
func _wood_material() -> ShaderMaterial:
	if _wood_mat == null:
		_wood_mat = _shader(WOOD_SHADER)
	return _wood_mat


## A tapered n-sided tube between two canonical points.
func _limb(st: SurfaceTool, a: Vector4, b: Vector4, ra: float, rb: float, sides: int, shade: float) -> void:
	var axis := Vector3(b.x - a.x, b.y - a.y, b.z - a.z)
	if a.w < 0.5 and b.w < 0.5:
		axis = Vector3.UP
	axis = axis.normalized()
	var side := axis.cross(Vector3.FORWARD if absf(axis.y) > 0.9 else Vector3.UP).normalized()
	var up := axis.cross(side).normalized()
	for s in sides:
		var a0 := TAU * float(s) / float(sides)
		var a1 := TAU * float(s + 1) / float(sides)
		var d0 := side * cos(a0) + up * sin(a0)
		var d1 := side * cos(a1) + up * sin(a1)
		var quad := [[a, d0 * ra, d0], [b, d0 * rb, d0], [b, d1 * rb, d1],
			[a, d0 * ra, d0], [b, d1 * rb, d1], [a, d1 * ra, d1]]
		for q: Array in quad:
			st.set_custom(0, Color(q[0].x, q[0].y, q[0].z, q[0].w))
			st.set_normal(q[2])
			st.set_uv(Vector2.ZERO)
			st.add_vertex(q[1])


## One lump: an icosphere around canonical `centre` (crown mode, offset 0:
## the vertex IS the anchor, so the crown shape bends it like the rest).
func _blob(st: SurfaceTool, centre: Vector3, rad: float, subdiv: int, rng: RandomNumberGenerator, shell: float) -> void:
	var tris := _icosphere(subdiv)
	var lump := {}
	for tri: Array in tris:
		for v: Vector3 in tri:
			var key := Vector3i(roundi(v.x * 1000), roundi(v.y * 1000), roundi(v.z * 1000))
			if not lump.has(key):
				lump[key] = rng.randf_range(0.88, 1.08)
	for tri: Array in tris:
		# Godot's front faces wind clockwise.
		for j in [0, 2, 1]:
			var v: Vector3 = tri[j]
			var key := Vector3i(roundi(v.x * 1000), roundi(v.y * 1000), roundi(v.z * 1000))
			var p := centre + v * rad * float(lump[key])
			st.set_custom(0, Color(p.x, p.y, p.z, 1.0 + shell))
			st.set_normal(v)
			st.set_uv(Vector2(atan2(v.z, v.x) / TAU * 2.0 + 0.5, v.y * 1.0 + 0.5))
			st.add_vertex(Vector3.ZERO)


func _card(st: SurfaceTool, anchor: Vector3, off: Vector3, n: Vector3, size: float, spin: float) -> void:
	var t := n.cross(Vector3.UP if absf(n.y) < 0.95 else Vector3.RIGHT).normalized()
	var b := n.cross(t).normalized()
	var ct := cos(spin)
	var sn := sin(spin)
	var u := (t * ct + b * sn) * size * 0.5
	var v := (-t * sn + b * ct) * size * 0.5
	var corners := [off - u - v, off + u - v, off + u + v, off - u + v]
	var uvs := [Vector2(0, 1), Vector2(1, 1), Vector2(1, 0), Vector2(0, 0)]
	for j in [0, 2, 1, 0, 3, 2]:
		st.set_custom(0, Color(anchor.x, anchor.y, anchor.z, 1.0))
		st.set_normal(n)
		st.set_uv(uvs[j])
		st.add_vertex(corners[j])


## Mid LOD: the near LOD's lumps at full size (20 faces each) and a trunk.
func _mid_mesh() -> ArrayMesh:
	var rng := RandomNumberGenerator.new()
	rng.seed = 7
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	st.set_custom_format(0, SurfaceTool.CUSTOM_RGBA_FLOAT)
	for bl: Array in _blobs(11):
		_blob(st, bl[0], float(bl[1]) * 1.04, 0, rng, 0.0)
	_limb(st, Vector4(0, 0, 0, 0), Vector4(0, 1, 0, 0), 0.022, 0.018, 5, 1.0)
	var mesh := st.commit()
	mesh.surface_set_material(0, _shader(MID_SHADER))
	mesh.custom_aabb = AABB(Vector3(-0.8, -0.1, -0.8), Vector3(1.6, 1.3, 1.6))
	return mesh


func _far_mesh() -> ArrayMesh:
	var q := QuadMesh.new()
	var st := SurfaceTool.new()
	st.append_from(q, 0, Transform3D())
	var mesh := st.commit()
	mesh.surface_set_material(0, _shader(FAR_SHADER))
	mesh.custom_aabb = AABB(Vector3(-0.8, -0.1, -0.8), Vector3(1.6, 1.3, 1.6))
	return mesh


## Unit icosphere as a list of [a, b, c] triangles.
static func _icosphere(subdiv: int) -> Array:
	var t := (1.0 + sqrt(5.0)) / 2.0
	var v := [Vector3(-1, t, 0), Vector3(1, t, 0), Vector3(-1, -t, 0), Vector3(1, -t, 0),
		Vector3(0, -1, t), Vector3(0, 1, t), Vector3(0, -1, -t), Vector3(0, 1, -t),
		Vector3(t, 0, -1), Vector3(t, 0, 1), Vector3(-t, 0, -1), Vector3(-t, 0, 1)]
	for i in v.size():
		v[i] = (v[i] as Vector3).normalized()
	var f := [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4],
		[11, 10, 2], [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
		[4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]]
	var tris := []
	for tri: Array in f:
		tris.append([v[tri[0]], v[tri[1]], v[tri[2]]])
	for s in subdiv:
		var nt := []
		for tri: Array in tris:
			var a: Vector3 = tri[0]
			var b: Vector3 = tri[1]
			var c: Vector3 = tri[2]
			var ab := ((a + b) * 0.5).normalized()
			var bc := ((b + c) * 0.5).normalized()
			var ca := ((c + a) * 0.5).normalized()
			nt.append([a, ab, ca])
			nt.append([b, bc, ab])
			nt.append([c, ca, bc])
			nt.append([ab, bc, ca])
		tris = nt
	return tris


# ------------------------------------------------------------------ furniture

## NYC Parks lamp posts (the 1911 cast-iron post with its acorn globe, ~3.7 m,
## Parks green-black) and benches (slatted, cast-iron ends), along the park
## paths. Chunked, drawn only close: from 400 m a bench is two pixels.
func _build_furniture(meta: Dictionary) -> void:
	var off: Vector2 = env.call("data_offset")
	var lamps: Array = meta.get("lamps", [])
	var benches: Array = meta.get("benches", [])
	if lamps.is_empty() and benches.is_empty():
		return
	var iron := StandardMaterial3D.new()
	iron.albedo_color = Color(0.06, 0.08, 0.06)
	iron.roughness = 0.5
	iron.metallic = 0.6
	_lamp_mat = StandardMaterial3D.new()
	_lamp_mat.albedo_color = Color(0.85, 0.84, 0.78)
	_lamp_mat.roughness = 0.2
	_lamp_mat.emission_enabled = true
	_lamp_mat.emission = Color(1.0, 0.85, 0.6)
	_lamp_mat.emission_energy_multiplier = 0.0

	var post := SurfaceTool.new()
	post.begin(Mesh.PRIMITIVE_TRIANGLES)
	for part: Array in [[0.0, 0.5, 0.16, 0.10], [0.5, 3.3, 0.065, 0.05], [3.3, 3.45, 0.11, 0.07]]:
		var cm := CylinderMesh.new()
		cm.bottom_radius = part[2]
		cm.top_radius = part[3]
		cm.height = part[1] - part[0]
		cm.radial_segments = 8
		cm.rings = 1
		post.append_from(cm, 0, Transform3D(Basis(), Vector3(0, (part[0] + part[1]) * 0.5, 0)))
	var post_mesh := post.commit()
	post_mesh.surface_set_material(0, iron)
	var globe := SphereMesh.new()
	globe.radius = 0.2
	globe.height = 0.55
	globe.radial_segments = 8
	globe.rings = 4
	globe.material = _lamp_mat

	var bench := SurfaceTool.new()
	bench.begin(Mesh.PRIMITIVE_TRIANGLES)
	for bx: Array in [[Vector3(1.8, 0.05, 0.42), Vector3(0, 0.44, 0.0)],
			[Vector3(1.8, 0.36, 0.05), Vector3(0, 0.68, -0.22)],
			[Vector3(0.06, 0.44, 0.48), Vector3(-0.82, 0.22, -0.02)],
			[Vector3(0.06, 0.44, 0.48), Vector3(0.82, 0.22, -0.02)]]:
		var b := BoxMesh.new()
		b.size = bx[0]
		bench.append_from(b, 0, Transform3D(Basis(), bx[1]))
	var bench_mesh := bench.commit()
	var wood := StandardMaterial3D.new()
	wood.albedo_color = Color(0.20, 0.24, 0.14)     # Parks-green slats, weathered
	wood.roughness = 0.85
	bench_mesh.surface_set_material(0, wood)

	var lamp_chunks := {}
	for l: Array in lamps:
		var p := Vector2(float(l[0]), float(l[1])) - off
		var k := Vector2i(floori(p.x / CHUNK_M), floori(p.y / CHUNK_M))
		if not lamp_chunks.has(k):
			lamp_chunks[k] = []
		(lamp_chunks[k] as Array).append(Transform3D(Basis(), Vector3(p.x, float(env.call("ground_height", p.x, p.y)), -p.y)))
	for k: Vector2i in lamp_chunks:
		var xf: Array = lamp_chunks[k]
		_furniture_mmi("park_lamp_posts_%d_%d" % [k.x, k.y], post_mesh, xf, k, LAMP_DRAW_M, true)
		var heads: Array = []
		for t: Transform3D in xf:
			heads.append(t.translated(Vector3(0, 3.7, 0)))
		_furniture_mmi("park_lamp_heads_%d_%d" % [k.x, k.y], globe, heads, k, LAMP_DRAW_M, false)
	var bench_chunks := {}
	for b: Array in benches:
		var p := Vector2(float(b[0]), float(b[1])) - off
		var k := Vector2i(floori(p.x / CHUNK_M), floori(p.y / CHUNK_M))
		if not bench_chunks.has(k):
			bench_chunks[k] = []
		# trees.json gives the bench's facing as a compass heading in the
		# dataset frame; the bench mesh faces +Z (its back toward -Z).
		var hdg := deg_to_rad(float(b[2]))
		var basis := Basis(Vector3.UP, PI - hdg)
		(bench_chunks[k] as Array).append(Transform3D(basis, Vector3(p.x, float(env.call("ground_height", p.x, p.y)), -p.y)))
	for k: Vector2i in bench_chunks:
		_furniture_mmi("park_benches_%d_%d" % [k.x, k.y], bench_mesh, bench_chunks[k], k, BENCH_DRAW_M, false)


func _furniture_mmi(name_: String, mesh: Mesh, xfs: Array, key: Vector2i, draw_to: float, shadows: bool) -> void:
	var centre := (Vector2(key) + Vector2(0.5, 0.5)) * CHUNK_M
	var shift := Vector3(-centre.x, 0.0, centre.y)
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.mesh = mesh
	mm.instance_count = xfs.size()
	for i in xfs.size():
		var t: Transform3D = xfs[i]
		mm.set_instance_transform(i, Transform3D(t.basis, t.origin + shift))
	var mi := MultiMeshInstance3D.new()
	mi.name = name_
	mi.multimesh = mm
	mi.position = -shift
	mi.visibility_range_end = draw_to + HALF_DIAG
	mi.visibility_range_end_margin = 40.0
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON if shadows \
		else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mi)

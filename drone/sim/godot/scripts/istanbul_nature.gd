## Istanbul's trees: the cypress woods of the Ottoman cemeteries, the great
## oriental planes (cinar) of the mosque courtyards and the Hippodrome, the
## lush parks (Gulhane and the Topkapi grounds, Yildiz, the moat gardens under
## the Land Walls), the umbrella stone pines, and the city's street and garden
## trees, with a few palms on the shore promenades.
##
## Data: assets/istanbul/trees.dat + trees.json, baked by
## tools/fetch_istanbul_trees.py: canopy where Sentinel-2's summer NDVI shows
## it (Contains modified Copernicus Sentinel data 2025), evergreen or
## deciduous from its winter NDVI, OSM's mapped trees and tree rows ((c)
## OpenStreetMap contributors, ODbL), never on water, roads, roofs or in
## buildings. Without the files this does nothing.
##
## Built the way nature.gd (Manhattan's) builds its trees, and kept separate
## from it so each scene can change on its own:
##   * one procedural crown mesh for every family; the crown's shape is a
##     per-instance family in the vertex shader (crown_map). Istanbul adds two
##     families to nature.gd's four: UMBRELLA (the stone pine's flat parasol
##     on a tall bare trunk) and FLAME (the Mediterranean cypress: a dark,
##     narrow spire);
##   * LODs per tree in the shader (near: lumps + leaf cards + limbs; mid:
##     lumps; far: a camera-facing disc), chunk visibility ranges as the coarse
##     cull, near chunks cast shadows only close to a camera;
##   * palms are the CC BY palm models in assets/models (MultiMesh, drawn to
##     2.5 km).
##
## LEAF SEASON from world_clock's date (summer when the clock runs on the
## default sun): deciduous trees leaf out in April (Judas trees flower pink
## first), turn in late October (planes go tan and hold their leaves into
## December, lindens yellow, chestnuts brown) and stand bare through the
## winter; a bare crown is drawn as see-through twigs, not a ball. Every tree
## is a MultiMesh instance whose custom data carries its colour and a leaf
## level (0 bare .. 3 full), re-filled when the date changes.
##
## Scenery only, like Manhattan's trees: no colliders, never in env.props (the
## tallest tree is ~30 m, under every flight floor). Thermal: the shaders draw
## themselves in the LWIR band as vegetation (thermal.gdshaderinc).
extends Node3D

const TREES_BIN := "res://assets/istanbul/trees.dat"
const TREES_JSON := "res://assets/istanbul/trees.json"
const INC := "res://shaders/thermal.gdshaderinc"

const CHUNK_M := 250.0
const MID_CHUNK_M := 500.0
const FAR_CHUNK_M := 1000.0
const LOD_NEAR_M := 240.0
const LOD_MID_M := 800.0
const FAR_END_M := 5500.0
const HALF_DIAG := 177.0
const MID_HALF_DIAG := 354.0
const FAR_HALF_DIAG := 708.0
const PALM_DRAW_M := 2500.0
const KIND_SOLID := 250

enum { ROUND, VASE, COLUMNAR, CONICAL, UMBRELLA, FLAME }

## Per species (trees.json "species" order): crown family, summer colour,
## autumn colour (sRGB 0-255), deciduous, leaf-fall shift (days later than
## the city's mean), airy crown.
const SPECIES := {
	"plane": [ROUND, Color8(82, 104, 52), Color8(150, 118, 62), true, 14, false],
	"linden": [ROUND, Color8(74, 98, 46), Color8(176, 156, 58), true, 0, false],
	"chestnut": [ROUND, Color8(52, 80, 38), Color8(132, 90, 44), true, -10, false],
	"judas": [ROUND, Color8(86, 108, 54), Color8(170, 148, 64), true, 0, true],
	"acacia": [VASE, Color8(98, 118, 54), Color8(178, 168, 72), true, 4, true],
	"oak": [ROUND, Color8(54, 76, 38), Color8(128, 86, 44), true, 10, false],
	"maple": [ROUND, Color8(66, 94, 42), Color8(186, 104, 46), true, -4, false],
	"magnolia": [COLUMNAR, Color8(40, 62, 34), Color8(40, 62, 34), false, 0, false],
	"cypress": [FLAME, Color8(32, 48, 32), Color8(32, 48, 32), false, 0, false],
	"stone_pine": [UMBRELLA, Color8(58, 80, 46), Color8(58, 80, 46), false, 0, false],
	"pine": [CONICAL, Color8(46, 66, 40), Color8(46, 66, 40), false, 0, false],
	"cedar": [CONICAL, Color8(52, 70, 60), Color8(52, 70, 60), false, 0, false],
	"palm_date": [ROUND, Color8(70, 92, 44), Color8(70, 92, 44), false, 0, false],
	"palm_fan": [ROUND, Color8(70, 92, 44), Color8(70, 92, 44), false, 0, false],
	"laurel": [ROUND, Color8(44, 66, 36), Color8(44, 66, 36), false, 0, false],
	"olive": [ROUND, Color8(92, 104, 78), Color8(92, 104, 78), false, 0, true],
}
## Twigs: what a bare crown shows.
const TWIG := Color8(92, 80, 70)
const JUDAS_BLOOM := Color8(196, 74, 132)
const TINT_GAIN := 0.92
const DEFAULT_DOY := 196

const ModelKit = preload("res://scripts/model_kit.gd")
const PALMS := {"palm_date": "palm_date", "palm_fan": "palm_fan"}

var env: Node3D
var count := 0
var _sp_names: Array = []
var _sp_rows: Array = []        # per species index: the SPECIES row
## Per tree: position (sim), ground y, basis rows, species, shade.
var _pts := PackedVector2Array()
var _ys := PackedFloat32Array()
var _basis := PackedFloat32Array()
var _sp := PackedByteArray()
var _shade := PackedFloat32Array()
var _custom := PackedFloat32Array()
## [MultiMesh, idx: PackedInt32Array, centre: Vector2]
var _mms: Array = []
var _doy := -1
## The mid LOD's two materials: opaque while every crown is in full leaf
## (alpha testing costs early-z on every mid tree), holed once leaves fall.
var _mid_mesh_ref: ArrayMesh
var _mid_solid: ShaderMaterial
var _mid_holes: ShaderMaterial
var _any_thin := false
var _doy_t := 0.0
var _shadow_chunks: Array = []
var _shadow_t := 0.0
var _cams: Array[Camera3D] = []
var _cams_t := 0.0


func _ready() -> void:
	env = get_parent()
	name = "istanbul_nature"
	if IpcServer._get_launch_arg("--trees", "1") == "0":
		return
	if not FileAccess.file_exists(TREES_BIN) or not FileAccess.file_exists(TREES_JSON):
		push_warning("[istanbul_nature] no trees: run drone/sim/tools/fetch_istanbul_trees.py")
		return
	# One idle frame later: the ground and the landmarks' solids are up then.
	_build.call_deferred()


func _build() -> void:
	var t0 := Time.get_ticks_msec()
	var meta: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(TREES_JSON))
	_sp_names = meta.get("species", [])
	for s in _sp_names:
		_sp_rows.append(SPECIES.get(s, SPECIES["plane"]))
	var lods := IpcServer._get_launch_arg("--tree-lod", "").split(",")
	var near_m := float(lods[0]) if lods.size() > 0 and lods[0] != "" else LOD_NEAR_M
	var mid_m := float(lods[1]) if lods.size() > 1 else LOD_MID_M
	var far_m := float(lods[2]) if lods.size() > 2 else FAR_END_M
	var shadows := IpcServer._get_launch_arg("--tree-shadows", "near")
	var palms := _load(meta)
	_doy = _clock_doy()
	_fill_custom()
	_build_trees(near_m, mid_m, far_m, shadows)
	var n_palms := _build_palms(palms)
	print("[istanbul_nature] %d trees + %d palms in %d ms (%s); LOD %.0f/%.0f/%.0f m, shadows %s, day %d" %
		[count, n_palms, Time.get_ticks_msec() - t0, str(meta.get("kinds", {})), near_m, mid_m, far_m,
		shadows, _doy])


func _process(delta: float) -> void:
	_shadow_t -= delta
	if _shadow_t <= 0.0 and not _shadow_chunks.is_empty():
		_shadow_t = 0.25
		_update_shadows()
	_doy_t -= delta
	if _doy_t <= 0.0 and count > 0:
		_doy_t = 2.0
		var d := _clock_doy()
		if d != _doy:
			_doy = d
			_fill_custom()
			_pick_mid()
			for e: Array in _mms:
				(e[0] as MultiMesh).buffer = _buffer(e[1], e[2])


## The world clock's day of year; summer while it runs on the default sun.
func _clock_doy() -> int:
	var forced := IpcServer._get_launch_arg("--leaf-day", "")
	if forced != "":
		return int(forced)
	var c := env.get_node_or_null("world_clock")
	if c == null:
		return DEFAULT_DOY
	var t = c.get("_time")
	if t is Dictionary and (t as Dictionary).has("day_of_year"):
		return int(t["day_of_year"])
	return DEFAULT_DOY


# ------------------------------------------------------------------ data

## Reads trees.dat (layout in tools/fetch_istanbul_trees.py's header) into
## the per-tree arrays; returns the palms, which are drawn as models.
func _load(meta: Dictionary) -> Dictionary:
	var b := FileAccess.get_file_as_bytes(TREES_BIN)
	var n := b.decode_u32(0)
	var q := float(meta.get("quant_m", 0.4))
	var org: Array = meta.get("origin", [0.0, 0.0])
	var o := Vector2(float(org[0]), float(org[1]))
	var pe := 4
	var pn := pe + 2 * n
	var col := pn + 2 * n         # u8 h, r, species, kind, flags, shade; i8 lean e, n
	var palms := {}
	# Solids the landmarks added (add_obstacle) are not in the bake: trees
	# standing inside one are dropped. Only bins holding such a solid are tested.
	var solid_bins := {}
	var bf: PackedInt32Array = env.get("b_first")
	var kinds: PackedByteArray = env.get("b_kind")
	var aabb: PackedFloat32Array = env.get("b_aabb")
	for i in bf.size():
		if kinds[i] == KIND_SOLID:
			for gx in range(floori(aabb[i * 4] / 50.0), floori(aabb[i * 4 + 2] / 50.0) + 1):
				for gy in range(floori(aabb[i * 4 + 1] / 50.0), floori(aabb[i * 4 + 3] / 50.0) + 1):
					solid_bins[Vector2i(gx, gy)] = true
	var dropped := 0
	for i in n:
		var p := o + Vector2(b.decode_u16(pe + 2 * i), b.decode_u16(pn + 2 * i)) * q
		var h := b[col + i] * 0.2
		var r := b[col + n + i] * 0.1
		var spi := b[col + 2 * n + i]
		var sname: String = _sp_names[spi] if spi < _sp_names.size() else "plane"
		if bool(env.call("is_water", p.x, p.y)):
			dropped += 1
			continue
		if not solid_bins.is_empty() and solid_bins.has(Vector2i(floori(p.x / 50.0), floori(p.y / 50.0))) \
				and int(env.call("building_at", p.x, p.y)) >= 0:
			dropped += 1
			continue
		var y := float(env.call("ground_height", p.x, p.y)) - 0.15
		if PALMS.has(sname):
			if not palms.has(sname):
				palms[sname] = []
			(palms[sname] as Array).append([p, y, h, _hash(p, 4.0) * TAU])
			continue
		var lean := Vector2(b.decode_s8(col + 6 * n + i), b.decode_s8(col + 7 * n + i)) * 0.1
		var a := _hash(p, 1.0) * TAU
		var w := 2.0 * r * 1.12
		var c := cos(a)
		var s := sin(a)
		var ln := lean / 0.65
		_basis.append_array(PackedFloat32Array([c * w, ln.x, s * w, 0.0, h, 0.0, -s * w, -ln.y, c * w]))
		_pts.append(p)
		_ys.append(y)
		_sp.append(spi)
		_shade.append(float(b[col + 5 * n + i]) / 128.0)
	count = _pts.size()
	if dropped > 0:
		print("[istanbul_nature] %d trees dropped (water or a landmark's solid)" % dropped)
	return palms


static func _hash(p: Vector2, k: float) -> float:
	return fposmod(sin(p.x * 12.9898 + p.y * 78.233 + k * 37.719) * 43758.5453, 1.0)


## Leaf state of a deciduous tree on day `d`, shifted `shift` days:
## [leaf level 0..3, autumn 0..1, spring 0..1 (fresh pale green)].
static func _leaf_state(d: int, shift: int) -> Array:
	var x := d - shift
	if x < 85 or x >= 352:
		return [0, 1.0, 0.0]
	if x < 102:
		return [1, 0.0, 1.0]
	if x < 120:
		return [2, 0.0, 1.0]
	if x < 140:
		return [3, 0.0, 1.0 - (x - 120) / 20.0]
	if x < 290:
		return [3, 0.0, 0.0]
	if x < 318:
		return [3, (x - 290) / 28.0, 0.0]
	if x < 334:
		return [2, 1.0, 0.0]
	return [1, 1.0, 0.0]


## Instance custom data for every tree on the current day: linear colour
## and (family * 4 + leaf level + 0.5) / 32.
func _fill_custom() -> void:
	_custom.resize(count * 4)
	_any_thin = false
	for i in count:
		var row: Array = _sp_rows[_sp[i]]
		var fam: int = row[0]
		var c: Color = row[1]
		var lvl := 3
		var airy: bool = row[5]
		var p := _pts[i]
		var jit := _hash(p, 7.0)
		if row[3]:
			var st := _leaf_state(_doy, int(row[4]) + int(jit * 12.0) - 6)
			lvl = st[0]
			c = c.lerp(row[2], float(st[1]) * (0.75 + 0.25 * jit))
			c = c.lerp(Color8(132, 160, 70), float(st[2]) * 0.5)
			if lvl < 3:
				c = c.lerp(TWIG, [0.85, 0.5, 0.2][lvl])
			if _sp_names[_sp[i]] == "judas" and _doy >= 92 and _doy < 122:
				c = JUDAS_BLOOM.lerp(Color8(170, 90, 140), jit)
				lvl = 2
		elif _doy < 75 or _doy > 335:
			c = c.darkened(0.08)                  # evergreens go a little duller in winter
		if airy and lvl == 3:
			lvl = 2
		if lvl < 2:
			_any_thin = true
		var t := c.srgb_to_linear()
		var g := TINT_GAIN * _shade[i] * (0.92 + 0.16 * _hash(p, 2.0))
		_custom[i * 4] = clampf(t.r * g, 0.0, 1.0)
		_custom[i * 4 + 1] = clampf(t.g * g, 0.0, 1.0)
		_custom[i * 4 + 2] = clampf(t.b * g, 0.0, 1.0)
		_custom[i * 4 + 3] = (float(fam * 4 + lvl) + 0.5) / 32.0


# ------------------------------------------------------------------ trees

func _build_trees(near_m: float, mid_m: float, far_m: float, shadows: String) -> void:
	if count == 0:
		return
	var near_chunks := _bucket(_pts, CHUNK_M)
	var mid_chunks := _bucket(_pts, MID_CHUNK_M)
	var far_chunks := _bucket(_pts, FAR_CHUNK_M)
	var leaf_tex := _leaf_texture()
	var near_mesh := _near_mesh(11, leaf_tex)
	var mid_mesh := _mid_mesh()
	var far_mesh := _far_mesh()
	for sidx in near_mesh.get_surface_count():
		var sm := near_mesh.surface_get_material(sidx) as ShaderMaterial
		sm.set_shader_parameter("lod_begin", 0.0)
		sm.set_shader_parameter("lod_end", near_m)
	_mid_mesh_ref = mid_mesh
	_mid_solid = mid_mesh.surface_get_material(0) as ShaderMaterial
	_mid_holes = _shader(MID_SHADER.replace("shader_type spatial;", "shader_type spatial;\n#define LEAF_HOLES"))
	for m: ShaderMaterial in [_mid_solid, _mid_holes]:
		m.set_shader_parameter("lod_begin", near_m)
		m.set_shader_parameter("lod_end", mid_m)
	_pick_mid()
	var fm_mat := far_mesh.surface_get_material(0) as ShaderMaterial
	fm_mat.set_shader_parameter("lod_begin", mid_m)
	fm_mat.set_shader_parameter("lod_end", far_m)
	for key: Vector2i in near_chunks:
		var centre := (Vector2(key) + Vector2(0.5, 0.5)) * CHUNK_M
		var nm := _mmi("trees_near_%d_%d" % [key.x, key.y], near_mesh, near_chunks[key], centre,
			0.0, near_m + HALF_DIAG + 20.0)
		if shadows != "off":
			_shadow_chunks.append([nm, Vector3(centre.x, 0.0, -centre.y), near_m + HALF_DIAG])
	for key: Vector2i in mid_chunks:
		var centre := (Vector2(key) + Vector2(0.5, 0.5)) * MID_CHUNK_M
		var mi := _mmi("trees_mid_%d_%d" % [key.x, key.y], mid_mesh, mid_chunks[key], centre,
			0.0, mid_m + MID_HALF_DIAG + 20.0)
		if shadows == "mid":
			_shadow_chunks.append([mi, Vector3(centre.x, 0.0, -centre.y), mid_m + MID_HALF_DIAG])
	for key: Vector2i in far_chunks:
		var centre := (Vector2(key) + Vector2(0.5, 0.5)) * FAR_CHUNK_M
		_mmi("trees_far_%d_%d" % [key.x, key.y], far_mesh, far_chunks[key], centre,
			maxf(0.0, mid_m - FAR_HALF_DIAG - 20.0), far_m + FAR_HALF_DIAG)


func _pick_mid() -> void:
	if _mid_mesh_ref != null:
		_mid_mesh_ref.surface_set_material(0, _mid_holes if _any_thin else _mid_solid)


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


func _buffer(idx: PackedInt32Array, centre: Vector2) -> PackedFloat32Array:
	var buf := PackedFloat32Array()
	buf.resize(idx.size() * 16)
	var o := 0
	for i in idx:
		var k := i * 9
		var p := _pts[i] - centre
		buf[o] = _basis[k]; buf[o + 1] = _basis[k + 1]; buf[o + 2] = _basis[k + 2]; buf[o + 3] = p.x
		buf[o + 4] = _basis[k + 3]; buf[o + 5] = _basis[k + 4]; buf[o + 6] = _basis[k + 5]; buf[o + 7] = _ys[i]
		buf[o + 8] = _basis[k + 6]; buf[o + 9] = _basis[k + 7]; buf[o + 10] = _basis[k + 8]; buf[o + 11] = -p.y
		buf[o + 12] = _custom[i * 4]; buf[o + 13] = _custom[i * 4 + 1]
		buf[o + 14] = _custom[i * 4 + 2]; buf[o + 15] = _custom[i * 4 + 3]
		o += 16
	return buf


func _mmi(name_: String, mesh: Mesh, idx: PackedInt32Array, centre: Vector2,
		begin: float, end: float) -> MultiMeshInstance3D:
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_custom_data = true
	mm.mesh = mesh
	mm.instance_count = idx.size()
	mm.buffer = _buffer(idx, centre)
	_mms.append([mm, idx, centre])
	var mi := MultiMeshInstance3D.new()
	mi.name = name_
	mi.multimesh = mm
	mi.position = Vector3(centre.x, 0.0, -centre.y)
	mi.visibility_range_begin = begin
	mi.visibility_range_end = end
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mi)
	return mi


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


# ------------------------------------------------------------------ palms

## Palms: the CC BY models (assets/models/CREDITS.md), one MultiMesh per
## model part, scaled to each palm's baked height.
func _build_palms(palms: Dictionary) -> int:
	var n := 0
	for sname: String in palms:
		var root := ModelKit.instance(PALMS[sname])
		if root == null:
			continue
		var parts: Array = []
		var lo := INF
		var hi := -INF
		for mi in root.find_children("*", "MeshInstance3D", true, false):
			var m := mi as MeshInstance3D
			var xf := Transform3D()
			var node: Node = m
			while node != null and node != root:
				if node is Node3D:
					xf = (node as Node3D).transform * xf
				node = node.get_parent()
			var mesh := m.mesh.duplicate() as Mesh
			for k in mesh.get_surface_count():
				var mat := m.get_surface_override_material(k)
				if mat == null and m.material_override != null:
					mat = m.material_override
				if mat != null:
					mesh.surface_set_material(k, mat)
			var ab := xf * m.get_aabb()
			lo = minf(lo, ab.position.y)
			hi = maxf(hi, ab.end.y)
			parts.append([mesh, xf])
		root.free()
		if parts.is_empty() or hi <= lo:
			continue
		var list: Array = palms[sname]
		for part: Array in parts:
			var mm := MultiMesh.new()
			mm.transform_format = MultiMesh.TRANSFORM_3D
			mm.mesh = part[0]
			mm.instance_count = list.size()
			for i in list.size():
				var e: Array = list[i]
				var s: float = float(e[2]) / (hi - lo)
				var p: Vector2 = e[0]
				var b := Basis(Vector3.UP, float(e[3])).scaled(Vector3(s, s, s))
				var t := Transform3D(b, Vector3(p.x, float(e[1]) - lo * s, -p.y)) * (part[1] as Transform3D)
				mm.set_instance_transform(i, t)
			var mi := MultiMeshInstance3D.new()
			mi.name = "palms_%s" % sname
			mi.multimesh = mm
			mi.visibility_range_end = PALM_DRAW_M
			mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
			add_child(mi)
		n += list.size()
		print("[istanbul_nature] %s: %d parts, model %.1f m tall" % [sname, parts.size(), hi - lo])
	return n


# ------------------------------------------------------------------ shaders

## nature.gd's crown families plus UMBRELLA (4) and FLAME (5); the leaf
## level (0 bare .. 3 full) rides in the same custom channel.
const COMMON := """
uniform float lod_begin = 0.0;
uniform float lod_end = 240.0;
uniform float wind_amp = 1.0;

float t_hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

float t_code(float a) { return floor(a * 32.0); }
float t_fam(float a) { return floor(t_code(a) * 0.25); }
float t_leaf(float a) { return mod(t_code(a), 4.0) / 3.0; }

// Clear trunk, as a fraction of the height, by family.
float crown_base(float fam) {
    if (fam < 0.5) return 0.30;
    if (fam < 1.5) return 0.36;
    if (fam < 2.5) return 0.14;
    if (fam < 3.5) return 0.08;
    if (fam < 4.5) return 0.60;      // stone pine: a tall bare trunk
    return 0.03;                     // cypress: leafy to the ground
}
// Crown half-width (0..1 of the widest) at height t (0 crown bottom .. 1 top).
float crown_profile(float t, float fam) {
    float e = sqrt(max(1.0 - (2.0 * t - 1.0) * (2.0 * t - 1.0), 0.0));
    if (fam < 0.5) return e;
    if (fam < 1.5) {
        float w = mix(0.28, 1.0, smoothstep(0.0, 0.72, t));
        float k = max(t - 0.72, 0.0) / 0.28;
        return w * sqrt(max(1.0 - k * k, 0.0));
    }
    if (fam < 2.5) return pow(e, 0.55);
    if (fam < 3.5) return pow(1.0 - t, 0.85) * sqrt(smoothstep(0.0, 0.18, t)) * 1.12;
    if (fam < 4.5) {                 // umbrella: flat underside, low dome on top
        float k = max(t - 0.35, 0.0) / 0.65;
        return sqrt(smoothstep(0.0, 0.3, t)) * sqrt(max(1.0 - k * k, 0.0));
    }
    // flame: widest a quarter of the way up, drawn to a point
    return min(1.0, sqrt(smoothstep(0.0, 0.12, t)) * pow(1.0 - t, 0.75) * 1.3);
}
vec3 crown_centre(float fam) {
    float cb = crown_base(fam);
    return vec3(0.0, cb + (1.0 - cb) * 0.5, 0.0);
}
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
bool lod_out(vec3 base, vec3 cam, mat4 proj) {
    if (proj[3][3] > 0.5) return false;
    float d = distance(cam, base);
    return d < lod_begin || d >= lod_end;
}
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
varying float v_leaf;
varying float v_ao;
varying float v_shell;

void vertex() {
    vec3 base = MODEL_MATRIX[3].xyz;
    float fam = t_fam(INSTANCE_CUSTOM.a);
    v_leaf = t_leaf(INSTANCE_CUSTOM.a);
    v_tint = INSTANCE_CUSTOM.rgb;
    v_shell = step(1.5, CUSTOM0.w);
    vec3 anchor = crown_map(CUSTOM0, fam);
    vec3 centre = crown_centre(fam);
    vec3 sn = normalize((anchor - centre) * vec3(1.0, 1.6, 1.0) + vec3(0.0, 0.12, 0.0));
    NORMAL = normalize(mix(NORMAL, sn, 0.7));
    float depth = length(CUSTOM0.xyz);
    float t = clamp(CUSTOM0.y * 0.5 + 0.5, 0.0, 1.0);
    v_ao = mix(0.55, 1.0, smoothstep(0.2, 1.0, depth)) * mix(0.72, 1.08, t);
    vec3 p = t_wind(anchor + VERTEX, base, TIME);
    p += NORMAL * sin(TIME * 5.0 + dot(p, vec3(31.0, 17.0, 23.0))) * 0.004 * (1.0 - v_shell);
    VERTEX = p;
    // A thinning crown drops whole leaf cards (the anchor is shared by a card's corners).
    if (v_shell < 0.5 && t_hash(CUSTOM0.xz * 13.0 + CUSTOM0.y) > 0.4 + 0.6 * v_leaf) { VERTEX = vec3(0.0); }
    if (lod_out(base, CAMERA_POSITION_WORLD, PROJECTION_MATRIX)) { VERTEX = vec3(0.0); }
}

void fragment() {
    vec4 tx = texture(leaf_tex, UV);
    if (!FRONT_FACING) { NORMAL = -NORMAL; }
    // Solid face-on and leafy at the rim when in full leaf; airy and then
    // bare twigs (the leaf texture cut hard, no solid core) as leaves fall.
    float ndv = abs(dot(NORMAL, VIEW));
    float full = smoothstep(0.5, 1.0, v_leaf);
    float solid = v_shell * smoothstep(0.2, 0.55, ndv) * full;
    // Lumps fade out as the leaves fall (gone when bare); the cards left
    // over read as twigs.
    ALPHA = max(tx.a * (v_shell > 0.5 ? mix(0.45, 1.0, v_leaf) : 1.0), solid);
    ALPHA_SCISSOR_THRESHOLD = 0.5;
    ALBEDO = v_tint * tx.rgb * 1.25 * v_ao * (v_shell > 0.5 ? 0.85 : 1.0);
    ROUGHNESS = 0.8;
    SPECULAR = 0.15;
    BACKLIGHT = v_tint * 0.6 * v_leaf;
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
varying float v_fam;
void vertex() {
    vec3 base = MODEL_MATRIX[3].xyz;
    float fam = t_fam(INSTANCE_CUSTOM.a);
    v_fam = fam;
    v_shade = 0.75 + 0.5 * t_hash(base.xz + 3.0);
    VERTEX = t_wind(crown_map(CUSTOM0, fam) + VERTEX, base, TIME);
    if (lod_out(base, CAMERA_POSITION_WORLD, PROJECTION_MATRIX)) { VERTEX = vec3(0.0); }
}
void fragment() {
    // Stone pines show a warm orange-brown plated trunk; the rest dark bark.
    vec3 bark = v_fam > 3.5 && v_fam < 4.5 ? vec3(0.10, 0.055, 0.035) : vec3(0.032, 0.028, 0.024);
    ALBEDO = bark * v_shade;
    ROUGHNESS = 1.0;
    SPECULAR = 0.05;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.5, 8.0, 0.6, -0.5);
        TH_OUT(th);
    }
}
"""

const MID_SHADER := """
shader_type spatial;
render_mode cull_back, diffuse_lambert_wrap;
#include "%s"
%s
varying vec3 v_tint;
varying float v_ao;
varying vec3 v_obj;
varying float v_wood;
varying float v_leaf;
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
    float fam = t_fam(INSTANCE_CUSTOM.a);
    v_leaf = t_leaf(INSTANCE_CUSTOM.a);
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
    // A thinning crown opens up: holes through the lumps, twigs in them.
#ifdef LEAF_HOLES
    float hole = vn(v_obj * 3.1 + 7.0);
    ALPHA = v_wood > 0.5 ? 1.0 : step((1.0 - v_leaf) * 0.75, hole);
    ALPHA_SCISSOR_THRESHOLD = 0.5;
#endif
    vec3 leaf = v_tint * v_ao * mix(0.55, 1.15, n);
    ALBEDO = mix(leaf, vec3(0.032, 0.028, 0.024), v_wood);
    ROUGHNESS = 0.85;
    SPECULAR = 0.25;
    BACKLIGHT = v_tint * 0.25 * (1.0 - v_wood) * v_leaf;
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th = th_surface(0.45, 5.0, 0.55, -1.5);
        TH_OUT(th);
        BACKLIGHT = vec3(0.0);
    }
}
"""

const FAR_SHADER := """
shader_type spatial;
render_mode skip_vertex_transform, cull_disabled, diffuse_lambert_wrap;
#include "%s"
%s
varying vec3 v_tint;
varying float v_seed;
varying float v_leaf;
void vertex() {
    vec3 base = MODEL_MATRIX[3].xyz;
    float fam = t_fam(INSTANCE_CUSTOM.a);
    v_leaf = t_leaf(INSTANCE_CUSTOM.a);
    v_tint = INSTANCE_CUSTOM.rgb;
    v_seed = t_hash(base.xz);
    float r = length(MODEL_MATRIX[0].xyz) * 0.5 * (fam > 1.5 && fam < 3.5 ? 0.8 : (fam > 4.5 ? 0.75 : 0.95));
    float h = length(MODEL_MATRIX[1].xyz);
    float cb = crown_base(fam);
    float hh = h * (1.0 - cb) * 0.5;
    vec3 cw = (MODEL_MATRIX * vec4(crown_centre(fam), 1.0)).xyz;
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
    float rim = (0.86 + 0.1 * sin(a * 5.0 + v_seed * 40.0) * sin(a * 3.0 + v_seed * 17.0)) * mix(0.72, 1.0, v_leaf);
    float rr = length(c);
    float spec = fract(sin(dot(floor(c * 4.0), vec2(12.9, 78.2)) + v_seed) * 43758.5);
    ALPHA = rr < rim && spec > (1.0 - v_leaf) * 0.55 ? 1.0 : 0.0;
    ALPHA_SCISSOR_THRESHOLD = 0.5;
    vec3 n = vec3(c.x, -c.y, sqrt(max(1.0 - rr * rr, 0.05)));
    NORMAL = normalize(n);
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
# The crown meshes are nature.gd's (same lumps, limbs and cards), built here
# against this script's shaders.

func _leaf_texture() -> ImageTexture:
	const SZ := 256
	var img := Image.create(SZ, SZ, false, Image.FORMAT_RGBA8)
	img.fill(Color(0.62, 0.64, 0.55, 0.0))
	var rng := RandomNumberGenerator.new()
	rng.seed = 4507
	var sprays: Array[Vector3] = []
	for k in 7:
		var a := rng.randf() * TAU
		var r := rng.randf_range(0.0, 0.25) * SZ
		sprays.append(Vector3(SZ * 0.5 + cos(a) * r, SZ * 0.5 + sin(a) * r, rng.randf_range(0.14, 0.24) * SZ))
	for k in 2600:
		var sp: Vector3 = sprays[k % sprays.size()]
		var ang := rng.randf() * TAU
		var rad := sqrt(rng.randf()) * sp.z
		var cx := clampf(sp.x + cos(ang) * rad, 6.0, SZ - 6.0)
		var cy := clampf(sp.y + sin(ang) * rad, 6.0, SZ - 6.0)
		var ln := rng.randf_range(4.0, 7.0)
		var wd := ln * rng.randf_range(0.42, 0.6)
		var rot := rng.randf() * TAU
		var shade := rng.randf_range(0.6, 1.0)
		var ca := cos(rot)
		var sa := sin(rot)
		var ext := int(ceil(ln))
		for y in range(int(cy) - ext, int(cy) + ext + 1):
			for x in range(int(cx) - ext, int(cx) + ext + 1):
				if x < 0 or y < 0 or x >= SZ or y >= SZ:
					continue
				var dx := x - cx
				var dy := y - cy
				var u := (dx * ca + dy * sa) / ln
				var v := (-dx * sa + dy * ca) / wd
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
	var full := Image.create_from_data(SZ, SZ, true, Image.FORMAT_RGBA8, data)
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


func _near_mesh(seed_: int, leaf_tex: Texture2D) -> ArrayMesh:
	var rng := RandomNumberGenerator.new()
	rng.seed = seed_ * 7 + 1
	var blobs := _blobs(seed_)
	var wood := SurfaceTool.new()
	wood.begin(Mesh.PRIMITIVE_TRIANGLES)
	wood.set_custom_format(0, SurfaceTool.CUSTOM_RGBA_FLOAT)
	_limb(wood, Vector4(0, 0, 0, 0), Vector4(0, 1, 0, 0), 0.022, 0.018, 6, 1.0)
	_limb(wood, Vector4(0, -1, 0, 1), Vector4(0, 0.3, 0, 1), 0.018, 0.008, 5, 0.9)
	for k in range(1, blobs.size()):
		var c: Vector3 = blobs[k][0]
		var from := Vector4(0, lerpf(-0.9, -0.1, float(k) / 5.0), 0, 1)
		_limb(wood, from, Vector4(c.x * 1.1, c.y * 1.1, c.z * 1.1, 1), 0.011, 0.004, 4, 0.85)
	var mesh := wood.commit()
	mesh.surface_set_material(0, _shader(WOOD_SHADER))
	var leaf := SurfaceTool.new()
	leaf.begin(Mesh.PRIMITIVE_TRIANGLES)
	leaf.set_custom_format(0, SurfaceTool.CUSTOM_RGBA_FLOAT)
	for bl: Array in blobs:
		_blob(leaf, bl[0], float(bl[1]) * 0.94, 0, rng, 1.0)
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
			_card(leaf, c + Vector3(0.0, 0.0, float(j) * 1e-3), off, out, size, rng.randf() * TAU)
	leaf.commit(mesh)
	var lm := _shader(LEAF_SHADER)
	lm.set_shader_parameter("leaf_tex", leaf_tex)
	mesh.surface_set_material(1, lm)
	mesh.custom_aabb = AABB(Vector3(-0.8, -0.1, -0.8), Vector3(1.6, 1.3, 1.6))
	return mesh


func _limb(st: SurfaceTool, a: Vector4, b: Vector4, ra: float, rb: float, sides: int, _shade: float) -> void:
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


func _blob(st: SurfaceTool, centre: Vector3, rad: float, subdiv: int, rng: RandomNumberGenerator, shell: float) -> void:
	var tris := _icosphere(subdiv)
	var lump := {}
	for tri: Array in tris:
		for v: Vector3 in tri:
			var key := Vector3i(roundi(v.x * 1000), roundi(v.y * 1000), roundi(v.z * 1000))
			if not lump.has(key):
				lump[key] = rng.randf_range(0.88, 1.08)
	for tri: Array in tris:
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

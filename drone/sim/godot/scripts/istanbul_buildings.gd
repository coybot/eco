## Istanbul's ~70k buildings drawn as the city's own: a sea of red terracotta
## hipped roofs over plastered pastel apartments, the 19th-century masonry of
## Beyoglu and Galata, Ottoman wooden houses with their projecting bays
## (cumba), banded stone hans under rows of lead domes, and the flat concrete
## roofs crowded with water tanks, solar heaters, dishes and chimneys.
##
## env_istanbul calls build(env) (static) in place of its plain extruded
## blocks; every building with b_skip == 0 is drawn here. Collision is NOT
## touched: env's extruded footprint up to b_top is what strikes, raycasts and
## the router answer against. The drawn roof never rises above b_top (a
## pitched roof's ridge sits AT b_top and its eaves below it); only the small
## rooftop kit (tanks, dishes, chimneys: under ~2 m), eave overhangs (~0.5 m)
## and the bay windows (~1 m proud) stand outside the solid, as Manhattan's
## rooftop kit does.
##
## WHAT A BUILDING IS. OSM gives a footprint, a kind and, rarely, roof shape,
## levels and colour (b_flags, b_levels, b_colour). The rest is decided per
## building from a stable hash, its height and size, and which quarter it
## stands in (QUARTERS: wooden houses in Zeyrek, Fener, Balat, Suleymaniye,
## Kuzguncuk; masonry in Beyoglu, Galata, Eminonu; hans round the bazaars;
## concrete apartments everywhere else).
##
## ROOFS. Hipped roofs are an inset-and-raise of the footprint itself: every
## edge is offset inward by d (miter joins) and the inset ring raised by
## d * tan(pitch); d is the largest inset that stays a simple polygon, so a
## rectangle gets a true ridge and an L or a T gets hips that meet where its
## narrowest arm closes. Near-rectangles may get gables instead. Flat roofs
## get a parapet and the clutter. Kind "bazaar" gets rows of small lead domes.
##
## WALLS. Baked cells (assets/istanbul/istanbul_facade_cells,
## tools/bake_istanbul_facade_cells.py), one bay by one storey each, laid out
## in metres along each footprint edge, storeys counted down from the eaves so
## floors stay level on Istanbul's hills, the ground floor stepping with the
## street. A face standing against a neighbour (probed just outside the
## edge) is dropped where the neighbour hides it and drawn as a bare party
## wall where it shows above it.
##
## DRAWS. One mesh (one material) per 500 m tile for every wall, roof, bay
## and dome: about the base's draw count. The rooftop kit is MultiMeshes per
## 1 km tile and part, drawn within ~1.1 km, casting shadows within 500 m.
##
## NIGHT. Windows light per building and per floor from env.night_amount(),
## with occupancy from the clock (homes fill up 19-23 h, shops close 21-23 h),
## warm tungsten, cool-white LED (common in Istanbul homes) and the odd TV.
## THERMAL: the shaders answer thermal_pass themselves (thermal.gdshaderinc).
extends Node3D

const SELF_PATH := "res://scripts/istanbul_buildings.gd"
const CELLS_DIR := "res://assets/istanbul/istanbul_facade_cells/"
const INC := "res://shaders/thermal.gdshaderinc"
const CHUNK := 500.0
const KIT_CHUNK := 1000.0
const KIT_RANGE := 650.0
const SHADOW_M := 350.0
const SHADOW_FAR := 3500.0

enum { S_APT, S_M19, S_WOOD, S_HAN, S_MOD, S_PUB, S_IND, S_SMALL, S_RUIN }
enum { SF_WALL, SF_PARTY, SF_TILE, SF_FLAT, SF_LEAD, SF_PARAPET, SF_GABLE, SF_CUMBA, SF_FLOOR, SF_METAL }

## Building flags (CUSTOM0.w).
const F_SHOPS := 1
const F_AWN := 2
const F_CORNICE := 4
const F_BALC := 8
const F_CLOSED := 16
const F_SHUT := 32
const F_OFFICE := 64
const F_AC := 128
const F_TRIM := 256      # x0..15

## Kit parts: mesh name -> draw range (m).
const PARTS := ["box", "tank", "solar", "dish", "mast"]
enum { K_TANK, K_SOLAR, K_CHIMNEY, K_DISH, K_AC, K_HUT, K_MAST }

## Quarters: [lat, lon, radius m, apt, m19, wood, han, modern, pitched, shops].
## Weights are blended by distance (gaussian, radius = 1 sigma) over a base
## of the city's ordinary concrete apartments.
const BASE_PROFILE := [0.82, 0.04, 0.02, 0.0, 0.12, 0.62, 0.35]
const QUARTERS := [
	[41.0062, 28.9760, 550.0, 0.40, 0.15, 0.32, 0.05, 0.08, 0.92, 0.35],    # Sultanahmet, Cankurtaran
	[41.0128, 28.9690, 500.0, 0.25, 0.20, 0.02, 0.38, 0.15, 0.45, 0.95],    # Mahmutpasa, Grand Bazaar quarter
	[41.0155, 28.9755, 420.0, 0.15, 0.45, 0.02, 0.22, 0.16, 0.45, 0.90],    # Eminonu, Sirkeci
	[41.0090, 28.9560, 700.0, 0.48, 0.08, 0.02, 0.04, 0.38, 0.30, 0.70],    # Laleli, Aksaray, Beyazit
	[41.0160, 28.9610, 380.0, 0.40, 0.08, 0.35, 0.15, 0.02, 0.88, 0.30],    # Suleymaniye
	[41.0198, 28.9535, 420.0, 0.55, 0.08, 0.32, 0.03, 0.02, 0.85, 0.25],    # Zeyrek
	[41.0180, 28.9400, 1100.0, 0.85, 0.04, 0.05, 0.0, 0.06, 0.62, 0.45],    # Fatih, Carsamba
	[41.0300, 28.9480, 550.0, 0.30, 0.38, 0.30, 0.02, 0.0, 0.88, 0.30],     # Fener, Balat
	[41.0360, 28.9380, 550.0, 0.70, 0.08, 0.20, 0.0, 0.02, 0.75, 0.25],     # Ayvansaray, Edirnekapi
	[41.0030, 28.9450, 1300.0, 0.86, 0.06, 0.04, 0.0, 0.04, 0.62, 0.40],    # Kumkapi, Samatya
	[41.0240, 28.9750, 480.0, 0.15, 0.68, 0.0, 0.02, 0.15, 0.55, 0.75],     # Galata, Karakoy
	[41.0330, 28.9800, 850.0, 0.28, 0.64, 0.02, 0.0, 0.06, 0.55, 0.60],     # Beyoglu, Cihangir, Tarlabasi
	[41.0370, 28.9680, 550.0, 0.65, 0.30, 0.03, 0.0, 0.02, 0.68, 0.35],     # Kasimpasa
	[41.0430, 29.0030, 1100.0, 0.66, 0.06, 0.0, 0.0, 0.28, 0.30, 0.55],     # Besiktas
	[41.0240, 29.0170, 1100.0, 0.70, 0.08, 0.14, 0.02, 0.06, 0.70, 0.45],   # Uskudar
	[41.0360, 29.0310, 380.0, 0.40, 0.06, 0.50, 0.0, 0.04, 0.92, 0.30],     # Kuzguncuk
	[40.9900, 29.0260, 1400.0, 0.74, 0.06, 0.0, 0.0, 0.20, 0.35, 0.60],     # Kadikoy
	[40.9830, 29.0250, 450.0, 0.58, 0.36, 0.02, 0.0, 0.04, 0.55, 0.35],     # Moda
]

## Wall palettes (sRGB, as picked), by style.
const PAL_APT := [Color(0.92, 0.87, 0.74), Color(0.92, 0.91, 0.87), Color(0.86, 0.80, 0.70), Color(0.94, 0.87, 0.62),
	Color(0.88, 0.71, 0.48), Color(0.90, 0.68, 0.57), Color(0.78, 0.78, 0.76), Color(0.90, 0.74, 0.72),
	Color(0.93, 0.80, 0.64), Color(0.79, 0.83, 0.71), Color(0.74, 0.80, 0.84), Color(0.80, 0.53, 0.41),
	Color(0.92, 0.87, 0.74), Color(0.92, 0.91, 0.87), Color(0.86, 0.80, 0.70), Color(0.94, 0.87, 0.62)]
const PAL_M19 := [Color(0.74, 0.71, 0.65), Color(0.82, 0.66, 0.44), Color(0.82, 0.64, 0.60), Color(0.88, 0.82, 0.70),
	Color(0.67, 0.71, 0.61), Color(0.58, 0.56, 0.53), Color(0.72, 0.78, 0.82), Color(0.66, 0.33, 0.27),
	Color(0.86, 0.74, 0.46), Color(0.74, 0.71, 0.65)]
const PAL_WOOD := [Color(0.80, 0.60, 0.32), Color(0.52, 0.21, 0.16), Color(0.47, 0.55, 0.62), Color(0.48, 0.44, 0.38),
	Color(0.89, 0.81, 0.57), Color(0.87, 0.86, 0.81), Color(0.83, 0.59, 0.49), Color(0.46, 0.56, 0.43),
	Color(0.80, 0.60, 0.32), Color(0.52, 0.21, 0.16)]
const PAL_TILE := [Color(0.62, 0.31, 0.22), Color(0.56, 0.29, 0.22), Color(0.66, 0.36, 0.26), Color(0.50, 0.29, 0.24),
	Color(0.60, 0.34, 0.27), Color(0.47, 0.30, 0.26), Color(0.64, 0.32, 0.22), Color(0.68, 0.41, 0.31)]

var env: Node3D
var mat: ShaderMaterial
var kit_mat: ShaderMaterial
var counts := {}
var _night_last := -1.0
var _hour_last := -100.0

# Read-only copies of env's arrays (Packed arrays share until written), so
# the worker threads touch no Node.
var _top := PackedFloat32Array()
var _h := PackedFloat32Array()
var _base := PackedFloat32Array()
var _minh := PackedFloat32Array()
var _kind := PackedByteArray()
var _flags := PackedByteArray()
var _levels := PackedByteArray()
var _colour := PackedByteArray()
var _name := PackedInt32Array()
var _centre := PackedVector2Array()
var _names: Array = []
var _colours: Array = []
var _quarters: Array = []        # [Vector2 centre, radius, profile Array]
var _keys: Array = []
var _chunks: Dictionary = {}
var _results: Array = []
var _mutex := Mutex.new()


# ================================================================== entry

static func build(e: Node3D) -> bool:
	var node: Node3D = load(SELF_PATH).new()
	node.name = "istanbul_buildings"
	if not node.call("_build", e):
		node.free()
		return false
	var root: Node = e.get("_building_root")
	(root if root != null else e).add_child(node)
	return true


func _build(e: Node3D) -> bool:
	env = e
	var t0 := Time.get_ticks_msec()
	var cells := _load_cells()
	if cells.is_empty():
		return false
	mat = _make_material(BUILDING_SHADER)
	mat.set_shader_parameter("cells_mask", cells["mask"])
	mat.set_shader_parameter("cells_col", cells["colour"])
	kit_mat = _make_material(KIT_SHADER)
	_top = env.get("b_top")
	_h = env.get("b_h")
	_base = env.get("b_base")
	_minh = env.get("b_minh")
	_kind = env.get("b_kind")
	_flags = env.get("b_flags")
	_levels = env.get("b_levels")
	_colour = env.get("b_colour")
	_name = env.get("b_name")
	_centre = env.get("b_centre")
	var meta: Dictionary = env.get("building_meta")
	_names = meta.get("names", [])
	_colours = meta.get("colours", [])
	for q: Array in QUARTERS:
		var c: Vector2 = env.call("geo_to_sim", float(q[0]), float(q[1]))
		_quarters.append([c, float(q[2]), q.slice(3)])
	var skip: PackedByteArray = env.get("b_skip")
	for i in _top.size():
		if skip[i] != 0 or int(_kind[i]) >= 250:
			continue
		var c := _centre[i]
		var key := Vector2i(floori(c.x / CHUNK), floori(c.y / CHUNK))
		if not _chunks.has(key):
			_chunks[key] = PackedInt32Array()
		var arr: PackedInt32Array = _chunks[key]
		arr.append(i)
		_chunks[key] = arr
	_keys = _chunks.keys()
	_results.resize(_keys.size())
	var t1 := Time.get_ticks_msec()
	var gid := WorkerThreadPool.add_group_task(_chunk_job, _keys.size(), -1, true, "istanbul_buildings")
	WorkerThreadPool.wait_for_group_task_completion(gid)
	var t2 := Time.get_ticks_msec()
	var kits := {}          # part -> {Vector2i: [xforms, colours, customs]}
	var nverts := 0
	var nstris := 0
	var ntris := 0
	for k in _keys.size():
		var r: Array = _results[k]
		if r.is_empty():
			continue
		var arrays: Array = r[0]
		var key: Vector2i = _keys[k]
		if (arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size() > 0:
			var m := ArrayMesh.new()
			m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays, [], {},
				Mesh.ARRAY_CUSTOM_RGBA_FLOAT << Mesh.ARRAY_FORMAT_CUSTOM0_SHIFT)
			var mi := MeshInstance3D.new()
			mi.name = "b_%d_%d" % [key.x, key.y]
			mi.mesh = m
			mi.material_override = mat
			mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
			add_child(mi)
			var sp: PackedVector3Array = r[3]
			if sp.size() > 0:
				var sa := []
				sa.resize(Mesh.ARRAY_MAX)
				sa[Mesh.ARRAY_VERTEX] = sp
				var sm := ArrayMesh.new()
				sm.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, sa)
				var si := MeshInstance3D.new()
				si.name = mi.name + "_shadow"
				si.mesh = sm
				si.material_override = _shadow_mat()
				si.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_SHADOWS_ONLY
				# Past ~3.5 km a roof's shadow is a pixel or two in the last split.
				si.visibility_range_end = SHADOW_FAR
				si.visibility_range_end_margin = 300.0
				add_child(si)
				nstris += sp.size() / 3
			nverts += (arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size()
			ntris += (arrays[Mesh.ARRAY_INDEX] as PackedInt32Array).size() / 3
		var kit: Dictionary = r[1]
		for part: String in kit:
			var d: Array = kit[part]
			var kk := Vector2i(floori(float(key.x) * CHUNK / KIT_CHUNK), floori(float(key.y) * CHUNK / KIT_CHUNK))
			if not kits.has(part):
				kits[part] = {}
			var pc: Dictionary = kits[part]
			if not pc.has(kk):
				pc[kk] = [[], [], []]
			var dst: Array = pc[kk]
			(dst[0] as Array).append_array(d[0])
			(dst[1] as Array).append_array(d[1])
			(dst[2] as Array).append_array(d[2])
		var cnt: Dictionary = r[2]
		for n: String in cnt:
			counts[n] = int(counts.get(n, 0)) + int(cnt[n])
	_build_kit(kits)
	_results.clear()
	_chunks.clear()
	print("[istanbul_buildings] %d chunks, %d verts, %d tris, %d shadow tris; %s; threads %d ms, total %d ms" %
		[_keys.size(), nverts, ntris, nstris, counts, t2 - t1, Time.get_ticks_msec() - t0])
	return true


func _process(_delta: float) -> void:
	if mat == null or env == null:
		return
	var n := float(env.call("night_amount"))
	var h := _clock_hour()
	if absf(n - _night_last) < 0.002 and absf(h - _hour_last) < 0.01:
		return
	_night_last = n
	_hour_last = h
	mat.set_shader_parameter("night", n)
	kit_mat.set_shader_parameter("night", n)
	mat.set_shader_parameter("home_occ", _curve(HOME_OCC, h))
	mat.set_shader_parameter("office_occ", _curve(OFFICE_OCC, h))
	mat.set_shader_parameter("shop_occ", _curve(SHOP_OCC, h))


## Shares by local hour [hour, value]. Istanbul eats late: homes peak 21-24 h;
## shops stay lit to 21-22 h and a few all night.
const HOME_OCC := [[0.0, 0.75], [1.0, 0.55], [3.0, 0.2], [5.0, 0.15], [6.0, 0.35], [8.0, 0.3],
	[17.0, 0.5], [19.0, 0.8], [21.0, 1.0], [23.5, 0.95], [24.0, 0.75]]
const OFFICE_OCC := [[0.0, 0.1], [6.0, 0.1], [8.0, 0.6], [9.0, 0.9], [18.0, 0.8], [19.0, 0.5],
	[21.0, 0.25], [24.0, 0.1]]
const SHOP_OCC := [[0.0, 0.12], [7.0, 0.2], [9.0, 1.0], [20.0, 1.0], [21.5, 0.6], [23.0, 0.25], [24.0, 0.12]]


static func _curve(table: Array, h: float) -> float:
	for i in range(1, table.size()):
		var a: Array = table[i - 1]
		var b: Array = table[i]
		if h <= float(b[0]):
			return lerpf(float(a[1]), float(b[1]), (h - float(a[0])) / maxf(float(b[0]) - float(a[0]), 1e-3))
	return float(table[-1][1])


func _clock_hour() -> float:
	var wc := env.get_node_or_null("world_clock")
	if wc != null:
		var t: Variant = wc.get("_time")
		if t is Dictionary and (t as Dictionary).has("hour"):
			return float((t as Dictionary)["hour"])
	return 21.0


# ================================================================== data

static var _cells_cache: Dictionary = {}


func _load_cells() -> Dictionary:
	if not _cells_cache.is_empty():
		return _cells_cache
	var f := FileAccess.open(CELLS_DIR + "cells.json", FileAccess.READ)
	if f == null:
		push_warning("[istanbul_buildings] no %scells.json: run drone/sim/tools/bake_istanbul_facade_cells.py" % CELLS_DIR)
		return {}
	var doc: Dictionary = JSON.parse_string(f.get_as_text())
	f.close()
	var px := int(doc.get("px", 256))
	var n: int = (doc.get("layers", []) as Array).size()
	var out := {}
	for key: String in ["mask", "colour"]:
		var strip := Image.load_from_file(CELLS_DIR + key + ".png")
		if strip == null or strip.get_height() < px * n:
			push_error("[istanbul_buildings] %s%s.png missing or short" % [CELLS_DIR, key])
			return {}
		strip.convert(Image.FORMAT_RGBA8)
		var layers: Array[Image] = []
		for k in n:
			var layer := strip.get_region(Rect2i(0, k * px, px, px))
			layer.generate_mipmaps()
			layers.append(layer)
		var arr := Texture2DArray.new()
		arr.create_from_images(layers)
		out[key] = arr
	_cells_cache = out
	return out


var _smat: ShaderMaterial


## Shadow casters are closed only from outside: draw both sides.
func _shadow_mat() -> ShaderMaterial:
	if _smat == null:
		var sh := Shader.new()
		sh.code = "shader_type spatial;\nrender_mode cull_disabled;\nvoid fragment() { ALBEDO = vec3(0.5); }\n"
		_smat = ShaderMaterial.new()
		_smat.shader = sh
	return _smat


func _make_material(code: String) -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = "shader_type spatial;\n#include \"%s\"\n" % INC + code
	var m := ShaderMaterial.new()
	m.shader = sh
	return m


# ================================================================== geometry accumulator

class Acc:
	var pos := PackedVector3Array()
	var nrm := PackedVector3Array()
	var col := PackedColorArray()
	var uv := PackedVector2Array()
	var uv2 := PackedVector2Array()
	var cus := PackedFloat32Array()
	var idx := PackedInt32Array()
	var c := Color.WHITE
	var seed := 0.0
	## The shadow caster: walls to the eaves, the roof without its overhang,
	## parapet, kit, bays or domes; positions only.
	var spos := PackedVector3Array()

	func sq(a: Vector3, b: Vector3, c2: Vector3, d: Vector3) -> void:
		spos.append_array(PackedVector3Array([a, b, c2, a, c2, d]))

	func st(a: Vector3, b: Vector3, c2: Vector3) -> void:
		spos.append_array(PackedVector3Array([a, b, c2]))

	var style := 0
	var storey := 3.0
	var flags := 0

	func surf(s: int, colour: Color) -> void:
		c = colour
		_sy = float(style + 16 * s)

	var _sy := 0.0

	func v(p: Vector3, n: Vector3, a: Vector2, b: Vector2) -> int:
		pos.append(p)
		nrm.append(n)
		col.append(c)
		uv.append(a)
		uv2.append(b)
		cus.append(seed)
		cus.append(_sy)
		cus.append(storey)
		cus.append(float(flags))
		return pos.size() - 1

	## A quad p0 p1 p2 p3 (in order round it) facing n.
	func quad(p0: Vector3, p1: Vector3, p2: Vector3, p3: Vector3, n: Vector3,
			a0: Vector2, a1: Vector2, a2: Vector2, a3: Vector2, b0: Vector2, b1: Vector2, b2: Vector2, b3: Vector2) -> void:
		var i0 := v(p0, n, a0, b0)
		var i1 := v(p1, n, a1, b1)
		var i2 := v(p2, n, a2, b2)
		var i3 := v(p3, n, a3, b3)
		# Godot's front faces wind clockwise seen from the front.
		if (p1 - p0).cross(p2 - p0).dot(n) > 0.0:
			idx.append_array(PackedInt32Array([i0, i2, i1, i0, i3, i2]))
		else:
			idx.append_array(PackedInt32Array([i0, i1, i2, i0, i2, i3]))

	func tri(p0: Vector3, p1: Vector3, p2: Vector3, n: Vector3, a0: Vector2, a1: Vector2, a2: Vector2, b: Vector2) -> void:
		var i0 := v(p0, n, a0, b)
		var i1 := v(p1, n, a1, b)
		var i2 := v(p2, n, a2, b)
		if (p1 - p0).cross(p2 - p0).dot(n) > 0.0:
			idx.append_array(PackedInt32Array([i0, i2, i1]))
		else:
			idx.append_array(PackedInt32Array([i0, i1, i2]))

	func arrays() -> Array:
		var arr := []
		arr.resize(Mesh.ARRAY_MAX)
		arr[Mesh.ARRAY_VERTEX] = pos
		arr[Mesh.ARRAY_NORMAL] = nrm
		arr[Mesh.ARRAY_COLOR] = col
		arr[Mesh.ARRAY_TEX_UV] = uv
		arr[Mesh.ARRAY_TEX_UV2] = uv2
		arr[Mesh.ARRAY_CUSTOM0] = cus
		arr[Mesh.ARRAY_INDEX] = idx
		return arr


# ================================================================== per chunk (worker threads)

func _chunk_job(k: int) -> void:
	var ids: PackedInt32Array = _chunks[_keys[k]]
	var acc := Acc.new()
	var kit := {}
	var cnt := {}
	var rng := RandomNumberGenerator.new()
	for i in ids:
		rng.seed = hash(i * 7919 + 1013)
		_building(i, acc, kit, cnt, rng)
	var res := [acc.arrays(), kit, cnt, acc.spos]
	_mutex.lock()
	_results[k] = res
	_mutex.unlock()


static func _bump(cnt: Dictionary, k: String, n: int = 1) -> void:
	cnt[k] = int(cnt.get(k, 0)) + n


func _building(i: int, acc: Acc, kit: Dictionary, cnt: Dictionary, rng: RandomNumberGenerator) -> void:
	var fp := _simplify(env.call("footprint", i))
	if fp.size() < 3:
		return
	var n := fp.size()
	var kind := int(_kind[i])
	var fl := int(_flags[i])
	var top := _top[i]
	var h := _h[i]
	var lo := _base[i] + _minh[i]
	var g0 := top - h
	var area := absf(_signed_area(fp))
	var obb := _obb(fp)
	var w: float = obb.x
	var c := _centre[i]
	var prof := _profile(c)
	var nm := ""
	if _name[i] >= 0 and _name[i] < _names.size():
		nm = str(_names[_name[i]]).to_lower()

	# ---- style
	var style := S_APT
	match kind:
		1:
			style = _pick(prof, rng, [1.0, 1.0, 0.1, 1.2, 1.6], h, area)
		2:
			style = S_HAN
		3, 12, 13:
			style = S_M19
		4:
			style = S_HAN if rng.randf() < 0.6 else S_M19
		5:
			style = S_IND
		6:
			style = S_PUB
		7:
			style = S_WOOD
		8:
			style = S_MOD
		9:
			style = S_SMALL
		10:
			style = S_RUIN
		11:
			style = S_HAN
		_:
			style = _pick(prof, rng, [1.0, 1.0, 1.0, 0.25, 1.0], h, area)
	if (area < 22.0 or h < 4.2) and style in [S_APT, S_M19, S_WOOD, S_MOD]:
		style = S_SMALL
	_bump(cnt, ["apt", "m19", "wood", "han", "modern", "public", "industrial", "small", "ruin"][style])

	# ---- look
	var seed := rng.randf()
	var storey := 3.0
	var flags := 0
	var trim := 0
	var wall := Color(0.85, 0.80, 0.70)
	match style:
		S_APT:
			storey = rng.randf_range(2.85, 3.15)
			wall = PAL_APT[rng.randi() % PAL_APT.size()]
			trim = [0, 0, 0, 0, 12, 4, 13, 2][rng.randi() % 8]
			if h >= 8.0:
				var r := rng.randf()
				flags |= F_BALC if r < 0.75 else 0
				flags |= F_CLOSED if rng.randf() < 0.55 else 0
			flags |= F_AC if rng.randf() < 0.7 else 0
		S_M19:
			storey = rng.randf_range(3.8, 4.3)
			wall = PAL_M19[rng.randi() % PAL_M19.size()]
			trim = [1, 7, 6, 13, 1][rng.randi() % 5]
			flags |= F_CORNICE if rng.randf() < 0.75 else 0
			flags |= F_SHUT if rng.randf() < 0.4 else 0
			flags |= F_BALC if rng.randf() < 0.6 else 0
			if kind == 12:
				wall = Color(0.90, 0.88, 0.83)
				trim = 7
		S_WOOD:
			storey = rng.randf_range(2.9, 3.2)
			wall = PAL_WOOD[rng.randi() % PAL_WOOD.size()]
			trim = [0, 0, 13, 2, 7][rng.randi() % 5]
		S_HAN:
			storey = rng.randf_range(4.2, 4.8)
			wall = Color(0.74, 0.69, 0.60).lerp(Color(0.66, 0.62, 0.55), rng.randf())
			trim = 5
			if kind == 2:
				trim = 6
		S_MOD:
			storey = rng.randf_range(3.2, 3.6)
			wall = [Color(0.74, 0.74, 0.72), Color(0.90, 0.90, 0.88), Color(0.82, 0.77, 0.68), Color(0.45, 0.46, 0.47),
				Color(0.86, 0.82, 0.74)][rng.randi() % 5]
			trim = [4, 8, 12, 4][rng.randi() % 4]
			flags |= F_OFFICE if (kind == 1 or rng.randf() < 0.5) else 0
		S_PUB:
			storey = rng.randf_range(3.8, 4.3)
			wall = [Color(0.90, 0.84, 0.70), Color(0.86, 0.70, 0.52), Color(0.88, 0.72, 0.66), Color(0.88, 0.88, 0.84)][rng.randi() % 4]
			trim = [0, 7, 1][rng.randi() % 3]
			flags |= F_OFFICE
			flags |= F_CORNICE if rng.randf() < 0.4 else 0
		S_IND:
			storey = 5.0
			wall = [Color(0.66, 0.67, 0.66), Color(0.60, 0.66, 0.70), Color(0.80, 0.76, 0.68), Color(0.70, 0.55, 0.45)][rng.randi() % 4]
			trim = 8
		S_SMALL:
			storey = 3.0
			wall = PAL_APT[rng.randi() % PAL_APT.size()]
			trim = 0
		S_RUIN:
			storey = 4.0
			wall = Color(0.68, 0.64, 0.57)
			trim = 6
	if int(_levels[i]) > 0:
		var s := h / float(_levels[i])
		if s > 2.6 and s < 5.5:
			storey = s
	# OSM's own colour, when tagged.
	var ci := int(_colour[i])
	if ci > 0 and ci < _colours.size():
		var oc := Color.from_string(str(_colours[ci]), Color(-1, 0, 0))
		if oc.r >= 0.0:
			wall = oc
	# Shops on the ground floor.
	var shop_w := {S_APT: 1.0, S_M19: 1.0, S_HAN: 1.0, S_MOD: 0.8, S_WOOD: 0.08}
	var shop_p: float = float(prof[6]) * float(shop_w.get(style, 0.0))
	if kind == 1:
		shop_p = maxf(shop_p, 0.85)
	if h >= 6.0 and area > 35.0 and rng.randf() < shop_p:
		flags |= F_SHOPS
		flags |= F_AWN if rng.randf() < 0.4 else 0
	flags |= F_TRIM * trim
	# From the air the plaster is a shade duller than it is picked.
	var lum := wall.get_luminance()
	wall = wall.lerp(Color(lum, lum, lum), 0.06) * 0.95
	wall.a = 1.0
	acc.seed = seed
	acc.style = style
	acc.storey = storey
	acc.flags = flags

	# ---- roof kind
	var roof := "flat"
	var pitched_p := float(prof[5])
	if h > 32.0:
		pitched_p *= 0.05
	elif h > 24.0:
		pitched_p *= 0.25
	elif h > 18.0:
		pitched_p *= 0.55
	elif h > 14.0:
		pitched_p *= 0.85
	if area > 2500.0:
		pitched_p *= 0.1
	elif area > 1200.0:
		pitched_p *= 0.4
	match style:
		S_WOOD:
			pitched_p = minf(0.97, pitched_p * 1.6 + 0.3)
		S_M19:
			pitched_p = minf(0.9, pitched_p * 1.2)
		S_MOD:
			pitched_p *= 0.25
		S_PUB:
			pitched_p = minf(0.85, pitched_p * 1.1)
		S_HAN:
			pitched_p = 0.35
		S_IND:
			pitched_p = 0.6
		S_SMALL:
			pitched_p = 0.45
	if rng.randf() < pitched_p:
		roof = "pitched"
	if fl & 16:
		roof = "flat"
	elif fl & 8:
		roof = "pitched"
	if style == S_RUIN:
		roof = "none"
	elif kind == 11 and area < 3500.0:
		roof = "domes"
	elif (fl & 4) and kind != 0 and area < 1500.0:
		roof = "dome"
	elif kind == 4 and (nm.contains("hamam") or nm.contains("hammam")):
		roof = "domes"
	if n > 28 or w < 3.0:
		if roof == "pitched":
			roof = "flat"

	# ---- edges: which stand against a neighbour
	var gl := PackedFloat32Array()
	gl.resize(n)
	for k in n:
		gl[k] = float(env.call("ground_height", fp[k].x, fp[k].y))
	var nb_top := PackedFloat32Array()      # INF = exposed
	nb_top.resize(n)
	var party := PackedByteArray()          # 0 exposed, 1 party (shows above nb_top), 2 party all along
	party.resize(n)
	for k in n:
		var p := fp[k]
		var q := fp[(k + 1) % n]
		var e := q - p
		var L := e.length()
		nb_top[k] = INF
		if L < 0.3:
			continue
		var o2 := Vector2(e.y, -e.x) / L
		var probes: Array = [0.5] if L < 6.0 else [0.18, 0.5, 0.82]
		var hits := 0
		var nt := INF
		for f: float in probes:
			var pt := p + e * f + o2 * 0.6
			var j := int(env.call("building_at", pt.x, pt.y))
			if j >= 0 and j != i:
				hits += 1
				nt = minf(nt, _top[j])
		if hits * 2 > probes.size():
			party[k] = 2 if hits == probes.size() else 1
			nb_top[k] = nt if hits == probes.size() else INF

	# ---- roof geometry (sets the wall top)
	var tanp := tan(deg_to_rad(rng.randf_range(24.0, 31.0) if style != S_WOOD else rng.randf_range(27.0, 33.0)))
	var ov := 0.45 if style != S_WOOD else 0.75
	if style == S_IND:
		tanp = tan(deg_to_rad(rng.randf_range(10.0, 16.0)))
		ov = 0.3
	var t0c: Color = PAL_TILE[rng.randi() % PAL_TILE.size()]
	var t1c: Color = PAL_TILE[rng.randi() % PAL_TILE.size()]
	var roof_c := t0c.lerp(t1c, rng.randf()) * rng.randf_range(0.80, 0.98)
	if rng.randf() < 0.18:
		# Old, faded and lichened.
		roof_c = roof_c.lerp(Color(0.50, 0.42, 0.38), rng.randf_range(0.3, 0.6))
	var rl := roof_c.get_luminance()
	roof_c = roof_c.lerp(Color(rl, rl, rl), 0.12)
	roof_c.a = 1.0
	var info := {"type": "flat", "y": top, "ring": fp}
	var wall_top := top
	var gable_edges := PackedInt32Array()
	if roof == "pitched":
		var hip := _hip(fp, w)
		var d: float = hip[0]
		var inner: PackedVector2Array = hip[1]
		var gable := n == 4 and _rectish(fp) and (obb.y / maxf(w, 0.1) > 1.25 and rng.randf() < 0.3 or style == S_IND)
		if gable:
			d = w * 0.5
		var rise := d * tanp
		var wall_h := top - g0
		if rise > wall_h * 0.4:
			tanp *= wall_h * 0.4 / rise
			rise = wall_h * 0.4
		if gable:
			var eave := top - rise
			gable_edges = _gable(fp, eave, top, tanp, ov, acc, style, roof_c, gl, wall)
			if gable_edges.size() > 0:
				wall_top = eave
				info = {"type": "gable", "eave": eave, "tanp": tanp, "top": top, "ring": fp, "axis": gable_edges}
				_bump(cnt, "gable")
		elif d >= 1.2 and d >= 0.28 * w and inner.size() >= 3:
			var eave := top - rise
			_hip_roof(fp, inner, eave, top, tanp, ov, acc, style, roof_c)
			wall_top = eave
			info = {"type": "hip", "eave": eave, "tanp": tanp, "top": top, "ring": fp}
			_bump(cnt, "hip")
	if info["type"] == "flat":
		match roof:
			"none":
				info = _ruin(fp, top, g0, acc, wall)
			"domes", "dome":
				info = _flat_roof(fp, top, h, acc, Color(0.64, 0.67, 0.69), SF_LEAD, 0.6)
				_domes(info, roof == "dome", obb, acc, rng, cnt)
			_:
				var fc: Color = [Color(0.60, 0.59, 0.56), Color(0.66, 0.65, 0.62), Color(0.52, 0.51, 0.49),
					Color(0.30, 0.30, 0.31), Color(0.64, 0.46, 0.38), Color(0.70, 0.69, 0.66)][rng.randi() % 6]
				info = _flat_roof(fp, top, h, acc, fc, SF_FLAT, 0.9 if h > 5.0 else 0.0)
				_bump(cnt, "flat")

	# ---- walls
	acc.surf(SF_WALL, wall)
	var eave_ref := wall_top
	var s_top := wall_top if str(info["type"]) in ["hip", "gable", "none"] else float(info["y"])
	for k in n:
		var p := fp[k]
		var q := fp[(k + 1) % n]
		var e := q - p
		var L := e.length()
		if L < 0.05:
			continue
		var bot := lo
		var sf := SF_WALL
		if party[k] == 2:
			if nb_top[k] >= wall_top - 0.3:
				continue
			bot = maxf(lo, nb_top[k] - 0.2)
			sf = SF_PARTY
		elif party[k] == 1:
			sf = SF_PARTY
		acc.surf(sf, wall)
		var nn := Vector3(e.y, 0.0, e.x) / L
		acc.sq(Vector3(p.x, bot, -p.y), Vector3(q.x, bot, -q.y), Vector3(q.x, s_top, -q.y), Vector3(p.x, s_top, -p.y))
		acc.quad(Vector3(p.x, bot, -p.y), Vector3(q.x, bot, -q.y), Vector3(q.x, wall_top, -q.y), Vector3(p.x, wall_top, -p.y), nn,
			Vector2(0.0, L), Vector2(L, L), Vector2(L, L), Vector2(0.0, L),
			Vector2(eave_ref, gl[k]), Vector2(eave_ref, gl[(k + 1) % n]), Vector2(eave_ref, gl[(k + 1) % n]), Vector2(eave_ref, gl[k]))

	# ---- the bay window (cumba / cikma)
	var cumba_p := {S_WOOD: 0.8, S_M19: 0.35, S_APT: 0.4}
	if h >= 8.5 and info["type"] != "none" and rng.randf() < float(cumba_p.get(style, 0.0)):
		_cumba(fp, party, gl, eave_ref, wall_top, style, storey, flags, acc, rng, wall, roof_c if info["type"] != "flat" else Color(0.6, 0.59, 0.56), cnt)

	# ---- rooftop kit
	_roof_kit(i, fp, info, style, area, h, wall, kit, cnt, rng)


# ------------------------------------------------------------------ style picking

## Profile at a point: BASE_PROFILE blended with the quarters around it.
func _profile(c: Vector2) -> Array:
	var acc := BASE_PROFILE.duplicate()
	var wsum := 0.35
	for k in acc.size():
		acc[k] = float(acc[k]) * wsum
	for q: Array in _quarters:
		var d := (q[0] as Vector2).distance_to(c) / float(q[1])
		if d > 2.5:
			continue
		var wt := exp(-d * d)
		var pr: Array = q[2]
		for k in acc.size():
			acc[k] = float(acc[k]) + float(pr[k]) * wt
		wsum += wt
	for k in acc.size():
		acc[k] = float(acc[k]) / wsum
	return acc


## Style from the profile's (apt, m19, wood, han, modern) weights times `bias`,
## constrained by what fits the building.
func _pick(prof: Array, rng: RandomNumberGenerator, bias: Array, h: float, area: float) -> int:
	var wts := PackedFloat32Array()
	var styles := [S_APT, S_M19, S_WOOD, S_HAN, S_MOD]
	for k in 5:
		wts.append(float(prof[k]) * float(bias[k]))
	if h > 15.0 or area > 400.0:
		wts[2] = 0.0
	if h > 30.0:
		wts[1] = 0.0
	if area < 150.0:
		wts[3] *= 0.2
	if h > 24.0 or area > 1500.0:
		wts[4] *= 3.0
	var tot := 0.0
	for x in wts:
		tot += x
	var r := rng.randf() * tot
	for k in 5:
		r -= wts[k]
		if r <= 0.0:
			return styles[k]
	return S_APT


# ------------------------------------------------------------------ 2D helpers

static func _signed_area(p: PackedVector2Array) -> float:
	var a := 0.0
	var n := p.size()
	for k in n:
		a += p[k].cross(p[(k + 1) % n])
	return a * 0.5


## Drops near-duplicate and near-collinear vertices; CCW out.
static func _simplify(fp: PackedVector2Array) -> PackedVector2Array:
	var p := fp.duplicate()
	if _signed_area(p) < 0.0:
		p.reverse()
	for pass_ in 3:
		var n := p.size()
		if n <= 3:
			break
		var out := PackedVector2Array()
		var changed := false
		for k in n:
			var a: Vector2 = out[out.size() - 1] if out.size() > 0 else p[n - 1]
			var b := p[k]
			var c := p[(k + 1) % n]
			if b.distance_to(a) < 0.35:
				changed = true
				continue
			var ac := c - a
			var lac := ac.length()
			if lac > 0.01 and absf(ac.cross(b - a)) / lac < 0.15:
				changed = true
				continue
			out.append(b)
		if out.size() < 3 or not changed:
			if out.size() >= 3:
				p = out
			break
		p = out
	return p


## Min-area bounding rectangle over the edge directions: (width, length, angle).
static func _obb(p: PackedVector2Array) -> Vector3:
	var best := Vector3(0.0, 0.0, 0.0)
	var best_a := INF
	var n := p.size()
	for k in n:
		var e := p[(k + 1) % n] - p[k]
		if e.length() < 0.5:
			continue
		var t := e.normalized()
		var o := Vector2(-t.y, t.x)
		var t0 := INF
		var t1 := -INF
		var o0 := INF
		var o1 := -INF
		for v in p:
			var a := v.dot(t)
			var b := v.dot(o)
			t0 = minf(t0, a)
			t1 = maxf(t1, a)
			o0 = minf(o0, b)
			o1 = maxf(o1, b)
		var ar := (t1 - t0) * (o1 - o0)
		if ar < best_a:
			best_a = ar
			var w := minf(t1 - t0, o1 - o0)
			var l := maxf(t1 - t0, o1 - o0)
			var ang := atan2(t.y, t.x) if (t1 - t0) >= (o1 - o0) else atan2(o.y, o.x)
			best = Vector3(w, l, ang)
	return best


static func _rectish(p: PackedVector2Array) -> bool:
	for k in 4:
		var a := (p[(k + 1) % 4] - p[k]).normalized()
		var b := (p[(k + 2) % 4] - p[(k + 1) % 4]).normalized()
		if absf(a.dot(b)) > 0.2:
			return false
	return true


## Every edge moved d inward (d < 0: outward), miter joins. Empty if a miter
## runs away (a needle-sharp corner).
static func _offset(p: PackedVector2Array, d: float) -> PackedVector2Array:
	var n := p.size()
	var out := PackedVector2Array()
	out.resize(n)
	for k in n:
		var a := p[(k - 1 + n) % n]
		var b := p[k]
		var c := p[(k + 1) % n]
		var t0 := (b - a).normalized()
		var t1 := (c - b).normalized()
		var n0 := Vector2(-t0.y, t0.x)
		var n1 := Vector2(-t1.y, t1.x)
		var den := t0.cross(t1)
		var q: Vector2
		if absf(den) < 1e-3:
			q = b + n1 * d
		else:
			var p0 := b + n0 * d
			var p1 := b + n1 * d
			q = p0 + t0 * ((p1 - p0).cross(t1) / den)
		if q.distance_to(b) > absf(d) * 4.0 + 0.01:
			if d > 0.0:
				return PackedVector2Array()
			q = b + (q - b).normalized() * absf(d) * 2.0
		out[k] = q
	return out


## The inset ring is the outer ring's shrunk twin: no edge flipped, simple,
## inside the outer.
static func _inset_ok(p: PackedVector2Array, q: PackedVector2Array) -> bool:
	var n := p.size()
	if q.size() != n:
		return false
	for k in n:
		var e0 := p[(k + 1) % n] - p[k]
		var e1 := q[(k + 1) % n] - q[k]
		if e1.dot(e0) <= 0.0 or e1.length() < 0.02:
			return false
	if _signed_area(q) <= 0.0:
		return false
	for k in n:
		if not Geometry2D.is_point_in_polygon(q[k], p):
			return false
	if n > 3:
		for k in n:
			for j in range(k + 2, n):
				if k == 0 and j == n - 1:
					continue
				if Geometry2D.segment_intersects_segment(q[k], q[(k + 1) % n], q[j], q[(j + 1) % n]) != null:
					return false
	return true


## The deepest valid inset (d, ring) up to half the width, capped at 7.5 m.
static func _hip(p: PackedVector2Array, w: float) -> Array:
	var hi := minf(w * 0.5, 7.5)
	var q := _offset(p, hi * 0.985)
	if not q.is_empty() and _inset_ok(p, q):
		return [hi * 0.985, q]
	var lo := 0.0
	var best := PackedVector2Array()
	for it in 7:
		var mid := (lo + hi) * 0.5
		var r := _offset(p, mid)
		if not r.is_empty() and _inset_ok(p, r):
			lo = mid
			best = r
		else:
			hi = mid
	return [lo, best]


static func _edge_dist(p: PackedVector2Array, pt: Vector2) -> float:
	var d := INF
	var n := p.size()
	for k in n:
		var c := Geometry2D.get_closest_point_to_segment(pt, p[k], p[(k + 1) % n])
		d = minf(d, c.distance_to(pt))
	return d


static func _g(p: Vector2, y: float) -> Vector3:
	return Vector3(p.x, y, -p.y)


static func _up(a: Vector3, b: Vector3, c: Vector3) -> Vector3:
	var n := (b - a).cross(c - a).normalized()
	return -n if n.y < 0.0 else n


# ------------------------------------------------------------------ roofs

func _hip_roof(fp: PackedVector2Array, inner: PackedVector2Array, eave: float, top: float, tanp: float, ov: float,
		acc: Acc, style: int, roof_c: Color) -> void:
	var n := fp.size()
	var outer := _offset(fp, -ov)
	var y_o := eave - ov * tanp
	acc.surf(SF_TILE, roof_c)
	var b := Vector2(eave, top)
	for k in n:
		var k1 := (k + 1) % n
		var o0 := _g(outer[k], y_o)
		var o1 := _g(outer[k1], y_o)
		var i1 := _g(inner[k1], top)
		var i0 := _g(inner[k], top)
		var nn := _up(o0, o1, i1) if o0.distance_to(o1) > 0.01 else _up(o0, i1, i0)
		acc.quad(o0, o1, i1, i0, nn, Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, b, b, b, b)
	# A wide flat top (a big footprint hipped round its edge) is a concrete
	# terrace, not a field of tiles.
	if absf(_signed_area(inner)) > 30.0:
		acc.surf(SF_FLAT, Color(0.62, 0.61, 0.58))
	var tri := Geometry2D.triangulate_polygon(inner)
	for k in range(0, tri.size(), 3):
		acc.tri(_g(inner[tri[k]], top), _g(inner[tri[k + 1]], top), _g(inner[tri[k + 2]], top), Vector3.UP,
			Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, b)
		acc.st(_g(inner[tri[k]], top), _g(inner[tri[k + 1]], top), _g(inner[tri[k + 2]], top))
	for k in n:
		acc.sq(_g(fp[k], eave), _g(fp[(k + 1) % n], eave), _g(inner[(k + 1) % n], top), _g(inner[k], top))


## A gable roof on a near-rectangle: the ridge down the long axis, gable walls
## at the short ends. Returns the two long edges' indices (empty on failure).
func _gable(fp: PackedVector2Array, eave: float, top: float, tanp: float, ov: float, acc: Acc, style: int,
		roof_c: Color, gl: PackedFloat32Array, wall: Color) -> PackedInt32Array:
	var k0 := 0 if fp[1].distance_to(fp[0]) >= fp[2].distance_to(fp[1]) else 1
	var A := fp[k0]
	var B := fp[(k0 + 1) % 4]
	var C := fp[(k0 + 2) % 4]
	var D := fp[(k0 + 3) % 4]
	var ax := (B - A).normalized()
	var inn := Vector2(-ax.y, ax.x)
	var R1 := (D + A) * 0.5
	var R2 := (B + C) * 0.5
	var og := 0.3
	var y_o := eave - ov * tanp
	var b := Vector2(eave, top)
	acc.surf(SF_METAL if style == S_IND else SF_TILE, roof_c if style != S_IND else
		[Color(0.58, 0.60, 0.61), Color(0.55, 0.31, 0.24), Color(0.48, 0.55, 0.60)][int(acc.seed * 3.0) % 3])
	var a1 := _g(A - inn * ov - ax * og, y_o)
	var b1 := _g(B - inn * ov + ax * og, y_o)
	var r2 := _g(R2 + ax * og, top)
	var r1 := _g(R1 - ax * og, top)
	acc.quad(a1, b1, r2, r1, _up(a1, b1, r2), Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, b, b, b, b)
	var c1 := _g(C + inn * ov + ax * og, y_o)
	var d1 := _g(D + inn * ov - ax * og, y_o)
	acc.quad(c1, d1, r1, r2, _up(c1, d1, r1), Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, b, b, b, b)
	acc.sq(_g(A, eave), _g(B, eave), _g(R2, top), _g(R1, top))
	acc.sq(_g(C, eave), _g(D, eave), _g(R1, top), _g(R2, top))
	acc.st(_g(D, eave), _g(A, eave), _g(R1, top))
	acc.st(_g(B, eave), _g(C, eave), _g(R2, top))
	# Gable walls, in the wall's colour, on the short edges (D->A and B->C).
	acc.surf(SF_GABLE, wall)
	for pr: Array in [[D, A, R1, (k0 + 3) % 4], [B, C, R2, (k0 + 1) % 4]]:
		var p: Vector2 = pr[0]
		var q: Vector2 = pr[1]
		var r: Vector2 = pr[2]
		var e := q - p
		var L := e.length()
		var nn := Vector3(e.y, 0.0, e.x) / maxf(L, 0.01)
		acc.tri(_g(p, eave), _g(q, eave), _g(r, top), nn, Vector2(0.0, L), Vector2(L, L), Vector2(L * 0.5, L),
			Vector2(eave, gl[int(pr[3])]))
	return PackedInt32Array([k0, (k0 + 2) % 4])


## A flat roof with a parapet `ph` m high (0: none). Returns the kit's info.
func _flat_roof(fp: PackedVector2Array, top: float, h: float, acc: Acc, rc: Color, sf: int, ph: float) -> Dictionary:
	var ring := fp
	var y := top
	if ph > 0.0:
		var q := _offset(fp, 0.25)
		if not q.is_empty() and _inset_ok(fp, q):
			ring = q
			y = top - ph
			var n := q.size()
			for k in n:
				var p0 := q[k]
				var p1 := q[(k + 1) % n]
				var e := p1 - p0
				var L := e.length()
				if L < 0.05:
					continue
				var nn := -Vector3(e.y, 0.0, e.x) / L
				acc.surf(SF_PARAPET, rc)
				acc.quad(_g(p0, y), _g(p1, y), _g(p1, top), _g(p0, top), nn,
					Vector2(0.0, L), Vector2(L, L), Vector2(L, L), Vector2(0.0, L),
					Vector2(top, y), Vector2(top, y), Vector2(top, y), Vector2(top, y))
			# The coping: the strip between the wall's top and the inset.
			acc.surf(SF_PARAPET, rc)
			for k in n:
				var a := fp[k]
				var b := fp[(k + 1) % n]
				acc.quad(_g(a, top), _g(b, top), _g(q[(k + 1) % n], top), _g(q[k], top), Vector3.UP,
					Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, Vector2.ZERO,
					Vector2(top, top), Vector2(top, top), Vector2(top, top), Vector2(top, top))
	acc.surf(sf, rc)
	var tri := Geometry2D.triangulate_polygon(ring)
	var b2 := Vector2(top, y)
	for k in range(0, tri.size(), 3):
		acc.tri(_g(ring[tri[k]], y), _g(ring[tri[k + 1]], y), _g(ring[tri[k + 2]], y), Vector3.UP,
			Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, b2)
		acc.st(_g(ring[tri[k]], y), _g(ring[tri[k + 1]], y), _g(ring[tri[k + 2]], y))
	return {"type": "flat", "y": y, "ring": ring}


## A roofless shell: the walls' inner faces and a rubble floor.
func _ruin(fp: PackedVector2Array, top: float, g0: float, acc: Acc, wall: Color) -> Dictionary:
	var q := _offset(fp, 0.9)
	var fy := g0 + 0.4
	if q.is_empty() or not _inset_ok(fp, q):
		return _flat_roof(fp, top, top - g0, acc, Color(0.42, 0.44, 0.33), SF_FLOOR, 0.0)
	var n := q.size()
	acc.surf(SF_PARTY, wall)
	for k in n:
		var p0 := q[k]
		var p1 := q[(k + 1) % n]
		var e := p1 - p0
		var L := e.length()
		if L < 0.05:
			continue
		var nn := -Vector3(e.y, 0.0, e.x) / L
		acc.quad(_g(p0, fy), _g(p1, fy), _g(p1, top), _g(p0, top), nn,
			Vector2(0.0, L), Vector2(L, L), Vector2(L, L), Vector2(0.0, L),
			Vector2(top, g0), Vector2(top, g0), Vector2(top, g0), Vector2(top, g0))
	acc.surf(SF_FLAT, wall.darkened(0.1))
	for k in n:
		acc.quad(_g(fp[k], top), _g(fp[(k + 1) % n], top), _g(q[(k + 1) % n], top), _g(q[k], top), Vector3.UP,
			Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, Vector2.ZERO,
			Vector2(top, top), Vector2(top, top), Vector2(top, top), Vector2(top, top))
	acc.surf(SF_FLOOR, Color(0.44, 0.45, 0.34))
	var tri := Geometry2D.triangulate_polygon(q)
	for k in range(0, tri.size(), 3):
		acc.tri(_g(q[tri[k]], fy), _g(q[tri[k + 1]], fy), _g(q[tri[k + 2]], fy), Vector3.UP,
			Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, Vector2(top, fy))
	return {"type": "none", "y": fy, "ring": q}


## Lead domes on a flat lead roof: rows of small ones across a han or a
## bazaar (one central one when `single`), on low drums.
func _domes(info: Dictionary, single: bool, obb: Vector3, acc: Acc, rng: RandomNumberGenerator, cnt: Dictionary) -> void:
	var ring: PackedVector2Array = info["ring"]
	var y: float = info["y"]
	var c := Vector2.ZERO
	for p in ring:
		c += p
	c /= float(ring.size())
	var ang := obb.z
	var t := Vector2(cos(ang), sin(ang))
	var o := Vector2(-t.y, t.x)
	acc.surf(SF_LEAD, Color(0.64, 0.67, 0.69))
	if single:
		var r := minf(obb.x * 0.32, 9.0)
		if r > 1.5 and Geometry2D.is_point_in_polygon(c, ring) and _edge_dist(ring, c) > r:
			_dome(c, y, r, acc)
			_bump(cnt, "dome")
		return
	var sp := rng.randf_range(5.0, 6.5)
	var r := sp * 0.40
	var nx := int(obb.y / sp) + 1
	var ny := int(obb.x / sp) + 1
	var nd := 0
	for a in range(-nx, nx + 1):
		for b in range(-ny, ny + 1):
			var p := c + t * (float(a) * sp) + o * (float(b) * sp)
			if not Geometry2D.is_point_in_polygon(p, ring):
				continue
			if _edge_dist(ring, p) < r + 0.4:
				continue
			_dome(p, y, r, acc)
			nd += 1
			if nd > 400:
				return
	_bump(cnt, "dome", nd)


## A low drum and a shallow hemisphere (10 sides, 3 rings), smooth.
func _dome(p: Vector2, y: float, r: float, acc: Acc) -> void:
	var seg := 10
	var drum := r * 0.25
	var b := Vector2(y, y + drum + r * 0.8)
	var base := acc.pos.size()
	var rings := [[0.0, 1.0, 0.0], [drum, 1.0, 0.0]]
	for k in range(1, 4):
		var a := float(k) / 4.0 * PI * 0.5
		rings.append([drum + sin(a) * r * 0.8, cos(a), sin(a)])
	for rr: Array in rings:
		for s in seg:
			var th := TAU * float(s) / float(seg)
			var dx := cos(th)
			var dz := sin(th)
			var rad := r * float(rr[1])
			var nrm := Vector3(dx * float(rr[1]), float(rr[2]), dz * float(rr[1])).normalized() if float(rr[2]) > 0.0 \
				else Vector3(dx, 0.0, dz)
			acc.v(Vector3(p.x + dx * rad, y + float(rr[0]), -p.y + dz * rad), nrm, Vector2(th, 0.0), b)
	var apex := acc.v(Vector3(p.x, y + drum + r * 0.8, -p.y), Vector3.UP, Vector2.ZERO, b)
	for ri in rings.size() - 1:
		for s in seg:
			var a0 := base + ri * seg + s
			var a1 := base + ri * seg + (s + 1) % seg
			var b0 := a0 + seg
			var b1 := a1 + seg
			acc.idx.append_array(PackedInt32Array([a0, b1, a1, a0, b0, b1]))
	var last := base + (rings.size() - 1) * seg
	for s in seg:
		acc.idx.append_array(PackedInt32Array([last + s, apex, last + (s + 1) % seg]))


## The cumba: a closed bay jutting ~1 m over the street on the upper floors,
## centred on the longest street face.
func _cumba(fp: PackedVector2Array, party: PackedByteArray, gl: PackedFloat32Array, eave_ref: float, wall_top: float,
		style: int, storey: float, flags: int, acc: Acc, rng: RandomNumberGenerator, wall: Color, top_c: Color, cnt: Dictionary) -> void:
	var n := fp.size()
	var best := -1
	var best_l := 4.5
	for k in n:
		if party[k] != 0:
			continue
		var L := fp[k].distance_to(fp[(k + 1) % n])
		if L > best_l:
			best_l = L
			best = k
	if best < 0:
		return
	var p := fp[best]
	var q := fp[(best + 1) % n]
	var t := (q - p) / best_l
	var o := Vector2(t.y, -t.x)
	var width := minf(best_l * 0.6, rng.randf_range(3.0, 5.5)) if style != S_APT else best_l * rng.randf_range(0.55, 0.85)
	var depth := rng.randf_range(0.8, 1.1) if style != S_APT else rng.randf_range(0.9, 1.3)
	var u0 := (best_l - width) * 0.5
	if style == S_WOOD and rng.randf() < 0.3:
		u0 = rng.randf_range(0.3, best_l - width - 0.3) if best_l - width > 0.6 else u0
	var g := (gl[best] + gl[(best + 1) % n]) * 0.5
	var tb := 0.9 if (flags & F_CORNICE) else 0.35
	if style == S_WOOD:
		tb = 0.3
	var ytg := eave_ref - tb
	var gfh := maxf(storey * 1.3, 4.0) if (flags & F_SHOPS) else storey * 1.1
	var rows := floorf((ytg - g - gfh) / storey + 0.5)
	if rows < 2.0:
		return
	var y0 := ytg - rows * storey
	var y1 := wall_top - 0.15
	if y1 - y0 < storey * 1.5:
		return
	var a := p + t * u0
	var b := p + t * (u0 + width)
	var a2 := a + o * depth
	var b2 := b + o * depth
	var gg := Vector2(eave_ref, g)
	acc.surf(SF_CUMBA, wall)
	# Front and the two sides (they carry windows too), each laid out from its own corner.
	for f: Array in [[a2, b2, o], [a, a2, -t], [b2, b, t]]:
		var f0: Vector2 = f[0]
		var f1: Vector2 = f[1]
		var nv: Vector2 = f[2]
		var L := f0.distance_to(f1)
		acc.quad(_g(f0, y0), _g(f1, y0), _g(f1, y1), _g(f0, y1), Vector3(nv.x, 0.0, -nv.y),
			Vector2(0.0, L), Vector2(L, L), Vector2(L, L), Vector2(0.0, L), gg, gg, gg, gg)
	# Underside (the corbelled soffit) and top.
	acc.surf(SF_PARAPET, wall.darkened(0.25))
	acc.quad(_g(a, y0), _g(b, y0), _g(b2, y0), _g(a2, y0), Vector3.DOWN,
		Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, Vector2(y0, y0 - 1.0), Vector2(y0, y0 - 1.0),
		Vector2(y0, y0 - 1.0), Vector2(y0, y0 - 1.0))
	acc.surf(SF_TILE if style == S_WOOD else SF_FLAT, top_c)
	var yt := y1 + 0.08
	acc.quad(_g(a - t * 0.1, yt), _g(b + t * 0.1, yt), _g(b2 + t * 0.1 + o * 0.15, yt - 0.25), _g(a2 - t * 0.1 + o * 0.15, yt - 0.25),
		_up(_g(a, yt), _g(b, yt), _g(b2 + o * 0.15, yt - 0.25)),
		Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, Vector2(yt - 0.3, yt), Vector2(yt - 0.3, yt),
		Vector2(yt - 0.3, yt), Vector2(yt - 0.3, yt))
	_bump(cnt, "cumba")


# ------------------------------------------------------------------ rooftop kit

func _roof_y(info: Dictionary, p: Vector2) -> float:
	match str(info["type"]):
		"hip":
			var ring: PackedVector2Array = info["ring"]
			return minf(float(info["eave"]) + float(info["tanp"]) * _edge_dist(ring, p), float(info["top"]))
		"gable":
			var ring: PackedVector2Array = info["ring"]
			var ax: PackedInt32Array = info["axis"]
			var d := INF
			for k in ax:
				var c := Geometry2D.get_closest_point_to_segment_uncapped(p, ring[k], ring[(k + 1) % 4])
				d = minf(d, c.distance_to(p))
			return minf(float(info["eave"]) + float(info["tanp"]) * d, float(info["top"]))
	return float(info["y"])


## A free spot inside the roof ring, `margin` from its edges, clear of `used`.
static func _spot(ring: PackedVector2Array, used: Array, margin: float, size: float, rng: RandomNumberGenerator) -> Vector2:
	var e0 := INF
	var n0 := INF
	var e1 := -INF
	var n1 := -INF
	for p in ring:
		e0 = minf(e0, p.x)
		n0 = minf(n0, p.y)
		e1 = maxf(e1, p.x)
		n1 = maxf(n1, p.y)
	for k in 8:
		var p := Vector2(rng.randf_range(e0, e1), rng.randf_range(n0, n1))
		if not Geometry2D.is_point_in_polygon(p, ring):
			continue
		if _edge_dist(ring, p) < margin:
			continue
		var ok := true
		for u: Vector3 in used:
			if Vector2(u.x, u.y).distance_to(p) < u.z + size:
				ok = false
				break
		if ok:
			used.append(Vector3(p.x, p.y, size))
			return p
	return Vector2.INF


func _put(kit: Dictionary, part: String, p: Vector2, base: float, size: Vector3, yaw: float, col: Color, kind: int, sd: float) -> void:
	if not kit.has(part):
		kit[part] = [[], [], []]
	var d: Array = kit[part]
	var xf := Transform3D(Basis(Vector3.UP, yaw) * Basis.from_scale(size), Vector3(p.x, base + size.y * 0.5, -p.y))
	(d[0] as Array).append(xf)
	(d[1] as Array).append(col)
	(d[2] as Array).append(Color(float(kind), sd, size.x, size.z))


func _roof_kit(i: int, fp: PackedVector2Array, info: Dictionary, style: int, area: float, h: float, wall: Color,
		kit: Dictionary, cnt: Dictionary, rng: RandomNumberGenerator) -> void:
	var typ := str(info["type"])
	if typ == "none" or h < 3.5 or style == S_RUIN:
		return
	var ring: PackedVector2Array = info["ring"]
	var used: Array = []
	var ob := _obb(fp)
	var yaw_b := ob.z            # the building's axis (ENU angle); Godot yaw = that angle about +y
	# Godot: rotating about +y by yaw maps +x to (cos, 0, -sin): ENU angle a -> yaw a.
	var south_yaw := yaw_b
	# Edge-aligned orientation nearest due south (panels face +z = south at yaw 0).
	var best := INF
	for k in 4:
		var yy := yaw_b + float(k) * PI * 0.5
		var dz := cos(yy)            # the mesh's +z after the yaw, as z
		if -dz < best:
			best = -dz
			south_yaw = yy
	var tank_cols := [Color(0.88, 0.88, 0.85), Color(0.80, 0.81, 0.80), Color(0.36, 0.52, 0.72), Color(0.64, 0.66, 0.67),
		Color(0.90, 0.90, 0.88), Color(0.18, 0.18, 0.19)]
	if typ == "flat":
		var y: float = info["y"]
		var big := area > 90.0
		# Stair / lift hut.
		if h > 9.0 and area > 70.0 and rng.randf() < 0.55:
			var s := Vector3(rng.randf_range(2.4, 3.4), rng.randf_range(2.3, 2.8), rng.randf_range(2.6, 4.0))
			var p := _spot(ring, used, 1.6, 1.8, rng)
			if p != Vector2.INF:
				_put(kit, "box", p, y, s, yaw_b, wall.darkened(0.05), K_HUT, rng.randf())
				_bump(cnt, "hut")
		# Water tanks (depo): one or many, on little stands.
		var nt := clampi(int(area / 140.0) + rng.randi_range(0, 2), 0, 7) if rng.randf() < 0.85 else 0
		for k in nt:
			var p := _spot(ring, used, 0.8, 1.1, rng)
			if p == Vector2.INF:
				continue
			var dia := rng.randf_range(0.9, 1.3)
			var col: Color = tank_cols[rng.randi() % tank_cols.size()]
			if rng.randf() < 0.75:
				_put(kit, "tank", p, y + 0.25, Vector3(rng.randf_range(1.6, 2.4), dia, dia), yaw_b + (PI * 0.5 if rng.randf() < 0.5 else 0.0), col, K_TANK, 0.0)
			else:
				_put(kit, "tank", p, y, Vector3(dia * 1.2, dia * 1.3, dia * 1.2), yaw_b, col, K_TANK, 1.0)
			_bump(cnt, "tank")
		# Solar water heaters, facing south.
		if rng.randf() < 0.6:
			for k in clampi(int(area / 110.0) + rng.randi_range(0, 1), 1, 6):
				var p := _spot(ring, used, 1.0, 1.3, rng)
				if p == Vector2.INF:
					continue
				_put(kit, "solar", p, y, Vector3(rng.randf_range(1.8, 2.2), 1.3, 1.5), south_yaw,
					tank_cols[rng.randi() % 3], K_SOLAR, rng.randf())
				_bump(cnt, "solar")
		# Dishes, lots of them, at Turksat (SSE, ~40 deg up).
		for k in rng.randi_range(0, 4 if big else 2):
			var p := _spot(ring, used, 0.5, 0.5, rng)
			if p == Vector2.INF:
				continue
			_put(kit, "dish", p, y, Vector3.ONE * rng.randf_range(0.7, 1.1), deg_to_rad(25.0 + rng.randf_range(-8.0, 8.0)),
				Color(0.88, 0.88, 0.86).darkened(rng.randf() * 0.2), K_DISH, 0.0)
			_bump(cnt, "dish")
		# AC condensers.
		for k in rng.randi_range(0, 3 if big else 1):
			var p := _spot(ring, used, 0.4, 0.5, rng)
			if p != Vector2.INF:
				_put(kit, "box", p, y, Vector3(0.85, 0.6, 0.35), yaw_b, Color(0.84, 0.84, 0.82), K_AC, 0.0)
				_bump(cnt, "ac")
		# Flues and chimneys.
		for k in rng.randi_range(0, 2):
			var p := _spot(ring, used, 0.3, 0.3, rng)
			if p != Vector2.INF:
				_put(kit, "box", p, y, Vector3(0.45, rng.randf_range(0.8, 1.6), 0.45), yaw_b, wall.darkened(0.1), K_CHIMNEY, 0.0)
				_bump(cnt, "chimney")
		if rng.randf() < 0.25:
			var p := _spot(ring, used, 0.4, 0.3, rng)
			if p != Vector2.INF:
				_put(kit, "mast", p, y, Vector3(1.0, rng.randf_range(2.0, 3.5), 1.0), rng.randf() * TAU, Color(0.45, 0.45, 0.46), K_MAST, 0.0)
	else:
		# Pitched: chimneys (brick or plastered), a dish or two, the odd solar
		# heater on the south slope, a TV aerial.
		var chim := Color(0.58, 0.33, 0.24) if rng.randf() < 0.5 else wall.darkened(0.08)
		for k in rng.randi_range(1, 3 if area > 150.0 else 2):
			var p := _spot(ring, used, 0.9, 0.4, rng)
			if p == Vector2.INF:
				continue
			var yb := _roof_y(info, p)
			_put(kit, "box", p, yb - 0.6, Vector3(rng.randf_range(0.45, 0.7), rng.randf_range(1.5, 2.2), rng.randf_range(0.45, 0.9)),
				yaw_b, chim, K_CHIMNEY, 0.0)
			_bump(cnt, "chimney")
		for k in rng.randi_range(0, 2):
			var p := _spot(ring, used, 1.0, 0.5, rng)
			if p == Vector2.INF:
				continue
			_put(kit, "dish", p, _roof_y(info, p) - 0.2, Vector3.ONE * rng.randf_range(0.7, 1.0), deg_to_rad(25.0 + rng.randf_range(-8.0, 8.0)),
				Color(0.88, 0.88, 0.86), K_DISH, 0.0)
			_bump(cnt, "dish")
		if rng.randf() < 0.22 and area > 70.0:
			var p := _spot(ring, used, 1.4, 1.2, rng)
			if p != Vector2.INF:
				_put(kit, "solar", p, _roof_y(info, p) - 0.5, Vector3(2.0, 1.3, 1.5), south_yaw,
					tank_cols[rng.randi() % 3], K_SOLAR, rng.randf())
				_bump(cnt, "solar")
		if rng.randf() < 0.3:
			var p := _spot(ring, used, 1.5, 0.3, rng)
			if p != Vector2.INF:
				_put(kit, "mast", p, _roof_y(info, p) - 0.3, Vector3(1.0, rng.randf_range(2.0, 3.0), 1.0), rng.randf() * TAU,
					Color(0.45, 0.45, 0.46), K_MAST, 0.0)


func _build_kit(kits: Dictionary) -> void:
	var root := Node3D.new()
	root.name = "rooftops"
	add_child(root)
	var meshes := _kit_meshes()
	for part: String in kits:
		var chunks: Dictionary = kits[part]
		for k: Vector2i in chunks:
			var d: Array = chunks[k]
			var xf: Array = d[0]
			if xf.is_empty():
				continue
			var mm := MultiMesh.new()
			mm.transform_format = MultiMesh.TRANSFORM_3D
			mm.use_colors = true
			mm.use_custom_data = true
			mm.mesh = meshes[part]
			mm.instance_count = xf.size()
			var cols: Array = d[1]
			var cus: Array = d[2]
			for j in xf.size():
				mm.set_instance_transform(j, xf[j])
				mm.set_instance_color(j, cols[j])
				mm.set_instance_custom_data(j, cus[j])
			var mmi := MultiMeshInstance3D.new()
			mmi.name = "%s_%d_%d" % [part, k.x, k.y]
			mmi.multimesh = mm
			mmi.material_override = kit_mat
			mmi.visibility_range_end = KIT_RANGE
			mmi.visibility_range_end_margin = 100.0
			mmi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
			mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
			root.add_child(mmi)
			if part in ["box", "tank"]:
				var sh := MultiMeshInstance3D.new()
				sh.name = mmi.name + "_shadow"
				sh.multimesh = mm
				sh.material_override = kit_mat
				sh.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_SHADOWS_ONLY
				sh.visibility_range_end = SHADOW_M
				root.add_child(sh)


## Unit meshes standing in y -0.5..0.5, facing +z (south at yaw 0).
static func _kit_meshes() -> Dictionary:
	var box := BoxMesh.new()
	box.size = Vector3.ONE
	var cyl := CylinderMesh.new()
	cyl.top_radius = 0.5
	cyl.bottom_radius = 0.5
	cyl.height = 1.0
	cyl.radial_segments = 8
	cyl.rings = 1
	# Tank: a cylinder whose axis is x (the instance flags upright ones by
	# custom.y = 1 and the shader does not care which).
	var st := SurfaceTool.new()
	st.append_from(cyl, 0, Transform3D(Basis(Vector3(0, 0, 1), PI * 0.5), Vector3.ZERO))
	var tank := st.commit()
	# Solar heater: the tilted collector, the tank across its top, a frame.
	st = SurfaceTool.new()
	st.append_from(box, 0, Transform3D(Basis(Vector3(1, 0, 0), deg_to_rad(40.0)) * Basis.from_scale(Vector3(1.0, 0.04, 0.9)),
		Vector3(0.0, -0.15, 0.08)))
	st.append_from(cyl, 0, Transform3D(Basis(Vector3(0, 0, 1), PI * 0.5) * Basis.from_scale(Vector3(0.34, 1.0, 0.34)),
		Vector3(0.0, 0.30, -0.36)))
	for x: float in [-0.45, 0.45]:
		st.append_from(box, 0, Transform3D(Basis.from_scale(Vector3(0.03, 0.75, 0.03)), Vector3(x, -0.12, -0.36)))
	var solar := st.commit()
	# Dish: a shallow disc tilted to face +z and up 40 deg, on an arm.
	st = SurfaceTool.new()
	var disc := CylinderMesh.new()
	disc.top_radius = 0.5
	disc.bottom_radius = 0.42
	disc.height = 0.08
	disc.radial_segments = 8
	disc.rings = 1
	st.append_from(disc, 0, Transform3D(Basis(Vector3(1, 0, 0), deg_to_rad(50.0)), Vector3(0.0, 0.1, 0.0)))
	st.append_from(box, 0, Transform3D(Basis.from_scale(Vector3(0.05, 0.6, 0.05)), Vector3(0.0, -0.2, -0.1)))
	var dish := st.commit()
	# Mast: a pole with two cross bars (a TV aerial).
	st = SurfaceTool.new()
	st.append_from(box, 0, Transform3D(Basis.from_scale(Vector3(0.04, 1.0, 0.04)), Vector3.ZERO))
	st.append_from(box, 0, Transform3D(Basis.from_scale(Vector3(0.9, 0.03, 0.03)), Vector3(0.0, 0.42, 0.0)))
	st.append_from(box, 0, Transform3D(Basis.from_scale(Vector3(0.6, 0.03, 0.03)), Vector3(0.0, 0.28, 0.0)))
	var mast := st.commit()
	return {"box": box, "tank": tank, "solar": solar, "dish": dish, "mast": mast}


# ================================================================== shaders

const BUILDING_SHADER := """
render_mode cull_disabled, diffuse_burley;
uniform sampler2DArray cells_mask : filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2DArray cells_col : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform float night = 0.0;
uniform float home_occ = 1.0;
uniform float office_occ = 0.3;
uniform float shop_occ = 1.0;

varying vec3 v_world;
varying vec3 v_n;
varying vec4 v_col;
varying vec4 v_cus;
varying vec2 v_uv;
varying vec2 v_uv2;

void vertex() {
    v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
    v_n = (MODEL_MATRIX * vec4(NORMAL, 0.0)).xyz;
    v_col = COLOR;
    v_cus = CUSTOM0;
    v_uv = UV;
    v_uv2 = UV2;
}

float hh(vec4 p) {
    p = fract(p * vec4(0.1031, 0.1030, 0.0973, 0.1099));
    p += dot(p, p.wzxy + 33.33);
    return fract((p.x + p.y) * (p.z + p.w));
}
float hs(float seed, float k) { return hh(vec4(seed * 1000.0, k, 7.0, 3.0)); }
float bit(float flags, float b) { return mod(floor(flags / b + 0.001), 2.0); }
vec3 lin(vec3 c) { return pow(max(c, vec3(0.0)), vec3(2.2)); }
float vnoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    float a = hh(vec4(i, 1.3, 7.7));
    float b = hh(vec4(i + vec2(1.0, 0.0), 1.3, 7.7));
    float c = hh(vec4(i + vec2(0.0, 1.0), 1.3, 7.7));
    float d = hh(vec4(i + vec2(1.0, 1.0), 1.3, 7.7));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
float fnoise(vec2 p, float period, float fw) {
    return mix(vnoise(p / period), 0.5, smoothstep(0.15, 0.45, fw / period));
}
float aaline(float v, float w) {
    float fw = fwidth(v);
    float d = abs(fract(v + 0.5) - 0.5);
    float l = 1.0 - smoothstep(w * 0.5 - fw, w * 0.5 + fw, d);
    return mix(l, w, smoothstep(w * 0.5, w * 2.0, fw));
}

// Trim: white PVC, cream stone, brown wood, dark green, aluminium, brick,
// grey stone, marble, dark grey, oxblood, ochre, pale blue, anthracite,
// beige, terracotta, oak.
const vec3 TRIM[16] = vec3[16](vec3(0.92, 0.92, 0.90), vec3(0.84, 0.80, 0.70), vec3(0.35, 0.22, 0.14),
    vec3(0.16, 0.26, 0.20), vec3(0.62, 0.63, 0.64), vec3(0.62, 0.36, 0.25), vec3(0.66, 0.64, 0.60),
    vec3(0.88, 0.87, 0.83), vec3(0.25, 0.25, 0.26), vec3(0.45, 0.18, 0.14), vec3(0.78, 0.62, 0.38),
    vec3(0.62, 0.70, 0.76), vec3(0.20, 0.21, 0.22), vec3(0.80, 0.74, 0.63), vec3(0.70, 0.42, 0.30),
    vec3(0.52, 0.36, 0.20));
// Shop signs and awnings.
const vec3 SHOP[10] = vec3[10](vec3(0.70, 0.12, 0.10), vec3(0.10, 0.32, 0.20), vec3(0.08, 0.20, 0.45),
    vec3(0.90, 0.88, 0.82), vec3(0.85, 0.65, 0.10), vec3(0.15, 0.15, 0.16), vec3(0.80, 0.40, 0.12),
    vec3(0.25, 0.50, 0.60), vec3(0.55, 0.12, 0.30), vec3(0.50, 0.50, 0.50));

const float L_APT_DARK = 0.0;
const float L_APT_TUL = 1.0;
const float L_APT_SHUT = 2.0;
const float L_APT_AC = 3.0;
const float L_BAL_SOLID = 4.0;
const float L_BAL_RAIL = 5.0;
const float L_BAL_LAUNDRY = 6.0;
const float L_BAL_CLOSED = 7.0;
const float L_M19 = 8.0;
const float L_M19_SHUT = 9.0;
const float L_M19_BAL = 10.0;
const float L_M19_ARCH = 11.0;
const float L_WOOD = 12.0;
const float L_WOOD_KAFES = 13.0;
const float L_WOOD_BLANK = 14.0;
const float L_HAN = 15.0;
const float L_HAN_BLANK = 16.0;
const float L_RIBBON = 17.0;
const float L_CURTAIN = 18.0;
const float L_PUB = 19.0;
const float L_SHOP = 20.0;
const float L_SHOP_SHUT = 21.0;
const float L_SHOP_AWN = 22.0;
const float L_ENTRANCE = 23.0;
const float L_PLINTH = 24.0;
const float L_WOOD_DOOR = 25.0;
const float L_HAN_PORTAL = 26.0;
const float L_BLANK = 27.0;
const float L_CORNICE = 28.0;
const float L_PARTY_BRICK = 29.0;
const float L_PARTY_PLASTER = 30.0;
const float L_EAVE = 31.0;
const float L_IND = 32.0;
const float L_APT_BARS = 33.0;

const int S_APT = 0;
const int S_M19 = 1;
const int S_WOOD = 2;
const int S_HAN = 3;
const int S_MOD = 4;
const int S_PUB = 5;
const int S_IND = 6;
const int S_SMALL = 7;
const int S_RUIN = 8;

float base_layer(int s) {
    if (s == S_APT) return L_APT_TUL;
    if (s == S_M19) return L_M19;
    if (s == S_WOOD) return L_WOOD;
    if (s == S_HAN || s == S_RUIN) return L_HAN_BLANK;
    if (s == S_MOD) return L_RIBBON;
    if (s == S_PUB) return L_PUB;
    if (s == S_IND) return L_IND;
    return L_BLANK;
}
float bay_width(int s, float seed) {
    float k = hs(seed, 6.0);
    if (s == S_APT) return mix(2.9, 3.6, k);
    if (s == S_M19) return mix(2.2, 2.7, k);
    if (s == S_WOOD) return mix(2.4, 2.9, k);
    if (s == S_HAN) return mix(2.8, 3.4, k);
    if (s == S_MOD) return mix(2.8, 3.4, k);
    if (s == S_PUB) return mix(3.0, 3.6, k);
    if (s == S_IND) return 5.0;
    return 3.0;
}

void fragment() {
    float seed = v_cus.x;
    float sy = v_cus.y + 0.01;
    int style = int(mod(sy, 16.0));
    int surf = int(floor(sy / 16.0));
    float storey = max(v_cus.z, 2.4);
    float flags = v_cus.w;
    vec3 basec = lin(v_col.rgb);
    vec3 nw = normalize(v_n);
    if (!FRONT_FACING) { nw = -nw; }
    vec3 c = vec3(0.5);
    float rough = 0.9;
    float metal = 0.0;
    float spec = 0.4;
    vec3 emit = vec3(0.0);
    float glass_frac = 0.0;
    float absorb = 0.65;
    float mass = 0.6;
    float veg = 0.0;
    vec2 pw = v_world.xz;
    float fw = length(fwidth(pw)) + fwidth(v_world.y);
    bool roofish = surf == 2 || surf == 3 || surf == 4 || surf == 8 || surf == 9;

    if (roofish && !FRONT_FACING) {
        // The underside of the eaves: painted boards or plaster, in shade.
        c = lin(style == S_WOOD ? vec3(0.42, 0.33, 0.24) : vec3(0.62, 0.60, 0.56));
        rough = 0.9;
    } else if (surf == 2) {
        // Turkish (alaturka) barrel tiles: channels down the slope, courses
        // across it, each tile its own shade; moss and soot, re-laid patches;
        // the gutter dark, the ridge capped.
        vec2 sl = nw.xz;
        float sll = length(sl);
        vec2 down = sll > 0.05 ? sl / sll : vec2(1.0, 0.0);
        vec2 across = vec2(-down.y, down.x);
        float s = dot(pw, across);
        float t = v_world.y / max(sll, 0.3);
        float fws = fwidth(s);
        float fwt = fwidth(t);
        float ch = 0.5 + 0.5 * cos(6.2832 * s / 0.22);
        ch = mix(ch, 0.5, smoothstep(0.04, 0.12, fws));
        float course = aaline(t / 0.38, 0.10);
        float tid = hh(vec4(floor(s / 0.22), floor(t / 0.38), seed * 100.0, 3.0));
        tid = mix(tid, 0.5, smoothstep(0.06, 0.2, max(fws, fwt)));
        c = basec * (0.74 + 0.40 * ch) * (1.0 - 0.28 * course) * (0.82 + 0.36 * tid);
        float n1 = fnoise(pw + seed * 131.0, 3.5, fw);
        float n2 = fnoise(pw + seed * 57.0, 1.2, fw);
        float n3 = fnoise(pw + seed * 17.0, 9.0, fw);
        float age = hs(seed, 4.0);
        c = mix(c, c * vec3(0.55, 0.55, 0.50), smoothstep(0.5, 0.85, n1) * (0.3 + 0.6 * age));
        c = mix(c, lin(vec3(0.33, 0.33, 0.24)), smoothstep(0.7, 0.92, n2) * 0.45 * age);
        c = mix(c, lin(vec3(0.80, 0.44, 0.27)), smoothstep(0.72, 0.9, n3) * 0.5 * hs(seed, 5.0));
        float above = v_world.y - v_uv2.x;
        c *= mix(0.55, 1.0, smoothstep(-0.35, -0.1, above));
        float ridge = smoothstep(v_uv2.y - 0.16, v_uv2.y - 0.06, v_world.y);
        c = mix(c, basec * 0.78, ridge * 0.7);
        rough = 0.82; spec = 0.35; absorb = 0.72; mass = 0.45;
    } else if (surf == 3 || surf == 8) {
        // Flat concrete roofs: screed, stains where water stands, patched
        // bitumen; a ruin's floor is rubble and weeds.
        float n1 = fnoise(pw + seed * 311.0, 5.0, fw);
        float n2 = fnoise(pw + seed * 97.0, 1.3, fw);
        c = basec * (0.90 + 0.14 * n1) * (0.93 + 0.12 * n2);
        c = mix(c, c * 0.78, smoothstep(0.62, 0.86, fnoise(pw + seed * 41.0, 3.0, fw)) * 0.6 * hs(seed, 9.0));
        if (surf == 8) { c = mix(c, lin(vec3(0.30, 0.36, 0.20)), smoothstep(0.4, 0.7, n2) * 0.7); veg = 0.6; }
        rough = 0.95; absorb = 0.7; mass = 0.8;
    } else if (surf == 4) {
        // Lead sheet: grey-blue, dull, its seams, a white bloom in streaks.
        float n1 = fnoise(pw + seed * 71.0, 2.0, fw);
        c = basec * (0.85 + 0.25 * n1);
        c *= 1.0 - 0.15 * aaline((pw.x + pw.y) / 0.62, 0.06);
        rough = 0.6; metal = 0.12; absorb = 0.5; mass = 0.25;
    } else if (surf == 9) {
        vec2 sl = nw.xz;
        float sll = length(sl);
        vec2 down = sll > 0.05 ? sl / sll : vec2(1.0, 0.0);
        float s = dot(pw, vec2(-down.y, down.x));
        c = basec * (0.85 + 0.2 * aaline(s / 0.2, 0.3)) * (0.85 + 0.25 * fnoise(pw + seed * 13.0, 4.0, fw));
        rough = 0.5; metal = 0.45; absorb = 0.55; mass = 0.15;
    } else if (surf == 5) {
        // Parapet inside, coping, bay soffit: wall colour, a dark bitumen
        // upstand at the bottom, a pale coping on top.
        c = basec * 0.78;
        if (nw.y > 0.5) { c = lin(vec3(0.72, 0.71, 0.68)); }
        else if (nw.y < -0.5) { c = basec * 0.6; }
        else { c = mix(lin(vec3(0.12, 0.12, 0.12)), c, smoothstep(0.25, 0.35, v_world.y - v_uv2.y)); }
        rough = 0.9;
    } else {
        // ---- walls: the facade cells
        float u = v_uv.x;
        float L = max(v_uv.y, 0.1);
        float eave = v_uv2.x;
        float gl = v_uv2.y;
        float y = v_world.y;
        bool shops = bit(flags, 1.0) > 0.5;
        bool awn = bit(flags, 2.0) > 0.5;
        bool cornice = bit(flags, 4.0) > 0.5;
        bool balc = bit(flags, 8.0) > 0.5;
        bool closed = bit(flags, 16.0) > 0.5;
        bool shut = bit(flags, 32.0) > 0.5;
        bool office = bit(flags, 64.0) > 0.5;
        bool acu = bit(flags, 128.0) > 0.5;
        vec3 trim = lin(TRIM[int(mod(floor(flags / 256.0 + 0.001), 16.0))]);
        float fk = hh(vec4(seed * 1000.0, floor(L * 10.0), floor(nw.x * 8.0 + 8.5), floor(nw.z * 8.0 + 8.5)));
        bool cumba = surf == 7;
        float tb = cornice ? 0.9 : 0.35;
        if (style == S_WOOD) { tb = 0.3; }
        float ytg = eave - tb;
        float gfh = shops ? max(storey * 1.3, 4.0) : storey * 1.1;
        if (style == S_HAN || style == S_IND) { gfh = storey; }
        float nrow = max(0.0, floor((ytg - gl - gfh) / storey + 0.5));
        float gf_top = ytg - nrow * storey;
        float bay = bay_width(style, seed);
        float zone;           // 0 ground, 1 upper, 2 top band, 3 plinth below the street floor
        float cell_h;
        float ycell;
        float fi = 0.0;
        if (y > ytg) { zone = 2.0; cell_h = tb; ycell = y - ytg; bay = 1.0; }
        else if (y >= gf_top || cumba) { zone = 1.0; float q = (ytg - y) / storey; fi = floor(q); cell_h = storey; ycell = (1.0 - fract(q)) * storey; }
        else if (y >= gf_top - gfh) { zone = 0.0; cell_h = gfh; ycell = y - (gf_top - gfh); if (shops) { bay = mix(4.2, 6.0, hs(seed, 40.0)); } }
        else { zone = 3.0; cell_h = storey; ycell = mod(y - (gf_top - gfh), storey); }
        float nb = max(1.0, floor(L / bay + 0.5));
        float bw = L / nb;
        vec2 cuv = vec2(u / bw, ycell / cell_h);
        vec2 dx = vec2(dFdx(u) / bw, dFdx(y) / cell_h);
        vec2 dy = vec2(dFdy(u) / bw, dFdy(y) / cell_h);
        float bi = clamp(floor(cuv.x), 0.0, nb - 1.0);
        vec2 f = vec2(cuv.x - bi, clamp(cuv.y, 0.0, 1.0));
        float r = hh(vec4(seed * 1000.0, fi, bi, fk * 97.0));
        float colr = hh(vec4(seed * 1000.0, bi, fk * 53.0, 11.0));
        vec3 tcol = trim;
        float layer = L_BLANK;
        float avg_l = base_layer(style);
        bool party = surf == 1;
        bool tiny = L < 1.6;
        if (party) {
            layer = hs(seed, 31.0) < 0.35 ? L_PARTY_BRICK : L_PARTY_PLASTER;
            avg_l = layer;
            if (style == S_HAN || style == S_RUIN) { layer = L_HAN_BLANK; avg_l = layer; }
            if (style == S_WOOD) { layer = L_WOOD_BLANK; avg_l = layer; }
        } else if (surf == 6 || tiny) {
            layer = style == S_WOOD ? L_WOOD_BLANK : (style == S_HAN || style == S_RUIN ? L_HAN_BLANK : (style == S_IND ? L_IND : L_BLANK));
            avg_l = layer;
        } else if (zone == 2.0) {
            layer = cornice ? L_CORNICE : (style == S_HAN ? L_HAN_BLANK : (style == S_WOOD ? L_WOOD_BLANK : L_EAVE));
            avg_l = layer;
        } else if (zone == 3.0) {
            layer = style == S_WOOD || style == S_APT || style == S_SMALL ? L_PLINTH : L_HAN_BLANK;
            if (style == S_M19 || style == S_PUB) { layer = L_PLINTH; }
            avg_l = layer;
        } else if (zone == 0.0) {
            float dbi = floor(nb * hs(seed, 14.0 + fk * 10.0));
            bool door = abs(bi - dbi) < 0.5;
            if (shops) {
                float sr = hh(vec4(seed * 1000.0, bi, fk * 31.0, 5.0));
                layer = door && nb > 2.0 ? L_ENTRANCE : (awn && sr < 0.5 ? L_SHOP_AWN : (sr < 0.82 ? L_SHOP : L_SHOP_SHUT));
                tcol = lin(SHOP[int(floor(hh(vec4(seed * 1000.0, bi, fk * 7.0, 9.0)) * 9.99))]);
                avg_l = L_SHOP;
            } else if (style == S_WOOD) {
                layer = door ? L_WOOD_DOOR : (hs(seed, 15.0) < 0.5 ? L_PLINTH : L_WOOD);
                avg_l = L_WOOD;
            } else if (style == S_HAN || style == S_RUIN) {
                layer = door && style == S_HAN ? L_HAN_PORTAL : L_HAN_BLANK;
                avg_l = L_HAN_BLANK;
            } else if (style == S_M19) {
                layer = door ? L_ENTRANCE : (r < 0.6 ? L_M19_ARCH : L_PLINTH);
            } else if (style == S_MOD) {
                layer = door ? L_ENTRANCE : L_RIBBON;
            } else if (style == S_PUB) {
                layer = door ? L_ENTRANCE : L_PUB;
            } else if (style == S_IND) {
                layer = door ? L_SHOP_SHUT : L_IND;
            } else if (style == S_SMALL) {
                layer = door ? L_SHOP_SHUT : L_BLANK;
            } else {
                layer = door ? L_ENTRANCE : (r < 0.55 ? L_APT_BARS : L_APT_TUL);
                avg_l = L_APT_TUL;
            }
        } else {
            // Upper floors.
            if (style == S_APT) {
                float bal_share = mix(0.2, 0.55, hs(seed, 12.0));
                bool bcol = (balc || cumba) && colr < bal_share && nb >= 2.0;
                if (cumba && closed) { bcol = colr < 0.7; }
                if (bcol) {
                    float rc = hh(vec4(seed * 1000.0, fi, bi, 19.0));
                    if (closed && rc < mix(0.3, 0.9, hs(seed, 13.0))) { layer = L_BAL_CLOSED; }
                    else if (hs(seed, 14.0) < 0.5) { layer = L_BAL_SOLID; }
                    else { layer = rc > 0.86 ? L_BAL_LAUNDRY : L_BAL_RAIL; }
                } else {
                    layer = (acu && r < 0.13) ? L_APT_AC : (r < 0.30 ? L_APT_SHUT : (r < 0.88 ? L_APT_TUL : L_APT_DARK));
                }
            } else if (style == S_M19) {
                bool mid = abs(bi - (nb - 1.0) * 0.5) < 0.6;
                bool piano = fi >= nrow - 1.5 && fi < nrow - 0.5;
                if (balc && mid && (piano || hs(seed, 16.0) < 0.3)) { layer = L_M19_BAL; }
                else if (hs(seed, 15.0) < 0.22) { layer = L_M19_ARCH; }
                else { layer = shut && r < 0.6 ? L_M19_SHUT : L_M19; }
            } else if (style == S_WOOD) {
                bool corner = nb > 3.0 && (bi < 0.5 || bi > nb - 1.5) && hs(seed, 17.0) < 0.4 && !cumba;
                layer = corner ? L_WOOD_BLANK : (r < 0.12 ? L_WOOD_KAFES : L_WOOD);
            } else if (style == S_HAN || style == S_RUIN) {
                layer = style == S_RUIN || r < 0.25 ? L_HAN_BLANK : L_HAN;
            } else if (style == S_MOD) {
                layer = (office && hs(seed, 18.0) < 0.5) ? L_CURTAIN : L_RIBBON;
                avg_l = layer;
            } else if (style == S_PUB) {
                layer = L_PUB;
            } else if (style == S_IND) {
                layer = fi < 0.5 && r < 0.6 ? L_RIBBON : L_IND;
            } else {
                layer = r < 0.7 ? L_APT_TUL : L_BLANK;
                avg_l = L_APT_TUL;
            }
        }
        vec4 m = textureGrad(cells_mask, vec3(f, layer), dx, dy);
        vec3 own = textureGrad(cells_col, vec3(f, layer), dx, dy).rgb;
        float px = max(length(dx), length(dy));
        float far = smoothstep(0.2, 0.55, px);
        if (far > 0.0) {
            vec4 ma = textureGrad(cells_mask, vec3(f, avg_l), dx, dy);
            vec3 ca = textureGrad(cells_col, vec3(f, avg_l), dx, dy).rgb;
            m = mix(m, ma, far);
            own = mix(own, ca, far);
            tcol = mix(tcol, trim, far);
        }
        // Weathering: some walls freshly painted, most not; grime near the
        // street, rain streaks from the sills, darker under the eaves.
        float wear = 0.84 + 0.18 * hs(seed, 2.0);
        vec3 wall = basec * wear * (1.0 - 0.18 * smoothstep(2.5, 0.0, y - gl));
        wall *= 1.0 - 0.08 * smoothstep(0.45, 0.8, fnoise(vec2(u * 2.0 + seed * 50.0, y * 0.25), 1.5, fw));
        wall *= 1.0 - 0.12 * smoothstep(eave - 1.2, eave, y);
        float rest = clamp(1.0 - m.r - m.g, 0.0, 1.0);
        c = (own * rest + wall * m.r + tcol * m.g) * m.a;
        glass_frac = m.b;
        rough = mix(0.9, style == S_MOD ? 0.08 : 0.15, m.b);
        metal = m.b * (style == S_MOD ? 0.5 : 0.25);
        spec = mix(0.35, 0.6, m.b);

        // NIGHT (as manhattan_buildings after its barcode fix): each window its
        // own turn-on point through dusk, homes' share from the clock, lit by
        // tungsten, neutral and cool-white LED; offices by floor; shops till late.
        float come = smoothstep(0.02, 0.45, night);
        float fl_k = hh(vec4(seed * 1000.0, fi, 3.0, 71.0));
        float wk = hh(vec4(seed * 1000.0, fi, bi, fk + 17.0));
        float ck = hh(vec4(seed * 1000.0, fi, bi, fk + 29.0));
        float wb = hh(vec4(seed * 1000.0, fi, bi, fk + 37.0));
        float on;
        float far_on;
        float bright;
        vec3 lc;
        float inner = 0.8 + 0.35 * f.y;
        bool street = zone == 0.0 && (layer == L_SHOP || layer == L_SHOP_AWN || layer == L_ENTRANCE);
        if (street) {
            float sh = layer == L_ENTRANCE ? 0.9 : shop_occ;
            on = step(wk, sh) * step(wk * 0.6, come);
            far_on = sh * come * 0.85;
            bright = mix(0.55, 0.95, wb);
            lc = ck < 0.35 ? vec3(0.92, 0.94, 1.0) : (ck < 0.8 ? vec3(1.0, 0.82, 0.60) : vec3(1.0, 0.92, 0.80));
            inner = 1.0;
        } else if (office || style == S_MOD) {
            float fl_on = step(fl_k, office_occ * mix(0.75, 1.2, hs(seed, 61.0)));
            float desk = step(wk, mix(0.6, 0.95, hs(seed, 62.0)));
            on = fl_on * desk * come;
            far_on = fl_on * 0.75 * come;
            bright = mix(0.45, 0.85, wb);
            lc = ck < 0.6 ? vec3(0.90, 0.93, 1.0) : vec3(1.0, 0.90, 0.76);
            inner = 0.75 + 0.4 * smoothstep(0.7, 0.9, f.y);
        } else {
            float share = mix(0.14, 0.40, hs(seed, 61.0)) * mix(0.6, 1.4, fl_k) * home_occ;
            on = step(wk, share * come);
            far_on = share * come;
            bright = mix(0.3, 0.85, wb);
            lc = ck < 0.36 ? vec3(1.0, 0.70, 0.42) : (ck < 0.58 ? vec3(1.0, 0.82, 0.62)
               : (ck < 0.94 ? vec3(0.86, 0.92, 1.0) : vec3(0.55, 0.66, 1.0) * (0.75 + 0.25 * sin(TIME * 7.0 + ck * 60.0))));
        }
        on = mix(on, far_on, far);
        inner = mix(inner, 0.95, far);
        float blindk = clamp(dot(own, vec3(0.33)) * 4.0, 0.0, 1.0);
        emit = lc * m.b * on * bright * inner * mix(0.62, 0.48, blindk);
        if (street) { emit *= 1.2; }
        float fres = pow(1.0 - clamp(abs(dot(NORMAL, VIEW)), 0.0, 1.0), 3.0);
        emit += m.b * (1.0 - on) * smoothstep(0.2, 0.8, night) * vec3(0.008, 0.010, 0.019) * (0.4 + 1.2 * fres);
        // At night the plaster itself is never black: the street lamps wash the
        // lower floors (very faintly).
        emit += c * smoothstep(0.3, 0.9, night) * 0.04 * smoothstep(8.0, 0.0, y - gl);
    }

    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        vec2 th;
        if (roofish || surf == 5) {
            th = th_surface(absorb, 26.0, mass, 0.6 * hs(seed, 7.1));
            th = mix(th, th_surface(0.45, 5.0, 0.55, -1.0), veg);
        } else {
            th = th_surface(0.6, 18.0, 0.7, 0.8 + 1.5 * hs(seed, 11.3));
            float sky = th_enc(mix(thermal_air_c, thermal_sky_c, 0.35));
            th.y = mix(th.y, sky, glass_frac * 0.45);
            th.x *= 1.0 - glass_frac * 0.6;
            th.y += length(emit) * 0.004;
        }
        TH_OUT(th);
    } else {
        ALBEDO = c;
        ROUGHNESS = rough;
        METALLIC = metal;
        SPECULAR = spec;
        EMISSION = emit;
    }
}
"""


## Rooftop kit: the part's kind in INSTANCE_CUSTOM.x (0 water tank, 1 solar
## heater, 2 chimney, 3 dish, 4 AC condenser, 5 stair hut, 6 aerial).
const KIT_SHADER := """
render_mode cull_back, diffuse_burley;
uniform float night = 0.0;
varying vec4 v_col;
varying vec4 v_cus;
varying vec3 v_local;
varying vec3 v_size;
void vertex() {
    v_size = vec3(length(MODEL_MATRIX[0].xyz), length(MODEL_MATRIX[1].xyz), length(MODEL_MATRIX[2].xyz));
    v_local = VERTEX;
    v_col = COLOR;
    v_cus = INSTANCE_CUSTOM;
}
float aaline(float v, float w) {
    float fw = fwidth(v);
    float d = abs(fract(v + 0.5) - 0.5);
    float l = 1.0 - smoothstep(w * 0.5 - fw, w * 0.5 + fw, d);
    return mix(l, w, smoothstep(w * 0.5, w * 2.0, fw));
}
void fragment() {
    int kind = int(v_cus.x + 0.5);
    vec3 wn = (INV_VIEW_MATRIX * vec4(NORMAL, 0.0)).xyz;
    bool up = wn.y > 0.6;
    vec3 c = v_col.rgb;
    float rough = 0.7;
    float metal = 0.0;
    vec3 emit = vec3(0.0);
    float y = (v_local.y + 0.5) * v_size.y;
    vec2 th = th_surface(0.5, 16.0, 0.3, 0.0);
    if (kind == 0) {            // water tank: moulded bands
        c *= 0.92 + 0.08 * aaline(v_local.x * 4.0, 0.2);
        if (up) { c *= 1.05; }
        rough = 0.5;
        th = th_surface(0.4, 10.0, 0.9, 0.0);
    } else if (kind == 1) {     // solar heater: dark glass collector, tank on top, frame
        bool tank = v_local.y > 0.1 && v_local.z < -0.15;
        bool frame = abs(v_local.x) > 0.43 && v_local.z < -0.3;
        if (!tank && !frame) {
            c = vec3(0.06, 0.08, 0.13);
            c *= 1.0 - 0.4 * aaline(v_local.x * 12.0, 0.15);
            rough = 0.12; metal = 0.3;
            th = th_surface(0.9, 30.0, 0.2, 4.0);
        } else if (frame) {
            c = vec3(0.55, 0.56, 0.57); metal = 0.6; rough = 0.5;
        } else {
            rough = 0.45; metal = 0.3;
            th = th_surface(0.4, 14.0, 0.8, 8.0);
        }
    } else if (kind == 2) {     // chimney: soot at the top, a cap
        c *= 1.0 - 0.6 * smoothstep(v_size.y - 0.35, v_size.y, y);
        if (up) { c = vec3(0.10, 0.10, 0.10); }
        th = th_surface(0.6, 18.0, 0.7, 2.0 + 10.0 * night);
    } else if (kind == 3) {     // dish
        rough = 0.6;
        if (v_local.y < -0.0) { c = vec3(0.35, 0.35, 0.36); metal = 0.5; }
        th = th_surface(0.3, 14.0, 0.15, 0.0);
    } else if (kind == 4) {     // AC condenser: fan grille on the face, louvres
        float fan = 1.0 - smoothstep(0.26, 0.3, length(vec2(v_local.x * 1.6 + 0.15, v_local.y)));
        if (!up) { c = mix(c, vec3(0.15, 0.15, 0.16), fan * 0.8 * step(0.3, abs(v_local.z))); }
        rough = 0.6;
        th = th_surface(0.45, 14.0, 0.2, 8.0);
    } else if (kind == 5) {     // stair hut: a door, a concrete top
        if (up) { c = vec3(0.50, 0.49, 0.47); }
        else {
            float door = step(abs(v_local.x), 0.18) * step(y, 2.0) * step(0.45, v_local.z);
            c = mix(c, vec3(0.30, 0.22, 0.16), door);
        }
        rough = 0.9;
        th = th_surface(0.6, 18.0, 0.7, 0.5);
    } else {                    // aerial
        c = vec3(0.45, 0.45, 0.46); metal = 0.7; rough = 0.4;
    }
    if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
        TH_OUT(th);
    } else {
        ALBEDO = pow(max(c, vec3(0.0)), vec3(2.2));
        ROUGHNESS = rough;
        METALLIC = metal;
        EMISSION = emit;
    }
}
"""

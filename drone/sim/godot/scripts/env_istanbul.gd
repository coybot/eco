## Historic Istanbul: the walled peninsula (Sultanahmet, Suleymaniye, Fatih,
## Zeyrek, out to the Theodosian Walls), Galata and Beyoglu across the Golden
## Horn, and the Asian shore at Uskudar and Kadikoy.
##
## Data: drone/sim/godot/assets/istanbul/, baked by
## drone/sim/tools/fetch_istanbul.py from OpenStreetMap (ODbL), NASA SRTM
## (public domain) and the EU's GHSL building heights (CC BY 4.0). See
## assets/istanbul/CREDITS.md.
##
## HOW THIS DIFFERS FROM manhattan:
##   * Real ground. Istanbul is seven hills: the ground is a heightfield, from
##     the sea (y 0) to ~80 m at Fatih, and altitudes are metres above SEA
##     LEVEL. ground_height() is the terrain; collision_height() adds the roofs.
##   * Real footprints. The old city has no grid, so a building is its OSM
##     polygon, not an axis-aligned box. Collision is the extruded polygon
##     (point-in-polygon for collision_height, an exact ray-vs-prism for
##     raycast_structures); the router's occupancy (WALLS) is each footprint
##     cut into OCC_RES strips.
##   * No rotation. The frame is plain ENU around a datum at Hagia Sophia:
##     +east, +north, Godot (e, y, -n).
##
## SCENE API (what scenery scripts may read; mirrors env_manhattan_photo's so
## the shared kit — scene_look, world_clock, ... — works unchanged):
##   region, GEO_DATUM, SUN_LATLON, UTC_OFFSET, GRID_ROTATION_DEG (0), SEA_Y,
##   data_offset() (zero), to_sim(), godot_pos(), geo_to_sim(), sim_to_geo(),
##   sim_bounds(), is_water(), water_mask(), ground_height(), night_amount(),
##   and the building arrays (b_* below, verts) for scripts that dress them.
extends Node3D

const SceneKit = preload("res://scripts/scene_kit.gd")
const LightingKit = preload("res://scripts/lighting_kit.gd")
const SceneLook = preload("res://scripts/scene_look.gd")

const DIR := "res://assets/istanbul/"

const region := "istanbul"
const GEO_DATUM := [41.0086, 28.9802]          # Hagia Sophia
const SUN_LATLON := [41.0086, 28.9802]
const UTC_OFFSET := 3.0                         # Turkey, no DST since 2016
const GRID_ROTATION_DEG := 0.0
const M_PER_DEG_LAT := 111132.0
const SEA_Y := 0.0
const LAND_Y := 0.0          # only for scripts that assume flat ground; prefer ground_height()

## The detailed scene, ENU metres (fetch_istanbul.BOUNDS).
const BOUNDS := Rect2(-6000.0, -3200.0, 11000.0, 8000.0)
const OCC_RES := 5.0
## Spatial bins for building lookups.
const BIN := 50.0
## Render tiles.
const CHUNK := 500.0
## b_kind of add_obstacle() solids (past the OSM kinds in buildings.json).
const KIND_SOLID := 250

var WALLS: Array = []
var doors: Dictionary = {}
var DOOR_PANELS: Dictionary = {}
var props: Array = []
var _half := 5500.0

# Terrain: row 0 = NORTH edge of _tb.
var _t := PackedFloat32Array()
var _tw := 0
var _th := 0
var _tb := Rect2()
var _tres := 10.0
var _tmin := 0.0
var _tmax := 0.0

# Water mask: row 0 = NORTH edge of _wb.
var _water := PackedByteArray()
var _ww := 0
var _wh := 0
var _wb := Rect2()
var _wres := 4.0
var _water_tex: ImageTexture

# Buildings, parallel arrays. verts is every footprint (CCW, not closed);
# building i is verts[b_first[i] .. b_first[i] + b_count[i]).
var verts := PackedVector2Array()
var b_first := PackedInt32Array()
var b_count := PackedInt32Array()
var b_h := PackedFloat32Array()       # height above the ground at its centre
var b_minh := PackedFloat32Array()    # bottom above ground (bridges, overhangs)
var b_base := PackedFloat32Array()    # lowest ground under it (walls go down to here)
var b_top := PackedFloat32Array()     # absolute roof height (sea-level metres)
var b_kind := PackedByteArray()
var b_flags := PackedByteArray()
var b_levels := PackedByteArray()
var b_colour := PackedByteArray()
var b_name := PackedInt32Array()
var b_centre := PackedVector2Array()
var b_aabb := PackedFloat32Array()    # 4 per building: e0, n0, e1, n1
var building_meta: Dictionary = {}    # buildings.json
var _bins: Dictionary = {}            # Vector2i -> PackedInt32Array
## 1 = drawn by a scenery script instead (a landmark's own model); still solid.
var b_skip := PackedByteArray()

var _building_root: Node3D


func _ready() -> void:
	var t0 := Time.get_ticks_msec()
	SceneKit.build(self, false, 0)
	LightingKit.for_large_env(self, 9000.0)
	_drop_scenekit_ground()
	_load_terrain()
	_load_water()
	_load_buildings()
	b_skip.resize(b_first.size())
	b_skip.fill(0)
	# 1. Landmarks claim the OSM footprints they replace (b_skip) and add their
	#    own solids (add_obstacle) BEFORE anything is drawn or the router's
	#    occupancy is cut.
	_phase("istanbul_landmarks", "claim")
	_build_walls()
	# 2. Ground, sea, buildings: a scenery script replaces each base version
	#    when it exists (static build(env) -> bool, true = handled).
	if not _phase("istanbul_ground", "build"):
		_render_terrain()
	if not _phase("istanbul_sea", "build"):
		_render_water()
	_building_root = Node3D.new()
	_building_root.name = "buildings"
	add_child(_building_root)
	if not _phase("istanbul_buildings", "build"):
		_render_buildings()
	var b := sim_bounds()
	_half = maxf(b.size.x, b.size.y) * 0.5
	# 3. The look and the clock (shared with manhattan_photo), then every
	#    scenery node that follows them. Each is optional: a fresh base runs
	#    with none of them.
	SceneLook.setup(self, region)
	add_child(preload("res://scripts/world_clock.gd").new())
	for s in SCENERY:
		var path := "res://scripts/%s.gd" % s
		if ResourceLoader.exists(path):
			var node = load(path).new()
			if node is Node:
				add_child(node)
	print("[env_istanbul] ready in %d ms: %d buildings, %d occupancy strips, terrain %.0f-%.0f m"
		% [Time.get_ticks_msec() - t0, b_first.size(), WALLS.size(), _tmin, _tmax])


## Scenery nodes added after the look and clock, in this order, when their
## script exists. Each is a child of this env and reads the scene API.
const SCENERY := ["istanbul_landmarks", "istanbul_bridges", "istanbul_ships",
	"istanbul_nature", "istanbul_night", "istanbul_life", "istanbul_far"]


## Calls a static `func <fn>(env) -> bool` on scripts/<script>.gd if it exists.
func _phase(script: String, fn: String) -> bool:
	var path := "res://scripts/%s.gd" % script
	if not ResourceLoader.exists(path):
		return false
	var sc: Script = load(path)
	for m in sc.get_script_method_list():
		if m["name"] == fn:
			return bool(sc.call(fn, self))
	return false


func _drop_scenekit_ground() -> void:
	var dome := get_node_or_null("sky_dome")
	if dome:
		dome.queue_free()
	for child in get_children():
		if child is MeshInstance3D and child.mesh is PlaneMesh:
			child.queue_free()


# ================================================================ scene API

func data_offset() -> Vector2:
	return Vector2.ZERO


func to_sim(p: Vector2) -> Vector2:
	return p


func godot_pos(p: Vector2, y: float = 0.0) -> Vector3:
	return Vector3(p.x, y, -p.y)


func geo_to_sim(lat: float, lon: float) -> Vector2:
	var m_lon := 111320.0 * cos(deg_to_rad(float(GEO_DATUM[0])))
	return Vector2((lon - float(GEO_DATUM[1])) * m_lon, (lat - float(GEO_DATUM[0])) * M_PER_DEG_LAT)


func sim_to_geo(p: Vector2) -> Array:
	var m_lon := 111320.0 * cos(deg_to_rad(float(GEO_DATUM[0])))
	return [float(GEO_DATUM[0]) + p.y / M_PER_DEG_LAT, float(GEO_DATUM[1]) + p.x / m_lon]


func sim_bounds() -> Rect2:
	return BOUNDS


## Terrain + water reach (the far shores), wider than sim_bounds().
func wide_bounds() -> Rect2:
	return _tb


func grid_config() -> Dictionary:
	return {"res": OCC_RES, "origin": [BOUNDS.position.x, BOUNDS.position.y],
		"w": int(ceil(BOUNDS.size.x / OCC_RES)), "h": int(ceil(BOUNDS.size.y / OCC_RES))}


## {"tex": ImageTexture (R8, 1 = water, row 0 north), "bounds": Rect2, "res": m}
func water_mask() -> Dictionary:
	return {"tex": _water_tex, "bounds": _wb, "res": _wres}


func is_water(e: float, n: float) -> bool:
	if _ww == 0:
		return false
	var x := int((e - _wb.position.x) / _wres)
	var y := int((_wb.end.y - n) / _wres)
	if x < 0 or y < 0 or x >= _ww or y >= _wh:
		return true          # beyond the mask is open water (the Marmara)
	return _water[y * _ww + x] > 127


## Bare ground in sea-level metres, bilinear on the 10 m grid. 0 on water.
func ground_height(e: float, n: float) -> float:
	if _tw == 0:
		return 0.0
	var fx := clampf((e - _tb.position.x) / _tres - 0.5, 0.0, float(_tw - 1))
	var fy := clampf((_tb.end.y - n) / _tres - 0.5, 0.0, float(_th - 1))
	var x0 := int(fx)
	var y0 := int(fy)
	var x1 := mini(x0 + 1, _tw - 1)
	var y1 := mini(y0 + 1, _th - 1)
	var tx := fx - float(x0)
	var ty := fy - float(y0)
	return lerpf(lerpf(_t[y0 * _tw + x0], _t[y0 * _tw + x1], tx),
		lerpf(_t[y1 * _tw + x0], _t[y1 * _tw + x1], tx), ty)


## What an aircraft hits standing still: the ground, or a roof.
func collision_height(e: float, n: float) -> float:
	var h := ground_height(e, n)
	var i := building_at(e, n)
	if i >= 0:
		h = maxf(h, b_top[i])
	return h


## Index of the building whose footprint contains (e, n), or -1.
func building_at(e: float, n: float) -> int:
	var key := Vector2i(floori(e / BIN), floori(n / BIN))
	if not _bins.has(key):
		return -1
	for i in (_bins[key] as PackedInt32Array):
		var k := i * 4
		if e < b_aabb[k] or e > b_aabb[k + 2] or n < b_aabb[k + 1] or n > b_aabb[k + 3]:
			continue
		if _inside(i, e, n):
			return i
	return -1


## Building indices whose bounding boxes touch the rect.
func buildings_in(r: Rect2) -> PackedInt32Array:
	var out := PackedInt32Array()
	var seen := {}
	for gx in range(floori(r.position.x / BIN), floori(r.end.x / BIN) + 1):
		for gy in range(floori(r.position.y / BIN), floori(r.end.y / BIN) + 1):
			var key := Vector2i(gx, gy)
			if not _bins.has(key):
				continue
			for i in (_bins[key] as PackedInt32Array):
				if seen.has(i):
					continue
				seen[i] = true
				var k := i * 4
				if b_aabb[k + 2] < r.position.x or b_aabb[k] > r.end.x \
						or b_aabb[k + 3] < r.position.y or b_aabb[k + 1] > r.end.y:
					continue
				out.append(i)
	return out


func footprint(i: int) -> PackedVector2Array:
	return verts.slice(b_first[i], b_first[i] + b_count[i])


func night_amount() -> float:
	var c := get_node_or_null("world_clock")
	if c != null and c.has_method("night_amount"):
		return float(c.night_amount())
	return 0.0


func env_state() -> Dictionary:
	return {"env": "istanbul", "region_m": [BOUNDS.size.x, BOUNDS.size.y],
		"terrain_m": [_tmin, _tmax], "buildings": b_first.size(),
		"geo_datum": GEO_DATUM}


## A solid that is not an OSM building (a minaret, a dome, a wall tower):
## collision, raycasts and, if called before the occupancy is cut (the
## landmarks' claim phase), the router's grid. Not drawn: the caller draws it.
## bottom/top are absolute (sea-level) metres.
func add_obstacle(ring: PackedVector2Array, bottom: float, top: float) -> int:
	var c := Vector2.ZERO
	for p in ring:
		c += p
	c /= float(maxi(ring.size(), 1))
	var g := ground_height(c.x, c.y)
	_add_building(ring, top - g, maxf(bottom - g, 0.0), KIND_SOLID, 0, 0, 0, -1, g)
	var i := b_first.size() - 1
	b_skip.append(1)
	b_base[i] = minf(b_base[i], bottom)
	b_minh[i] = bottom - b_base[i]
	b_top[i] = top
	return i


## A temporary obstacle (the IPC add_wall op): a box building.
func add_wall(rect: Rect2, height: float = 12.0) -> void:
	var g := ground_height(rect.get_center().x, rect.get_center().y)
	var ring := PackedVector2Array([rect.position, Vector2(rect.end.x, rect.position.y),
		rect.end, Vector2(rect.position.x, rect.end.y)])
	_add_building(ring, height, 0.0, 0, 0, 0, 0, -1, g)
	b_skip.append(0)
	WALLS.append(rect)


# =============================================================== occlusion

## Distance along an ENU ray to the first building or ground, or -1 on a miss
## (env_manhattan's contract; fixedwing_manager accepts both conventions).
## Buildings are exact extruded polygons, walked bin by bin; the ground is
## marched at 5 m.
func raycast_structures(from: Vector3, dir: Vector3, max_dist: float) -> float:
	var best := max_dist
	var hit := false
	var o := Vector2(from.x, from.y)
	var d := Vector2(dir.x, dir.y)
	var seen := {}
	for key in _ray_bins(o, d, max_dist):
		if not _bins.has(key):
			continue
		for i in (_bins[key] as PackedInt32Array):
			if seen.has(i):
				continue
			seen[i] = true
			var t := _ray_building(i, from, dir, best)
			if t >= 0.0 and t < best:
				best = t
				hit = true
	var step := 5.0
	var s := 0.0
	while s < best:
		var p := from + dir * s
		if p.z < ground_height(p.x, p.y):
			return s
		s += step
	return best if hit else -1.0


## Bins a 2D ray passes through (Amanatides-Woo walk).
func _ray_bins(o: Vector2, d: Vector2, max_t: float) -> Array:
	var out: Array = []
	var cx := floori(o.x / BIN)
	var cy := floori(o.y / BIN)
	out.append(Vector2i(cx, cy))
	var len2 := d.length()
	if len2 < 1e-6:
		return out
	var sx := 1 if d.x > 0.0 else -1
	var sy := 1 if d.y > 0.0 else -1
	var tdx := INF if absf(d.x) < 1e-9 else BIN / absf(d.x)
	var tdy := INF if absf(d.y) < 1e-9 else BIN / absf(d.y)
	var nx := (float(cx + (1 if sx > 0 else 0)) * BIN - o.x)
	var ny := (float(cy + (1 if sy > 0 else 0)) * BIN - o.y)
	var tmx := INF if absf(d.x) < 1e-9 else nx / d.x
	var tmy := INF if absf(d.y) < 1e-9 else ny / d.y
	var guard := 0
	while guard < 4000:
		guard += 1
		if tmx < tmy:
			if tmx > max_t:
				break
			cx += sx
			tmx += tdx
		else:
			if tmy > max_t:
				break
			cy += sy
			tmy += tdy
		out.append(Vector2i(cx, cy))
	return out


## First t in [0, max_t] where the ray is inside building i's prism, or -1.
func _ray_building(i: int, from: Vector3, dir: Vector3, max_t: float) -> float:
	var lo := b_base[i] + b_minh[i]
	var hi := b_top[i]
	# Vertical window of the ray.
	var tz0 := 0.0
	var tz1 := max_t
	if absf(dir.z) < 1e-9:
		if from.z < lo or from.z > hi:
			return -1.0
	else:
		var a := (lo - from.z) / dir.z
		var b := (hi - from.z) / dir.z
		tz0 = maxf(tz0, minf(a, b))
		tz1 = minf(tz1, maxf(a, b))
		if tz0 > tz1:
			return -1.0
	# Horizontal: crossings of the 2D ray with the footprint's edges.
	var o := Vector2(from.x, from.y)
	var d := Vector2(dir.x, dir.y)
	var ts := PackedFloat32Array()
	var f := b_first[i]
	var c := b_count[i]
	for k in c:
		var p := verts[f + k]
		var q := verts[f + (k + 1) % c]
		var e := q - p
		var den := d.x * e.y - d.y * e.x
		if absf(den) < 1e-12:
			continue
		var w := p - o
		var t := (w.x * e.y - w.y * e.x) / den
		var u := (w.x * d.y - w.y * d.x) / den
		if u >= 0.0 and u < 1.0 and t >= 0.0:
			ts.append(t)
	ts.sort()
	var inside := _inside(i, o.x, o.y)
	var start := 0.0
	var idx := 0
	if not inside:
		if ts.is_empty():
			return -1.0
		start = ts[0]
		idx = 1
	while true:
		var end := ts[idx] if idx < ts.size() else INF
		var a0 := maxf(start, tz0)
		var a1 := minf(end, tz1)
		if a0 <= a1:
			return a0
		if idx + 1 >= ts.size():
			return -1.0
		start = ts[idx + 1]
		idx += 2
	return -1.0


func _inside(i: int, e: float, n: float) -> bool:
	var f := b_first[i]
	var c := b_count[i]
	var inside := false
	var j := c - 1
	for k in c:
		var a := verts[f + k]
		var b := verts[f + j]
		if (a.y > n) != (b.y > n) and e < (b.x - a.x) * (n - a.y) / (b.y - a.y) + a.x:
			inside = not inside
		j = k
	return inside


# ==================================================================== data

func _read_json(path: String) -> Dictionary:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		push_error("[env_istanbul] missing %s — run drone/sim/tools/fetch_istanbul.py" % path)
		return {}
	var d = JSON.parse_string(f.get_as_text())
	return d if d is Dictionary else {}


func _rect_of(b: Array) -> Rect2:
	return Rect2(float(b[0]), float(b[1]), float(b[2]) - float(b[0]), float(b[3]) - float(b[1]))


func _load_terrain() -> void:
	var m := _read_json(DIR + "terrain.json")
	if m.is_empty():
		return
	var raw := FileAccess.get_file_as_bytes(DIR + "terrain.zi16")
	_tw = int(m["size_px"][0])
	_th = int(m["size_px"][1])
	var bytes := raw.decompress(_tw * _th * 2, FileAccess.COMPRESSION_DEFLATE)
	if bytes.size() != _tw * _th * 2:
		# zlib stream (with header): Godot's DEFLATE wants raw; skip the 2-byte header.
		bytes = raw.slice(2).decompress(_tw * _th * 2, FileAccess.COMPRESSION_DEFLATE)
	_tb = _rect_of(m["bounds_enu"])
	_tres = float(m["res_m"])
	_tmin = float(m["min_m"])
	_tmax = float(m["max_m"])
	_t.resize(_tw * _th)
	for k in _tw * _th:
		_t[k] = float(bytes.decode_s16(k * 2)) * 0.1


func _load_water() -> void:
	var m := _read_json(DIR + "water.json")
	if m.is_empty():
		return
	var img := Image.load_from_file(DIR + "water.png")
	if img == null:
		return
	img.convert(Image.FORMAT_R8)
	_ww = img.get_width()
	_wh = img.get_height()
	_water = img.get_data()
	_wb = _rect_of(m["bounds_enu"])
	_wres = float(m["res_m"])
	_water_tex = ImageTexture.create_from_image(img)


func _load_buildings() -> void:
	building_meta = _read_json(DIR + "buildings.json")
	var raw := FileAccess.get_file_as_bytes(DIR + "buildings.dat")
	if raw.size() < 16 or raw.slice(0, 4).get_string_from_ascii() != "IST1":
		push_error("[env_istanbul] bad or missing buildings.dat")
		return
	var n := raw.decode_u32(4)
	var nv := raw.decode_u32(8)
	var q := raw.decode_float(12)
	var rec := 16
	var vbase := rec + n * 16
	var fp := PackedVector2Array()
	for i in n:
		var r := rec + i * 16
		var first := raw.decode_u32(r)
		var cnt := raw.decode_u16(r + 4)
		var h := float(raw.decode_u16(r + 6)) * 0.1
		var mh := float(raw.decode_u16(r + 8)) * 0.1
		var kind := raw[r + 10]
		var flags := raw[r + 11]
		var levels := raw[r + 12]
		var col := raw[r + 13]
		var nm := raw.decode_u16(r + 14)
		fp.resize(cnt)
		for k in cnt:
			var v := vbase + (first + k) * 4
			fp[k] = Vector2(float(raw.decode_s16(v)) * q, float(raw.decode_s16(v + 2)) * q)
		_add_building(fp, h, mh, kind, flags, levels, col, -1 if nm == 65535 else nm, -INF)
	if verts.size() != nv:
		push_warning("[env_istanbul] vertex count %d, header says %d" % [verts.size(), nv])


## ground_at: the ground under the centre, or -INF to sample it here.
func _add_building(fp: PackedVector2Array, h: float, mh: float, kind: int, flags: int,
		levels: int, col: int, nm: int, ground_at: float) -> void:
	var i := b_first.size()
	b_first.append(verts.size())
	b_count.append(fp.size())
	verts.append_array(fp)
	var e0 := INF
	var n0 := INF
	var e1 := -INF
	var n1 := -INF
	var c := Vector2.ZERO
	var low := INF
	for p in fp:
		e0 = minf(e0, p.x)
		n0 = minf(n0, p.y)
		e1 = maxf(e1, p.x)
		n1 = maxf(n1, p.y)
		c += p
		low = minf(low, ground_height(p.x, p.y))
	c /= float(fp.size())
	var g := ground_at if ground_at > -1e9 else ground_height(c.x, c.y)
	b_h.append(h)
	b_minh.append(mh)
	b_base.append(minf(low, g) - 0.3)
	b_top.append(g + h)
	b_kind.append(kind)
	b_flags.append(flags)
	b_levels.append(levels)
	b_colour.append(col)
	b_name.append(nm)
	b_centre.append(c)
	b_aabb.append_array(PackedFloat32Array([e0, n0, e1, n1]))
	for gx in range(floori(e0 / BIN), floori(e1 / BIN) + 1):
		for gy in range(floori(n0 / BIN), floori(n1 / BIN) + 1):
			var key := Vector2i(gx, gy)
			if not _bins.has(key):
				_bins[key] = PackedInt32Array()
			var arr: PackedInt32Array = _bins[key]
			arr.append(i)
			_bins[key] = arr


## The router's occupancy: each footprint cut into OCC_RES strips, each strip
## the footprint's east-west extent along that strip's centre line.
func _build_walls() -> void:
	for i in b_first.size():
		var k := i * 4
		var n0 := floorf(b_aabb[k + 1] / OCC_RES) * OCC_RES
		var n := n0
		while n < b_aabb[k + 3]:
			var lo := INF
			var hi := -INF
			for yy in [n + 0.5, n + OCC_RES * 0.5, n + OCC_RES - 0.5]:
				var xs := _scan(i, yy)
				if xs.x <= xs.y:
					lo = minf(lo, xs.x)
					hi = maxf(hi, xs.y)
			if lo <= hi:
				WALLS.append(Rect2(lo, n, maxf(hi - lo, 0.5), OCC_RES))
			n += OCC_RES


## East-west extent of building i's footprint along the line north = y.
func _scan(i: int, y: float) -> Vector2:
	var f := b_first[i]
	var c := b_count[i]
	var lo := INF
	var hi := -INF
	for k in c:
		var a := verts[f + k]
		var b := verts[f + (k + 1) % c]
		if (a.y <= y and b.y > y) or (b.y <= y and a.y > y):
			var x := a.x + (y - a.y) / (b.y - a.y) * (b.x - a.x)
			lo = minf(lo, x)
			hi = maxf(hi, x)
	return Vector2(lo, hi)


# ================================================================== render

const GROUND_SHADER := """
shader_type spatial;
render_mode diffuse_burley;
uniform sampler2D landcover : filter_nearest;
uniform vec4 lc_rect;          // e0, n0, width, height (ENU m)
uniform sampler2D water : filter_linear;
uniform vec4 w_rect;
uniform vec3 classes[13];
varying vec3 v_world;
void vertex() { v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
	vec2 en = vec2(v_world.x, -v_world.z);
	vec2 luv = vec2((en.x - lc_rect.x) / lc_rect.z, 1.0 - (en.y - lc_rect.y) / lc_rect.w);
	vec3 c = vec3(0.47, 0.44, 0.40);
	if (luv.x > 0.0 && luv.x < 1.0 && luv.y > 0.0 && luv.y < 1.0) {
		int k = int(texture(landcover, luv).r * 255.0 + 0.5);
		c = classes[clamp(k, 0, 12)];
	} else {
		c = vec3(0.42, 0.43, 0.36);
	}
	vec2 wuv = vec2((en.x - w_rect.x) / w_rect.z, 1.0 - (en.y - w_rect.y) / w_rect.w);
	float wet = texture(water, wuv).r;
	c = mix(c, vec3(0.20, 0.22, 0.20), smoothstep(0.3, 0.9, wet));
	ALBEDO = c;
	ROUGHNESS = 0.92;
}
"""


func _ground_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = GROUND_SHADER
	var mat := ShaderMaterial.new()
	mat.shader = sh
	var lc := Image.load_from_file(DIR + "landcover.png")
	if lc != null:
		lc.convert(Image.FORMAT_R8)
		mat.set_shader_parameter("landcover", ImageTexture.create_from_image(lc))
	var lm := _read_json(DIR + "landcover.json")
	if not lm.is_empty():
		var r := _rect_of(lm["bounds_enu"])
		mat.set_shader_parameter("lc_rect", Vector4(r.position.x, r.position.y, r.size.x, r.size.y))
	mat.set_shader_parameter("water", _water_tex)
	mat.set_shader_parameter("w_rect", Vector4(_wb.position.x, _wb.position.y, _wb.size.x, _wb.size.y))
	# urban park wood cemetery plaza parking pitch beach courtyard railway construction garden playground
	mat.set_shader_parameter("classes", PackedVector3Array([
		Vector3(0.47, 0.44, 0.40), Vector3(0.30, 0.42, 0.20), Vector3(0.19, 0.30, 0.14),
		Vector3(0.27, 0.35, 0.21), Vector3(0.66, 0.61, 0.54), Vector3(0.36, 0.36, 0.36),
		Vector3(0.26, 0.48, 0.24), Vector3(0.80, 0.72, 0.56), Vector3(0.68, 0.63, 0.55),
		Vector3(0.38, 0.35, 0.31), Vector3(0.56, 0.49, 0.40), Vector3(0.33, 0.45, 0.22),
		Vector3(0.58, 0.47, 0.36)]))
	return mat


## Ground as a grid mesh: 10 m posts inside BOUNDS (in CHUNK*2 tiles), 50 m
## posts for the far shores outside it. Under water the mesh drops to a seabed
## so the sea surface has depth to show.
func _render_terrain() -> void:
	if _tw == 0:
		return
	var mat := _ground_material()
	var root := Node3D.new()
	root.name = "terrain"
	add_child(root)
	var tile := 1000.0
	var ex := BOUNDS.position.x
	while ex < BOUNDS.end.x - 1.0:
		var ny := BOUNDS.position.y
		while ny < BOUNDS.end.y - 1.0:
			root.add_child(_terrain_tile(Rect2(ex, ny, tile, tile), 10.0, mat, false))
			ny += tile
		ex += tile
	var far := 2000.0
	ex = _tb.position.x
	while ex < _tb.end.x - 1.0:
		var ny := _tb.position.y
		while ny < _tb.end.y - 1.0:
			var r := Rect2(ex, ny, far, far)
			root.add_child(_terrain_tile(r, 50.0, mat, true))
			ny += far
		ex += far


func _terrain_tile(r: Rect2, step: float, mat: Material, skip_core: bool) -> MeshInstance3D:
	var nx := int(round(r.size.x / step))
	var ny := int(round(r.size.y / step))
	var pos := PackedVector3Array()
	var nrm := PackedVector3Array()
	var idx := PackedInt32Array()
	pos.resize((nx + 1) * (ny + 1))
	nrm.resize(pos.size())
	for j in ny + 1:
		for i in nx + 1:
			var e := r.position.x + i * step
			var n := r.position.y + j * step
			var h := ground_height(e, n)
			if is_water(e, n):
				h = -6.0
			pos[j * (nx + 1) + i] = Vector3(e, h, -n)
	for j in ny + 1:
		for i in nx + 1:
			var e := r.position.x + i * step
			var n := r.position.y + j * step
			var dx := ground_height(e + step, n) - ground_height(e - step, n)
			var dy := ground_height(e, n + step) - ground_height(e, n - step)
			nrm[j * (nx + 1) + i] = Vector3(-dx, 2.0 * step, dy).normalized()
	for j in ny:
		for i in nx:
			if skip_core:
				var ce := r.position.x + (i + 0.5) * step
				var cn := r.position.y + (j + 0.5) * step
				if BOUNDS.has_point(Vector2(ce, cn)):
					continue
			var a := j * (nx + 1) + i
			var b := a + 1
			var c := a + nx + 1
			var d := c + 1
			# Godot front faces are clockwise seen from the front (above):
			# a (i, j), b east of it, c north of it.
			idx.append_array(PackedInt32Array([a, c, b, b, c, d]))
	var mi := MeshInstance3D.new()
	if idx.is_empty():
		return mi
	var arr := []
	arr.resize(Mesh.ARRAY_MAX)
	arr[Mesh.ARRAY_VERTEX] = pos
	arr[Mesh.ARRAY_NORMAL] = nrm
	arr[Mesh.ARRAY_INDEX] = idx
	var m := ArrayMesh.new()
	m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
	m.surface_set_material(0, mat)
	mi.mesh = m
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	return mi


func _render_water() -> void:
	var mi := MeshInstance3D.new()
	mi.name = "sea"
	var pm := PlaneMesh.new()
	pm.size = Vector2(60000.0, 60000.0)
	mi.mesh = pm
	mi.position = Vector3(0.0, SEA_Y, 0.0)
	var m := StandardMaterial3D.new()
	m.albedo_color = Color(0.10, 0.22, 0.27)
	m.roughness = 0.12
	m.metallic_specular = 0.6
	mi.material_override = m
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mi)


## Plain extruded footprints, one mesh per CHUNK, coloured by kind. A base
## look; the buildings' real dress (facades, tiled roofs, domes) is layered by
## the scenery scripts.
func _render_buildings() -> void:
	var chunks := {}
	for i in b_first.size():
		if b_skip[i] != 0:
			continue
		var c := b_centre[i]
		var key := Vector2i(floori(c.x / CHUNK), floori(c.y / CHUNK))
		if not chunks.has(key):
			chunks[key] = PackedInt32Array()
		var arr: PackedInt32Array = chunks[key]
		arr.append(i)
		chunks[key] = arr
	var mat := StandardMaterial3D.new()
	mat.vertex_color_use_as_albedo = true
	mat.roughness = 0.85
	for key in chunks:
		var mi := MeshInstance3D.new()
		mi.name = "b_%d_%d" % [key.x, key.y]
		mi.mesh = _building_mesh(chunks[key])
		mi.material_override = mat
		_building_root.add_child(mi)


const WALL_COLOURS := [Color(0.86, 0.80, 0.68), Color(0.80, 0.70, 0.58), Color(0.88, 0.84, 0.78),
	Color(0.78, 0.62, 0.52), Color(0.74, 0.72, 0.66), Color(0.90, 0.78, 0.60), Color(0.70, 0.66, 0.58)]


func _building_colour(i: int) -> Array:
	var kind := int(b_kind[i])
	var hsh := absi(int(b_centre[i].x * 7.13 + b_centre[i].y * 3.71))
	var wall: Color = WALL_COLOURS[hsh % WALL_COLOURS.size()]
	var roof := Color(0.55, 0.53, 0.50)
	var pitched := b_h[i] < 22.0 and (kind == 0 or kind == 7 or kind == 4) and (hsh / 7) % 3 != 0
	if pitched or kind == 12:
		roof = Color(0.66, 0.33, 0.22)
	match kind:
		2, 4, 12:
			wall = Color(0.86, 0.83, 0.77)
		3:
			wall = Color(0.82, 0.74, 0.66)
		7:
			wall = [Color(0.55, 0.40, 0.30), Color(0.80, 0.72, 0.55), Color(0.70, 0.50, 0.42)][hsh % 3]
		10:
			wall = Color(0.62, 0.56, 0.48)
	if kind == 2:
		roof = Color(0.50, 0.52, 0.54)
	return [wall, roof]


func _emit_tri(pos: PackedVector3Array, nrm: PackedVector3Array, col: PackedColorArray,
		a: Vector3, b: Vector3, c: Vector3, n: Vector3, cl: Color) -> void:
	if (b - a).cross(c - a).dot(n) > 0.0:
		var t := b
		b = c
		c = t
	pos.append_array(PackedVector3Array([a, b, c]))
	nrm.append_array(PackedVector3Array([n, n, n]))
	col.append_array(PackedColorArray([cl, cl, cl]))


func _building_mesh(ids: PackedInt32Array) -> ArrayMesh:
	var pos := PackedVector3Array()
	var nrm := PackedVector3Array()
	var col := PackedColorArray()
	for i in ids:
		var cs := _building_colour(i)
		var wall: Color = cs[0]
		var roof: Color = cs[1]
		var lo := b_base[i] + b_minh[i]
		var hi := b_top[i]
		var fp := footprint(i)
		var c := fp.size()
		for k in c:
			var p: Vector2 = fp[k]
			var q: Vector2 = fp[(k + 1) % c]
			var e := q - p
			if e.length() < 0.05:
				continue
			var n := Vector3(e.y, 0.0, e.x).normalized()   # ENU outward (e.y, -e.x) -> Godot (x, 0, -n)
			var a := Vector3(p.x, lo, -p.y)
			var b := Vector3(q.x, lo, -q.y)
			var a2 := Vector3(p.x, hi, -p.y)
			var b2 := Vector3(q.x, hi, -q.y)
			var shade := wall.darkened(0.04 * float(k % 2))
			_emit_tri(pos, nrm, col, a, b, b2, n, shade)
			_emit_tri(pos, nrm, col, a, b2, a2, n, shade)
		var tri := Geometry2D.triangulate_polygon(fp)
		if tri.is_empty():
			for k in range(1, c - 1):
				tri.append_array(PackedInt32Array([0, k, k + 1]))
		for k in range(0, tri.size(), 3):
			var p0: Vector2 = fp[tri[k]]
			var p1: Vector2 = fp[tri[k + 1]]
			var p2: Vector2 = fp[tri[k + 2]]
			_emit_tri(pos, nrm, col, Vector3(p0.x, hi, -p0.y), Vector3(p1.x, hi, -p1.y),
				Vector3(p2.x, hi, -p2.y), Vector3.UP, roof)
	var arr := []
	arr.resize(Mesh.ARRAY_MAX)
	arr[Mesh.ARRAY_VERTEX] = pos
	arr[Mesh.ARRAY_NORMAL] = nrm
	arr[Mesh.ARRAY_COLOR] = col
	var m := ArrayMesh.new()
	if not pos.is_empty():
		m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
	return m

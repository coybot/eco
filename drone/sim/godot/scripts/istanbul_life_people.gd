## People in Istanbul's streets and squares (a part of istanbul_life.gd):
## Manhattan's walkers (life_people.gd: walks carried by the person shader,
## lazy chunks near low cameras, drawn through the RenderingServer) placed for
## Istanbul.
##
## Where and how many:
##   * Along every street side, pedestrian street (spread across its width),
##     footway and stair, at a base rate per metre by class, times how built
##     up the blocks are (the env's footprints x heights), times the hot spots
##     below. Walks are cut into pieces of at most PIECE_M and each piece is
##     sheared to the slope between its ends, so people climb the hills.
##   * The hot spots (HOT: real places, published coordinates): Sultanahmet
##     Square, the Hippodrome, Eminonu and the Spice Bazaar, the Grand Bazaar,
##     Beyazit, Istiklal Avenue (by name), the Galata Bridge (by name), Galata
##     Tower, Taksim, Uskudar and Kadikoy. Their squares also fill with people
##     milling about off the street lines: random short walks on the open
##     ground (land cover plaza, park, garden or courtyard; never in a
##     building or the water).
##   * Anglers line both rails of the Galata Bridge, rods out over the Golden
##     Horn, as they do all day and half the night.
## Sparse elsewhere: the base rates are low and the old city's back streets
## carry few.
extends "res://scripts/life_people.gd"

const IM = preload("res://scripts/istanbul_life_meshes.gd")

const IST_BASE := {"primary": 0.08, "secondary": 0.06, "street": 0.035, "service": 0.012,
	"path": 0.03, "pedestrian": 0.16, "steps": 0.04}
const PIECE_M := 30.0
const MAX_PER_M := 1.4
## [lat, lon, radius m, street multiplier, milling people per m^2 at the centre]
const HOT := [
	[41.00715, 28.97800, 190.0, 4.0, 0.012],    # Sultanahmet Square / Park
	[41.00580, 28.97530, 160.0, 4.0, 0.012],    # Hippodrome (At Meydani)
	[41.01700, 28.97100, 230.0, 5.0, 0.016],    # Eminonu, New Mosque square, Spice Bazaar
	[41.01070, 28.96800, 260.0, 4.0, 0.0],      # Grand Bazaar streets
	[41.01050, 28.96420, 140.0, 3.0, 0.008],    # Beyazit Square
	[41.01620, 28.96390, 120.0, 2.0, 0.004],    # Suleymaniye
	[41.02560, 28.97410, 90.0, 3.5, 0.012],     # Galata Tower square
	[41.03690, 28.98510, 140.0, 3.5, 0.012],    # Taksim Square
	[41.02260, 28.97410, 130.0, 2.5, 0.006],    # Karakoy waterfront
	[41.02650, 29.01540, 200.0, 2.5, 0.006],    # Uskudar square and pier
	[40.99050, 29.02550, 260.0, 3.0, 0.006],    # Kadikoy pier and market
]
const STREET_MULT := {"İstiklal Caddesi": 4.0, "Galata Köprüsü": 2.5}
const GALATA_BRIDGE := "Galata Köprüsü"
## Land cover classes people mill on: park, plaza, courtyard, garden.
const OPEN_CLASSES := [1, 4, 8, 11]

var _hot: Array = []                        # [centre Vector2, r, mult, plaza]
var _angler_mesh: ArrayMesh
var _anglers := {}                          # chunk -> PackedFloat32Array
var _lc := PackedByteArray()
var _lc_w := 0
var _lc_h := 0
var _lc_b := Rect2()
var _lc_res := 2.0
var anglers := 0


func _prepare() -> void:
	if roads == null or not roads.loaded:
		return
	_mesh = LM.person_mesh()
	_angler_mesh = IM.angler_mesh()
	for h: Array in HOT:
		_hot.append([env.call("geo_to_sim", float(h[0]), float(h[1])), float(h[2]), float(h[3]), float(h[4])])
	_load_landcover()
	# Building bulk per cell: footprint (bounding box x 0.75) times height.
	var aabb: PackedFloat32Array = env.get("b_aabb")
	var hs: PackedFloat32Array = env.get("b_h")
	var cs: PackedVector2Array = env.get("b_centre")
	for i in hs.size():
		var k := i * 4
		var area := (aabb[k + 2] - aabb[k]) * (aabb[k + 3] - aabb[k + 1]) * 0.75
		var key := Vector2i(int(floor(cs[i].x / CELL)), int(floor(cs[i].y / CELL)))
		_bulk[key] = float(_bulk.get(key, 0.0)) + area * hs[i] / (CELL * CELL)
	for d in range(0, roads.edge_count(), 2):
		var cls: String = roads.d_class[d]
		if not IST_BASE.has(cls):
			continue
		if (roads.d_flags[d] & (roads.FLAG_TUNNEL | roads.FLAG_BELOW)) != 0:
			continue
		if (roads.d_flags[d] & roads.FLAG_BRIDGE) != 0 and roads.name_of(d) != GALATA_BRIDGE \
				and not cls in ["path", "pedestrian", "steps"]:
			continue
		var mid: Vector2 = roads.nodes[roads.d_from[d]] + roads.d_dir[d] * roads.d_len[d] * 0.5
		_add_to_chunk(mid, d)
	# Squares: their chunks exist even with no street in them.
	for h: Array in _hot:
		if float(h[3]) <= 0.0:
			continue
		var c: Vector2 = h[0]
		var r: float = h[1]
		for ky in range(int(floor((c.y - r) / CHUNK)), int(floor((c.y + r) / CHUNK)) + 1):
			for kx in range(int(floor((c.x - r) / CHUNK)), int(floor((c.x + r) / CHUNK)) + 1):
				if not _chunk_edges.has(Vector2i(kx, ky)):
					_chunk_edges[Vector2i(kx, ky)] = PackedInt32Array()
	_place_anglers()


func _add_to_chunk(p: Vector2, d: int) -> void:
	var key := Vector2i(int(floor(p.x / CHUNK)), int(floor(p.y / CHUNK)))
	if not _chunk_edges.has(key):
		_chunk_edges[key] = PackedInt32Array()
	if d >= 0:
		var arr: PackedInt32Array = _chunk_edges[key]
		arr.append(d)
		_chunk_edges[key] = arr


func _load_landcover() -> void:
	var f := FileAccess.open("res://assets/istanbul/landcover.json", FileAccess.READ)
	if f == null:
		return
	var m = JSON.parse_string(f.get_as_text())
	var img := Image.load_from_file("res://assets/istanbul/landcover.png")
	if not (m is Dictionary) or img == null:
		return
	img.convert(Image.FORMAT_R8)
	_lc = img.get_data()
	_lc_w = img.get_width()
	_lc_h = img.get_height()
	var b: Array = m["bounds_enu"]
	_lc_b = Rect2(float(b[0]), float(b[1]), float(b[2]) - float(b[0]), float(b[3]) - float(b[1]))
	_lc_res = float(m["res_m"])


func _landcover(p: Vector2) -> int:
	if _lc_w == 0:
		return 0
	var x := int((p.x - _lc_b.position.x) / _lc_res)
	var y := int((_lc_b.end.y - p.y) / _lc_res)
	if x < 0 or y < 0 or x >= _lc_w or y >= _lc_h:
		return 0
	return _lc[y * _lc_w + x]


func _open_ground(p: Vector2) -> bool:
	return _landcover(p) in OPEN_CLASSES and not env.call("is_water", p.x, p.y) \
		and env.call("building_at", p.x, p.y) < 0


## Hot-spot multiplier on street rates, and milling people per m^2, at p.
func _hot_at(p: Vector2) -> Vector2:
	var mult := 1.0
	var mill := 0.0
	for h: Array in _hot:
		var t := (h[0] as Vector2).distance_to(p) / float(h[1])
		if t >= 1.0:
			continue
		var f := 1.0 - smoothstep(0.45, 1.0, t)
		mult += (float(h[2]) - 1.0) * f
		mill += float(h[3]) * f
	return Vector2(mult, mill)


## Anglers along both outer rails of the Galata Bridge, facing the water.
func _place_anglers() -> void:
	var edges: Array = []
	var centre := Vector2.ZERO
	for d in range(0, roads.edge_count(), 2):
		if roads.name_of(d) == GALATA_BRIDGE and (roads.d_flags[d] & roads.FLAG_BRIDGE) != 0:
			edges.append(d)
			centre += roads.nodes[roads.d_from[d]] + roads.d_dir[d] * roads.d_len[d] * 0.5
	if edges.is_empty():
		return
	centre /= float(edges.size())
	var rng := RandomNumberGenerator.new()
	rng.seed = 0x6a1a7a
	for d: int in edges:
		var dir: Vector2 = roads.d_dir[d]
		var right := Vector2(dir.y, -dir.x)
		var a: Vector2 = roads.nodes[roads.d_from[d]]
		var mid: Vector2 = a + dir * roads.d_len[d] * 0.5
		var out := right if right.dot(mid - centre) > 0.0 else -right
		var off: float = roads.d_halfw[d] + 2.6
		var s := rng.randf_range(0.0, 2.0)
		while s < roads.d_len[d]:
			var p := a + dir * s + out * off
			# Only over the water, clear of the bascule span's gap and the ends.
			if env.call("is_water", p.x + out.x * 4.0, p.y + out.y * 4.0) and rng.randf() < 0.82:
				var y: float = roads.y_at(d, s, a + dir * s)
				var b := Basis(Vector3.UP, atan2(out.x, -out.y) + rng.randf_range(-0.25, 0.25))
				var shirt: Color = SHIRTS[rng.randi_range(0, SHIRTS.size() - 1)]
				var key := Vector2i(int(floor(p.x / CHUNK)), int(floor(p.y / CHUNK)))
				if not _anglers.has(key):
					_anglers[key] = PackedFloat32Array()
					_add_to_chunk(p, -1)
				var buf: PackedFloat32Array = _anglers[key]
				_push(buf, b, Vector3(p.x, y, -p.y), [LM.pack_rgb(shirt), 0.0, 0.0, rng.randf() * 0.2])
				_anglers[key] = buf
				anglers += 1
			s += rng.randf_range(1.4, 3.2)


func _build_chunk(key: Vector2i) -> void:
	if _built.has(key):
		return
	var rng := RandomNumberGenerator.new()
	rng.seed = hash(key) ^ 0x5eed
	var buf := PackedFloat32Array()
	var n := 0
	for d: int in _chunk_edges[key]:
		var cls: String = roads.d_class[d]
		var length: float = roads.d_len[d]
		var dir: Vector2 = roads.d_dir[d]
		var right := Vector2(dir.y, -dir.x)
		var a: Vector2 = roads.nodes[roads.d_from[d]]
		var mid := a + dir * length * 0.5
		var hot := _hot_at(mid)
		var per_m: float = float(IST_BASE[cls]) * clampf(_bulk_at(mid) / 8.0, 0.15, 2.5) * hot.x
		per_m *= float(STREET_MULT.get(roads.name_of(d), 1.0))
		per_m = minf(per_m, MAX_PER_M)
		var wide := cls == "pedestrian"
		var line := cls in ["path", "steps"]
		var sides: Array = [0.0] if (wide or line) else [1.0, -1.0]
		var walk_off: float = 0.0 if (wide or line) else roads.d_halfw[d] + 2.0
		var spread: float = maxf(roads.d_halfw[d] - 0.8, 0.5) if wide else (0.5 if line else 0.9)
		var pieces := maxi(1, int(ceil(length / PIECE_M)))
		var pl := length / float(pieces)
		for side: float in sides:
			for pc in pieces:
				var s0 := pl * pc
				var count := int(per_m * pl + rng.randf())
				for k in count:
					var lateral := walk_off * side + rng.randf_range(-spread, spread)
					var shirt: Color = SHIRTS[rng.randi_range(0, SHIRTS.size() - 1)]
					var phase := rng.randf()
					if rng.randf() < (0.25 if wide else 0.12):
						var s := s0 + rng.randf_range(0.0, pl)
						var p := a + dir * s + right * (lateral + side * 1.0)
						if env.call("building_at", p.x, p.y) >= 0:
							continue
						_push(buf, Basis(Vector3.UP, rng.randf_range(-PI, PI)),
							Vector3(p.x, roads.y_at(d, s, p), -p.y), [LM.pack_rgb(shirt), 0.0, 0.0, phase])
					else:
						var back := rng.randf() < 0.5
						var fwd := -dir if back else dir
						var sa := (s0 + pl) if back else s0
						var sb := s0 if back else (s0 + pl)
						var pa := a + dir * sa + right * lateral
						var pb := a + dir * sb + right * lateral
						var ya: float = roads.y_at(d, sa, pa)
						var yb: float = roads.y_at(d, sb, pb)
						_push_walk(buf, pa - fwd * 0.5, fwd, ya, (yb - ya) / maxf(pl, 0.5), pl + 1.0,
							rng.randf_range(1.0, 1.5), LM.pack_rgb(shirt), phase)
					n += 1
	n += _mill(key, rng, buf)
	var insts: Array = []
	if not buf.is_empty():
		insts.append(_rs_instance(buf, _mesh))
	if _anglers.has(key):
		insts.append(_rs_instance(_anglers[key], _angler_mesh))
		n += (_anglers[key] as PackedFloat32Array).size() / 16
	_built[key] = insts
	_people += n


## A walk from p along fwd (2D), rising `grade` per metre: the person shader
## walks local +Z, so +Z is sheared up the slope and the body stays upright.
func _push_walk(buf: PackedFloat32Array, p: Vector2, fwd: Vector2, y: float, grade: float, length: float,
		speed: float, shirt: float, phase: float) -> void:
	var b := Basis(Vector3.UP, atan2(fwd.x, -fwd.y))
	b.z = b.z + Vector3(0.0, clampf(grade, -0.6, 0.6), 0.0)
	_push(buf, b, Vector3(p.x, y, -p.y), [shirt, length, speed, phase])


## People milling about the squares of the hot spots in this chunk.
func _mill(key: Vector2i, rng: RandomNumberGenerator, buf: PackedFloat32Array) -> int:
	var r0 := Rect2(key.x * CHUNK, key.y * CHUNK, CHUNK, CHUNK)
	var peak := 0.0
	for h: Array in _hot:
		var c: Vector2 = h[0]
		var r: float = h[1]
		if float(h[3]) > 0.0 and r0.grow(r).has_point(c):
			peak = maxf(peak, float(h[3]))
	if peak <= 0.0:
		return 0
	var n := 0
	var tries := int(CHUNK * CHUNK * peak)
	for t in tries:
		var p := Vector2(r0.position.x + rng.randf() * CHUNK, r0.position.y + rng.randf() * CHUNK)
		var want := _hot_at(p).y
		if rng.randf() * peak > want or not _open_ground(p):
			continue
		var shirt := LM.pack_rgb(SHIRTS[rng.randi_range(0, SHIRTS.size() - 1)])
		var y: float = roads.surface_y(p.x, p.y)
		var phase := rng.randf()
		if rng.randf() < 0.35:
			_push(buf, Basis(Vector3.UP, rng.randf_range(-PI, PI)), Vector3(p.x, y, -p.y), [shirt, 0.0, 0.0, phase])
		else:
			var ang := rng.randf_range(-PI, PI)
			var fwd := Vector2(sin(ang), cos(ang))
			var length := rng.randf_range(4.0, 22.0)
			var q := p + fwd * length
			if not _open_ground(q) or not _open_ground(p + fwd * length * 0.5):
				_push(buf, Basis(Vector3.UP, ang), Vector3(p.x, y, -p.y), [shirt, 0.0, 0.0, phase])
			else:
				var yq: float = roads.surface_y(q.x, q.y)
				_push_walk(buf, p, fwd, y, (yq - y) / length, length, rng.randf_range(0.6, 1.3), shirt, phase)
		n += 1
	return n


func _rs_instance(buf: PackedFloat32Array, mesh: ArrayMesh) -> RID:
	var mm := RenderingServer.multimesh_create()
	RenderingServer.multimesh_allocate_data(mm, buf.size() / 16, RenderingServer.MULTIMESH_TRANSFORM_3D, false, true)
	RenderingServer.multimesh_set_mesh(mm, mesh.get_rid())
	RenderingServer.multimesh_set_buffer(mm, buf)
	var aabb := RenderingServer.multimesh_get_aabb(mm).grow(PIECE_M + 10.0)
	RenderingServer.multimesh_set_custom_aabb(mm, aabb)
	var inst := RenderingServer.instance_create2(mm, get_world_3d().scenario)
	RenderingServer.instance_geometry_set_material_override(inst, LM.person_material().get_rid())
	RenderingServer.instance_geometry_set_cast_shadows_setting(inst, RenderingServer.SHADOW_CASTING_SETTING_OFF)
	RenderingServer.instance_geometry_set_visibility_range(inst, 0.0, DRAW_M, 0.0, 0.0,
		RenderingServer.VISIBILITY_RANGE_FADE_DISABLED)
	RenderingServer.instance_set_visible(inst, on)
	_rids.append(inst)
	_rids.append(mm)
	return inst

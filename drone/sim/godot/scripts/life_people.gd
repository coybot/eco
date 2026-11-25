## People on Manhattan's sidewalks and park paths (a part of world_life.gd).
##
## Every street side gets a sidewalk line ~2.2 m outside its kerb, and park
## paths their own line. How many people walk a metre of it is the street's
## class (avenues busiest) times how built-up the blocks around are: the
## building bulk (footprint x height, from the env's own footprints) per
## square metre of a CELL-metre cell, so Midtown and the Financial District
## crowd and the Upper West Side side streets do not. At night the shader
## shows a quarter of them.
##
## Walking is in the vertex shader, not on the CPU: each person carries a
## walk (start, direction, length) and a speed and goes along it, wrapping
## round at the end, where they are scaled out of sight over half a metre
## (that end is a corner, among the crowd). Nothing in GDScript runs per
## person per frame, so tens of thousands cost what drawing them costs.
## People do not cross the roads or avoid each other; from 30 m up and higher
## that is not what reads.
##
## Built lazily per chunk near the cameras and drawn through the
## RenderingServer, as the parked cars (life_parked.gd). Not solid, and not
## props: no one is counted or detected.
extends Node3D

const LM = preload("res://scripts/life_meshes.gd")

const CHUNK := 250.0
const BUILD_M := 900.0
const DRAW_M := 460.0
const CELL := 150.0
const BASE := {"primary": 0.14, "secondary": 0.09, "street": 0.06, "service": 0.02, "path": 0.04}
const SHIRTS := [Color(0.9, 0.9, 0.88), Color(0.12, 0.13, 0.16), Color(0.2, 0.25, 0.4), Color(0.55, 0.15, 0.12),
	Color(0.06, 0.06, 0.07), Color(0.75, 0.68, 0.5), Color(0.3, 0.45, 0.35), Color(0.6, 0.6, 0.62),
	Color(0.42, 0.3, 0.22), Color(0.7, 0.45, 0.1), Color(0.35, 0.2, 0.4)]

var env: Node3D
var roads
var on := false
var _mesh: ArrayMesh
var _bulk := {}                             # Vector2i -> building metres per m^2
var _chunk_edges := {}
var _built := {}
var _rids: Array[RID] = []
var _todo: Array[Vector2i] = []
var _people := 0


func setup(env_node: Node3D, road_data) -> void:
	env = env_node
	roads = road_data
	name = "people"


func _exit_tree() -> void:
	for r in _rids:
		RenderingServer.free_rid(r)
	_rids.clear()


func set_on(want: bool) -> void:
	if want and _mesh == null:
		_prepare()
	on = want and _mesh != null
	for key in _built:
		for inst: RID in _built[key]:
			RenderingServer.instance_set_visible(inst, on)


func count() -> int:
	return _people


func _prepare() -> void:
	if roads == null or not roads.loaded:
		return
	_mesh = LM.person_mesh()
	var rects: Array = env.get("_rects")
	var heights: PackedFloat32Array = env.get("_heights")
	for i in rects.size():
		var r: Rect2 = rects[i]
		var c := r.get_center()
		var key := Vector2i(int(floor(c.x / CELL)), int(floor(c.y / CELL)))
		_bulk[key] = float(_bulk.get(key, 0.0)) + r.get_area() * heights[i] / (CELL * CELL)
	for d in range(0, roads.edge_count(), 2):
		var cls: String = roads.d_class[d]
		if not BASE.has(cls):
			continue
		if (roads.d_flags[d] & (roads.FLAG_BRIDGE | roads.FLAG_TUNNEL | roads.FLAG_BELOW)) != 0:
			continue
		var mid: Vector2 = roads.nodes[roads.d_from[d]] + roads.d_dir[d] * roads.d_len[d] * 0.5
		var key := Vector2i(int(floor(mid.x / CHUNK)), int(floor(mid.y / CHUNK)))
		if not _chunk_edges.has(key):
			_chunk_edges[key] = PackedInt32Array()
		var arr: PackedInt32Array = _chunk_edges[key]
		arr.append(d)
		_chunk_edges[key] = arr


## How built-up the blocks around (e, n) are: building metres per square metre
## of ground, averaged over the cell and its neighbours.
func _bulk_at(p: Vector2) -> float:
	var k := Vector2i(int(floor(p.x / CELL)), int(floor(p.y / CELL)))
	var s := 0.0
	for dy in [-1, 0, 1]:
		for dx in [-1, 0, 1]:
			s += float(_bulk.get(k + Vector2i(dx, dy), 0.0))
	return s / 9.0


func update(cams: Array, budget_ms: float = 3.0) -> void:
	if not on:
		return
	for cam: Camera3D in cams:
		var p := cam.global_position
		var c := Vector2(p.x, -p.z)
		# People are only worth building under a low camera.
		var reach := BUILD_M if p.y < 600.0 else 0.0
		var k0 := Vector2i(int(floor((c.x - reach) / CHUNK)), int(floor((c.y - reach) / CHUNK)))
		var k1 := Vector2i(int(floor((c.x + reach) / CHUNK)), int(floor((c.y + reach) / CHUNK)))
		for ky in range(k0.y, k1.y + 1):
			for kx in range(k0.x, k1.x + 1):
				var key := Vector2i(kx, ky)
				if _built.has(key) or not _chunk_edges.has(key) or key in _todo:
					continue
				if Vector2((kx + 0.5) * CHUNK, (ky + 0.5) * CHUNK).distance_to(c) < reach:
					_todo.append(key)
	var t0 := Time.get_ticks_usec()
	while not _todo.is_empty() and float(Time.get_ticks_usec() - t0) < budget_ms * 1000.0:
		_build_chunk(_todo.pop_front())


func build_near(c: Vector2, reach: float) -> void:
	for key: Vector2i in _chunk_edges:
		if not _built.has(key) and Vector2((key.x + 0.5) * CHUNK, (key.y + 0.5) * CHUNK).distance_to(c) < reach:
			_build_chunk(key)


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
		var bulk := _bulk_at(mid)
		var per_m: float = float(BASE[cls]) * clampf(bulk / 16.0, 0.15, 4.0)
		var path := cls == "path"
		var sides: Array = [0.0] if path else [1.0, -1.0]
		var walk_off: float = 0.0 if path else roads.d_halfw[d] + 2.2
		for side: float in sides:
			var count := int(per_m * length + rng.randf())
			for k in count:
				var lateral := walk_off * side + rng.randf_range(-1.1, 1.1) * (0.6 if path else 1.0)
				var shirt: Color = SHIRTS[rng.randi_range(0, SHIRTS.size() - 1)]
				var phase := rng.randf()
				if rng.randf() < 0.14:
					# Standing: at the building line, any way round.
					var s := rng.randf_range(0.0, length)
					var p := a + dir * s + right * (lateral + side * 1.2)
					_push(buf, Basis(Vector3.UP, rng.randf_range(-PI, PI)), Vector3(p.x, 0.0, -p.y),
						[LM.pack_rgb(shirt), 0.0, 0.0, phase])
				else:
					var fwd := dir if rng.randf() < 0.5 else -dir
					var start := (a if fwd == dir else a + dir * length) + right * lateral - fwd * 1.0
					_push(buf, Basis(Vector3.UP, atan2(fwd.x, -fwd.y)), Vector3(start.x, 0.0, -start.y),
						[LM.pack_rgb(shirt), length + 2.0, rng.randf_range(1.05, 1.6), phase])
				n += 1
	var insts: Array = []
	if not buf.is_empty():
		var mm := RenderingServer.multimesh_create()
		RenderingServer.multimesh_allocate_data(mm, buf.size() / 16, RenderingServer.MULTIMESH_TRANSFORM_3D, false, true)
		RenderingServer.multimesh_set_mesh(mm, _mesh.get_rid())
		RenderingServer.multimesh_set_buffer(mm, buf)
		# The walks reach past the chunk; the bounds must too, or a walker
		# vanishes when its start's chunk leaves the view.
		var aabb := RenderingServer.multimesh_get_aabb(mm).grow(200.0)
		RenderingServer.multimesh_set_custom_aabb(mm, aabb)
		var inst := RenderingServer.instance_create2(mm, get_world_3d().scenario)
		RenderingServer.instance_geometry_set_material_override(inst, LM.person_material().get_rid())
		RenderingServer.instance_geometry_set_cast_shadows_setting(inst, RenderingServer.SHADOW_CASTING_SETTING_OFF)
		RenderingServer.instance_geometry_set_visibility_range(inst, 0.0, DRAW_M, 0.0, 0.0,
			RenderingServer.VISIBILITY_RANGE_FADE_DISABLED)
		RenderingServer.instance_set_visible(inst, on)
		_rids.append(inst)
		_rids.append(mm)
		insts.append(inst)
	_built[key] = insts
	_people += n


static func _push(buf: PackedFloat32Array, b: Basis, o: Vector3, cu: Array) -> void:
	buf.append_array([b.x.x, b.y.x, b.z.x, o.x, b.x.y, b.y.y, b.z.y, o.y, b.x.z, b.y.z, b.z.z, o.z,
		cu[0], cu[1], cu[2], cu[3]])

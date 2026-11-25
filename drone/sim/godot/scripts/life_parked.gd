## Parked cars along Manhattan's kerbs (a part of world_life.gd).
##
## Where: every street side roads.json says has a parking lane (CSCL
## number_park_lanes; 2 = both kerbs, 1 = one, chosen by the edge's hash), not
## on bridges, tunnels, below-grade or elevated roadways, and not within a
## crosswalk's reach of a junction. Cars stand ~1.1 m off the kerb at 6.6-7.8 m
## pitch with one space in seven empty, nose to the traffic beside them (both
## kerbs face the flow on a one-way street).
##
## About 200,000 cars island-wide, so they are built lazily: a CHUNK-metre
## chunk is filled when a camera first comes within BUILD_M of it, a few chunks
## a frame, and then kept. Nothing about a chunk depends on when it was built
## (each is seeded by its own position), so the scene is the same every run.
##
## Drawn straight through the RenderingServer, not as nodes: a chunk is a
## MultiMesh per kind and per detail band, and as nodes the city's few
## thousand of them would cost every find_children() walk in the sim
## (thermal_kit rescans the tree every two seconds). Bands: the model near,
## its clustered reduction mid, a box far, handed over car by car in the
## vertex shader (life_meshes.gd). No real shadows (a dark blob instead):
## tens of thousands of cars in four shadow cascades cost more than the
## rest of the street.
extends Node3D

const LM = preload("res://scripts/life_meshes.gd")

const CHUNK := 300.0
const BUILD_M := 1900.0
const NEAR_M := 110.0
const MID_M := 460.0
const FAR_M := 1500.0
const KINDS := [["sedan", 0.3], ["suv", 0.27], ["hatch", 0.1], ["minivan", 0.1], ["wagon", 0.07],
	["compact", 0.09], ["van", 0.07]]
const PARK_CLASSES := ["primary", "secondary", "street", "service"]

var env: Node3D
var roads
var on := false
var _kinds: Array[String] = []
var _cum := PackedFloat32Array()
var _dims: Array[Vector3] = []
var _near_mat: ShaderMaterial
var _mid_mat: ShaderMaterial
var _far_mat: ShaderMaterial
var _box: ArrayMesh
var _chunk_edges := {}                      # Vector2i -> PackedInt32Array of directed edges (way 0)
var _built := {}                            # Vector2i -> Array of RS instance RIDs
var _rids: Array[RID] = []                  # every RS resource, for freeing
var _cars := 0
var _todo: Array[Vector2i] = []


func setup(env_node: Node3D, road_data) -> void:
	env = env_node
	roads = road_data
	name = "parked"


func materials() -> Array:
	return [_near_mat, _mid_mat, _far_mat]


func _exit_tree() -> void:
	for r in _rids:
		RenderingServer.free_rid(r)
	_rids.clear()


func set_on(want: bool) -> void:
	if want and _kinds.is_empty():
		_prepare()
	on = want and not _kinds.is_empty()
	for key in _built:
		for inst: RID in _built[key]:
			RenderingServer.instance_set_visible(inst, on)


func count() -> int:
	return _cars


func _prepare() -> void:
	if roads == null or not roads.loaded:
		return
	var acc := 0.0
	for e: Array in KINDS:
		var d: Dictionary = LM.kind(str(e[0]))
		if d.is_empty():
			continue
		_kinds.append(str(e[0]))
		_dims.append(d["dims"])
		acc += float(e[1])
		_cum.append(acc)
	for k in _cum.size():
		_cum[k] /= acc
	_near_mat = LM.lod_material(0.0, NEAR_M)
	_mid_mat = LM.lod_material(NEAR_M, MID_M)
	_far_mat = LM.lod_material(MID_M, FAR_M)
	_box = LM._box_car(Vector3(1, 1, 1))
	for d in range(0, roads.edge_count(), 2):
		if roads.d_park[d] <= 0 or not (roads.d_class[d] in PARK_CLASSES):
			continue
		if (roads.d_flags[d] & (roads.FLAG_BRIDGE | roads.FLAG_TUNNEL | roads.FLAG_BELOW | roads.FLAG_ELEVATED)) != 0:
			continue
		var mid: Vector2 = roads.nodes[roads.d_from[d]] + roads.d_dir[d] * roads.d_len[d] * 0.5
		var key := Vector2i(int(floor(mid.x / CHUNK)), int(floor(mid.y / CHUNK)))
		if not _chunk_edges.has(key):
			_chunk_edges[key] = PackedInt32Array()
		var arr: PackedInt32Array = _chunk_edges[key]
		arr.append(d)
		_chunk_edges[key] = arr
	print("[world_life] parked: %d chunks with kerbside parking" % _chunk_edges.size())


## Queue the chunks near the cameras; build a few a frame.
func update(cams: Array, budget_ms: float = 4.0) -> void:
	if not on:
		return
	for cam: Camera3D in cams:
		var p := cam.global_position
		var c := Vector2(p.x, -p.z)
		var reach := BUILD_M
		var k0 := Vector2i(int(floor((c.x - reach) / CHUNK)), int(floor((c.y - reach) / CHUNK)))
		var k1 := Vector2i(int(floor((c.x + reach) / CHUNK)), int(floor((c.y + reach) / CHUNK)))
		for ky in range(k0.y, k1.y + 1):
			for kx in range(k0.x, k1.x + 1):
				var key := Vector2i(kx, ky)
				if _built.has(key) or not _chunk_edges.has(key) or key in _todo:
					continue
				if Vector2((kx + 0.5) * CHUNK, (ky + 0.5) * CHUNK).distance_to(c) < reach:
					_todo.append(key)
	if _todo.is_empty():
		return
	# Nearest first.
	var cam0: Camera3D = cams[0] if not cams.is_empty() else null
	if cam0 != null:
		var c0 := Vector2(cam0.global_position.x, -cam0.global_position.z)
		_todo.sort_custom(func(a: Vector2i, b: Vector2i) -> bool:
			return Vector2((a.x + 0.5) * CHUNK, (a.y + 0.5) * CHUNK).distance_squared_to(c0) \
				< Vector2((b.x + 0.5) * CHUNK, (b.y + 0.5) * CHUNK).distance_squared_to(c0))
	var t0 := Time.get_ticks_usec()
	while not _todo.is_empty() and float(Time.get_ticks_usec() - t0) < budget_ms * 1000.0:
		_build_chunk(_todo.pop_front())


## Build every chunk within `reach` of `c` now (for a first frame that must
## already show them, e.g. a still capture).
func build_near(c: Vector2, reach: float) -> void:
	for key: Vector2i in _chunk_edges:
		if not _built.has(key) and Vector2((key.x + 0.5) * CHUNK, (key.y + 0.5) * CHUNK).distance_to(c) < reach:
			_build_chunk(key)


func _build_chunk(key: Vector2i) -> void:
	if _built.has(key):
		return
	var rng := RandomNumberGenerator.new()
	rng.seed = hash(key)
	var per_kind: Array = []
	for k in _kinds.size():
		per_kind.append(PackedFloat32Array())
	var far := PackedFloat32Array()
	var n := 0
	for d: int in _chunk_edges[key]:
		var length: float = roads.d_len[d]
		var dir: Vector2 = roads.d_dir[d]
		var right := Vector2(dir.y, -dir.x)
		var from: int = roads.d_from[d]
		var to: int = roads.d_from[d + 1]
		var off: float = roads.d_halfw[d] - 1.15
		if off < 2.2:
			continue
		var sides: Array = [1.0, -1.0] if roads.d_park[d] >= 2 else [1.0 if (d / 2) % 2 == 0 else -1.0]
		var s0: float = (roads.node_clear[from] + 5.0) if roads.node_deg[from] >= 3 else 1.0
		var s1: float = length - ((roads.node_clear[to] + 5.0) if roads.node_deg[to] >= 3 else 1.0)
		var oneway: bool = roads.d_oneway[d] == 1
		for side: float in sides:
			var s := s0 + rng.randf_range(0.0, 3.0)
			while s < s1:
				var k := mini(_cum.bsearch(rng.randf()), _kinds.size() - 1)
				var dims: Vector3 = _dims[k]
				var pitch := dims.x + rng.randf_range(1.6, 2.8)
				if s + dims.x > s1:
					break
				if rng.randf() < 0.86:
					var p: Vector2 = roads.nodes[from] + dir * (s + dims.x * 0.5) + right * side * off
					# Nose with the traffic on its side of the street.
					var fwd := dir if (side > 0.0 or oneway) else -dir
					var yaw := atan2(fwd.x, -fwd.y) + rng.randf_range(-0.03, 0.03)
					var b := Basis(Vector3.UP, yaw)
					var o := Vector3(p.x, 0.0, -p.y)
					var paint: Color = LM.BLACK_CAR if rng.randf() < 0.14 else LM.PAINTS[rng.randi_range(0, LM.PAINTS.size() - 1)]
					if _kinds[k] == "van":
						paint = Color(0.92, 0.92, 0.91)
					var warm := rng.randf_range(0.6, 1.0) if rng.randf() < 0.08 else rng.randf_range(0.0, 0.15)
					var cu := [LM.pack_rgb(paint), 0.0, 0.0, warm]
					_push(per_kind[k], b, o, cu)
					var bs := b * Basis.from_scale(Vector3(dims.z, dims.y, dims.x))
					_push(far, bs, o, cu)
					n += 1
				s += pitch
	var insts: Array = []
	var scen := get_world_3d().scenario
	for k in _kinds.size():
		var buf: PackedFloat32Array = per_kind[k]
		if buf.is_empty():
			continue
		var d: Dictionary = LM.kind(_kinds[k])
		insts.append(_instance(scen, d["lod0"], buf, _near_mat, 0.0, NEAR_M + CHUNK * 0.75))
		insts.append(_instance(scen, d["lod1"], buf, _mid_mat, maxf(0.0, NEAR_M - CHUNK * 0.75), MID_M + CHUNK * 0.75))
	if not far.is_empty():
		insts.append(_instance(scen, _box, far, _far_mat, maxf(0.0, MID_M - CHUNK * 0.75), FAR_M + CHUNK * 0.75))
	_built[key] = insts
	_cars += n


static func _push(buf: PackedFloat32Array, b: Basis, o: Vector3, cu: Array) -> void:
	buf.append_array([b.x.x, b.y.x, b.z.x, o.x, b.x.y, b.y.y, b.z.y, o.y, b.x.z, b.y.z, b.z.z, o.z,
		cu[0], cu[1], cu[2], cu[3]])


func _instance(scen: RID, mesh: Mesh, buf: PackedFloat32Array, mat: Material, vis_begin: float,
		vis_end: float) -> RID:
	var mm := RenderingServer.multimesh_create()
	RenderingServer.multimesh_allocate_data(mm, buf.size() / 16, RenderingServer.MULTIMESH_TRANSFORM_3D, false, true)
	RenderingServer.multimesh_set_mesh(mm, mesh.get_rid())
	RenderingServer.multimesh_set_buffer(mm, buf)
	var inst := RenderingServer.instance_create2(mm, scen)
	RenderingServer.instance_geometry_set_material_override(inst, mat.get_rid())
	RenderingServer.instance_geometry_set_cast_shadows_setting(inst, RenderingServer.SHADOW_CASTING_SETTING_OFF)
	RenderingServer.instance_geometry_set_visibility_range(inst, vis_begin, vis_end, 0.0, 0.0,
		RenderingServer.VISIBILITY_RANGE_FADE_DISABLED)
	RenderingServer.instance_set_visible(inst, on)
	_rids.append(inst)
	_rids.append(mm)
	return inst

## Parked cars along Istanbul's streets (a part of istanbul_life.gd):
## Manhattan's parked cars (life_parked.gd: lazy chunks near the cameras, drawn
## through the RenderingServer in three detail bands) on Istanbul's kerbs.
##
## Where: OSM has no parking lanes here, so istanbul_life_roads derives them
## from the street (d_park: one kerb on a one-way street of 6 m or more, one
## or both on a two-way street of 9.5 / 12 m or more; none on stone-paved
## lanes, bridges, tunnels, steps or pedestrian streets, so the old city's
## alleys stay clear). Cars stand on the drawn ground, pitched with the hill.
## The mix leans white, as Turkish streets do, with a few light vans and a
## parked taxi now and then.
extends "res://scripts/life_parked.gd"

const IM = preload("res://scripts/istanbul_life_meshes.gd")

const IST_KINDS := [["sedan", 0.3], ["hatch", 0.24], ["compact", 0.12], ["suv", 0.1],
	["minivan", 0.08], ["van", 0.11], ["taxi", 0.05]]


func _prepare() -> void:
	if roads == null or not roads.loaded:
		return
	var acc := 0.0
	for e: Array in IST_KINDS:
		var d: Dictionary = IM.kind(str(e[0]))
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
		if roads.d_park[d] <= 0:
			continue
		var mid: Vector2 = roads.nodes[roads.d_from[d]] + roads.d_dir[d] * roads.d_len[d] * 0.5
		var key := Vector2i(int(floor(mid.x / CHUNK)), int(floor(mid.y / CHUNK)))
		if not _chunk_edges.has(key):
			_chunk_edges[key] = PackedInt32Array()
		var arr: PackedInt32Array = _chunk_edges[key]
		arr.append(d)
		_chunk_edges[key] = arr
	print("[istanbul_life] parked: %d chunks with kerbside parking" % _chunk_edges.size())


func _build_chunk(key: Vector2i) -> void:
	if _built.has(key):
		return
	var rng := RandomNumberGenerator.new()
	rng.seed = hash(key)
	var per_kind: Array = []
	for k in _kinds.size():
		per_kind.append(PackedFloat32Array())
	var near := {}
	var far := PackedFloat32Array()
	var n := 0
	for d: int in _chunk_edges[key]:
		var length: float = roads.d_len[d]
		var dir: Vector2 = roads.d_dir[d]
		var right := Vector2(dir.y, -dir.x)
		var from: int = roads.d_from[d]
		var to: int = roads.d_from[d + 1]
		var off: float = roads.d_halfw[d] - 1.1
		if off < 1.9:
			continue
		var sides: Array = [1.0, -1.0] if roads.d_park[d] >= 2 else [1.0 if (d / 2) % 2 == 0 else -1.0]
		var s0: float = (roads.node_clear[from] + 4.0) if roads.node_deg[from] >= 3 else 1.0
		var s1: float = length - ((roads.node_clear[to] + 4.0) if roads.node_deg[to] >= 3 else 1.0)
		var oneway: bool = roads.d_oneway[d] == 1
		for side: float in sides:
			var s := s0 + rng.randf_range(0.0, 3.0)
			while s < s1:
				var k := mini(_cum.bsearch(rng.randf()), _kinds.size() - 1)
				var dims: Vector3 = _dims[k]
				var pitch := dims.x + rng.randf_range(1.2, 2.6)
				if s + dims.x > s1:
					break
				var p: Vector2 = roads.nodes[from] + dir * (s + dims.x * 0.5) + right * side * off
				var clear: bool = not env.call("is_water", p.x, p.y) and env.call("building_at", p.x, p.y) < 0
				for pr: Array in roads.props:
					if p.distance_to(pr[0]) < float(pr[1]) + 4.0:
						clear = false
				if clear and rng.randf() < 0.8:
					var fwd := dir if (side > 0.0 or oneway) else -dir
					var yaw := atan2(fwd.x, -fwd.y) + rng.randf_range(-0.04, 0.04)
					var g: float = (roads.surface_y(p.x + fwd.x * 2.0, p.y + fwd.y * 2.0)
						- roads.surface_y(p.x - fwd.x * 2.0, p.y - fwd.y * 2.0)) / 4.0
					var b := Basis(Vector3.UP, yaw) * Basis(Vector3.RIGHT, -atan(clampf(g, -0.35, 0.35)))
					var o := Vector3(p.x, roads.surface_y(p.x, p.y), -p.y)
					var paint: Color = IM.PAINTS[rng.randi_range(0, IM.PAINTS.size() - 1)]
					if _kinds[k] == "van":
						paint = Color(0.92, 0.92, 0.91)
					elif _kinds[k] == "taxi":
						paint = IM.TAXI_YELLOW
					var warm := rng.randf_range(0.6, 1.0) if rng.randf() < 0.08 else rng.randf_range(0.0, 0.15)
					var cu := [LM.pack_rgb(paint), 0.0, 0.0, warm]
					_push(per_kind[k], b, o, cu)
					var sk := Vector3i(k, int(floor(p.x / SUB)), int(floor(p.y / SUB)))
					if not near.has(sk):
						near[sk] = PackedFloat32Array()
					var nb: PackedFloat32Array = near[sk]
					_push(nb, b, o, cu)
					near[sk] = nb
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
		var d: Dictionary = IM.kind(_kinds[k])
		insts.append(_instance(scen, d["lod1"], buf, _mid_mat, maxf(0.0, NEAR_M - CHUNK * 0.75), MID_M + CHUNK * 0.75))
	for sk: Vector3i in near:
		var d: Dictionary = IM.kind(_kinds[sk.x])
		insts.append(_instance(scen, d["lod0"], near[sk], _near_mat, 0.0, NEAR_M + SUB * 0.75))
	if not far.is_empty():
		var c := Vector2((key.x + 0.5) * CHUNK, (key.y + 0.5) * CHUNK)
		var sk := Vector2i(int(floor(c.x / SUPER)), int(floor(c.y / SUPER)))
		var fb: PackedFloat32Array = _far_buf.get(sk, PackedFloat32Array())
		fb.append_array(far)
		_far_buf[sk] = fb
		if _far_mm.has(sk):
			var mm: RID = _far_mm[sk][1]
			RenderingServer.multimesh_allocate_data(mm, fb.size() / 16, RenderingServer.MULTIMESH_TRANSFORM_3D, false, true)
			RenderingServer.multimesh_set_buffer(mm, fb)
		else:
			var inst := _instance(scen, _box, fb, _far_mat, 0.0, FAR_M + SUPER * 0.75)
			_far_mm[sk] = [inst, _rids[_rids.size() - 1]]
			insts.append(inst)
	_built[key] = insts
	_cars += n

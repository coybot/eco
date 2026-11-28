## Cars parked in Baylands' car parks (a part of baylands_life.gd):
## Manhattan's parked cars (life_parked.gd: lazy chunks near the cameras,
## three detail bands, drawn through the RenderingServer) in the region's
## surface lots instead of at the kerb.
##
## WHERE: every OSM surface car park, cut into stalls on its own axis by
## bake_baylands_life.py (2.7 x 5.5 m stalls, 7.3 m aisles), minus the stalls
## the 2020 lidar sees a tree, a building or a light pole over. The stalls
## line up with the striping in the 2024 NAIP photo.
##
## HOW FULL, BY THE CLOCK: office lots (the business park) about 55 % from
## 9 to 16 on weekdays (Bay Area office occupancy since 2022) and nearly
## empty at weekends; the Twin Creeks lot full on weekend days and busy on
## weekday evenings; the park's lots by the park's own visitors; apartments
## full at night, half by day; the stadium / theme-park lots almost empty
## (no event). Cars fill in clusters, not as noise.
##
## SOLID: each car near a camera is a static box body on collision layer 1,
## so a low quad's strike sweep hits it. The lots' own cars painted into the
## photo are still under them.
extends "res://scripts/life_parked.gd"

const BLT = preload("res://scripts/baylands_life_traffic.gd")

const BL_KINDS := [["sedan", 0.3], ["suv", 0.3], ["pickup", 0.1], ["hatch", 0.08], ["compact", 0.08],
	["minivan", 0.1], ["wagon", 0.04]]

var life: Dictionary = {}
var hour := 14.0
var weekend := false
var density := 1.0
var _lots: Array = []
var _chunk_stalls := {}                    # Vector2i -> PackedFloat32Array (e, n, yaw, occupancy)
var _bodies: Array[RID] = []
var _shapes: Array[RID] = []
var stall_count := 0


func setup_lots(env_node: Node3D, data: Dictionary) -> void:
	env = env_node
	life = data
	name = "parked"


## 0..1 of a lot's stalls taken at hour h.
static func occupancy(kind: String, h: float, we: bool) -> float:
	var day := smoothstep(7.0, 9.0, h) * (1.0 - smoothstep(16.5, 19.0, h))
	match kind:
		"office":
			return 0.06 if we else 0.06 + 0.5 * day
		"sports":
			if we:
				return 0.1 + 0.8 * smoothstep(7.5, 9.0, h) * (1.0 - smoothstep(17.5, 19.0, h))
			return 0.08 + 0.55 * smoothstep(16.5, 17.5, h) * (1.0 - smoothstep(20.5, 21.5, h))
		"park":
			var peak := 0.75 if we else 0.25
			return 0.02 + peak * smoothstep(8.0, 11.0, h) * (1.0 - smoothstep(17.0, 20.0, h))
		"home":
			return 0.85 - 0.35 * day if not we else 0.75
		"shop":
			return 0.05 + 0.45 * smoothstep(9.0, 11.0, h) * (1.0 - smoothstep(19.0, 21.0, h))
		"event":
			return 0.02
	return 0.3


func set_clock(h: float, we: bool, dens: float) -> void:
	var changed := absf(h - hour) > 0.25 or we != weekend or absf(dens - density) > 0.01
	hour = h
	weekend = we
	density = dens
	if changed and not _kinds.is_empty():
		_rebuild()


func _rebuild() -> void:
	for r in _rids:
		RenderingServer.free_rid(r)
	_rids.clear()
	_free_bodies()
	_built.clear()
	_far_buf.clear()
	_far_mm.clear()
	_todo.clear()
	_cars = 0


func _free_bodies() -> void:
	for b in _bodies:
		PhysicsServer3D.free_rid(b)
	_bodies.clear()


func _exit_tree() -> void:
	super._exit_tree()
	_free_bodies()
	for s in _shapes:
		PhysicsServer3D.free_rid(s)
	_shapes.clear()


func set_on(want: bool) -> void:
	super.set_on(want)
	var space := get_world_3d().space if on else RID()
	for b in _bodies:
		PhysicsServer3D.body_set_space(b, space)


func _prepare() -> void:
	var acc := 0.0
	for e: Array in BL_KINDS:
		var d: Dictionary = BLT.kind_mesh(str(e[0]))
		if d.is_empty():
			continue
		_kinds.append(str(e[0]))
		_dims.append(d["dims"])
		acc += float(e[1])
		_cum.append(acc)
		var dims: Vector3 = d["dims"]
		var shape := PhysicsServer3D.box_shape_create()
		PhysicsServer3D.shape_set_data(shape, Vector3(dims.z, dims.y, dims.x) * 0.5)
		_shapes.append(shape)
	for k in _cum.size():
		_cum[k] /= acc
	_near_mat = LM.lod_material(0.0, NEAR_M)
	_mid_mat = LM.lod_material(NEAR_M, MID_M)
	_far_mat = LM.lod_material(MID_M, FAR_M)
	_box = LM._box_car(Vector3(1, 1, 1))
	_lots = life.get("lots", [])
	var kinds := ["office", "sports", "park", "home", "shop", "event"]
	for li in _lots.size():
		var lot: Dictionary = _lots[li]
		var s: Array = lot["s"]
		var kk := kinds.find(str(lot["kind"]))
		for j in range(0, s.size(), 3):
			var p := Vector2(float(s[j]), float(s[j + 1]))
			var key := Vector2i(int(floor(p.x / CHUNK)), int(floor(p.y / CHUNK)))
			if not _chunk_stalls.has(key):
				_chunk_stalls[key] = PackedFloat32Array()
				_chunk_edges[key] = PackedInt32Array()      # life_parked.update() looks here
			var arr: PackedFloat32Array = _chunk_stalls[key]
			arr.append_array([p.x, p.y, float(s[j + 2]), float(kk)])
			_chunk_stalls[key] = arr
			stall_count += 1
	print("[baylands_life] parked: %d stalls in %d lots, %d chunks" % [stall_count, _lots.size(), _chunk_stalls.size()])


func _build_chunk(key: Vector2i) -> void:
	if _built.has(key):
		return
	var rng := RandomNumberGenerator.new()
	rng.seed = hash(key) ^ int(hour * 4.0) ^ (77 if weekend else 0)
	var kinds := ["office", "sports", "park", "home", "shop", "event"]
	var occ := PackedFloat32Array()
	for k: String in kinds:
		occ.append(clampf(occupancy(k, hour, weekend) * density, 0.0, 0.97))
	var per_kind: Array = []
	for k in _kinds.size():
		per_kind.append(PackedFloat32Array())
	var near := {}
	var far := PackedFloat32Array()
	var n := 0
	var st: PackedFloat32Array = _chunk_stalls.get(key, PackedFloat32Array())
	var space := get_world_3d().space
	for j in range(0, st.size(), 4):
		var p := Vector2(st[j], st[j + 1])
		# Clustered filling: a smooth field over the lot, so cars bunch
		# (near the doors, in the shade) instead of a salt-and-pepper lot.
		var field := 0.5 + 0.28 * sin(p.x * 0.041 + p.y * 0.017) + 0.22 * sin(p.y * 0.053 - p.x * 0.029)
		var o: float = occ[int(st[j + 3])]
		if rng.randf() > clampf(o + (field - 0.5) * 1.1 * minf(o, 1.0 - o) * 2.0, 0.0, 1.0):
			continue
		var k := mini(_cum.bsearch(rng.randf()), _kinds.size() - 1)
		var dims: Vector3 = _dims[k]
		var nose := float(st[j + 2]) + (PI if rng.randf() < 0.25 else 0.0)
		var yaw := atan2(cos(nose), -sin(nose)) + rng.randf_range(-0.04, 0.04)
		var b := Basis(Vector3.UP, yaw)
		var g := float(env.call("ground_height", p.x, p.y))
		var org := Vector3(p.x + rng.randf_range(-0.15, 0.15), g, -p.y)
		var paint: Color = BLT.BL_PAINTS[rng.randi_range(0, BLT.BL_PAINTS.size() - 1)]
		var warm := rng.randf_range(0.5, 1.0) if rng.randf() < 0.06 else rng.randf_range(0.0, 0.15)
		var cu := [LM.pack_rgb(paint), 0.0, 0.0, warm]
		_push(per_kind[k], b, org, cu)
		var sk := Vector3i(k, int(floor(p.x / SUB)), int(floor(p.y / SUB)))
		if not near.has(sk):
			near[sk] = PackedFloat32Array()
		var nb: PackedFloat32Array = near[sk]
		_push(nb, b, org, cu)
		near[sk] = nb
		_push(far, b * Basis.from_scale(Vector3(dims.z, dims.y, dims.x)), org, cu)
		var body := PhysicsServer3D.body_create()
		PhysicsServer3D.body_set_mode(body, PhysicsServer3D.BODY_MODE_STATIC)
		PhysicsServer3D.body_add_shape(body, _shapes[k], Transform3D(Basis(), Vector3(0.0, dims.y * 0.5, 0.0)))
		PhysicsServer3D.body_set_collision_layer(body, 1)
		PhysicsServer3D.body_set_collision_mask(body, 0)
		PhysicsServer3D.body_set_state(body, PhysicsServer3D.BODY_STATE_TRANSFORM, Transform3D(b, org))
		PhysicsServer3D.body_set_space(body, space if on else RID())
		_bodies.append(body)
		n += 1
	var insts: Array = []
	var scen := get_world_3d().scenario
	for k in _kinds.size():
		var buf: PackedFloat32Array = per_kind[k]
		if buf.is_empty():
			continue
		var d: Dictionary = BLT.kind_mesh(_kinds[k])
		insts.append(_instance(scen, d["lod1"], buf, _mid_mat, maxf(0.0, NEAR_M - CHUNK * 0.75), MID_M + CHUNK * 0.75))
	for sk: Vector3i in near:
		var d: Dictionary = BLT.kind_mesh(_kinds[sk.x])
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


## Parked cars within `radius` of c (from the built chunks' bodies).
func actors_near(c: Vector2, radius: float, out: Array) -> void:
	if not on:
		return
	for b in _bodies:
		var xf: Transform3D = PhysicsServer3D.body_get_state(b, PhysicsServer3D.BODY_STATE_TRANSFORM)
		var p := Vector2(xf.origin.x, -xf.origin.z)
		if p.distance_to(c) > radius:
			continue
		var fwd := xf.basis.z
		out.append({"class": "vehicle", "kind": "parked_car", "moving": false,
			"e": snappedf(p.x, 0.01), "n": snappedf(p.y, 0.01), "z": snappedf(xf.origin.y, 0.01),
			"heading": snappedf(atan2(-fwd.z, fwd.x), 0.01), "speed": 0.0, "size": [4.6, 1.85, 1.5]})

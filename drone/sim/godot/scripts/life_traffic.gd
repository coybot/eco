## Moving road traffic for the Manhattan photo scene (a part of world_life.gd).
##
## WHAT IT DRIVES. NYC's centreline network (life_roads.gd): right-hand lanes,
## one-way streets one way, nothing on paths, tunnels, below-grade roadways or
## bridge decks. Junctions where city streets meet are signalled the way
## Manhattan's are: the avenue (dataset north-south) approaches and the street
## (east-west) approaches take turns on a 90 s cycle, offset along the avenue
## so a platoon going north meets greens. Unsignalled junctions (ramps, service
## roads) give way by class: the lesser road stops, then goes when the junction
## is clear. Car following is a stopping-distance rule: never faster than can
## stop behind the vehicle ahead or at a red line.
##
## The mix is midday Manhattan: yellow taxis, black for-hire sedans and SUVs,
## private cars, delivery vans, box trucks, MTA buses (avenues and wide
## streets only) and a few police cars. Vans, trucks and buses keep off the
## parkways, as the city's commercial-vehicle rules make them.
##
## WHERE. Only near the cameras. Simulating the island's 2,000 lane-km at
## Manhattan density is ~40,000 vehicles, which GDScript cannot step at frame
## rate and nobody could see. Each current camera (the window's, every aircraft
## and vantage camera; up to MAX_FOCI) gets a bubble around the ground it looks
## at, filled at the class densities in DENSITY up to its share of the pool.
## A vehicle that drives out of its bubble is put back in on its outer ring,
## where a car is a few pixels; when a camera jumps (a new vantage), its
## bubble refills from scratch.
##
## HOW IT STAYS CHEAP. Every vehicle ticks every TICK_SPLIT-th frame (vehicles
## staggered across frames, ~5 Hz each) and the vertex shader carries it along
## between ticks (life_meshes.gd). Each kind has three meshes (the model, a
## clustered reduction, a box) in MultiMeshes whose used slots are kept
## packed, so a frame draws only the vehicles out, at the detail their distance
## from their camera needs.
##
## Solid: every vehicle is a kinematic box on collision layer 1, moved at its
## tick, so an aircraft's strike sweep hits a bus and the sim-truth occlusion
## sees it. None of it is a prop.
extends Node3D

const LM = preload("res://scripts/life_meshes.gd")

const LAYER_STRUCTURE := 1
const TICK_SPLIT := 6
const CELL := 200.0
const MAX_FOCI := 4
const LOD0_M := 100.0
const LOD1_M := 560.0
const DRAW_M := 1700.0
## m/s by class, before each driver's own factor. Midday Manhattan, not the
## posted limits: the avenues average about walking pace times four.
const SPEED := {"motorway": 17.0, "motorway_link": 11.0, "primary": 10.0, "secondary": 8.5,
	"street": 7.0, "service": 4.0}
## Vehicles per lane-km by class at midday, for filling a bubble.
const DENSITY := {"motorway": 22.0, "motorway_link": 12.0, "primary": 34.0, "secondary": 26.0,
	"street": 16.0, "service": 4.0}
## [kind, share]: the street mix.
const MIX := [["taxi", 0.21], ["taxi_van", 0.06], ["sedan", 0.1], ["suv", 0.13], ["hatch", 0.05],
	["minivan", 0.04], ["wagon", 0.03], ["compact", 0.04], ["police", 0.025], ["van", 0.11],
	["truck", 0.07], ["bus", 0.045]]
const SIGNAL_CYCLE := 90.0
const SIGNAL_NS_GREEN := 46.0       # avenue phase, then the street phase from SIGNAL_EW_START
const SIGNAL_EW_START := 50.0
const SIGNAL_EW_END := 86.0
const PROGRESSION_MPS := 9.0
const STUCK_S := 45.0

var env: Node3D
var roads                                   # life_roads.gd
var cap := 1400
var on := false
var _rng := RandomNumberGenerator.new()
var _clock := 0.0
var _frame := 0

# Kinds.
var _kinds: Array[String] = []
var _k_dims: Array[Vector3] = []
var _k_heavy := PackedByteArray()           # van/truck/bus: no parkways
var _k_bus := PackedByteArray()
var _k_shape: Array[RID] = []
var _mm: Array[MultiMesh] = []              # kind * 3 + lod
var _mmi: Array[MultiMeshInstance3D] = []
var _mm_owner := PackedInt32Array()          # (kind * 3 + lod) * cap + slot -> vehicle
var _mm_n := PackedInt32Array()
var _mix_cum := PackedFloat32Array()

# Spawn grid.
var _cell_edges := {}                       # Vector2i -> PackedInt32Array
var _cell_cum := {}                         # Vector2i -> PackedFloat32Array (cumulative weights)
var _cell_w := {}                           # Vector2i -> float

# Vehicles.
var _v_kind := PackedInt32Array()
var _v_edge := PackedInt32Array()
var _v_next := PackedInt32Array()
var _v_lane := PackedInt32Array()
var _v_s := PackedFloat32Array()
var _v_v := PackedFloat32Array()
var _v_v0 := PackedFloat32Array()
var _v_wait := PackedFloat32Array()
var _v_yaw := PackedFloat32Array()
var _v_blend := PackedVector2Array()
var _v_still := PackedFloat32Array()
var _v_tick := PackedFloat32Array()         # _clock at the last tick
var _v_focus := PackedInt32Array()          # -1 dormant
var _v_gen := PackedInt32Array()
var _v_lod := PackedInt32Array()            # -1: not drawn
var _v_slot := PackedInt32Array()
var _v_paint := PackedFloat32Array()
var _v_live := PackedByteArray()
var _v_rid: Array[RID] = []
var _queues := {}                           # edge * 8 + lane -> Array of vehicles, farthest along first
var _node_busy := PackedFloat32Array()      # _clock until which an unsignalled junction is taken
var _node_from := PackedInt32Array()

# Foci: [{"cam": Camera3D, "c": Vector2, "r": float, "pos": Vector3, "gen": int, "target": int}]
var _foci: Array = []
var _foci_due := 0.0


func setup(env_node: Node3D, road_data) -> void:
	env = env_node
	roads = road_data
	name = "traffic"
	_rng.seed = 7919


func _exit_tree() -> void:
	for r in _v_rid:
		PhysicsServer3D.free_rid(r)
	for r in _k_shape:
		PhysicsServer3D.free_rid(r)
	_v_rid.clear()
	_k_shape.clear()


func set_on(want: bool, count: int = -1) -> void:
	if count > 1 and _kinds.is_empty():
		cap = count                         # the pool is sized when first built
	if want and _kinds.is_empty():
		_build()
	on = want and not _kinds.is_empty()
	for j in _mmi.size():
		_mmi[j].visible = on and _mm_n[j] > 0
	var space := get_world_3d().space
	for i in _v_rid.size():
		PhysicsServer3D.body_set_space(_v_rid[i], space if (on and _v_live[i] == 1) else RID())
	if on:
		_foci_due = 0.0


func count() -> int:
	var n := 0
	for i in _v_live.size():
		n += _v_live[i]
	return n


func sample() -> Array:
	for i in _v_live.size():
		if _v_live[i] == 1:
			var p := _pos(i)
			return [p.x, p.y, 0.0]
	return []


func mean_speed() -> Dictionary:
	var sum := 0.0
	var still := 0
	var n := 0
	for i in _v_live.size():
		if _v_live[i] == 1:
			n += 1
			sum += _v_v[i]
			if _v_v[i] < 0.3:
				still += 1
	if n == 0:
		return {}
	return {"mean_mps": sum / n, "standing_frac": float(still) / n}


# ------------------------------------------------------------------ build

func _build() -> void:
	if roads == null or not roads.loaded:
		return
	var t0 := Time.get_ticks_msec()
	var acc := 0.0
	for e: Array in MIX:
		var d: Dictionary = LM.kind(str(e[0]))
		if d.is_empty():
			continue
		_kinds.append(str(e[0]))
		_k_dims.append(d["dims"])
		_k_heavy.append(1 if str(e[0]) in ["van", "truck", "bus"] else 0)
		_k_bus.append(1 if str(e[0]) == "bus" else 0)
		acc += float(e[1])
		_mix_cum.append(acc)
		var dims: Vector3 = d["dims"]
		var shape := PhysicsServer3D.box_shape_create()
		PhysicsServer3D.shape_set_data(shape, Vector3(dims.z, dims.y, dims.x) * 0.5)
		_k_shape.append(shape)
		for lod in 3:
			var mm := MultiMesh.new()
			mm.transform_format = MultiMesh.TRANSFORM_3D
			mm.use_custom_data = true
			mm.mesh = d["lod%d" % lod]
			mm.instance_count = cap
			mm.visible_instance_count = 0
			# The pool is around the cameras; a fixed box saves recomputing
			# the bounds from hundreds of moving transforms every frame.
			mm.custom_aabb = AABB(Vector3(-30000, -50, -30000), Vector3(60000, 200, 60000))
			_mm.append(mm)
			_mm_n.append(0)
			var mmi := MultiMeshInstance3D.new()
			mmi.name = "traffic_%s_%d" % [str(e[0]), lod]
			mmi.multimesh = mm
			mmi.material_override = LM.material()
			mmi.physics_interpolation_mode = Node.PHYSICS_INTERPOLATION_MODE_OFF
			# Real shadows only for the near ones; the rest carry a blob.
			mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON if lod == 0 \
				else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
			mmi.visible = false
			add_child(mmi)
			_mmi.append(mmi)
	for k in _mix_cum.size():
		_mix_cum[k] /= acc
	_mm_owner.resize(_mm.size() * cap)
	_build_grid()
	var nn: int = roads.nodes.size()
	_node_busy.resize(nn)
	_node_from.resize(nn)
	_node_from.fill(-1)
	for i in cap:
		_add_vehicle()
	print("[world_life] traffic: %d kinds, %d spawn cells, pool %d (%d ms)" % [_kinds.size(),
		_cell_edges.size(), cap, Time.get_ticks_msec() - t0])


func _build_grid() -> void:
	var tmp := {}
	for d in roads.edge_count():
		if not roads.drivable(d) or roads.d_len[d] < 6.0:
			continue
		var w: float = roads.d_len[d] * 0.001 * float(roads.d_lanes[d]) * float(DENSITY.get(roads.d_class[d], 5.0))
		var mid: Vector2 = roads.nodes[roads.d_from[d]] + roads.d_dir[d] * roads.d_len[d] * 0.5
		var key := Vector2i(int(floor(mid.x / CELL)), int(floor(mid.y / CELL)))
		if not tmp.has(key):
			tmp[key] = [PackedInt32Array(), PackedFloat32Array(), 0.0]
		var e: Array = tmp[key]
		var edges: PackedInt32Array = e[0]
		var cum: PackedFloat32Array = e[1]
		edges.append(d)
		e[2] = float(e[2]) + w
		cum.append(float(e[2]))
		e[0] = edges
		e[1] = cum
	for key in tmp:
		_cell_edges[key] = tmp[key][0]
		_cell_cum[key] = tmp[key][1]
		_cell_w[key] = tmp[key][2]


func _add_vehicle() -> void:
	var r := _rng.randf()
	var k := _mix_cum.bsearch(r)
	k = mini(k, _kinds.size() - 1)
	_v_kind.append(k)
	_v_edge.append(0)
	_v_next.append(-1)
	_v_lane.append(0)
	_v_s.append(0.0)
	_v_v.append(0.0)
	_v_v0.append(10.0)
	_v_wait.append(0.0)
	_v_yaw.append(0.0)
	_v_blend.append(Vector2.ZERO)
	_v_still.append(0.0)
	_v_tick.append(0.0)
	_v_focus.append(-1)
	_v_gen.append(-1)
	_v_lod.append(-1)
	_v_slot.append(-1)
	_v_live.append(0)
	var name_k := _kinds[k]
	var paint: Color
	match name_k:
		"taxi", "taxi_van":
			paint = LM.TAXI_YELLOW
		"police":
			paint = LM.POLICE_WHITE
		"bus", "truck":
			paint = Color(0.92, 0.93, 0.92) if name_k == "bus" else LM.PAINTS[_rng.randi_range(0, 7)]
		"van":
			paint = [Color(0.93, 0.93, 0.92), Color(0.93, 0.93, 0.92), Color(0.9, 0.9, 0.9),
				Color(0.3, 0.2, 0.12), Color(0.12, 0.14, 0.2)][_rng.randi_range(0, 4)]
		_:
			# For-hire cars are black; about a third of the sedans and SUVs.
			paint = LM.BLACK_CAR if (name_k in ["sedan", "suv"] and _rng.randf() < 0.45) \
				else LM.PAINTS[_rng.randi_range(0, LM.PAINTS.size() - 1)]
	_v_paint.append(LM.pack_rgb(paint))
	var rid := PhysicsServer3D.body_create()
	PhysicsServer3D.body_set_mode(rid, PhysicsServer3D.BODY_MODE_KINEMATIC)
	PhysicsServer3D.body_add_shape(rid, _k_shape[k], Transform3D(Basis(), Vector3(0.0, _k_dims[k].y * 0.5, 0.0)))
	PhysicsServer3D.body_set_collision_layer(rid, LAYER_STRUCTURE)
	PhysicsServer3D.body_set_collision_mask(rid, 0)
	_v_rid.append(rid)


# ------------------------------------------------------------------ foci

func _update_foci(cams: Array) -> void:
	var old := _foci
	_foci = []
	for i in cams.size():
		var cam: Camera3D = cams[i]
		var pos := cam.global_position
		var fwd := -cam.global_basis.z
		var alt := maxf(pos.y, 5.0)
		var nadir := Vector2(pos.x, -pos.z)
		var reach := clampf(alt * 4.0, 400.0, 1400.0)
		var g := nadir + Vector2(fwd.x, -fwd.z).normalized() * reach
		if fwd.y < -0.02:
			var t := alt / -fwd.y
			var hit := Vector2(pos.x + fwd.x * t, -(pos.z + fwd.z * t))
			g = nadir + (hit - nadir).limit_length(reach)
		var c := nadir.lerp(g, 0.6)
		var r := clampf(350.0 + alt * 1.1 + nadir.distance_to(g) * 0.45, 450.0, 1400.0)
		var gen := 0
		if i < old.size() and int(old[i]["cam_id"]) == cam.get_instance_id():
			gen = int(old[i]["gen"])
			# A jump (a vantage moved, a new view): refill rather than trickle.
			if (old[i]["c"] as Vector2).distance_to(c) > float(old[i]["r"]) * 0.6:
				gen += 1
		else:
			gen = _rng.randi()
		_foci.append({"cam": cam, "cam_id": cam.get_instance_id(), "c": c, "r": r, "pos": pos, "gen": gen, "target": 0})
	# Shares of the pool: what each bubble's roads hold, capped at a split
	# that favours the aircraft and vantage cameras over the window's (which
	# mostly chases an aircraft, inside that aircraft's own bubble).
	if _foci.is_empty():
		return
	var wsum := 0.0
	for f: Dictionary in _foci:
		f["share"] = 1.0 if (f["cam"] as Camera3D).get_viewport() is SubViewport else 0.35
		wsum += float(f["share"])
	for f: Dictionary in _foci:
		var w := _bubble_weight(f["c"], f["r"])
		f["target"] = mini(int(float(cap) * float(f["share"]) / wsum), int(w))
	for i in _v_focus.size():
		var want := -1
		var acc := 0
		for fi in _foci.size():
			acc += int(_foci[fi]["target"])
			if i < acc:
				want = fi
				break
		if want != _v_focus[i]:
			_v_focus[i] = want
			_v_gen[i] = -1          # respawn in its new bubble


func _bubble_weight(c: Vector2, r: float) -> float:
	var w := 0.0
	var k0 := Vector2i(int(floor((c.x - r) / CELL)), int(floor((c.y - r) / CELL)))
	var k1 := Vector2i(int(floor((c.x + r) / CELL)), int(floor((c.y + r) / CELL)))
	for ky in range(k0.y, k1.y + 1):
		for kx in range(k0.x, k1.x + 1):
			var key := Vector2i(kx, ky)
			if _cell_w.has(key):
				var cc := Vector2((kx + 0.5) * CELL, (ky + 0.5) * CELL)
				if cc.distance_to(c) <= r:
					w += float(_cell_w[key])
	return w


## A drivable edge in the bubble (or its ring r0..r), by weight; -1 if none.
func _sample_edge(c: Vector2, r0: float, r: float, heavy: bool, bus: bool) -> int:
	var keys: Array[Vector2i] = []
	var cum := PackedFloat32Array()
	var acc := 0.0
	var k0 := Vector2i(int(floor((c.x - r) / CELL)), int(floor((c.y - r) / CELL)))
	var k1 := Vector2i(int(floor((c.x + r) / CELL)), int(floor((c.y + r) / CELL)))
	for ky in range(k0.y, k1.y + 1):
		for kx in range(k0.x, k1.x + 1):
			var key := Vector2i(kx, ky)
			if not _cell_w.has(key):
				continue
			var dd := Vector2((kx + 0.5) * CELL, (ky + 0.5) * CELL).distance_to(c)
			if dd > r or dd < r0:
				continue
			acc += float(_cell_w[key])
			keys.append(key)
			cum.append(acc)
	if keys.is_empty():
		return -1
	for attempt in 8:
		var key := keys[mini(cum.bsearch(_rng.randf() * acc), keys.size() - 1)]
		var ccum: PackedFloat32Array = _cell_cum[key]
		var edges: PackedInt32Array = _cell_edges[key]
		var d := edges[mini(ccum.bsearch(_rng.randf() * ccum[ccum.size() - 1]), edges.size() - 1)]
		var cls: String = roads.d_class[d]
		if heavy and cls.begins_with("motorway"):
			continue
		if bus and not (cls == "primary" or cls == "secondary"):
			continue
		return d
	return -1


# ------------------------------------------------------------------ frame

## `cams`: the current cameras, aircraft and vantage ones first (world_life).
func step(dt: float, cams: Array) -> void:
	if not on:
		return
	_clock += dt
	_frame += 1
	_foci_due -= dt
	if _foci_due <= 0.0:
		_foci_due = 0.5
		_update_foci(cams.slice(0, MAX_FOCI))
	var phase := _frame % TICK_SPLIT
	for i in range(phase, _v_kind.size(), TICK_SPLIT):
		_tick(i)
	# An empty MultiMesh is hidden: it still costs a draw per pass otherwise
	# (36 of them, times the shadow cascades, times every camera).
	for j in _mm.size():
		_mm[j].visible_instance_count = _mm_n[j]
		_mmi[j].visible = _mm_n[j] > 0


func _tick(i: int) -> void:
	var fi := _v_focus[i]
	if fi < 0 or fi >= _foci.size():
		if _v_live[i] == 1:
			_retire(i)
		return
	var f: Dictionary = _foci[fi]
	if _v_gen[i] != int(f["gen"]):
		_respawn(i, f, 0.0)
		return
	var dt := minf(_clock - _v_tick[i], 0.5)
	_v_tick[i] = _clock
	if dt <= 0.0:
		return
	_drive(i, dt)
	if _v_live[i] == 0:
		return
	var p := _pos(i)
	var c: Vector2 = f["c"]
	var r: float = f["r"]
	if p.distance_squared_to(c) > r * r * 1.21 or _v_still[i] > STUCK_S:
		_respawn(i, f, r * 0.72)
		return
	_draw(i, p, f, dt)


func _pos(i: int) -> Vector2:
	var d := _v_edge[i]
	return roads.lane_pos(d, _v_s[i], roads.d_off0[d] + float(_v_lane[i]) * roads.LANE_W) + _v_blend[i]


func _respawn(i: int, f: Dictionary, r0: float) -> void:
	var k := _v_kind[i]
	var d := _sample_edge(f["c"], r0, f["r"], _k_heavy[k] == 1, _k_bus[k] == 1)
	if d < 0 and r0 > 0.0:
		d = _sample_edge(f["c"], 0.0, f["r"], _k_heavy[k] == 1, _k_bus[k] == 1)
	if d < 0:
		_retire(i)
		_v_gen[i] = int(f["gen"])
		return
	var lane: int = roads.free_lane(d, _rng.randi_range(0, roads.d_lanes[d] - 1))
	if lane < 0:
		_retire(i)
		_v_gen[i] = int(f["gen"])
		return
	var s := _rng.randf_range(0.0, roads.d_len[d])
	# Not on top of another vehicle in that lane.
	var q: Array = _queues.get(d * 8 + lane, [])
	for j: int in q:
		if absf(_v_s[j] - s) < 9.0:
			_retire(i)
			_v_gen[i] = int(f["gen"])
			return
	if _v_live[i] == 1:
		_leave_queue(i)
	_v_edge[i] = d
	_v_lane[i] = lane
	_v_s[i] = s
	_v_next[i] = _pick_next(d, k)
	var v0: float = float(SPEED.get(roads.d_class[d], 6.0)) * _rng.randf_range(0.8, 1.15)
	if _k_heavy[k] == 1:
		v0 *= 0.85
	_v_v0[i] = v0
	_v_v[i] = v0 * _rng.randf_range(0.3, 0.9)
	_v_wait[i] = 0.0
	_v_still[i] = 0.0
	_v_blend[i] = Vector2.ZERO
	var dir: Vector2 = roads.d_dir[d]
	_v_yaw[i] = atan2(dir.x, -dir.y)
	_v_tick[i] = _clock
	_v_gen[i] = int(f["gen"])
	_join_queue(i, d, lane, true)
	if _v_live[i] == 0:
		_v_live[i] = 1
		PhysicsServer3D.body_set_space(_v_rid[i], get_world_3d().space)
	_draw(i, _pos(i), f, 0.0)


func _retire(i: int) -> void:
	if _v_live[i] == 1:
		_leave_queue(i)
		PhysicsServer3D.body_set_space(_v_rid[i], RID())
	_v_live[i] = 0
	_set_lod(i, -1)


func _join_queue(i: int, d: int, lane: int, sorted: bool) -> void:
	var key := d * 8 + lane
	if not _queues.has(key):
		_queues[key] = []
	var q: Array = _queues[key]
	if not sorted:
		q.append(i)
		return
	var at := q.size()
	for k in q.size():
		if _v_s[q[k]] < _v_s[i]:
			at = k
			break
	q.insert(at, i)


func _leave_queue(i: int) -> void:
	var key := _v_edge[i] * 8 + _v_lane[i]
	var q = _queues.get(key)
	if q != null:
		(q as Array).erase(i)
		if (q as Array).is_empty():
			_queues.erase(key)


## Next directed edge after `d`: not straight back unless it is a dead end,
## weighted toward going straight on; heavy vehicles keep off the parkways,
## buses to the avenues and wide streets where they can.
func _pick_next(d: int, k: int) -> int:
	var node: int = roads.d_to[d]
	var best := -1
	var best_w := -1.0
	var back := -1
	var heavy := _k_heavy[k] == 1
	var bus := _k_bus[k] == 1
	for c: int in roads.out[node]:
		if roads.d_to[c] == roads.d_from[d]:
			back = c
			continue
		var cls: String = roads.d_class[c]
		if heavy and cls.begins_with("motorway"):
			continue
		if roads.free_lane(c, 0) < 0:
			continue
		var straight: float = roads.d_dir[c].dot(roads.d_dir[d])
		var w := _rng.randf() * (1.6 + straight)
		if bus and (cls == "primary" or cls == "secondary"):
			w += 2.0
		if w > best_w:
			best_w = w
			best = c
	return best if best != -1 else back


func _green(node: int, d: int) -> bool:
	var dir: Vector2 = roads.d_dir[d]
	var t := fposmod(_clock - roads.nodes[node].y / PROGRESSION_MPS, SIGNAL_CYCLE)
	if absf(dir.y) >= absf(dir.x):
		return t < SIGNAL_NS_GREEN
	return t >= SIGNAL_EW_START and t < SIGNAL_EW_END


func _drive(i: int, dt: float) -> void:
	var d := _v_edge[i]
	var s := _v_s[i]
	var lane := _v_lane[i]
	var k := _v_kind[i]
	var my_l: float = _k_dims[k].x
	var len_d: float = roads.d_len[d]
	var gap := 1.0e9
	var lead_v := 0.0
	var q: Array = _queues.get(d * 8 + lane, [])
	var at := q.find(i)
	if at > 0:
		var j: int = q[at - 1]
		gap = _v_s[j] - s - 0.5 * (_k_dims[_v_kind[j]].x + my_l)
		lead_v = _v_v[j]
	var nx := _v_next[i]
	if at <= 0 and nx >= 0:
		var q2: Array = _queues.get(nx * 8 + mini(lane, roads.d_lanes[nx] - 1), [])
		if not q2.is_empty():
			var j: int = q2[q2.size() - 1]
			gap = len_d - s + _v_s[j] - 0.5 * (_k_dims[_v_kind[j]].x + my_l)
			lead_v = _v_v[j]
	# The junction ahead.
	var node: int = roads.d_to[d]
	var v := _v_v[i]
	if roads.node_deg[node] >= 3:
		var line: float = maxf(len_d - roads.node_clear[node], 0.0)
		var to_line := line - s
		if to_line > -0.5 and to_line < 60.0:
			var stop := false
			var signalled: bool = roads.node_motorway[node] == 0 and roads.node_rank[node] >= 2
			if signalled:
				# Red: stop, unless already too close to stop (an amber run).
				stop = not _green(node, d) and to_line > v * v / 12.0
			else:
				var minor: bool = roads.d_rank[d] < roads.node_rank[node]
				var busy := _node_busy[node] > _clock and _node_from[node] != d
				if busy or (minor and _v_wait[i] < 1.0):
					stop = true
				if minor and to_line < 2.0 and v < 0.6:
					_v_wait[i] += dt
			if stop and to_line < gap:
				gap = maxf(to_line, 0.0)
				lead_v = 0.0
	# Never faster than can stop in the gap (3 m/s^2, 2 m margin).
	var want := minf(_v_v0[i], sqrt(maxf(0.0, 2.0 * 3.0 * (gap - 2.0))) + lead_v * 0.5)
	if gap < 2.5:
		want = 0.0
	var a := clampf((want - v) / maxf(dt, 0.05), -7.0, 2.2)
	v = maxf(0.0, v + a * dt)
	_v_v[i] = v
	_v_still[i] = (_v_still[i] + dt) if v < 0.3 else 0.0
	s += v * dt
	if s < len_d:
		_v_s[i] = s
		_v_blend[i] *= maxf(0.0, 1.0 - dt * 2.5)
		return
	# Into the next edge.
	if nx < 0:
		_retire(i)
		return
	var old := _pos(i)
	_leave_queue(i)
	s -= len_d
	_v_edge[i] = nx
	_v_lane[i] = roads.free_lane(nx, mini(lane, roads.d_lanes[nx] - 1))
	if _v_lane[i] < 0:
		_v_lane[i] = 0
		_retire(i)
		return
	_v_s[i] = minf(s, roads.d_len[nx])
	_v_next[i] = _pick_next(nx, k)
	_v_wait[i] = 0.0
	if roads.node_deg[node] >= 3:
		_node_busy[node] = _clock + 1.4
		_node_from[node] = d
	var v0: float = float(SPEED.get(roads.d_class[nx], 6.0)) * _rng.randf_range(0.85, 1.1)
	_v_v0[i] = v0 * (0.85 if _k_heavy[k] == 1 else 1.0)
	_join_queue(i, nx, _v_lane[i], false)
	_v_blend[i] = (old - _pos(i) + _v_blend[i]).limit_length(8.0)


func _draw(i: int, p: Vector2, f: Dictionary, dt: float) -> void:
	var d := _v_edge[i]
	var dir: Vector2 = roads.d_dir[d]
	var want := atan2(dir.x, -dir.y)
	var old := _v_yaw[i]
	var yaw := old + wrapf(want - old, -PI, PI) * (minf(1.0, dt * 3.5) if dt > 0.0 else 1.0)
	_v_yaw[i] = yaw
	var yr := clampf(wrapf(yaw - old, -PI, PI) / maxf(dt, 0.05), -1.5, 1.5) if dt > 0.0 else 0.0
	var xf := Transform3D(Basis(Vector3.UP, yaw), Vector3(p.x, 0.0, -p.y))
	var cam_pos: Vector3 = f["pos"]
	var dist := cam_pos.distance_to(xf.origin)
	var lod := 0 if dist < LOD0_M else (1 if dist < LOD1_M else (2 if dist < DRAW_M else -1))
	# A little hysteresis so a car on a band's edge does not flick.
	var cur := _v_lod[i]
	if cur >= 0 and lod >= 0 and lod != cur:
		var edge := LOD0_M if mini(lod, cur) == 0 else LOD1_M
		if absf(dist - edge) < edge * 0.06:
			lod = cur
	_set_lod(i, lod)
	PhysicsServer3D.body_set_state(_v_rid[i], PhysicsServer3D.BODY_STATE_TRANSFORM, xf)
	if lod < 0:
		return
	var mm := _mm[_v_kind[i] * 3 + lod]
	var slot := _v_slot[i]
	mm.set_instance_transform(slot, xf)
	var braking := 1.0 if (_v_v[i] < _v_v0[i] * 0.5 and _v_v[i] > 0.2) or _v_v[i] < 0.2 else 0.0
	mm.set_instance_custom_data(slot, Color(_v_paint[i], _v_v[i], fposmod(_clock, 1000.0),
		20.0 + yr + 10.0 * braking))


## Move vehicle i to `lod`'s MultiMesh (-1: none), keeping each MultiMesh's
## used slots packed at the front.
func _set_lod(i: int, lod: int) -> void:
	var cur := _v_lod[i]
	if cur == lod:
		return
	var k := _v_kind[i]
	if cur >= 0:
		var j := k * 3 + cur
		var slot := _v_slot[i]
		var last := _mm_n[j] - 1
		if slot != last:
			var other := _mm_owner[j * cap + last]
			_mm[j].set_instance_transform(slot, _mm[j].get_instance_transform(last))
			_mm[j].set_instance_custom_data(slot, _mm[j].get_instance_custom_data(last))
			_mm_owner[j * cap + slot] = other
			_v_slot[other] = slot
		_mm_n[j] = last
	_v_lod[i] = lod
	_v_slot[i] = -1
	if lod >= 0:
		var j := k * 3 + lod
		var slot := _mm_n[j]
		if slot >= _mm[j].instance_count:
			_v_lod[i] = -1
			return
		_mm_owner[j * cap + slot] = i
		_mm_n[j] = slot + 1
		_v_slot[i] = slot

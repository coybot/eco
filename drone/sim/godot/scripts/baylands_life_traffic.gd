## Road traffic around Baylands Park (a part of baylands_life.gd): the
## Istanbul / Manhattan traffic (life_traffic.gd: car following, junctions,
## the camera bubbles, shader-carried motion between ticks; heights and
## pitch from istanbul_life_traffic.gd) with a South Bay mix, posted speeds
## and the clock's traffic.
##
## What differs, and only that:
##   * THE MIX: Silicon Valley's: SUVs and sedans (a lot of them white,
##     black, grey and silver), pickups, minivans, delivery vans, box trucks,
##     and VTA buses on the arterials. No taxis, no police.
##   * SPEEDS: each driver takes the road's posted speed (OSM maxspeed),
##     times their own 0.9-1.1, freeway drivers a little over it; trucks 0.9.
##     Hwy 237 is posted 65 mph (29 m/s), Lawrence Expressway 45-50,
##     Caribbean Drive 45. At the weekday rush (7-9:30, 16-19) Hwy 237 crawls
##     at about 40 % of that.
##   * DENSITY: vehicles per lane-km by class, times the hour (weekday rush
##     peaks; weekends flatter and lighter).
##   * Trucks use the freeway (Manhattan keeps heavy vehicles off its
##     parkways; California does not). Only buses keep to the arterials.
extends "res://scripts/istanbul_life_traffic.gd"

const BL_MIX := [["sedan", 0.25], ["suv", 0.24], ["pickup", 0.1], ["hatch", 0.06], ["compact", 0.07],
	["minivan", 0.06], ["wagon", 0.02], ["van", 0.08], ["truck", 0.1], ["bus", 0.02]]
## Vehicles per lane-km at the day's typical (midday weekday) flow.
const BL_DENSITY := {"motorway": 16.0, "motorway_link": 8.0, "primary": 10.0, "secondary": 8.0,
	"street": 3.0, "service": 0.8}
## Californian paint shares: white, black, greys and silvers, then blue, red, others.
const BL_PAINTS := [Color(0.93, 0.93, 0.92), Color(0.93, 0.93, 0.92), Color(0.88, 0.88, 0.87),
	Color(0.05, 0.05, 0.055), Color(0.07, 0.07, 0.08), Color(0.42, 0.43, 0.45), Color(0.3, 0.31, 0.33),
	Color(0.66, 0.67, 0.68), Color(0.6, 0.61, 0.62), Color(0.13, 0.22, 0.45), Color(0.55, 0.08, 0.07),
	Color(0.35, 0.32, 0.28)]

var hour := 14.0
var weekend := false
var _factor := PackedFloat32Array()
## Aircraft positions (ENU, from baylands_life at 10 Hz). A vehicle's body
## is in the physics space only within SOLID_M of one: hundreds of moving
## kinematic bodies cost ~1 ms of physics a frame, and only the ones an
## aircraft could reach need to be solid.
var aircraft: Array = []
const SOLID_M := 250.0
var _in_space := PackedByteArray()


func set_clock(h: float, we: bool) -> void:
	var changed := absf(h - hour) > 0.25 or we != weekend
	hour = h
	weekend = we
	if changed and not _kinds.is_empty():
		_cell_edges.clear()
		_cell_cum.clear()
		_cell_w.clear()
		_build_grid()
		for f: Dictionary in _foci:
			f["gen"] = int(f["gen"]) + 1


## Traffic at hour h relative to the midday weekday flow.
func hour_factor() -> float:
	var h := hour
	if weekend:
		return lerpf(0.15, 0.85, smoothstep(6.0, 11.0, h)) * (1.0 - 0.75 * smoothstep(19.0, 23.5, h))
	if h < 5.0:
		return 0.08
	var keys := [[5.0, 0.1], [6.0, 0.55], [7.0, 1.3], [8.5, 1.45], [9.5, 1.0], [15.0, 1.0], [16.5, 1.5],
		[18.5, 1.4], [19.5, 0.7], [22.0, 0.3], [24.0, 0.1]]
	for i in keys.size() - 1:
		if h < float(keys[i + 1][0]):
			return lerpf(float(keys[i][1]), float(keys[i + 1][1]),
				(h - float(keys[i][0])) / (float(keys[i + 1][0]) - float(keys[i][0])))
	return 0.1


func _rush() -> bool:
	return not weekend and ((hour >= 7.0 and hour < 9.5) or (hour >= 16.0 and hour < 19.0))


static func kind_mesh(nm: String) -> Dictionary:
	if nm == "pickup":
		if not LM._cache.has("_bl_pickup"):
			LM._cache["_bl_pickup"] = LM._bake_model("pickup_hilux", "")
		return LM._cache["_bl_pickup"]
	return LM.kind(nm)


func _build() -> void:
	if roads == null or not roads.loaded:
		return
	var t0 := Time.get_ticks_msec()
	var acc := 0.0
	for e: Array in BL_MIX:
		var d: Dictionary = kind_mesh(str(e[0]))
		if d.is_empty():
			continue
		_kinds.append(str(e[0]))
		_k_dims.append(d["dims"])
		_k_heavy.append(1 if str(e[0]) == "bus" else 0)
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
			mm.custom_aabb = AABB(Vector3(-30000, -50, -30000), Vector3(60000, 400, 60000))
			_mm.append(mm)
			_mm_n.append(0)
			var mmi := MultiMeshInstance3D.new()
			mmi.name = "traffic_%s_%d" % [str(e[0]), lod]
			mmi.multimesh = mm
			mmi.material_override = LM.material()
			mmi.physics_interpolation_mode = Node.PHYSICS_INTERPOLATION_MODE_OFF
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
	print("[baylands_life] traffic: %d kinds, %d spawn cells, pool %d (%d ms)" % [_kinds.size(),
		_cell_edges.size(), cap, Time.get_ticks_msec() - t0])


func _build_grid() -> void:
	var tmp := {}
	var hf := hour_factor()
	for d in roads.edge_count():
		if not roads.drivable(d) or roads.d_len[d] < 6.0:
			continue
		var cls: String = roads.d_class[d]
		var dens: float = float(BL_DENSITY.get(cls, 2.0)) * hf
		if cls == "motorway" and _rush():
			dens *= 2.2                     # the queue
		var w: float = roads.d_len[d] * 0.001 * float(roads.d_lanes[d]) * dens
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
	super._add_vehicle()
	var i := _v_kind.size() - 1
	var nm: String = _kinds[_v_kind[i]]
	var paint: Color = BL_PAINTS[_rng.randi_range(0, BL_PAINTS.size() - 1)]
	match nm:
		"bus":
			paint = Color(0.92, 0.93, 0.93)
		"van", "truck":
			paint = Color(0.93, 0.93, 0.92) if _rng.randf() < 0.65 else paint
	_v_paint[i] = LM.pack_rgb(paint)
	_factor.append(_rng.randf_range(0.9, 1.1) * (0.9 if nm in ["truck", "bus"] else 1.0))
	_in_space.append(0)


func _near_aircraft(p: Vector2) -> bool:
	for a: Vector3 in aircraft:
		if Vector2(a.x, a.y).distance_squared_to(p) < SOLID_M * SOLID_M:
			return true
	return false


## Keeps vehicle i's body in the space only near an aircraft.
func _gate_body(i: int) -> void:
	var want := 1 if (on and _v_live[i] == 1 and _near_aircraft(_pos(i))) else 0
	if _in_space[i] != want:
		_in_space[i] = want
		PhysicsServer3D.body_set_space(_v_rid[i], get_world_3d().space if want == 1 else RID())


func _draw(i: int, p: Vector2, f: Dictionary, dt: float) -> void:
	super._draw(i, p, f, dt)
	_gate_body(i)


func _retire(i: int) -> void:
	super._retire(i)
	_in_space[i] = 0


func set_on(want: bool, count: int = -1) -> void:
	super.set_on(want, count)
	# The base puts every live body in the space; gate them again.
	_in_space.resize(_v_rid.size())
	_in_space.fill(0)
	for i in _v_rid.size():
		PhysicsServer3D.body_set_space(_v_rid[i], RID())


func _v0_for(i: int, d: int) -> float:
	var v: float = roads.d_speed[d] * _factor[i]
	var cls: String = roads.d_class[d]
	if cls == "motorway":
		v *= 0.42 if _rush() else 1.05
	elif cls == "service":
		v = minf(v, 6.0)
	return v


func _respawn(i: int, f: Dictionary, r0: float) -> void:
	var was := _v_live[i]
	super._respawn(i, f, r0)
	if was == 0 and _v_live[i] == 1:
		_in_space[i] = 1                    # the base just put its body in the space
		_gate_body(i)
	if _v_live[i] == 1:
		var old := maxf(_v_v0[i], 0.1)
		_v_v0[i] = _v0_for(i, _v_edge[i])
		_v_v[i] = _v_v[i] / old * _v_v0[i]


func _drive(i: int, dt: float) -> void:
	var before := _v_edge[i]
	super._drive(i, dt)
	if _v_live[i] == 1 and _v_edge[i] != before:
		_v_v0[i] = _v0_for(i, _v_edge[i])


func sample_on(cls: String) -> Array:
	for i in _v_live.size():
		if _v_live[i] == 1 and roads.d_class[_v_edge[i]] == cls:
			var p := _pos(i)
			return [p.x, p.y, roads.y_at(_v_edge[i], _v_s[i], p)]
	return []


## Moving vehicles within `radius` of c: truth for training (ENU).
func actors_near(c: Vector2, radius: float, out: Array) -> void:
	if not on:
		return
	for i in _v_live.size():
		if _v_live[i] != 1:
			continue
		var p := _pos(i)
		if p.distance_to(c) > radius:
			continue
		var d := _v_edge[i]
		var dims: Vector3 = _k_dims[_v_kind[i]]
		var dir: Vector2 = roads.d_dir[d]
		out.append({"class": "vehicle", "kind": _kinds[_v_kind[i]], "id": "v%d" % i, "moving": true,
			"e": snappedf(p.x, 0.01), "n": snappedf(p.y, 0.01), "z": snappedf(roads.y_at(d, _v_s[i], p), 0.01),
			"heading": snappedf(atan2(dir.y, dir.x), 0.01), "speed": snappedf(_v_v[i], 0.1),
			"size": [snappedf(dims.x, 0.01), snappedf(dims.z, 0.01), snappedf(dims.y, 0.01)]})

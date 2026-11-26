## Istanbul's road traffic (a part of istanbul_life.gd): Manhattan's traffic
## (life_traffic.gd: car following, junctions, the camera bubbles, the
## shader-driven motion between ticks) with Istanbul's mix, on its hills.
##
## What differs from Manhattan, and only that, is overridden here:
##   * The mix: yellow taxis (a fifth of what moves in the centre), private
##     cars (mostly white), light vans, dolmus minibuses, box trucks and IETT
##     buses (buses on primary and secondary roads, as in Manhattan).
##   * Height and pitch: every vehicle stands on the drawn ground, or on a
##     bridge deck, nose up or down the slope (roads.y_at / grade_at). Between
##     ticks the shader carries it along its own pitched forward axis.
##   * Signals: only at junctions OSM signals (roads.node_motorway = "no
##     signal"), the two phases split by the junction's main road axis, not
##     by north-south / east-west (the old city has no grid).
extends "res://scripts/life_traffic.gd"

const IM = preload("res://scripts/istanbul_life_meshes.gd")

const IST_MIX := [["taxi", 0.2], ["sedan", 0.17], ["hatch", 0.12], ["compact", 0.07],
	["suv", 0.08], ["wagon", 0.04], ["minivan", 0.05], ["van", 0.12], ["dolmus", 0.05],
	["truck", 0.04], ["iett", 0.06]]
const HEAVY := ["van", "truck", "iett"]


func _build() -> void:
	if roads == null or not roads.loaded:
		return
	var t0 := Time.get_ticks_msec()
	var acc := 0.0
	for e: Array in IST_MIX:
		var d: Dictionary = IM.kind(str(e[0]))
		if d.is_empty():
			continue
		_kinds.append(str(e[0]))
		_k_dims.append(d["dims"])
		_k_heavy.append(1 if str(e[0]) in HEAVY else 0)
		_k_bus.append(1 if str(e[0]) == "iett" else 0)
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
	print("[istanbul_life] traffic: %d kinds, %d spawn cells, pool %d (%d ms)" % [_kinds.size(),
		_cell_edges.size(), cap, Time.get_ticks_msec() - t0])


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
	var paint: Color
	match _kinds[k]:
		"taxi", "dolmus":
			paint = IM.TAXI_YELLOW
		"iett":
			paint = IM.IETT_WHITE
		"van":
			paint = [Color(0.93, 0.93, 0.92), Color(0.93, 0.93, 0.92), Color(0.9, 0.9, 0.9),
				Color(0.55, 0.56, 0.58), Color(0.12, 0.14, 0.2)][_rng.randi_range(0, 4)]
		"truck":
			paint = IM.PAINTS[_rng.randi_range(0, IM.PAINTS.size() - 1)]
		_:
			paint = IM.PAINTS[_rng.randi_range(0, IM.PAINTS.size() - 1)]
	_v_paint.append(LM.pack_rgb(paint))
	var rid := PhysicsServer3D.body_create()
	PhysicsServer3D.body_set_mode(rid, PhysicsServer3D.BODY_MODE_KINEMATIC)
	PhysicsServer3D.body_add_shape(rid, _k_shape[k], Transform3D(Basis(), Vector3(0.0, _k_dims[k].y * 0.5, 0.0)))
	PhysicsServer3D.body_set_collision_layer(rid, LAYER_STRUCTURE)
	PhysicsServer3D.body_set_collision_mask(rid, 0)
	_v_rid.append(rid)


func sample() -> Array:
	for i in _v_live.size():
		if _v_live[i] == 1:
			var p := _pos(i)
			return [p.x, p.y, roads.y_at(_v_edge[i], _v_s[i], p)]
	return []


## The two phases split a signalled junction's approaches along its main road
## and across it; the progression runs along the north (as Manhattan's).
func _green(node: int, d: int) -> bool:
	var dir: Vector2 = roads.d_dir[d]
	var axis: Vector2 = roads.node_axis[node]
	var t := fposmod(_clock - roads.nodes[node].y / PROGRESSION_MPS, SIGNAL_CYCLE)
	if axis == Vector2.ZERO or absf(dir.dot(axis)) >= 0.7071:
		return t < SIGNAL_NS_GREEN
	return t >= SIGNAL_EW_START and t < SIGNAL_EW_END


func _draw(i: int, p: Vector2, f: Dictionary, dt: float) -> void:
	var d := _v_edge[i]
	var dir: Vector2 = roads.d_dir[d]
	var want := atan2(dir.x, -dir.y)
	var old := _v_yaw[i]
	var yaw := old + wrapf(want - old, -PI, PI) * (minf(1.0, dt * 3.5) if dt > 0.0 else 1.0)
	_v_yaw[i] = yaw
	var yr := clampf(wrapf(yaw - old, -PI, PI) / maxf(dt, 0.05), -1.5, 1.5) if dt > 0.0 else 0.0
	var s := _v_s[i]
	var y: float = roads.y_at(d, s, p)
	var g: float = clampf(roads.grade_at(d, s, p), -0.35, 0.35)
	var xf := Transform3D(Basis(Vector3.UP, yaw) * Basis(Vector3.RIGHT, -atan(g)), Vector3(p.x, y, -p.y))
	var cam_pos: Vector3 = f["pos"]
	var dist := cam_pos.distance_to(xf.origin)
	var lod := 0 if dist < LOD0_M else (1 if dist < LOD1_M else (2 if dist < DRAW_M else -1))
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

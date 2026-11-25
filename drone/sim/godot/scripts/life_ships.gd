## Ships on New York's rivers and Upper Bay (a part of world_life.gd).
##
## Routes come from assets/manhattan/ships.json (tools/bake_manhattan_ships.py):
## the city's own ferry lines, the Hudson's and East River's fairways found
## from the water mask, the Upper Bay legs to the Narrows, the Staten Island
## ferry's crossing and a harbour tour loop. What sails them is the harbour's
## everyday traffic:
##
##   NYC Ferry catamarans on the ferry lines, end to end and back with a stop
##   at each end; two Staten Island ferries (the orange double-enders) crossing
##   from Whitehall; tugs pushing sand and gravel barges and a few small
##   working boats up and down the rivers; container ships and tankers in the
##   Upper Bay, more of them at anchor off Stapleton; harbour tour boats.
##
## A fairway is one-way: a ship that reaches the north end of the Hudson's
## northbound lane turns into the southbound one, so the rivers keep their
## traffic without anyone appearing from nowhere.
##
## Ships float with the waterline on the river (env.SEA_Y) and face +Z like the
## models (ModelKit). Solid: each is a kinematic body on layer 1 (hull plus
## superstructure), moved every frame, as the reference harbour scene does.
## The procedural ships (ferries, tour boats, tankers, container ships, barges)
## are boxes with the right proportions and colours; the tugs, the small
## freighter and the pilot boats are the downloaded models. Windows are lit at
## night (env.night_amount()).
extends Node3D

const ModelKit = preload("res://scripts/model_kit.gd")
const LM = preload("res://scripts/life_meshes.gd")

const LAYER_STRUCTURE := 1
const FERRY_DWELL_S := 50.0
const SI_DWELL_S := 120.0

var env: Node3D
var on := false
var _rng := RandomNumberGenerator.new()
var _routes: Array = []                     # {"name", "kind", "pts": PackedVector2Array (sim), "loop", "next"}
var _sea_y := -1.6

# Ships.
var _s_kind := PackedStringArray()
var _s_node: Array[Node3D] = []
var _s_wake: Array[MeshInstance3D] = []
var _s_rid: Array[RID] = []
var _s_pos := PackedVector2Array()
var _s_yaw := PackedFloat32Array()          # Godot yaw: +Z turned to the heading
var _s_v := PackedFloat32Array()
var _s_v0 := PackedFloat32Array()
var _s_route := PackedInt32Array()          # -1: at anchor
var _s_wp := PackedInt32Array()
var _s_dirn := PackedInt32Array()           # +1 along the route, -1 back (ferries)
var _s_dwell := PackedFloat32Array()
var _s_len := PackedFloat32Array()
var _s_beam := PackedFloat32Array()
var _shapes: Array[RID] = []
var _window_mats: Array[StandardMaterial3D] = []
var _body_mat: StandardMaterial3D


func setup(env_node: Node3D) -> void:
	env = env_node
	name = "ships"
	_rng.seed = 4242
	var sy = env.get("SEA_Y")
	if sy != null:
		_sea_y = float(sy)


func _exit_tree() -> void:
	for r in _s_rid:
		PhysicsServer3D.free_rid(r)
	for r in _shapes:
		PhysicsServer3D.free_rid(r)
	_s_rid.clear()
	_shapes.clear()


func count() -> int:
	return _s_kind.size() if on else 0


func sample() -> Array:
	for i in _s_kind.size():
		if _s_route[i] >= 0:
			return [_s_pos[i].x, _s_pos[i].y, _sea_y]
	return []


## The first of each kind out on the water: {kind: [e, n, sea level, yaw]}.
func samples() -> Dictionary:
	var out := {}
	for i in _s_kind.size():
		if not out.has(_s_kind[i]):
			out[_s_kind[i]] = [_s_pos[i].x, _s_pos[i].y, _sea_y, _s_yaw[i]]
	return out


func set_on(want: bool) -> void:
	if want and _s_kind.is_empty():
		_build()
	on = want and not _s_kind.is_empty()
	var space := get_world_3d().space
	for i in _s_node.size():
		_s_node[i].visible = on
		_s_wake[i].visible = on and _s_route[i] >= 0
		PhysicsServer3D.body_set_space(_s_rid[i], space if on else RID())
	if on:
		_draw()


func set_night(night: float) -> void:
	for m in _window_mats:
		m.emission_energy_multiplier = night * 2.2


# ------------------------------------------------------------------ build

func _build() -> void:
	var f := FileAccess.open("res://assets/manhattan/ships.json", FileAccess.READ)
	if f == null:
		return
	var doc = JSON.parse_string(f.get_as_text())
	f.close()
	if not (doc is Dictionary):
		return
	var off: Vector2 = env.call("data_offset")
	var by_name := {}
	for r: Dictionary in doc.get("routes", []):
		var pts := PackedVector2Array()
		for p: Array in r["pts"]:
			pts.append(Vector2(float(p[0]), float(p[1])) - off)
		if pts.size() < 2:
			continue
		by_name[str(r["name"])] = _routes.size()
		_routes.append({"name": str(r["name"]), "kind": str(r["kind"]), "pts": pts,
			"loop": bool(r.get("loop", false)), "next": -1, "length": _plen(pts)})
	# Fairways chain into their return lane.
	for r: Dictionary in _routes:
		var nm: String = r["name"]
		var other := ""
		if nm.ends_with("_n"):
			other = nm.substr(0, nm.length() - 2) + "_s"
		elif nm.ends_with("_s"):
			other = nm.substr(0, nm.length() - 2) + "_n"
		if other != "" and by_name.has(other):
			r["next"] = by_name[other]
	_body_mat = StandardMaterial3D.new()
	_body_mat.vertex_color_use_as_albedo = true
	_body_mat.vertex_color_is_srgb = true      # the colours here are picked as sRGB
	_body_mat.roughness = 0.65
	for ri in _routes.size():
		var r: Dictionary = _routes[ri]
		var L: float = r["length"]
		match str(r["kind"]):
			"ferry":
				var n := 2 if L > 9000.0 else 1
				for k in n:
					_add("nyc_ferry", ri, float(k) / float(n) + _rng.randf() * 0.2)
			"si_ferry":
				_add("si_ferry", ri, 0.0)
				_add("si_ferry", ri, 0.995)
			"channel_n", "channel_s":
				var hudson := str(r["name"]).begins_with("hudson")
				_add("tug_barge", ri, 0.2 + _rng.randf() * 0.2)
				_add("pilot", ri, 0.6 + _rng.randf() * 0.2)
				if hudson:
					_add("tug", ri, 0.45)
					_add("cargo" if str(r["kind"]) == "channel_s" else "tug_barge", ri, 0.85)
			"harbour":
				_add("container" if str(r["name"]).ends_with("_s") else "tanker", ri, 0.3)
				_add("tug_barge", ri, 0.7)
			"sightseeing":
				_add("tour", ri, 0.1)
				_add("tour", ri, 0.6)
	var kinds := ["tanker", "container", "tanker", "cargo"]
	var ai := 0
	for a: Dictionary in doc.get("anchorages", []):
		var p := Vector2(float(a["e"]), float(a["n"])) - off
		# Compass heading -> the sim's yaw (grid turned 29 deg from north).
		var hdg := deg_to_rad(float(a["hdg"]) - 29.0)
		var fwd := Vector2(sin(hdg), cos(hdg))
		_add_ship(kinds[ai % kinds.size()], p, atan2(fwd.x, -fwd.y), -1)
		ai += 1
	for n in _s_node:
		n.visible = false
	print("[world_life] ships: %d on %d routes" % [_s_kind.size(), _routes.size()])


static func _plen(pts: PackedVector2Array) -> float:
	var s := 0.0
	for i in pts.size() - 1:
		s += pts[i].distance_to(pts[i + 1])
	return s


## A ship `frac` of the way along route `ri`.
func _add(kind: String, ri: int, frac: float) -> void:
	var r: Dictionary = _routes[ri]
	var pts: PackedVector2Array = r["pts"]
	var target := clampf(frac, 0.0, 0.995) * float(r["length"])
	var acc := 0.0
	var i := 0
	while i < pts.size() - 2 and acc + pts[i].distance_to(pts[i + 1]) < target:
		acc += pts[i].distance_to(pts[i + 1])
		i += 1
	var seg := pts[i + 1] - pts[i]
	var t := clampf((target - acc) / maxf(seg.length(), 1.0), 0.0, 1.0)
	var p := pts[i].lerp(pts[i + 1], t)
	var dirn := 1
	# The second ferry on a line starts back the other way.
	if (kind == "nyc_ferry" or kind == "si_ferry") and frac >= 0.5:
		dirn = -1
	var yaw := atan2(seg.x, -seg.y) if dirn > 0 else atan2(-seg.x, seg.y)
	_add_ship(kind, p, yaw, ri)
	var s := _s_kind.size() - 1
	_s_dirn[s] = dirn
	_s_wp[s] = (i + 1) if dirn > 0 else i


func _add_ship(kind: String, pos: Vector2, yaw: float, route: int) -> void:
	var node := Node3D.new()
	node.name = "ship_%s_%d" % [kind, _s_kind.size()]
	node.physics_interpolation_mode = Node.PHYSICS_INTERPOLATION_MODE_OFF
	var dims := _make(kind, node)   # length, beam, freeboard
	node.set_meta("length_m", dims.x)       # thermal_kit places the engine room by it
	add_child(node)
	_s_kind.append(kind)
	_s_pos.append(pos)
	_s_yaw.append(yaw)
	var v0: float = {"nyc_ferry": 10.0, "si_ferry": 7.5, "tour": 5.0, "tug": 4.0, "tug_barge": 3.2,
		"pilot": 6.5, "cargo": 5.0, "container": 6.0, "tanker": 5.0}.get(kind, 4.0)
	_s_v0.append(v0 * _rng.randf_range(0.9, 1.1) if route >= 0 else 0.0)
	_s_v.append(_s_v0[_s_v0.size() - 1])
	_s_route.append(route)
	_s_wp.append(1)
	_s_dirn.append(1)
	_s_dwell.append(0.0)
	_s_len.append(dims.x)
	_s_beam.append(dims.y)
	_s_node.append(node)
	var wake := MeshInstance3D.new()
	var qm := PlaneMesh.new()
	qm.size = Vector2(1.0, 1.0)
	wake.mesh = qm
	wake.material_override = _wake_material()
	wake.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	wake.physics_interpolation_mode = Node.PHYSICS_INTERPOLATION_MODE_OFF
	wake.name = "wake_%d" % (_s_kind.size() - 1)
	add_child(wake)
	_s_wake.append(wake)
	var rid := PhysicsServer3D.body_create()
	PhysicsServer3D.body_set_mode(rid, PhysicsServer3D.BODY_MODE_KINEMATIC)
	PhysicsServer3D.body_set_collision_layer(rid, LAYER_STRUCTURE)
	PhysicsServer3D.body_set_collision_mask(rid, 0)
	for part: Array in node.get_meta("solids", []):
		var sh := PhysicsServer3D.box_shape_create()
		PhysicsServer3D.shape_set_data(sh, (part[1] as Vector3) * 0.5)
		PhysicsServer3D.body_add_shape(rid, sh, Transform3D(Basis(), part[0]))
		_shapes.append(sh)
	_s_rid.append(rid)


## Builds `kind` under `node` (+Z forward, waterline at y = 0); returns
## (length, beam, freeboard) and leaves the solid parts in node meta "solids"
## as [centre, size] boxes.
func _make(kind: String, node: Node3D) -> Vector3:
	match kind:
		"tug", "pilot", "cargo":
			var model: String = {"tug": "ship_tug", "pilot": "boat_pilot", "cargo": "ship_cargo"}[kind]
			var m := ModelKit.instance(model)
			if m != null:
				node.add_child(m)
				var ab := ModelKit.aabb(m)
				node.set_meta("solids", [[Vector3(ab.get_center().x, ab.get_center().y, ab.get_center().z), ab.size]])
				return Vector3(ab.size.z, ab.size.x, ab.end.y)
			return _procedural("tour", node)
		"tug_barge":
			var d := _procedural("barge", node)
			var tug := ModelKit.instance("ship_tug")
			if tug != null:
				var ab := ModelKit.aabb(tug)
				var holder := Node3D.new()
				holder.name = "ship_tug_pushing"
				holder.position = Vector3(0, 0, -d.x * 0.5 - ab.size.z * 0.5 + 3.0)
				holder.add_child(tug)
				node.add_child(holder)
				var solids: Array = node.get_meta("solids", [])
				solids.append([holder.position + ab.get_center(), ab.size])
				node.set_meta("solids", solids)
				d.x += ab.size.z - 3.0
			return d
	return _procedural(kind, node)


func _procedural(kind: String, node: Node3D) -> Vector3:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var win := SurfaceTool.new()
	win.begin(Mesh.PRIMITIVE_TRIANGLES)
	var has_win := kind != "barge"
	var solids: Array = []
	var L := 26.0
	var B := 8.5
	var F := 2.0
	var white := Color(0.93, 0.93, 0.92)
	var glass := Color(0.05, 0.06, 0.08)
	match kind:
		"nyc_ferry":
			# A NYC Ferry catamaran: twin white hulls, a glazed cabin, the
			# wheelhouse up top, a blue band.
			L = 26.0
			B = 8.5
			F = 1.6
			for sx in [-1.0, 1.0]:
				_b(st, Vector3(sx * 3.1, -0.2, 0), Vector3(2.0, 2.4, L), white)
			_b(st, Vector3(0, 1.3, 0), Vector3(B, 0.6, L * 0.94), white)
			_b(st, Vector3(0, 1.75, 0), Vector3(B * 1.01, 0.32, L * 0.95), Color(0.1, 0.33, 0.62))
			_b(st, Vector3(0, 2.9, -1.0), Vector3(B * 0.9, 2.6, L * 0.68), white)
			_b(win, Vector3(0, 3.0, -1.0), Vector3(B * 0.92, 1.2, L * 0.64), glass)
			_b(st, Vector3(0, 4.9, 2.5), Vector3(B * 0.55, 1.5, 4.5), white)
			_b(win, Vector3(0, 5.0, 2.5), Vector3(B * 0.57, 0.8, 4.6), glass)
			solids = [[Vector3(0, 1.2, 0), Vector3(B, 3.2, L)], [Vector3(0, 4.2, 0), Vector3(B * 0.9, 3.6, L * 0.7)]]
			F = 5.7
		"si_ferry":
			# A Staten Island ferry: 94 m double-ender, orange, three decks,
			# a wheelhouse at each end.
			L = 94.0
			B = 21.0
			var orange := Color(0.9, 0.28, 0.03)
			_b(st, Vector3(0, 0.0, 0), Vector3(B, 6.0, L * 0.8), orange)
			for sz in [-1.0, 1.0]:
				_b(st, Vector3(0, 0.0, sz * L * 0.45), Vector3(B * 0.7, 6.0, L * 0.1), orange)
			_b(st, Vector3(0, 4.5, 0), Vector3(B * 0.96, 3.0, L * 0.8), orange)
			_b(win, Vector3(0, 4.6, 0), Vector3(B * 0.97, 1.4, L * 0.74), glass)
			_b(st, Vector3(0, 7.5, 0), Vector3(B * 0.9, 3.0, L * 0.7), orange)
			_b(win, Vector3(0, 7.6, 0), Vector3(B * 0.91, 1.4, L * 0.66), glass)
			_b(st, Vector3(0, 9.1, 0), Vector3(B * 0.92, 0.2, L * 0.72), Color(0.82, 0.8, 0.76))
			for sz in [-1.0, 1.0]:
				_b(st, Vector3(0, 10.3, sz * L * 0.28), Vector3(B * 0.45, 2.2, 5.0), orange)
				_b(win, Vector3(0, 10.5, sz * L * 0.28), Vector3(B * 0.46, 0.9, 5.1), glass)
			_b(st, Vector3(0, 11.0, 0), Vector3(2.0, 4.0, 2.0), Color(0.15, 0.15, 0.16))
			solids = [[Vector3(0, 0, 0), Vector3(B, 6, L)], [Vector3(0, 6.0, 0), Vector3(B * 0.95, 6.0, L * 0.8)],
				[Vector3(0, 10.5, 0), Vector3(B * 0.45, 3.0, L * 0.6)]]
			F = 12.0
		"tour":
			# A harbour tour boat: white, two glazed decks, an open top deck.
			L = 50.0
			B = 10.0
			_b(st, Vector3(0, 0.2, -2.0), Vector3(B, 3.0, L * 0.84), white)
			_wedge(st, Vector3(0, -1.3, 19.0), Vector3(0, 1.7, 19.0), B, L * 0.5 - 19.0, white)
			_b(st, Vector3(0, 1.0, -2.0), Vector3(B * 1.01, 0.3, L * 0.85), Color(0.12, 0.2, 0.45))
			_b(st, Vector3(0, 3.0, -3.0), Vector3(B * 0.9, 2.6, L * 0.7), white)
			_b(win, Vector3(0, 3.1, -3.0), Vector3(B * 0.92, 1.3, L * 0.66), glass)
			_b(st, Vector3(0, 5.4, -4.0), Vector3(B * 0.85, 2.4, L * 0.55), white)
			_b(win, Vector3(0, 5.5, -4.0), Vector3(B * 0.87, 1.2, L * 0.5), glass)
			_b(st, Vector3(0, 6.7, -4.0), Vector3(B * 0.86, 0.15, L * 0.56), Color(0.55, 0.55, 0.57))
			solids = [[Vector3(0, 0.2, 0), Vector3(B, 3.0, L)], [Vector3(0, 4.3, -3.5), Vector3(B * 0.9, 5.2, L * 0.7)]]
			F = 6.8
		"barge":
			# A deck barge with a sand or gravel heap, pushed by a tug astern.
			L = 60.0
			B = 16.0
			_b(st, Vector3(0, 0.3, 0), Vector3(B, 3.4, L), Color(0.2, 0.12, 0.09))
			_b(st, Vector3(0, 2.05, 0), Vector3(B * 1.0, 0.1, L), Color(0.3, 0.27, 0.24))
			var heap := Color(0.62, 0.56, 0.45) if _rng.randf() < 0.5 else Color(0.42, 0.42, 0.42)
			_b(st, Vector3(0, 3.0, 0), Vector3(B * 0.8, 1.8, L * 0.85), heap)
			_b(st, Vector3(0, 4.2, 0), Vector3(B * 0.55, 1.0, L * 0.6), heap.darkened(0.05))
			solids = [[Vector3(0, 1.2, 0), Vector3(B, 5.0, L)]]
			F = 4.7
		"tanker":
			# A product tanker: black hull, green deck with its pipe run,
			# white accommodation aft and the funnel.
			L = 183.0
			B = 32.0
			var hull := Color(0.1, 0.1, 0.11)
			_b(st, Vector3(0, 1.0, -8.0), Vector3(B, 12.0, L - 26.0), hull)
			_wedge(st, Vector3(0, -5.0, L * 0.5 - 21.0), Vector3(0, 7.0, L * 0.5 - 21.0), B, 21.0, hull)
			_b(st, Vector3(0, 7.05, -8.0), Vector3(B * 0.98, 0.1, L - 27.0), Color(0.3, 0.4, 0.3))
			_b(st, Vector3(0, 8.0, 0.0), Vector3(2.0, 1.6, L * 0.65), Color(0.55, 0.55, 0.5))
			_b(st, Vector3(0, 8.5, L * 0.5 - 30.0), Vector3(B * 0.9, 3.0, 10.0), hull)
			_b(st, Vector3(0, 14.0, -L * 0.5 + 16.0), Vector3(B * 0.8, 14.0, 16.0), white)
			_b(win, Vector3(0, 18.0, -L * 0.5 + 24.05), Vector3(B * 0.7, 1.0, 0.1), glass)
			_b(st, Vector3(0, 22.0, -L * 0.5 + 9.0), Vector3(5.0, 8.0, 5.0), Color(0.6, 0.1, 0.08))
			solids = [[Vector3(0, 1.0, 0), Vector3(B, 12.0, L)], [Vector3(0, 14.0, -L * 0.5 + 16.0), Vector3(B * 0.8, 14.0, 16.0)]]
			F = 26.0
		"container":
			L = 170.0
			B = 26.0
			var hull := Color(0.12, 0.2, 0.32)
			var draft := 1.5
			var fb := 9.0
			var body_l := L * 0.8
			_b(st, Vector3(0, (fb - draft) * 0.5, -L * 0.5 + body_l * 0.5), Vector3(B, fb + draft, body_l), hull)
			_wedge(st, Vector3(0, -draft, -L * 0.5 + body_l), Vector3(0, fb, -L * 0.5 + body_l), B, L - body_l, hull)
			var cols := [Color(0.7, 0.15, 0.1), Color(0.15, 0.3, 0.6), Color(0.85, 0.55, 0.15), Color(0.3, 0.5, 0.3),
				Color(0.85, 0.85, 0.85), Color(0.5, 0.2, 0.45), Color(0.2, 0.2, 0.22)]
			var z := -L * 0.5 + 34.0
			var bay := 0
			while z < -L * 0.5 + body_l + 4.0:
				var tiers := 2 + (bay * 7) % 3
				for row in 8:
					var x := (float(row) - 3.5) * (B / 8.4)
					for tier in tiers:
						_b(st, Vector3(x, fb + 1.3 + tier * 2.6, z), Vector3(B / 8.4 - 0.15, 2.5, 12.0),
							cols[(bay * 3 + row * 5 + tier) % cols.size()])
				z += 12.6
				bay += 1
			_b(st, Vector3(0, fb + 7.0, -L * 0.5 + 16.0), Vector3(B * 0.9, 14.0, 12.0), white)
			_b(win, Vector3(0, fb + 12.0, -L * 0.5 + 22.05), Vector3(B * 0.8, 1.0, 0.1), glass)
			_b(st, Vector3(0, fb + 18.0, -L * 0.5 + 8.0), Vector3(4.0, 8.0, 5.0), Color(0.2, 0.2, 0.22))
			solids = [[Vector3(0, fb * 0.5, 0), Vector3(B, fb + draft, L)], [Vector3(0, fb + 5.0, 0), Vector3(B, 10.0, L * 0.7)],
				[Vector3(0, fb + 7.0, -L * 0.5 + 16.0), Vector3(B * 0.9, 14.0, 12.0)]]
			F = fb + 22.0
	var mesh := st.commit()
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = _body_mat
	mi.visibility_range_end = 6000.0 if L > 80.0 else 3000.0
	node.add_child(mi)
	if has_win:
		var wm := StandardMaterial3D.new()
		wm.vertex_color_use_as_albedo = true
		wm.vertex_color_is_srgb = true
		wm.roughness = 0.1
		wm.metallic = 0.3
		wm.emission_enabled = true
		wm.emission = Color(1.0, 0.82, 0.55)
		wm.emission_energy_multiplier = 0.0
		_window_mats.append(wm)
		var wi := MeshInstance3D.new()
		wi.name = "windows"
		wi.mesh = win.commit()
		wi.material_override = wm
		wi.visibility_range_end = mi.visibility_range_end
		wi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		node.add_child(wi)
	node.set_meta("solids", solids)
	return Vector3(L, B, F)


func _b(st: SurfaceTool, c: Vector3, s: Vector3, col: Color) -> void:
	LM._box(st, c, s, col, 1.0, Vector3.ZERO)


## A bow: from the hull's fore face (width `beam`, at the given bottom and
## top centres) to a stem `length` ahead (+Z).
static func _wedge(st: SurfaceTool, bottom_c: Vector3, top_c: Vector3, beam: float, length: float, col: Color) -> void:
	var hb := beam * 0.5
	var bl := bottom_c + Vector3(-hb, 0, 0)
	var br := bottom_c + Vector3(hb, 0, 0)
	var tl := top_c + Vector3(-hb, 0, 0)
	var tr := top_c + Vector3(hb, 0, 0)
	var sb := bottom_c + Vector3(0, 0, length * 0.7)
	var stp := top_c + Vector3(0, 0, length)
	var tris := [[tl, stp, tr], [bl, br, sb], [tl, bl, sb], [tl, sb, stp], [tr, stp, sb], [tr, sb, br]]
	var centre := (bl + br + tl + tr + sb + stp) / 6.0
	for t in tris:
		var nrm: Vector3 = ((t[1] - t[0]).cross(t[2] - t[0])).normalized()
		if nrm.dot((t[0] + t[1] + t[2]) / 3.0 - centre) < 0.0:
			nrm = -nrm
		LM._tri(st, t[0], t[1], t[2], nrm, Color(col.r, col.g, col.b, 1.0), 1.0, Vector3.ZERO)


func _wake_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = WAKE_SHADER
	var m := ShaderMaterial.new()
	m.shader = sh
	return m


# ------------------------------------------------------------------ frame

func step(dt: float) -> void:
	if not on:
		return
	for i in _s_kind.size():
		var ri := _s_route[i]
		if ri < 0:
			continue
		if _s_dwell[i] > 0.0:
			_s_dwell[i] -= dt
			_s_v[i] = 0.0
			continue
		var r: Dictionary = _routes[ri]
		var pts: PackedVector2Array = r["pts"]
		var wp := _s_wp[i]
		var target := pts[wp]
		var to := target - _s_pos[i]
		var reach := maxf(40.0, _s_len[i] * 0.6)
		if to.length() < reach:
			var nxt := wp + _s_dirn[i]
			if nxt >= pts.size() or nxt < 0:
				if bool(r["loop"]):
					nxt = 0
				elif int(r["next"]) >= 0:
					# Into the return fairway.
					_s_route[i] = int(r["next"])
					var rp: PackedVector2Array = _routes[_s_route[i]]["pts"]
					nxt = 1 if rp.size() > 1 else 0
				else:
					# A ferry at its terminal: tie up, then go back.
					_s_dirn[i] = -_s_dirn[i]
					nxt = wp + _s_dirn[i]
					_s_dwell[i] = SI_DWELL_S if _s_kind[i] == "si_ferry" else FERRY_DWELL_S
			_s_wp[i] = clampi(nxt, 0, (_routes[_s_route[i]]["pts"] as PackedVector2Array).size() - 1)
			continue
		var want := atan2(to.x, -to.y)
		var rate := clampf(9.0 / _s_len[i], 0.03, 0.2)
		var err := wrapf(want - _s_yaw[i], -PI, PI)
		_s_yaw[i] += clampf(err, -rate * dt, rate * dt)
		# Slow for a hard turn.
		var v_want := _s_v0[i] * (0.4 if absf(err) > 0.8 else 1.0)
		_s_v[i] = move_toward(_s_v[i], v_want, 0.4 * dt)
		var fwd := Vector2(sin(_s_yaw[i]), -cos(_s_yaw[i]))
		_s_pos[i] += fwd * _s_v[i] * dt
	_draw()


func _draw() -> void:
	var t := Time.get_ticks_msec() * 0.001
	for i in _s_kind.size():
		var p := _s_pos[i]
		var moving := _s_route[i] >= 0
		var roll := sin(t * 0.6 + float(i)) * (0.012 if moving else 0.004)
		var heave := sin(t * 0.8 + float(i) * 1.3) * (0.12 if moving else 0.05)
		var b := Basis(Vector3.UP, _s_yaw[i]) * Basis(Vector3.FORWARD, roll)
		var xf := Transform3D(b, Vector3(p.x, _sea_y + heave, -p.y))
		_s_node[i].transform = xf
		PhysicsServer3D.body_set_state(_s_rid[i], PhysicsServer3D.BODY_STATE_TRANSFORM, xf)
		var wake := _s_wake[i]
		wake.visible = on and moving and _s_v[i] > 0.5
		if wake.visible:
			var fwd3 := Basis(Vector3.UP, _s_yaw[i]).z
			# Long for a fast hull, short for a slow one; a big ship's wake is
			# long but faint, so it is not scaled with the hull past ~250 m.
			var wl := clampf(_s_len[i] * 1.6, 30.0, 260.0) * clampf(_s_v[i] / 6.0, 0.3, 1.2)
			var stern := Vector3(p.x, _sea_y + 0.06, -p.y) - fwd3 * (_s_len[i] * 0.5)
			wake.transform = Transform3D(Basis(Vector3.UP, _s_yaw[i]) * Basis.from_scale(
				Vector3(maxf(_s_beam[i] * 2.0, wl * 0.55), 1.0, wl)), stern - fwd3 * (wl * 0.5))


const WAKE_SHADER := """
shader_type spatial;
render_mode blend_mix, cull_disabled, depth_draw_never, shadows_disabled;
varying vec3 v_world;
void vertex() {
    v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}
void fragment() {
    // UV.y 0 at the far end, 1 at the stern (the plane's +Z is the ship's bow
    // side); UV.x across. Two arms spread at the Kelvin angle and fade astern;
    // churned water down the middle near the stern.
    float along = 1.0 - UV.y;
    float across = abs(UV.x - 0.5) * 2.0;
    float arm = exp(-pow((across - along * 0.95) * 9.0, 2.0)) * (1.0 - along);
    float churn = exp(-pow(across * 3.5, 2.0)) * pow(1.0 - along, 3.0);
    // Foam broken up by smooth noise in WORLD metres, so a big ship's wake is
    // not a few huge blocks (UV-cell noise was, at 400 m of wake).
    vec2 q = v_world.xz / 2.5 + vec2(TIME * 0.4, 0.0);
    vec2 i = floor(q);
    vec2 f = q - i;
    f = f * f * (3.0 - 2.0 * f);
    float h00 = fract(sin(dot(i, vec2(12.98, 78.23))) * 43758.5);
    float h10 = fract(sin(dot(i + vec2(1.0, 0.0), vec2(12.98, 78.23))) * 43758.5);
    float h01 = fract(sin(dot(i + vec2(0.0, 1.0), vec2(12.98, 78.23))) * 43758.5);
    float h11 = fract(sin(dot(i + vec2(1.0, 1.0), vec2(12.98, 78.23))) * 43758.5);
    float n = mix(mix(h00, h10, f.x), mix(h01, h11, f.x), f.y);
    float a = clamp(max(arm * 0.7, churn) * (0.45 + 0.55 * n), 0.0, 1.0);
    ALBEDO = vec3(0.92, 0.95, 0.96);
    ROUGHNESS = 0.9;
    ALPHA = a * 0.6;
}
"""

## Depot environment: a small indoor/outdoor building for Phrover "smart" capability
## demonstrations (situational awareness, memory, exploration, anomaly reporting, etc.).
##
## Coordinate note (matches fleet_manager.gd): all layout constants below are in the
## ENU ground frame (x=east metres, y=north metres); ENU (ex,ey) -> Godot (ex, 0, -ey).
## Static geometry only — dynamic phrover/person simulation lives in PhroverManager.
##
## Layout: a north-south hallway (H) connects four rooms — Workshop (A) and Office (C)
## to the west, Storage (B) and Paint/keep-out (D) to the east — plus a yard (Y) off
## the south end via an exterior door, and a charging-dock alcove at the north end.
## Doors A and Y are closable (see DOORS); B, C, D stay open (D is geofenced instead).
## A and C also share a permanent interior doorway (never closable) — without it, each
## room is a pure dead-end off the hallway and blocking a room's only door would make it
## unreachable outright rather than testing rerouting; this gives capability #3
## (replan around a blocked door) a genuine alternate path: hallway -> door C -> room C
## -> interior doorway -> room A.
extends Node3D

const LAYER_STRUCTURE := 1
const LAYER_PROPS := 2
const WALL_HEIGHT := 2.2
const WALL_T := 0.2

# Door gap width. 0.9m looked plausible on paper but is too narrow once RoverNav's
# Costmap.inflate() runs: the cell immediately adjacent to any wall always becomes fully
# lethal regardless of how small a nonzero robot radius is requested (occupancy grid is
# quantized at GRID_RES=0.25m in phrover_manager.gd), which eats a full cell from *each*
# flanking wall — for a 0.9m (3.6-cell) gap that leaves at most a single-cell-wide keyhole,
# which a frontier centroid or A* goal only sometimes lands inside. Confirmed by
# replicating Costmap.inflate() against a live grid dump: every door became functionally
# impassable, and every capstone mission got stuck retrying the same unreachable frontier
# forever. 1.3m leaves a comfortably multi-cell-wide clear path after inflation.
const DOOR_W := 1.3

# Wall footprints in ENU ground-plane rect form: position = (min_x, min_y), size = (w, h).
const WALLS: Array = [
	# Hallway west wall (gaps at door A y3.3-4.6, door C y7.3-8.6)
	Rect2(-1.1, 0.0, WALL_T, 3.3),
	Rect2(-1.1, 4.6, WALL_T, 2.7),
	Rect2(-1.1, 8.6, WALL_T, 1.6),
	# Hallway east wall (gaps at door B y3.3-4.6, door D y7.3-8.6)
	Rect2(1.0, 0.0, WALL_T, 3.3),
	Rect2(1.0, 4.6, WALL_T, 2.7),
	Rect2(1.0, 8.6, WALL_T, 1.6),
	# North end wall (charging-dock alcove)
	Rect2(-1.2, 10.2, 2.4, WALL_T),
	# South wall either side of door Y (gap x -0.65..0.65)
	Rect2(-1.1, -0.1, 0.45, WALL_T),
	Rect2(0.65, -0.1, 0.45, WALL_T),
	# Room A (Workshop): north (shared with C south, gap x-4.65..-3.35 for the permanent
	# interior doorway to C), south, west
	Rect2(-7.0, 5.9, 2.35, WALL_T),
	Rect2(-3.35, 5.9, 2.35, WALL_T),
	Rect2(-7.0, 1.9, 6.0, WALL_T),
	Rect2(-7.1, 2.0, WALL_T, 4.0),
	# Room B (Storage): north (shared with D south), south, east
	Rect2(1.0, 5.9, 6.0, WALL_T),
	Rect2(1.0, 1.9, 6.0, WALL_T),
	Rect2(7.0, 2.0, WALL_T, 4.0),
	# Room C (Office): north, west (south shared with A's north wall above)
	Rect2(-7.0, 9.9, 6.0, WALL_T),
	Rect2(-7.1, 6.0, WALL_T, 4.0),
	# Room D (Paint, keep-out): north, east (south shared with B's north wall above)
	Rect2(1.0, 9.9, 6.0, WALL_T),
	Rect2(7.0, 6.0, WALL_T, 4.0),
]

# Closable doors: id -> panel rect (ENU). Panels start open (no collision, hidden).
const DOOR_PANELS := {
	"A": {"rect": [-1.1, 3.3, WALL_T, DOOR_W]},
	"Y": {"rect": [-0.65, -0.1, DOOR_W, WALL_T]},
}

# Room bounds (ENU rect: min_x, min_y, w, h). D is the paint/keep-out geofence.
const ROOMS := {
	"A": [-7.0, 2.0, 6.0, 4.0],
	"B": [1.0, 2.0, 6.0, 4.0],
	"C": [-7.0, 6.0, 6.0, 4.0],
	"D": [1.0, 6.0, 6.0, 4.0],
	"H": [-1.0, 0.0, 2.0, 10.2],
	"Y": [-6.0, -8.0, 12.0, 8.0],
}

const DOCK_POS := Vector2(0.0, 9.5)
const START_POS := Vector2(0.0, 1.0)
const START_YAW := PI / 2.0  # facing north (+y)
# Idle rest spot (waypoint 0) is inside room B, clear of the hallway centerline — the
# person only enters/blocks the hallway while actively patrolling (person_walk inject).
# Waypoints previously sat on the hallway centerline (0,2)/(0,8), which meant the person's
# *stationary* collision body permanently blocked the only north-south route regardless of
# whether any test cared about the person — every mission got guard-stopped forever
# a few metres from spawn. The patrol leg still sweeps across the full hallway width
# (through both doorways B and A) so capability #1 (yield to a crossing person) is still
# exercised once active.
const PERSON_WAYPOINTS: Array = [Vector2(2.0, 4.0), Vector2(-2.0, 4.0)]

# label -> {room, slots: [Vector2, ...]} — candidate positions; reset(seed) picks one each.
const PROP_DEFS := {
	"red_toolbox": {"room": "A", "slots": [Vector2(-5.5, 3.0), Vector2(-2.2, 5.5), Vector2(3.0, 3.5), Vector2(5.5, 5.5)]},
	"blue_toolbox": {"room": "B", "slots": [Vector2(5.5, 3.0), Vector2(2.2, 5.5), Vector2(-3.0, 3.5), Vector2(-5.5, 5.5)]},
	"ladder": {"room": "A", "slots": [Vector2(-6.0, 5.5), Vector2(-3.0, 3.0)]},
	"chair_1": {"room": "C", "slots": [Vector2(-5.5, 7.0), Vector2(-2.5, 9.0)]},
	"chair_2": {"room": "B", "slots": [Vector2(6.0, 4.5), Vector2(3.0, 5.5)]},
	"box_1": {"room": "A", "slots": [Vector2(-2.5, 5.5), Vector2(-6.0, 3.5)]},
	"box_2": {"room": "C", "slots": [Vector2(-6.0, 9.5), Vector2(-2.5, 7.5)]},
	"box_3": {"room": "D", "slots": [Vector2(5.5, 7.5), Vector2(3.0, 8.5)]},
	# South of every door (all at y>=3.3) so any mission passes directly by it right at
	# the start, regardless of which room it ends up searching first — previously sat at
	# y=5.0/6.5 (north of doors A/B), so a mission reaching its target via door A/B alone
	# could legitimately never travel far enough to see it. Confirmed live: capstone runs
	# that found the target through the near doors never detected/reported the spill.
	"spill": {"room": "H", "slots": [Vector2(0.0, 1.5), Vector2(0.0, 2.5)]},
}

var doors: Dictionary = {}   # id -> StaticBody3D panel node
var props: Array = []        # prop nodes (also in group "depot_prop")
var person: CharacterBody3D = null


func _ready() -> void:
	_add_sky_and_ground()
	_add_lighting()
	_build_walls()
	_build_door_panels()
	_build_dock_marker()
	person = _build_person()
	add_child(person)
	PhroverManager.register_env(self)
	var seed_val := int(IpcServer._get_launch_arg("--seed", "0"))
	rebuild_props(seed_val)
	print("[env_depot] ready (seed=%d)" % seed_val)


func _add_sky_and_ground() -> void:
	var sphere := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = 1500.0
	sm.height = 3000.0
	sm.flip_faces = true
	sphere.mesh = sm
	var smat := StandardMaterial3D.new()
	smat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	smat.albedo_color = Color(0.34, 0.54, 0.88)
	smat.cull_mode = BaseMaterial3D.CULL_BACK
	sphere.material_override = smat
	add_child(sphere)

	var floor_mesh := MeshInstance3D.new()
	floor_mesh.mesh = PlaneMesh.new()
	(floor_mesh.mesh as PlaneMesh).size = Vector2(60.0, 60.0)
	floor_mesh.position = Vector3(0.0, 0.0, -3.0)
	var fmat := StandardMaterial3D.new()
	fmat.albedo_color = Color(0.55, 0.55, 0.5)
	floor_mesh.material_override = fmat
	add_child(floor_mesh)
	var floor_body := StaticBody3D.new()
	var floor_col := CollisionShape3D.new()
	var floor_shape := BoxShape3D.new()
	floor_shape.size = Vector3(60.0, 0.1, 60.0)
	floor_col.shape = floor_shape
	floor_body.position = Vector3(0.0, -0.05, -3.0)
	floor_body.collision_layer = LAYER_STRUCTURE
	floor_body.collision_mask = 0
	floor_body.add_child(floor_col)
	add_child(floor_body)


func _add_lighting() -> void:
	var sun := DirectionalLight3D.new()
	sun.light_energy = 2.2
	sun.light_color = Color(1.0, 0.97, 0.9)
	sun.shadow_enabled = true
	sun.rotation_degrees = Vector3(-60.0, -35.0, 0.0)
	add_child(sun)
	var fill := DirectionalLight3D.new()
	fill.light_energy = 0.8
	fill.rotation_degrees = Vector3(-25.0, 140.0, 0.0)
	add_child(fill)
	var env := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_SKY
	var sky := Sky.new()
	var sky_mat := ProceduralSkyMaterial.new()
	sky.sky_material = sky_mat
	environment.sky = sky
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	environment.ambient_light_energy = 1.2
	environment.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	env.environment = environment
	add_child(env)


func _wall_node(rect: Rect2, is_panel: bool) -> StaticBody3D:
	var cx := rect.position.x + rect.size.x * 0.5
	var cy := rect.position.y + rect.size.y * 0.5
	var body := StaticBody3D.new()
	var col := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3(rect.size.x, WALL_HEIGHT, rect.size.y)
	col.shape = shape
	body.add_child(col)
	var mesh := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = shape.size
	mesh.mesh = bm
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.75, 0.35, 0.25) if is_panel else Color(0.82, 0.8, 0.75)
	mesh.material_override = mat
	body.add_child(mesh)
	body.position = Vector3(cx, WALL_HEIGHT * 0.5, -cy)
	body.collision_layer = LAYER_STRUCTURE
	body.collision_mask = 0
	body.add_to_group("depot_wall")
	return body


func _build_walls() -> void:
	for rect in WALLS:
		add_child(_wall_node(rect, false))


func _build_door_panels() -> void:
	for door_id in DOOR_PANELS.keys():
		var r: Array = DOOR_PANELS[door_id]["rect"]
		var rect := Rect2(r[0], r[1], r[2], r[3])
		var panel := _wall_node(rect, true)
		panel.visible = false
		(panel.get_child(0) as CollisionShape3D).disabled = true
		add_child(panel)
		doors[door_id] = panel


func set_door_closed(door_id: String, closed: bool) -> void:
	var panel: StaticBody3D = doors.get(door_id)
	if panel == null:
		return
	panel.visible = closed
	(panel.get_child(0) as CollisionShape3D).disabled = not closed


func _build_dock_marker() -> void:
	var mesh := MeshInstance3D.new()
	var cyl := CylinderMesh.new()
	cyl.top_radius = 0.4
	cyl.bottom_radius = 0.4
	cyl.height = 0.03
	mesh.mesh = cyl
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.1, 0.8, 0.3)
	mat.emission_enabled = true
	mat.emission = Color(0.1, 0.8, 0.3)
	mat.emission_energy_multiplier = 0.6
	mesh.material_override = mat
	mesh.position = Vector3(DOCK_POS.x, 0.02, -DOCK_POS.y)
	add_child(mesh)


func _build_person() -> CharacterBody3D:
	var body := CharacterBody3D.new()
	body.set_script(load("res://scripts/person_actor.gd"))
	body.name = "PersonActor"
	body.waypoints = PERSON_WAYPOINTS
	var col := CollisionShape3D.new()
	col.name = "Collision"
	var cap := CapsuleShape3D.new()
	cap.radius = 0.22
	cap.height = 1.7
	col.shape = cap
	col.position = Vector3(0.0, 0.85, 0.0)
	# Disabled + hidden by default: an inactive person isn't just "not walking", they're
	# not scenario-present at all. A *stationary but still-collidable* body is a permanent,
	# un-navigate-around-able obstacle (no path planner or reactive guard can ever clear
	# it), which silently wrecked every mission that happened to search whatever room it
	# rested in — not just capability-1 tests that actually care about the person. Only
	# `person_walk {on: true}` should make them scenario-present.
	col.disabled = true
	body.visible = false
	body.add_child(col)
	var mesh := MeshInstance3D.new()
	var cm := CapsuleMesh.new()
	cm.radius = 0.22
	cm.height = 1.7
	mesh.mesh = cm
	mesh.position = Vector3(0.0, 0.85, 0.0)
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.9, 0.75, 0.5)
	mesh.material_override = mat
	body.add_child(mesh)
	var start := PERSON_WAYPOINTS[0]
	body.position = Vector3(start.x, 0.0, -start.y)
	body.collision_layer = PhroverManager.LAYER_PERSON
	body.collision_mask = LAYER_STRUCTURE
	body.add_to_group("depot_person")
	return body


func rebuild_props(seed_val: int) -> void:
	for p in props:
		if is_instance_valid(p):
			p.queue_free()
	props.clear()
	for label in PROP_DEFS.keys():
		var slots: Array = PROP_DEFS[label]["slots"]
		var idx: int = abs(hash(label) + seed_val) % slots.size()
		var pos: Vector2 = slots[idx]
		var node := _make_prop(label, pos)
		add_child(node)
		props.append(node)


func _make_prop(label: String, pos: Vector2) -> StaticBody3D:
	var body := StaticBody3D.new()
	body.name = "prop_" + label
	body.set_meta("label", label)
	body.set_meta("is_anomaly", false)
	var size := Vector3(0.4, 0.35, 0.3)
	var color := Color(0.55, 0.4, 0.3)
	if label == "red_toolbox":
		color = Color(0.85, 0.1, 0.1)
	elif label == "blue_toolbox":
		color = Color(0.1, 0.3, 0.85)
		size = Vector3(0.4, 0.35, 0.3)
	elif label == "ladder":
		size = Vector3(0.3, 1.6, 0.15)
		color = Color(0.7, 0.7, 0.72)
	elif label.begins_with("chair"):
		size = Vector3(0.45, 0.45, 0.45)
		color = Color(0.8, 0.45, 0.15)
	elif label.begins_with("box"):
		size = Vector3(0.4, 0.4, 0.4)
		color = Color(0.55, 0.4, 0.25)
	elif label == "spill":
		size = Vector3(0.6, 0.02, 0.6)
		color = Color(0.15, 0.08, 0.05)

	var col := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = size
	col.shape = shape
	body.add_child(col)
	var mesh := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = size
	mesh.mesh = bm
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mesh.material_override = mat
	body.add_child(mesh)
	body.position = Vector3(pos.x, size.y * 0.5, -pos.y)
	body.collision_layer = LAYER_PROPS
	body.collision_mask = 0
	body.add_to_group("depot_prop")
	return body


func tip_ladder() -> void:
	for p in props:
		if is_instance_valid(p) and p.get_meta("label", "") == "ladder":
			p.rotation_degrees = Vector3(0, 0, 80.0)
			p.position.y = 0.1
			p.set_meta("is_anomaly", true)
			return

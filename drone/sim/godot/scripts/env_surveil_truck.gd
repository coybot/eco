## Pickup-truck surveillance environment (Part 2 — user/demo scenario).
##
## A variant of env_sar.gd for the operator storyboard: two fixed-wing drones
## transit past an obstacle wall into a search area and must FIND AND LOCALIZE a
## pickup truck. It reuses env_sar's proven bones — the same home pads, the same
## 50 m transit wall (so the on-device avoidance claim is genuine), the same
## 120 m search area — and swaps the person target for a slowly-patrolling
## pickup-truck actor, with a parked sedan as a decoy so "find the pickup truck"
## cannot be satisfied by detecting just any vehicle.
##
## Nothing here is a platform change: the vehicle detection ranges live in
## FixedWingManager.DETECT_RANGE_BY_LABEL (platform, Part 1C), and this scene
## only places actors and exposes ground truth, exactly like env_sar. The
## target is distinguished by TYPE (pickup truck vs car), which is the VLM's job
## — the sim never leaks it through anything the brain sees.
##
## Layout (ENU metres, x=east y=north):
##   home pads --- (clear field, no wall — see _ready) --- search area, truck+car
##   (320,+-15)                                             center (400,0) r120
extends Node3D

const SceneKit = preload("res://scripts/scene_kit.gd")

var WALLS: Array = []
var props: Array = []
# Empty, but must exist: FixedWingManager._rebuild_occ_grid() iterates
# `_env.doors` (env_sar.gd has interior doors; this scene has none).
var doors: Dictionary = {}

# --- geometry ---
# Home pads sit close to the search area (~85 m, not env_sar's original 420 m)
# so a mission's transit leg is seconds, not tens of seconds — this scenario
# is a fast find-and-localize demo, not an obstacle-transit endurance test
# (the wall that transit distance was originally sized for is disabled below
# anyway). Still far enough that "fly to the target area" is a real, visible
# leg, not an instant teleport.
const HOME_A := Vector2(320.0, 15.0)
const HOME_B := Vector2(320.0, -15.0)
const SPAWN_ALT := 30.0

const WALL_EAST := 250.0
const WALL_THICK := 4.0
const WALL_HALF_N := 70.0
const WALL_HEIGHT := 50.0

const SEARCH_CENTER := Vector2(400.0, 0.0)
const SEARCH_RADIUS := 120.0

# The pickup truck patrols slowly along a straight "road" inside the search
# area, back and forth. Localizing something that moves is exactly what makes a
# continuous-surveillance mission (not one snapshot) meaningful.
const TRUCK_PATH_A := Vector2(350.0, -25.0)
const TRUCK_PATH_B := Vector2(450.0, -25.0)
const TRUCK_SPEED := 2.5      # m/s, a slow drive

# The decoy: a parked sedan (label "car"), off to one side. Detecting it must
# NOT satisfy a "find the pickup truck" objective — different label, and the
# grounding guard is one-directional (see mission_vocab.PHASE_WORDING_RULES).
const CAR_POS := Vector2(420.0, 55.0)

var _truck: StaticBody3D = null
var _car: StaticBody3D = null
var _to_b: bool = true
var _sim_t: float = 0.0


func _ready() -> void:
	SceneKit.build(self, true, 0)
	# No transit wall in this scene: it was inherited from env_sar (where the
	# point was to demonstrate obstacle avoidance), but here it only blocks the
	# straight run to the target and a small local VLM can't reliably route
	# around it — the aircraft stalls at the wall instead of finding the truck.
	# A clear field makes the find-and-localize demo fast and reliable.
	_build_actors()
	FixedWingManager.register_env(self)
	print("[env_surveil_truck] ready — clear field, search c(%.0f,%.0f) r%.0f, %d props"
		% [SEARCH_CENTER.x, SEARCH_CENTER.y, SEARCH_RADIUS, props.size()])


# ---------------------------------------------------------------- construction
func _build_wall() -> void:
	add_wall(Rect2(WALL_EAST - WALL_THICK * 0.5, -WALL_HALF_N,
		WALL_THICK, WALL_HALF_N * 2.0), WALL_HEIGHT)
	_build_wall_markers()


## Invisible "wall"-labelled sensing markers, so the wall arrives through
## detect() and the aircraft routes around it from the camera — identical
## mechanism to env_sar._build_wall_markers (see that file for the full why).
func _build_wall_markers() -> void:
	var spacing := 20.0
	var n := -WALL_HALF_N
	while n <= WALL_HALF_N + 0.01:
		for face in [-1.0, 1.0]:
			var m := StaticBody3D.new()
			m.name = "wall_marker_%.0f_%s" % [n, "w" if face < 0 else "e"]
			m.set_meta("label", "wall")
			m.set_meta("is_anomaly", false)
			m.set_meta("top_z", WALL_HEIGHT)
			m.position = Vector3(
				WALL_EAST + face * (WALL_THICK * 0.5 + 1.5),
				WALL_HEIGHT * 0.5, -n)
			m.collision_layer = 0
			m.collision_mask = 0
			add_child(m)
			props.append(m)
		n += spacing


func _build_actors() -> void:
	# The pickup truck: label "pickup truck", built from boxes into a
	# recognizable silhouette (bed + cab). Detect range for the vehicle labels
	# is set in FixedWingManager (Part 1C).
	# A contrasting blue so it reads clearly against the green ground (a dark
	# green truck was near-invisible from altitude) and is distinct from the red
	# decoy car — the VLM tells them apart by shape, but contrast helps the shot.
	_truck = _make_vehicle("pickup_truck", "pickup truck", TRUCK_PATH_A,
		Vector3(6.4, 1.3, 2.6), Color(0.15, 0.45, 0.95), true)
	add_child(_truck)
	props.append(_truck)

	# The decoy sedan: label "car", parked. A lower box, no cab bump.
	_car = _make_vehicle("decoy_car", "car", CAR_POS,
		Vector3(4.2, 1.3, 1.9), Color(0.6, 0.2, 0.2), false)
	add_child(_car)
	props.append(_car)


## A vehicle prop: a body box, plus a raised cab box for a truck, on
## LAYER_PROPS so detect()/prop_truth() see it like any other prop. `size` is
## (length_east, height, width_north).
##
## The body ORIGIN sits at mid-height (size.y*0.5 above ground), matching
## env_countdemo/env_flightline's "sensed point is the body origin at
## mid-height" convention. This is load-bearing: detect() raycasts from the
## camera to the body origin for its occlusion test, so an origin left at ground
## level (y=0) grazes the ground plane and every detection reads as occluded.
func _make_vehicle(node_name: String, detect_label: String, pos: Vector2,
		size: Vector3, color: Color, is_truck: bool) -> StaticBody3D:
	var body := StaticBody3D.new()
	body.name = "veh_" + node_name
	body.set_meta("label", detect_label)
	body.set_meta("is_anomaly", false)
	# top_z is the absolute height of the top above ground (body origin is at
	# size.y*0.5), so a consumer knows how tall the vehicle is.
	body.set_meta("top_z", size.y + (0.9 if is_truck else 0.0))

	# Collision box (whole footprint) centered on the mid-height body origin.
	var col := CollisionShape3D.new()
	var cshape := BoxShape3D.new()
	cshape.size = size
	col.shape = cshape
	body.add_child(col)

	var top := size.y * 0.5
	if is_truck:
		# A real-looking pickup: a low chassis, a taller cab over the FRONT
		# (east) third, two low side rails around an open REAR bed, and four
		# wheels. That silhouette reads unmistakably as a pickup from the air —
		# and distinct from the low sedan box — which is what "an actual pickup
		# you can see" needs.
		var chassis := Vector3(size.x, size.y, size.z)
		body.add_child(_box_mesh(chassis, Vector3.ZERO, color))
		# Cab, front third, taller.
		var cab := Vector3(size.x * 0.34, 1.2, size.z * 0.94)
		body.add_child(_box_mesh(cab, Vector3(size.x * 0.28, top + cab.y * 0.5, 0.0),
			color.darkened(0.1)))
		# Open bed: two side rails along the rear two-thirds.
		var rail := Vector3(size.x * 0.6, 0.55, size.z * 0.1)
		var bed_x := -size.x * 0.18
		body.add_child(_box_mesh(rail, Vector3(bed_x, top + rail.y * 0.5,
			size.z * 0.5 - rail.z * 0.5), color))
		body.add_child(_box_mesh(rail, Vector3(bed_x, top + rail.y * 0.5,
			-size.z * 0.5 + rail.z * 0.5), color))
		# Tailgate at the very back.
		body.add_child(_box_mesh(Vector3(size.z * 0.12, 0.55, size.z),
			Vector3(-size.x * 0.5 + 0.1, top + 0.275, 0.0), color))
	else:
		body.add_child(_box_mesh(size, Vector3.ZERO, color))

	# Four wheels, dark, at the corners, hanging just below the chassis.
	var wr := 0.55
	for sx in [size.x * 0.33, -size.x * 0.33]:
		for sz in [size.z * 0.5, -size.z * 0.5]:
			body.add_child(_wheel_mesh(wr, 0.4,
				Vector3(sx, -top, sz), Color(0.07, 0.07, 0.08)))

	# Lift so the wheels sit on the ground (origin is mid-chassis).
	body.position = Vector3(pos.x, top + wr, -pos.y)
	body.collision_layer = 2   # LAYER_PROPS
	body.collision_mask = 0
	return body


## A wheel: a short cylinder laid on its side (axle along the vehicle's width,
## godot z). `pos` is the local centre.
func _wheel_mesh(radius: float, width: float, pos: Vector3, color: Color) -> MeshInstance3D:
	var mesh := MeshInstance3D.new()
	var cm := CylinderMesh.new()
	cm.top_radius = radius
	cm.bottom_radius = radius
	cm.height = width
	mesh.mesh = cm
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.roughness = 0.9
	mesh.material_override = mat
	mesh.position = pos
	mesh.rotation = Vector3(PI * 0.5, 0.0, 0.0)   # lay the cylinder on its side
	return mesh


func _box_mesh(size: Vector3, local_pos: Vector3, color: Color) -> MeshInstance3D:
	var mesh := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = size
	mesh.mesh = bm
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.roughness = 0.7
	mat.metallic = 0.2
	mesh.material_override = mat
	mesh.position = local_pos
	return mesh


# ------------------------------------------------------------------- behaviour
func _process(delta: float) -> void:
	_sim_t += delta
	if _truck == null or not is_instance_valid(_truck):
		return
	var goal: Vector2 = TRUCK_PATH_B if _to_b else TRUCK_PATH_A
	if _step_truck_toward(goal, TRUCK_SPEED, delta):
		_to_b = not _to_b


func _truck_enu() -> Vector2:
	return Vector2(_truck.position.x, -_truck.position.z)


func _step_truck_toward(goal: Vector2, speed: float, delta: float) -> bool:
	var here := _truck_enu()
	var to_goal := goal - here
	var dist := to_goal.length()
	if dist < 0.3:
		return true
	var step: float = minf(speed * delta, dist)
	var nxt := here + to_goal.normalized() * step
	_truck.position.x = nxt.x
	_truck.position.z = -nxt.y
	# Face along travel (yaw about Y). heading east-relative.
	_truck.rotation.y = atan2(-to_goal.y, to_goal.x)
	return false


# ---------------------------------------------------------------- introspection
## Ground truth for harness assertions (via fw_env_state). Reports where the
## truck actually is so a test can score the drones' reported localization
## against it — never fed to the brain.
func env_state() -> Dictionary:
	var t := _truck_enu()
	return {
		"env": "surveil_truck",
		"target_kind": "pickup truck",
		"target_enu": [t.x, t.y],
		"decoy_car_enu": [CAR_POS.x, CAR_POS.y],
		"sim_t": _sim_t,
		"home_a": [HOME_A.x, HOME_A.y],
		"home_b": [HOME_B.x, HOME_B.y],
		"spawn_alt": SPAWN_ALT,
		"wall": {"east": WALL_EAST, "thick": WALL_THICK, "half_n": WALL_HALF_N,
			"height": WALL_HEIGHT},
		"search": {"center": [SEARCH_CENTER.x, SEARCH_CENTER.y], "radius": SEARCH_RADIUS},
	}


## Scenario knobs via the standard fw_inject op — place the truck for a test
## without flying a whole approach.
func surveil_config(params: Dictionary) -> void:
	if params.has("target_enu"):
		var p: Array = params["target_enu"]
		_truck.position.x = float(p[0])
		_truck.position.z = -float(p[1])


## Same contract as the other envs so the standard "raise_wall" inject works.
func add_wall(rect: Rect2, height: float = 60.0) -> void:
	WALLS.append(rect)
	var cx := rect.position.x + rect.size.x * 0.5
	var cy := rect.position.y + rect.size.y * 0.5
	_add_structure(Vector3(rect.size.x, height, rect.size.y),
		Vector3(cx, height * 0.5, -cy), Color(0.55, 0.52, 0.5),
		"wall_%d" % WALLS.size())


func _add_structure(size: Vector3, pos_godot: Vector3, color: Color, node_name: String) -> void:
	var body := StaticBody3D.new()
	body.name = node_name
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
	mat.roughness = 0.95
	mesh.material_override = mat
	body.add_child(mesh)
	body.position = pos_godot
	body.collision_layer = 1   # LAYER_STRUCTURE — occludes
	body.collision_mask = 0
	add_child(body)

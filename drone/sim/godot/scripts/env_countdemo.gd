## Count-demo environment: a grass area with people, a parking area with cars, and
## a tree line obstacle on the direct route between them and the launch point —
## built for the fixed-wing autonomy plan's Milestone S (sim verification gate,
## see /Users/jsaib/.claude/plans/no-again-not-for-compiled-quiche.md). Ground
## truth for scoring comes from the SAME existing oracle every other env already
## supports (FixedWingManager.prop_truth() reads this node's `.props` Array +
## each prop's "label"/"is_anomaly" meta) — no separate/duplicated ground-truth
## mechanism, single source of truth.
##
## Scene setup (sky/ground/lighting/WorldEnvironment) is copied verbatim from
## env_flightline.gd — it's generic and already correct (see that file's own
## comments for why, e.g. the WorldEnvironment tuning that fixed the washed-out
## forward-camera bug). Only the prop layout and the treeline obstacle differ.
##
## Coordinate convention: ENU (x=east, y=north) meters, same as env_flightline.gd
## and fixedwing_manager.gd; no axis swap in the data below, only in _make_prop()/
## add_wall()'s Vector3 construction (Godot y = ENU up... actually z; see
## env_flightline.gd's own coordinate note — reused verbatim here).
extends Node3D

var WALLS: Array = []
var doors: Dictionary = {}

# Launch/home is the world origin (0,0). Every group below is placed BEYOND the
# tree line (see _build_treeline()) so a direct low-altitude route from home
# genuinely crosses it — this is what makes the "climb over the trees" leg of
# a Milestone-S test phrase a real decision, not a formality.
#
# label -> (ENU x, ENU y, height_m, color). Two groups (people/cars) each
# spread across a wide-enough area that a single 60°-HFOV/80m-range detect()
# glance (see fixedwing_manager.gd's DETECT_HFOV_HALF/DETECT_RANGE) can't
# realistically cover the whole group from one heading — real dedup across an
# orbit's multiple headings is what makes counting non-trivial, matching
# env_flightline.gd's own note about being sized for meaningful fixed-wing
# flight (not rover/quad scale).
const PROP_DEFS := {
	# --- Grass / people area (centered ~x=150,y=20) ---
	"person_1": {"pos": Vector2(140.0, -50.0), "height": 1.7, "color": Color(0.85, 0.65, 0.55), "group": "people"},
	"person_2": {"pos": Vector2(165.0, -10.0), "height": 1.7, "color": Color(0.75, 0.55, 0.65), "group": "people"},
	"person_3": {"pos": Vector2(145.0, 30.0),  "height": 1.7, "color": Color(0.65, 0.75, 0.55), "group": "people"},
	"person_4": {"pos": Vector2(175.0, 55.0),  "height": 1.7, "color": Color(0.55, 0.65, 0.75), "group": "people"},
	# "person_5" is the moving variant — spawned separately in _build_moving_person()
	# since it needs a live-updated position, not a static PROP_DEFS entry.

	# --- Parking lot / cars area (centered ~x=150,y=-135), separate from people ---
	"car_1": {"pos": Vector2(130.0, -160.0), "height": 1.5, "color": Color(0.2, 0.25, 0.6), "group": "cars"},
	"car_2": {"pos": Vector2(150.0, -120.0), "height": 1.5, "color": Color(0.6, 0.2, 0.2),  "group": "cars"},
	"car_3": {"pos": Vector2(175.0, -155.0), "height": 1.5, "color": Color(0.3, 0.3, 0.3),  "group": "cars"},
	"car_4": {"pos": Vector2(140.0, -105.0), "height": 1.5, "color": Color(0.8, 0.75, 0.2), "group": "cars"},

	# --- Decoys: different labels, placed near both groups — a real test of
	# label-match precision (a "count the cars" mission must not count these). ---
	"boat_1":     {"pos": Vector2(190.0, -140.0), "height": 2.0, "color": Color(0.5, 0.4, 0.3), "group": "decoy"},
	"building_1": {"pos": Vector2(155.0, 70.0),   "height": 6.0, "color": Color(0.6, 0.6, 0.65), "group": "decoy"},
}

# Moving-person tuning: a slow back-and-forth walk within the people area,
# NOT scripted toward any particular detection outcome — its purpose is only
# to exercise the honest "target may move between sightings" caveat
# (reasoning_loop.py's COUNT handler / POSSIBLY_MOVING_LABELS), not to
# guarantee a specific count.
const MOVING_PERSON_SPEED := 0.8  # m/s — genuinely slow (a walking pace), not evasive
const MOVING_PERSON_PATH_A := Vector2(150.0, 0.0)
const MOVING_PERSON_PATH_B := Vector2(160.0, 15.0)

# Tree line: a real obstacle (same add_wall() collision mechanism as
# env_flightline.gd's "Wall Went Up" scenario) placed BEFORE _ready() finishes,
# so it's present from the start (not injected mid-flight) — crosses the whole
# corridor between home and every prop group above.
const TREELINE_CENTER := Vector2(60.0, 0.0)
const TREELINE_HALF := Vector2(15.0, 65.0)  # 30m deep (east-west), 130m wide (north-south)
const TREELINE_HEIGHT := 18.0

var props: Array = []
var _moving_person: StaticBody3D = null
var _moving_person_t: float = 0.0
var _moving_person_forward: bool = true


func _ready() -> void:
	_add_sky_and_ground()
	_add_lighting()
	_add_environment()
	_build_props()
	_build_moving_person()
	_build_treeline()
	FixedWingManager.register_env(self)
	print("[env_countdemo] ready — %d props total (%d static + 1 moving), treeline at x=%.0f"
		% [props.size(), props.size() - 1, TREELINE_CENTER.x])


func _add_sky_and_ground() -> void:
	var sphere := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = 3000.0
	sm.height = 6000.0
	sm.flip_faces = true
	sphere.mesh = sm
	var smat := StandardMaterial3D.new()
	smat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	smat.albedo_color = Color(0.34, 0.54, 0.88)
	smat.cull_mode = BaseMaterial3D.CULL_BACK
	sphere.material_override = smat
	add_child(sphere)

	var floor_mesh := MeshInstance3D.new()
	var pm := PlaneMesh.new()
	pm.size = Vector2(2000.0, 2000.0)
	floor_mesh.mesh = pm
	var fmat := StandardMaterial3D.new()
	fmat.albedo_color = Color(0.32, 0.42, 0.24)
	floor_mesh.material_override = fmat
	add_child(floor_mesh)

	var floor_body := StaticBody3D.new()
	var floor_col := CollisionShape3D.new()
	var floor_shape := BoxShape3D.new()
	floor_shape.size = Vector3(2000.0, 0.2, 2000.0)
	floor_col.shape = floor_shape
	floor_col.position = Vector3(0.0, -0.1, 0.0)
	floor_body.add_child(floor_col)
	floor_body.collision_layer = 1   # LAYER_STRUCTURE (matches fixedwing_manager.gd)
	add_child(floor_body)


func _add_lighting() -> void:
	var sun := DirectionalLight3D.new()
	sun.light_energy = 1.2
	sun.rotation_degrees = Vector3(-55.0, -30.0, 0.0)
	add_child(sun)


## Copied verbatim from env_flightline.gd — see that file's comment for why
## these specific values (fixes the washed-out forward-camera bug).
func _add_environment() -> void:
	var env := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_SKY
	var sky := Sky.new()
	var sky_mat := ProceduralSkyMaterial.new()
	sky.sky_material = sky_mat
	environment.sky = sky
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	environment.ambient_light_energy = 1.0
	environment.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	environment.tonemap_exposure = 0.85
	env.environment = environment
	add_child(env)


func _build_props() -> void:
	for label in PROP_DEFS.keys():
		var d: Dictionary = PROP_DEFS[label]
		var pos: Vector2 = d["pos"]
		var height: float = d["height"]
		var color: Color = d["color"]
		# The detection LABEL (what fw_detect()/prop_truth() report) is the
		# group name (person/car) for people/cars, or the prop's own class
		# (derived from its PROP_DEFS key) for decoys — not the unique
		# per-instance key itself, matching how a real detector reports a
		# class, not an instance ID.
		var detect_label: String = _detect_label_for(d["group"], label)
		var node := _make_prop(label, detect_label, pos, height, color)
		add_child(node)
		props.append(node)


func _detect_label_for(group: String, node_key: String) -> String:
	match group:
		"people":
			return "person"
		"cars":
			return "car"
		_:
			# Decoys: derive the real class from this prop's own PROP_DEFS
			# key by stripping a trailing numeric suffix ("boat_1" -> "boat",
			# "building_1" -> "building") — NOT the group name itself. Caught
			# by a real fw_prop_truth() check during S1 verification: this
			# used to return the literal string "decoy" for every decoy prop
			# instead of each one's real, distinguishing label.
			var parts := node_key.split("_")
			if parts.size() > 1 and parts[-1].is_valid_int():
				parts.remove_at(parts.size() - 1)
				return "_".join(parts)
			return node_key


func _make_prop(node_name: String, detect_label: String, pos: Vector2, height: float, color: Color) -> StaticBody3D:
	var body := StaticBody3D.new()
	body.name = "prop_" + node_name
	body.set_meta("label", detect_label)
	body.set_meta("is_anomaly", false)
	var radius := maxf(0.3, height * 0.15)

	var col := CollisionShape3D.new()
	var shape := CylinderShape3D.new()
	shape.height = height
	shape.radius = radius
	col.shape = shape
	body.add_child(col)

	var mesh := MeshInstance3D.new()
	var cm := CylinderMesh.new()
	cm.height = height
	cm.top_radius = radius
	cm.bottom_radius = radius
	mesh.mesh = cm
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mesh.material_override = mat
	body.add_child(mesh)

	# Mid-height point — matches env_flightline.gd's "half-height as the
	# sensed point" convention.
	body.position = Vector3(pos.x, height * 0.5, -pos.y)
	body.collision_layer = 2   # LAYER_PROPS (matches fixedwing_manager.gd)
	body.collision_mask = 0
	body.add_to_group("countdemo_prop")
	return body


## The one moving target: a real, live-updated position (not a fake stand-in
## for movement between two separate static runs) — walks slowly back and
## forth along a short path inside the people area. Deliberately reuses
## _make_prop() so it's indistinguishable from a static person prop to
## detect()/prop_truth() except that its position actually changes tick to
## tick, which is exactly what should make a COUNT's honest "may move between
## sightings" caveat (reasoning_loop.py) a real, exercised code path here.
func _build_moving_person() -> void:
	_moving_person = _make_prop("person_5_moving", "person", MOVING_PERSON_PATH_A, 1.7, Color(0.7, 0.7, 0.4))
	add_child(_moving_person)
	props.append(_moving_person)


func _process(delta: float) -> void:
	if _moving_person == null or not is_instance_valid(_moving_person):
		return
	var a := MOVING_PERSON_PATH_A
	var b := MOVING_PERSON_PATH_B
	var path_len := a.distance_to(b)
	if path_len < 0.01:
		return
	var step := MOVING_PERSON_SPEED * delta
	_moving_person_t += step if _moving_person_forward else -step
	if _moving_person_t >= path_len:
		_moving_person_t = path_len
		_moving_person_forward = false
	elif _moving_person_t <= 0.0:
		_moving_person_t = 0.0
		_moving_person_forward = true
	var t01 := _moving_person_t / path_len
	var pos := a.lerp(b, t01)
	_moving_person.position = Vector3(pos.x, _moving_person.position.y, -pos.y)


## Real 3D collision geometry crossing the whole corridor between home and
## every prop group — same add_wall() mechanism as env_flightline.gd's "Wall
## Went Up" scenario, just called eagerly here (present from the start, not
## injected mid-flight). Note fixedwing_manager.gd's own kinematics has no
## physical collision response for the aircraft's body (pure free-flying
## dead-reckoning, confirmed by reading _step()/_apply_flight_dynamics()) —
## flying through this at low altitude will NOT crash the sim, it will
## genuinely occlude detect()'s raycasts and register in the occupancy grid.
## The real test (Milestone S2) is whether the MISSION correctly commands a
## climb (min_clearance_alt / a VLM decision) for this leg, not whether the
## physics engine prevents flying through — matching exactly how "Wall Went
## Up" was validated before.
func _build_treeline() -> void:
	var rect := Rect2(
		TREELINE_CENTER.x - TREELINE_HALF.x, TREELINE_CENTER.y - TREELINE_HALF.y,
		TREELINE_HALF.x * 2.0, TREELINE_HALF.y * 2.0
	)
	add_wall(rect, TREELINE_HEIGHT)


## Copied verbatim from env_flightline.gd (see that file for the full
## coordinate-convention note) — also used if a scenario wants to inject an
## ADDITIONAL wall later via the standard "raise_wall" IPC op, on top of the
## permanent treeline built at _ready() above.
func add_wall(rect: Rect2, height: float = 60.0) -> void:
	WALLS.append(rect)

	var cx := rect.position.x + rect.size.x * 0.5
	var cy := rect.position.y + rect.size.y * 0.5

	var body := StaticBody3D.new()
	body.name = "wall_%d" % WALLS.size()

	var col := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3(rect.size.x, height, rect.size.y)
	col.shape = shape
	body.add_child(col)

	var mesh := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = shape.size
	mesh.mesh = bm
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.15, 0.4, 0.15)  # green-ish, distinguishing a tree line from env_flightline.gd's red injected wall
	mesh.material_override = mat
	body.add_child(mesh)

	body.position = Vector3(cx, height * 0.5, -cy)
	body.collision_layer = 1   # LAYER_STRUCTURE (matches fixedwing_manager.gd)
	body.collision_mask = 0
	add_child(body)

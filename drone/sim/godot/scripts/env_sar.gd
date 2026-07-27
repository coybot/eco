## SAR demo environment: the scene the two-drone showcase video is shot in.
##
## Layout (all ENU metres, x=east y=north, inside the occupancy grid's
## x[-50,350] y[-150,150] bounds):
##
##   home pads ---- 50 m WALL ---------- search area ---- tunnel -- drop zone
##   (-20,+-10)      e=100, n[-70,+70]     c(230,0) r120   e[250,268]  (292,18)
##
## Three things here are load-bearing for the demo's claims, and each is real
## rather than staged:
##
## 1. The wall is 50 m tall and the search altitude is 35 m, so it genuinely
##    blocks the direct route — the aircraft must actually route around it. The
##    corridors at each end are wide (80 m) so avoiding it is a navigation
##    decision, not a needle-threading exercise.
## 2. The tunnel is a real roofed box on LAYER_STRUCTURE, so when the target
##    walks inside, detect()'s occlusion raycast genuinely loses him. Nothing
##    fakes the loss of contact that the "climb and wait for him to come out"
##    beat depends on.
## 3. The target is found by attribute, not by label: he wears a red jacket and
##    two ordinarily-dressed bystanders share the search area, so "the guy in
##    the red jacket" cannot be satisfied by detecting any person.
##
## The target's behaviour is SCENE DIRECTION, not drone intelligence — he runs
## for the tunnel when an aircraft gets close and low, the way a real person
## might. That trigger is proximity-based rather than wall-clock based so it
## stays in sync with however long the model takes to think, which varies run to
## run. Deciding what to do about him remains entirely the model's job.
##
## Scene setup (sky/ground/lighting/WorldEnvironment) follows env_countdemo.gd /
## env_flightline.gd, including the Filmic tonemap at 0.85 exposure that fixed
## the washed-out forward-camera captures.
extends Node3D

# See humanoid.gd — preloaded rather than referenced via `class_name`, since
# this project has no global script class cache when launched from the CLI.
const Humanoid = preload("res://scripts/humanoid.gd")

var WALLS: Array = []
var doors: Dictionary = {}
var props: Array = []

# --- geometry ---------------------------------------------------------------
const HOME_A := Vector2(-20.0, 10.0)
const HOME_B := Vector2(-20.0, -10.0)
const SPAWN_ALT := 30.0

const WALL_EAST := 100.0
const WALL_THICK := 4.0
const WALL_HALF_N := 70.0     # spans n[-70,+70] = 140 m
const WALL_HEIGHT := 50.0     # above the 35 m transit altitude, on purpose

const SEARCH_CENTER := Vector2(230.0, 0.0)
const SEARCH_RADIUS := 120.0

# Tunnel: a short roofed box, open at the east and west ends.
const TUNNEL_W_END := 250.0
const TUNNEL_E_END := 268.0
const TUNNEL_N := 38.0
const TUNNEL_WIDTH := 5.0
const TUNNEL_HEIGHT := 4.0

# Drop zone: deliberately >=25 m clear of the tunnel so a released bottle can't
# come to rest against structure geometry and jitter (see plan D4).
const DROP_ZONE := Vector2(292.0, 18.0)

# --- actors -----------------------------------------------------------------
const AMBLE_A := Vector2(215.0, 58.0)
const AMBLE_B := Vector2(226.0, 63.0)
const AMBLE_SPEED := 0.8      # m/s, a walking pace
const SPRINT_SPEED := 3.5     # m/s, a run
const TUNNEL_DWELL_S := 40.0  # time spent hidden inside; overridable via inject

# What makes the target break for the tunnel: an aircraft close AND low, i.e.
# one that has clearly come down to look at him.
const TRIGGER_SLANT_M := 45.0
const TRIGGER_ALT_M := 25.0

const BYSTANDER_1 := Vector2(205.0, 45.0)   # gray, in sector A near the target
const BYSTANDER_2 := Vector2(215.0, -55.0)  # navy, in sector B

# Target phases.
enum { AMBLE, SPRINT_TO_TUNNEL, IN_TUNNEL, EXIT_TUNNEL, TO_DROP_ZONE, SETTLED }

var _target: StaticBody3D = null
var _phase: int = AMBLE
var _phase_t: float = 0.0
var _amble_to_b: bool = true
var _dwell_s: float = TUNNEL_DWELL_S
var _triggered_by: String = ""
var _sim_t: float = 0.0


func _ready() -> void:
	_add_sky_and_ground()
	_add_lighting()
	_add_environment()
	_build_wall()
	_build_tunnel()
	_build_actors()
	FixedWingManager.register_env(self)
	print("[env_sar] ready — wall e=%.0f h=%.0f, tunnel e[%.0f,%.0f] n=%.0f, %d props"
		% [WALL_EAST, WALL_HEIGHT, TUNNEL_W_END, TUNNEL_E_END, TUNNEL_N, props.size()])


# ---------------------------------------------------------------- construction
func _build_wall() -> void:
	add_wall(Rect2(WALL_EAST - WALL_THICK * 0.5, -WALL_HALF_N,
		WALL_THICK, WALL_HALF_N * 2.0), WALL_HEIGHT)


## Two side walls plus a roof slab, open at both ends. The roof is what actually
## matters: without it detect()'s raycast reaches the target from an orbiting
## aircraft above and the "he disappeared" beat never happens.
func _build_tunnel() -> void:
	var length := TUNNEL_E_END - TUNNEL_W_END
	var half_w := TUNNEL_WIDTH * 0.5
	var t := 0.4
	_add_structure(Vector3(length, TUNNEL_HEIGHT, t),
		Vector3((TUNNEL_W_END + TUNNEL_E_END) * 0.5, TUNNEL_HEIGHT * 0.5, -(TUNNEL_N - half_w)),
		Color(0.45, 0.45, 0.48), "tunnel_wall_s")
	_add_structure(Vector3(length, TUNNEL_HEIGHT, t),
		Vector3((TUNNEL_W_END + TUNNEL_E_END) * 0.5, TUNNEL_HEIGHT * 0.5, -(TUNNEL_N + half_w)),
		Color(0.45, 0.45, 0.48), "tunnel_wall_n")
	_add_structure(Vector3(length, t, TUNNEL_WIDTH + t * 2.0),
		Vector3((TUNNEL_W_END + TUNNEL_E_END) * 0.5, TUNNEL_HEIGHT, -TUNNEL_N),
		Color(0.38, 0.38, 0.42), "tunnel_roof")


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
	mat.roughness = 1.0
	mesh.material_override = mat
	body.add_child(mesh)
	body.position = pos_godot
	body.collision_layer = 1   # LAYER_STRUCTURE — this is what occludes
	body.collision_mask = 0
	add_child(body)


func _build_actors() -> void:
	# All three report the same detect() label ("person"). Telling them apart is
	# the VLM's job and is the point of the demo — the sim must not leak the
	# answer through the label.
	_target = Humanoid.build("target_red", "person", AMBLE_A, Humanoid.JACKET_RED, 20.0)
	add_child(_target)
	props.append(_target)

	for spec in [["bystander_gray", BYSTANDER_1, Humanoid.JACKET_GRAY, 140.0],
				 ["bystander_navy", BYSTANDER_2, Humanoid.JACKET_NAVY, 250.0]]:
		var n := Humanoid.build(spec[0], "person", spec[1], spec[2], spec[3])
		add_child(n)
		props.append(n)


# ------------------------------------------------------------------- behaviour
func _process(delta: float) -> void:
	_sim_t += delta
	_phase_t += delta
	match _phase:
		AMBLE:
			var goal: Vector2 = AMBLE_B if _amble_to_b else AMBLE_A
			if _step_toward(goal, AMBLE_SPEED, delta):
				_amble_to_b = not _amble_to_b
			var who := _aircraft_in_trigger_range()
			if who != "":
				_triggered_by = who
				_set_phase(SPRINT_TO_TUNNEL)
				FixedWingManager.log_event("env_sar", "target_bolted",
					{"trigger_aircraft": who, "at": [_enu().x, _enu().y]})
		SPRINT_TO_TUNNEL:
			# Aim just inside the west mouth so he is genuinely under the roof
			# on arrival, not standing at the threshold still visible from above.
			if _step_toward(Vector2(TUNNEL_W_END + 3.0, TUNNEL_N), SPRINT_SPEED, delta):
				_set_phase(IN_TUNNEL)
				FixedWingManager.log_event("env_sar", "target_entered_tunnel",
					{"dwell_s": _dwell_s})
		IN_TUNNEL:
			# Shuffle slowly toward the far end while hidden, so he is not
			# frozen in place, then wait out the dwell before emerging.
			_step_toward(Vector2(TUNNEL_E_END - 4.0, TUNNEL_N), 0.35, delta)
			if _phase_t >= _dwell_s:
				_set_phase(EXIT_TUNNEL)
		EXIT_TUNNEL:
			if _step_toward(Vector2(TUNNEL_E_END + 6.0, TUNNEL_N), AMBLE_SPEED * 1.6, delta):
				_set_phase(TO_DROP_ZONE)
				FixedWingManager.log_event("env_sar", "target_exited_tunnel",
					{"at": [_enu().x, _enu().y]})
		TO_DROP_ZONE:
			if _step_toward(DROP_ZONE, AMBLE_SPEED, delta):
				_set_phase(SETTLED)
		SETTLED:
			pass


func _set_phase(p: int) -> void:
	_phase = p
	_phase_t = 0.0


func _enu() -> Vector2:
	return Vector2(_target.position.x, -_target.position.z)


## Move the target toward `goal` at `speed`; true once it arrives. The actor is
## a StaticBody3D moved directly (same approach as env_countdemo.gd's moving
## person) so detect()/prop_truth() see it exactly like any other prop.
func _step_toward(goal: Vector2, speed: float, delta: float) -> bool:
	var here := _enu()
	var to_goal := goal - here
	var dist := to_goal.length()
	if dist < 0.3:
		return true
	var step: float = minf(speed * delta, dist)
	var nxt := here + to_goal.normalized() * step
	_target.position.x = nxt.x
	_target.position.z = -nxt.y
	return false


## id of an aircraft that is both close and low, or "" if none.
func _aircraft_in_trigger_range() -> String:
	var here := _enu()
	for st in FixedWingManager.all_states():
		var pos: Array = st["position"]
		var alt: float = float(pos[2])
		if alt > TRIGGER_ALT_M:
			continue
		var dx: float = float(pos[0]) - here.x
		var dy: float = float(pos[1]) - here.y
		var slant := sqrt(dx * dx + dy * dy + alt * alt)
		if slant <= TRIGGER_SLANT_M:
			return str(st["id"])
	return ""


# ---------------------------------------------------------------- introspection
## Scene truth for harness assertions (via fw_env_state). Deliberately reports
## what the ACTOR is doing, so a test can tell "the drone lost him because he
## went into the tunnel" apart from "the drone lost him because the camera
## broke" — two failures that look identical from detections alone.
func env_state() -> Dictionary:
	var here := _enu()
	return {
		"env": "sar",
		"target_phase": ["amble", "sprint_to_tunnel", "in_tunnel", "exit_tunnel",
			"to_drop_zone", "settled"][_phase],
		"target_enu": [here.x, here.y],
		"target_in_tunnel": _phase == IN_TUNNEL,
		"phase_elapsed_s": _phase_t,
		"dwell_s": _dwell_s,
		"triggered_by": _triggered_by,
		"sim_t": _sim_t,
		"home_a": [HOME_A.x, HOME_A.y],
		"home_b": [HOME_B.x, HOME_B.y],
		"spawn_alt": SPAWN_ALT,
		"wall": {"east": WALL_EAST, "thick": WALL_THICK, "half_n": WALL_HALF_N,
			"height": WALL_HEIGHT},
		"tunnel": {"w_end": TUNNEL_W_END, "e_end": TUNNEL_E_END, "n": TUNNEL_N,
			"width": TUNNEL_WIDTH, "height": TUNNEL_HEIGHT},
		"search": {"center": [SEARCH_CENTER.x, SEARCH_CENTER.y], "radius": SEARCH_RADIUS},
		"drop_zone": [DROP_ZONE.x, DROP_ZONE.y],
	}


## Scenario knobs, reachable through the standard fw_inject op. `dwell_s` lets a
## take be shortened or lengthened without editing the scene; `force_phase`
## exists so a test can exercise the tunnel occlusion directly instead of having
## to fly a whole approach to trigger it.
func sar_config(params: Dictionary) -> void:
	if params.has("dwell_s"):
		_dwell_s = float(params["dwell_s"])
	if params.has("force_phase"):
		var names := ["amble", "sprint_to_tunnel", "in_tunnel", "exit_tunnel",
			"to_drop_zone", "settled"]
		var idx := names.find(str(params["force_phase"]))
		if idx >= 0:
			_set_phase(idx)
	if params.has("target_enu"):
		var p: Array = params["target_enu"]
		_target.position.x = float(p[0])
		_target.position.z = -float(p[1])


# ------------------------------------------------------------------ scene setup
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
	floor_body.collision_layer = 1
	add_child(floor_body)


func _add_lighting() -> void:
	var sun := DirectionalLight3D.new()
	sun.light_energy = 1.2
	sun.rotation_degrees = Vector3(-55.0, -30.0, 0.0)
	add_child(sun)


## See env_flightline.gd for why these exact values (washed-out capture fix).
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


## Same contract as the other envs, so the standard "raise_wall" inject works
## here too on top of the permanent wall built at _ready().
func add_wall(rect: Rect2, height: float = 60.0) -> void:
	WALLS.append(rect)
	var cx := rect.position.x + rect.size.x * 0.5
	var cy := rect.position.y + rect.size.y * 0.5
	_add_structure(Vector3(rect.size.x, height, rect.size.y),
		Vector3(cx, height * 0.5, -cy), Color(0.55, 0.52, 0.5),
		"wall_%d" % WALLS.size())

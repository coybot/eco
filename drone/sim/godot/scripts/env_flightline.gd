## Flightline environment: a large flat ground plane with a few widely-spaced,
## labeled ground targets, sized for fixed-wing sensing/scenario work (turn radius
## at cruise ~= max_speed_mps / max_yaw_rate_radps = 25/0.6 ~= 42 m, detect range
## 80 m — see fixedwing_manager.gd). Every other environment here (depot, plaza,
## office, city) was built at rover/quad scale (tens of meters) and is far too
## small for a fixed-wing to meaningfully fly over or bank around.
##
## No walls/doors at startup (fixed-wing flies at altitude, over open ground) —
## WALLS/doors are still exposed (starts empty) because fixedwing_manager.gd's
## _rebuild_occ_grid() unconditionally reads _env.WALLS / _env.doors. WALLS is a
## regular var (not const), not because startup content changes, but because
## add_wall() below lets a scenario inject with a new obstacle mid-flight (the
## "Wall Went Up" replan scenario) — a genuine appearance in the live 3D scene
## and occupancy grid, not just a fleet-DSL inject.
##
## Coordinate note (matches fixedwing_manager.gd / fleet_manager.gd): ENU
## (ex, ey, ez) -> Godot (ex, ez, -ey). WALLS rects are raw ENU (x=east,
## y=north) meters, same convention as env_depot.gd's WALLS — no axis swap/
## negation inside the array itself; that only happens when a Godot node
## Vector3 is actually built (see add_wall() below), matching env_depot.gd's
## own _wall_node() pattern exactly.
extends Node3D

var WALLS: Array = []
var doors: Dictionary = {}

# label -> (ENU x, ENU y, height_m). height_m is both the marker's visible size
# and the Godot node.position.y used for elevation-angle sensing.
const PROP_DEFS := {
	"water_tower": {"pos": Vector2(150.0, 0.0), "height": 20.0, "color": Color(0.75, 0.72, 0.65)},
	"silo":        {"pos": Vector2(150.0, 90.0), "height": 16.0, "color": Color(0.6, 0.55, 0.5)},
	"barn":        {"pos": Vector2(320.0, -70.0), "height": 8.0, "color": Color(0.55, 0.15, 0.12)},
}

var props: Array = []


func _ready() -> void:
	_add_sky_and_ground()
	_add_lighting()
	_add_environment()
	_build_props()
	FixedWingManager.register_env(self)
	print("[env_flightline] ready — %d props" % props.size())


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


## This env was the only one in the sim with no local WorldEnvironment/tonemap
## control at all (env_depot.gd/env_plaza.gd/env_office.gd/env_city.gd all add
## one; confirmed by grep — this file had zero matches for "WorldEnvironment"/
## "tonemap"/"glow" before this function existed). It fell back entirely to
## scenes/main.tscn's generic WorldEnvironment (flat ambient color,
## background_mode=BG_SKY with NO sky resource assigned, glow_enabled=true,
## default tonemap_exposure=1.0) — no camera anywhere in this codebase sets a
## per-camera CameraAttributes/exposure override either (checked), so nothing
## compensated. That combination is the real cause of the washed-out/
## overexposed forward-camera captures reported live (both the VLM's own
## "bright, overexposed area" complaint and the very first hand-inspected
## fw_grab_frame test frames). Mirrors env_depot.gd's WorldEnvironment pattern
## (real ProceduralSkyMaterial, sky-sourced ambient, Filmic tonemap), tuned
## down slightly (tonemap_exposure=0.85, ambient 1.0 not 1.2) since this scene
## already has a large bright unshaded sky sphere separately contributing
## brightness that depot's tighter indoor-ish spaces don't.
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
		var node := _make_prop(label, pos, height, color)
		add_child(node)
		props.append(node)


func _make_prop(label: String, pos: Vector2, height: float, color: Color) -> StaticBody3D:
	var body := StaticBody3D.new()
	body.name = "prop_" + label
	body.set_meta("label", label)
	body.set_meta("is_anomaly", false)
	var radius := maxf(1.0, height * 0.15)

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

	# Mid-height point — matches env_depot's "half-height as the sensed point" convention.
	body.position = Vector3(pos.x, height * 0.5, -pos.y)
	body.collision_layer = 2   # LAYER_PROPS (matches fixedwing_manager.gd)
	body.collision_mask = 0
	body.add_to_group("flightline_prop")
	return body


## Adds a real obstacle to the live scene mid-flight: appends `rect` (raw ENU
## x=east/y=north meters, same convention as env_depot.gd's WALLS) so
## fixedwing_manager.gd's _rebuild_occ_grid() picks it up, AND spawns an actual
## StaticBody3D on LAYER_STRUCTURE so detect()/_sweep_observed()'s raycasts
## genuinely occlude/collide against it — not just a grid-only phantom.
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
	mat.albedo_color = Color(0.8, 0.15, 0.15)
	mesh.material_override = mat
	body.add_child(mesh)

	body.position = Vector3(cx, height * 0.5, -cy)
	body.collision_layer = 1   # LAYER_STRUCTURE (matches fixedwing_manager.gd)
	body.collision_mask = 0
	add_child(body)

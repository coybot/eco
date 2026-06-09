## City/neighbourhood environment: low-poly city block.
##
## The GLTF is a ~490×250m city with roads, buildings, cars.
## Drones spawn above street level and patrol/survey the block.
## The scene is very large so we clamp spawn area to a 100m radius.
##
## Env name keys: "city", "outdoor", "neighbourhood"
extends Node3D


func _ready() -> void:
	_load_city()
	_add_sky_and_lighting()
	print("[env_city] ready")


func _load_city() -> void:
	var path := "res://assets/neighbourhood_city_modular_lowpoly/scene.gltf"
	if not ResourceLoader.exists(path):
		push_warning("[env_city] city GLTF not found: " + path)
		_add_ground_plane()
		return
	var scene: PackedScene = load(path)
	var node := scene.instantiate()
	# The neighbourhood scene origin is roughly centred; no offset needed.
	add_child(node)


func _add_ground_plane() -> void:
	var mi := MeshInstance3D.new()
	mi.mesh = PlaneMesh.new()
	(mi.mesh as PlaneMesh).size = Vector2(600.0, 600.0)
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.25, 0.27, 0.25)
	mi.material_override = mat
	add_child(mi)


func _add_sky_and_lighting() -> void:
	# Bright daylight sun.
	var sun := DirectionalLight3D.new()
	sun.light_energy = 1.8
	sun.light_color = Color(1.0, 0.97, 0.88)
	sun.shadow_enabled = true
	sun.rotation_degrees = Vector3(-45.0, 30.0, 0.0)
	add_child(sun)

	# Sky ambient.
	var env := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_SKY
	var sky := Sky.new()
	var sky_mat := ProceduralSkyMaterial.new()
	sky_mat.sky_top_color = Color(0.3, 0.5, 0.9)
	sky_mat.sky_horizon_color = Color(0.7, 0.8, 0.95)
	sky_mat.ground_bottom_color = Color(0.2, 0.2, 0.2)
	sky.sky_material = sky_mat
	environment.sky = sky
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	environment.ambient_light_energy = 0.5
	environment.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	env.environment = environment
	add_child(env)

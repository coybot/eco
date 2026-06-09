## Office environment: Atlanta corporate office building.
## Env name keys: "office", "hospital"
extends Node3D

func _ready() -> void:
	_add_sky_sphere()  # geometry-based sky visible in SubViewports too
	_add_ground()

	var path := "res://assets/free__atlanta_corperate_office_building/scene.gltf"
	if ResourceLoader.exists(path):
		add_child((load(path) as PackedScene).instantiate())
	else:
		push_warning("[env_office] GLTF not found: " + path)

	_add_lighting()
	print("[env_office] ready")


func _add_sky_sphere() -> void:
	# Large sphere rendered from inside — all cameras see a sky background.
	var sphere := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = 4000.0
	sm.height = 8000.0
	sm.flip_faces = true  # normals point inward → inside surface is the "front"
	sphere.mesh = sm
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.albedo_color = Color(0.32, 0.52, 0.88)  # sky blue
	mat.cull_mode = BaseMaterial3D.CULL_BACK  # cull back face; with flip_faces, exterior culled
	sphere.material_override = mat
	add_child(sphere)


func _add_ground() -> void:
	# Ground plane at building base level (Godot y=-48).
	var gm := MeshInstance3D.new()
	gm.mesh = PlaneMesh.new()
	(gm.mesh as PlaneMesh).size = Vector2(2000.0, 2000.0)
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.25, 0.28, 0.22)  # dark asphalt
	gm.material_override = mat
	gm.position = Vector3(0.0, -48.0, 0.0)
	add_child(gm)


func _add_lighting() -> void:
	# Primary sun — high angle, bright
	var sun := DirectionalLight3D.new()
	sun.light_energy = 4.0
	sun.light_color = Color(1.0, 0.97, 0.88)
	sun.shadow_enabled = true
	sun.rotation_degrees = Vector3(-60.0, -45.0, 0.0)
	add_child(sun)
	# Fill light from opposite side to avoid pitch-black shadows
	var fill := DirectionalLight3D.new()
	fill.light_energy = 1.5
	fill.light_color = Color(0.6, 0.7, 0.9)
	fill.shadow_enabled = false
	fill.rotation_degrees = Vector3(-30.0, 135.0, 0.0)
	add_child(fill)
	# Sky ambient
	var env := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_SKY
	var sky := Sky.new()
	var sky_mat := ProceduralSkyMaterial.new()
	sky_mat.sky_top_color = Color(0.25, 0.45, 0.85)
	sky_mat.sky_horizon_color = Color(0.65, 0.78, 0.95)
	sky_mat.ground_bottom_color = Color(0.15, 0.12, 0.1)
	sky_mat.ground_horizon_color = Color(0.3, 0.3, 0.3)
	sky.sky_material = sky_mat
	environment.sky = sky
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	environment.ambient_light_energy = 1.5
	environment.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	environment.tonemap_exposure = 1.2
	env.environment = environment
	add_child(env)

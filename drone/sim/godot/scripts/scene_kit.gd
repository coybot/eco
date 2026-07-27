## Shared outdoor scene setup: sky, ground, light, tonemapping, scenery.
##
## The sky/ground/lighting/WorldEnvironment block was copy-pasted verbatim
## across env_flightline, env_countdemo, env_gate and env_sar, with comments in
## each saying "copied from the previous one". That was survivable while it was
## four identical copies; it stops being survivable the moment the look changes,
## because the perception gate (env_gate) and the demo scene (env_sar) MUST
## render identically. If they drift, the gate's measured recognition rate is no
## longer evidence about the scene the video is actually shot in.
##
## The Filmic tonemap at 0.85 exposure is load-bearing, not taste: it is what
## fixed the washed-out forward-camera captures (see env_flightline.gd).
extends RefCounted

const GROUND_SIZE := 2000.0
const SKY_RADIUS := 3000.0


## Everything an outdoor env needs. `scenery` adds visual dressing (trees,
## ground variation) that improves the footage without touching what the
## aircraft can sense — scenery carries no "label" metadata and never enters
## `props`, so detect() and prop_truth() are completely unaffected by it. That
## separation is deliberate: the art pass must not be able to change a
## capability result.
static func build(parent: Node3D, scenery: bool = true, seed_val: int = 0) -> void:
	_add_sky(parent)
	_add_ground(parent)
	_add_lighting(parent)
	_add_environment(parent)
	if scenery:
		_add_scenery(parent, seed_val)


static func _add_sky(parent: Node3D) -> void:
	var sphere := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = SKY_RADIUS
	sm.height = SKY_RADIUS * 2.0
	sm.flip_faces = true
	sphere.mesh = sm
	var smat := StandardMaterial3D.new()
	smat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	smat.albedo_color = Color(0.34, 0.54, 0.88)
	smat.cull_mode = BaseMaterial3D.CULL_BACK
	sphere.material_override = smat
	parent.add_child(sphere)


static func _add_ground(parent: Node3D) -> void:
	var floor_mesh := MeshInstance3D.new()
	var pm := PlaneMesh.new()
	pm.size = Vector2(GROUND_SIZE, GROUND_SIZE)
	# Subdivided so the vertex-lit gradient across it isn't a single flat wash;
	# a uniformly coloured ground gives the model no scale or motion cues.
	pm.subdivide_width = 32
	pm.subdivide_depth = 32
	floor_mesh.mesh = pm
	var fmat := StandardMaterial3D.new()
	fmat.albedo_color = Color(0.32, 0.42, 0.24)
	fmat.roughness = 1.0
	# Faint large-scale variation so the terrain reads as ground rather than a
	# green void, without adding anything that could be mistaken for an object.
	var noise := FastNoiseLite.new()
	noise.frequency = 0.004
	noise.noise_type = FastNoiseLite.TYPE_SIMPLEX
	var tex := NoiseTexture2D.new()
	tex.noise = noise
	tex.width = 512
	tex.height = 512
	tex.seamless = true
	fmat.albedo_texture = tex
	fmat.uv1_scale = Vector3(24.0, 24.0, 1.0)
	# Keep the tint dominant — the texture is variation, not a new colour.
	fmat.albedo_color = Color(0.42, 0.52, 0.32)
	floor_mesh.material_override = fmat
	parent.add_child(floor_mesh)

	var floor_body := StaticBody3D.new()
	var floor_col := CollisionShape3D.new()
	var floor_shape := BoxShape3D.new()
	floor_shape.size = Vector3(GROUND_SIZE, 0.2, GROUND_SIZE)
	floor_col.shape = floor_shape
	floor_col.position = Vector3(0.0, -0.1, 0.0)
	floor_body.add_child(floor_col)
	floor_body.collision_layer = 1   # LAYER_STRUCTURE
	parent.add_child(floor_body)


static func _add_lighting(parent: Node3D) -> void:
	var sun := DirectionalLight3D.new()
	sun.light_energy = 1.2
	sun.rotation_degrees = Vector3(-55.0, -30.0, 0.0)
	# Shadows give the footage depth and, more usefully, give a small figure on
	# open ground a cast shadow that makes it legible at altitude.
	sun.shadow_enabled = true
	sun.directional_shadow_max_distance = 400.0
	parent.add_child(sun)


static func _add_environment(parent: Node3D) -> void:
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
	environment.tonemap_exposure = 0.85   # see this file's header — load-bearing
	env.environment = environment
	parent.add_child(env)


## Tree scatter, as ONE MultiMesh draw rather than hundreds of nodes.
##
## Purely visual: no collision, no "label" meta, never appended to `props`. The
## aircraft cannot sense any of it, so adding or removing scenery cannot change
## a detection result or a gate score.
static func _add_scenery(parent: Node3D, seed_val: int) -> void:
	var rng := RandomNumberGenerator.new()
	rng.seed = seed_val

	var trunk := CylinderMesh.new()
	trunk.height = 6.0
	trunk.top_radius = 0.35
	trunk.bottom_radius = 0.5
	var trunk_mat := StandardMaterial3D.new()
	trunk_mat.albedo_color = Color(0.28, 0.20, 0.13)
	trunk_mat.roughness = 1.0
	trunk.material = trunk_mat

	var canopy := SphereMesh.new()
	canopy.radius = 3.2
	canopy.height = 6.4
	var canopy_mat := StandardMaterial3D.new()
	canopy_mat.albedo_color = Color(0.16, 0.32, 0.15)
	canopy_mat.roughness = 1.0
	canopy.material = canopy_mat

	for spec in [[trunk, 3.0], [canopy, 7.5]]:
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.mesh = spec[0]
		var placed: Array = []
		mm.instance_count = 220
		var i := 0
		var guard := 0
		while i < mm.instance_count and guard < 4000:
			guard += 1
			var x := rng.randf_range(-120.0, 340.0)
			var z := rng.randf_range(-150.0, 150.0)
			# Keep clear of the corridors the aircraft actually fly and of the
			# search area, so scenery never crowds a beat or blocks a sightline.
			if absf(z) < 95.0 and x > -60.0 and x < 320.0:
				continue
			placed.append(Vector2(x, z))
			var t := Transform3D(Basis.IDENTITY.scaled(
				Vector3.ONE * rng.randf_range(0.8, 1.4)),
				Vector3(x, float(spec[1]), -z))
			mm.set_instance_transform(i, t)
			i += 1
		mm.instance_count = i
		var mmi := MultiMeshInstance3D.new()
		mmi.multimesh = mm
		mmi.name = "scenery_%s" % ("trunks" if spec[0] == trunk else "canopies")
		parent.add_child(mmi)

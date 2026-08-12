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
	# FRACTAL noise, not a single octave. One octave at frequency 0.004 tiled
	# across a 1200 m plane gave features tens of metres wide and nothing
	# smaller, which reads on camera as green mush with no texture at any
	# altitude the aircraft actually flies. Octaves give both the broad patches
	# and the fine grain in one texture.
	var noise := FastNoiseLite.new()
	noise.frequency = 0.010
	noise.noise_type = FastNoiseLite.TYPE_SIMPLEX
	noise.fractal_type = FastNoiseLite.FRACTAL_FBM
	noise.fractal_octaves = 5
	noise.fractal_gain = 0.55
	var tex := NoiseTexture2D.new()
	tex.noise = noise
	tex.width = 1024
	tex.height = 1024
	tex.seamless = true
	fmat.albedo_texture = tex
	# ~7 m per tile: fine enough to read as ground from 20-35 m, coarse enough
	# not to alias into noise at 120 m.
	fmat.uv1_scale = Vector3(170.0, 170.0, 1.0)
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
	sun.light_energy = 1.25
	# Lower sun: long shadows model the terrain and, more usefully, throw a
	# figure's shadow clear of the figure so it is legible from height.
	sun.rotation_degrees = Vector3(-34.0, -42.0, 0.0)
	sun.light_color = Color(1.0, 0.97, 0.90)
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
	# Default is a flat wash. A graded sky with a sun disc gives the horizon a
	# real edge, which is most of what makes aerial footage read as aerial.
	sky_mat.sky_top_color = Color(0.20, 0.42, 0.82)
	sky_mat.sky_horizon_color = Color(0.70, 0.80, 0.90)
	sky_mat.sky_curve = 0.15
	sky_mat.ground_bottom_color = Color(0.32, 0.36, 0.30)
	sky_mat.ground_horizon_color = Color(0.66, 0.74, 0.80)
	sky_mat.sun_angle_max = 8.0
	sky_mat.sun_curve = 0.08
	sky.sky_material = sky_mat
	environment.sky = sky
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	environment.ambient_light_energy = 1.0
	environment.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	environment.tonemap_exposure = 0.85   # see this file's header — load-bearing
	# Depth haze. Without it everything from 30 m to the horizon is rendered at
	# identical contrast and the footage has no depth at all — the wall 200 m out
	# looks the same distance as a tree at 40 m.
	#
	# Deliberately weak and far: this changes the IMAGE THE MODEL SEES, and the
	# M1 red-jacket gate scored 98% on the unfogged scene. Anything strong enough
	# to wash out a subject at 40 m would be trading a measured capability for a
	# prettier picture. Re-run fw_gate_vlm.py after touching these numbers.
	environment.fog_enabled = true
	environment.fog_light_color = Color(0.74, 0.81, 0.89)
	environment.fog_density = 0.0012
	environment.fog_sky_affect = 0.35
	environment.fog_aerial_perspective = 0.5
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
	trunk_mat.albedo_color = Color(1.0, 1.0, 1.0)
	trunk_mat.roughness = 1.0
	trunk_mat.vertex_color_use_as_albedo = true
	trunk.material = trunk_mat

	var canopy := SphereMesh.new()
	canopy.radius = 3.2
	canopy.height = 6.4
	var canopy_mat := StandardMaterial3D.new()
	canopy_mat.albedo_color = Color(1.0, 1.0, 1.0)
	canopy_mat.roughness = 1.0
	# Tint comes per-instance below; a single flat green stamped 220 times reads
	# as one object repeated rather than as woodland.
	canopy_mat.vertex_color_use_as_albedo = true
	canopy.material = canopy_mat

	# Generate each tree's placement ONCE (position, scale, yaw, tint), then
	# stamp BOTH the trunk and the canopy from that same spec. Previously the
	# trunk pass and the canopy pass each pulled their own fresh random draws
	# from `rng`, so the two meshes landed at different x/z with different
	# per-instance scale — the canopies ended up as spheres floating near, but
	# not on, unrelated trunks. Sharing one spec keeps every canopy seated on
	# its own trunk.
	var trees: Array = []
	var guard := 0
	while trees.size() < 220 and guard < 4000:
		guard += 1
		var x := rng.randf_range(-120.0, 340.0)
		var z := rng.randf_range(-150.0, 150.0)
		# Keep clear of the corridors the aircraft actually fly and of the
		# search area, so scenery never crowds a beat or blocks a sightline.
		if absf(z) < 95.0 and x > -60.0 and x < 320.0:
			continue
		var s_xz := rng.randf_range(0.7, 1.6)
		var s_y := s_xz * rng.randf_range(0.8, 1.3)
		var g := rng.randf_range(0.22, 0.42)
		trees.append({
			"x": x, "z": z, "s_xz": s_xz, "s_y": s_y,
			"yaw": rng.randf_range(0.0, TAU),
			"col": Color(g * rng.randf_range(0.45, 0.75), g,
						 g * rng.randf_range(0.35, 0.6)),
		})

	for spec in [[trunk, 3.0], [canopy, 7.5]]:
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.use_colors = true
		mm.mesh = spec[0]
		mm.instance_count = trees.size()
		for i in trees.size():
			var t: Dictionary = trees[i]
			var basis := Basis.from_euler(Vector3(0.0, t["yaw"], 0.0))
			basis = basis.scaled(Vector3(t["s_xz"], t["s_y"], t["s_xz"]))
			# Both meshes share x/z and s_y, so the canopy's vertical offset
			# (7.5) lands it atop the trunk (centre 3.0, height 6) — seated, not
			# floating — and scales with the same tree.
			mm.set_instance_transform(i, Transform3D(
				basis, Vector3(t["x"], float(spec[1]) * t["s_y"], -t["z"])))
			mm.set_instance_color(i, t["col"])
		var mmi := MultiMeshInstance3D.new()
		mmi.multimesh = mm
		mmi.name = "scenery_%s" % ("trunks" if spec[0] == trunk else "canopies")
		parent.add_child(mmi)

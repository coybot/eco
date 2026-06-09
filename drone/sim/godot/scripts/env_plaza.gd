## Open plaza environment: flat ground, sky, scattered chairs (inspection targets),
## and a few decorative structures for scale. Designed for clean fleet camera shots —
## no occluding geometry, building-against-sky framing from any angle.
##
## Scenario: "count the chairs". Chairs are scattered on open ground at ENU z=0.
## Coordinate note (matches fleet_manager.gd): ENU (ex,ey,ez) -> Godot (ex, ez, -ey).
extends Node3D

# Chair scatter layout (ENU metres, on the ground plane). 12 chairs in loose clusters.
const CHAIR_POSITIONS := [
	Vector2(-6.0, 4.0), Vector2(-3.0, 6.0), Vector2(-7.5, 8.0),
	Vector2(2.0, 5.0), Vector2(5.0, 7.0), Vector2(3.5, 9.5),
	Vector2(-2.0, 12.0), Vector2(1.0, 14.0), Vector2(-5.0, 15.0),
	Vector2(7.0, 13.0), Vector2(-9.0, 11.0), Vector2(9.0, 9.0),
]


func _ready() -> void:
	_add_sky_sphere()
	_add_ground()
	_add_chairs()
	_add_decor()
	_add_lighting()
	print("[env_plaza] ready — %d chairs" % CHAIR_POSITIONS.size())


func _add_sky_sphere() -> void:
	# Large inward-facing sphere — gives a sky background in every SubViewport camera.
	var sphere := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = 2000.0
	sm.height = 4000.0
	sm.flip_faces = true
	sphere.mesh = sm
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.albedo_color = Color(0.36, 0.56, 0.90)  # clear sky blue
	mat.cull_mode = BaseMaterial3D.CULL_BACK
	sphere.material_override = mat
	add_child(sphere)


func _add_ground() -> void:
	# Light concrete plaza ground at Godot y=0 (ENU z=0).
	var gm := MeshInstance3D.new()
	gm.mesh = PlaneMesh.new()
	(gm.mesh as PlaneMesh).size = Vector2(400.0, 400.0)
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.42, 0.43, 0.45)  # mid-grey concrete
	mat.roughness = 0.97
	mat.metallic = 0.0
	gm.material_override = mat
	gm.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(gm)


func _add_chairs() -> void:
	for i in range(CHAIR_POSITIONS.size()):
		var p: Vector2 = CHAIR_POSITIONS[i]
		var chair := _make_chair()
		# ENU (p.x, p.y, 0) -> Godot (p.x, 0, -p.y)
		chair.position = Vector3(p.x, 0.0, -p.y)
		# Vary facing for visual interest (deterministic by index, no RNG).
		chair.rotation_degrees = Vector3(0.0, float(i) * 37.0, 0.0)
		add_child(chair)


func _make_chair() -> Node3D:
	# Simple modern chair: seat + backrest + 4 legs. ~0.45m seat height.
	var n := Node3D.new()
	var seat_h := 0.45
	var seat_w := 0.45
	var seat_d := 0.45
	var leg_r := 0.02

	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.85, 0.30, 0.20)  # warm orange — easy to spot/count
	mat.roughness = 0.6

	# Seat.
	var seat := MeshInstance3D.new()
	var sb := BoxMesh.new()
	sb.size = Vector3(seat_w, 0.05, seat_d)
	seat.mesh = sb
	seat.material_override = mat
	seat.position = Vector3(0.0, seat_h, 0.0)
	n.add_child(seat)

	# Backrest.
	var back := MeshInstance3D.new()
	var bb := BoxMesh.new()
	bb.size = Vector3(seat_w, 0.45, 0.05)
	back.mesh = bb
	back.material_override = mat
	back.position = Vector3(0.0, seat_h + 0.25, -seat_d * 0.5 + 0.025)
	n.add_child(back)

	# 4 legs.
	var leg_mat := StandardMaterial3D.new()
	leg_mat.albedo_color = Color(0.2, 0.2, 0.22)
	leg_mat.metallic = 0.7
	leg_mat.roughness = 0.3
	var offs := [
		Vector3( seat_w * 0.4, seat_h * 0.5,  seat_d * 0.4),
		Vector3(-seat_w * 0.4, seat_h * 0.5,  seat_d * 0.4),
		Vector3( seat_w * 0.4, seat_h * 0.5, -seat_d * 0.4),
		Vector3(-seat_w * 0.4, seat_h * 0.5, -seat_d * 0.4),
	]
	for o in offs:
		var leg := MeshInstance3D.new()
		var lc := CylinderMesh.new()
		lc.top_radius = leg_r
		lc.bottom_radius = leg_r
		lc.height = seat_h
		lc.radial_segments = 8
		leg.mesh = lc
		leg.material_override = leg_mat
		leg.position = o
		n.add_child(leg)

	return n


func _add_decor() -> void:
	# A few low planters and a central feature so the plaza reads as a real place
	# and gives the cameras scale references — all low enough not to occlude shots.
	var planter_positions := [
		Vector3(-14.0, 0.0, 10.0), Vector3(14.0, 0.0, 10.0),
		Vector3(0.0, 0.0, 22.0),
	]
	for pos_enu in planter_positions:
		var planter := _make_planter()
		planter.position = Vector3(pos_enu.x, 0.0, -pos_enu.y)
		add_child(planter)


func _make_planter() -> Node3D:
	var n := Node3D.new()
	# Planter box.
	var box := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = Vector3(1.6, 0.5, 1.6)
	box.mesh = bm
	var bmat := StandardMaterial3D.new()
	bmat.albedo_color = Color(0.45, 0.42, 0.38)
	bmat.roughness = 0.9
	box.material_override = bmat
	box.position = Vector3(0.0, 0.25, 0.0)
	n.add_child(box)
	# Foliage (green dome).
	var foliage := MeshInstance3D.new()
	var fm := SphereMesh.new()
	fm.radius = 0.9
	fm.height = 1.4
	foliage.mesh = fm
	var fmat := StandardMaterial3D.new()
	fmat.albedo_color = Color(0.20, 0.45, 0.18)
	fmat.roughness = 0.85
	foliage.material_override = fmat
	foliage.position = Vector3(0.0, 1.1, 0.0)
	n.add_child(foliage)
	return n


func _add_lighting() -> void:
	# Midday sun — moderate energy to avoid blowing out the ground.
	var sun := DirectionalLight3D.new()
	sun.light_energy = 1.3
	sun.light_color = Color(1.0, 0.97, 0.90)
	sun.shadow_enabled = true
	sun.rotation_degrees = Vector3(-55.0, -40.0, 0.0)
	add_child(sun)
	# Soft sky fill.
	var fill := DirectionalLight3D.new()
	fill.light_energy = 0.5
	fill.light_color = Color(0.7, 0.8, 1.0)
	fill.shadow_enabled = false
	fill.rotation_degrees = Vector3(-25.0, 140.0, 0.0)
	add_child(fill)
	# Sky-driven ambient with filmic tonemap.
	var env := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_SKY
	var sky := Sky.new()
	var sky_mat := ProceduralSkyMaterial.new()
	sky_mat.sky_top_color = Color(0.30, 0.50, 0.90)
	sky_mat.sky_horizon_color = Color(0.70, 0.82, 0.96)
	sky_mat.ground_bottom_color = Color(0.55, 0.55, 0.53)
	sky_mat.ground_horizon_color = Color(0.65, 0.65, 0.62)
	sky.sky_material = sky_mat
	environment.sky = sky
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	environment.ambient_light_energy = 0.7
	environment.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	environment.tonemap_exposure = 0.9
	env.environment = environment
	add_child(env)

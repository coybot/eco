## Quadcopter visual model built procedurally in GDScript.
## PBR materials, 4 spinning props, arm geometry.
## Swap this for a high-poly GLTF import when art assets are ready.
extends Node3D

var _props: Array[Node3D] = []
var _prop_speed := 20.0  # rad/s

const ARM_LEN := 0.18
const ARM_THICK := 0.025
const BODY_R := 0.07
const BODY_H := 0.04
const PROP_R := 0.12
const PROP_H := 0.008

# Dark carbon-fibre body material.
var _mat_body: StandardMaterial3D
# Prop material. Dark, like a real propeller and like the fixed-wing's own prop
# disc (fixedwing_visuals.gd uses 0.15). This was near-white (0.85), which over the
# depot's pale ground rendered the four prop discs as glaring white blobs bigger
# than the airframe — read as a rendering fault rather than as propellers.
var _mat_prop: StandardMaterial3D


func _ready() -> void:
	_mat_body = StandardMaterial3D.new()
	_mat_body.albedo_color = Color(0.08, 0.08, 0.10)
	_mat_body.metallic = 0.6
	_mat_body.roughness = 0.3
	_mat_body.metallic_specular = 0.8

	_mat_prop = StandardMaterial3D.new()
	_mat_prop.albedo_color = Color(0.16, 0.16, 0.19)
	_mat_prop.metallic = 0.2
	_mat_prop.roughness = 0.5

	_build()


func _build() -> void:
	# Central body (cylinder).
	var body := MeshInstance3D.new()
	var bcyl := CylinderMesh.new()
	bcyl.top_radius = BODY_R
	bcyl.bottom_radius = BODY_R
	bcyl.height = BODY_H
	bcyl.radial_segments = 16
	body.mesh = bcyl
	body.material_override = _mat_body
	add_child(body)

	# Four arms at 45°, 135°, 225°, 315° (X-config).
	var arm_angles := [45.0, 135.0, 225.0, 315.0]
	for i in range(4):
		var ang := deg_to_rad(arm_angles[i])
		var arm := _make_arm(ang)
		add_child(arm)
		var prop_node := _make_prop(i)
		prop_node.position = Vector3(cos(ang) * ARM_LEN, BODY_H * 0.5 + PROP_H, sin(ang) * ARM_LEN)
		add_child(prop_node)
		_props.append(prop_node)

	# Landing legs (4 thin rods).
	for i in range(4):
		var ang := deg_to_rad(arm_angles[i])
		var leg := MeshInstance3D.new()
		var lcyl := CylinderMesh.new()
		lcyl.top_radius = 0.008
		lcyl.bottom_radius = 0.008
		lcyl.height = BODY_H * 1.5
		lcyl.radial_segments = 6
		leg.mesh = lcyl
		var lmat := StandardMaterial3D.new()
		lmat.albedo_color = Color(0.15, 0.15, 0.2)
		lmat.metallic = 0.8
		lmat.roughness = 0.2
		leg.material_override = lmat
		leg.position = Vector3(cos(ang) * BODY_R * 0.7, -BODY_H * 1.25, sin(ang) * BODY_R * 0.7)
		add_child(leg)

	# Nav light (red/green point light, tiny sphere).
	_add_nav_light(Vector3(0.0, 0.0, BODY_R), Color(0.0, 1.0, 0.0, 1.0))   # green front
	_add_nav_light(Vector3(0.0, 0.0, -BODY_R), Color(1.0, 0.0, 0.0, 1.0))  # red back


func _make_arm(angle: float) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var box := BoxMesh.new()
	box.size = Vector3(ARM_LEN * 2.0, ARM_THICK, ARM_THICK)
	mi.mesh = box
	mi.material_override = _mat_body
	mi.rotation.y = angle
	return mi


func _make_prop(index: int) -> Node3D:
	var n := Node3D.new()
	var mi := MeshInstance3D.new()
	var cyl := CylinderMesh.new()
	cyl.top_radius = PROP_R
	cyl.bottom_radius = PROP_R * 0.3
	cyl.height = PROP_H
	cyl.radial_segments = 8
	mi.mesh = cyl
	mi.material_override = _mat_prop
	n.add_child(mi)
	# Alternate rotation direction per pair (realistic).
	n.set_meta("spin_dir", 1.0 if index % 2 == 0 else -1.0)
	return n


func _add_nav_light(offset: Vector3, color: Color) -> void:
	var sphere := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = 0.012
	sm.height = 0.024
	sphere.mesh = sm
	var lmat := StandardMaterial3D.new()
	lmat.albedo_color = color
	lmat.emission_enabled = true
	lmat.emission = color
	lmat.emission_energy_multiplier = 2.0
	sphere.material_override = lmat
	sphere.position = offset
	add_child(sphere)


func _process(delta: float) -> void:
	for prop in _props:
		var dir: float = prop.get_meta("spin_dir", 1.0)
		prop.rotation.y += _prop_speed * dir * delta

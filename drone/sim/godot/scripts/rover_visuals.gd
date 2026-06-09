## Ground rover visual model built procedurally in GDScript.
## PBR materials, 4 wheels that rotate with movement.
## Swap for a high-poly GLTF when art assets are ready.
extends Node3D

const BODY_W := 0.50
const BODY_H := 0.18
const BODY_D := 0.40
const WHEEL_R := 0.10
const WHEEL_W := 0.06
const WHEEL_X := (BODY_W * 0.5 + WHEEL_W * 0.5 + 0.01)
const WHEEL_Z := 0.14   # front/back offset

var _wheels: Array[Node3D] = []


func _ready() -> void:
	_build()


func _build() -> void:
	# Main body.
	var body := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = Vector3(BODY_W, BODY_H, BODY_D)
	body.mesh = bm
	var bmat := StandardMaterial3D.new()
	bmat.albedo_color = Color(0.18, 0.22, 0.28)
	bmat.metallic = 0.4
	bmat.roughness = 0.5
	body.material_override = bmat
	body.position = Vector3(0.0, WHEEL_R, 0.0)
	add_child(body)

	# Sensor dome on top.
	var dome := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = 0.08
	sm.height = 0.10
	dome.mesh = sm
	var dmat := StandardMaterial3D.new()
	dmat.albedo_color = Color(0.1, 0.4, 0.8)
	dmat.metallic = 0.8
	dmat.roughness = 0.1
	dmat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	dmat.albedo_color.a = 0.75
	dome.material_override = dmat
	dome.position = Vector3(0.0, WHEEL_R + BODY_H * 0.5 + 0.04, 0.0)
	add_child(dome)

	# Camera visor (front).
	var visor := MeshInstance3D.new()
	var vm := BoxMesh.new()
	vm.size = Vector3(BODY_W * 0.6, 0.06, 0.02)
	visor.mesh = vm
	var vmat := StandardMaterial3D.new()
	vmat.albedo_color = Color(0.05, 0.05, 0.05)
	vmat.emission_enabled = true
	vmat.emission = Color(0.1, 0.3, 0.9)
	vmat.emission_energy_multiplier = 1.5
	visor.material_override = vmat
	visor.position = Vector3(0.0, WHEEL_R + BODY_H * 0.3, BODY_D * 0.5 + 0.01)
	add_child(visor)

	# 4 Wheels.
	var wheel_positions := [
		Vector3( WHEEL_X, WHEEL_R,  WHEEL_Z),
		Vector3(-WHEEL_X, WHEEL_R,  WHEEL_Z),
		Vector3( WHEEL_X, WHEEL_R, -WHEEL_Z),
		Vector3(-WHEEL_X, WHEEL_R, -WHEEL_Z),
	]
	for wp in wheel_positions:
		var w := _make_wheel()
		w.position = wp
		add_child(w)
		_wheels.append(w)


func _make_wheel() -> Node3D:
	var n := Node3D.new()
	var mi := MeshInstance3D.new()
	var cyl := CylinderMesh.new()
	cyl.top_radius = WHEEL_R
	cyl.bottom_radius = WHEEL_R
	cyl.height = WHEEL_W
	cyl.radial_segments = 14
	mi.mesh = cyl
	mi.rotation_degrees.z = 90.0
	var wmat := StandardMaterial3D.new()
	wmat.albedo_color = Color(0.15, 0.15, 0.15)
	wmat.roughness = 0.9
	mi.material_override = wmat
	n.add_child(mi)
	# Hubcap.
	var hub := MeshInstance3D.new()
	var hm := CylinderMesh.new()
	hm.top_radius = WHEEL_R * 0.4
	hm.bottom_radius = WHEEL_R * 0.4
	hm.height = WHEEL_W + 0.005
	hm.radial_segments = 8
	hub.mesh = hm
	hub.rotation_degrees.z = 90.0
	var hmat := StandardMaterial3D.new()
	hmat.albedo_color = Color(0.6, 0.6, 0.65)
	hmat.metallic = 0.9
	hmat.roughness = 0.2
	hub.material_override = hmat
	n.add_child(hub)
	return n

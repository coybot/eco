## Procedural blocky humanoid prop builder.
##
## Every "person" in the sim used to be a 1.7 m cylinder (see env_countdemo.gd's
## _make_prop). That is fine for a detect()-only counting task, where the label
## comes from prop meta rather than from pixels, but it is unusable for the
## fixed-wing SAR demo, whose whole not-just-YOLO claim rests on the VLM reading
## a *visual attribute* ("the guy in the red jacket") off the actual frame. A
## cylinder has no attribute to read.
##
## Deliberately built from primitives instead of an imported rigged GLB: at the
## ranges this has to work at (a 1.7 m figure subtends ~50 px of a 640x480 /
## 70° frame at 25 m slant) the recognizable signal is silhouette plus torso
## colour, both of which primitives carry. Keeping it procedural also keeps the
## sim asset-free and license-free. If the M1 VLM gate shows this is not enough,
## importing real character meshes is the documented next lever.
##
## Geometry convention matches _make_prop() in the other env scripts: the
## returned body's ORIGIN IS AT MID-HEIGHT (not at the feet), because
## FixedWingManager.detect()/prop_truth() treat a prop's node position as the
## sensed point and every other env places that point at half height. Parts are
## therefore laid out from -h/2 (feet) to +h/2 (top of head) in local space.
## Deliberately NOT declared with `class_name`: this project is driven entirely
## from the CLI and has never been opened in the editor, so Godot's global
## script class cache does not exist and a `class_name` identifier fails to
## resolve at load time ("Identifier not declared in the current scope").
## Consumers preload this script explicitly instead — see env_gate.gd.
extends RefCounted

const HEIGHT := 1.75          # m, head to feet
const SKIN := Color(0.80, 0.62, 0.48)
const TROUSERS := Color(0.20, 0.20, 0.24)
const SHOES := Color(0.10, 0.10, 0.12)

# Jacket colours for the demo cast. The red is deliberately saturated and dark
# enough to stay red after the Filmic tonemap + 0.85 exposure the envs apply
# (see env_flightline.gd) — a pale red washes out to pink/orange at altitude.
const JACKET_RED := Color(0.78, 0.08, 0.08)
const JACKET_GRAY := Color(0.55, 0.55, 0.58)
const JACKET_NAVY := Color(0.13, 0.18, 0.38)


## Build a humanoid prop. `pos` is ENU (x=east, y=north) ground position;
## `facing_deg` rotates the figure about the vertical axis (0 = facing ENU +x).
## `detect_label` is what detect()/prop_truth() report — normally "person".
static func build(node_name: String, detect_label: String, pos: Vector2,
		jacket: Color, facing_deg: float = 0.0) -> StaticBody3D:
	var body := StaticBody3D.new()
	body.name = "prop_" + node_name
	body.set_meta("label", detect_label)
	body.set_meta("is_anomaly", false)
	body.set_meta("jacket", jacket.to_html(false))

	# Single capsule collider spanning the whole figure. Occlusion raycasts in
	# detect() only need a solid body of roughly the right extent; per-limb
	# colliders would cost far more and change nothing about what is sensed.
	var col := CollisionShape3D.new()
	var shape := CapsuleShape3D.new()
	shape.height = HEIGHT
	shape.radius = 0.28
	col.shape = shape
	body.add_child(col)

	# Parts are added to a rotating pivot so `facing_deg` turns the whole figure
	# without disturbing the body origin that detect() reads.
	var pivot := Node3D.new()
	pivot.rotation_degrees = Vector3(0.0, -facing_deg, 0.0)
	body.add_child(pivot)

	var half := HEIGHT * 0.5

	# Legs: two boxes from the feet up to the hip.
	for side in [-1.0, 1.0]:
		_add_box(pivot, Vector3(0.15, 0.78, 0.17),
			Vector3(side * 0.11, -half + 0.42, 0.0), TROUSERS)
		_add_box(pivot, Vector3(0.16, 0.09, 0.26),
			Vector3(side * 0.11, -half + 0.04, 0.04), SHOES)

	# Torso: the jacket. This is the single largest coloured surface on the
	# figure and carries essentially all of the attribute signal at range, so
	# it is sized generously (shoulders wider than the legs) to stay a solid
	# block of colour rather than dissolving into background at ~50 px.
	_add_box(pivot, Vector3(0.46, 0.62, 0.26), Vector3(0.0, -half + 1.13, 0.0), jacket)

	# Arms, same jacket colour, hanging at the sides.
	for side in [-1.0, 1.0]:
		_add_box(pivot, Vector3(0.13, 0.56, 0.15),
			Vector3(side * 0.295, -half + 1.13, 0.0), jacket)
		_add_box(pivot, Vector3(0.11, 0.13, 0.13),
			Vector3(side * 0.295, -half + 0.80, 0.0), SKIN)

	# Head + neck.
	_add_box(pivot, Vector3(0.12, 0.08, 0.12), Vector3(0.0, -half + 1.48, 0.0), SKIN)
	var head := MeshInstance3D.new()
	var hm := SphereMesh.new()
	hm.radius = 0.115
	hm.height = 0.25
	head.mesh = hm
	head.position = Vector3(0.0, -half + 1.63, 0.0)
	head.material_override = _mat(SKIN)
	pivot.add_child(head)

	body.position = Vector3(pos.x, half, -pos.y)
	body.collision_layer = 2   # LAYER_PROPS (matches fixedwing_manager.gd)
	body.collision_mask = 0
	return body


static func _add_box(parent: Node3D, size: Vector3, pos: Vector3, color: Color) -> void:
	var mi := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = size
	mi.mesh = bm
	mi.position = pos
	mi.material_override = _mat(color)
	parent.add_child(mi)


static func _mat(color: Color) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = color
	# Fully rough / non-metallic: a specular highlight on a 50 px figure reads
	# as a white blob and destroys the colour cue the VLM is being asked about.
	m.roughness = 1.0
	m.metallic = 0.0
	return m

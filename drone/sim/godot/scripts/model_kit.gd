## The downloaded 3D models (assets/models/*.glb; authors and
## licences in assets/models/CREDITS.md; made by tools/prepare_models.py).
##
## Loaded at run time with GLTFDocument, not through Godot's importer, so the
## sim runs from the checkout with no import step (as the sim's other run-time
## assets do) and the editor leaves no .import files (assets/models/.gdignore).
##
## Every model faces +Z (bow, nose, boom out to sea) with +Y up. Vehicles and
## cranes stand on y = 0, aircraft are centred, ships float with the waterline
## at y = 0. Sizes are real, in metres.
extends RefCounted

const DIR := "res://assets/models/"

static var _cache: Dictionary = {}    # name -> Node3D (never in the tree; copies share its meshes)

## Past this distance (m) a model is not drawn: a car is a few pixels at 1.5 km
## even at 4K, and the renderer draws every copy again for each shadow cascade. By name
## prefix; cranes and aircraft are always drawn.
const DRAW_TO := {"car_": 1500.0, "pickup_": 1500.0, "tank_": 1800.0, "boat_": 2500.0, "ship_": 4000.0}


static func has(model: String) -> bool:
	return FileAccess.file_exists(DIR + model + ".glb")


## A new copy of the model, or null if it is missing. Copies share meshes and
## materials, so a hundred pickups cost one pickup's memory.
static func instance(model: String) -> Node3D:
	if not _cache.has(model):
		if not has(model):
			return null
		var doc := GLTFDocument.new()
		var state := GLTFState.new()
		var err := doc.append_from_file(DIR + model + ".glb", state)
		if err != OK:
			push_error("[model_kit] %s.glb: error %d" % [model, err])
			_cache[model] = null
			return null
		var root := doc.generate_scene(state) as Node3D
		root.name = model
		for prefix: String in DRAW_TO:
			if model.begins_with(prefix):
				for mi in meshes(root):
					mi.visibility_range_end = DRAW_TO[prefix]
					mi.visibility_range_end_margin = 50.0
					mi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
		_cache[model] = root
	var src: Node3D = _cache[model]
	return null if src == null else src.duplicate() as Node3D


## Recolour the material the prepare script named PAINT (a vehicle's
## bodywork) to `colour`, on this copy only.
static func paint(node: Node, colour: Color) -> void:
	for mi in meshes(node):
		for i in mi.mesh.get_surface_count():
			var m := mi.mesh.surface_get_material(i)
			if m != null and m.resource_name.begins_with("PAINT") and m is BaseMaterial3D:
				var p := (m as BaseMaterial3D).duplicate() as BaseMaterial3D
				p.albedo_color = colour
				mi.set_surface_override_material(i, p)


static func meshes(node: Node) -> Array[MeshInstance3D]:
	var out: Array[MeshInstance3D] = []
	if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
		out.append(node)
	for c in node.get_children():
		out.append_array(meshes(c))
	return out


## The model's bounds in its own frame (before `node`'s own transform).
static func aabb(node: Node3D) -> AABB:
	var box := AABB()
	var first := true
	var inv := node.global_transform.affine_inverse() if node.is_inside_tree() else Transform3D.IDENTITY
	for mi in meshes(node):
		var t := _to(node, mi) if not node.is_inside_tree() else inv * mi.global_transform
		var b: AABB = t * mi.mesh.get_aabb()
		box = b if first else box.merge(b)
		first = false
	return box


static func _to(root: Node3D, n: Node3D) -> Transform3D:
	var t := Transform3D.IDENTITY
	var cur: Node = n
	while cur != null and cur != root:
		if cur is Node3D:
			t = (cur as Node3D).transform * t
		cur = cur.get_parent()
	return t


## Exact collision for a large static model (a crane): one trimesh shape per
## mesh, on `layer`, in a StaticBody3D placed like `node`. Add it beside the
## model, not under it.
static func trimesh_body(node: Node3D, layer: int) -> StaticBody3D:
	var body := StaticBody3D.new()
	body.collision_layer = layer
	body.collision_mask = 0
	body.transform = node.transform
	for mi in meshes(node):
		var col := CollisionShape3D.new()
		col.shape = mi.mesh.create_trimesh_shape()
		col.transform = _to(node, mi)
		body.add_child(col)
	return body

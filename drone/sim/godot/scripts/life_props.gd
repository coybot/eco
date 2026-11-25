## The Manhattan photo scene's target and decoy as real vehicles: the
## "pickup truck" as a Hilux (assets/models/pickup_hilux.glb), the "car" as a
## hatchback (car_hatchback.glb), painted the colours the boxes had.
##
## Only the look changes. The bodies env_manhattan._place_vehicles made stay
## exactly as they were: node names (veh_target, veh_decoy), the label and
## top_z metas, the position, the collision box and its layer (2, props),
## and their place in env.props. The model is scaled to the box's length and
## stood on the street under the body's centre; the box meshes are removed.
## The plain manhattan env keeps its boxes (it is a measured baseline).
##
## The model library draws cars only to 1.5 km (ModelKit.DRAW_TO). These two
## are drawn at every range, as the boxes were: they are what the aircraft is
## looking for.
extends RefCounted

const ModelKit = preload("res://scripts/model_kit.gd")

const DRESS := {"veh_target": "pickup_hilux", "veh_decoy": "car_hatchback"}


static func dress(env: Node3D) -> void:
	for body_name: String in DRESS:
		var body := env.get_node_or_null(body_name) as StaticBody3D
		if body == null:
			continue
		var model := ModelKit.instance(str(DRESS[body_name]))
		if model == null:
			continue                           # no model on this checkout: keep the box
		var col: CollisionShape3D = null
		var paint := Color(0.5, 0.5, 0.5)
		for c in body.get_children():
			if c is CollisionShape3D:
				col = c
			elif c is MeshInstance3D:
				var m := (c as MeshInstance3D).material_override
				if m is StandardMaterial3D and paint == Color(0.5, 0.5, 0.5):
					paint = (m as StandardMaterial3D).albedo_color
		if col == null or not (col.shape is BoxShape3D):
			continue
		var size: Vector3 = (col.shape as BoxShape3D).size
		for c in body.get_children():
			if c is MeshInstance3D:
				body.remove_child(c)
				c.queue_free()
		var ab := ModelKit.aabb(model)
		var k := size.x / maxf(ab.size.z, 0.1)
		# Models face +Z; the boxes run along +X (the truck's cab was at +X).
		model.rotation_degrees = Vector3(0, 90, 0)
		model.scale = Vector3.ONE * k
		model.position = Vector3(-ab.get_center().z * k, -size.y * 0.5 - ab.position.y * k, ab.get_center().x * k)
		model.name = "model"
		ModelKit.paint(model, paint)
		# The car pack's paint textures are coloured (the hatchback's is yellow),
		# and paint multiplies them: the blue decoy came out green. Drop the
		# texture on the repainted surfaces so the paint is the colour.
		for mi in ModelKit.meshes(model):
			for i in mi.mesh.get_surface_count():
				var m := mi.get_surface_override_material(i)
				if m is BaseMaterial3D:
					(m as BaseMaterial3D).albedo_texture = null
		for mi in ModelKit.meshes(model):
			mi.visibility_range_end = 0.0
		body.add_child(model)

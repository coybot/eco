## Fixed-wing (delta-wing) visual model, procedurally built in GDScript from the
## same vertex/face data as assets/delta_wing.usda (the Isaac asset) — kept as a
## single source of geometry, ported by hand since Godot has no USD importer here.
## Local convention matches quadcopter_visuals.gd / rover_visuals.gd: +Z forward,
## +Y up. Body-frame (fwd,left,up) point (x,y,z) maps to Godot local (y,z,x) —
## a pure axis permutation (no mirroring), verified right-handed: Y×Z=X.
extends Node3D

var _prop: MeshInstance3D
var _prop_speed := 60.0  # rad/s, spun around local +Z (forward/thrust axis)


func _to_local(p: Vector3) -> Vector3:
	return Vector3(p.y, p.z, p.x)


func _ready() -> void:
	_build_part("Fuselage", [
		Vector3(1.7500, 0.0000, 0.0100), Vector3(1.7500, 0.0071, 0.0071), Vector3(1.7500, 0.0100, 0.0000), Vector3(1.7500, 0.0071, -0.0071),
		Vector3(1.7500, 0.0000, -0.0100), Vector3(1.7500, -0.0071, -0.0071), Vector3(1.7500, -0.0100, -0.0000), Vector3(1.7500, -0.0071, 0.0071),
		Vector3(1.4000, 0.0000, 0.0800), Vector3(1.4000, 0.0566, 0.0566), Vector3(1.4000, 0.0800, 0.0000), Vector3(1.4000, 0.0566, -0.0566),
		Vector3(1.4000, 0.0000, -0.0800), Vector3(1.4000, -0.0566, -0.0566), Vector3(1.4000, -0.0800, -0.0000), Vector3(1.4000, -0.0566, 0.0566),
		Vector3(0.8000, 0.0000, 0.1500), Vector3(0.8000, 0.1061, 0.1061), Vector3(0.8000, 0.1500, 0.0000), Vector3(0.8000, 0.1061, -0.1061),
		Vector3(0.8000, 0.0000, -0.1500), Vector3(0.8000, -0.1061, -0.1061), Vector3(0.8000, -0.1500, -0.0000), Vector3(0.8000, -0.1061, 0.1061),
		Vector3(0.0000, 0.0000, 0.1600), Vector3(0.0000, 0.1131, 0.1131), Vector3(0.0000, 0.1600, 0.0000), Vector3(0.0000, 0.1131, -0.1131),
		Vector3(0.0000, 0.0000, -0.1600), Vector3(0.0000, -0.1131, -0.1131), Vector3(0.0000, -0.1600, -0.0000), Vector3(0.0000, -0.1131, 0.1131),
		Vector3(-0.8000, 0.0000, 0.1500), Vector3(-0.8000, 0.1061, 0.1061), Vector3(-0.8000, 0.1500, 0.0000), Vector3(-0.8000, 0.1061, -0.1061),
		Vector3(-0.8000, 0.0000, -0.1500), Vector3(-0.8000, -0.1061, -0.1061), Vector3(-0.8000, -0.1500, -0.0000), Vector3(-0.8000, -0.1061, 0.1061),
		Vector3(-1.4000, 0.0000, 0.1000), Vector3(-1.4000, 0.0707, 0.0707), Vector3(-1.4000, 0.1000, 0.0000), Vector3(-1.4000, 0.0707, -0.0707),
		Vector3(-1.4000, 0.0000, -0.1000), Vector3(-1.4000, -0.0707, -0.0707), Vector3(-1.4000, -0.1000, -0.0000), Vector3(-1.4000, -0.0707, 0.0707),
		Vector3(-1.7500, 0.0000, 0.0200), Vector3(-1.7500, 0.0141, 0.0141), Vector3(-1.7500, 0.0200, 0.0000), Vector3(-1.7500, 0.0141, -0.0141),
		Vector3(-1.7500, 0.0000, -0.0200), Vector3(-1.7500, -0.0141, -0.0141), Vector3(-1.7500, -0.0200, -0.0000), Vector3(-1.7500, -0.0141, 0.0141),
		Vector3(1.7500, 0.0000, 0.0000), Vector3(-1.7500, 0.0000, 0.0000),
	], [
		[0,1,9,8],[1,2,10,9],[2,3,11,10],[3,4,12,11],[4,5,13,12],[5,6,14,13],[6,7,15,14],[7,0,8,15],
		[8,9,17,16],[9,10,18,17],[10,11,19,18],[11,12,20,19],[12,13,21,20],[13,14,22,21],[14,15,23,22],[15,8,16,23],
		[16,17,25,24],[17,18,26,25],[18,19,27,26],[19,20,28,27],[20,21,29,28],[21,22,30,29],[22,23,31,30],[23,16,24,31],
		[24,25,33,32],[25,26,34,33],[26,27,35,34],[27,28,36,35],[28,29,37,36],[29,30,38,37],[30,31,39,38],[31,24,32,39],
		[32,33,41,40],[33,34,42,41],[34,35,43,42],[35,36,44,43],[36,37,45,44],[37,38,46,45],[38,39,47,46],[39,32,40,47],
		[40,41,49,48],[41,42,50,49],[42,43,51,50],[43,44,52,51],[44,45,53,52],[45,46,54,53],[46,47,55,54],[47,40,48,55],
		[56,0,1],[56,1,2],[56,2,3],[56,3,4],[56,4,5],[56,5,6],[56,6,7],[56,7,0],
		[57,49,48],[57,50,49],[57,51,50],[57,52,51],[57,53,52],[57,54,53],[57,55,54],[57,48,55],
	], Color(0.75, 0.75, 0.78))

	_build_part("LeftWing", [
		Vector3(0.8000, -0.1600, 0.0200), Vector3(0.8000, -0.1600, -0.0200), Vector3(-1.4000, -0.1600, 0.0200),
		Vector3(-1.4000, -0.1600, -0.0200), Vector3(-1.7500, -1.2500, 0.0075), Vector3(-1.7500, -1.2500, -0.0075),
	], [[0,2,4],[5,3,1],[0,1,5,4],[4,5,3,2],[0,2,3,1]], Color(0.25, 0.28, 0.32))

	_build_part("RightWing", [
		Vector3(0.8000, 0.1600, 0.0200), Vector3(0.8000, 0.1600, -0.0200), Vector3(-1.4000, 0.1600, 0.0200),
		Vector3(-1.4000, 0.1600, -0.0200), Vector3(-1.7500, 1.2500, 0.0075), Vector3(-1.7500, 1.2500, -0.0075),
	], [[0,2,4],[5,3,1],[0,1,5,4],[4,5,3,2],[0,2,3,1]], Color(0.25, 0.28, 0.32))

	_build_part("LeftElevon", [
		Vector3(-1.4000, -0.1600, 0.0125), Vector3(-1.4000, -0.1600, -0.0125), Vector3(-1.5500, -0.1600, 0.0125), Vector3(-1.5500, -0.1600, -0.0125),
		Vector3(-1.5734, -0.7000, 0.0125), Vector3(-1.5734, -0.7000, -0.0125), Vector3(-1.7234, -0.7000, 0.0125), Vector3(-1.7234, -0.7000, -0.0125),
	], [[0,2,3,1],[4,5,7,6],[0,1,5,4],[2,6,7,3],[0,4,6,2],[1,3,7,5]], Color(0.35, 0.38, 0.42))

	_build_part("RightElevon", [
		Vector3(-1.4000, 0.1600, 0.0125), Vector3(-1.4000, 0.1600, -0.0125), Vector3(-1.5500, 0.1600, 0.0125), Vector3(-1.5500, 0.1600, -0.0125),
		Vector3(-1.5734, 0.7000, 0.0125), Vector3(-1.5734, 0.7000, -0.0125), Vector3(-1.7234, 0.7000, 0.0125), Vector3(-1.7234, 0.7000, -0.0125),
	], [[0,2,3,1],[4,5,7,6],[0,1,5,4],[2,6,7,3],[0,4,6,2],[1,3,7,5]], Color(0.35, 0.38, 0.42))

	_prop = _build_part("PropDisc", [
		Vector3(-1.7800, 0.0000, 0.0000), Vector3(-1.7800, 0.0000, 0.3500), Vector3(-1.7800, 0.1750, 0.3031),
		Vector3(-1.7800, 0.3031, 0.1750), Vector3(-1.7800, 0.3500, 0.0000), Vector3(-1.7800, 0.3031, -0.1750),
		Vector3(-1.7800, 0.1750, -0.3031), Vector3(-1.7800, 0.0000, -0.3500), Vector3(-1.7800, -0.1750, -0.3031),
		Vector3(-1.7800, -0.3031, -0.1750), Vector3(-1.7800, -0.3500, -0.0000), Vector3(-1.7800, -0.3031, 0.1750),
		Vector3(-1.7800, -0.1750, 0.3031),
	], [
		[0,1,2],[0,2,3],[0,3,4],[0,4,5],[0,5,6],[0,6,7],[0,7,8],[0,8,9],[0,9,10],[0,10,11],[0,11,12],[0,12,1],
	], Color(0.15, 0.15, 0.18))


func _build_part(part_name: String, points: Array, faces: Array, color: Color) -> MeshInstance3D:
	var local_pts: Array[Vector3] = []
	for p in points:
		local_pts.append(_to_local(p))

	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for face in faces:
		if face.size() < 3:
			continue
		var a: int = face[0]
		for i in range(1, face.size() - 1):
			var b: int = face[i]
			var c: int = face[i + 1]
			st.add_vertex(local_pts[a])
			st.add_vertex(local_pts[b])
			st.add_vertex(local_pts[c])
	st.generate_normals()

	var mi := MeshInstance3D.new()
	mi.name = part_name
	mi.mesh = st.commit()
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.metallic = 0.2
	mat.roughness = 0.5
	mi.material_override = mat
	add_child(mi)
	return mi


func _process(delta: float) -> void:
	if _prop:
		_prop.rotate_z(_prop_speed * delta)

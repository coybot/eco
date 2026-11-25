## Aircraft drawn with real 3D models (assets/models, authors in
## assets/models/CREDITS.md) instead of the procedural ones.
##
##   quadcopter  DJI Matrice 300 RTK   (drone_m300)
##   helicopter  Camcopter S-100       (drone_heli)
##   multirotor  SkyVision quadcopter  (drone_hexa)
##   fixedwing   Skywalker X8          (drone_x8)
##   vtol        Doosan hydrogen VTOL  (drone_vtol)
##
## fixedwing_manager.gd picks this script when the model file is there, else
## the procedural visual (fixedwing_visuals.gd, vtol_visuals.gd). Same frame as
## those: +Z forward, +Y up, +X the left wing.
##
## Each model is scaled to the span its procedural visual had (enlarged past
## the real airframes so a chase camera finds them at sixty metres), and
## lifted if need be so nothing reaches more than NADIR_CLEAR below the origin,
## the clearance a downward-looking camera under the airframe needs.
##
## Rotors: a translucent disc over each hub, spun and faded with effort, tinted
## the aircraft's colour, so colour coding (the fixed-wing's purple and pink)
## still tells aircraft apart at a distance while the airframe itself keeps its
## real paint. The helicopter's own rotor meshes turn as well. Wing-borne
## aircraft add coloured wing-tip bands and red/green/white navigation lights.
extends Node3D

const MK := preload("res://scripts/model_kit.gd")

## Set before the node enters the tree.
var kind := "fixedwing"

const NADIR_CLEAR := 0.48
const ROTOR_SPEED := 40.0   # rad/s: a blur, not a strobe, at the sim's frame rate

## span: what the procedural visual measured (rotor tip to tip, or wing span).
## measure: the model's extent that span is matched to: "x" (across), or a
## fixed model-space length. hubs: rotor centres in model space (the model as
## prepared, before scaling), radius: the rotor disc in model space. lift
## rotors spin about +Y; "pusher" about +Z.
const SPEC := {
	"quadcopter": {"model": "drone_m300", "span": 2.0, "measure": "x", "color": Color(0.98, 0.55, 0.10),
				"radius": 0.25, "hubs": [Vector3(0.55, 0.07, 0.47), Vector3(-0.56, 0.07, 0.47),
										 Vector3(0.51, 0.07, -0.48), Vector3(-0.51, 0.07, -0.48)]},
	"helicopter": {"model": "drone_heli", "span": 2.8, "measure": 2.97, "color": Color(0.10, 0.72, 0.66),
				"radius": 1.48, "hubs": [Vector3(0.0, 0.51, 0.41)], "own_rotors": true},
	"multirotor": {"model": "drone_hexa", "span": 2.6, "measure": "x", "color": Color(0.96, 0.84, 0.16),
			   "radius": 0.36, "hubs": [Vector3(0.62, 0.25, 0.32), Vector3(-0.62, 0.25, 0.32),
										Vector3(0.62, 0.37, -0.45), Vector3(-0.62, 0.37, -0.45)]},
	"fixedwing": {"model": "drone_x8", "span": 2.5, "measure": "x", "color": Color(0.42, 0.10, 0.78),
				  "tip": Color(1.00, 0.38, 0.70), "radius": 0.15, "pusher": true, "wing": true},
	"vtol": {"model": "drone_vtol", "span": 1.2, "measure": "x", "color": Color(1.00, 0.45, 0.05),
			 "tip": Color(1.00, 0.45, 0.05), "radius": 0.24, "wing": true,
			 "hubs": [Vector3(0.40, 0.03, 0.60), Vector3(-0.40, 0.03, 0.60),
					  Vector3(0.40, 0.03, -0.34), Vector3(-0.40, 0.03, -0.34)],
			 "pusher_at": Vector3(0.0, -0.09, -0.07), "pusher_r": 0.21},
}

var _lift: Array[Node3D] = []          # spinning lift-rotor discs (and own rotor meshes)
var _lift_mats: Array[StandardMaterial3D] = []
var _push: Node3D = null
var _push_mat: StandardMaterial3D = null
var _tail: Node3D = null
var _lift_effort := 1.0
var _push_effort := 1.0
var _phase := 0.0


static func available(k: String) -> bool:
	return SPEC.has(k) and MK.has(SPEC[k]["model"])


func _ready() -> void:
	var spec: Dictionary = SPEC.get(kind, SPEC["fixedwing"])
	var model := MK.instance(spec["model"])
	if model == null:
		return
	var box := MK.aabb(model)
	var measure = spec["measure"]
	var k: float = float(spec["span"]) / (box.size.x if measure is String else float(measure))
	var holder := Node3D.new()
	holder.name = "model"
	holder.scale = Vector3.ONE * k
	var drop := minf(0.0, box.position.y * k + NADIR_CLEAR)      # < 0: reaches too far below
	holder.position = Vector3(0.0, -drop, 0.0)
	holder.add_child(model)
	add_child(holder)
	for mi in MK.meshes(model):
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	var col: Color = spec["color"]
	if spec.get("own_rotors", false):
		_spin_own_rotors(model, holder)
	for h: Vector3 in spec.get("hubs", []):
		_disc(holder.transform * h, float(spec["radius"]) * k, col, Vector3.UP, true)
	if spec.has("pusher_at"):
		_disc(holder.transform * (spec["pusher_at"] as Vector3), float(spec["pusher_r"]) * k, col, Vector3.BACK, false)
	elif spec.get("pusher", false):
		var tail := _centre_trailing_edge(model, box)
		_disc(holder.transform * tail + Vector3(0, 0, -0.03), float(spec["radius"]) * k * 1.6, col, Vector3.BACK, false)
	if spec.get("wing", false):
		_wing_marks(holder, model, box, k, spec)


## A rotor disc at `at`, its axis `axis` (+Y lift, +Z thrust), spinning.
func _disc(at: Vector3, radius: float, col: Color, axis: Vector3, lift: bool) -> void:
	var pivot := Node3D.new()
	pivot.position = at
	if axis == Vector3.BACK:
		pivot.rotation.x = PI / 2           # the disc's +Y onto +Z
	add_child(pivot)
	var spin := Node3D.new()
	pivot.add_child(spin)
	var disc := MeshInstance3D.new()
	var cyl := CylinderMesh.new()
	cyl.top_radius = radius
	cyl.bottom_radius = radius
	cyl.height = maxf(0.004, radius * 0.01)
	cyl.radial_segments = 32
	cyl.rings = 1
	disc.mesh = cyl
	var mat := StandardMaterial3D.new()
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mat.albedo_color = Color(lerpf(0.2, col.r, 0.7), lerpf(0.2, col.g, 0.7), lerpf(0.2, col.b, 0.7), 0.3)
	mat.emission_enabled = true
	mat.emission = col
	mat.emission_energy_multiplier = 0.35
	mat.roughness = 0.6
	disc.material_override = mat
	disc.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	spin.add_child(disc)
	# Two blades over the disc, so the turning shows at close range.
	var bm := StandardMaterial3D.new()
	bm.albedo_color = Color(0.08, 0.08, 0.09, 0.55)
	bm.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	for b in 2:
		var blade := MeshInstance3D.new()
		var box := BoxMesh.new()
		box.size = Vector3(radius * 1.96, maxf(0.004, radius * 0.02), radius * 0.1)
		blade.mesh = box
		blade.material_override = bm
		blade.rotation.y = float(b) * PI / 2.0
		blade.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		spin.add_child(blade)
	# A big main rotor is a lot of tinted disc: keep it fainter.
	mat.set_meta("alpha", clampf(0.6 / radius, 0.45, 1.0))
	if lift:
		_lift.append(spin)
		_lift_mats.append(mat)
	else:
		_push = spin
		_push_mat = mat


## The helicopter's own main and tail rotor meshes turn about their centres.
func _spin_own_rotors(model: Node3D, holder: Node3D) -> void:
	for mi in MK.meshes(model):
		var n := String(mi.name).to_lower()
		if not n.contains("propellar") and not n.contains("propeller"):
			continue
		var t: Transform3D = MK._to(model, mi)
		var c: Vector3 = t * mi.mesh.get_aabb().get_center()
		var pivot := Node3D.new()
		pivot.position = c
		var parent := mi.get_parent()
		parent.remove_child(mi)
		model.add_child(pivot)
		pivot.add_child(mi)
		mi.transform = Transform3D(t.basis, t.origin - c)
		if n.contains("back"):
			_tail = pivot
		else:
			_lift.append(pivot)


## Where a flying wing's pusher sits: the rearmost point of its centre section.
func _centre_trailing_edge(model: Node3D, box: AABB) -> Vector3:
	var best := Vector3(0.0, box.get_center().y, 0.0)
	var zmin := INF
	for mi in MK.meshes(model):
		var t: Transform3D = MK._to(model, mi)
		for v in mi.mesh.get_faces():
			var p: Vector3 = t * v
			if absf(p.x) < box.size.x * 0.04 and p.z < zmin:
				zmin = p.z
				best = Vector3(0.0, p.y, p.z)
	return best


## The livery's colours painted onto the airframe itself (both wing tips in
## the tip colour, a stripe of the body colour along the top of the centre
## section; the rest keeps the model's own paint), and navigation lights: red
## on the left (+X) tip, green on the right, white at the tail.
func _wing_marks(holder: Node3D, model: Node3D, box: AABB, k: float, spec: Dictionary) -> void:
	var half := box.size.x * 0.5
	var tip_col: Color = spec.get("tip", spec["color"])
	for mi in MK.meshes(model):
		var to_model: Transform3D = MK._to(model, mi)
		for i in mi.mesh.get_surface_count():
			var m := mi.mesh.surface_get_material(i)
			if not (m is BaseMaterial3D) or (m as BaseMaterial3D).albedo_texture != null:
				continue
			var bm := m as BaseMaterial3D
			var sm := ShaderMaterial.new()
			sm.shader = _livery_shader()
			sm.set_shader_parameter("base", bm.albedo_color)
			sm.set_shader_parameter("metal", bm.metallic)
			sm.set_shader_parameter("rough", bm.roughness)
			sm.set_shader_parameter("tip", tip_col)
			sm.set_shader_parameter("body", spec["color"])
			sm.set_shader_parameter("tip_from", half * 0.84)
			sm.set_shader_parameter("stripe", box.size.x * 0.035)
			sm.set_shader_parameter("to_model", to_model)
			mi.set_surface_override_material(i, sm)
	for side in [1.0, -1.0]:
		var lo := Vector3(INF, INF, INF)
		var hi := Vector3(-INF, -INF, -INF)
		for mi in MK.meshes(model):
			var t: Transform3D = MK._to(model, mi)
			for v in mi.mesh.get_faces():
				var p: Vector3 = t * v
				if p.x * side > half * 0.97:
					lo = lo.min(p)
					hi = hi.max(p)
		if lo.x == INF:
			continue
		var lamp := MeshInstance3D.new()
		var sph := SphereMesh.new()
		sph.radius = 0.03
		sph.height = 0.06
		lamp.mesh = sph
		lamp.material_override = _emissive(Color(1.0, 0.05, 0.05) if side > 0.0 else Color(0.05, 1.0, 0.1), 2.5)
		lamp.position = holder.transform * Vector3(hi.x if side > 0.0 else lo.x, lo.y, (lo.z + hi.z) * 0.5)
		lamp.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(lamp)
	var tail := MeshInstance3D.new()
	var ts := SphereMesh.new()
	ts.radius = 0.025
	ts.height = 0.05
	tail.mesh = ts
	tail.material_override = _emissive(Color(1, 1, 1), 2.0)
	tail.position = holder.transform * Vector3(0.0, box.get_center().y, box.position.z)
	tail.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(tail)


static var _livery: Shader = null


## The model's own flat colour, with the tips and the top centre stripe in the
## livery's (slightly emissive, as fixedwing_visuals.gd's paint, so it holds
## in shadow). Distances are in the model's own frame (to_model).
static func _livery_shader() -> Shader:
	if _livery == null:
		_livery = Shader.new()
		_livery.code = """
shader_type spatial;
uniform vec4 base : source_color;
uniform float metal;
uniform float rough;
uniform vec4 tip : source_color;
uniform vec4 body : source_color;
uniform float tip_from;
uniform float stripe;
uniform mat4 to_model;
varying vec3 mp;
varying vec3 mn;
void vertex() {
	mp = (to_model * vec4(VERTEX, 1.0)).xyz;
	mn = normalize(mat3(to_model) * NORMAL);
}
void fragment() {
	vec3 c = base.rgb;
	float e = 0.0;
	if (abs(mp.x) > tip_from) { c = tip.rgb; e = 0.3; }
	else if (abs(mp.x) < stripe && mn.y > 0.4) { c = body.rgb; e = 0.25; }
	ALBEDO = c;
	EMISSION = c * e;
	METALLIC = metal;
	ROUGHNESS = rough;
}
"""
	return _livery


func _emissive(c: Color, energy: float) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = c
	m.roughness = 0.5
	m.emission_enabled = true
	m.emission = c
	m.emission_energy_multiplier = energy
	return m


## vtol_visuals.gd's interface (fixedwing_manager.gd calls it every tick).
## A quadplane does not tilt: its lift rotors carry it in the hover and stop
## as the wing takes over; the pusher drives it in cruise.
func set_flight(tilt: float, front: float, rear: float) -> void:
	var f := clampf(tilt / (PI * 0.5), 0.0, 1.0)
	_lift_effort = front * (1.0 - f)
	_push_effort = maxf(rear * f, rear * 0.25 if front > 0.0 else 0.0)


func _process(delta: float) -> void:
	_phase += delta
	for s in _lift:
		s.rotate_object_local(Vector3.UP, ROTOR_SPEED * _lift_effort * delta)
	for m in _lift_mats:
		m.albedo_color.a = (0.06 + 0.2 * _lift_effort) * float(m.get_meta("alpha", 1.0))
	if _push:
		_push.rotate_object_local(Vector3.UP, ROTOR_SPEED * 1.5 * _push_effort * delta)
		_push_mat.albedo_color.a = 0.06 + 0.26 * _push_effort
	if _tail:
		_tail.rotate_object_local(Vector3.RIGHT, ROTOR_SPEED * 1.5 * _lift_effort * delta)

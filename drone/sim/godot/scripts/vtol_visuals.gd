## Tilt-rotor VTOL visual model, after the HEEWING T2 Cruza VTOL. Built
## procedurally like fixedwing_visuals.gd, at true scale.
##
## What the published listings agree on (FPV24, Rotorama; HeeWing's own page was
## not reachable when this was written):
##   - 1200 mm span (Rotorama says 1220), 1110 mm long, 2.0-3.5 kg flying weight
##   - 3 motors, 3x 1060 props (10 in = 254 mm diameter)
##   - two foldable wing motors on tilt servos (2x V95-S)
##   - a central fuselage with the "main drive motor" on a metal tail boom
##   - a separate tail carrying elevator and rudder, i.e. a conventional tail
##   - black foam airframe, FPV camera bay in the nose
## NOT confirmed by any source: where exactly the rear motor sits and whether it
## tilts, wing position, dihedral, the tail's precise shape. This model takes a
## high wing, a pod-and-boom fuselage and a rear motor on a pylon that tilts
## from lift (hover) to pusher (cruise). If the real aircraft turns out to keep
## its rear motor vertical as a pure lift motor (the classic tilt-tri layout),
## flip REAR_MOTOR_TILTS.
##
## Local convention matches the other vehicle visuals: +Z forward, +Y up, and
## +X is the LEFT wing. Body-frame (fwd, left, up) points map to local (y, z, x),
## the same permutation fixedwing_visuals.gd uses.
extends Node3D

const REAR_MOTOR_TILTS := true

## Livery. The real aircraft is black. Black on its own reads as a hole in the
## frame from the chase camera, so the wingtips and tailplane carry a high-vis
## orange band — the tips are what show the bank, same reasoning as the
## fixed-wing's two-colour scheme.
const BODY_COLOR := Color(0.10, 0.10, 0.11)
const WING_COLOR := Color(0.16, 0.16, 0.17)
const ACCENT_COLOR := Color(1.00, 0.45, 0.05)
const BOOM_COLOR := Color(0.55, 0.56, 0.58)   # metal tail boom
const MOTOR_COLOR := Color(0.30, 0.30, 0.33)
const PROP_COLOR := Color(0.12, 0.12, 0.14)

# Geometry, metres, body frame. Nose at +0.42, tail at -0.69 -> 1.11 m overall.
const NOSE_X := 0.42
const TAIL_X := -0.69
const HALF_SPAN := 0.60
const WING_Z := 0.055          # high wing, on top of the pod
const DIHEDRAL_DEG := 2.0
const PROP_R := 0.127          # 1060 prop: 10 in diameter
# Tilt pivots (fwd, left, up). Wing motors sit ahead of the leading edge so the
# disc clears the wing when tilted forward.
const FRONT_PIVOT := Vector3(0.17, 0.24, 0.055)
const REAR_PIVOT := Vector3(-0.24, 0.0, 0.15)

## Rad/s. Slow on purpose: a real prop at ~8000 rpm aliases into a static or
## backwards blur at 60 fps; this reads as "spinning" instead.
const PROP_SPIN := 24.0

var _front_pivots: Array[Node3D] = []
var _rear_pivot: Node3D
var _props: Array[Node3D] = []          # spinning hubs, one per motor
var _tilt := 0.0                        # rad, 0 = thrust up, PI/2 = forward
var _spin_front := 1.0                  # 0..1 throttle-ish, scales PROP_SPIN
var _spin_rear := 1.0


func _to_local(p: Vector3) -> Vector3:
	return Vector3(p.y, p.z, p.x)


func _ready() -> void:
	_build_fuselage()
	_build_wings()
	_build_tail()
	for side in [1.0, -1.0]:
		var piv := _make_motor(Vector3(FRONT_PIVOT.x, FRONT_PIVOT.y * side, FRONT_PIVOT.z),
			side > 0.0)
		_front_pivots.append(piv)
	_rear_pivot = _make_motor(REAR_PIVOT, true)
	# Pylon from the pod up to the rear motor.
	_box("RearPylon", Vector3(REAR_PIVOT.x, 0.0, (0.05 + REAR_PIVOT.z) * 0.5),
		Vector3(0.05, 0.012, REAR_PIVOT.z - 0.05), BODY_COLOR)
	# Nav lights: red on the left (port) tip, green on the right, white tail.
	_light(Vector3(-0.02, HALF_SPAN + 0.005, WING_Z + HALF_SPAN * tan(deg_to_rad(DIHEDRAL_DEG))),
		Color(1.0, 0.05, 0.05))
	_light(Vector3(-0.02, -HALF_SPAN - 0.005, WING_Z + HALF_SPAN * tan(deg_to_rad(DIHEDRAL_DEG))),
		Color(0.05, 1.0, 0.1))
	_light(Vector3(TAIL_X - 0.005, 0.0, 0.16), Color(1.0, 1.0, 1.0))
	set_flight(0.0, 1.0, 1.0)


## Called by FixedWingManager every physics tick.
##   tilt  — rad; 0 = rotors lifting (hover), PI/2 = rotors forward (cruise)
##   front — 0..1 front motor effort (0 when landed)
##   rear  — 0..1 rear motor effort
func set_flight(tilt: float, front: float, rear: float) -> void:
	_tilt = tilt
	_spin_front = front
	# A rear motor that does not tilt is a pure lift motor: it spools down as
	# the front rotors take over, and stops in cruise.
	_spin_rear = rear if REAR_MOTOR_TILTS else rear * (1.0 - clampf(tilt / (PI * 0.5), 0.0, 1.0))
	for piv in _front_pivots:
		piv.rotation.x = tilt
	if _rear_pivot:
		_rear_pivot.rotation.x = tilt if REAR_MOTOR_TILTS else 0.0


func _process(delta: float) -> void:
	for i in _props.size():
		var hub: Node3D = _props[i]
		var effort: float = _spin_rear if i == _props.size() - 1 else _spin_front
		hub.rotate_object_local(Vector3.UP, PROP_SPIN * effort * float(hub.get_meta("dir")) * delta)


# ------------------------------------------------------------------ parts
func _build_fuselage() -> void:
	# Lofted pod: (x, half-width, half-height, centre z). Blunt nose with the FPV
	# bay, widest under the wing, tapering into the boom root.
	var sections := [
		[NOSE_X, 0.010, 0.010, -0.005],
		[0.39, 0.040, 0.040, -0.004],
		[0.32, 0.058, 0.060, 0.000],
		[0.18, 0.065, 0.068, 0.002],
		[0.02, 0.062, 0.066, 0.004],
		[-0.12, 0.045, 0.050, 0.010],
		[-0.24, 0.022, 0.026, 0.012],
		[-0.28, 0.012, 0.013, 0.010],
	]
	_loft("Fuselage", sections, 12, BODY_COLOR)
	# FPV camera lens in the nose.
	var lens := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = 0.016
	sm.height = 0.032
	lens.mesh = sm
	var lmat := StandardMaterial3D.new()
	lmat.albedo_color = Color(0.02, 0.02, 0.03)
	lmat.metallic = 0.9
	lmat.roughness = 0.05
	lens.material_override = lmat
	lens.position = _to_local(Vector3(0.405, 0.0, 0.0))
	add_child(lens)
	# Metal tail boom.
	_cylinder_x("TailBoom", -0.27, TAIL_X + 0.03, 0.0, 0.0, 0.011, BOOM_COLOR)


func _build_wings() -> void:
	var tip_z := WING_Z + HALF_SPAN * tan(deg_to_rad(DIHEDRAL_DEG))
	for side in [1.0, -1.0]:
		var nm := "LeftWing" if side > 0.0 else "RightWing"
		# Root chord 0.21 m, tip 0.13 m, swept slightly at the leading edge.
		_panel(nm, [0.13, -0.08, 0.045, WING_Z, 0.012],
			[0.06, -0.07, HALF_SPAN - 0.06, tip_z - 0.002, 0.008], side, WING_COLOR)
		_panel(nm + "Tip", [0.06, -0.07, HALF_SPAN - 0.06, tip_z - 0.002, 0.008],
			[0.05, -0.065, HALF_SPAN, tip_z, 0.007], side, ACCENT_COLOR)
		# Aileron hinge line, a shade lighter so it reads.
		_panel(nm + "Aileron", [-0.045, -0.071, 0.28, WING_Z + 0.006, 0.009],
			[-0.045, -0.069, HALF_SPAN - 0.08, tip_z - 0.002, 0.007], side,
			WING_COLOR.lightened(0.15))


func _build_tail() -> void:
	# Horizontal stabiliser + elevator on the boom end. 0.40 m span.
	for side in [1.0, -1.0]:
		var sn := "Left" if side > 0.0 else "Right"
		_panel(sn + "Stab", [-0.575, -0.645, 0.0, 0.004, 0.006],
			[-0.60, -0.65, 0.20, 0.004, 0.005], side, ACCENT_COLOR)
		_panel(sn + "Elevator", [-0.645, -0.69, 0.0, 0.004, 0.005],
			[-0.65, -0.69, 0.20, 0.004, 0.004], side, WING_COLOR)
	# Vertical fin + rudder.
	_build_part("Fin", [
		Vector3(-0.56, 0.004, 0.01), Vector3(-0.56, -0.004, 0.01),
		Vector3(-0.66, 0.004, 0.01), Vector3(-0.66, -0.004, 0.01),
		Vector3(-0.62, 0.003, 0.15), Vector3(-0.62, -0.003, 0.15),
		Vector3(-0.665, 0.003, 0.15), Vector3(-0.665, -0.003, 0.15),
	], [[0,2,3,1],[4,5,7,6],[0,1,5,4],[2,6,7,3],[0,4,6,2],[1,3,7,5]], BODY_COLOR)
	_build_part("Rudder", [
		Vector3(-0.66, 0.004, 0.01), Vector3(-0.66, -0.004, 0.01),
		Vector3(-0.69, 0.003, 0.01), Vector3(-0.69, -0.003, 0.01),
		Vector3(-0.665, 0.003, 0.15), Vector3(-0.665, -0.003, 0.15),
		Vector3(-0.69, 0.002, 0.15), Vector3(-0.69, -0.002, 0.15),
	], [[0,2,3,1],[4,5,7,6],[0,1,5,4],[2,6,7,3],[0,4,6,2],[1,3,7,5]], ACCENT_COLOR)


## One motor on a tilt pivot. The motor is built with its thrust axis along the
## pivot's local +Y, so pivot.rotation.x = tilt swings it from up (0) to forward
## (PI/2): a +X rotation carries +Y onto +Z, the nose.
func _make_motor(pivot_body: Vector3, ccw: bool) -> Node3D:
	var pivot := Node3D.new()
	pivot.position = _to_local(pivot_body)
	add_child(pivot)
	# Nacelle/mount stub and motor bell.
	var bell := MeshInstance3D.new()
	var cyl := CylinderMesh.new()
	cyl.top_radius = 0.017
	cyl.bottom_radius = 0.019
	cyl.height = 0.045
	cyl.radial_segments = 14
	bell.mesh = cyl
	bell.material_override = _mat(MOTOR_COLOR, 0.7, 0.35, 0.0)
	bell.position = Vector3(0.0, 0.0225, 0.0)
	pivot.add_child(bell)
	# Prop: two blades plus a faint disc so it reads at distance.
	var hub := Node3D.new()
	hub.position = Vector3(0.0, 0.05, 0.0)
	hub.set_meta("dir", 1.0 if ccw else -1.0)
	pivot.add_child(hub)
	var blade := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = Vector3(PROP_R * 2.0, 0.004, 0.022)
	blade.mesh = bm
	blade.material_override = _mat(PROP_COLOR, 0.1, 0.6, 0.0)
	hub.add_child(blade)
	var disc := MeshInstance3D.new()
	var dm := CylinderMesh.new()
	dm.top_radius = PROP_R
	dm.bottom_radius = PROP_R
	dm.height = 0.001
	dm.radial_segments = 24
	disc.mesh = dm
	var dmat := _mat(PROP_COLOR, 0.0, 0.8, 0.0)
	dmat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	dmat.albedo_color.a = 0.22
	disc.material_override = dmat
	hub.add_child(disc)
	_props.append(hub)
	return pivot


# ------------------------------------------------------------------ helpers
## Tapered flat panel from a root section to a tip section, mirrored by `side`.
## Section = [x_leading, x_trailing, |y|, z, thickness].
func _panel(part_name: String, root: Array, tip: Array, side: float, color: Color) -> void:
	var pts: Array = []
	for s in [root, tip]:
		var y: float = float(s[2]) * side
		var z: float = float(s[3])
		var h: float = float(s[4]) * 0.5
		pts.append(Vector3(s[0], y, z + h))   # LE top
		pts.append(Vector3(s[0], y, z - h))   # LE bottom
		pts.append(Vector3(s[1], y, z + h))   # TE top
		pts.append(Vector3(s[1], y, z - h))   # TE bottom
	# 0-3 root, 4-7 tip. Winding is irrelevant for a double-sided material.
	_build_part(part_name, pts,
		[[0,2,6,4],[1,5,7,3],[0,4,5,1],[2,3,7,6],[0,1,3,2],[4,6,7,5]], color)


func _loft(part_name: String, sections: Array, segs: int, color: Color) -> void:
	var pts: Array = []
	for s in sections:
		for k in segs:
			var a := TAU * float(k) / float(segs)
			pts.append(Vector3(s[0], float(s[1]) * cos(a), float(s[3]) + float(s[2]) * sin(a)))
	var faces: Array = []
	for r in sections.size() - 1:
		for k in segs:
			var k2 := (k + 1) % segs
			faces.append([r * segs + k, r * segs + k2, (r + 1) * segs + k2, (r + 1) * segs + k])
	# End caps.
	var first: Array = []
	var last: Array = []
	for k in segs:
		first.append(k)
		last.append((sections.size() - 1) * segs + k)
	faces.append(first)
	faces.append(last)
	_build_part(part_name, pts, faces, color)


func _cylinder_x(part_name: String, x0: float, x1: float, y: float, z: float,
		r: float, color: Color) -> void:
	var mi := MeshInstance3D.new()
	mi.name = part_name
	var cyl := CylinderMesh.new()
	cyl.top_radius = r
	cyl.bottom_radius = r
	cyl.height = absf(x1 - x0)
	cyl.radial_segments = 10
	mi.mesh = cyl
	mi.material_override = _mat(color, 0.85, 0.3, 0.0)
	mi.position = _to_local(Vector3((x0 + x1) * 0.5, y, z))
	mi.rotation.x = PI * 0.5   # cylinder axis +Y -> +Z (body forward)
	add_child(mi)


func _box(part_name: String, centre: Vector3, size_body: Vector3, color: Color) -> void:
	var mi := MeshInstance3D.new()
	mi.name = part_name
	var bm := BoxMesh.new()
	bm.size = _to_local(size_body)
	mi.mesh = bm
	mi.material_override = _mat(color, 0.2, 0.5, 0.08)
	mi.position = _to_local(centre)
	add_child(mi)


func _light(p: Vector3, color: Color) -> void:
	var mi := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = 0.008
	sm.height = 0.016
	mi.mesh = sm
	var m := StandardMaterial3D.new()
	m.albedo_color = color
	m.emission_enabled = true
	m.emission = color
	m.emission_energy_multiplier = 2.5
	mi.material_override = m
	mi.position = _to_local(p)
	add_child(mi)


func _mat(color: Color, metallic: float, roughness: float, emission: float) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = color
	m.metallic = metallic
	m.roughness = roughness
	m.cull_mode = BaseMaterial3D.CULL_DISABLED
	if emission > 0.0:
		m.emission_enabled = true
		m.emission = color
		m.emission_energy_multiplier = emission
	return m


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
			st.add_vertex(local_pts[a])
			st.add_vertex(local_pts[face[i]])
			st.add_vertex(local_pts[face[i + 1]])
	st.generate_normals()
	var mi := MeshInstance3D.new()
	mi.name = part_name
	mi.mesh = st.commit()
	# Same small emission as the fixed-wing: keeps a dark airframe legible in
	# shadow and against a bright sky.
	mi.material_override = _mat(color, 0.2, 0.5, 0.08)
	add_child(mi)
	return mi

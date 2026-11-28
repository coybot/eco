## Power lines of the Baylands scene: the PG&E 115 kV lattice towers along the
## park's west edge and across the north, the steel 60 kV / 12 kV lines, the
## wood distribution poles, and every conductor between them. For a drone at
## this field they are the most dangerous thing there is, so they are drawn
## where they stand and they are SOLID.
##
## Data: assets/baylands/wires.json, made by drone/sim/tools/bake_baylands_wires.py.
##   * Structures: OSM routes and tower/pole nodes (ODbL), each moved to where
##     the 2020 USGS lidar sees it and given its measured top; arm rows from
##     the lidar where seen (else the line's own measured layout); a few
##     towers OSM lacks found in the lidar (src "lidar-chain").
##   * Conductors are INFERRED: the lidar delivery holds no conductor returns.
##     Each hangs from its arm tips as a catenary z(s) with parameter c (m),
##     kept above the CPUC GO 95 ground clearance for its voltage.
##
## Solid: claim() adds every structure (body, head and arms as prisms) with
## env.add_obstacle, so the router's grid avoids them, and every conductor as
## short env.add_line_obstacle segments along its catenary, which the
## fixed-wing / VTOL / quad strike sweep hits.
##
## WIRE_RADIUS (default 0.6 m, `--wire-radius=<m>` to change): the solid
## radius of a conductor. A real conductor is 1-3 cm across, but the sim's
## aircraft are swept as POINTS, and a point crossing a 2 cm wire almost never
## touches it while the real airframe (props, wings: ~1.2 m span) would. So the
## wire is fattened to about the airframe's half-span: a path within 0.6 m of
## the conductor is a strike, which is what it would be in the air.
##
## IPC: env_op "wires" -> towers and conductor polylines (ENU + height above
## NAVD88) for clearance rewards; args {rect: [e0, n0, e1, n1], step: m} to
## crop and resample, or {at: [e, n, z]} for the clearance from one point.
extends Node3D

const DATA := "res://assets/baylands/wires.json"
const WIRE_RADIUS_DEFAULT := 0.6
## Catenary segment length for the solids and the drawn cables (m).
const SEG := 4.0
const CHUNK := 500.0
## Insulator string length by conductor class (m; negative = pin above the arm).
const INSULATOR := {"phase115": 1.5, "phase60": 1.1, "phase12": 0.6, "dist": -0.25}

static var _data: Dictionary = {}
static var _polys: Array = []          # per wire: PackedVector3Array (e, n, z)
static var wire_radius := WIRE_RADIUS_DEFAULT

var env: Node


# ================================================================== data

static func _load() -> Dictionary:
	if not _data.is_empty():
		return _data
	var f := FileAccess.open(DATA, FileAccess.READ)
	if f == null:
		push_warning("[baylands_wires] missing %s (run drone/sim/tools/bake_baylands_wires.py)" % DATA)
		return {}
	var d = JSON.parse_string(f.get_as_text())
	if not d is Dictionary:
		return {}
	_data = d
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--wire-radius="):
			wire_radius = maxf(float(a.get_slice("=", 1)), 0.005)
	_polys.clear()
	for w in _data.get("wires", []):
		_polys.append(catenary(_v3(w["a"]), _v3(w["b"]), float(w["c"]), SEG))
	return _data


static func _v3(a: Array) -> Vector3:
	return Vector3(float(a[0]), float(a[1]), float(a[2]))


## Points (e, n, z) every ~step metres along the catenary from a to b with
## parameter c = H/w: z(s) = z_a + c (cosh((s - x0)/c) - cosh(x0/c)).
static func catenary(a: Vector3, b: Vector3, c: float, step: float) -> PackedVector3Array:
	var out := PackedVector3Array()
	var hv := Vector2(b.x - a.x, b.y - a.y)
	var L := hv.length()
	if L < 0.5:
		out.append(a)
		out.append(b)
		return out
	var dz := b.z - a.z
	var x0 := L * 0.5 - c * _asinh(dz / (2.0 * c * sinh(L / (2.0 * c))))
	var k := maxi(2, ceili(L / step))
	for i in k + 1:
		var s := L * float(i) / float(k)
		var z := a.z + c * (cosh((s - x0) / c) - cosh(x0 / c))
		var xy := Vector2(a.x, a.y) + hv * (s / L)
		out.append(Vector3(xy.x, xy.y, z))
	return out


static func _asinh(x: float) -> float:
	return log(x + sqrt(x * x + 1.0))


# ================================================================= solids

## Before the router's grid is cut: every structure and conductor is solid.
static func claim(env) -> bool:
	var d := _load()
	if d.is_empty():
		return false
	var n_obs := 0
	for s in d["structures"]:
		for p in _prisms(env, s):
			env.add_obstacle(p[0], p[1], p[2])
			n_obs += 1
	var n_seg := 0
	for i in _polys.size():
		var pl: PackedVector3Array = _polys[i]
		for k in pl.size() - 1:
			env.add_line_obstacle(pl[k], pl[k + 1], wire_radius, "wire")
			n_seg += 1
	print("[baylands_wires] solid: %d structures as %d prisms, %d conductors as %d segments, radius %.2f m"
		% [d["structures"].size(), n_obs, _polys.size(), n_seg, wire_radius])
	return true


## [ring, bottom, top] prisms covering one structure: lower body, upper body
## (and peak), and the head (arms + hanging insulators).
static func _prisms(env, s: Dictionary) -> Array:
	var c := Vector2(float(s["e"]), float(s["n"]))
	var g: float = env.ground_height(c.x, c.y)
	var top: float = float(s["top"])
	var yaw := float(s["yaw"])
	var u := Vector2(cos(yaw), sin(yaw))
	var v := Vector2(-u.y, u.x)
	var out := []
	var arms: Array = s["arms"]
	var zlo := top
	var zhi := g
	var reach := 0.0
	for a in arms:
		zlo = minf(zlo, float(a[0]))
		zhi = maxf(zhi, float(a[0]))
		reach = maxf(reach, float(a[1]))
	var ins := absf(float(INSULATOR.get(s.get("cls", "dist"), 0.6)))
	match str(s["type"]):
		"lattice":
			var bw := float(s["base_w"])
			var zw := _waist(g, top, zlo)
			out.append([_rect(c, u, v, bw * 0.5 + 0.2, bw * 0.5 + 0.2), g - 0.5, zw])
			var hw := _head_half(bw) + 0.25
			out.append([_rect(c, u, v, hw, hw), zw, top + 0.3])
			out.append([_rect(c, u, v, 1.6, reach + 0.4), zlo - ins - 0.4, zhi + 1.6])
		"steel_pole":
			out.append([_rect(c, u, v, 0.6, 0.6), g - 0.5, top + 0.3])
			out.append([_rect(c, u, v, 0.9, reach + 0.4), zlo - ins - 0.4, zhi + 0.6])
		_:
			out.append([_rect(c, u, v, 0.6, 0.6), g - 0.5, top + 0.2])
			out.append([_rect(c, u, v, 0.35, reach + 0.35), zlo - 0.8, zhi + 0.45])
	return out


static func _rect(c: Vector2, u: Vector2, v: Vector2, ha: float, hl: float) -> PackedVector2Array:
	return PackedVector2Array([c - u * ha - v * hl, c + u * ha - v * hl, c + u * ha + v * hl, c - u * ha + v * hl])


## Where a lattice tower's tapering body meets its straight head.
static func _waist(g: float, top: float, zlo: float) -> float:
	return clampf(zlo - 2.0, g + 0.35 * (top - g), top - 3.0)


static func _head_half(bw: float) -> float:
	return clampf(0.13 * bw + 0.25, 0.55, 1.1)


# ================================================================ drawing

func _ready() -> void:
	env = get_parent()
	var d := _load()
	if d.is_empty():
		return
	var t0 := Time.get_ticks_msec()
	_build_structures(d)
	_build_wires(d)
	if env.has_method("register_op"):
		env.register_op("wires", _op_wires)
	print("[baylands_wires] drawn %d structures, %d conductors in %d ms"
		% [d["structures"].size(), d["wires"].size(), Time.get_ticks_msec() - t0])


# Mesh accumulator per chunk: positions, normals, colours (a = metalness), indices.
class Acc:
	var p := PackedVector3Array()
	var n := PackedVector3Array()
	var c := PackedColorArray()
	var i := PackedInt32Array()

	func quad(a: Vector3, b: Vector3, cc: Vector3, d: Vector3, nrm: Vector3, col: Color) -> void:
		var k := p.size()
		p.append_array(PackedVector3Array([a, b, cc, d]))
		n.append_array(PackedVector3Array([nrm, nrm, nrm, nrm]))
		c.append_array(PackedColorArray([col, col, col, col]))
		# Godot front faces are clockwise seen from the front.
		if (b - a).cross(cc - a).dot(nrm) > 0.0:
			i.append_array(PackedInt32Array([k, k + 2, k + 1, k, k + 3, k + 2]))
		else:
			i.append_array(PackedInt32Array([k, k + 1, k + 2, k, k + 2, k + 3]))

	## A square bar of width w from a to b (Godot frame).
	func bar(a: Vector3, b: Vector3, w: float, col: Color) -> void:
		var d := b - a
		if d.length() < 1e-3:
			return
		d = d.normalized()
		var up := Vector3.UP if absf(d.y) < 0.95 else Vector3.RIGHT
		var s1 := d.cross(up).normalized() * (w * 0.5)
		var s2 := d.cross(s1).normalized() * (w * 0.5)
		var corners := [s1 + s2, s1 - s2, -s1 - s2, -s1 + s2]
		for k in 4:
			var o1: Vector3 = corners[k]
			var o2: Vector3 = corners[(k + 1) % 4]
			var nrm := (o1 + o2).normalized()
			quad(a + o1, b + o1, b + o2, a + o2, nrm, col)

	## A tapered n-sided tube from a (radius ra) to b (radius rb).
	func tube(a: Vector3, b: Vector3, ra: float, rb: float, sides: int, col: Color, cap := true) -> void:
		var d := (b - a).normalized()
		var up := Vector3.UP if absf(d.y) < 0.95 else Vector3.RIGHT
		var s1 := d.cross(up).normalized()
		var s2 := d.cross(s1).normalized()
		var k0 := p.size()
		for k in sides + 1:
			var ang := TAU * float(k) / float(sides)
			var o := s1 * cos(ang) + s2 * sin(ang)
			p.append(a + o * ra)
			p.append(b + o * rb)
			n.append(o)
			n.append(o)
			c.append(col)
			c.append(col)
		for k in sides:
			var q := k0 + k * 2
			# Winding: outward normals, clockwise from outside.
			i.append_array(PackedInt32Array([q, q + 1, q + 2, q + 1, q + 3, q + 2]))
		if cap:
			var kc := p.size()
			p.append(b)
			n.append(d)
			c.append(col)
			for k in sides + 1:
				var ang := TAU * float(k) / float(sides)
				p.append(b + (s1 * cos(ang) + s2 * sin(ang)) * rb)
				n.append(d)
				c.append(col)
			for k in sides:
				i.append_array(PackedInt32Array([kc, kc + 2 + k, kc + 1 + k]))


const STEEL := Color(0.50, 0.51, 0.50, 1.0)         # weathered galvanised steel
const STEEL_POLE := Color(0.52, 0.53, 0.52, 1.0)    # painted / weathering steel poles
const WOOD := Color(0.34, 0.28, 0.22, 0.0)          # treated pole, sun-greyed
const PORCELAIN := Color(0.70, 0.70, 0.67, 0.15)    # grey insulators
const TRANSFORMER := Color(0.55, 0.58, 0.56, 0.6)


func _gp(c: Vector2, u: Vector2, v: Vector2, a: float, l: float, z: float) -> Vector3:
	var q := c + u * a + v * l
	return Vector3(q.x, z, -q.y)


func _build_structures(d: Dictionary) -> void:
	var chunks := {}
	var idx := 0
	for s in d["structures"]:
		var c := Vector2(float(s["e"]), float(s["n"]))
		var key := Vector2i(floori(c.x / CHUNK), floori(c.y / CHUNK))
		if not chunks.has(key):
			chunks[key] = Acc.new()
		var acc: Acc = chunks[key]
		match str(s["type"]):
			"lattice":
				_lattice(acc, s)
			"steel_pole":
				_steel_pole(acc, s)
			_:
				_wood_pole(acc, s, idx)
		idx += 1
	var mat := _structure_material()
	var root := Node3D.new()
	root.name = "towers"
	add_child(root)
	for key in chunks:
		var acc: Acc = chunks[key]
		var arr := []
		arr.resize(Mesh.ARRAY_MAX)
		arr[Mesh.ARRAY_VERTEX] = acc.p
		arr[Mesh.ARRAY_NORMAL] = acc.n
		arr[Mesh.ARRAY_COLOR] = acc.c
		arr[Mesh.ARRAY_INDEX] = acc.i
		var m := ArrayMesh.new()
		m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
		var mi := MeshInstance3D.new()
		mi.name = "towers_%d_%d" % [key.x, key.y]
		mi.mesh = m
		mi.material_override = mat
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
		root.add_child(mi)


## A double-circuit lattice steel tower: four legs tapering from the measured
## base width to a straight head, X-braced faces, triangular truss arms at
## the measured rows, a shield-wire peak, insulator strings at the arm tips.
func _lattice(acc: Acc, s: Dictionary) -> void:
	var c := Vector2(float(s["e"]), float(s["n"]))
	var g: float = env.ground_height(c.x, c.y)
	var top := float(s["top"])
	var yaw := float(s["yaw"])
	var u := Vector2(cos(yaw), sin(yaw))
	var v := Vector2(-u.y, u.x)
	var arms: Array = s["arms"].duplicate()
	arms.sort_custom(func(x, y): return float(x[0]) > float(y[0]))
	var zhi := float(arms[0][0]) if not arms.is_empty() else top - 2.0
	var zlo := float(arms[-1][0]) if not arms.is_empty() else top - 8.0
	var bw := float(s["base_w"])
	var hb := bw * 0.5
	var zw := _waist(g, top, zlo)
	var hw := _head_half(bw)
	var zh := minf(zhi + 1.3, top - 0.6) if s["shield"] else top
	var tint := STEEL.darkened(fmod(absf(c.x * 0.37 + c.y * 0.11), 0.06))
	# Rings (z, half-width): tapering body panels, then the head at each arm.
	var rings := []
	var npan := clampi(roundi((zw - g) / maxf(hb + hw, 1.5)), 3, 9)
	for k in npan + 1:
		var t := float(k) / float(npan)
		# Panels shorten toward the waist, as real towers' do.
		var tz := 1.0 - pow(1.0 - t, 1.25)
		rings.append([lerpf(g - 0.2, zw, tz), lerpf(hb, hw, tz)])
	for k in range(arms.size() - 1, -1, -1):
		var za := float(arms[k][0])
		if za > float(rings[-1][0]) + 0.6:
			rings.append([za, hw * lerpf(1.0, 0.9, (za - zw) / maxf(zh - zw, 1.0))])
	if zh > float(rings[-1][0]) + 0.6:
		rings.append([zh, hw * 0.85])
	var corner := [Vector2(1, 1), Vector2(-1, 1), Vector2(-1, -1), Vector2(1, -1)]
	var leg_w := clampf(0.035 * bw, 0.12, 0.22)
	for r in rings.size() - 1:
		var z0 := float(rings[r][0])
		var z1 := float(rings[r + 1][0])
		var h0 := float(rings[r][1])
		var h1 := float(rings[r + 1][1])
		for k in 4:
			var a0: Vector2 = corner[k] * h0
			var a1: Vector2 = corner[k] * h1
			var b0: Vector2 = corner[(k + 1) % 4] * h0
			var b1: Vector2 = corner[(k + 1) % 4] * h1
			acc.bar(_gp(c, u, v, a0.x, a0.y, z0), _gp(c, u, v, a1.x, a1.y, z1), leg_w, tint)
			# X bracing on each face; single diagonals in the short head panels.
			acc.bar(_gp(c, u, v, a0.x, a0.y, z0), _gp(c, u, v, b1.x, b1.y, z1), 0.07, tint)
			if z1 - z0 > 1.6 or z0 < zw:
				acc.bar(_gp(c, u, v, b0.x, b0.y, z0), _gp(c, u, v, a1.x, a1.y, z1), 0.07, tint)
			acc.bar(_gp(c, u, v, a1.x, a1.y, z1), _gp(c, u, v, b1.x, b1.y, z1), 0.08, tint)
	# Footings.
	for k in 4:
		var f: Vector2 = corner[k] * hb
		acc.tube(_gp(c, u, v, f.x, f.y, g - 0.3), _gp(c, u, v, f.x, f.y, g + 0.35), 0.45, 0.4, 8,
			Color(0.62, 0.60, 0.56, 0.0))
	# Peak for the shield wire: legs converge onto a short mast.
	if s["shield"]:
		for k in 4:
			var a1: Vector2 = corner[k] * (hw * 0.85)
			acc.bar(_gp(c, u, v, a1.x, a1.y, zh), _gp(c, u, v, 0.0, 0.0, top - 0.15), 0.09, tint)
		acc.bar(_gp(c, u, v, -0.25, 0.0, top - 0.2), _gp(c, u, v, 0.25, 0.0, top - 0.2), 0.08, tint)
	# Arms: triangular trusses both sides, insulators at the tips.
	var ins := float(INSULATOR.get(s.get("cls", "phase115"), 1.5))
	for a in arms:
		var za := float(a[0])
		var reach := float(a[1])
		var hz := hw * lerpf(1.0, 0.9, (za - zw) / maxf(zh - zw, 1.0))
		for side in [-1.0, 1.0]:
			var tip := _gp(c, u, v, 0.0, side * reach, za)
			var r0 := _gp(c, u, v, hz, side * hz, za)
			var r1 := _gp(c, u, v, -hz, side * hz, za)
			var t0 := _gp(c, u, v, 0.0, side * hz, za + 1.2)
			acc.bar(r0, tip, 0.09, tint)
			acc.bar(r1, tip, 0.09, tint)
			acc.bar(t0, tip, 0.08, tint)
			acc.bar(_gp(c, u, v, hz, side * hz, za + 1.2), t0, 0.06, tint)
			acc.bar(_gp(c, u, v, -hz, side * hz, za + 1.2), t0, 0.06, tint)
			var mid := _gp(c, u, v, 0.0, side * lerpf(hz, reach, 0.5), za)
			acc.bar(t0.lerp(tip, 0.5), mid, 0.05, tint)
			_insulator(acc, c, u, v, side * reach, za, ins, bool(s["dead_end"]))


## A tubular steel pole: tapered 12-sided shaft, davit arms, insulators.
func _steel_pole(acc: Acc, s: Dictionary) -> void:
	var c := Vector2(float(s["e"]), float(s["n"]))
	var g: float = env.ground_height(c.x, c.y)
	var top := float(s["top"])
	var yaw := float(s["yaw"])
	var u := Vector2(cos(yaw), sin(yaw))
	var v := Vector2(-u.y, u.x)
	var h := top - g
	var rb := clampf(0.018 * h + 0.12, 0.3, 0.75)
	var rt := rb * 0.45
	acc.tube(_gp(c, u, v, 0, 0, g - 0.3), _gp(c, u, v, 0, 0, top), rb, rt, 12, STEEL_POLE)
	acc.tube(_gp(c, u, v, 0, 0, g - 0.3), _gp(c, u, v, 0, 0, g + 0.5), rb + 0.35, rb + 0.3, 12,
		Color(0.62, 0.60, 0.56, 0.0))
	var ins := float(INSULATOR.get(s.get("cls", "phase60"), 1.1))
	for a in s["arms"]:
		var za := float(a[0])
		var reach := float(a[1])
		var rz := lerpf(rb, rt, (za - g) / maxf(h, 1.0))
		for side in [-1.0, 1.0]:
			var root := _gp(c, u, v, 0, side * rz * 0.8, za - 0.3)
			var tip := _gp(c, u, v, 0, side * reach, za)
			acc.tube(root, tip, 0.13, 0.07, 6, STEEL_POLE, false)
			acc.bar(_gp(c, u, v, 0, side * rz, za - 1.3), _gp(c, u, v, 0, side * reach * 0.6, za - 0.15), 0.07, STEEL_POLE)
			_insulator(acc, c, u, v, side * reach, za, ins, bool(s["dead_end"]))


## A wood distribution pole: crossarm with braces, three pin insulators, and
## a transformer can on every fourth pole.
func _wood_pole(acc: Acc, s: Dictionary, idx: int) -> void:
	var c := Vector2(float(s["e"]), float(s["n"]))
	var g: float = env.ground_height(c.x, c.y)
	var top := float(s["top"])
	var yaw := float(s["yaw"])
	var u := Vector2(cos(yaw), sin(yaw))
	var v := Vector2(-u.y, u.x)
	var wood := WOOD.lightened(fmod(absf(c.x * 0.71 + c.y * 0.29), 0.12))
	acc.tube(_gp(c, u, v, 0, 0, g - 0.3), _gp(c, u, v, 0, 0, top), 0.17, 0.12, 8, wood)
	var arms: Array = s["arms"]
	var za := float(arms[0][0]) if not arms.is_empty() else top - 0.5
	var reach := float(arms[0][1]) if not arms.is_empty() else 1.2
	var half := reach + 0.15
	# Crossarm (8.9 x 11.4 cm fir) on the pole's face, with two flat braces.
	acc.bar(_gp(c, u, v, 0.16, -half, za), _gp(c, u, v, 0.16, half, za), 0.1, wood.darkened(0.08))
	for side in [-1.0, 1.0]:
		acc.bar(_gp(c, u, v, 0.15, 0, za - 0.65), _gp(c, u, v, 0.15, side * 0.6, za - 0.05), 0.04,
			Color(0.45, 0.46, 0.45, 0.8))
	for off in [-reach, 0.35, reach]:
		var b := _gp(c, u, v, 0.16, off, za + 0.05)
		var t := _gp(c, u, v, 0.16, off, za + 0.25)
		acc.tube(b, t, 0.06, 0.045, 8, PORCELAIN)
	if idx % 4 == 1:
		var cb := _gp(c, u, v, -0.42, 0, za - 2.4)
		acc.tube(cb, cb + Vector3(0, 0.85, 0), 0.24, 0.24, 10, TRANSFORMER)
		acc.bar(_gp(c, u, v, -0.17, 0, za - 1.7), _gp(c, u, v, -0.3, 0, za - 1.7), 0.06, TRANSFORMER)


## A suspension string hanging from an arm tip, or (dead-end) two strain
## strings along the line. The conductor attaches at the bottom / the tip.
func _insulator(acc: Acc, c: Vector2, u: Vector2, v: Vector2, l: float, z: float, ins: float, dead: bool) -> void:
	if ins <= 0.0:
		return
	if dead:
		for sgn in [-1.0, 1.0]:
			acc.tube(_gp(c, u, v, 0.0, l, z), _gp(c, u, v, sgn * ins, l, z), 0.07, 0.07, 6, PORCELAIN, false)
	else:
		var a := _gp(c, u, v, 0.0, l, z)
		var b := _gp(c, u, v, 0.0, l, z - ins)
		acc.tube(a, b, 0.05, 0.05, 6, PORCELAIN, false)
		# Sheds: a few discs down the string read as an insulator, not a rod.
		var nd := maxi(2, int(ins / 0.3))
		for k in nd:
			var t := (float(k) + 0.5) / float(nd)
			var q := a.lerp(b, t)
			acc.tube(q + Vector3(0, 0.03, 0), q - Vector3(0, 0.03, 0), 0.12, 0.12, 8, PORCELAIN)


const STRUCT_SHADER := """
shader_type spatial;
render_mode diffuse_burley, specular_schlick_ggx;
#include "res://shaders/thermal.gdshaderinc"
varying vec3 v_world;
void vertex() { v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
	// COLOR.a is metalness: galvanised steel 1, wood and concrete 0.
	ALBEDO = COLOR.rgb;
	METALLIC = COLOR.a * 0.3;
	ROUGHNESS = mix(0.86, 0.62, COLOR.a);
	if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
		// Thin steel follows the air, and its low emissivity mirrors the cold
		// sky; wood is a little sun-heated.
		vec2 th = th_surface(mix(0.7, 0.45, COLOR.a), mix(14.0, 8.0, COLOR.a), 0.2, 0.0);
		th.y = mix(th.y, th_enc(thermal_sky_c), 0.35 * COLOR.a);
		th = th_air(th, length(CAMERA_POSITION_WORLD - v_world));
		TH_OUT(th);
	}
}
"""


func _structure_material() -> ShaderMaterial:
	var sh := Shader.new()
	sh.code = STRUCT_SHADER
	var m := ShaderMaterial.new()
	m.shader = sh
	return m


# ---------------------------------------------------------------- cables

## Conductors as camera-facing ribbons that never get thinner than MIN_PX
## pixels: where the real cable is narrower than that, the ribbon stays one
## pixel wide and its opacity drops to the fraction of the pixel the cable
## covers. A camera does the same (a sub-pixel wire still darkens the pixels
## it crosses), so a far span reads as a faint line, not nothing, and a near
## one as a solid cable.
const WIRE_SHADER := """
shader_type spatial;
render_mode skip_vertex_transform, cull_disabled, blend_mix, depth_draw_never, diffuse_burley, specular_schlick_ggx;
#include "res://shaders/thermal.gdshaderinc"
uniform float min_px = 1.5;
uniform vec3 colour : source_color = vec3(0.47, 0.48, 0.47);
varying float coverage;
varying vec3 v_world;
void vertex() {
	v_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	vec3 p = (MODELVIEW_MATRIX * vec4(VERTEX, 1.0)).xyz;
	vec3 t = normalize((MODELVIEW_MATRIX * vec4(NORMAL, 0.0)).xyz);
	vec3 to_eye = normalize(-p);
	vec3 side = cross(t, to_eye);
	float sl = length(side);
	side = sl > 1e-4 ? side / sl : vec3(1.0, 0.0, 0.0);
	// abs(): a viewport rendered to a texture can carry a Y-flipped projection.
	float px_m = 2.0 * max(-p.z, 0.01) / max(abs(PROJECTION_MATRIX[1][1]) * VIEWPORT_SIZE.y, 1e-3);
	float r = UV.y;
	float half_w = max(r, 0.5 * min_px * px_m);
	coverage = clamp(r / half_w, 0.0, 1.0);
	p += side * half_w * UV.x;
	VERTEX = p;
	NORMAL = normalize(side * UV.x * 0.8 + to_eye);
}
void fragment() {
	ALBEDO = colour;
	METALLIC = 0.6;
	ROUGHNESS = 0.45;
	ALPHA = coverage;
	if (thermal_pass(CAMERA_VISIBLE_LAYERS)) {
		// Current-carrying aluminium runs a few degrees above the air, but
		// mirrors the cold sky; the two roughly cancel at grazing views.
		vec2 th = th_surface(0.3, 6.0, 0.0, 3.0);
		th.y = mix(th.y, th_enc(thermal_sky_c), 0.4);
		th = th_air(th, length(CAMERA_POSITION_WORLD - v_world));
		TH_OUT(th);
		ALPHA = coverage;
	}
}
"""


class WAcc:
	var p := PackedVector3Array()
	var n := PackedVector3Array()
	var uv := PackedVector2Array()
	var i := PackedInt32Array()


func _build_wires(d: Dictionary) -> void:
	var sh := Shader.new()
	sh.code = WIRE_SHADER
	var mat := ShaderMaterial.new()
	mat.shader = sh
	var chunks := {}
	var wires: Array = d["wires"]
	for wi in wires.size():
		var w: Dictionary = wires[wi]
		var pl: PackedVector3Array = _polys[wi]
		var mid := pl[pl.size() / 2]
		var key := Vector2i(floori(mid.x / CHUNK), floori(mid.y / CHUNK))
		if not chunks.has(key):
			chunks[key] = WAcc.new()
		var ch: WAcc = chunks[key]
		var r := float(w["r"])
		var base := ch.p.size()
		for k in pl.size():
			var q := pl[k]
			var t := (pl[mini(k + 1, pl.size() - 1)] - pl[maxi(k - 1, 0)]).normalized()
			var gp := Vector3(q.x, q.z, -q.y)
			var gt := Vector3(t.x, t.z, -t.y)
			ch.p.append(gp)
			ch.p.append(gp)
			ch.n.append(gt)
			ch.n.append(gt)
			ch.uv.append(Vector2(-1.0, r))
			ch.uv.append(Vector2(1.0, r))
		for k in pl.size() - 1:
			var a := base + k * 2
			ch.i.append_array(PackedInt32Array([a, a + 1, a + 2, a + 1, a + 3, a + 2]))
	var root := Node3D.new()
	root.name = "conductors"
	add_child(root)
	for key in chunks:
		var ch: WAcc = chunks[key]
		var arr := []
		arr.resize(Mesh.ARRAY_MAX)
		arr[Mesh.ARRAY_VERTEX] = ch.p
		arr[Mesh.ARRAY_NORMAL] = ch.n
		arr[Mesh.ARRAY_TEX_UV] = ch.uv
		arr[Mesh.ARRAY_INDEX] = ch.i
		var m := ArrayMesh.new()
		m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
		# The ribbons widen in the vertex shader: pad the bounds so a span
		# is not culled while its fattened edge is still on screen.
		var mi := MeshInstance3D.new()
		mi.name = "wires_%d_%d" % [key.x, key.y]
		mi.mesh = m
		mi.material_override = mat
		mi.extra_cull_margin = 4.0
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		root.add_child(mi)


# ==================================================================== IPC

## env_op "wires": {rect?: [e0, n0, e1, n1], step?: m} -> towers and conductor
## polylines; {at: [e, n, z], reach?: m} -> clearance from that point.
func _op_wires(args: Dictionary) -> Dictionary:
	var d := _load()
	if args.has("at"):
		var a: Array = args["at"]
		var p := Vector3(float(a[0]), float(a[1]), float(a[2]))
		var reach := float(args.get("reach", 100.0))
		var best_t := INF
		for s in d["structures"]:
			var dd := Vector2(float(s["e"]) - p.x, float(s["n"]) - p.y).length()
			if p.z <= float(s["top"]) + 1.0:
				best_t = minf(best_t, dd)
		return {"ok": true, "wire_clearance": env.line_clearance(p, reach) if env.has_method("line_clearance") else null,
			"structure_dist_xy": best_t if best_t < INF else null, "radius": wire_radius}
	var rect := Rect2(-1e9, -1e9, 2e9, 2e9)
	if args.has("rect"):
		var r: Array = args["rect"]
		rect = Rect2(float(r[0]), float(r[1]), float(r[2]) - float(r[0]), float(r[3]) - float(r[1]))
	var step := float(args.get("step", 10.0))
	var towers := []
	for s in d["structures"]:
		if rect.has_point(Vector2(float(s["e"]), float(s["n"]))):
			towers.append({"e": s["e"], "n": s["n"], "base": env.ground_height(float(s["e"]), float(s["n"])),
				"top": s["top"], "type": s["type"], "yaw": s["yaw"], "arms": s["arms"], "src": s["src"]})
	var cond := []
	var wires: Array = d["wires"]
	for wi in wires.size():
		var w: Dictionary = wires[wi]
		var a := _v3(w["a"])
		var b := _v3(w["b"])
		if not (rect.has_point(Vector2(a.x, a.y)) or rect.has_point(Vector2(b.x, b.y))):
			continue
		var pts := []
		for q in catenary(a, b, float(w["c"]), step):
			pts.append([snappedf(q.x, 0.01), snappedf(q.y, 0.01), snappedf(q.z, 0.01)])
		cond.append({"kind": w["kind"], "line": w["line"], "points": pts})
	return {"ok": true, "radius": wire_radius, "frame": "ENU m about GEO_DATUM, z m above NAVD88",
		"towers": towers, "conductors": cond}

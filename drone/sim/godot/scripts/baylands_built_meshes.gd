## Procedural meshes for baylands_built: poles and their heads, parked cars,
## park and street furniture. Built in code (no downloaded models), so all of
## it is ours to license (Apache-2.0 with the repo). Units metres, +Y up,
## local +X = the way the thing faces (arm, front of a car).
##
## Vertex COLOR carries a material tag the shaders read (see SHADERS in
## baylands_built.gd): r = base grey/colour, g = part (0 body/paint, 0.25
## glass, 0.5 black trim/rubber, 0.75 lights, 1 contact shadow), b = unused,
## a = 1.
extends RefCounted


class Mesher:
	var pos := PackedVector3Array()
	var nrm := PackedVector3Array()
	var col := PackedColorArray()
	var uv := PackedVector2Array()
	var idx := PackedInt32Array()

	func _push(p: Vector3, n: Vector3, cl: Color) -> void:
		pos.append(p)
		nrm.append(n)
		col.append(cl)
		uv.append(Vector2(p.x + p.z, p.y))

	func tri(a: Vector3, b: Vector3, c: Vector3, cl: Color, n := Vector3.ZERO) -> void:
		if n == Vector3.ZERO:
			n = -(b - a).cross(c - a).normalized()   # Godot: clockwise front faces
		var o := pos.size()
		_push(a, n, cl); _push(b, n, cl); _push(c, n, cl)
		if (b - a).cross(c - a).dot(n) > 0.0:
			idx.append_array(PackedInt32Array([o, o + 2, o + 1]))
		else:
			idx.append_array(PackedInt32Array([o, o + 1, o + 2]))

	## A planar quad a-b-c-d (in order around it), 4 vertices.
	func quad(a: Vector3, b: Vector3, c: Vector3, d: Vector3, cl: Color, n: Vector3) -> void:
		var o := pos.size()
		_push(a, n, cl); _push(b, n, cl); _push(c, n, cl); _push(d, n, cl)
		if (b - a).cross(c - a).dot(n) > 0.0:
			idx.append_array(PackedInt32Array([o, o + 2, o + 1, o, o + 3, o + 2]))
		else:
			idx.append_array(PackedInt32Array([o, o + 1, o + 2, o, o + 2, o + 3]))

	## Another mesher's geometry, transformed.
	func append(m: Mesher, xf: Transform3D) -> void:
		var o := pos.size()
		var nb := xf.basis.orthonormalized()
		for k in m.pos.size():
			var p: Vector3 = xf * m.pos[k]
			pos.append(p)
			nrm.append(nb * m.nrm[k])
			col.append(m.col[k])
			uv.append(Vector2(p.x + p.z, p.y))
		for i in m.idx:
			idx.append(o + i)

	## Axis-aligned box from lo to hi (all six faces unless skip_bottom).
	func box(lo: Vector3, hi: Vector3, cl: Color, skip_bottom := true) -> void:
		var x0 := lo.x; var y0 := lo.y; var z0 := lo.z
		var x1 := hi.x; var y1 := hi.y; var z1 := hi.z
		quad(Vector3(x0, y1, z0), Vector3(x1, y1, z0), Vector3(x1, y1, z1), Vector3(x0, y1, z1), cl, Vector3.UP)
		quad(Vector3(x0, y0, z1), Vector3(x1, y0, z1), Vector3(x1, y1, z1), Vector3(x0, y1, z1), cl, Vector3(0, 0, 1))
		quad(Vector3(x1, y0, z0), Vector3(x0, y0, z0), Vector3(x0, y1, z0), Vector3(x1, y1, z0), cl, Vector3(0, 0, -1))
		quad(Vector3(x1, y0, z1), Vector3(x1, y0, z0), Vector3(x1, y1, z0), Vector3(x1, y1, z1), cl, Vector3(1, 0, 0))
		quad(Vector3(x0, y0, z0), Vector3(x0, y0, z1), Vector3(x0, y1, z1), Vector3(x0, y1, z0), cl, Vector3(-1, 0, 0))
		if not skip_bottom:
			quad(Vector3(x0, y0, z0), Vector3(x0, y0, z1), Vector3(x1, y0, z1), Vector3(x1, y0, z0), cl, Vector3.DOWN)

	## Vertical tapered prism (sides + top), axis Y from y0 to y1.
	func cyl(c: Vector3, r0: float, r1: float, y0: float, y1: float, sides: int, cl: Color, cap := true) -> void:
		for k in sides:
			var a0 := TAU * k / sides
			var a1 := TAU * (k + 1) / sides
			var d0 := Vector3(cos(a0), 0, sin(a0))
			var d1 := Vector3(cos(a1), 0, sin(a1))
			var n := ((d0 + d1) * 0.5).normalized()
			quad(c + d0 * r0 + Vector3(0, y0, 0), c + d1 * r0 + Vector3(0, y0, 0),
				c + d1 * r1 + Vector3(0, y1, 0), c + d0 * r1 + Vector3(0, y1, 0), cl, n)
			if cap:
				tri(c + Vector3(0, y1, 0), c + d0 * r1 + Vector3(0, y1, 0), c + d1 * r1 + Vector3(0, y1, 0), cl, Vector3.UP)

	## A box between two points (a beam), square section s.
	func beam(a: Vector3, b: Vector3, s: float, cl: Color) -> void:
		var d := (b - a)
		var L := d.length()
		if L < 1e-4:
			return
		var f := d / L
		var up := Vector3.UP if absf(f.y) < 0.9 else Vector3.RIGHT
		var r := f.cross(up).normalized() * (s * 0.5)
		var u := r.cross(f).normalized() * (s * 0.5)
		var P := [a - r - u, a + r - u, a + r + u, a - r + u]
		var Q := [b - r - u, b + r - u, b + r + u, b - r + u]
		for k in 4:
			var n: Vector3 = ((P[k] + P[(k + 1) % 4]) * 0.5 - a).normalized()
			quad(P[k], P[(k + 1) % 4], Q[(k + 1) % 4], Q[k], cl, n)

	func arrays() -> Array:
		var arr := []
		arr.resize(Mesh.ARRAY_MAX)
		arr[Mesh.ARRAY_VERTEX] = pos
		arr[Mesh.ARRAY_NORMAL] = nrm
		arr[Mesh.ARRAY_COLOR] = col
		arr[Mesh.ARRAY_TEX_UV] = uv
		arr[Mesh.ARRAY_INDEX] = idx
		return arr

	func mesh() -> ArrayMesh:
		var m := ArrayMesh.new()
		if not pos.is_empty():
			m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays())
		return m


const GALV := Color(0.62, 0.63, 0.62, 1)
const WOOD := Color(0.36, 0.28, 0.20, 1)
const DARK := Color(0.12, 0.12, 0.12, 1)
const WHITE := Color(0.90, 0.90, 0.88, 1)
const CONC := Color(0.70, 0.69, 0.66, 1)
const GREEN := Color(0.20, 0.30, 0.22, 1)


## Pole shafts: unit height (scaled by the instance), per kind.
static func shaft(kind: int) -> Mesher:
	var m := Mesher.new()
	match kind:
		0: m.cyl(Vector3.ZERO, 0.10, 0.06, 0.0, 1.0, 8, GALV)
		1: m.cyl(Vector3.ZERO, 0.16, 0.12, 0.0, 1.0, 8, WOOD)
		2: m.cyl(Vector3.ZERO, 0.32, 0.18, 0.0, 1.0, 10, GALV)
		3: m.cyl(Vector3.ZERO, 0.08, 0.04, 0.0, 1.0, 8, WHITE)
		6: m.cyl(Vector3.ZERO, 0.45, 0.25, 0.0, 1.0, 10, GALV)
		_: m.cyl(Vector3.ZERO, 0.07, 0.06, 0.0, 1.0, 6, GALV)
	return m


## What sits on top of each kind of pole; origin = the pole's top, +X = facing.
static func head(kind: int) -> Mesher:
	var m := Mesher.new()
	match kind:
		0:   # street light: a 2 m davit arm and a cobra-head luminaire
			m.beam(Vector3(0, -0.6, 0), Vector3(0.8, -0.15, 0), 0.07, GALV)
			m.beam(Vector3(0.8, -0.15, 0), Vector3(2.0, -0.05, 0), 0.06, GALV)
			m.box(Vector3(1.75, -0.22, -0.17), Vector3(2.45, -0.04, 0.17), Color(0.55, 0.56, 0.55, 1))
			m.box(Vector3(1.85, -0.25, -0.13), Vector3(2.35, -0.21, 0.13), Color(0.80, 0.80, 0.75, 0.75))
		1:   # utility pole: crossarm with insulators, a transformer can on some
			m.box(Vector3(-0.05, -0.65, -1.2), Vector3(0.05, -0.55, 1.2), WOOD)
			for z in [-1.05, -0.35, 0.35, 1.05]:
				m.cyl(Vector3(0, -0.55, z), 0.05, 0.04, 0.0, 0.18, 6, Color(0.55, 0.55, 0.52, 1))
		2:   # sports-field light: a rack of floodlights facing the pitch
			m.box(Vector3(-0.3, -2.2, -1.6), Vector3(-0.1, 0.0, 1.6), GALV)
			for row in 3:
				for k in 4:
					var z := -1.2 + k * 0.8
					var y := -0.5 - row * 0.7
					m.box(Vector3(-0.1, y - 0.25, z - 0.28), Vector3(0.35, y + 0.25, z + 0.28), Color(0.45, 0.46, 0.45, 1))
					m.box(Vector3(0.35, y - 0.2, z - 0.22), Vector3(0.37, y + 0.2, z + 0.22), Color(0.85, 0.85, 0.80, 0.75))
		3:   # flagpole: ball and a 1.5 x 0.9 m flag
			m.cyl(Vector3(0, 0, 0), 0.07, 0.07, 0.0, 0.14, 6, Color(0.8, 0.7, 0.3, 1))
			var fl := Color(0.75, 0.15, 0.15, 1)
			m.quad(Vector3(0.05, -0.15, 0), Vector3(1.55, -0.15, 0.08), Vector3(1.55, -1.05, 0.08), Vector3(0.05, -1.05, 0), fl, Vector3(0, 0, 1))
			m.quad(Vector3(0.05, -0.15, 0), Vector3(0.05, -1.05, 0), Vector3(1.55, -1.05, 0.08), Vector3(1.55, -0.15, 0.08), fl, Vector3(0, 0, -1))
			m.quad(Vector3(0.05, -0.15, 0.002), Vector3(0.7, -0.15, 0.02), Vector3(0.7, -0.6, 0.02), Vector3(0.05, -0.6, 0.002), Color(0.15, 0.2, 0.45, 1), Vector3(0, 0, 1))
		6:   # mast: antenna panels
			for k in 3:
				var a := TAU * k / 3.0
				var d := Vector3(cos(a), 0, sin(a))
				m.box(d * 0.5 + Vector3(-0.15, -2.4, -0.15), d * 0.5 + Vector3(0.15, -0.2, 0.15), WHITE)
		_:   # sign / signal / camera pole: a small plate
			m.box(Vector3(0.05, -0.9, -0.35), Vector3(0.09, -0.1, 0.35), Color(0.55, 0.56, 0.58, 1))
	return m


## Collision of each pole kind: shaft half-size (m) and the head box
## [forward from the pole, half width, depth below the top] or [].
static func pole_solid(kind: int) -> Array:
	match kind:
		0: return [0.12, [2.5, 0.2, 0.7]]
		1: return [0.18, [0.1, 1.25, 0.8]]
		2: return [0.35, [0.45, 1.7, 2.3]]
		3: return [0.08, [1.6, 0.1, 1.1]]
		6: return [0.75, []]
	return [0.1, []]


# ------------------------------------------------------------------ cars

## Parked-car bodies sharing ONE topology, so a single MultiMesh draws them
## all: VERTEX is the sedan, CUSTOM0/1/2 hold the SUV, pickup and van as
## absolute positions; the car shader picks by INSTANCE_CUSTOM.x.
## [length, width, height, hood_len, rear_len (trunk/bed), belt, cabin_inset,
##  windshield_run, rear_glass_run]
const BODIES := [
	[4.75, 1.84, 1.44, 1.15, 0.95, 0.88, 0.14, 0.75, 0.62],   # sedan
	[4.70, 1.92, 1.72, 1.05, 0.30, 1.02, 0.10, 0.55, 0.18],   # SUV / crossover
	[5.60, 2.00, 1.86, 1.30, 1.85, 1.10, 0.08, 0.45, 0.05],   # pickup
	[5.10, 1.98, 1.78, 0.85, 0.20, 1.00, 0.08, 0.60, 0.10],   # minivan
]


static func _car_body(m: Mesher, b: Array) -> void:
	var L: float = b[0]; var Wd: float = b[1]; var H: float = b[2]
	var hood: float = b[3]; var rear: float = b[4]; var belt: float = b[5]
	var inset: float = b[6]; var ws: float = b[7]; var rg: float = b[8]
	var x0 := -L * 0.5
	var x1 := L * 0.5
	var w := Wd * 0.5
	var y0 := 0.28
	var paint := Color(1, 0, 0, 1)
	var glass := Color(1, 0.25, 0, 1)
	var trim := Color(1, 0.5, 0, 1)
	var lamp := Color(1, 0.75, 0, 1)
	# lower body: rounded a little at the bumpers
	var xb0 := x0 + 0.12
	var xb1 := x1 - 0.12
	var yb := belt - 0.12
	# sides
	m.quad(Vector3(x0, y0, w), Vector3(x1, y0, w), Vector3(x1, belt, w), Vector3(x0, belt, w), paint, Vector3(0, 0, 1))
	m.quad(Vector3(x1, y0, -w), Vector3(x0, y0, -w), Vector3(x0, belt, -w), Vector3(x1, belt, -w), paint, Vector3(0, 0, -1))
	# front and rear faces (lights band)
	m.quad(Vector3(x1, y0, w), Vector3(x1, y0, -w), Vector3(x1, yb, -w), Vector3(x1, yb, w), trim, Vector3(1, 0, 0))
	m.quad(Vector3(x1, yb, w), Vector3(x1, yb, -w), Vector3(xb1, belt, -w), Vector3(xb1, belt, w), lamp, Vector3(0.7, 0.7, 0).normalized())
	m.quad(Vector3(x0, y0, -w), Vector3(x0, y0, w), Vector3(x0, yb, w), Vector3(x0, yb, -w), trim, Vector3(-1, 0, 0))
	m.quad(Vector3(x0, yb, -w), Vector3(x0, yb, w), Vector3(xb0, belt, w), Vector3(xb0, belt, -w), lamp, Vector3(-0.7, 0.7, 0).normalized())
	# hood and trunk/bed tops
	var xh := x1 - hood
	var xr := x0 + rear
	m.quad(Vector3(xh, belt, w), Vector3(xb1, belt, w), Vector3(xb1, belt, -w), Vector3(xh, belt, -w), paint, Vector3.UP)
	m.quad(Vector3(xb0, belt, w), Vector3(xr, belt, w), Vector3(xr, belt, -w), Vector3(xb0, belt, -w), paint, Vector3.UP)
	# cabin: windshield, roof, rear glass, side glass
	var wi := w - inset
	var xw := xh + ws          # top of the windshield
	var xg := xr + rg          # top of the rear glass
	m.quad(Vector3(xh, belt, w), Vector3(xh, belt, -w), Vector3(xw, H, -wi), Vector3(xw, H, wi), glass, Vector3(H - belt, ws, 0).normalized())
	m.quad(Vector3(xg, H, wi), Vector3(xw, H, wi), Vector3(xw, H, -wi), Vector3(xg, H, -wi), paint, Vector3.UP)
	m.quad(Vector3(xr, belt, -w), Vector3(xr, belt, w), Vector3(xg, H, wi), Vector3(xg, H, -wi), glass, Vector3(-(H - belt), maxf(rg, 0.05), 0).normalized())
	m.quad(Vector3(xr, belt, w), Vector3(xh, belt, w), Vector3(xw, H, wi), Vector3(xg, H, wi), glass, Vector3(0, inset, H - belt).normalized())
	m.quad(Vector3(xh, belt, -w), Vector3(xr, belt, -w), Vector3(xg, H, -wi), Vector3(xw, H, -wi), glass, Vector3(0, inset, -(H - belt)).normalized())
	# wheels: one dark block per axle, tyre to tyre
	for wx in [x0 + 0.85 if rear < 1.0 else x0 + 1.1, x1 - 0.95]:
		m.box(Vector3(wx - 0.33, 0.0, -w + 0.02), Vector3(wx + 0.33, 0.62, w - 0.02), trim)
	# contact shadow: parked cars cast no real-time shadow (39k of them
	# would cost a third of the frame); a dark footprint under each reads
	# the same from above
	var sh := Color(1, 1.0, 0, 1)
	m.quad(Vector3(x0 - 0.15, 0.03, w + 0.15), Vector3(x1 + 0.15, 0.03, w + 0.15),
		Vector3(x1 + 0.15, 0.03, -w - 0.15), Vector3(x0 - 0.15, 0.03, -w - 0.15), sh, Vector3.UP)


static func car_mesh() -> ArrayMesh:
	var ms: Array = []
	for b in BODIES:
		var m := Mesher.new()
		_car_body(m, b)
		ms.append(m)
	var base: Mesher = ms[0]
	var arr := base.arrays()
	arr[Mesh.ARRAY_TEX_UV] = null
	for k in 3:
		var other: Mesher = ms[k + 1]
		var c := PackedFloat32Array()
		for i in other.pos.size():
			var p: Vector3 = other.pos[i]
			var n: Vector3 = other.nrm[i]
			# xyz = position; w = the normal packed (octahedral would do; a
			# yaw-only pack is enough here: n.y and the sign of the side)
			c.append_array(PackedFloat32Array([p.x, p.y, p.z, _pack_n(n)]))
		arr[Mesh.ARRAY_CUSTOM0 + k] = c
	var m := ArrayMesh.new()
	m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr, [], {},
		(Mesh.ARRAY_CUSTOM_RGBA_FLOAT << Mesh.ARRAY_FORMAT_CUSTOM0_SHIFT)
		| (Mesh.ARRAY_CUSTOM_RGBA_FLOAT << Mesh.ARRAY_FORMAT_CUSTOM1_SHIFT)
		| (Mesh.ARRAY_CUSTOM_RGBA_FLOAT << Mesh.ARRAY_FORMAT_CUSTOM2_SHIFT))
	return m


## A normal in one float: 8-bit-ish x, y, z in [0, 255] packed base 256.
static func _pack_n(n: Vector3) -> float:
	var q := (n * 0.5 + Vector3(0.5, 0.5, 0.5)) * 63.0
	return float(int(q.x)) + float(int(q.y)) * 64.0 + float(int(q.z)) * 4096.0


# ------------------------------------------------------------- furniture

static func furniture(kind: String) -> Mesher:
	var m := Mesher.new()
	var wood := Color(0.50, 0.38, 0.26, 1)
	var metal := Color(0.30, 0.32, 0.30, 1)
	match kind:
		"bench":
			m.box(Vector3(-0.25, 0.42, -0.9), Vector3(0.20, 0.47, 0.9), wood)
			m.box(Vector3(-0.28, 0.5, -0.9), Vector3(-0.22, 0.85, 0.9), wood)
			for z in [-0.75, 0.75]:
				m.box(Vector3(-0.25, 0.0, z - 0.04), Vector3(0.2, 0.42, z + 0.04), metal)
		"table":
			m.box(Vector3(-0.38, 0.72, -0.95), Vector3(0.38, 0.77, 0.95), wood)
			for x in [-0.68, 0.68]:
				m.box(Vector3(x - 0.13, 0.42, -0.95), Vector3(x + 0.13, 0.46, 0.95), wood)
			for z in [-0.7, 0.7]:
				m.box(Vector3(-0.75, 0.0, z - 0.04), Vector3(0.75, 0.42, z + 0.04), metal)
				m.box(Vector3(-0.05, 0.0, z - 0.04), Vector3(0.05, 0.72, z + 0.04), metal)
		"bbq":
			m.cyl(Vector3.ZERO, 0.06, 0.06, 0.0, 0.8, 6, metal)
			m.box(Vector3(-0.3, 0.8, -0.45), Vector3(0.3, 1.0, 0.45), DARK)
		"bin":
			m.cyl(Vector3.ZERO, 0.30, 0.30, 0.0, 0.95, 8, Color(0.25, 0.35, 0.28, 1))
		"bollard":
			m.cyl(Vector3.ZERO, 0.11, 0.11, 0.0, 0.95, 8, Color(0.75, 0.72, 0.35, 1))
		"gate":
			for z in [-2.0, 2.0]:
				m.cyl(Vector3(0, 0, z), 0.06, 0.06, 0.0, 1.4, 6, GALV)
			m.beam(Vector3(0, 1.3, -2.0), Vector3(0, 1.3, 2.0), 0.05, GALV)
			m.beam(Vector3(0, 0.3, -2.0), Vector3(0, 0.3, 2.0), 0.05, GALV)
			m.beam(Vector3(0, 0.3, -2.0), Vector3(0, 1.3, 2.0), 0.04, GALV)
		"lift_gate":
			m.box(Vector3(-0.2, 0.0, -0.2), Vector3(0.2, 1.0, 0.2), Color(0.8, 0.7, 0.2, 1))
			m.box(Vector3(-0.04, 0.85, 0.2), Vector3(0.04, 0.93, 4.0), WHITE)
		"fountain":
			m.box(Vector3(-0.25, 0.0, -0.25), Vector3(0.25, 0.9, 0.25), CONC)
		"board":
			for z in [-0.6, 0.6]:
				m.box(Vector3(-0.05, 0.0, z - 0.05), Vector3(0.05, 1.9, z + 0.05), wood)
			m.box(Vector3(-0.04, 0.9, -0.75), Vector3(0.04, 1.9, 0.75), Color(0.35, 0.25, 0.18, 1))
		"bike_rack":
			for z in [-0.6, 0.0, 0.6]:
				m.beam(Vector3(-0.4, 0.0, z), Vector3(-0.4, 0.8, z), 0.05, GALV)
				m.beam(Vector3(0.4, 0.0, z), Vector3(0.4, 0.8, z), 0.05, GALV)
				m.beam(Vector3(-0.4, 0.8, z), Vector3(0.4, 0.8, z), 0.05, GALV)
		"fitness":
			for z in [-0.8, 0.8]:
				m.cyl(Vector3(0, 0, z), 0.06, 0.06, 0.0, 2.1, 6, GALV)
			m.beam(Vector3(0, 2.05, -0.8), Vector3(0, 2.05, 0.8), 0.05, GALV)
		"bus_stop":
			for x in [-1.6, 1.6]:
				m.box(Vector3(x - 0.04, 0.0, -0.6), Vector3(x + 0.04, 2.5, -0.52), GALV)
			m.box(Vector3(-1.7, 2.5, -0.9), Vector3(1.7, 2.6, 0.7), Color(0.4, 0.42, 0.42, 1))
			m.box(Vector3(-1.6, 0.4, -0.6), Vector3(1.6, 2.4, -0.58), Color(0.5, 0.6, 0.65, 0.25))
			m.box(Vector3(-1.2, 0.42, -0.5), Vector3(1.2, 0.48, -0.2), metal)
		"signal":
			m.cyl(Vector3.ZERO, 0.16, 0.12, 0.0, 6.6, 8, Color(0.55, 0.56, 0.52, 1))
			m.beam(Vector3(0, 6.3, 0), Vector3(8.0, 6.4, 0), 0.16, Color(0.55, 0.56, 0.52, 1))
			for x in [3.0, 5.5, 7.6]:
				m.box(Vector3(x - 0.18, 5.15, -0.18), Vector3(x + 0.18, 6.3, 0.18), Color(0.15, 0.15, 0.12, 1))
			m.box(Vector3(0.15, 2.4, -0.18), Vector3(0.5, 3.4, 0.18), Color(0.15, 0.15, 0.12, 1))
		"bus_sign":
			m.cyl(Vector3.ZERO, 0.04, 0.04, 0.0, 2.6, 6, GALV)
			m.box(Vector3(-0.02, 2.0, -0.25), Vector3(0.02, 2.6, 0.25), Color(0.85, 0.85, 0.82, 1))
	return m


## Solid boxes for furniture over 2 m: [half x, half z, height] or [].
static func furniture_solid(kind: String) -> Array:
	match kind:
		"bus_stop": return [1.75, 0.9, 2.65]
		"fitness": return [0.15, 0.9, 2.15]
	return []


## Soccer goal: posts, crossbar, the net's frame. Origin at the goal line's
## centre, +X into the field, width along Z.
static func goal(width: float) -> Mesher:
	var m := Mesher.new()
	var h := 2.44 if width > 6.0 else 1.83
	var w := width * 0.5
	m.cyl(Vector3(0, 0, -w), 0.06, 0.06, 0.0, h, 8, WHITE, false)
	m.cyl(Vector3(0, 0, w), 0.06, 0.06, 0.0, h, 8, WHITE, false)
	m.beam(Vector3(0, h, -w), Vector3(0, h, w), 0.12, WHITE)
	m.beam(Vector3(0, h, -w), Vector3(-1.6, 0.0, -w), 0.04, GALV)
	m.beam(Vector3(0, h, w), Vector3(-1.6, 0.0, w), 0.04, GALV)
	m.beam(Vector3(-1.6, 0.0, -w), Vector3(-1.6, 0.0, w), 0.04, GALV)
	var net := Color(0.9, 0.9, 0.9, 0.3)
	m.quad(Vector3(0, h, -w), Vector3(0, h, w), Vector3(-1.6, 0.0, w), Vector3(-1.6, 0.0, -w), net, Vector3(-0.8, 0.6, 0).normalized())
	return m

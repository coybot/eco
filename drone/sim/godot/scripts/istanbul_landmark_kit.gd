## Geometry kit for istanbul_landmarks.gd: a triangle accumulator (Parts)
## whose vertex colour carries the material (albedo in rgb, the MATS index in
## alpha), so every landmark in a 1 km chunk is ONE draw of ONE shader, plus
## the solid primitives the landmarks are made of (prisms, domes, drums,
## frusta, boxes, arcades).
##
## Frame: sim (east, north) in metres; Godot (e, y, -n). Every face is wound
## from its outward normal, so callers only say which way is out.
extends RefCounted

## name: [albedo (sRGB), roughness, metallic, grain, mode, heat [absorb, sun_c, mass, own_c], flood]
## mode: 0 plain, 1 ashlar, 4 hall windows, 5 drum windows, 6 arcade (cut out),
## 7 lead sheets (seams), 8 stone with brick bands, 9 brick courses,
## 10 palace windows, 11 glass. flood: how much it glows under the night floodlights.
const MATS := {
	"stone": [Color(0.80, 0.76, 0.68), 0.85, 0.0, 0.18, 1, [0.5, 18.0, 0.85, 0.0], 0.55],
	"stone_win": [Color(0.80, 0.76, 0.68), 0.85, 0.0, 0.18, 4, [0.5, 18.0, 0.85, 0.0], 0.55],
	"drum_win": [Color(0.80, 0.76, 0.68), 0.85, 0.0, 0.18, 5, [0.5, 18.0, 0.85, 0.0], 0.6],
	"arcade": [Color(0.83, 0.80, 0.73), 0.8, 0.0, 0.14, 6, [0.5, 18.0, 0.85, 0.0], 0.5],
	"lead": [Color(0.54, 0.56, 0.58), 0.62, 0.15, 0.12, 7, [0.35, 22.0, 0.15, -2.0], 0.45],
	"lead_flat": [Color(0.54, 0.55, 0.56), 0.7, 0.12, 0.15, 0, [0.35, 22.0, 0.15, -2.0], 0.25],
	"gold": [Color(0.86, 0.68, 0.30), 0.3, 1.0, 0.05, 0, [0.15, 8.0, 0.1, -4.0], 1.4],
	"marble": [Color(0.90, 0.89, 0.86), 0.4, 0.0, 0.08, 0, [0.35, 14.0, 0.85, 0.0], 0.6],
	"hs_win": [Color(0.83, 0.60, 0.49), 0.85, 0.0, 0.2, 4, [0.55, 20.0, 0.85, 0.0], 0.6],
	"hs": [Color(0.83, 0.60, 0.49), 0.85, 0.0, 0.2, 0, [0.55, 20.0, 0.85, 0.0], 0.6],
	"brick": [Color(0.56, 0.31, 0.23), 0.9, 0.0, 0.25, 9, [0.65, 20.0, 0.8, 0.0], 0.45],
	"banded": [Color(0.74, 0.69, 0.59), 0.9, 0.0, 0.3, 8, [0.6, 20.0, 0.9, 0.0], 0.4],
	"granite": [Color(0.64, 0.47, 0.44), 0.55, 0.0, 0.35, 0, [0.6, 20.0, 0.95, 0.0], 0.6],
	"porphyry": [Color(0.40, 0.21, 0.19), 0.6, 0.0, 0.3, 0, [0.7, 22.0, 0.95, 0.0], 0.5],
	"bronze": [Color(0.27, 0.30, 0.23), 0.5, 0.7, 0.25, 0, [0.6, 18.0, 0.3, 0.0], 0.4],
	"white_win": [Color(0.91, 0.89, 0.84), 0.6, 0.0, 0.1, 10, [0.35, 14.0, 0.8, 0.0], 0.7],
	"white": [Color(0.91, 0.89, 0.84), 0.6, 0.0, 0.1, 0, [0.35, 14.0, 0.8, 0.0], 0.7],
	"tile": [Color(0.62, 0.31, 0.21), 0.8, 0.0, 0.25, 0, [0.7, 24.0, 0.5, 0.0], 0.2],
	"slate": [Color(0.30, 0.32, 0.34), 0.6, 0.1, 0.15, 0, [0.8, 24.0, 0.4, 0.0], 0.15],
	"iron": [Color(0.11, 0.11, 0.11), 0.5, 0.6, 0.0, 0, [0.6, 20.0, 0.2, 0.0], 0.1],
	"garden": [Color(0.33, 0.38, 0.22), 1.0, 0.0, 0.5, 0, [0.5, 8.0, 0.55, -1.0], 0.0],
	"rubble": [Color(0.73, 0.67, 0.57), 0.9, 0.0, 0.35, 1, [0.55, 20.0, 0.9, 0.0], 0.5],
	"rubble_win": [Color(0.73, 0.67, 0.57), 0.9, 0.0, 0.35, 4, [0.55, 20.0, 0.9, 0.0], 0.5],
	"wood": [Color(0.40, 0.29, 0.20), 0.8, 0.0, 0.25, 0, [0.5, 12.0, 0.2, 0.0], 0.3],
	"glass": [Color(0.10, 0.12, 0.14), 0.15, 0.2, 0.0, 11, [0.1, 4.0, 0.5, 0.0], 0.0],
	"clock": [Color(0.94, 0.93, 0.88), 0.4, 0.0, 0.0, 0, [0.3, 10.0, 0.3, 0.0], 1.6],
	"stone_dark": [Color(0.62, 0.59, 0.53), 0.9, 0.0, 0.3, 1, [0.6, 20.0, 0.9, 0.0], 0.45],
	"hs_stone": [Color(0.86, 0.80, 0.70), 0.85, 0.0, 0.18, 1, [0.5, 18.0, 0.85, 0.0], 0.55],
}

static var _cols: Dictionary = {}


static func colour(k: String) -> Color:
	if _cols.has(k):
		return _cols[k]
	var c: Color = (MATS[k] as Array)[0]
	var lin := c.srgb_to_linear()
	lin.a = float(MATS.keys().find(k)) / 255.0
	_cols[k] = lin
	return lin


static func g(p: Vector2, y: float) -> Vector3:
	return Vector3(p.x, y, -p.y)


## Godot direction of a sim direction.
static func gd(d: Vector2) -> Vector3:
	return Vector3(d.x, 0.0, -d.y)


# ====================================================================== Parts
class Parts:
	var v := PackedVector3Array()
	var n := PackedVector3Array()
	var uv := PackedVector2Array()
	var uv2 := PackedVector2Array()
	var col := PackedColorArray()
	## The UV2 written with every vertex (per-surface shader parameters:
	## wall height and bay for windows, bay and spring for arcades).
	var q := Vector2.ZERO
	var _cc := {}

	func _colour(k: String) -> Color:
		if _cc.has(k):
			return _cc[k]
		var c: Color = (MATS[k] as Array)[0]
		var lin := c.srgb_to_linear()
		lin.a = float(MATS.keys().find(k)) / 255.0
		_cc[k] = lin
		return lin

	func _g(p: Vector2, y: float) -> Vector3:
		return Vector3(p.x, y, -p.y)

	func _gd(d: Vector2) -> Vector3:
		return Vector3(d.x, 0.0, -d.y)

	func _area(p: PackedVector2Array) -> float:
		var a := 0.0
		var cnt := p.size()
		for i in cnt:
			var u := p[i]
			var w := p[(i + 1) % cnt]
			a += u.x * w.y - w.x * u.y
		return a * 0.5

	## A flat triangle facing `out` (any vector on its outward side).
	func tri(k: String, a: Vector3, b: Vector3, c: Vector3, out: Vector3,
			ua := Vector2.ZERO, ub := Vector2.ZERO, uc := Vector2.ZERO) -> void:
		var nn := (c - a).cross(b - a)
		if nn.length_squared() < 1e-10:
			return
		if nn.dot(out) < 0.0:
			var t := b
			b = c
			c = t
			var tu := ub
			ub = uc
			uc = tu
			nn = -nn
		nn = nn.normalized()
		var cl: Color = _colour(k)
		v.append(a); v.append(b); v.append(c)
		n.append(nn); n.append(nn); n.append(nn)
		uv.append(ua); uv.append(ub); uv.append(uc)
		uv2.append(q); uv2.append(q); uv2.append(q)
		col.append(cl); col.append(cl); col.append(cl)

	## A triangle with smooth normals (domes, drums); wound to their average.
	func stri(k: String, a: Vector3, b: Vector3, c: Vector3, na: Vector3, nb: Vector3, nc: Vector3,
			ua := Vector2.ZERO, ub := Vector2.ZERO, uc := Vector2.ZERO) -> void:
		var nn := (c - a).cross(b - a)
		if nn.length_squared() < 1e-12:
			return
		if nn.dot(na + nb + nc) < 0.0:
			var t := b
			b = c
			c = t
			var tn := nb
			nb = nc
			nc = tn
			var tu := ub
			ub = uc
			uc = tu
		var cl: Color = _colour(k)
		v.append(a); v.append(b); v.append(c)
		n.append(na); n.append(nb); n.append(nc)
		uv.append(ua); uv.append(ub); uv.append(uc)
		uv2.append(q); uv2.append(q); uv2.append(q)
		col.append(cl); col.append(cl); col.append(cl)

	## Quad a-b-c-d (in order around it) facing `out`. UV: a (u0, v0) b (u1, v0)
	## c (u1, v1) d (u0, v1) from `r` = Rect2(u0, v0, du, dv).
	func quad(k: String, a: Vector3, b: Vector3, c: Vector3, d: Vector3, out: Vector3,
			r := Rect2(0, 0, 1, 1)) -> void:
		var u0 := r.position
		var u1 := r.end
		tri(k, a, b, c, out, u0, Vector2(u1.x, u0.y), u1)
		tri(k, a, c, d, out, u0, u1, Vector2(u0.x, u1.y))

	func append(o: Parts) -> void:
		v.append_array(o.v)
		n.append_array(o.n)
		uv.append_array(o.uv)
		uv2.append_array(o.uv2)
		col.append_array(o.col)

	func mesh(mat: Material) -> ArrayMesh:
		var arr := []
		arr.resize(Mesh.ARRAY_MAX)
		arr[Mesh.ARRAY_VERTEX] = v
		arr[Mesh.ARRAY_NORMAL] = n
		arr[Mesh.ARRAY_TEX_UV] = uv
		arr[Mesh.ARRAY_TEX_UV2] = uv2
		arr[Mesh.ARRAY_COLOR] = col
		var m := ArrayMesh.new()
		if not v.is_empty():
			m.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arr)
			m.surface_set_material(0, mat)
		return m

	# ------------------------------------------------------------ primitives

	## Box centred at `c` (Godot), half extents `h` along basis `b`.
	func box(k: String, c: Vector3, h: Vector3, b := Basis.IDENTITY, top := true, bottom := false) -> void:
		var x := b.x * h.x
		var y := b.y * h.y
		var z := b.z * h.z
		var p := func(sx: float, sy: float, sz: float) -> Vector3: return c + x * sx + y * sy + z * sz
		quad(k, p.call(1, -1, -1), p.call(1, -1, 1), p.call(1, 1, 1), p.call(1, 1, -1), x,
			Rect2(0, 0, 2 * h.z, 2 * h.y))
		quad(k, p.call(-1, -1, 1), p.call(-1, -1, -1), p.call(-1, 1, -1), p.call(-1, 1, 1), -x,
			Rect2(0, 0, 2 * h.z, 2 * h.y))
		quad(k, p.call(-1, -1, 1), p.call(1, -1, 1), p.call(1, 1, 1), p.call(-1, 1, 1), z,
			Rect2(0, 0, 2 * h.x, 2 * h.y))
		quad(k, p.call(1, -1, -1), p.call(-1, -1, -1), p.call(-1, 1, -1), p.call(1, 1, -1), -z,
			Rect2(0, 0, 2 * h.x, 2 * h.y))
		if top:
			quad(k, p.call(-1, 1, -1), p.call(1, 1, -1), p.call(1, 1, 1), p.call(-1, 1, 1), y)
		if bottom:
			quad(k, p.call(-1, -1, -1), p.call(1, -1, -1), p.call(1, -1, 1), p.call(-1, -1, 1), -y)

	## Footprint (sim, CCW or not) extruded from y0 to y1. UV: x metres along
	## the ring, y metres above y0; UV2 = (y1 - y0, bay) for the window modes.
	func prism(k: String, poly: PackedVector2Array, y0: float, y1: float, top_k := "", bay := 0.0) -> void:
		var cnt := poly.size()
		if cnt < 3:
			return
		var c := Vector2.ZERO
		for p in poly:
			c += p
		c /= float(cnt)
		var save := q
		q = Vector2(y1 - y0, bay)
		var along := 0.0
		var ccw := _area(poly) > 0.0
		for i in cnt:
			var a := poly[i]
			var b := poly[(i + 1) % cnt]
			var e := b - a
			var l := e.length()
			if l < 0.02:
				continue
			var o := Vector2(e.y, -e.x) if ccw else Vector2(-e.y, e.x)
			quad(k, _g(a, y0), _g(b, y0), _g(b, y1), _g(a, y1), _gd(o),
				Rect2(along, 0.0, l, y1 - y0))
			along += l
		q = save
		if top_k != "":
			cap(top_k, poly, y1, true)

	## A flat polygon at height y, facing up (or down).
	func cap(k: String, poly: PackedVector2Array, y: float, up := true) -> void:
		var tri_i := Geometry2D.triangulate_polygon(poly)
		var out := Vector3.UP if up else Vector3.DOWN
		for i in range(0, tri_i.size(), 3):
			var a := poly[tri_i[i]]
			var b := poly[tri_i[i + 1]]
			var c := poly[tri_i[i + 2]]
			tri(k, _g(a, y), _g(b, y), _g(c, y), out,
				Vector2(a.x, a.y) * 0.25, Vector2(b.x, b.y) * 0.25, Vector2(c.x, c.y) * 0.25)

	## Frustum (or cylinder) of `seg` sides around `at` (Godot, its base),
	## radius r0 -> r1 over height h; smooth sides. UV: x metres around, y
	## metres up; UV2 = (h, bay). a0..a1 limits it to an arc (radians).
	func frustum(k: String, at: Vector3, r0: float, r1: float, h: float, seg := 12, top := true,
			bay := 0.0, a0 := 0.0, a1 := TAU, rot := 0.0) -> void:
		var save := q
		q = Vector2(h, bay)
		var slope := (r0 - r1) / maxf(h, 0.001)
		var rm := (r0 + r1) * 0.5
		for i in seg:
			var t0 := rot + a0 + (a1 - a0) * float(i) / seg
			var t1 := rot + a0 + (a1 - a0) * float(i + 1) / seg
			var d0 := Vector3(cos(t0), 0, sin(t0))
			var d1 := Vector3(cos(t1), 0, sin(t1))
			var n0 := (d0 + Vector3(0, slope, 0)).normalized()
			var n1 := (d1 + Vector3(0, slope, 0)).normalized()
			var up := Vector3(0, h, 0)
			var x0 := rm * (t0 - rot - a0)
			var x1 := rm * (t1 - rot - a0)
			stri(k, at + d0 * r0, at + d1 * r0, at + d1 * r1 + up, n0, n1, n1,
				Vector2(x0, 0), Vector2(x1, 0), Vector2(x1, h))
			stri(k, at + d0 * r0, at + d1 * r1 + up, at + d0 * r1 + up, n0, n1, n0,
				Vector2(x0, 0), Vector2(x1, h), Vector2(x0, h))
			if top and r1 > 0.0:
				tri(k, at + up, at + d0 * r1 + up, at + d1 * r1 + up, Vector3.UP)
		q = save

	## Dome (ellipsoidal cap) springing at `c` (Godot), radius r, rise h.
	## seg around, rings up. UV.x = ribs * fraction around (lead seams), UV.y 0..1 up.
	## a0..a1: an arc only (a semi-dome is PI wide).
	func dome(k: String, c: Vector3, r: float, h: float, seg := 16, rings := 6, ribs := 0.0,
			a0 := 0.0, a1 := TAU, pointed := 0.0) -> void:
		if ribs <= 0.0:
			ribs = float(seg)
		var pts := []
		var nrm := []
		for j in rings + 1:
			var ph := PI * 0.5 * float(j) / rings
			var row := []
			var nrow := []
			for i in seg + 1:
				var t := a0 + (a1 - a0) * float(i) / seg
				var cr := cos(ph)
				var sr := sin(ph)
				# A slightly pointed (Ottoman) profile: lift the upper rings.
				var lift := pointed * pow(sr, 6.0)
				var p := c + Vector3(r * cr * cos(t), h * sr * (1.0 + lift), r * cr * sin(t))
				row.append(p)
				nrow.append(Vector3(cr * cos(t) / r, sr / h, cr * sin(t) / r).normalized())
			pts.append(row)
			nrm.append(nrow)
		var fu := ribs / float(seg) * (TAU / maxf(a1 - a0, 0.01))
		for j in rings:
			for i in seg:
				var u0 := Vector2(float(i) * fu * (a1 - a0) / TAU, float(j) / rings)
				var u1 := Vector2(float(i + 1) * fu * (a1 - a0) / TAU, float(j + 1) / rings)
				var p00: Vector3 = pts[j][i]
				var p01: Vector3 = pts[j][i + 1]
				var p10: Vector3 = pts[j + 1][i]
				var p11: Vector3 = pts[j + 1][i + 1]
				stri(k, p00, p01, p11, nrm[j][i], nrm[j][i + 1], nrm[j + 1][i + 1],
					u0, Vector2(u1.x, u0.y), u1)
				if j < rings - 1:
					stri(k, p00, p11, p10, nrm[j][i], nrm[j + 1][i + 1], nrm[j + 1][i],
						u0, u1, Vector2(u0.x, u1.y))

	## A wall strip along a sim polyline, both faces, from per-point bottoms to
	## per-point tops (Godot y). UV: x metres along, y metres above the bottom;
	## UV2 = (height, bay).
	func strip(k: String, pts: PackedVector2Array, y0: PackedFloat32Array, y1: PackedFloat32Array,
			both := true, bay := 0.0, side := 1.0) -> void:
		var along := 0.0
		var save := q
		for i in pts.size() - 1:
			var a := pts[i]
			var b := pts[i + 1]
			var e := b - a
			var l := e.length()
			if l < 0.01:
				continue
			var o := _gd(Vector2(e.y, -e.x) * side)
			q = Vector2(y1[i] - y0[i], bay)
			var r := Rect2(along, 0.0, l, y1[i] - y0[i])
			quad(k, _g(a, y0[i]), _g(b, y0[i + 1]), _g(b, y1[i + 1]), _g(a, y1[i]), o, r)
			if both:
				quad(k, _g(a, y0[i]), _g(b, y0[i + 1]), _g(b, y1[i + 1]), _g(a, y1[i]), -o, r)
			along += l
		q = save


# ================================================================== helpers

static func area(p: PackedVector2Array) -> float:
	var a := 0.0
	var cnt := p.size()
	for i in cnt:
		var u := p[i]
		var w := p[(i + 1) % cnt]
		a += u.x * w.y - w.x * u.y
	return a * 0.5


static func centroid(p: PackedVector2Array) -> Vector2:
	var a := 0.0
	var cx := 0.0
	var cy := 0.0
	var cnt := p.size()
	for i in cnt:
		var u := p[i]
		var w := p[(i + 1) % cnt]
		var cr := u.x * w.y - w.x * u.y
		a += cr
		cx += (u.x + w.x) * cr
		cy += (u.y + w.y) * cr
	if absf(a) < 1e-6:
		var s := Vector2.ZERO
		for u in p:
			s += u
		return s / maxf(cnt, 1)
	return Vector2(cx, cy) / (3.0 * a)


## Minimum-area oriented rectangle: [centre, axis (unit), half extents (along, across)].
static func obb(p: PackedVector2Array) -> Array:
	var hull := Geometry2D.convex_hull(p)
	var best := INF
	var out := [centroid(p), Vector2.RIGHT, Vector2.ONE]
	for i in hull.size() - 1:
		var e := hull[i + 1] - hull[i]
		if e.length() < 0.05:
			continue
		var ax := e.normalized()
		var px := Vector2(-ax.y, ax.x)
		var lo := Vector2(INF, INF)
		var hi := Vector2(-INF, -INF)
		for h in hull:
			var u := h.dot(ax)
			var w := h.dot(px)
			lo = Vector2(minf(lo.x, u), minf(lo.y, w))
			hi = Vector2(maxf(hi.x, u), maxf(hi.y, w))
		var a := (hi.x - lo.x) * (hi.y - lo.y)
		if a < best:
			best = a
			var mid := (lo + hi) * 0.5
			out = [ax * mid.x + px * mid.y, ax, (hi - lo) * 0.5]
	return out


## Distance from a point to a polygon's boundary.
static func edge_dist(p: PackedVector2Array, c: Vector2) -> float:
	var best := INF
	for i in p.size():
		var a := p[i]
		var b := p[(i + 1) % p.size()]
		best = minf(best, Geometry2D.get_closest_point_to_segment(c, a, b).distance_to(c))
	return best


## A rectangle in a local frame (centre o, axis ax): u in [u0, u1], v in [v0, v1].
static func rect(o: Vector2, ax: Vector2, u0: float, u1: float, v0: float, v1: float) -> PackedVector2Array:
	var px := Vector2(-ax.y, ax.x)
	return PackedVector2Array([o + ax * u0 + px * v0, o + ax * u1 + px * v0,
		o + ax * u1 + px * v1, o + ax * u0 + px * v1])


## A regular polygon (sim) for collision rings.
static func ngon(c: Vector2, r: float, sides := 8, rot := 0.0) -> PackedVector2Array:
	var out := PackedVector2Array()
	for i in sides:
		var t := rot + TAU * float(i) / sides
		out.append(c + Vector2(cos(t), sin(t)) * r)
	return out


## Deterministic 0..1 hash of a position.
static func hash01(p: Vector2, salt := 0.0) -> float:
	return fposmod(sin(p.x * 12.9898 + p.y * 78.233 + salt * 37.719) * 43758.5453, 1.0)


## Polygon offset by d (outward for d > 0); the largest result, or the input.
static func offset(p: PackedVector2Array, d: float) -> PackedVector2Array:
	var r := Geometry2D.offset_polygon(p, d, Geometry2D.JOIN_MITER)
	var best := p
	var ba := -1.0
	for q in r:
		var a := absf(area(q))
		if a > ba:
			ba = a
			best = q
	return best


## Per-vertex miter inset of a CCW polygon by d (negative = outset); same
## vertex count, so the two rings can be joined face by face.
static func inset(p: PackedVector2Array, d: float) -> PackedVector2Array:
	var out := PackedVector2Array()
	var n := p.size()
	var s := 1.0 if area(p) > 0.0 else -1.0
	for i in n:
		var a := p[(i - 1 + n) % n]
		var b := p[i]
		var c := p[(i + 1) % n]
		var e0 := (b - a).normalized()
		var e1 := (c - b).normalized()
		var n0 := Vector2(-e0.y, e0.x) * s
		var n1 := Vector2(-e1.y, e1.x) * s
		var bis := (n0 + n1)
		if bis.length() < 1e-4:
			bis = n0
		bis = bis.normalized()
		var k := clampf(1.0 / maxf(bis.dot(n0), 0.3), 1.0, 3.0)
		out.append(b + bis * d * k)
	return out

## City walls and the aqueduct for istanbul_landmarks.gd, along the OSM lines
## (features.json walls / towers / aqueducts):
##   * Theodosian Land Walls: the inner wall ~12 m high and ~5 m thick with a
##     tower every ~55 m (alternately square and octagonal, ~18 m, standing
##     proud of the wall), the outer wall ~17 m out (~7 m, smaller towers
##     between the big ones), and the moat ~35 m out (today market gardens
##     between low scarp walls). Limestone with bands of brick, crenellated,
##     the parapet worn ragged.
##   * Blachernae, the sea walls, Galata's Genoese walls, Topkapi's Sur-u
##     Sultani: a single crenellated wall (OSM height, else ~9 m) with towers
##     at OSM's tower points (and every ~70 m along the longer runs).
##   * The Hippodrome's Sphendone: its curved substructure wall.
##   * Valens Aqueduct: two tiers of arches carrying a level channel, 29 m at
##     its tallest over the valley (Wikipedia), along the OSM line.
## All of it is solid (env.add_obstacle) in ~30 m pieces.
extends RefCounted

const Kit = preload("res://scripts/istanbul_landmark_kit.gd")
const Mosque = preload("res://scripts/istanbul_landmark_mosque.gd")

## Roughly the old city's middle: walls face away from it.
const CITY := Vector2(-2000.0, 800.0)


static func _pts(a: Array) -> PackedVector2Array:
	var out := PackedVector2Array()
	for q: Array in a:
		out.append(Vector2(float(q[0]), float(q[1])))
	return out


## A polyline resampled every `step` metres (keeping its corners).
static func resample(pts: PackedVector2Array, step: float) -> PackedVector2Array:
	var out := PackedVector2Array()
	if pts.size() < 2:
		return pts
	out.append(pts[0])
	for i in pts.size() - 1:
		var a := pts[i]
		var b := pts[i + 1]
		var l := a.distance_to(b)
		var n := maxi(1, int(ceil(l / step)))
		for k in range(1, n + 1):
			out.append(a.lerp(b, float(k) / n))
	return out


static func _length(pts: PackedVector2Array) -> float:
	var l := 0.0
	for i in pts.size() - 1:
		l += pts[i].distance_to(pts[i + 1])
	return l


## Offset a polyline sideways by d (positive = to the left of its direction).
static func shift(pts: PackedVector2Array, d: float) -> PackedVector2Array:
	var out := PackedVector2Array()
	var n := pts.size()
	for i in n:
		var a := pts[maxi(i - 1, 0)]
		var b := pts[mini(i + 1, n - 1)]
		var t := (b - a).normalized()
		out.append(pts[i] + Vector2(-t.y, t.x) * d)
	return out


# =================================================================== plan

static func plan(env, p: Dictionary) -> void:
	var feats: Dictionary = p["features"]
	var runs: Array = p["walls"]
	var land_lines: Array = []
	for w: Dictionary in feats.get("walls", []):
		var pts := _pts(w["pts"])
		if pts.size() < 2:
			continue
		var nm := Mosque.fold(String(w.get("name", "")))
		var closed := pts[0].distance_to(pts[pts.size() - 1]) < 0.5 and pts.size() > 3
		var mean := Vector2.ZERO
		for q in pts:
			mean += q
		mean /= pts.size()
		var h: float = float(w["h"]) if w.get("h") != null else 0.0
		if closed:
			var ring := pts.slice(0, pts.size() - 1)
			var a := absf(Kit.area(ring))
			var per := _length(pts)
			if 2.0 * a / per < 25.0 and a > 50.0:
				# A wall mapped as its outline: extrude it.
				if Kit.area(ring) < 0.0:
					ring.reverse()
				var g := _ground_min(env, ring)
				var top := g + (h if h > 0.0 else 12.0)
				env.add_obstacle(ring, g - 1.0, top)
				runs.append({"type": "outline", "ring": ring, "g": g, "top": top})
				continue
		var kind := "wall"
		if nm.contains("hippodrome"):
			kind = "sphendone"
		elif nm.contains("theodosian") or (nm == "" and mean.x < -4600.0):
			kind = "land"
		if kind == "land" and mean.y > 2400.0:
			kind = "wall"
		if h <= 1.5:
			h = {"land": 12.0, "sphendone": 12.0, "wall": 9.0}[kind]
		var mat := "rubble" if nm.contains("sultani") or nm.contains("galata") else "banded"
		var run := {"type": kind, "pts": resample(pts, 9.0), "h": h, "mat": mat}
		_plan_run(env, run)
		runs.append(run)
		if kind == "land":
			land_lines.append(run["pts"])
	# Mapped towers (defensive) away from the land walls' own towers.
	var towers := []
	for t: Dictionary in feats.get("towers", []):
		var ty := String(t.get("type", ""))
		if ty != "defensive" or String(t.get("name", "")) != "":
			continue
		var tp := Vector2(float(t["p"][0]), float(t["p"][1]))
		var near := false
		for ln: PackedVector2Array in land_lines:
			for q in ln:
				if q.distance_to(tp) < 40.0:
					near = true
					break
			if near:
				break
		if near:
			continue
		var g: float = env.ground_height(tp.x, tp.y)
		var th := float(t["h"]) if t.get("h") != null and float(t["h"]) > 6.0 else 14.0
		env.add_obstacle(Kit.ngon(tp, 5.0, 4, PI / 4.0), g - 1.0, g + th)
		towers.append({"p": tp, "g": g, "h": th})
	runs.append({"type": "towers", "list": towers})
	# Aqueducts.
	for aq: Dictionary in feats.get("aqueducts", []):
		var pts := _pts(aq["pts"])
		if pts.size() < 2:
			continue
		var named := Mosque.fold(String(aq.get("name", ""))).contains("valens")
		var run := {"type": "aqueduct", "pts": resample(pts, 8.0), "h": float(aq["h"]) if aq.get("h") != null else 0.0,
			"valens": named}
		_plan_aqueduct(env, run)
		runs.append(run)


static func _ground_min(env, ring: PackedVector2Array) -> float:
	var g := INF
	for q in ring:
		g = minf(g, env.ground_height(q.x, q.y))
	return g


## Outward unit normal of segment a-b (away from the city).
static func _out(a: Vector2, b: Vector2) -> Vector2:
	var e := (b - a).normalized()
	var n := Vector2(e.y, -e.x)
	return n if ((a + b) * 0.5 - CITY).dot(n) > 0.0 else -n


static func _plan_run(env, run: Dictionary) -> void:
	var pts: PackedVector2Array = run["pts"]
	var kind: String = run["type"]
	var h: float = run["h"]
	var thick := 5.0 if kind == "land" else (4.0 if kind == "sphendone" else 3.0)
	run["thick"] = thick
	# Ground and the worn parapet height at each point.
	var gs := PackedFloat32Array()
	var tops := PackedFloat32Array()
	for q in pts:
		var g: float = env.ground_height(q.x, q.y)
		gs.append(g)
		var wear := 0.0
		if kind == "land":
			wear = -2.2 * smoothstep(0.55, 0.95, Kit.hash01(Vector2(floorf(q.x / 40.0), floorf(q.y / 40.0)), 1.0))
		tops.append(g + h + wear)
	if kind == "sphendone":
		# The Sphendone holds the Hippodrome up: level with its top, tall
		# where the ground falls away.
		var gmax := -INF
		for g in gs:
			gmax = maxf(gmax, g)
		for k in tops.size():
			tops[k] = maxf(gmax + 1.5, gs[k] + 3.0)
	run["g"] = gs
	run["top"] = tops
	_solids_along(env, pts, gs, tops, thick)
	# Towers: every ~55 m on the land walls (~70 m elsewhere, longer runs only).
	var towers := []
	var every := 55.0 if kind == "land" else 70.0
	var total := _length(pts)
	if kind != "sphendone" and (kind == "land" or total > 150.0):
		var along := every * 0.5
		var acc := 0.0
		for i in pts.size() - 1:
			var a := pts[i]
			var b := pts[i + 1]
			var l := a.distance_to(b)
			while along <= acc + l and l > 0.01:
				var t := (along - acc) / l
				var tp := a.lerp(b, t)
				var o := _out(a, b)
				var sz := 5.0 if kind == "land" else 4.0
				var tc := tp + o * (sz - thick * 0.3)
				var g: float = env.ground_height(tc.x, tc.y)
				var th := h + (6.5 if kind == "land" else 4.0)
				var octo := kind == "land" and Kit.hash01(tp, 4.0) < 0.3
				var ruin := kind == "land" and Kit.hash01(tp, 5.0) < 0.18
				if ruin:
					th -= 4.0
				towers.append({"p": tc, "dir": (b - a) / l, "g": g, "h": th, "s": sz, "oct": octo})
				env.add_obstacle(Kit.rect(tc, (b - a) / l, -sz, sz, -sz, sz), g - 1.0, g + th + 1.0)
				along += every
			acc += l
	run["towers"] = towers
	if kind == "land":
		# Outer wall ~17 m out, moat ~35 m out (south of Blachernae only).
		var side := 1.0
		if pts.size() >= 2:
			var e := (pts[1] - pts[0]).normalized()
			side = 1.0 if Vector2(-e.y, e.x).dot(_out(pts[0], pts[1])) > 0.0 else -1.0
		var outer := shift(pts, 17.0 * side)
		var og := PackedFloat32Array()
		var otop := PackedFloat32Array()
		for q in outer:
			var g: float = env.ground_height(q.x, q.y)
			og.append(g)
			otop.append(g + 7.0 - 1.5 * smoothstep(0.6, 0.95, Kit.hash01(Vector2(floorf(q.x / 30.0), floorf(q.y / 30.0)), 2.0)))
		run["outer"] = outer
		run["og"] = og
		run["otop"] = otop
		_solids_along(env, outer, og, otop, 2.2)
		var mo0 := shift(pts, 27.0 * side)
		var mo1 := shift(pts, 45.0 * side)
		run["moat"] = [mo0, mo1]
		# Outer towers, between the inner ones.
		var ot := []
		for k in range(0, outer.size() - 1, 6):
			var a := outer[k]
			var b := outer[k + 1]
			if a.distance_to(b) < 0.5:
				continue
			var o := _out(a, b)
			var tc := a + o * 2.5
			var g: float = env.ground_height(tc.x, tc.y)
			ot.append({"p": tc, "dir": (b - a).normalized(), "g": g, "h": 10.0, "s": 3.2, "oct": false})
			env.add_obstacle(Kit.rect(tc, (b - a).normalized(), -3.2, 3.2, -3.2, 3.2), g - 1.0, g + 11.0)
		run["otowers"] = ot


## Obstacles along a wall line, in ~30 m pieces.
static func _solids_along(env, pts: PackedVector2Array, gs: PackedFloat32Array, tops: PackedFloat32Array, thick: float) -> void:
	var i := 0
	while i < pts.size() - 1:
		var j := mini(i + 3, pts.size() - 1)
		var a := pts[i]
		var b := pts[j]
		if a.distance_to(b) > 0.5:
			var e := (b - a).normalized()
			var lo := INF
			var hi := -INF
			for k in range(i, j + 1):
				lo = minf(lo, gs[k])
				hi = maxf(hi, tops[k])
			var ring := Kit.rect(a, e, -0.5, a.distance_to(b) + 0.5, -thick * 0.5, thick * 0.5)
			env.add_obstacle(ring, lo - 1.0, hi + 0.8)
		i = j


static func _plan_aqueduct(env, run: Dictionary) -> void:
	var pts: PackedVector2Array = run["pts"]
	var gs := PackedFloat32Array()
	var gmin := INF
	var gmax := -INF
	for q in pts:
		var g: float = env.ground_height(q.x, q.y)
		gs.append(g)
		gmin = minf(gmin, g)
		gmax = maxf(gmax, g)
	var h: float = run["h"]
	if h <= 0.0:
		h = 10.0
	# The channel runs level: its top is the tallest point's ground + h.
	var top := gmin + h
	run["g"] = gs
	run["level"] = top
	var tops := PackedFloat32Array()
	for g in gs:
		tops.append(maxf(top, g + 3.0))
	run["top"] = tops
	_solids_along(env, pts, gs, tops, 6.2)


# =================================================================== draw

static func draw(lm, plan: Dictionary) -> void:
	for run: Dictionary in plan["walls"]:
		match run["type"]:
			"land", "wall", "sphendone":
				_wall(lm, run)
			"outline":
				var ring: PackedVector2Array = run["ring"]
				var P: Kit.Parts = lm.parts(Kit.centroid(ring))
				P.prism("banded", ring, float(run["g"]) - 1.0, run["top"], "stone_dark")
				lm.count("wall_outlines")
			"towers":
				for t: Dictionary in run["list"]:
					var tp: Vector2 = t["p"]
					var P: Kit.Parts = lm.parts(tp)
					_tower(lm, P, {"p": tp, "dir": Vector2.RIGHT, "g": t["g"], "h": t["h"], "s": 4.5, "oct": false}, "banded")
				lm.count("towers", run["list"].size())
			"aqueduct":
				_aqueduct(lm, run)


static func _wall(lm, run: Dictionary) -> void:
	var pts: PackedVector2Array = run["pts"]
	var kind: String = run["type"]
	var gs: PackedFloat32Array = run["g"]
	var tops: PackedFloat32Array = run["top"]
	var th: float = run["thick"]
	var mat: String = run.get("mat", "banded")
	_slab(lm, pts, gs, tops, th, mat, kind != "sphendone")
	for t: Dictionary in run.get("towers", []):
		_tower(lm, lm.parts(t["p"]), t, mat)
	if run.has("outer"):
		_slab(lm, run["outer"], run["og"], run["otop"], 2.2, mat, true)
		for t: Dictionary in run.get("otowers", []):
			_tower(lm, lm.parts(t["p"]), t, mat)
		var mo: Array = run["moat"]
		_moat(lm, mo[0], mo[1])
	# Floodlit every ~60 m.
	var acc := 0.0
	for i in pts.size() - 1:
		acc += pts[i].distance_to(pts[i + 1])
		if acc > 60.0:
			acc = 0.0
			lm.add_flood(Kit.g(pts[i], gs[i]), 30.0, tops[i] - gs[i], "wall")
	lm.count("wall_m", int(_length(pts)))


## A thick wall along pts: both faces, the walkway, merlons on its outer lip.
static func _slab(lm, pts: PackedVector2Array, gs: PackedFloat32Array, tops: PackedFloat32Array,
		th: float, mat: String, merlons: bool) -> void:
	if pts.size() < 2:
		return
	var a := shift(pts, th * 0.5)
	var b := shift(pts, -th * 0.5)
	var lo := PackedFloat32Array()
	for g in gs:
		lo.append(g - 1.5)
	var P: Kit.Parts = lm.parts(pts[pts.size() / 2])
	# Left face points left, right face right: strip() faces +perp with side.
	P.strip(mat, a, lo, tops, false, 0.0, -1.0)
	P.strip(mat, b, lo, tops, false, 0.0, 1.0)
	for i in pts.size() - 1:
		P.quad("stone_dark", Kit.g(a[i], tops[i]), Kit.g(a[i + 1], tops[i + 1]),
			Kit.g(b[i + 1], tops[i + 1]), Kit.g(b[i], tops[i]), Vector3.UP)
	# End caps.
	for k: int in [0, pts.size() - 1]:
		P.quad(mat, Kit.g(a[k], lo[k]), Kit.g(b[k], lo[k]), Kit.g(b[k], tops[k]), Kit.g(a[k], tops[k]),
			Kit.gd(pts[k] - pts[1 if k == 0 else k - 1]))
	if not merlons:
		return
	# Merlons along the outward lip, one every 2.4 m.
	var acc := 0.0
	for i in pts.size() - 1:
		var p0 := pts[i]
		var p1 := pts[i + 1]
		var l := p0.distance_to(p1)
		if l < 0.1:
			continue
		var e := (p1 - p0) / l
		var o := _out(p0, p1)
		var t := acc
		while t < l:
			var f := t / l
			var q := p0.lerp(p1, f) + o * (th * 0.5 - 0.35)
			var y := lerpf(tops[i], tops[i + 1], f)
			var b2 := Basis(Vector3.UP, e.angle()) * Basis.from_scale(Vector3(1.25, 1.5, 0.7))
			lm.inst("merlon", Transform3D(b2, Kit.g(q, y)))
			t += 2.4
		acc = t - l


## A wall tower standing proud of the wall: square or octagonal, merlons,
## arrow-slit windows (the drum-window pattern), a stone top.
static func _tower(lm, P: Kit.Parts, t: Dictionary, mat: String) -> void:
	var tp: Vector2 = t["p"]
	var g: float = t["g"]
	var h: float = t["h"]
	var s: float = t["s"]
	var d: Vector2 = t["dir"]
	var y0 := g - 1.5
	if t["oct"]:
		var r := s / cos(PI / 8.0)
		P.frustum(mat, Kit.g(tp, y0), r, r * 0.97, h + 1.5, 8, false, 0.0, 0.0, TAU, -d.angle() + PI / 8.0)
		P.frustum("stone_dark", Kit.g(tp, g + h), r * 0.97, r * 0.97, 0.01, 8, true, 0.0, 0.0, TAU, -d.angle() + PI / 8.0)
	else:
		var ring := Kit.rect(tp, d, -s, s, -s, s)
		P.prism(mat, ring, y0, g + h, "stone_dark")
	# Merlons round the top.
	var n := 4
	for k in 4:
		var e := d.rotated(PI * 0.5 * k)
		var o := Vector2(e.y, -e.x)
		for j in n:
			var f := (float(j) + 0.5) / n * 2.0 - 1.0
			var q := tp + o * (s - 0.35) + e * f * (s - 0.6)
			var b := Basis(Vector3.UP, e.angle()) * Basis.from_scale(Vector3(1.2, 1.4, 0.7))
			lm.inst("merlon", Transform3D(b, Kit.g(q, g + h)))


## The moat: a garden floor between two low scarp walls.
static func _moat(lm, a: PackedVector2Array, b: PackedVector2Array) -> void:
	var P: Kit.Parts = lm.parts(a[a.size() / 2])
	for i in a.size() - 1:
		var ya: float = lm.env.ground_height(a[i].x, a[i].y)
		var ya1: float = lm.env.ground_height(a[i + 1].x, a[i + 1].y)
		var yb: float = lm.env.ground_height(b[i].x, b[i].y)
		var yb1: float = lm.env.ground_height(b[i + 1].x, b[i + 1].y)
		if lm.env.is_water(a[i].x, a[i].y) or lm.env.is_water(b[i].x, b[i].y):
			continue
		P.quad("garden", Kit.g(a[i], ya + 0.35), Kit.g(a[i + 1], ya1 + 0.35),
			Kit.g(b[i + 1], yb1 + 0.35), Kit.g(b[i], yb + 0.35), Vector3.UP)
	var la := PackedFloat32Array()
	var ha := PackedFloat32Array()
	for q in a:
		var g: float = lm.env.ground_height(q.x, q.y)
		la.append(g - 1.0)
		ha.append(g + 1.8)
	P.strip("stone_dark", a, la, ha, true)
	var lb := PackedFloat32Array()
	var hb := PackedFloat32Array()
	for q in b:
		var g: float = lm.env.ground_height(q.x, q.y)
		lb.append(g - 1.0)
		hb.append(g + 1.2)
	P.strip("stone_dark", b, lb, hb, true)


## The aqueduct: bays of two arch tiers on piers, real arch soffits, the
## channel on top.
static func _aqueduct(lm, run: Dictionary) -> void:
	var pts: PackedVector2Array = run["pts"]
	var gs: PackedFloat32Array = run["g"]
	var level: float = run["level"]
	var big: bool = run["valens"]
	var w0 := 6.6 if big else 3.0
	var w1 := 5.0 if big else 3.0
	var bays := 0
	for i in pts.size() - 1:
		var a := pts[i]
		var b := pts[i + 1]
		var l := a.distance_to(b)
		if l < 1.0:
			continue
		var e := (b - a) / l
		var g := minf(gs[i], gs[i + 1])
		var top := maxf(level, maxf(gs[i], gs[i + 1]) + 3.0)
		var H := top - g
		var P: Kit.Parts = lm.parts(a)
		var mid := a.lerp(b, 0.5)
		if H < 7.0 or not big:
			_bay(P, a, b, e, g - 1.0, top - 1.2, w0, l * 0.62, "rubble")
		else:
			var t1 := g + H * 0.52
			_bay(P, a, b, e, g - 1.0, t1, w0, l * 0.55, "rubble")
			P.box("stone_dark", Kit.g(mid, t1 + 0.3), Vector3(l * 0.5, 0.3, w0 * 0.5 + 0.2), Basis(Vector3.UP, e.angle()))
			_bay(P, a, b, e, t1 + 0.6, top - 1.2, w1, l * 0.5, "rubble")
		# Channel and its coping.
		P.box("stone_dark", Kit.g(mid, top - 0.6), Vector3(l * 0.5 + 0.02, 0.6, (w1 if big else w0) * 0.5 + 0.15), Basis(Vector3.UP, e.angle()))
		bays += 1
		if i % 8 == 0:
			lm.add_flood(Kit.g(a, g), 20.0, H, "wall")
	lm.count("aqueduct_bays", bays)


## One bay: a pier at each end (half of it), an arch of span `span` between,
## spandrels to y1, w thick.
static func _bay(P: Kit.Parts, a: Vector2, b: Vector2, e: Vector2, y0: float, y1: float, w: float,
		span: float, mat: String) -> void:
	var l := a.distance_to(b)
	var mid := a.lerp(b, 0.5)
	var n := Vector2(-e.y, e.x)
	var r := span * 0.5
	var spring := y1 - r - minf(1.6, (y1 - y0) * 0.15)
	if spring < y0 + 1.0:
		spring = y0 + 1.0
		r = minf(r, y1 - spring - 0.5)
	var sides: Array = [w * 0.5, -w * 0.5]
	for sd: float in sides:
		var o := Kit.gd(n * signf(sd))
		var off := n * sd
		# Spandrel face: the bay's rectangle minus the arch opening.
		var poly := PackedVector2Array()
		poly.append(Vector2(-l * 0.5, y0))
		poly.append(Vector2(-r, y0))
		poly.append(Vector2(-r, spring))
		for k in range(1, 8):
			var t := PI - PI * k / 8.0
			poly.append(Vector2(cos(t) * r, spring + sin(t) * r))
		poly.append(Vector2(r, spring))
		poly.append(Vector2(r, y0))
		poly.append(Vector2(l * 0.5, y0))
		poly.append(Vector2(l * 0.5, y1))
		poly.append(Vector2(-l * 0.5, y1))
		var tri := Geometry2D.triangulate_polygon(poly)
		var to3 := func(q: Vector2) -> Vector3: return Kit.g(mid + e * q.x + off, q.y)
		P.q = Vector2.ZERO
		for k in range(0, tri.size(), 3):
			var p0: Vector2 = poly[tri[k]]
			var p1: Vector2 = poly[tri[k + 1]]
			var p2: Vector2 = poly[tri[k + 2]]
			P.tri(mat, to3.call(p0), to3.call(p1), to3.call(p2), o,
				Vector2(p0.x, p0.y - y0), Vector2(p1.x, p1.y - y0), Vector2(p2.x, p2.y - y0))
	# Soffit (inside of the arch) and the pier jambs.
	var hw := w * 0.5
	for k in 8:
		var t0 := PI - PI * k / 8.0
		var t1 := PI - PI * (k + 1) / 8.0
		var q0 := mid + e * cos(t0) * r
		var q1 := mid + e * cos(t1) * r
		var y_0 := spring + sin(t0) * r
		var y_1 := spring + sin(t1) * r
		var inward := Vector3(-cos((t0 + t1) * 0.5) * e.x, -sin((t0 + t1) * 0.5), cos((t0 + t1) * 0.5) * e.y)
		P.quad(mat, Kit.g(q0 + n * hw, y_0), Kit.g(q1 + n * hw, y_1), Kit.g(q1 - n * hw, y_1), Kit.g(q0 - n * hw, y_0), inward)
	for sd: float in [-1.0, 1.0]:
		var q := mid + e * sd * r
		P.quad(mat, Kit.g(q + n * hw, y0), Kit.g(q - n * hw, y0), Kit.g(q - n * hw, spring), Kit.g(q + n * hw, spring),
			Kit.gd(-e * sd))
	# Pier ends (seen where the line turns or ends).
	for sd: float in [-1.0, 1.0]:
		var q := mid + e * sd * l * 0.5
		P.quad(mat, Kit.g(q + n * hw, y0), Kit.g(q - n * hw, y0), Kit.g(q - n * hw, y1), Kit.g(q + n * hw, y1),
			Kit.gd(e * sd))


## Instanced unit meshes this module uses.
static func unit(nm: String, P: Kit.Parts) -> void:
	match nm:
		"merlon":
			P.box("stone_dark", Vector3(0, 0.5, 0), Vector3(0.5, 0.5, 0.5))

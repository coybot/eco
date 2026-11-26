## Named landmarks for istanbul_landmarks.gd, at their real (OSM) positions
## with published dimensions (Wikipedia/Wikidata facts; where no figure is
## published the proportions are read from photographs and marked ~):
##   Hagia Sophia: dome 31.2 m across, crown 55.6 m; two great semi-domes,
##     exedrae, apse, buttresses, narthex; four differing minarets (the red
##     brick one of Mehmed II at the SE, Bayezid II's slender NE one, Selim
##     II's / Murad III's twin west ones by Sinan).
##   Galata Tower: 62.6 m (66.9 m to the finial), 16.45 m across, conical cap.
##   Maiden's Tower (Kiz Kulesi): on its islet, ~23 m (~27 m to the finial).
##   Beyazit Tower: 85 m fire-watch tower.
##   Column of Constantine (Cemberlitas): 34.8 m, porphyry drums, iron hoops.
##   Hippodrome: Obelisk of Theodosius (18.5 m + pedestal, ~25 m), Walled
##     Obelisk (32 m), Serpent Column (5.5 m bronze).
##   Topkapi: Tower of Justice (~41 m), Gate of Salutation's twin towers,
##     pavilions with wide-eaved lead roofs, the kitchens' 10 domes and two
##     rows of 20 chimneys.
##   Dolmabahce Palace (~600 m of white facade, the Ceremonial Hall's dome,
##     36 m), its 27 m clock tower; Haydarpasa Station with its corner towers.
extends RefCounted

const Kit = preload("res://scripts/istanbul_landmark_kit.gd")
const Mosque = preload("res://scripts/istanbul_landmark_mosque.gd")


static func _names(env) -> Dictionary:
	var out := {}
	var names: Array = env.building_meta.get("names", [])
	for i in env.b_first.size():
		var k: int = env.b_name[i]
		if k < 0 or k >= names.size():
			continue
		var nm := Mosque.fold(String(names[k]))
		if not out.has(nm):
			out[nm] = []
		out[nm].append(i)
	return out


static func _first(by: Dictionary, nm: String, kind := -1, env = null) -> int:
	for i: int in by.get(nm, []):
		if kind < 0 or int(env.b_kind[i]) == kind:
			return i
	return -1


static func _landmark(feats: Dictionary, nm: String) -> Dictionary:
	for l: Dictionary in feats.get("landmarks", []):
		if Mosque.fold(String(l.get("name", ""))) == nm:
			return l
	return {}


static func _ring(a: Array) -> PackedVector2Array:
	var out := PackedVector2Array()
	for q: Array in a:
		out.append(Vector2(float(q[0]), float(q[1])))
	return out


## Buildings whose centre lies inside poly.
static func _inside(env, poly: PackedVector2Array, min_area := 0.0) -> Array:
	var bb := Rect2(poly[0], Vector2.ZERO)
	for p in poly:
		bb = bb.expand(p)
	var out := []
	for i in env.buildings_in(bb):
		if env.b_skip[i] != 0 or int(env.b_kind[i]) == 250:
			continue
		if Geometry2D.is_point_in_polygon(env.b_centre[i], poly) and absf(Kit.area(env.footprint(i))) >= min_area:
			out.append(i)
	return out


static func _claim(env, i: int, top_rel: float) -> void:
	env.b_skip[i] = 1
	var c: Vector2 = env.b_centre[i]
	var g: float = env.ground_height(c.x, c.y)
	env.b_top[i] = g + top_rel
	env.b_h[i] = top_rel


static func _solid(env, ring: PackedVector2Array, lo: float, hi: float) -> void:
	if hi > lo + 0.3 and ring.size() >= 3:
		env.add_obstacle(ring, lo, hi)


# =================================================================== plan

static func plan(env, p: Dictionary) -> void:
	var feats: Dictionary = p["features"]
	var by := _names(env)
	var sites: Array = p["sites"]
	for f: Callable in [_plan_hagia_sophia, _plan_galata, _plan_maiden, _plan_beyazit, _plan_columns,
			_plan_topkapi, _plan_dolmabahce, _plan_haydarpasa]:
		var s = f.call(env, feats, by)
		if s is Dictionary and not s.is_empty():
			sites.append(s)
		elif s is Array:
			sites.append_array(s)


static func _plan_hagia_sophia(env, feats: Dictionary, by: Dictionary) -> Dictionary:
	var i := _first(by, "hagia sophia", 2, env)
	if i < 0:
		return {}
	var fp: PackedVector2Array = env.footprint(i)
	var c := Kit.centroid(fp)
	var ax := Vector2(0.84, -0.542).normalized()
	# The OSM 3D parts: the main dome (h > 50) and the semi-domes (h ~39).
	var semis := []
	for bp: Dictionary in feats.get("building_parts", []):
		if String(bp.get("roof", "")) != "dome" or bp.get("h") == null:
			continue
		var r := _ring(bp["ring"])
		var pc := Kit.centroid(r)
		if pc.distance_to(c) > 70.0:
			continue
		if float(bp["h"]) > 50.0:
			c = pc
		elif float(bp["h"]) > 35.0:
			semis.append(pc)
	if semis.size() == 2:
		var d: Vector2 = semis[1] - semis[0]
		ax = d.normalized() if d.dot(ax) > 0.0 else -d.normalized()
	var g: float = env.ground_height(c.x, c.y)
	var g_low: float = env.b_base[i]
	var mins := []
	var px := Vector2(-ax.y, ax.x)
	for m: Dictionary in feats.get("minarets", []):
		var mp := Vector2(float(m["p"][0]), float(m["p"][1]))
		if mp.distance_to(c) > 95.0:
			continue
		var u := (mp - c).dot(ax)
		var v := (mp - c).dot(px)
		var mg: float = env.ground_height(mp.x, mp.y)
		var mm := {"p": mp, "g": minf(mg, g) - 0.5, "g0": mg, "style": "ottoman"}
		if u < 0.0:
			mm.merge({"h": 60.0, "r": 1.85, "n": 2, "mat": "stone"})       # Sinan's twins (west)
		elif v > 0.0:
			mm.merge({"h": 57.0, "r": 1.35, "n": 1, "mat": "stone"})       # Bayezid II (NE)
		else:
			mm.merge({"h": 55.0, "r": 1.65, "n": 1, "mat": "brick"})       # Mehmed II (SE, brick)
		mins.append(mm)
	var s := {"type": "hagia_sophia", "c": c, "ax": ax, "g": g, "g_low": g_low, "mins": mins, "claims": [i]}
	_claim(env, i, 24.0)
	for j in _inside(env, fp):
		if j != i:
			_claim(env, j, 0.5)
	var R := func(u0: float, u1: float, v0: float, v1: float) -> PackedVector2Array:
		return Kit.rect(c, ax, u0, u1, v0, v1)
	_solid(env, R.call(-46.0, -34.0, -31.0, 31.0), g_low, g + 17.0)
	_solid(env, R.call(-16.5, 16.5, -17.0, 17.0), g + 23.0, g + 43.0)
	_solid(env, Kit.ngon(c, 16.2, 8), g + 42.0, g + 49.0)
	_solid(env, Kit.ngon(c, 10.5, 8), g + 48.0, g + 54.0)
	_solid(env, Kit.ngon(c, 5.0, 8), g + 53.0, g + 57.5)
	for su: float in [-1.0, 1.0]:
		_solid(env, R.call(su * 16.0 if su > 0 else -31.0, 31.0 if su > 0 else -16.0, -15.6, 15.6), g + 23.0, g + 38.0)
		for sv: float in [-1.0, 1.0]:
			_solid(env, R.call(su * 13.0 - 3.5, su * 13.0 + 3.5, sv * 36.0 - (0.0 if sv > 0 else 9.0), sv * 36.0 + (9.0 if sv > 0 else 0.0)), g_low, g + 32.0)
	for m: Dictionary in mins:
		_solid(env, Kit.ngon(m["p"], float(m["r"]) + 1.0, 8), float(m["g"]), float(m["g0"]) + float(m["h"]))
	return s


static func _round_tower(env, i: int, H: float) -> Dictionary:
	var fp: PackedVector2Array = env.footprint(i)
	var c := Kit.centroid(fp)
	var r := sqrt(absf(Kit.area(fp)) / PI)
	var g: float = env.ground_height(c.x, c.y)
	_claim(env, i, H * 0.8)
	_solid(env, Kit.ngon(c, r + 0.6, 10), env.b_base[i], g + H)
	return {"c": c, "r": r, "g": g, "g_low": env.b_base[i], "claims": [i]}


static func _plan_galata(env, _feats: Dictionary, by: Dictionary) -> Dictionary:
	var i := _first(by, "galata tower")
	if i < 0:
		return {}
	var s := _round_tower(env, i, 66.9)
	s["type"] = "galata"
	s["r"] = 8.2
	return s


static func _plan_beyazit(env, _feats: Dictionary, by: Dictionary) -> Dictionary:
	var i := _first(by, "beyazit tower")
	if i < 0:
		return {}
	var fp: PackedVector2Array = env.footprint(i)
	var ob := Kit.obb(fp)
	var c: Vector2 = ob[0]
	var g: float = env.ground_height(c.x, c.y)
	_claim(env, i, 50.0)
	_solid(env, Kit.ngon(c, 4.6, 8), g + 49.0, g + 85.0)
	return {"type": "beyazit", "c": c, "ax": ob[1], "half": minf(ob[2].x, ob[2].y), "g": g,
		"g_low": env.b_base[i], "claims": [i]}


static func _plan_maiden(env, feats: Dictionary, _by: Dictionary) -> Dictionary:
	var l := _landmark(feats, "kiz kulesi")
	if l.is_empty() or not l.has("ring"):
		return {}
	var ring := _ring(l["ring"])
	if Kit.area(ring) < 0.0:
		ring.reverse()
	var c := Kit.centroid(ring)
	var ob := Kit.obb(ring)
	var claims := _inside(env, Kit.offset(ring, 4.0))
	for j: int in claims:
		_claim(env, j, 9.0)
	var tc := c + Vector2(0.0, 4.0)
	_solid(env, ring, -1.0, 9.5)
	_solid(env, Kit.ngon(tc, 4.6, 8), 8.0, 27.0)
	return {"type": "maiden", "ring": ring, "c": c, "tc": tc, "ax": ob[1], "claims": claims}


static func _plan_columns(env, feats: Dictionary, _by: Dictionary) -> Array:
	var out := []
	var defs := {
		"column of constantine": "constantine",
		"obelisk of theodosius": "obelisk",
		"the walled obelisk": "walled_obelisk",
		"serpent column": "serpent",
	}
	for nm: String in defs:
		var l := _landmark(feats, nm)
		if l.is_empty():
			continue
		var c := Vector2(float(l["p"][0]), float(l["p"][1]))
		var ring := _ring(l["ring"]) if l.has("ring") else Kit.ngon(c, 1.5, 4)
		if l.has("ring"):
			c = Kit.centroid(ring)
		var g: float = env.ground_height(c.x, c.y)
		var t: String = defs[nm]
		var top: float = {"constantine": 34.8, "obelisk": 25.6, "walled_obelisk": 32.0, "serpent": 5.5}[t]
		var claims := _inside(env, Kit.offset(ring, 1.0))
		for j: int in claims:
			_claim(env, j, 4.5 if t == "constantine" else 1.0)
		_solid(env, Kit.ngon(c, 2.2 if t != "serpent" else 0.6, 8), g - 1.0, g + top)
		out.append({"type": t, "c": c, "g": g, "ring": ring, "claims": claims})
	return out


static func _plan_topkapi(env, feats: Dictionary, by: Dictionary) -> Array:
	var out := []
	var l := _landmark(feats, "topkapi palace")
	if l.is_empty() or not l.has("ring"):
		return out
	var ring := _ring(l["ring"])
	# Tower of Justice.
	var tj := _first(by, "tower of justice")
	if tj >= 0:
		var fp: PackedVector2Array = env.footprint(tj)
		var ob := Kit.obb(fp)
		var c: Vector2 = ob[0]
		var g: float = env.ground_height(c.x, c.y)
		_claim(env, tj, 34.0)
		_solid(env, Kit.ngon(c, 3.2, 8), g + 33.0, g + 41.0)
		out.append({"type": "justice", "c": c, "ax": ob[1], "half": maxf(minf(ob[2].x, ob[2].y), 3.6), "g": g,
			"g_low": env.b_base[tj], "claims": [tj]})
	# Gate of Salutation: the twin towers.
	var gs := _landmark(feats, "gate of salutation")
	if not gs.is_empty() and gs.has("ring"):
		var gr := _ring(gs["ring"])
		var ob := Kit.obb(gr)
		var c: Vector2 = ob[0]
		var a: Vector2 = ob[1] if ob[2].x >= ob[2].y else Vector2(-ob[1].y, ob[1].x)
		var hl := maxf(ob[2].x, ob[2].y)
		var g: float = env.ground_height(c.x, c.y)
		var claims := _inside(env, Kit.offset(gr, 1.0))
		for j: int in claims:
			_claim(env, j, 12.0)
		var towers := [c + a * (hl - 3.0), c - a * (hl - 3.0)]
		for tp: Vector2 in towers:
			_solid(env, Kit.ngon(tp, 4.0, 8), g - 1.0, g + 27.0)
		_solid(env, Kit.rect(c, a, -hl, hl, -6.0, 6.0), g - 1.0, g + 12.0)
		out.append({"type": "salutation", "c": c, "ax": a, "hl": hl, "g": g, "towers": towers, "claims": claims})
	# Kitchens and pavilions.
	var kit_i := _first(by, "palace kitchens")
	var pav := []
	for j in _inside(env, ring, 25.0):
		var k := int(env.b_kind[j])
		if j == tj or (k != 4 and k != 12 and k != 0 and k != 6):
			continue
		if j == kit_i:
			continue
		var fp: PackedVector2Array = env.footprint(j)
		var h := clampf(float(env.b_h[j]), 5.0, 13.0)
		var c: Vector2 = env.b_centre[j]
		var ob := Kit.obb(fp)
		var fill := absf(Kit.area(fp)) / maxf(4.0 * ob[2].x * ob[2].y, 1.0)
		_claim(env, j, h + (0.35 * minf(ob[2].x, ob[2].y) if fill > 0.72 else 1.0))
		pav.append({"i": j, "fp": fp, "h": h, "g": env.ground_height(c.x, c.y), "g_low": env.b_base[j],
			"hip": fill > 0.72 and minf(ob[2].x, ob[2].y) < 18.0, "ob": ob,
			"dome": absf(Kit.area(fp)) < 450.0 and absf(Kit.area(fp)) > 60.0 and Kit.hash01(c, 9.0) < 0.35})
	out.append({"type": "pavilions", "list": pav, "claims": pav.map(func(x): return x["i"])})
	if kit_i >= 0:
		var fp: PackedVector2Array = env.footprint(kit_i)
		var ob := Kit.obb(fp)
		var c: Vector2 = ob[0]
		var g: float = env.ground_height(c.x, c.y)
		_claim(env, kit_i, 9.0)
		out.append({"type": "kitchens", "fp": fp, "ob": ob, "g": g, "g_low": env.b_base[kit_i], "claims": [kit_i]})
	return out


static func _plan_dolmabahce(env, _feats: Dictionary, by: Dictionary) -> Array:
	var out := []
	var parts := []
	var biggest := -1
	var ba := 0.0
	for i: int in by.get("dolmabahce palace", []):
		var fp: PackedVector2Array = env.footprint(i)
		var a := absf(Kit.area(fp))
		var h := clampf(float(env.b_h[i]), 12.0, 18.0)
		_claim(env, i, h + 1.2)
		var c: Vector2 = env.b_centre[i]
		parts.append({"i": i, "fp": fp, "h": h, "g": env.ground_height(c.x, c.y), "g_low": env.b_base[i]})
		if a > ba:
			ba = a
			biggest = parts.size() - 1
	if not parts.is_empty():
		var big: Dictionary = parts[biggest]
		var fp: PackedVector2Array = big["fp"]
		var c := Kit.centroid(fp)
		if not Geometry2D.is_point_in_polygon(c, fp):
			c = big["fp"][0]
		var g: float = big["g"]
		big["hall"] = c
		_solid(env, Kit.ngon(c, 16.0, 8), g + 10.0, g + 36.0)
		out.append({"type": "palace", "parts": parts, "claims": parts.map(func(x): return x["i"])})
	var ct := _first(by, "dolmabahce clock tower")
	if ct >= 0:
		var fp: PackedVector2Array = env.footprint(ct)
		var ob := Kit.obb(fp)
		var c: Vector2 = ob[0]
		var g: float = env.ground_height(c.x, c.y)
		_claim(env, ct, 8.0)
		_solid(env, Kit.ngon(c, 3.6, 8), g, g + 27.0)
		out.append({"type": "clock", "c": c, "ax": ob[1], "g": g, "claims": [ct]})
	return out


static func _plan_haydarpasa(env, _feats: Dictionary, by: Dictionary) -> Dictionary:
	var i := _first(by, "haydarpasa tren istasyonu")
	if i < 0:
		return {}
	var fp: PackedVector2Array = env.footprint(i)
	var ob := Kit.obb(fp)
	var o: Vector2 = ob[0]
	var a: Vector2 = ob[1]
	var he: Vector2 = ob[2]
	var px := Vector2(-a.y, a.x)
	var corners := [o + a * he.x + px * he.y, o - a * he.x + px * he.y, o - a * he.x - px * he.y, o + a * he.x - px * he.y]
	# The two adjacent corners with the most water beyond them face the sea.
	var best := 0
	var bw := -1
	for k in 4:
		var w := 0
		for cn: Vector2 in [corners[k], corners[(k + 1) % 4]]:
			var out_d := (cn - o).normalized()
			for dd: float in [25.0, 50.0, 80.0]:
				var q := cn + out_d * dd
				if env.is_water(q.x, q.y):
					w += 1
		if w > bw:
			bw = w
			best = k
	var towers := [corners[best], corners[(best + 1) % 4]]
	var g: float = env.ground_height(o.x, o.y)
	_claim(env, i, 22.0)
	_solid(env, Kit.rect(o, a, -he.x, he.x, -he.y, he.y), g + 21.0, g + 31.0)
	for tp: Vector2 in towers:
		var tc: Vector2 = tp + (o - tp).normalized() * 4.0
		_solid(env, Kit.ngon(tc, 5.6, 8), g - 1.0, g + 44.0)
	return {"type": "haydarpasa", "fp": fp, "o": o, "ax": a, "he": he, "g": g, "g_low": env.b_base[i],
		"towers": towers.map(func(t): return t + (o - t).normalized() * 4.0), "claims": [i]}


# =================================================================== draw

static func draw(lm, plan: Dictionary) -> void:
	for s: Dictionary in plan["sites"]:
		match s["type"]:
			"hagia_sophia":
				_hagia_sophia(lm, s)
			"galata":
				_galata(lm, s)
			"beyazit":
				_beyazit(lm, s)
			"maiden":
				_maiden(lm, s)
			"constantine", "obelisk", "walled_obelisk", "serpent":
				_column(lm, s)
			"justice":
				_justice(lm, s)
			"salutation":
				_salutation(lm, s)
			"pavilions":
				for pv: Dictionary in s["list"]:
					_pavilion(lm, pv)
			"kitchens":
				_kitchens(lm, s)
			"palace":
				_palace(lm, s)
			"clock":
				_clock(lm, s)
			"haydarpasa":
				_haydarpasa(lm, s)
		lm.count(s["type"])


static func _hagia_sophia(lm, s: Dictionary) -> void:
	var c: Vector2 = s["c"]
	var ax: Vector2 = s["ax"]
	var px := Vector2(-ax.y, ax.x)
	var g: float = s["g"]
	var gl: float = s["g_low"]
	var P: Kit.Parts = lm.parts(c)
	var R := func(u0: float, u1: float, v0: float, v1: float) -> PackedVector2Array:
		return Kit.rect(c, ax, u0, u1, v0, v1)
	var rot := -ax.angle()
	# Aisles and galleries (the body), the narthexes.
	P.prism("hs_win", R.call(-34.0, 31.0, -35.0, 35.0), gl, g + 23.5, "", 4.6)
	P.prism("hs", Kit.offset(R.call(-34.0, 31.0, -35.0, 35.0), 0.4), g + 23.5, g + 24.2, "lead_flat")
	P.prism("hs_win", R.call(-46.0, -34.0, -31.0, 31.0), gl, g + 16.0, "", 4.0)
	P.prism("hs", Kit.offset(R.call(-46.0, -34.0, -31.0, 31.0), 0.4), g + 16.0, g + 16.6, "lead_flat")
	P.prism("hs_win", R.call(-53.0, -46.0, -29.0, 29.0), gl, g + 10.0, "", 3.6)
	P.prism("hs", Kit.offset(R.call(-53.0, -46.0, -29.0, 29.0), 0.3), g + 10.0, g + 10.5, "lead_flat")
	# The tympanum block (north and south walls full of windows) under the dome.
	P.prism("hs_win", R.call(-16.5, 16.5, -17.0, 17.0), g + 23.0, g + 40.5, "lead_flat", 3.0)
	# The four great buttresses on the north and south faces, stepped.
	for su: float in [-1.0, 1.0]:
		for sv: float in [-1.0, 1.0]:
			var v_in := sv * 30.0
			var v_out := sv * 44.0
			var bc := c + ax * su * 13.0 + px * (v_in + v_out) * 0.5
			var b := Basis(Vector3.UP, -rot)
			P.box("hs", Kit.g(bc, (gl + g + 31.0) * 0.5), Vector3(3.6, (g + 31.0 - gl) * 0.5, 7.0), b)
			P.box("lead_flat", Kit.g(bc, g + 31.4), Vector3(3.8, 0.4, 7.2), b)
			var bc2 := c + ax * su * 13.0 + px * sv * 47.5
			P.box("hs", Kit.g(bc2, (gl + g + 20.0) * 0.5), Vector3(3.2, (g + 20.0 - gl) * 0.5, 3.5), b)
	# The drum: forty windows between forty buttresses; the shallow dome.
	var D := 31.2
	var db := g + 40.5
	var spring := g + 43.0
	P.frustum("drum_win", Kit.g(c, db), D * 0.5 + 0.6, D * 0.5 + 0.4, spring - db, 40, false, PI * (D + 1.0) / 40.0)
	for k in 40:
		var t := TAU * float(k) / 40.0
		var dir := Vector3(cos(t), 0, sin(t))
		P.box("hs", Kit.g(c, db + 1.4) + dir * (D * 0.5 + 1.4), Vector3(0.9, 1.5, 0.45), Basis(Vector3.UP, -t))
	P.dome("lead", Kit.g(c, spring), D * 0.5 + 0.5, 12.6, 40, 9, 40.0)
	lm.alem(Kit.g(c, spring + 12.5), 3.0)
	# The great semi-domes east and west, their exedrae, the apse.
	for su: float in [-1.0, 1.0]:
		var d := ax * su
		var sc := c + d * 15.8
		var td := atan2(-d.y, d.x)
		var a0 := td - PI * 0.5
		P.frustum("drum_win", Kit.g(sc, g + 23.0), 15.9, 15.9, 5.5, 20, false, 2.6, a0, a0 + PI)
		P.dome("lead", Kit.g(sc, g + 28.5), 15.8, 12.4, 20, 7, 20.0, a0, a0 + PI)
		for sg: float in [-1.0, 1.0]:
			var ec := sc + d * 6.5 + px * sg * 9.5
			var te := atan2(-(d.y + px.y * sg), d.x + px.x * sg)
			P.frustum("hs_win", Kit.g(ec, g + 20.0), 6.2, 6.2, 4.0, 8, false, 2.6, te - PI * 0.5, te + PI * 0.5)
			P.dome("lead", Kit.g(ec, g + 24.0), 6.0, 5.2, 8, 4, 10.0, te - PI * 0.5, te + PI * 0.5)
	# Apse (east): a polygonal half with its own semi-dome.
	var ac := c + ax * 31.0
	var ta := atan2(-ax.y, ax.x)
	P.frustum("hs_win", Kit.g(ac, gl), 8.0, 8.0, g + 21.0 - gl, 5, false, 3.0, ta - PI * 0.5, ta + PI * 0.5)
	P.dome("lead", Kit.g(ac, g + 21.0), 8.0, 6.5, 8, 4, 10.0, ta - PI * 0.5, ta + PI * 0.5)
	for m: Dictionary in s["mins"]:
		Mosque.minaret(lm, lm.parts(m["p"]), m, ax)
	lm.add_flood(Kit.g(c, g), 45.0, 56.0, "mosque")


static func _galata(lm, s: Dictionary) -> void:
	var c: Vector2 = s["c"]
	var g: float = s["g"]
	var r: float = s["r"]
	var P: Kit.Parts = lm.parts(c)
	var at := Kit.g(c, s["g_low"])
	var y_gal := g + 51.6
	P.frustum("rubble", at, r + 0.6, r + 0.6, g + 3.0 - float(s["g_low"]), 28, false)
	P.frustum("rubble_win", Kit.g(c, g + 3.0), r + 0.3, r - 0.2, y_gal - g - 3.0, 28, false, 7.0)
	# Gallery: balcony, iron rail, the arcaded drum, cornice.
	P.frustum("stone", Kit.g(c, y_gal - 1.2), r - 0.2, r + 1.2, 1.2, 28, false)
	P.frustum("stone", Kit.g(c, y_gal), r + 1.2, r + 1.2, 0.25, 28, true)
	P.frustum("iron", Kit.g(c, y_gal + 0.25), r + 1.15, r + 1.15, 1.05, 28, false)
	P.frustum("glass", Kit.g(c, y_gal + 0.25), r - 1.4, r - 1.4, 4.0, 28, false)
	P.frustum("arcade", Kit.g(c, y_gal + 0.25), r - 0.4, r - 0.4, 4.2, 28, false, 0.0)
	_arc_ring(P, c, r - 0.4, y_gal + 0.25, y_gal + 4.45, 24, 2.6)
	P.frustum("stone", Kit.g(c, y_gal + 4.45), r - 0.2, r + 0.5, 0.6, 28, true)
	# The cone: grey slate-lead, a gold finial.
	P.frustum("slate", Kit.g(c, y_gal + 5.0), r + 0.4, 0.15, 10.5, 28, false)
	lm.alem(Kit.g(c, y_gal + 15.4), 2.4)
	lm.add_flood(Kit.g(c, g), r + 1.0, 66.9, "tower")


## An arcade ring (both faces) round a cylinder: n bays, the arches springing at `spring`.
static func _arc_ring(P: Kit.Parts, c: Vector2, r: float, y0: float, y1: float, n: int, spring: float) -> void:
	var pts := PackedVector2Array()
	var b0 := PackedFloat32Array()
	var b1 := PackedFloat32Array()
	for k in n + 1:
		var t := TAU * float(k) / n
		pts.append(c + Vector2(cos(t), sin(t)) * r)
		b0.append(y0)
		b1.append(y1)
	var bay := TAU * r / n
	P.q = Vector2(bay, spring)
	for k in n:
		var a := pts[k]
		var b := pts[k + 1]
		var e := (b - a).normalized()
		var o := Kit.gd(Vector2(e.y, -e.x))
		P.quad("arcade", Kit.g(a, y0), Kit.g(b, y0), Kit.g(b, y1), Kit.g(a, y1), o, Rect2(0, 0, bay, y1 - y0))
		P.quad("arcade", Kit.g(a, y0), Kit.g(b, y0), Kit.g(b, y1), Kit.g(a, y1), -o, Rect2(0, 0, bay, y1 - y0))
	P.q = Vector2.ZERO


static func _beyazit(lm, s: Dictionary) -> void:
	var c: Vector2 = s["c"]
	var g: float = s["g"]
	var ax: Vector2 = s["ax"]
	var hw := clampf(float(s["half"]), 3.2, 4.6)
	var P: Kit.Parts = lm.parts(c)
	var b := Basis(Vector3.UP, ax.angle())
	var gl: float = s["g_low"]
	P.box("stone", Kit.g(c, (gl + g + 6.0) * 0.5), Vector3(hw + 0.8, (g + 6.0 - gl) * 0.5, hw + 0.8), b)
	# The square shaft, its corner fluting implied by a slight taper, to 50 m.
	var sq := Kit.rect(c, ax, -hw, hw, -hw, hw)
	P.prism("stone_win", sq, g + 6.0, g + 50.0, "", 2.8)
	P.prism("stone", Kit.offset(sq, 0.6), g + 50.0, g + 51.2, "stone")
	# Watch storeys: a windowed octagon, a balcony, a narrower one, lantern, cap.
	P.frustum("stone_win", Kit.g(c, g + 51.2), hw * 0.98, hw * 0.92, 9.0, 8, false, 2.2, 0.0, TAU, -ax.angle() + PI / 8.0)
	P.frustum("stone", Kit.g(c, g + 60.2), hw * 0.92, hw + 1.0, 0.8, 8, true, 0.0, 0.0, TAU, -ax.angle() + PI / 8.0)
	P.frustum("iron", Kit.g(c, g + 61.0), hw + 0.95, hw + 0.95, 1.0, 8, false, 0.0, 0.0, TAU, -ax.angle() + PI / 8.0)
	P.frustum("glass", Kit.g(c, g + 61.0), hw * 0.7, hw * 0.7, 7.0, 8, false)
	P.frustum("stone", Kit.g(c, g + 68.0), hw * 0.78, hw * 0.78, 1.0, 8, true)
	P.frustum("white_win", Kit.g(c, g + 69.0), hw * 0.55, hw * 0.55, 5.0, 8, false)
	P.frustum("lead", Kit.g(c, g + 74.0), hw * 0.7, 0.1, 9.5, 8, false)
	lm.alem(Kit.g(c, g + 83.3), 1.8)
	lm.add_flood(Kit.g(c, g), hw + 1.0, 85.0, "tower")


static func _maiden(lm, s: Dictionary) -> void:
	var ring: PackedVector2Array = s["ring"]
	var c: Vector2 = s["c"]
	var tc: Vector2 = s["tc"]
	var ax: Vector2 = s["ax"]
	var P: Kit.Parts = lm.parts(c)
	# The islet: a stone quay all round, then the low buildings, the tower.
	P.prism("stone_dark", ring, -2.0, 1.6, "stone")
	var inner := Kit.offset(ring, -6.0)
	if inner.size() >= 3:
		P.prism("white_win", inner, 1.6, 7.0, "", 0.0)
		P.prism("stone", Kit.offset(inner, 0.5), 7.0, 7.5, "lead_flat")
	var rot := -ax.angle()
	P.box("white", Kit.g(tc, 10.0), Vector3(2.9, 8.4, 2.9), Basis(Vector3.UP, -rot))
	P.frustum("white_win", Kit.g(tc, 18.4), 2.7, 2.6, 3.4, 8, false, 1.8, 0.0, TAU, rot + PI / 8.0)
	P.frustum("stone", Kit.g(tc, 21.8), 2.7, 3.5, 0.45, 8, true, 0.0, 0.0, TAU, rot + PI / 8.0)
	P.frustum("iron", Kit.g(tc, 22.25), 3.4, 3.4, 0.9, 8, false, 0.0, 0.0, TAU, rot + PI / 8.0)
	P.frustum("glass", Kit.g(tc, 22.25), 1.7, 1.7, 1.9, 8, false)
	P.dome("lead", Kit.g(tc, 24.1), 2.1, 1.5, 10, 3, 8.0)
	P.frustum("lead", Kit.g(tc, 25.4), 0.5, 0.05, 2.4, 8, false)
	lm.alem(Kit.g(tc, 27.7), 0.9)
	lm.add_flood(Kit.g(c, 1.6), 20.0, 27.0, "tower")


static func _column(lm, s: Dictionary) -> void:
	var c: Vector2 = s["c"]
	var g: float = s["g"]
	var P: Kit.Parts = lm.parts(c)
	match s["type"]:
		"constantine":
			var ring: PackedVector2Array = s["ring"]
			var ped := Kit.offset(ring, -2.0) if absf(Kit.area(ring)) > 60.0 else Kit.ngon(c, 3.2, 4, PI / 4.0)
			P.prism("stone_dark", ped, g - 1.0, g + 4.5, "stone")
			var y := g + 4.5
			P.frustum("marble", Kit.g(c, y), 2.0, 1.75, 1.2, 16, true)
			y += 1.2
			for k in 7:
				P.frustum("porphyry", Kit.g(c, y), 1.55, 1.52, 3.4, 16, false)
				P.frustum("iron", Kit.g(c, y + 3.1), 1.62, 1.62, 0.4, 16, false)
				y += 3.5
			P.frustum("stone", Kit.g(c, y), 1.55, 1.7, 34.8 - (y - g), 12, true)
			lm.add_flood(Kit.g(c, g), 3.0, 34.8, "column")
		"obelisk":
			P.box("marble", Kit.g(c, g + 0.8), Vector3(1.9, 0.8, 1.9))
			P.box("marble", Kit.g(c, g + 3.4), Vector3(1.55, 1.8, 1.55))
			P.box("marble", Kit.g(c, g + 5.5), Vector3(1.3, 0.3, 1.3))
			P.frustum("granite", Kit.g(c, g + 5.8), 1.25, 0.8, 18.5, 4, false, 0.0, 0.0, TAU, PI / 4.0)
			P.frustum("granite", Kit.g(c, g + 24.3), 0.8, 0.02, 1.3, 4, false, 0.0, 0.0, TAU, PI / 4.0)
			P.box("stone", Kit.g(c, g - 0.4), Vector3(3.2, 0.6, 3.2))
			lm.add_flood(Kit.g(c, g), 2.0, 25.6, "column")
		"walled_obelisk":
			P.box("stone", Kit.g(c, g + 1.0), Vector3(2.6, 1.0, 2.6))
			P.frustum("rubble", Kit.g(c, g + 2.0), 2.2, 1.25, 28.5, 4, false, 0.0, 0.0, TAU, PI / 4.0)
			P.frustum("rubble", Kit.g(c, g + 30.5), 1.25, 0.05, 1.5, 4, false, 0.0, 0.0, TAU, PI / 4.0)
			P.box("stone", Kit.g(c, g - 0.4), Vector3(3.6, 0.6, 3.6))
			lm.add_flood(Kit.g(c, g), 2.5, 32.0, "column")
		"serpent":
			P.frustum("stone_dark", Kit.g(c, g - 1.5), 2.4, 2.4, 1.4, 12, true)
			for k in 3:
				var t := TAU * k / 3.0
				for j in 6:
					var tt := t + float(j) * 0.9
					var off := Vector3(cos(tt), 0, sin(tt)) * 0.18
					P.frustum("bronze", Kit.g(c, g - 0.1 + j * 0.92) + off, 0.17, 0.17, 0.95, 6, j == 5)
			P.frustum("iron", Kit.g(c, g), 2.6, 2.6, 1.0, 12, false)


static func _justice(lm, s: Dictionary) -> void:
	var c: Vector2 = s["c"]
	var g: float = s["g"]
	var ax: Vector2 = s["ax"]
	var hw: float = clampf(float(s["half"]), 3.6, 5.0)
	var P: Kit.Parts = lm.parts(c)
	var sq := Kit.rect(c, ax, -hw, hw, -hw, hw)
	P.prism("stone_win", sq, s["g_low"], g + 26.0, "", 2.8)
	P.prism("stone", Kit.offset(sq, 0.5), g + 26.0, g + 26.8, "stone")
	# The belvedere: tall Palladian windows on every face, a deep eave.
	var up := Kit.rect(c, ax, -hw * 0.9, hw * 0.9, -hw * 0.9, hw * 0.9)
	P.prism("white_win", up, g + 26.8, g + 33.4, "", 0.0)
	P.prism("stone", Kit.offset(up, 1.4), g + 33.4, g + 33.8, "lead_flat")
	P.frustum("lead", Kit.g(c, g + 33.8), hw * 1.25, 0.08, 7.2, 4, false, 0.0, 0.0, TAU, -ax.angle() + PI / 4.0)
	lm.alem(Kit.g(c, g + 40.9), 1.6)
	lm.add_flood(Kit.g(c, g), hw + 1.0, 41.0, "tower")


static func _salutation(lm, s: Dictionary) -> void:
	var c: Vector2 = s["c"]
	var g: float = s["g"]
	var a: Vector2 = s["ax"]
	var hl: float = s["hl"]
	var P: Kit.Parts = lm.parts(c)
	var body := Kit.rect(c, a, -hl + 5.0, hl - 5.0, -6.0, 6.0)
	P.prism("stone_win", body, g - 1.0, g + 11.0, "", 3.6)
	P.prism("stone", Kit.offset(body, 1.6), g + 11.0, g + 11.4, "lead_flat")
	for tp: Vector2 in s["towers"]:
		P.frustum("stone", Kit.g(tp, g - 1.0), 3.8, 3.6, 19.5, 8, false, 0.0, 0.0, TAU, PI / 8.0)
		P.frustum("stone", Kit.g(tp, g + 18.5), 3.6, 4.1, 0.6, 8, true, 0.0, 0.0, TAU, PI / 8.0)
		P.frustum("lead", Kit.g(tp, g + 19.1), 4.0, 0.08, 7.8, 8, false, 0.0, 0.0, TAU, PI / 8.0)
		lm.alem(Kit.g(tp, g + 26.8), 1.2)
	lm.add_flood(Kit.g(c, g), hl, 27.0, "palace")


## A Topkapi pavilion: plastered walls, a wide-eaved low lead roof (hipped
## on the rectangular ones), a small dome on some.
static func _pavilion(lm, pv: Dictionary) -> void:
	var fp: PackedVector2Array = pv["fp"]
	var g: float = pv["g"]
	var h: float = pv["h"]
	var c := Kit.centroid(fp)
	var P: Kit.Parts = lm.parts(c)
	P.prism("white_win", fp, pv["g_low"], g + h, "", 0.0)
	var ob: Array = pv["ob"]
	if pv["hip"]:
		var o: Vector2 = ob[0]
		var a: Vector2 = ob[1]
		var he: Vector2 = ob[2] + Vector2(1.6, 1.6)
		var eave := Kit.rect(o, a, -he.x, he.x, -he.y, he.y)
		P.prism("wood", eave, g + h - 0.25, g + h, "")
		P.cap("wood", eave, g + h - 0.25, false)
		_hip_lead(P, o, a, he, g + h, 0.32)
	else:
		var eave := Kit.offset(fp, 1.4)
		P.prism("wood", eave, g + h - 0.25, g + h + 0.15, "lead_flat")
		P.cap("wood", eave, g + h - 0.25, false)
	if pv["dome"]:
		var r := minf(Kit.edge_dist(fp, c), 7.0) * 0.75
		if r > 1.5:
			var top := g + h + (0.32 * minf(ob[2].x, ob[2].y) * 0.6 if pv["hip"] else 0.15)
			P.frustum("white", Kit.g(c, top - 0.5), r * 1.05, r * 1.05, 1.0, 10, false)
			P.dome("lead", Kit.g(c, top + 0.5), r, r * 0.9, 10, 4, 10.0)
			lm.alem(Kit.g(c, top + 0.5 + r * 0.9), r * 0.35)
	lm.add_flood(Kit.g(c, g), sqrt(absf(Kit.area(fp))) * 0.5, h + 3.0, "palace")


## A hipped lead roof over an oriented rectangle (half extents he), pitch k.
static func _hip_lead(P: Kit.Parts, o: Vector2, a: Vector2, he: Vector2, y: float, k: float) -> void:
	var px := Vector2(-a.y, a.x)
	var ra := a if he.x >= he.y else px
	var rb := px if he.x >= he.y else a
	var hl := maxf(he.x, he.y)
	var hw := minf(he.x, he.y)
	var rise := hw * k
	var e := [Kit.g(o + ra * hl + rb * hw, y), Kit.g(o - ra * hl + rb * hw, y),
		Kit.g(o - ra * hl - rb * hw, y), Kit.g(o + ra * hl - rb * hw, y)]
	var r0 := Kit.g(o + ra * (hl - hw), y + rise)
	var r1 := Kit.g(o - ra * (hl - hw), y + rise)
	var up := Vector3.UP
	P.quad("lead_flat", e[0], e[1], r1, r0, Kit.gd(rb) + up)
	P.quad("lead_flat", e[2], e[3], r0, r1, Kit.gd(-rb) + up)
	P.tri("lead_flat", e[1], e[2], r1, Kit.gd(-ra) + up)
	P.tri("lead_flat", e[3], e[0], r0, Kit.gd(ra) + up)


static func _kitchens(lm, s: Dictionary) -> void:
	var fp: PackedVector2Array = s["fp"]
	var g: float = s["g"]
	var ob: Array = s["ob"]
	var o: Vector2 = ob[0]
	var a: Vector2 = ob[1] if ob[2].x >= ob[2].y else Vector2(-ob[1].y, ob[1].x)
	var hl := maxf(ob[2].x, ob[2].y)
	var hw := minf(ob[2].x, ob[2].y)
	var px := Vector2(-a.y, a.x)
	var P: Kit.Parts = lm.parts(o)
	P.prism("stone_win", fp, s["g_low"], g + 7.5, "", 5.0)
	P.prism("stone", Kit.offset(fp, 0.4), g + 7.5, g + 8.2, "lead_flat")
	# Ten domes down the spine, two rows of twenty chimneys either side.
	var row_v := clampf(hw * 0.45, 3.0, 12.0)
	for k in 10:
		var u := -hl + (2.0 * hl) * (float(k) + 0.5) / 10.0
		var dc := o + a * u
		var r := minf(2.0 * hl / 10.0 * 0.38, row_v * 0.8)
		P.frustum("stone", Kit.g(dc, g + 8.0), r * 1.05, r * 1.05, 1.4, 10, false)
		P.dome("lead", Kit.g(dc, g + 9.4), r, r * 0.85, 10, 4, 10.0)
		lm.alem(Kit.g(dc, g + 9.4 + r * 0.85), 0.9)
	for sv: float in [-1.0, 1.0]:
		for k in 20:
			var u := -hl + (2.0 * hl) * (float(k) + 0.5) / 20.0
			var cp := o + a * u + px * sv * row_v * 1.6
			if not Geometry2D.is_point_in_polygon(cp, fp):
				continue
			P.box("stone", Kit.g(cp, g + 10.0), Vector3(0.7, 2.0, 0.7), Basis(Vector3.UP, a.angle()))
			P.box("lead_flat", Kit.g(cp, g + 12.25), Vector3(0.9, 0.25, 0.9), Basis(Vector3.UP, a.angle()))
	lm.add_flood(Kit.g(o, g), hl, 12.0, "palace")


static func _palace(lm, s: Dictionary) -> void:
	for pt: Dictionary in s["parts"]:
		var fp: PackedVector2Array = pt["fp"]
		var g: float = pt["g"]
		var h: float = pt["h"]
		var c := Kit.centroid(fp)
		var P: Kit.Parts = lm.parts(c)
		P.prism("white_win", fp, pt["g_low"], g + h, "", 0.0)
		P.prism("white", Kit.offset(fp, 0.5), g + h, g + h + 0.6, "")
		# Balustrade parapet, a low lead roof behind it.
		P.prism("marble", fp, g + h + 0.6, g + h + 1.6, "")
		P.cap("lead_flat", Kit.offset(fp, -0.6), g + h + 1.1)
		if pt.has("hall"):
			var hc: Vector2 = pt["hall"]
			P.prism("white_win", Kit.ngon(hc, 17.0, 8, PI / 8.0), g + h, g + 28.0, "lead_flat", 0.0)
			P.dome("lead", Kit.g(hc, g + 28.0), 15.5, 8.0, 32, 6, 48.0)
			lm.alem(Kit.g(hc, g + 36.0), 2.0)
		lm.add_flood(Kit.g(c, g), sqrt(absf(Kit.area(fp))) * 0.5, h + 2.0, "palace")


static func _clock(lm, s: Dictionary) -> void:
	var c: Vector2 = s["c"]
	var g: float = s["g"]
	var ax: Vector2 = s["ax"]
	var P: Kit.Parts = lm.parts(c)
	var b := Basis(Vector3.UP, ax.angle())
	P.box("white", Kit.g(c, g + 0.6), Vector3(4.6, 0.6, 4.6), b)
	P.box("white_win", Kit.g(c, g + 5.6), Vector3(3.6, 4.4, 3.6), b)
	P.box("stone", Kit.g(c, g + 10.2), Vector3(3.9, 0.25, 3.9), b)
	P.box("white", Kit.g(c, g + 13.6), Vector3(3.1, 3.2, 3.1), b)
	P.box("stone", Kit.g(c, g + 17.0), Vector3(3.3, 0.2, 3.3), b)
	P.box("white", Kit.g(c, g + 19.4), Vector3(2.7, 2.2, 2.7), b)
	# Four clock faces.
	for k in 4:
		var d := b * (Vector3(cos(PI * 0.5 * k), 0, sin(PI * 0.5 * k)))
		var fc := Kit.g(c, g + 19.4) + d * 2.72
		var fb := Basis(Vector3.UP, ax.angle() - PI * 0.5 * k)
		P.box("clock", fc, Vector3(0.05, 1.35, 1.35), fb)
		P.box("iron", fc + d * 0.06, Vector3(0.03, 0.9, 0.08), fb)
		P.box("iron", fc + d * 0.06 + Vector3(0, 0.15, 0), Vector3(0.03, 0.08, 0.6), fb)
	P.box("stone", Kit.g(c, g + 21.8), Vector3(3.0, 0.2, 3.0), b)
	P.frustum("white", Kit.g(c, g + 22.0), 2.0, 2.0, 1.6, 8, false)
	P.dome("lead", Kit.g(c, g + 23.6), 2.1, 2.0, 10, 4, 8.0)
	lm.alem(Kit.g(c, g + 25.6), 1.4)
	lm.add_flood(Kit.g(c, g), 4.0, 27.0, "tower")


static func _haydarpasa(lm, s: Dictionary) -> void:
	var fp: PackedVector2Array = s["fp"]
	var g: float = s["g"]
	var o: Vector2 = s["o"]
	var a: Vector2 = s["ax"]
	var he: Vector2 = s["he"]
	var P: Kit.Parts = lm.parts(o)
	P.prism("stone_win", fp, s["g_low"], g + 21.0, "", 3.4)
	P.prism("stone", Kit.offset(fp, 0.6), g + 21.0, g + 21.8, "")
	_mansard(P, fp, g + 21.8, 5.0, 7.0, "slate")
	for tc: Vector2 in s["towers"]:
		P.frustum("stone_win", Kit.g(tc, g - 1.0), 5.4, 5.4, 33.0, 16, false, 3.0)
		P.frustum("stone", Kit.g(tc, g + 32.0), 5.4, 5.9, 0.8, 16, true)
		P.frustum("slate", Kit.g(tc, g + 32.8), 5.7, 0.1, 10.5, 16, false)
		lm.alem(Kit.g(tc, g + 43.2), 1.0)
	lm.add_flood(Kit.g(o, g), maxf(he.x, he.y), 32.0, "palace")


static func _hip_slate(P: Kit.Parts, o: Vector2, a: Vector2, he: Vector2, y: float, k: float) -> void:
	var px := Vector2(-a.y, a.x)
	var ra := a if he.x >= he.y else px
	var rb := px if he.x >= he.y else a
	var hl := maxf(he.x, he.y)
	var hw := minf(he.x, he.y)
	var rise := hw * k
	var e := [Kit.g(o + ra * hl + rb * hw, y), Kit.g(o - ra * hl + rb * hw, y),
		Kit.g(o - ra * hl - rb * hw, y), Kit.g(o + ra * hl - rb * hw, y)]
	var r0 := Kit.g(o + ra * (hl - hw), y + rise)
	var r1 := Kit.g(o - ra * (hl - hw), y + rise)
	var up := Vector3.UP
	P.quad("slate", e[0], e[1], r1, r0, Kit.gd(rb) + up)
	P.quad("slate", e[2], e[3], r0, r1, Kit.gd(-rb) + up)
	P.tri("slate", e[1], e[2], r1, Kit.gd(-ra) + up)
	P.tri("slate", e[3], e[0], r0, Kit.gd(ra) + up)


## A mansard roof over a footprint: steep slopes in `inset` metres, `rise`
## high, a flat top.
static func _mansard(P: Kit.Parts, fp: PackedVector2Array, y: float, inset: float, rise: float, mat: String) -> void:
	var ring := fp.duplicate()
	if Kit.area(ring) < 0.0:
		ring.reverse()
	var outer := Kit.inset(ring, -0.5)
	var inner := Kit.inset(ring, inset)
	var n := ring.size()
	for i in n:
		var j := (i + 1) % n
		var e := outer[j] - outer[i]
		var o := Kit.gd(Vector2(e.y, -e.x).normalized()) + Vector3.UP * 0.6
		P.quad(mat, Kit.g(outer[i], y), Kit.g(outer[j], y), Kit.g(inner[j], y + rise), Kit.g(inner[i], y + rise), o)
	if absf(Kit.area(inner)) > 10.0 and not Geometry2D.triangulate_polygon(inner).is_empty():
		P.cap(mat, inner, y + rise)

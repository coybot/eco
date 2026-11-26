## Ottoman mosques for istanbul_landmarks.gd: a procedural generator applied
## to every OSM mosque footprint, and the named imperial mosques at their
## published dimensions.
##
## A mosque is planned (plan(), in the env's claim phase: footprint claimed,
## solids added) into a spec Dictionary, then drawn from that same spec
## (draw(), when the landmarks node is built), so what an aircraft hits is
## what it sees.
##
## The plan, from the footprint alone: the footprint's oriented rectangle,
## turned so its long axis points as near the qibla (bearing 151.6 deg from
## Istanbul) as it can; a long footprint is a prayer hall (the qibla end) and
## a courtyard (the rest). The hall's size picks the type:
##   * small (mescit): one dome on an octagonal drum, or a hipped tile roof;
##   * single-dome (S < 30 m): dome ~0.8 of the hall on a windowed drum,
##     a domed portico (son cemaat yeri) in front;
##   * imperial (S >= 30 m): central dome ~0.47 of the hall, semi-domes
##     on its axis (or all four sides), weight turrets, domed side aisles.
## Minarets stand where OSM maps them, else at the hall's front corners (one
## or two) and the courtyard's far corners (four for the largest).
extends RefCounted

const Kit = preload("res://scripts/istanbul_landmark_kit.gd")

## Qibla from Istanbul: bearing 151.6 deg (ENU unit vector).
const QIBLA := Vector2(0.4756, -0.8797)

## Named mosques, published dimensions (Wikipedia; facts only). Keys are
## ASCII-folded lowercase substrings of the OSM name. D = central dome
## diameter (m), crown = dome crown above the floor (m), semis = semi-domes
## ("2" on the qibla axis, "4" all sides, "3" all but the qibla side, "1" the
## qibla side), mins = minaret count when OSM maps none, ser = serefes per
## minaret (front first), mh = minaret heights (front, far), style.
const SPECIALS := {
	"blue mosque": {"D": 23.5, "crown": 43.0, "semis": "4", "mins": 6, "ser": [3, 3, 3, 3, 2, 2], "mh": [64.0, 54.0]},
	"sultanahmet camii": {"D": 23.5, "crown": 43.0, "semis": "4", "mins": 6, "ser": [3, 3, 3, 3, 2, 2], "mh": [64.0, 54.0]},
	"suleymaniye mosque": {"D": 26.5, "crown": 53.0, "semis": "2", "mins": 4, "ser": [3, 3, 2, 2], "mh": [72.0, 56.0]},
	"new mosque": {"D": 17.5, "crown": 36.0, "semis": "4", "mins": 2, "ser": [3, 3], "mh": [56.0, 56.0]},
	"yeni cami": {"D": 17.5, "crown": 36.0, "semis": "4", "mins": 2, "ser": [3, 3], "mh": [56.0, 56.0]},
	"sehzade mosque": {"D": 19.0, "crown": 37.0, "semis": "4", "mins": 2, "ser": [2, 2], "mh": [55.0, 55.0]},
	"fatih mosque": {"D": 26.0, "crown": 44.0, "semis": "4", "mins": 2, "ser": [2, 2], "mh": [62.0, 62.0]},
	"bayezid ii mosque": {"D": 16.8, "crown": 36.0, "semis": "2", "mins": 2, "ser": [1, 1], "mh": [52.0, 52.0], "hall": 0.0},
	"nuruosmaniye": {"D": 25.75, "crown": 43.5, "semis": "0", "mins": 2, "ser": [2, 2], "mh": [58.0, 58.0], "style": "baroque"},
	"yavuz sultan selim": {"D": 24.5, "crown": 32.5, "semis": "0", "mins": 2, "ser": [1, 1], "mh": [50.0, 50.0]},
	"mihrimah sultan camii": {"D": 20.0, "crown": 37.0, "semis": "0", "mins": 1, "ser": [1], "mh": [56.0], "tall_block": true},
	"rustem pasa": {"D": 15.2, "crown": 27.0, "semis": "0", "mins": 1, "ser": [1], "mh": [42.0], "platform": 5.0},
	"little hagia sophia": {"D": 15.5, "crown": 22.0, "semis": "0", "mins": 1, "ser": [1], "mh": [26.0], "style": "byz", "octagon": true},
	"kilic ali pasha": {"D": 12.7, "crown": 26.0, "semis": "1", "mins": 1, "ser": [1], "mh": [40.0]},
	"nusretiye": {"D": 12.0, "crown": 29.0, "semis": "0", "mins": 2, "ser": [1, 1], "mh": [40.0, 40.0], "style": "baroque", "court": false},
	"dolmabahce mosque": {"D": 13.0, "crown": 26.0, "semis": "0", "mins": 2, "ser": [1, 1], "mh": [42.0, 42.0], "style": "baroque", "court": false},
	"ortakoy mosque": {"D": 11.0, "crown": 22.0, "semis": "0", "mins": 2, "ser": [1, 1], "mh": [32.0, 32.0], "style": "baroque", "court": false},
	"mihrimah sultan mosque": {"D": 11.6, "crown": 24.0, "semis": "3", "mins": 2, "ser": [2, 2], "mh": [40.0, 40.0]},
	"yeni valide": {"D": 15.0, "crown": 30.0, "semis": "2", "mins": 2, "ser": [2, 2], "mh": [46.0, 46.0]},
	"atik valide": {"D": 12.7, "crown": 25.0, "semis": "0", "mins": 1, "ser": [1], "mh": [38.0]},
	"eyup sultan": {"D": 17.0, "crown": 30.0, "semis": "0", "mins": 2, "ser": [2, 2], "mh": [58.0, 58.0], "style": "baroque"},
	"sokullu mehmed pasa mosque": {"D": 13.0, "crown": 24.0, "semis": "0", "mins": 1, "ser": [1], "mh": [36.0]},
	"selimiye mosque": {"D": 14.0, "crown": 27.0, "semis": "0", "mins": 2, "ser": [1, 1], "mh": [38.0, 38.0], "style": "baroque"},
	"laleli": {"D": 12.0, "crown": 25.0, "semis": "0", "mins": 2, "ser": [1, 1], "mh": [40.0, 40.0], "style": "baroque"},
	"chora": {"style": "byz", "mins": 1},
	"molla zeyrek": {"style": "byz", "mins": 1},
	"fethiye": {"style": "byz", "mins": 1},
	"kalenderhane": {"style": "byz", "mins": 1},
	"gul camii": {"style": "byz", "mins": 1},
	"bodrum": {"style": "byz", "mins": 1},
}


static func fold(s: String) -> String:
	s = s.to_lower()
	for pair in [["ş", "s"], ["ı", "i"], ["ğ", "g"], ["ü", "u"], ["ö", "o"], ["ç", "c"], ["â", "a"], ["î", "i"], ["İ", "i"], ["i̇", "i"]]:
		s = s.replace(pair[0], pair[1])
	return s


static func special(key: String) -> Dictionary:
	if key == "":
		return {}
	for k: String in SPECIALS:
		if key.contains(k):
			return SPECIALS[k]
	return {}


static func _largest(polys: Array) -> PackedVector2Array:
	var best := PackedVector2Array()
	var ba := 0.0
	for p: PackedVector2Array in polys:
		var a := absf(Kit.area(p))
		if a > ba:
			ba = a
			best = p
	return best


## Local frame range (u along ax, v along px) of a polygon about o.
static func _span(p: PackedVector2Array, o: Vector2, ax: Vector2) -> Rect2:
	var px := Vector2(-ax.y, ax.x)
	var lo := Vector2(INF, INF)
	var hi := Vector2(-INF, -INF)
	for q in p:
		var d := q - o
		lo = Vector2(minf(lo.x, d.dot(ax)), minf(lo.y, d.dot(px)))
		hi = Vector2(maxf(hi.x, d.dot(ax)), maxf(hi.y, d.dot(px)))
	return Rect2(lo, hi - lo)


## Minarets OSM maps (features.json), grouped by the mosque footprint each
## stands in or beside: {building index: [[Vector2, h or 0], ...]}.
static func mapped_minarets(env, feats: Dictionary) -> Dictionary:
	var out := {}
	for m: Dictionary in feats.get("minarets", []):
		var p := Vector2(float(m["p"][0]), float(m["p"][1]))
		var best := -1
		var bd := 32.0
		for j in env.buildings_in(Rect2(p - Vector2(32, 32), Vector2(64, 64))):
			if int(env.b_kind[j]) != 2:
				continue
			var d := 0.0 if env._inside(j, p.x, p.y) else Kit.edge_dist(env.footprint(j), p)
			if d < bd:
				bd = d
				best = j
		if best < 0:
			continue
		if not out.has(best):
			out[best] = []
		var h = m.get("h")
		out[best].append([p, float(h) if h != null else 0.0])
	return out


# =================================================================== plan

## A spec for mosque footprint i, or {} to leave it to the buildings.
static func plan(env, i: int, name: String, mapped: Array) -> Dictionary:
	var fp: PackedVector2Array = env.footprint(i)
	var area := absf(Kit.area(fp))
	if area < 45.0:
		return {}
	var key := fold(name)
	var sp := special(key)
	var ob := Kit.obb(fp)
	var o: Vector2 = ob[0]
	var a1: Vector2 = ob[1]
	var he: Vector2 = ob[2]
	var cands := [[a1, he.x, he.y], [-a1, he.x, he.y],
		[Vector2(-a1.y, a1.x), he.y, he.x], [Vector2(a1.y, -a1.x), he.y, he.x]]
	var ax := a1
	var hl := he.x
	var hw := he.y
	var bd := -2.0
	for cd: Array in cands:
		var d: float = (cd[0] as Vector2).dot(QIBLA)
		if d > bd:
			bd = d
			ax = cd[0]
			hl = cd[1]
			hw = cd[2]
	var px := Vector2(-ax.y, ax.x)
	var kind := "mosque"
	if key.contains("medrese") or key.contains("madrasa"):
		kind = "medrese"
	elif key.contains("turbe") or key.contains("tomb"):
		kind = "turbe"
	var g_low: float = env.b_base[i]
	var spec := {"i": i, "name": name, "kind": kind, "ax": ax, "style": sp.get("style", "ottoman"),
		"g_low": g_low, "mins": [], "court": PackedVector2Array(), "domes": [], "solids": []}

	# ---------------------------------------------------- hall and courtyard
	var L := 2.0 * hl
	var W := 2.0 * hw
	var hall := fp
	var court := PackedVector2Array()
	var want_court: bool = sp.get("court", true) and kind == "mosque"
	if want_court and area >= 900.0 and L > 1.4 * W:
		var hall_len := minf(W * 1.05, L * 0.6)
		var cut := hl - hall_len
		var big := 2000.0
		var hp := _largest(Geometry2D.intersect_polygons(fp, Kit.rect(o, ax, cut, big, -big, big)))
		var cp := _largest(Geometry2D.intersect_polygons(fp, Kit.rect(o, ax, -big, cut, -big, big)))
		if hp.size() >= 3 and cp.size() >= 3 and absf(Kit.area(cp)) > 150.0:
			hall = hp
			court = cp
	spec["court"] = court
	var hs := _span(hall, o, ax)
	var hu0 := hs.position.x
	var hu1 := hs.end.x
	var hv0 := hs.position.y
	var hv1 := hs.end.y
	var Wh := hv1 - hv0
	var Lh := hu1 - hu0

	if kind == "medrese":
		return _plan_medrese(env, spec, fp, o, ax)

	# Portico across the hall's front (the end away from the qibla).
	var pd := 0.0
	if kind == "mosque" and Wh >= 9.0 and Lh >= 12.0 and not sp.get("octagon", false):
		pd = clampf(0.2 * Wh, 3.5, 9.0)
		if Lh - pd < 0.75 * Wh * 0.6:
			pd = 0.0
	var bu0 := hu0 + pd
	var body := hall
	if pd > 0.0:
		var bp := _largest(Geometry2D.intersect_polygons(hall, Kit.rect(o, ax, bu0, 2000.0, -2000.0, 2000.0)))
		if bp.size() >= 3:
			body = bp
		else:
			pd = 0.0
			bu0 = hu0
	var Lb := hu1 - bu0
	var S := minf(Lb, Wh)
	var c := o + ax * (bu0 + hu1) * 0.5 + px * (hv0 + hv1) * 0.5
	if not Geometry2D.is_point_in_polygon(c, body):
		c = Kit.centroid(body)
	var fit := 2.0 * Kit.edge_dist(body, c)
	var g: float = env.ground_height(c.x, c.y)
	if sp.has("platform"):
		g += float(sp["platform"])
	spec["c"] = c
	spec["g"] = g
	spec["hall"] = body
	spec["portico"] = [hu0, bu0, hv0, hv1, o] if pd > 0.0 else []

	# ---------------------------------------------------------- the domes
	var semis: String = sp.get("semis", "")
	var imperial := semis != "" and semis != "0"
	if semis == "" and S >= 30.0:
		semis = "2"
		imperial = true
	var D: float
	var crown: float
	var rise: float
	var drum_h: float
	var wall_h: float
	var drum_base: float
	var semi_spring := 0.0
	var roof := "dome"
	if kind == "turbe":
		D = clampf(0.86 * S, 3.0, 14.0)
		wall_h = 0.55 * S + 2.5
		rise = 0.5 * D
		drum_h = 0.08 * D
		drum_base = wall_h
		crown = drum_base + drum_h + rise
	elif imperial:
		D = float(sp.get("D", 0.47 * S))
		D = minf(D, fit * 0.98) if not sp.has("D") else D
		crown = float(sp.get("crown", 1.9 * D))
		rise = 0.47 * D
		drum_h = 0.12 * D
		drum_base = crown - rise - drum_h
		semi_spring = drum_base - 0.02 * D - 0.45 * D
		wall_h = maxf(semi_spring - 0.08 * D, 0.32 * crown)
	else:
		D = float(sp.get("D", clampf(0.8 * S, 4.5, 26.0)))
		if not sp.has("D"):
			D = minf(D, fit * 0.96)
		rise = 0.48 * D
		drum_h = 0.1 * D if D > 9.0 else 0.06 * D
		if sp.has("crown"):
			crown = float(sp["crown"])
			wall_h = crown - rise - drum_h - 0.12 * D
			if sp.get("tall_block", false):
				wall_h = minf(wall_h, 0.42 * crown)
		else:
			wall_h = 0.42 * S + 3.2
			crown = wall_h + 0.12 * D + drum_h + rise
		drum_base = crown - rise - drum_h
		var hsh := Kit.hash01(c, 3.0)
		if area < 170.0 and sp.is_empty() and mapped.is_empty() and hsh < 0.38:
			roof = "hip"
	if D < 3.0:
		return {}
	spec["roof"] = roof
	spec["D"] = D
	spec["crown"] = crown
	spec["rise"] = rise
	spec["drum_h"] = drum_h
	spec["drum_base"] = drum_base
	spec["wall_h"] = wall_h
	spec["semis"] = semis if imperial else ""
	spec["semi_spring"] = semi_spring
	spec["S"] = S
	spec["Lb"] = Lb
	spec["Wh"] = Wh
	spec["octagon"] = sp.get("octagon", false) or (not imperial and D >= 0.86 * S)
	# A dome much smaller than its footprint (a complex mapped as one
	# building): the prayer hall is a cube round the dome, the rest a low annex.
	var fill := absf(Kit.area(body)) / maxf(Lb * Wh, 1.0)
	spec["annex"] = 0.0
	if not imperial and kind == "mosque" and roof == "dome" and (D < 0.7 * S or fill < 0.68) and S > 12.0:
		spec["annex"] = clampf(0.45 * wall_h, 4.0, 7.0)
		spec["octagon"] = false

	# Aisle domes (imperial): along the sides, or the four corners.
	var domes: Array = []
	if imperial:
		var hD := 0.53 * D
		var aw := Wh * 0.5 - hD
		var au := Lb * 0.5 - hD
		var lc := (bu0 + hu1) * 0.5
		var vc := (hv0 + hv1) * 0.5
		if semis == "2" and aw > 3.0:
			var n := maxi(1, int(roundf(Lb / aw)))
			for side: float in [-1.0, 1.0]:
				for k in n:
					var u := bu0 + Lb * (float(k) + 0.5) / n
					var big := k == (n - 1) / 2 and n % 2 == 1
					domes.append([o + ax * u + px * (vc + side * (hD + aw * 0.5)), aw * (0.46 if big else 0.4)])
		elif (semis == "4" or semis == "3") and aw > 3.0 and au > 3.0:
			var rr := minf(aw, au) * 0.44
			for su: float in [-1.0, 1.0]:
				for sv: float in [-1.0, 1.0]:
					domes.append([o + ax * (lc + su * (hD + au * 0.5)) + px * (vc + sv * (hD + aw * 0.5)), rr])
	spec["domes"] = domes

	# ------------------------------------------------------------ minarets
	var mins: Array = []
	var nmin: int = int(sp.get("mins", 0))
	if kind != "mosque":
		nmin = 0
	elif nmin == 0:
		if court.is_empty():
			nmin = 1 if area < 1400.0 else 2
		else:
			nmin = 2 if area < 4000.0 else 4
	var mh: Array = sp.get("mh", [])
	var ser: Array = sp.get("ser", [])
	var base_h := crown * 1.3 if imperial else crown * 1.42 + 4.0
	var hmin := clampf(base_h, 16.0, 75.0)
	if roof == "hip":
		hmin = clampf(wall_h * 2.6, 15.0, 24.0)
	var spots: Array = []     # [p, h, front?]
	if not mapped.is_empty() and kind == "mosque":
		for m: Array in mapped:
			spots.append([m[0], float(m[1]), true])
		# The far ones (nearest the courtyard's far end) are the lower pair.
		if spots.size() >= 4:
			spots.sort_custom(func(a, b): return (a[0] - c).dot(ax) > (b[0] - c).dot(ax))
			for k in spots.size():
				spots[k][2] = k < spots.size() - 2 if spots.size() > 4 else k < 2
	else:
		var rm := clampf(0.036 * hmin, 1.05, 2.4)
		var uf := bu0 + rm * 1.2 if pd > 0.0 else hu0 + rm * 1.2
		var right := o + ax * uf + px * (hv0 - rm * 1.15)
		var left := o + ax * uf + px * (hv1 + rm * 1.15)
		if nmin >= 1:
			spots.append([right, 0.0, true])
		if nmin >= 2:
			spots.append([left, 0.0, true])
		if nmin >= 4 and not court.is_empty():
			var cs := _span(court, o, ax)
			spots.append([o + ax * (cs.position.x - rm * 1.2) + px * (cs.position.y - rm * 1.2), 0.0, false])
			spots.append([o + ax * (cs.position.x - rm * 1.2) + px * (cs.end.y + rm * 1.2), 0.0, false])
	for k in spots.size():
		var s: Array = spots[k]
		var front: bool = s[2]
		var h: float = s[1]
		if h <= 0.0:
			if mh.size() > 0:
				h = float(mh[0] if front or mh.size() == 1 else mh[1])
			else:
				h = hmin if front else hmin * 0.82
		elif not mh.is_empty() and spots.size() <= 4:
			h = maxf(h, float(mh[0] if front or mh.size() == 1 else mh[1]))
		var n := 1 if h < 40.0 else (2 if h < 58.0 else 3)
		if k < ser.size():
			n = int(ser[k])
		elif not front and n > 1:
			n -= 1
		var st: String = spec["style"]
		var r := clampf(0.03 * h, 0.95, 2.0)
		if st == "baroque":
			r = clampf(0.026 * h, 0.9, 1.6)
		var mp: Vector2 = s[0]
		var mg: float = env.ground_height(mp.x, mp.y)
		var mst := st
		mins.append({"p": mp, "g": minf(mg, g), "g0": maxf(mg, g - (float(sp["platform"]) if sp.has("platform") else 0.0)),
			"h": h, "r": r, "n": n, "style": mst, "mat": "brick" if st == "byz" else "stone"})
	spec["mins"] = mins
	_solids(env, spec)
	return spec


## A madrasa: cells round a courtyard, a small dome over each, a larger
## dome over the lecture hall on the qibla side.
static func _plan_medrese(env, spec: Dictionary, fp: PackedVector2Array, o: Vector2, ax: Vector2) -> Dictionary:
	var c := Kit.centroid(fp)
	var g: float = env.ground_height(c.x, c.y)
	var sp := _span(fp, o, ax)
	var S := minf(sp.size.x, sp.size.y)
	if S < 16.0:
		spec["kind"] = "turbe"
		var D := clampf(0.8 * S, 3.0, 12.0)
		spec.merge({"c": c, "g": g, "hall": fp, "portico": [], "roof": "dome", "D": D,
			"rise": 0.48 * D, "drum_h": 0.06 * D, "wall_h": 0.45 * S + 3.0,
			"drum_base": 0.45 * S + 3.0, "crown": 0.45 * S + 3.0 + 0.54 * D, "semis": "",
			"S": S, "octagon": true, "court": PackedVector2Array(), "domes": [], "mins": []}, true)
		_solids(env, spec)
		return spec
	spec.merge({"c": c, "g": g, "hall": PackedVector2Array(), "portico": [], "roof": "none",
		"D": 0.0, "crown": 8.0, "wall_h": 6.5, "semis": "", "S": S, "court": fp,
		"dershane": o + ax * (sp.end.x - S * 0.18) + Vector2(-ax.y, ax.x) * sp.get_center().y,
		"dershane_D": clampf(S * 0.28, 6.0, 12.0)}, true)
	_solids(env, spec)
	return spec


## The spec's solids: the claimed footprint drops to its lowest part's height,
## the rest go in as obstacles (and are kept in spec["solids"]).
static func _solids(env, spec: Dictionary) -> void:
	var i: int = spec["i"]
	var g: float = spec["g"]
	var g_low: float = spec["g_low"]
	var cg: Vector2 = env.b_centre[i]
	var base_top := g + float(spec["wall_h"])
	if not (spec["court"] as PackedVector2Array).is_empty():
		base_top = g + _arcade_h(spec)
	if spec["roof"] == "hip":
		base_top = g + float(spec["wall_h"]) + 0.3 * float(spec["S"])
	if float(spec.get("annex", 0.0)) > 0.0:
		base_top = g + float(spec["annex"])
	env.b_skip[i] = 1
	env.b_top[i] = base_top
	env.b_h[i] = base_top - env.ground_height(cg.x, cg.y)
	var add := func(ring: PackedVector2Array, lo: float, hi: float) -> void:
		if hi > lo + 0.3:
			env.add_obstacle(ring, lo, hi)
	if not (spec["court"] as PackedVector2Array).is_empty() and (spec["hall"] as PackedVector2Array).size() >= 3:
		add.call(spec["hall"], g_low, g + float(spec["wall_h"]))
	var c: Vector2 = spec["c"]
	var D: float = spec["D"]
	if float(spec.get("annex", 0.0)) > 0.0:
		var hc := 0.55 * D
		add.call(Kit.rect(c, spec["ax"], -hc, hc, -hc, hc), g_low, g + float(spec["wall_h"]))
	if spec["kind"] == "medrese":
		if spec.has("dershane"):
			var dc: Vector2 = spec["dershane"]
			add.call(Kit.ngon(dc, float(spec["dershane_D"]) * 0.5, 8), g_low, g + 6.5 + float(spec["dershane_D"]) * 0.5)
	elif spec["roof"] == "dome" and D > 0.0:
		var wall_top := g + float(spec["wall_h"])
		var db := g + float(spec["drum_base"])
		var spring := db + float(spec["drum_h"])
		var rise: float = spec["rise"]
		var ax: Vector2 = spec["ax"]
		var rot := ax.angle()
		var hD := 0.53 * D
		if db > wall_top + 0.3:
			add.call(Kit.rect(c, ax, -hD, hD, -hD, hD), wall_top - 0.5, db)
		add.call(Kit.ngon(c, D * 0.52, 8, rot), db - 0.5, spring + rise * 0.4)
		add.call(Kit.ngon(c, D * 0.4, 8, rot), spring, spring + rise * 0.8)
		add.call(Kit.ngon(c, D * 0.22, 8, rot), spring, g + float(spec["crown"]) + 0.12 * D)
		var ss := g + float(spec.get("semi_spring", 0.0))
		for d: Vector2 in semi_dirs(spec):
			var sc := c + d * D * 0.5
			var pp := Vector2(-d.y, d.x)
			var ring := PackedVector2Array([sc + pp * D * 0.5, sc + pp * D * 0.5 + d * D * 0.48,
				sc - pp * D * 0.5 + d * D * 0.48, sc - pp * D * 0.5])
			add.call(ring, wall_top - 0.5, ss + 0.3 * D)
	for m: Dictionary in spec["mins"]:
		add.call(Kit.ngon(m["p"], float(m["r"]) + 1.0, 8), float(m["g"]) - 0.5, float(m["g0"]) + float(m["h"]))


static func _arcade_h(spec: Dictionary) -> float:
	var court: PackedVector2Array = spec["court"]
	var sp := Kit.obb(court)
	var dep := clampf(0.12 * minf(sp[2].x, sp[2].y) * 2.0, 4.0, 7.5)
	return 5.5 + dep * 0.5


## Semi-dome directions (sim unit vectors) of an imperial spec.
static func semi_dirs(spec: Dictionary) -> Array:
	var ax: Vector2 = spec["ax"]
	var px := Vector2(-ax.y, ax.x)
	match spec.get("semis", ""):
		"1":
			return [ax]
		"2":
			return [ax, -ax]
		"3":
			return [-ax, px, -px]
		"4":
			return [ax, -ax, px, -px]
	return []


# =================================================================== draw

## Draws a planned mosque. lm: the landmarks node (parts(p) -> Kit.Parts for
## the chunk at p; inst(unit, xf) for instanced parts).
static func draw(lm, spec: Dictionary) -> void:
	var c: Vector2 = spec["c"]
	var P: Kit.Parts = lm.parts(c)
	var g: float = spec["g"]
	var g_low: float = spec["g_low"]
	var ax: Vector2 = spec["ax"]
	var px := Vector2(-ax.y, ax.x)
	var style: String = spec["style"]
	var wall_k := "banded" if style == "byz" else "stone_win"
	var D: float = spec["D"]
	var wall_top := g + float(spec["wall_h"])
	var hall: PackedVector2Array = spec["hall"]
	if spec["kind"] == "medrese":
		_court(lm, P, spec, true)
		if spec.has("dershane"):
			var dc: Vector2 = spec["dershane"]
			var dd: float = spec["dershane_D"]
			P.prism("stone_win", Kit.ngon(dc, dd * 0.54, 8, ax.angle() + PI / 8.0), g_low, g + 6.5, "", 3.0)
			P.dome("lead", Kit.g(dc, g + 6.5), dd * 0.5, dd * 0.47, 14, 5, 24.0)
			lm.alem(Kit.g(dc, g + 6.5 + dd * 0.47), dd * 0.16)
		return
	if hall.size() >= 3:
		var bay := 4.2 if style != "byz" else 0.0
		if spec["roof"] == "hip":
			P.prism("stone_win", hall, g_low, wall_top, "", 3.0)
			_hip(P, hall, ax, wall_top, float(spec["S"]))
		elif float(spec.get("annex", 0.0)) > 0.0:
			var at := g + float(spec["annex"])
			P.prism(wall_k, hall, g_low, at - 0.4, "", bay)
			P.prism("stone" if style != "byz" else "banded", Kit.offset(hall, 0.3), at - 0.4, at, "lead_flat")
			var hc := 0.55 * D
			var cube := Kit.rect(spec["c"], ax, -hc, hc, -hc, hc)
			P.prism(wall_k, cube, g_low, wall_top - 0.5, "", bay)
			P.prism("stone" if style != "byz" else "banded", Kit.offset(cube, 0.35), wall_top - 0.5, wall_top + 0.1, "lead_flat")
		else:
			P.prism(wall_k, hall, g_low, wall_top - 0.5, "", bay)
			P.prism("stone" if style != "byz" else "banded", Kit.offset(hall, 0.35), wall_top - 0.5, wall_top + 0.1, "lead_flat")
	if not (spec["portico"] as Array).is_empty() and float(spec.get("annex", 0.0)) <= 0.0:
		_portico(lm, P, spec)
	if not (spec["court"] as PackedVector2Array).is_empty():
		_court(lm, P, spec, false)
	if spec["roof"] == "dome":
		_domes(lm, P, spec, wall_top)
	for d: Array in spec["domes"]:
		lm.cupola(Kit.g(d[0], wall_top + 0.1), float(d[1]))
	for m: Dictionary in spec["mins"]:
		minaret(lm, lm.parts(m["p"]), m, ax)


## The central dome and everything that carries it.
static func _domes(lm, P: Kit.Parts, spec: Dictionary, wall_top: float) -> void:
	var c: Vector2 = spec["c"]
	var g: float = spec["g"]
	var D: float = spec["D"]
	var ax: Vector2 = spec["ax"]
	var style: String = spec["style"]
	var db := g + float(spec["drum_base"])
	var dh: float = spec["drum_h"]
	var rise: float = spec["rise"]
	var spring := db + dh
	var rot := -ax.angle()
	var hD := 0.53 * D
	var stone := "banded" if style == "byz" else "stone"
	var winst := "banded" if style == "byz" else "stone_win"
	# Transition: an octagon (single domes filling the hall) or the square
	# tympanum block (imperial, big domes on piers).
	if db > wall_top + 0.2:
		if spec["octagon"]:
			var r8 := D * 0.5 / cos(PI / 8.0) * 1.04
			P.prism(winst, Kit.ngon(c, r8, 8, ax.angle() + PI / 8.0), wall_top - 0.3, db, "lead_flat", 2.6)
		else:
			P.prism(winst, Kit.rect(c, ax, -hD, hD, -hD, hD), wall_top - 0.3, db, "lead_flat", 3.2)
			# Weight turrets at the corners of the square.
			if D > 12.0:
				var tr := D * 0.055
				for su: float in [-1.0, 1.0]:
					for sv: float in [-1.0, 1.0]:
						var tp := c + ax * su * hD + Vector2(-ax.y, ax.x) * sv * hD
						P.frustum(stone, Kit.g(tp, db - 1.0), tr, tr * 0.95, D * 0.1 + 1.0, 8, true)
						lm.cupola(Kit.g(tp, db + D * 0.1), tr * 1.05)
	# Drum (windowed) and buttresses.
	var rd := D * 0.5 * 1.03
	var seg := clampi(int(D * 1.3), 16, 40)
	if dh > 0.4:
		var nwin := clampi(int(PI * D / 2.6), 8, 40)
		P.frustum("drum_win" if style != "byz" else "banded", Kit.g(c, db), rd, rd, dh, seg, false,
			PI * 2.0 * rd / nwin)
		if D > 14.0:
			var nb := nwin / 2 if nwin > 24 else nwin
			for k in nb:
				var t := TAU * (float(k) + 0.5) / nb
				var dir := Vector3(cos(t), 0, sin(t))
				var b := Basis(Vector3.UP, -t)
				P.box(stone, Kit.g(c, db + dh * 0.5) + dir * (rd + 0.45), Vector3(0.45, dh * 0.5, 0.35), b)
		# A cornice ring at the dome's foot.
		P.frustum(stone, Kit.g(c, spring - 0.25), rd + 0.25, rd + 0.25, 0.35, seg, false)
	# The dome: lead, seams every ~1.2 m round its foot.
	var ribs := roundf(PI * D / 1.25)
	P.dome("lead", Kit.g(c, spring), D * 0.5, rise, seg, clampi(int(D / 2.5), 5, 10), ribs, 0.0, TAU, 0.06)
	lm.alem(Kit.g(c, spring + rise * 1.06), clampf(D * 0.13, 0.8, 4.0))
	# Semi-domes: half drums with windows, quarter-sphere lead caps.
	var ss := g + float(spec.get("semi_spring", 0.0))
	for d: Vector2 in semi_dirs(spec):
		var sc := c + d * D * 0.5
		var td := atan2(-d.y, d.x)
		var a0 := td - PI * 0.5
		var r := D * 0.5
		if ss > wall_top:
			P.frustum("drum_win", Kit.g(sc, wall_top - 0.3), r * 1.02, r * 1.02, ss - wall_top + 0.3, 12, false,
				2.8, a0, a0 + PI)
		P.frustum(stone, Kit.g(sc, ss - 0.25), r * 1.04, r * 1.04, 0.35, 12, false, 0.0, a0, a0 + PI)
		P.dome("lead", Kit.g(sc, ss), r, 0.45 * D, 12, 6, roundf(PI * D / 2.5), a0, a0 + PI)
		# Small exedra semi-domes at its shoulders (imperial only).
		if D > 18.0:
			var pp := Vector2(-d.y, d.x)
			for sgn: float in [-1.0, 1.0]:
				var ec := sc + d * r * 0.55 + pp * sgn * r * 0.72
				var er := r * 0.36
				var te := atan2(-(d.y + pp.y * sgn * 0.8), d.x + pp.x * sgn * 0.8)
				if ss - wall_top - r * 0.25 < 1.0:
					continue
				P.frustum("drum_win", Kit.g(ec, wall_top - 0.3), er, er, ss - wall_top - r * 0.25, 8, false,
					2.6, te - PI * 0.5, te + PI * 0.5)
				P.dome("lead", Kit.g(ec, ss - r * 0.25), er, er * 0.9, 8, 4, 10.0,
					te - PI * 0.5, te + PI * 0.5)


## The domed portico in front of the hall: columns and arches (cut out by
## the shader), a lead roof, a small dome per bay.
static func _portico(lm, P: Kit.Parts, spec: Dictionary) -> void:
	var pt: Array = spec["portico"]
	var u0: float = pt[0]
	var u1: float = pt[1]
	var v0: float = pt[2] + 0.4
	var v1: float = pt[3] - 0.4
	var o: Vector2 = pt[4]
	var ax: Vector2 = spec["ax"]
	var px := Vector2(-ax.y, ax.x)
	var g: float = spec["g"]
	var g_low: float = spec["g_low"]
	var dep := u1 - u0
	var w := v1 - v0
	var n := maxi(1, int(roundf(w / maxf(dep, 3.0))))
	if n % 2 == 0:
		n += 1
	var bay := w / n
	var spring := clampf(dep * 0.75, 2.8, 5.5)
	var top := g + spring + bay * 0.5 + 1.0
	var corner := func(u: float, v: float) -> Vector2: return o + ax * u + px * v
	# Front arcade and the two ends.
	var pts := PackedVector2Array([corner.call(u1, v0), corner.call(u0, v0), corner.call(u0, v1), corner.call(u1, v1)])
	var ys0 := PackedFloat32Array([g, g, g, g])
	var ys1 := PackedFloat32Array([top, top, top, top])
	P.q = Vector2(bay, spring)
	_arcade_strip(P, pts, ys0, ys1, bay, spring)
	# Platform under it and the roof slab.
	var slab := Kit.rect(o, ax, u0, u1, v0, v1)
	P.prism("marble", slab, g_low, g + 0.05, "marble")
	P.prism("stone", Kit.offset(slab, 0.25), top, top + 0.45, "lead_flat")
	if w > 9.0:
		for k in n:
			var v := v0 + bay * (float(k) + 0.5)
			lm.cupola(Kit.g(corner.call((u0 + u1) * 0.5, v), top + 0.45), minf(bay, dep) * 0.4)


## An arcade face along a polyline (both faces, arches cut out by the shader).
static func _arcade_strip(P: Kit.Parts, pts: PackedVector2Array, y0: PackedFloat32Array,
		y1: PackedFloat32Array, bay: float, spring: float) -> void:
	for i in pts.size() - 1:
		var a := pts[i]
		var b := pts[i + 1]
		var l := (b - a).length()
		if l < 0.5:
			continue
		# Whole bays along each run so arches meet the corners.
		var nb := maxi(1, int(roundf(l / bay)))
		var bb := l / nb
		P.q = Vector2(bb, spring)
		var e := (b - a) / l
		var o := Kit.gd(Vector2(e.y, -e.x))
		var r := Rect2(0.0, 0.0, l, y1[i] - y0[i])
		P.quad("arcade", Kit.g(a, y0[i]), Kit.g(b, y0[i + 1]), Kit.g(b, y1[i + 1]), Kit.g(a, y1[i]), o, r)
		P.quad("arcade", Kit.g(a, y0[i]), Kit.g(b, y0[i + 1]), Kit.g(b, y1[i + 1]), Kit.g(a, y1[i]), -o, r)
		# Columns: thin marble posts at each pier so they read solid in shadow.
		for k in nb + 1:
			var p := a + e * bb * k
			P.frustum("marble", Kit.g(p, y0[i]), 0.28, 0.24, spring, 6, false)
	P.q = Vector2.ZERO


## The courtyard: an outer wall, a domed arcade round the inside, a fountain.
## medrese: the cells are closed rooms (walls with windows both sides).
static func _court(lm, P: Kit.Parts, spec: Dictionary, medrese: bool) -> void:
	var court: PackedVector2Array = spec["court"]
	if Kit.area(court) < 0.0:
		court.reverse()
	var g: float = spec["g"]
	var g_low: float = spec["g_low"]
	var ob := Kit.obb(court)
	var dep := clampf(0.12 * minf(ob[2].x, ob[2].y) * 2.0, 4.0, 7.5)
	if medrese:
		dep = 5.5
	var ah := 5.5 + dep * 0.5 if not medrese else 6.5
	var inner := Kit.offset(court, -dep)
	if inner.size() < 3 or absf(Kit.area(inner)) < 30.0:
		P.prism("stone_win", court, g_low, g + ah, "lead_flat", 3.0)
		return
	if Kit.area(inner) < 0.0:
		inner.reverse()
	# Outer wall (windows outward), the court floor, the inner arcade.
	P.prism("stone_win", court, g_low, g + ah, "", 4.0)
	P.cap("marble", inner, g + 0.08)
	var pts := inner.duplicate()
	pts.append(inner[0])
	var y0 := PackedFloat32Array()
	var y1 := PackedFloat32Array()
	for k in pts.size():
		y0.append(g)
		y1.append(g + ah)
	if medrese:
		P.strip("stone_win", pts, y0, y1, true, 3.0)
	else:
		_arcade_strip(P, pts, y0, y1, dep, ah - dep * 0.5 - 1.0)
	# Roof ring: the outer ring minus the court, as a lead slab.
	var ring := Geometry2D.clip_polygons(court, inner)
	for rp: PackedVector2Array in ring:
		if not Geometry2D.is_polygon_clockwise(rp):
			P.cap("lead_flat", rp, g + ah)
	# Domes along the arcade, one per bay.
	var mid := Kit.offset(court, -dep * 0.5)
	if mid.size() >= 3:
		for k in mid.size():
			var a := mid[k]
			var b := mid[(k + 1) % mid.size()]
			var l := a.distance_to(b)
			var nb := maxi(1, int(roundf(l / dep)))
			for j in nb:
				var p := a.lerp(b, (float(j) + 0.5) / nb)
				lm.cupola(Kit.g(p, g + ah), dep * 0.4)
	# The fountain (sadirvan): an octagonal kiosk with a wide lead roof.
	if not medrese and absf(Kit.area(inner)) > 200.0:
		var fc := Kit.centroid(inner)
		var fr := clampf(sqrt(absf(Kit.area(inner))) * 0.07, 2.0, 4.5)
		P.frustum("marble", Kit.g(fc, g), fr * 0.7, fr * 0.7, 1.0, 8, true)
		for k in 8:
			var t := TAU * k / 8.0
			P.frustum("marble", Kit.g(fc, g) + Vector3(cos(t), 0, sin(t)) * fr * 0.85, 0.15, 0.15, 3.2, 4, false)
		P.frustum("lead_flat", Kit.g(fc, g + 3.2), fr * 1.25, fr * 0.5, fr * 0.35, 8, false)
		P.dome("lead", Kit.g(fc, g + 3.2 + fr * 0.35), fr * 0.5, fr * 0.45, 8, 3, 8.0)
		lm.add_flood(Kit.g(fc, g), fr, 4.0, "fountain")


## A hipped tile roof over a small mosque.
static func _hip(P: Kit.Parts, hall: PackedVector2Array, ax: Vector2, y: float, S: float) -> void:
	var ob := Kit.obb(hall)
	var o: Vector2 = ob[0]
	var a: Vector2 = ob[1]
	var he: Vector2 = ob[2] + Vector2(0.6, 0.6)
	var px := Vector2(-a.y, a.x)
	var hr := minf(he.x, he.y) * 0.62
	var ridge := maxf(he.x - he.y, 0.0) if he.x > he.y else 0.0
	var ra := a if he.x >= he.y else px
	var rb := px if he.x >= he.y else a
	var hl := maxf(he.x, he.y)
	var hw := minf(he.x, he.y)
	var c0 := [o + ra * hl + rb * hw, o - ra * hl + rb * hw, o - ra * hl - rb * hw, o + ra * hl - rb * hw]
	var r0 := Kit.g(o + ra * (hl - hw), y + hr)
	var r1 := Kit.g(o - ra * (hl - hw), y + hr)
	if ridge <= 0.01:
		r1 = r0
	var e := [Kit.g(c0[0], y), Kit.g(c0[1], y), Kit.g(c0[2], y), Kit.g(c0[3], y)]
	var up := Vector3.UP
	P.quad("tile", e[0], e[1], r1, r0, Kit.gd(rb) + up)
	P.quad("tile", e[2], e[3], r0, r1, Kit.gd(-rb) + up)
	P.tri("tile", e[1], e[2], r1, Kit.gd(-ra) + up)
	P.tri("tile", e[3], e[0], r0, Kit.gd(ra) + up)


# ================================================================ minaret

## A pencil minaret: polygonal pedestal, a fluted shaft, 1-3 serefe
## balconies on corbels, a slimmer upper shaft and a tall conical lead cap
## with a gold alem. m: {p, g (bottom), g0 (ground the height counts from),
## h, r, n, style, mat}.
static func minaret(lm, P: Kit.Parts, m: Dictionary, ax: Vector2) -> void:
	var p: Vector2 = m["p"]
	var gb: float = m["g"]
	var g0: float = m["g0"]
	var H: float = m["h"]
	var r: float = m["r"]
	var n: int = m["n"]
	var st: String = m["style"]
	var mat: String = m["mat"]
	var rot := -ax.angle()
	var base := Kit.g(p, gb)
	var top := g0 + H
	var fr: Array = [[0.74], [0.58, 0.76], [0.47, 0.62, 0.77]][clampi(n, 1, 3) - 1]
	var cap_h := H * (0.16 if st != "baroque" else 0.11)
	var hp := clampf(0.15 * H, 3.5, 12.0)
	var y := g0 + hp
	if st == "baroque":
		P.frustum(mat, base, r * 1.35, r * 1.35, y - gb, 8, true, 0.0, 0.0, TAU, rot + PI / 8.0)
		P.frustum("stone", Kit.g(p, y), r * 1.45, r * 1.45, 0.5, 8, true, 0.0, 0.0, TAU, rot + PI / 8.0)
	else:
		# Square pedestal, then the bevelled transition (pabuc) to the shaft.
		P.box(mat, Kit.g(p, (gb + y) * 0.5), Vector3(r * 1.3, (y - gb) * 0.5, r * 1.3), Basis(Vector3.UP, -rot))
		P.frustum(mat, Kit.g(p, y), r * 1.3 * 1.15, r * 1.02, 1.4, 8, false, 0.0, 0.0, TAU, rot + PI / 8.0)
	y += 1.4 if st != "baroque" else 0.5
	var seg := 12 if st != "baroque" else 14
	var top_ser := g0 + H * float(fr[fr.size() - 1])
	var rr := r
	for k in fr.size():
		var hs := g0 + H * float(fr[k])
		var r1 := lerpf(r, r * 0.9, (hs - g0) / H)
		P.frustum(mat, Kit.g(p, y), rr, r1, hs - 1.6 - y, seg, false)
		# Corbel (muqarnas), floor, parapet.
		P.frustum("stone", Kit.g(p, hs - 1.6), r1, r1 + 0.95, 1.6, seg, false)
		P.frustum("stone", Kit.g(p, hs), r1 + 0.95, r1 + 0.95, 0.18, seg, true)
		P.frustum("marble", Kit.g(p, hs + 0.18), r1 + 0.9, r1 + 0.9, 1.0, seg, false)
		lm.balcony(Kit.g(p, hs + 0.2), r1 + 0.9)
		y = hs + 0.18
		rr = r1
		if k < fr.size() - 1:
			y = hs + 0.18
	# Upper shaft above the top balcony, a moulded ring, the cap.
	var ru := rr * 0.84
	var cap0 := top - cap_h
	P.frustum(mat, Kit.g(p, y), ru, ru, cap0 - y, seg, false)
	P.frustum("stone", Kit.g(p, cap0 - 0.35), ru * 1.12, ru * 1.12, 0.35, seg, true)
	P.frustum("lead", Kit.g(p, cap0), ru * 1.12, 0.06, cap_h, seg, false)
	lm.alem(Kit.g(p, top - 0.1), clampf(H * 0.045, 1.0, 3.2))

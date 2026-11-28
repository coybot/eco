## Baylands Park SITE: the limits a policy trained here must respect when it
## flies the real field, as data plus a query API. Added by env_baylands as a
## scenery child (SCENERY list); draws nothing unless --site-overlay.
##
## Reference data for simulation, NOT a legal determination. Before any real
## flight the pilot must check B4UFLY/LAANC, NOTAMs and TFRs, and get
## permission from the park.
##
## DATA: assets/baylands/site.json (drone/sim/tools/fetch_baylands_site.py):
## the park and its wetlands preserve (OSM), hours and rules (City of
## Sunnyvale), airspace classes, UAS Facility Map cells, 14 CFR 99.7 security
## areas, the stadium TFR, FAA obstacle file points (FAA, public domain),
## launch pads chosen from the lidar, NUQ wind climatology (IEM). Everything
## in scene ENU metres; heights in feet AS PUBLISHED and metres
## (1 ft = 0.3048 m). Altitudes 'MSL' = metres above NAVD88.
##
## ENV OPS (IPC {"op":"env_op","name":<op>,"args":{...}}):
##   site        {keys?: [..]} -> site.json (or those keys); pads get
##               ground_env_m (this scene's ground_height there).
##   check       {e, n, alt_agl | alt_msl, tfr_active?} -> which limits apply:
##               airspace class + volumes, UASFM ceiling + LAANC, 99.7 areas,
##               stadium TFR, park / preserve, wire clearance, roofs;
##               violations, warnings, max_agl_m. Same answers as
##               drone/sim/baylands_site.py (which lacks wires and roofs).
##   check_path  {points: [[e, n, alt], ..], alt_ref: "agl"|"msl", step?: 5,
##               tfr_active?} -> spans of each violation along the path (3D
##               metres), first violation, min wire clearance, and the first
##               strike of a leg on terrain/structures/wires (raycast_structures).
##
## OVERLAY (--site-overlay, off by default; debugging only): translucent
## UASFM ceiling plates coloured by ceiling, 99.7 volumes (red), Class C/D
## surface-area walls (magenta/blue), stadium TFR ring (orange), park
## (green) and preserve (cyan) fences, pads (white rings) and FAA obstacle
## file points (yellow sticks to their published AMSL height).
extends Node3D

const SITE := "res://assets/baylands/site.json"
const FT := 0.3048
const PART107_MAX_AGL_M := 121.92
const CLASS_ORDER := "BCDE"

var env: Node
var site: Dictionary = {}
var _park := PackedVector2Array()
var _preserve := PackedVector2Array()
## [{a: Dictionary, polys: Array[Array[PackedVector2Array]], boxes: Array[Rect2]}]
var _air: Array = []
var _sec: Array = []
## [{c: Dictionary, ring: PackedVector2Array, box: Rect2}]
var _cells: Array = []


func _ready() -> void:
	name = "baylands_site"
	env = get_parent()
	var d = JSON.parse_string(FileAccess.get_file_as_string(SITE))
	if not (d is Dictionary):
		push_error("[baylands_site] missing or bad %s; run drone/sim/tools/fetch_baylands_site.py" % SITE)
		return
	site = d
	_park = _pv(site["park"]["ring"])
	_preserve = _pv(site["preserve"]["ring"])
	_air = _volumes(site.get("airspace", []))
	_sec = _volumes(site.get("security", []))
	for c in site["uasfm"]["cells"]:
		var r := _pv(c["ring"])
		_cells.append({"c": c, "ring": r, "box": _box(r)})
	if env != null and env.has_method("register_op"):
		env.register_op("site", _op_site)
		env.register_op("check", _op_check)
		env.register_op("check_path", _op_check_path)
	var overlay := "--site-overlay" in OS.get_cmdline_user_args()
	if overlay:
		_build_overlay()
	print("[baylands_site] %d airspace volumes, %d UASFM cells, %d 99.7 areas, %d pads; ops site/check/check_path; overlay %s"
		% [_air.size(), _cells.size(), _sec.size(), (site.get("pads", []) as Array).size(), "on" if overlay else "off"])


# ================================================================== helpers

func _pv(a: Array) -> PackedVector2Array:
	var out := PackedVector2Array()
	for p in a:
		out.append(Vector2(float(p[0]), float(p[1])))
	return out


func _box(r: PackedVector2Array) -> Rect2:
	var b := Rect2(r[0], Vector2.ZERO)
	for p in r:
		b = b.expand(p)
	return b


func _volumes(src: Array) -> Array:
	var out: Array = []
	for a in src:
		var polys: Array = []
		var boxes: Array = []
		for poly in a["polys"]:
			var rings: Array = []
			for ring in poly:
				rings.append(_pv(ring))
			polys.append(rings)
			boxes.append(_box(rings[0]).grow(0.01))
		out.append({"a": a, "polys": polys, "boxes": boxes})
	return out


func _in_vol(v: Dictionary, p: Vector2) -> bool:
	var polys: Array = v["polys"]
	for k in polys.size():
		if not (v["boxes"][k] as Rect2).has_point(p):
			continue
		var rings: Array = polys[k]
		if not Geometry2D.is_point_in_polygon(p, rings[0]):
			continue
		var hole := false
		for h in range(1, rings.size()):
			if Geometry2D.is_point_in_polygon(p, rings[h]):
				hole = true
				break
		if not hole:
			return true
	return false


## An airspace floor/ceiling -> metres above NAVD88 at this ground.
func _limit_m(lim: Dictionary, ground: float, top: bool) -> float:
	var ref := str(lim.get("ref", "MSL"))
	if ref == "SFC":
		return ground if top else -INF
	if ref == "AGL":
		return ground + float(lim["m"])
	return float(lim["m"])


func _ground(e: float, n: float) -> float:
	return float(env.ground_height(e, n)) if env != null and env.has_method("ground_height") else 0.0


func _cell_at(p: Vector2) -> Dictionary:
	for c in _cells:
		if (c["box"] as Rect2).has_point(p) and Geometry2D.is_point_in_polygon(p, c["ring"]):
			return c["c"]
	return {}


# ===================================================================== ops

func _op_site(args: Dictionary) -> Dictionary:
	var out: Dictionary = site.duplicate(true)
	for p in out.get("pads", []):
		p["ground_env_m"] = snappedf(_ground(float(p["e"]), float(p["n"])), 0.01)
	if args.has("keys"):
		var sub := {"disclaimer": site["disclaimer"], "frame": site["frame"]}
		for k in args["keys"]:
			if out.has(k):
				sub[k] = out[k]
		out = sub
	return {"ok": true, "site": out}


func _op_check(args: Dictionary) -> Dictionary:
	var r := check(float(args.get("e", 0.0)), float(args.get("n", 0.0)),
		args.get("alt_agl", null), args.get("alt_msl", null), bool(args.get("tfr_active", false)))
	return r


func _op_check_path(args: Dictionary) -> Dictionary:
	return check_path(args.get("points", []), str(args.get("alt_ref", "agl")),
		float(args.get("step", 5.0)), bool(args.get("tfr_active", false)))


## Which limits apply at (e, n) and this altitude. Give alt_agl or alt_msl
## (metres; null for the other). See drone/sim/baylands_site.py Site.check.
func check(e: float, n: float, alt_agl = null, alt_msl = null, tfr_active := false) -> Dictionary:
	var g := _ground(e, n)
	var agl := 0.0
	var msl := 0.0
	if alt_msl == null:
		agl = float(alt_agl) if alt_agl != null else 0.0
		msl = g + agl
	else:
		msl = float(alt_msl)
		agl = msl - g
	var p := Vector2(e, n)
	var viol: Array = []
	var warn: Array = []
	var lim_m := PART107_MAX_AGL_M
	var lim_by := "part107"
	if agl < 0.0:
		viol.append({"code": "below_ground", "text": "below the ground (%.1f m AGL)" % agl})

	var vols: Array = []
	for v in _air:
		if not _in_vol(v, p):
			continue
		var a: Dictionary = v["a"]
		var lo := _limit_m(a["floor"], g, false)
		var hi := _limit_m(a["ceiling"], g, true)
		if msl >= lo and msl <= hi:
			vols.append({"name": a["name"], "class": a["class"], "type": a["type"],
				"floor_msl_m": null if lo == -INF else snappedf(lo, 0.1),
				"ceiling_msl_m": snappedf(hi, 0.1), "hours": a.get("hours")})
	var cls := "G"
	for c in CLASS_ORDER:
		var found := false
		for v in vols:
			if v["class"] == c:
				found = true
		if found:
			cls = c
			break
	var controlled := cls in "BCD"
	for v in vols:
		if v["type"] == "CLASS_E2":
			controlled = true

	var um = null
	var cell := _cell_at(p)
	if not cell.is_empty():
		var cm := float(cell["ceiling_m"])
		var over := agl > cm + 1e-6
		var apts: Array = []
		for ap in cell["airports"]:
			apts.append(ap["id"])
		um = {"ceiling_ft": int(cell["ceiling_ft"]), "ceiling_m": cm, "laanc": cell["laanc"],
			"airports": apts, "over": over}
		if cm < lim_m:
			lim_m = cm
			lim_by = "uasfm"
		if over and controlled:
			viol.append({"code": "uasfm_ceiling",
				"text": "above the UAS facility map ceiling %d ft (%.1f m) AGL" % [int(cell["ceiling_ft"]), cm]})
	if controlled:
		if not cell.is_empty() and bool(cell["laanc"]):
			warn.append("controlled airspace (Class %s): FAA authorization needed; LAANC up to %d ft AGL" % [cls, int(cell["ceiling_ft"])])
		else:
			warn.append("controlled airspace (Class %s): FAA authorization needed; no LAANC here (FAA DroneZone)" % cls)
	if agl > PART107_MAX_AGL_M + 1e-6:
		viol.append({"code": "part107_ceiling", "text": "above 400 ft (121.92 m) AGL, 14 CFR 107.51"})

	var sec: Array = []
	for v in _sec:
		if not _in_vol(v, p):
			continue
		var s: Dictionary = v["a"]
		var inside := agl <= float(s["ceiling"]["m"]) + 1e-6
		sec.append({"name": s["name"], "ceiling_ft": s["ceiling"]["ft"], "ceiling_m": s["ceiling"]["m"], "inside": inside})
		if inside:
			lim_m = 0.0
			lim_by = "security"
			viol.append({"code": "security_99_7",
				"text": "inside the 14 CFR 99.7 area %s (surface to %d ft AGL)" % [s["name"], int(s["ceiling"]["ft"])]})

	var tfr: Array = []
	for t in site.get("tfr", []):
		var d := Vector2(float(t["centre"][0]), float(t["centre"][1])).distance_to(p)
		var inside := d <= float(t["radius_m"]) and agl <= float(t["ceiling"]["m"])
		tfr.append({"name": t["name"], "inside": inside, "distance_m": snappedf(d, 0.1), "active": tfr_active})
		if inside and tfr_active:
			lim_m = 0.0
			lim_by = "tfr"
			viol.append({"code": "tfr", "text": "inside the %s stadium TFR (event day)" % t["name"]})
		elif inside:
			warn.append("inside the %s stadium TFR area: prohibited on event days (1 h before to 1 h after)" % t["name"])

	var park := Geometry2D.is_point_in_polygon(p, _park)
	var pres := Geometry2D.is_point_in_polygon(p, _preserve)
	if pres:
		warn.append("over the wetlands preserve: no launch or landing; keep wildlife undisturbed")
	if not park and not pres:
		warn.append("outside Baylands Park")

	# Scene-only: roofs and other solids, and wires.
	var roof := g
	if env != null and env.has_method("collision_height"):
		roof = float(env.collision_height(e, n))
		if msl < roof - 0.05 and roof > g + 0.5:
			viol.append({"code": "inside_structure", "text": "inside a structure (top %.1f m above NAVD88)" % roof})
	var wire = null
	if env != null and env.has_method("line_clearance") and not (env.line_a as PackedVector3Array).is_empty():
		var c := float(env.line_clearance(Vector3(e, n, msl), 100.0))
		if c < INF:
			wire = snappedf(c, 0.01)
			if c <= 0.0:
				viol.append({"code": "wire", "text": "touching a wire or cable"})

	var ll: Array = env.sim_to_geo(p) if env != null and env.has_method("sim_to_geo") else [0.0, 0.0]
	return {"ok": viol.is_empty(), "e": e, "n": n, "lat": ll[0], "lon": ll[1],
		"ground_m": snappedf(g, 0.01), "collision_m": snappedf(roof, 0.01),
		"alt_agl_m": snappedf(agl, 0.01), "alt_msl_m": snappedf(msl, 0.01),
		"alt_agl_ft": snappedf(agl / FT, 0.1), "alt_msl_ft": snappedf(msl / FT, 0.1),
		"airspace": {"class": cls, "controlled": controlled, "volumes": vols},
		"uasfm": um, "security": sec, "tfr": tfr, "park": park, "preserve": pres,
		"park_hours": site["park"]["hours"]["text"] if park else null,
		"wire_clearance_m": wire, "max_agl_m": snappedf(lim_m, 0.01), "max_agl_by": lim_by,
		"violations": viol, "warnings": warn, "disclaimer": site["disclaimer"]}


func check_path(points: Array, alt_ref := "agl", step := 5.0, tfr_active := false) -> Dictionary:
	var pts: Array = []
	for q in points:
		pts.append(Vector3(float(q[0]), float(q[1]), float(q[2])))
	var spans := {}
	var prev := {}
	var first = null
	var strike = null
	var max_agl := -INF
	var min_wire := INF
	var classes := {}
	var zones := {}
	var s_total := 0.0
	step = maxf(step, 0.5)
	for k in pts.size():
		var a: Vector3 = pts[maxi(k - 1, 0)]
		var b: Vector3 = pts[k]
		var seg := a.distance_to(b)
		# One raycast per leg on the scene's solids (terrain, roofs, wires).
		if k > 0 and strike == null and env != null and env.has_method("raycast_structures") and seg > 0.01:
			var a3 := Vector3(a.x, a.y, a.z + (_ground(a.x, a.y) if alt_ref == "agl" else 0.0))
			var b3 := Vector3(b.x, b.y, b.z + (_ground(b.x, b.y) if alt_ref == "agl" else 0.0))
			var l3 := a3.distance_to(b3)
			var t := float(env.raycast_structures(a3, (b3 - a3) / l3, l3))
			if t >= 0.0 and t < l3 - 0.01:
				var hp := a3 + (b3 - a3) / l3 * t
				strike = {"leg": k - 1, "s_m": snappedf(s_total + t * seg / l3, 0.1),
					"e": snappedf(hp.x, 0.1), "n": snappedf(hp.y, 0.1), "z_m": snappedf(hp.z, 0.1)}
		var m := 0 if k == 0 else maxi(1, int(ceil(seg / step)))
		for i in range(0 if k == 0 else 1, m + 1):
			var t := 1.0 if m == 0 else float(i) / float(m)
			var q := a.lerp(b, t)
			var s := s_total + seg * t
			var r := check(q.x, q.y, q.z if alt_ref == "agl" else null, q.z if alt_ref != "agl" else null, tfr_active)
			max_agl = maxf(max_agl, float(r["alt_agl_m"]))
			classes[r["airspace"]["class"]] = true
			if r["preserve"]:
				zones["preserve"] = true
			for x in r["security"]:
				if x["inside"]:
					zones[x["name"]] = true
			if r["wire_clearance_m"] != null:
				min_wire = minf(min_wire, float(r["wire_clearance_m"]))
			var codes := {}
			for v in r["violations"]:
				codes[v["code"]] = true
			for c in codes:
				if not spans.has(c):
					spans[c] = []
				var arr: Array = spans[c]
				if prev.has(c):
					var last: Array = arr[arr.size() - 1]
					last[1] = snappedf(s, 0.1)
				else:
					arr.append([snappedf(s, 0.1), snappedf(s, 0.1)])
			prev = codes
			if not codes.is_empty() and first == null:
				first = {"s_m": snappedf(s, 0.1), "leg": maxi(k - 1, 0), "e": snappedf(q.x, 0.1),
					"n": snappedf(q.y, 0.1), "violations": r["violations"]}
		s_total += seg
	return {"ok": first == null and strike == null, "length_m": snappedf(s_total, 0.1),
		"first_violation": first, "violations": spans, "strike": strike,
		"max_agl_m": snappedf(max_agl, 0.01), "min_wire_clearance_m": null if min_wire == INF else min_wire,
		"classes": classes.keys(), "zones": zones.keys(), "disclaimer": site["disclaimer"]}


# ================================================================ overlay

var _st: SurfaceTool


func _v(e: float, n: float, y: float) -> Vector3:
	return Vector3(e, y, -n)


func _quad(a: Vector3, b: Vector3, c: Vector3, d: Vector3, col: Color) -> void:
	_st.set_color(col)
	for q in [a, b, c, a, c, d]:
		_st.add_vertex(q)


## A ground-following translucent wall along a ring, h0..h1 metres AGL.
func _wall(ring: PackedVector2Array, closed: bool, h0: float, h1: float, col: Color, seg := 40.0) -> void:
	var n := ring.size()
	for k in (n if closed else n - 1):
		var p: Vector2 = ring[k]
		var q: Vector2 = ring[(k + 1) % n]
		var m := maxi(1, int(ceil(p.distance_to(q) / seg)))
		for i in m:
			var u := p.lerp(q, float(i) / m)
			var w := p.lerp(q, float(i + 1) / m)
			var gu := _ground(u.x, u.y)
			var gw := _ground(w.x, w.y)
			_quad(_v(u.x, u.y, gu + h0), _v(w.x, w.y, gw + h0), _v(w.x, w.y, gw + h1), _v(u.x, u.y, gu + h1), col)


## A flat strip on the ground along a ring (outlines that read from above).
func _ribbon(ring: PackedVector2Array, closed: bool, width: float, col: Color, seg := 20.0) -> void:
	var n := ring.size()
	for k in (n if closed else n - 1):
		var p: Vector2 = ring[k]
		var q: Vector2 = ring[(k + 1) % n]
		if p.distance_to(q) < 0.01:
			continue
		var side := (q - p).normalized().orthogonal() * width * 0.5
		var m := maxi(1, int(ceil(p.distance_to(q) / seg)))
		for i in m:
			var u := p.lerp(q, float(i) / m)
			var w := p.lerp(q, float(i + 1) / m)
			var gu := _ground(u.x, u.y) + 0.35
			var gw := _ground(w.x, w.y) + 0.35
			_quad(_v(u.x - side.x, u.y - side.y, gu), _v(w.x - side.x, w.y - side.y, gw),
				_v(w.x + side.x, w.y + side.y, gw), _v(u.x + side.x, u.y + side.y, gu), col)


## A flat translucent plate over a ring at h metres above the ground at its centre.
func _plate(ring: PackedVector2Array, h: float, col: Color) -> void:
	var c := Vector2.ZERO
	for p in ring:
		c += p
	c /= float(ring.size())
	var y := _ground(c.x, c.y) + h
	var tri := Geometry2D.triangulate_polygon(ring)
	_st.set_color(col)
	for k in tri:
		_st.add_vertex(_v(ring[k].x, ring[k].y, y))


## size = text height, metres.
func _label(text: String, e: float, n: float, y: float, col: Color, size := 2.0) -> void:
	var l := Label3D.new()
	l.text = text
	l.position = _v(e, n, y)
	l.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	l.pixel_size = size / 64.0
	l.font_size = 64
	l.outline_size = 12
	l.modulate = col
	l.no_depth_test = true
	add_child(l)


func _ceiling_colour(ft: float) -> Color:
	if ft <= 0.0:
		return Color(1.0, 0.1, 0.1)
	if ft <= 100.0:
		return Color(1.0, 0.45, 0.1)
	if ft <= 200.0:
		return Color(1.0, 0.85, 0.1)
	if ft <= 300.0:
		return Color(0.55, 1.0, 0.2)
	return Color(0.2, 0.85, 1.0)


func _build_overlay() -> void:
	_st = SurfaceTool.new()
	_st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var reach: Rect2 = (env.sim_bounds() as Rect2).grow(300.0) if env.has_method("sim_bounds") else Rect2(-2800, -2700, 5600, 5400)
	# UASFM ceiling plates and their edges.
	for c in _cells:
		if not reach.intersects(c["box"]):
			continue
		var ft := float(c["c"]["ceiling_ft"])
		var col := _ceiling_colour(ft)
		var h := maxf(float(c["c"]["ceiling_m"]), 2.0)
		_plate(c["ring"], h, Color(col, 0.035))
		_wall(c["ring"], true, h - 0.8, h + 0.8, Color(col, 0.6), 200.0)
		_ribbon(c["ring"], true, 2.0, Color(col, 0.45), 100.0)
		var ctr := (c["box"] as Rect2).get_center()
		_label("%d ft%s" % [int(ft), "" if c["c"]["laanc"] else " no LAANC"], ctr.x, ctr.y, _ground(ctr.x, ctr.y) + h + 4.0, col, 22.0)
	# 14 CFR 99.7 volumes.
	for v in _sec:
		var a: Dictionary = v["a"]
		for poly in v["polys"]:
			_wall(poly[0], true, 0.0, float(a["ceiling"]["m"]), Color(1.0, 0.1, 0.1, 0.22))
			_ribbon(poly[0], true, 8.0, Color(1.0, 0.1, 0.1, 0.7))
		var b: Rect2 = v["boxes"][0]
		_label("99.7 " + str(a["name"]), b.get_center().x, b.get_center().y, 140.0, Color(1, 0.3, 0.3), 25.0)
	# Class C/D surface areas.
	for v in _air:
		var a: Dictionary = v["a"]
		if str(a["floor"]["ref"]) != "SFC" or not (str(a["class"]) in "CD"):
			continue
		var col := Color(1.0, 0.2, 1.0, 0.16) if a["class"] == "C" else Color(0.2, 0.5, 1.0, 0.16)
		for poly in v["polys"]:
			_wall(poly[0], true, 0.0, 150.0, col, 60.0)
			_ribbon(poly[0], true, 6.0, Color(col, 0.7), 40.0)
	# Stadium TFR ring (event days only).
	for t in site.get("tfr", []):
		var ring := PackedVector2Array()
		var cen := Vector2(float(t["centre"][0]), float(t["centre"][1]))
		for k in 256:
			var th := TAU * k / 256.0
			ring.append(cen + Vector2(cos(th), sin(th)) * float(t["radius_m"]))
		_wall(ring, true, 0.0, 30.0, Color(1.0, 0.55, 0.1, 0.25), 60.0)
		_ribbon(ring, true, 6.0, Color(1.0, 0.55, 0.1, 0.7), 60.0)
	# Park and preserve fences.
	_wall(_park, true, 0.0, 2.5, Color(0.2, 1.0, 0.3, 0.45), 10.0)
	_wall(_preserve, true, 0.0, 2.5, Color(0.2, 0.95, 1.0, 0.45), 10.0)
	_ribbon(_park, true, 3.0, Color(0.2, 1.0, 0.3, 0.75), 10.0)
	_ribbon(_preserve, true, 3.0, Color(0.2, 0.95, 1.0, 0.75), 10.0)
	# Pads: a white ring, 5 m radius.
	for p in site.get("pads", []):
		var e := float(p["e"])
		var n := float(p["n"])
		var g := _ground(e, n) + 0.08
		var r := float(p.get("radius_m", 5.0))
		for k in 48:
			var a0 := TAU * k / 48.0
			var a1 := TAU * (k + 1) / 48.0
			_quad(_v(e + cos(a0) * r, n + sin(a0) * r, g), _v(e + cos(a1) * r, n + sin(a1) * r, g),
				_v(e + cos(a1) * (r - 0.8), n + sin(a1) * (r - 0.8), g), _v(e + cos(a0) * (r - 0.8), n + sin(a0) * (r - 0.8), g),
				Color(1, 1, 1, 0.9))
		_quad(_v(e - 0.3, n - r, g), _v(e + 0.3, n - r, g), _v(e + 0.3, n + r, g), _v(e - 0.3, n + r, g), Color(1, 1, 1, 0.9))
		_quad(_v(e - r, n - 0.3, g), _v(e + r, n - 0.3, g), _v(e + r, n + 0.3, g), _v(e - r, n + 0.3, g), Color(1, 1, 1, 0.9))
		_label(str(p["id"]), e, n, g + 4.0, Color(1, 1, 1), 4.0)
	# FAA obstacle file: a stick to the published AMSL height.
	for o in site.get("obstacles_dof", []):
		var e := float(o["e"])
		var n := float(o["n"])
		if not reach.has_point(Vector2(e, n)):
			continue
		var g := _ground(e, n)
		var top := float(o["amsl_m"])
		_quad(_v(e - 0.6, n, g), _v(e + 0.6, n, g), _v(e + 0.6, n, top), _v(e - 0.6, n, top), Color(1, 1, 0.1, 0.8))
		_quad(_v(e, n - 0.6, g), _v(e, n + 0.6, g), _v(e, n + 0.6, top), _v(e, n - 0.6, top), Color(1, 1, 0.1, 0.8))
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mat.vertex_color_use_as_albedo = true
	var mi := MeshInstance3D.new()
	mi.name = "site_overlay"
	mi.mesh = _st.commit()
	mi.material_override = mat
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mi)
	_st = null

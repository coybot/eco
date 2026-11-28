## The birds of Baylands Park and the salt ponds around it (a part of
## baylands_life.gd). Don Edwards San Francisco Bay National Wildlife Refuge
## is next door; for a drone here birds are a real collision hazard and a
## real detector distractor, so they are placed by habitat and they react.
##
## WHO AND WHERE (habitat samples baked from the lidar water mask and OSM by
## bake_baylands_life.py; counts are plausible numbers for a South Bay day,
## scaled by season from the clock's day of year):
##   * Canada geese: flocks grazing the Twin Creeks fields and the park's
##     lawns, loafing on the ponds and sloughs; they commute between them in
##     V's at 12-45 m, so a V crosses the park every few minutes.
##   * American white pelicans: pods floating on the open salt ponds.
##   * Mallards: rafts on the ponds and sloughs (many more in winter).
##   * American avocets and black-necked stilts: feeding along pond edges.
##   * Great and snowy egrets, great blue herons: stalking the slough and
##     marsh edges, alone or in twos.
##   * Gulls: wheeling over the transfer station, the water pollution control
##     plant's ponds and the landfill (circling, shader-flown), and resting in
##     rafts on the ponds.
##   * Swallows: hawking insects low over the meadow, marsh and pond edges
##     (spring and summer).
##   * A red-tailed hawk or two soaring over the grassland.
##
## BEHAVIOUR. A resting flock sits until an aircraft comes inside its flush
## distance (30-50 m, by species: published drone-approach flight-initiation
## distances for waterbirds are mostly 20-60 m), or until its own timer sends
## it somewhere else. Then the birds take off one after another (a second
## or so apart), climb away from the aircraft, form up (V's for geese and
## pelicans, loose rafts for ducks and gulls, a tight swirl for shorebirds)
## and fly at their species' speed to another site of their habitat, where
## they glide in, fold their wings and settle.
##
## NOT SOLID. A bird is not a wall: flying into one is a bird strike, not a
## structure strike. Every tick (10 Hz) each aircraft is checked against the
## birds near it; a hit within half the bird's span (plus 0.5 m of airframe)
## is recorded as a "bird_strike" event (species, mass, relative speed) in the
## life events (env_op "life") and, for fixed-wing/VTOL aircraft, in
## FixedWingManager's event log. What a strike does to the aircraft is the
## training code's call.
##
## COST: one MultiMesh per species (9 draws); a resting bird costs nothing on
## the CPU; a flying flock writes its birds' transforms every frame.
extends Node3D

const BM = preload("res://scripts/baylands_life_meshes.gd")

enum { REST, TAKEOFF, FLY, LAND }

## flush: 3D distance (m) at which an aircraft flushes the flock; speed: cruise
## (m/s); alt: cruise height above the ground (m); form: flight formation;
## gap: V spacing (m); rest: habitats it settles on; moves: s between its own
## moves; groups/size: flocks and birds per flock before the season factor.
const BEHAVIOUR := {
	"goose": {"flush": 45.0, "speed": 17.0, "alt": [12.0, 45.0], "form": "v", "gap": 2.4,
		"rest": ["field", "pond_water", "slough_water"], "moves": [180.0, 700.0], "groups": 9, "size": [10, 55]},
	"pelican": {"flush": 50.0, "speed": 13.0, "alt": [15.0, 60.0], "form": "v", "gap": 3.8,
		"rest": ["open_water"], "moves": [900.0, 3000.0], "groups": 4, "size": [8, 35]},
	"duck": {"flush": 45.0, "speed": 20.0, "alt": [8.0, 30.0], "form": "cluster", "gap": 1.6,
		"rest": ["pond_water", "slough_water"], "moves": [600.0, 2400.0], "groups": 10, "size": [6, 30]},
	"shorebird": {"flush": 30.0, "speed": 14.0, "alt": [3.0, 14.0], "form": "swirl", "gap": 0.9,
		"rest": ["pond_edge"], "moves": [400.0, 1800.0], "groups": 8, "size": [15, 60]},
	"egret": {"flush": 30.0, "speed": 9.0, "alt": [5.0, 20.0], "form": "loose", "gap": 6.0,
		"rest": ["slough_edge", "pond_edge"], "moves": [300.0, 1500.0], "groups": 30, "size": [1, 2]},
	"heron": {"flush": 35.0, "speed": 9.5, "alt": [6.0, 25.0], "form": "loose", "gap": 6.0,
		"rest": ["slough_edge", "marsh"], "moves": [600.0, 3000.0], "groups": 8, "size": [1, 1]},
	"gull": {"flush": 30.0, "speed": 10.0, "alt": [8.0, 30.0], "form": "cluster", "gap": 2.2,
		"rest": ["open_water", "pond_edge"], "moves": [300.0, 1200.0], "groups": 4, "size": [15, 60]},
}
const FLOATERS := ["goose", "duck", "pelican", "gull"]
const SHADOW_SPECIES := ["goose", "pelican", "egret", "heron"]
## --life-noshadow: no shadows from people or birds (for a laptop GPU).
static var NO_SHADOW: bool = "--life-noshadow" in OS.get_cmdline_user_args()
## Waterfowl and gulls at rest face into the wind: the bay's afternoon
## sea breeze is from the north-west (~300 deg true) most of the year.
const WIND_FROM_DEG := 300.0
const DRAW_M := 2500.0

var env: Node3D
var data: Dictionary = {}
var on := false
var _built := false
var _rng := RandomNumberGenerator.new()
var _clock := 0.0
var _doy := 196
var _blocked_rects: Array = []              # [centre, angle, L, W] fields in use (soccer)

var _mm := {}                               # species -> MultiMesh
var _mmi := {}
var _circ_mm := {}                          # gull / swallow / hawk
var _circ_mmi := {}
var _sites := {}                            # habitat -> Array of Vector3 (e, n, extra)
var groups: Array = []
var circles: Array = []                     # {sp, origin: Vector3 godot, cu: PackedFloat32Array (4 per bird), n}
var events: Array = []
var _strike_cool := {}                      # aircraft id + bird key -> clock


func setup(env_node: Node3D, life: Dictionary) -> void:
	env = env_node
	data = life.get("birds", {})
	name = "birds"
	_rng.seed = 0xB1AD5


func set_on(want: bool) -> void:
	if want and not _built:
		_build()
	on = want and _built
	for k in _mmi:
		(_mmi[k] as MultiMeshInstance3D).visible = on
	for k in _circ_mmi:
		(_circ_mmi[k] as MultiMeshInstance3D).visible = on


func set_day(doy: int) -> void:
	_doy = doy


func set_blocked_fields(rects: Array) -> void:
	_blocked_rects = rects


func count() -> int:
	if not on:
		return 0
	var n := 0
	for g: Dictionary in groups:
		n += int(g["n"])
	for c: Dictionary in circles:
		n += int(c["n"])
	return n


func counts() -> Dictionary:
	var out := {}
	if not on:
		return out
	for g: Dictionary in groups:
		out[g["sp"]] = int(out.get(g["sp"], 0)) + int(g["n"])
	for c: Dictionary in circles:
		var k: String = c["sp"] + ("" if c["sp"] != "gull" else "_circling")
		out[k] = int(out.get(k, 0)) + int(c["n"])
	return out


func flying_groups() -> int:
	var n := 0
	for g: Dictionary in groups:
		if g["state"] != REST:
			n += 1
	return n


# ------------------------------------------------------------------ season

func _season(sp: String) -> float:
	var m := int(float(_doy - 1) / 30.5) + 1          # month 1..12
	var winter := m >= 11 or m <= 3
	match sp:
		"duck":
			return 1.0 if winter else (0.6 if m in [4, 10] else 0.35)
		"pelican":
			return 1.0 if m in [8, 9, 10, 11] else (0.7 if winter else 0.5)
		"swallow":
			return 1.0 if m in [3, 4, 5, 6, 7, 8] else (0.5 if m == 9 else 0.12)
		"goose":
			return 1.2 if winter else 1.0
		"shorebird":
			return 1.0 if winter else 0.75
		"gull":
			return 1.15 if winter else 1.0
	return 1.0


# ------------------------------------------------------------------ build

func _build() -> void:
	var t0 := Time.get_ticks_msec()
	for hab in data:
		var arr: Array = []
		for p: Array in data[hab]:
			arr.append(Vector3(float(p[0]), float(p[1]), float(p[2]) if p.size() > 2 else 0.0))
		_sites[hab] = arr
	var per_sp := {}
	for sp: String in BEHAVIOUR:
		var b: Dictionary = BEHAVIOUR[sp]
		var ng := maxi(1, int(round(float(b["groups"]) * minf(_season(sp), 1.0))))
		var used: Array = []
		for gi in ng:
			var hab: String = b["rest"][_rng.randi_range(0, (b["rest"] as Array).size() - 1)]
			var site := _pick_site(sp, [hab], Vector2.ZERO, used, -1.0)
			if site.is_empty():
				continue
			used.append(Vector2(site["p"].x, site["p"].y))
			var sz: Array = b["size"]
			var n := maxi(1, int(round(float(_rng.randi_range(int(sz[0]), int(sz[1]))) * _season(sp))))
			var g := {"sp": sp, "n": n, "state": REST, "site": site, "start": int(per_sp.get(sp, 0)),
				"P": Vector3.ZERO, "head": 0.0, "v": 0.0, "t0": 0.0, "target": {}, "alt": 20.0,
				"next": _rng.randf_range(20.0, float(b["moves"][1])), "rest": PackedVector3Array(),
				"yaw": PackedFloat32Array(), "slots": PackedVector3Array(), "delay": PackedFloat32Array(),
				"pos": PackedVector3Array(), "var": PackedFloat32Array(), "seed": PackedFloat32Array(),
				"float": PackedByteArray(), "from": PackedVector3Array(), "bank": 0.0, "threat": ""}
			per_sp[sp] = int(per_sp.get(sp, 0)) + n
			groups.append(g)
	for sp: String in per_sp:
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.use_custom_data = true
		mm.mesh = BM.bird_mesh(sp)
		mm.instance_count = int(per_sp[sp])
		mm.custom_aabb = AABB(Vector3(-6000, -20, -6000), Vector3(12000, 400, 12000))
		_mm[sp] = mm
		var mmi := MultiMeshInstance3D.new()
		mmi.name = "birds_" + sp
		mmi.multimesh = mm
		mmi.material_override = BM.bird_material(sp, false)
		# Shadows for the big birds only: a goose's or a pelican's shadow on
		# the grass or the water reads from the air; a duck's barely does.
		mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON \
			if sp in SHADOW_SPECIES and not NO_SHADOW else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		mmi.visibility_range_end = DRAW_M
		mmi.visible = false
		add_child(mmi)
		_mmi[sp] = mmi
	for g: Dictionary in groups:
		_init_group(g)
	_build_circles()
	_built = true
	print("[baylands_life] birds: %d flocks (%s), %d circling, %d ms" % [groups.size(),
		str(per_sp), circles.reduce(func(a, c): return a + int(c["n"]), 0), Time.get_ticks_msec() - t0])


func _init_group(g: Dictionary) -> void:
	var n: int = g["n"]
	var sp: String = g["sp"]
	var b: Dictionary = BEHAVIOUR[sp]
	g["slots"] = _formation(b, n)
	var d := PackedFloat32Array()
	var vr := PackedFloat32Array()
	var sd := PackedFloat32Array()
	d.resize(n)
	vr.resize(n)
	sd.resize(n)
	for k in n:
		d[k] = _rng.randf_range(0.0, 1.6) if n > 1 else 0.0
		sd[k] = _rng.randf()
		match sp:
			"egret":
				vr[k] = 1.0 if _rng.randf() < 0.35 else 0.0
			"duck":
				vr[k] = 1.0 if _rng.randf() < 0.45 else 0.0
			"shorebird":
				vr[k] = 1.0 if _rng.randf() < 0.4 else 0.0
			_:
				vr[k] = 0.0
	g["delay"] = d
	g["var"] = vr
	g["seed"] = sd
	g["pos"].resize(n)
	_settle(g, g["site"])
	# A few flocks are already on the move when the scene starts, so a
	# fresh frame shows geese in the air.
	if sp == "goose" and _rng.randf() < 0.3:
		_takeoff(g, Vector3.INF, "")
		g["t0"] -= 6.0
		g["state"] = FLY


## Formation offsets in the flock's frame: x right, y up, z forward (m).
func _formation(b: Dictionary, n: int) -> PackedVector3Array:
	var out := PackedVector3Array()
	var gap: float = b["gap"]
	match str(b["form"]):
		"v":
			for k in n:
				var i := (k + 1) / 2
				var side := 1.0 if k % 2 == 1 else -1.0
				var wave := i / 14
				var j := i - wave * 14
				out.append(Vector3(side * (float(j) * gap * 0.8 + float(wave) * 3.0),
					-0.12 * float(j) + _rng.randf_range(-0.3, 0.3),
					-float(j) * gap * 0.75 - float(wave) * 26.0 + _rng.randf_range(-0.4, 0.4)))
		"loose":
			for k in n:
				out.append(Vector3(float(k) * gap * 0.7, _rng.randf_range(-1.0, 1.0), -float(k) * gap * 0.7))
		_:
			var r := gap * sqrt(float(n)) * (0.7 if b["form"] == "swirl" else 1.0)
			for k in n:
				out.append(Vector3(_rng.randfn(0.0, r * 0.6), _rng.randfn(0.0, r * 0.25), _rng.randfn(0.0, r * 0.8)))
	return out


## A site for `sp` on one of `habs`, weighted toward the park (where the
## cameras are), away from `avoid` sites; `from`/`min_d` keep it away from a
## flock's old site or a threat. {} if none.
func _pick_site(sp: String, habs: Array, from: Vector2, avoid: Array, min_d: float,
		threat: Vector2 = Vector2.INF) -> Dictionary:
	var best := {}
	var best_w := -1.0
	for attempt in 40:
		var hab: String = habs[_rng.randi_range(0, habs.size() - 1)]
		var arr: Array = _sites.get(hab, [])
		if arr.is_empty():
			continue
		var s: Vector3 = arr[_rng.randi_range(0, arr.size() - 1)]
		var p := Vector2(s.x, s.y)
		if min_d > 0.0 and (p.distance_to(from) < min_d or p.distance_to(from) > min_d * 6.0):
			continue
		var bad := false
		for q: Vector2 in avoid:
			if q.distance_to(p) < 120.0:
				bad = true
				break
		if bad or _in_blocked(p):
			continue
		var w := 1.0 / (1.0 + p.length() / 900.0) * _rng.randf_range(0.5, 1.0)
		if threat != Vector2.INF:
			var away := (from - threat).normalized()
			w *= 1.2 + (p - from).normalized().dot(away)
		if w > best_w:
			best_w = w
			best = {"p": s, "hab": hab}
	return best


func _in_blocked(p: Vector2) -> bool:
	for r: Array in _blocked_rects:
		var c: Vector2 = r[0]
		var a: float = r[1]
		var d := (p - c).rotated(-a)
		if absf(d.x) < float(r[2]) * 0.5 + 15.0 and absf(d.y) < float(r[3]) * 0.5 + 15.0:
			return true
	return false


## Rest positions round a site: grazing geese spread on the grass, rafts on
## the water, waders along the edge. Writes the birds' transforms.
func _settle(g: Dictionary, site: Dictionary) -> void:
	g["site"] = site
	g["state"] = REST
	var sp: String = g["sp"]
	var n: int = g["n"]
	var hab: String = site["hab"]
	var c := Vector2(site["p"].x, site["p"].y)
	var on_water := hab in ["open_water", "pond_water", "slough_water"]
	var spread := sqrt(float(n)) * (2.2 if sp == "goose" and not on_water else
		(1.8 if on_water else (1.3 if sp == "shorebird" else 3.0)))
	var wind := deg_to_rad(90.0 - WIND_FROM_DEG)      # ENU angle the wind comes FROM
	var rest := PackedVector3Array()
	var yaw := PackedFloat32Array()
	var fl := PackedByteArray()
	rest.resize(n)
	yaw.resize(n)
	fl.resize(n)
	var base := _rng.randf_range(-PI, PI)
	for k in n:
		var p := c
		for tries in 12:
			p = c + Vector2(_rng.randfn(0.0, spread), _rng.randfn(0.0, spread)) if n > 1 else c
			var wet: bool = env.call("is_water", p.x, p.y)
			if (on_water and wet) or (not on_water and (not wet or sp in ["egret", "heron", "shorebird"])):
				break
		var wet: bool = env.call("is_water", p.x, p.y)
		var z := _surface(p, wet)
		var floats := wet and sp in FLOATERS
		if wet and not floats:
			z -= 0.12                      # wading
		rest[k] = Vector3(p.x, p.y, z)
		var h: float
		if floats:
			h = wind + _rng.randf_range(-0.5, 0.5)
		elif sp == "goose":
			h = base + _rng.randf_range(-0.9, 0.9)
		else:
			h = _rng.randf_range(-PI, PI)
		yaw[k] = h
		fl[k] = 1 if floats else 0
	g["rest"] = rest
	g["yaw"] = yaw
	g["float"] = fl
	g["P"] = Vector3(c.x, c.y, _surface(c, on_water))
	var feed := 0.1
	if sp == "shorebird" or (sp == "goose" and not on_water):
		feed = 0.8
	elif sp in ["egret", "heron"]:
		feed = 0.5
	elif sp == "duck":
		feed = 0.3
	g["feed"] = feed
	_write_rest(g)


## Writes a resting flock's birds where they stand.
func _write_rest(g: Dictionary) -> void:
	g["state"] = REST
	var mm: MultiMesh = _mm[g["sp"]]
	var rest: PackedVector3Array = g["rest"]
	for k in int(g["n"]):
		var i: int = g["start"] + k
		g["pos"][k] = rest[k]
		mm.set_instance_transform(i, _xf(rest[k], g["yaw"][k], 0.0, _scale(g, k)))
		mm.set_instance_custom_data(i, Color(0.0, 0.0, float(g["feed"]),
			float(g["var"][k]) + 4.0 * float(g["float"][k]) + g["seed"][k] * 0.99))


func _scale(g: Dictionary, k: int) -> float:
	var s := 1.0 + (float(g["seed"][k]) - 0.5) * 0.12
	if g["sp"] == "egret" and float(g["var"][k]) > 0.5:
		s *= 0.62                          # snowy egret
	if g["sp"] == "shorebird" and float(g["var"][k]) > 0.5:
		s *= 0.88                          # stilt
	return s


func _surface(p: Vector2, wet: bool) -> float:
	if wet and env.has_method("water_level"):
		return float(env.call("water_level", p.x, p.y))
	return float(env.call("ground_height", p.x, p.y))


## Godot transform for a bird at ENU p heading h (rad, CCW from east), banked.
static func _xf(p: Vector3, h: float, bank: float, s: float) -> Transform3D:
	var b := Basis(Vector3.UP, atan2(cos(h), -sin(h))) * Basis(Vector3.BACK, bank)
	return Transform3D(b.scaled(Vector3(s, s, s)), Vector3(p.x, p.z, -p.y))


func _build_circles() -> void:
	var specs := [
		# [species, habitats, zones, birds per zone, radius m, speed m/s, AGL m]
		["gull", ["landfill"], 4, [20, 50], [9.0, 55.0], [7.0, 11.0], [8.0, 45.0]],
		["gull", ["open_water"], 2, [15, 35], [12.0, 60.0], [7.0, 11.0], [6.0, 35.0]],
		["swallow", ["field", "marsh", "pond_edge"], 8, [6, 16], [5.0, 22.0], [8.0, 12.0], [2.5, 12.0]],
		["hawk", ["field", "marsh"], 2, [1, 1], [30.0, 60.0], [9.0, 12.0], [50.0, 150.0]],
	]
	var per := {}
	var tmp: Array = []
	for s: Array in specs:
		var sp: String = s[0]
		var zones := maxi(1, int(round(float(s[2]) * minf(_season(sp), 1.0))))
		var used: Array = []
		for z in zones:
			var site := _pick_site(sp, s[1], Vector2.ZERO, used, -1.0)
			if site.is_empty():
				continue
			var c := Vector2(site["p"].x, site["p"].y)
			used.append(c)
			var n := maxi(1, int(round(float(_rng.randi_range(int(s[3][0]), int(s[3][1]))) * _season(sp))))
			var g := float(env.call("ground_height", c.x, c.y))
			var cu := PackedFloat32Array()
			var org := PackedVector3Array()
			for k in n:
				var r := _rng.randf_range(float(s[4][0]), float(s[4][1]))
				var v := _rng.randf_range(float(s[5][0]), float(s[5][1]))
				var w := v / r * (1.0 if _rng.randf() < 0.5 else -1.0)
				var off := Vector2(_rng.randfn(0.0, 40.0), _rng.randfn(0.0, 40.0)) if sp != "hawk" else Vector2.ZERO
				var y := g + _rng.randf_range(float(s[6][0]), float(s[6][1]))
				org.append(Vector3(c.x + off.x, y, -(c.y + off.y)))
				cu.append_array([r, w, _rng.randf_range(0.0, TAU), _rng.randf()])
			tmp.append({"sp": sp, "c": c, "n": n, "org": org, "cu": cu, "start": int(per.get(sp, 0)),
				"reach": float(s[4][1]) * 1.7 + 60.0})
			per[sp] = int(per.get(sp, 0)) + n
	for sp: String in per:
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.use_custom_data = true
		mm.mesh = BM.bird_mesh(sp)
		mm.instance_count = int(per[sp])
		mm.custom_aabb = AABB(Vector3(-6000, -20, -6000), Vector3(12000, 500, 12000))
		_circ_mm[sp] = mm
		var mmi := MultiMeshInstance3D.new()
		mmi.name = "circling_" + sp
		mmi.multimesh = mm
		mmi.material_override = BM.bird_material(sp, true)
		mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		mmi.visibility_range_end = DRAW_M
		mmi.visible = false
		add_child(mmi)
		_circ_mmi[sp] = mmi
	for c: Dictionary in tmp:
		var mm: MultiMesh = _circ_mm[c["sp"]]
		var cu: PackedFloat32Array = c["cu"]
		for k in int(c["n"]):
			var i: int = c["start"] + k
			mm.set_instance_transform(i, Transform3D(Basis(), c["org"][k]))
			mm.set_instance_custom_data(i, Color(cu[k * 4], cu[k * 4 + 1], cu[k * 4 + 2], cu[k * 4 + 3]))
		circles.append(c)


## Where circling bird (origin o, custom cu) is at time t, Godot space. The
## same formula as BIRD_SHADER's circle_pos().
static func circle_pos(o: Vector3, cu: Array, t: float, swallow: bool) -> Vector3:
	var seed: float = fposmod(float(cu[3]), 1.0)
	var r: float = float(cu[0]) * (1.0 + 0.25 * sin(t * 0.11 + seed * 20.0))
	var a: float = float(cu[2]) + float(cu[1]) * t
	var c := Vector3(sin(t * 0.021 + seed * 6.0), 0.0, cos(t * 0.017 + seed * 9.0)) * float(cu[0]) * 0.7
	var p := c + Vector3(cos(a) * r, 3.0 * sin(t * 0.23 + seed * 13.0), sin(a) * r)
	if swallow:
		p += Vector3(sin(t * 1.7 + seed * 5.0) * 4.0, sin(t * 2.3 + seed * 3.0) * 1.5, cos(t * 1.9 + seed * 7.0) * 4.0)
	return o + p


# ------------------------------------------------------------------ behaviour

## Lift off from the site, away from `threat` (ENU) if there is one.
func _takeoff(g: Dictionary, threat: Vector3, by: String) -> void:
	var sp: String = g["sp"]
	var b: Dictionary = BEHAVIOUR[sp]
	var c := Vector2(g["P"].x, g["P"].y)
	var t2 := Vector2.INF if threat == Vector3.INF else Vector2(threat.x, threat.y)
	var target := _pick_site(sp, b["rest"], c, [], 250.0 if sp not in ["egret", "heron"] else 120.0, t2)
	if target.is_empty():
		target = _pick_site(sp, b["rest"], c, [], -1.0, t2)
	if target.is_empty():
		target = g["site"]
	g["target"] = target
	var dir: Vector2
	if t2 != Vector2.INF:
		dir = (c - t2).normalized()
		if dir.length() < 0.5:
			dir = Vector2.RIGHT
	else:
		dir = (Vector2(target["p"].x, target["p"].y) - c).normalized()
		if dir.length() < 0.5:
			dir = Vector2.RIGHT.rotated(_rng.randf_range(-PI, PI))
	g["head"] = atan2(dir.y, dir.x)
	g["v"] = 2.0
	g["alt"] = _rng.randf_range(float(b["alt"][0]), float(b["alt"][1]))
	g["t0"] = _clock
	g["state"] = TAKEOFF
	g["threat"] = by
	g["from"] = g["rest"].duplicate()
	if by != "":
		events.append({"t": snappedf(_clock, 0.1), "kind": "flush", "species": sp, "birds": g["n"],
			"e": snappedf(c.x, 0.1), "n": snappedf(c.y, 0.1), "by": by})


func step(dt: float, clock: float, aircraft: Array) -> void:
	if not on:
		return
	_clock = clock
	for g: Dictionary in groups:
		match int(g["state"]):
			REST:
				g["next"] = float(g["next"]) - dt
				if float(g["next"]) <= 0.0:
					var b: Dictionary = BEHAVIOUR[g["sp"]]
					g["next"] = _rng.randf_range(float(b["moves"][0]), float(b["moves"][1]))
					_takeoff(g, Vector3.INF, "")
			_:
				_fly(g, dt)


## Aircraft (from baylands_life at 10 Hz): [{id, p: Vector3 ENU, v: Vector3}].
func check_aircraft(aircraft: Array) -> void:
	if not on or aircraft.is_empty():
		return
	for g: Dictionary in groups:
		var b: Dictionary = BEHAVIOUR[g["sp"]]
		var P: Vector3 = g["P"]
		for a: Dictionary in aircraft:
			var ap: Vector3 = a["p"]
			var d := ap.distance_to(P)
			if int(g["state"]) == REST and d < float(b["flush"]) + sqrt(float(g["n"])) * 1.5:
				_takeoff(g, ap, str(a["id"]))
				break
			if d < 90.0:
				_strike_group(g, a)
	for c: Dictionary in circles:
		var cc: Vector2 = c["c"]
		for a: Dictionary in aircraft:
			var ap: Vector3 = a["p"]
			if Vector2(ap.x, ap.y).distance_to(cc) > float(c["reach"]) + 60.0:
				continue
			var cu: PackedFloat32Array = c["cu"]
			var tnow := fposmod(_clock, 3600.0)
			var half: float = float(BM.SPECIES[c["sp"]]["span"]) * 0.5
			for k in int(c["n"]):
				var q := circle_pos(c["org"][k], [cu[k * 4], cu[k * 4 + 1], cu[k * 4 + 2], cu[k * 4 + 3]], tnow,
					c["sp"] == "swallow")
				var qe := Vector3(q.x, -q.z, q.y)
				if qe.distance_to(ap) < half * 0.7 + 0.5:
					_strike(a, c["sp"], qe, "%s_c%d_%d" % [c["sp"], circles.find(c), k], Vector3.ZERO)


func _strike_group(g: Dictionary, a: Dictionary) -> void:
	var half: float = float(BM.SPECIES[g["sp"]]["span"]) * 0.5
	var ap: Vector3 = a["p"]
	var vel := Vector3(cos(float(g["head"])), sin(float(g["head"])), 0.0) * float(g["v"]) \
		if int(g["state"]) != REST else Vector3.ZERO
	for k in int(g["n"]):
		var q: Vector3 = g["pos"][k]
		var r := half * 0.7 + 0.5 if int(g["state"]) != REST else 0.6
		if q.distance_to(ap) < r:
			_strike(a, g["sp"], q, "%s_%d_%d" % [g["sp"], groups.find(g), k], vel)


func _strike(a: Dictionary, sp: String, q: Vector3, key: String, bird_v: Vector3) -> void:
	var k := str(a["id"]) + "/" + key
	if _clock - float(_strike_cool.get(k, -100.0)) < 5.0:
		return
	_strike_cool[k] = _clock
	var av: Vector3 = a.get("v", Vector3.ZERO)
	var ev := {"t": snappedf(_clock, 0.01), "kind": "bird_strike", "aircraft": str(a["id"]), "species": sp,
		"mass_kg": BM.SPECIES[sp]["mass"], "e": snappedf(q.x, 0.01), "n": snappedf(q.y, 0.01),
		"z": snappedf(q.z, 0.01), "rel_speed_mps": snappedf((av - bird_v).length(), 0.1)}
	events.append(ev)
	var fwm := get_node_or_null("/root/FixedWingManager")
	if fwm != null and str(a.get("src", "")) == "fw":
		fwm.call("log_event", str(a["id"]), "bird_strike", ev)
	print("[baylands_life] BIRD STRIKE %s hit a %s (%.1f kg) at ENU (%.1f, %.1f, %.1f), %.1f m/s" % [
		a["id"], sp, float(ev["mass_kg"]), q.x, q.y, q.z, float(ev["rel_speed_mps"])])


func _fly(g: Dictionary, dt: float) -> void:
	var sp: String = g["sp"]
	var b: Dictionary = BEHAVIOUR[sp]
	var t := _clock - float(g["t0"])
	var state := int(g["state"])
	var cruise: float = b["speed"]
	var P: Vector3 = g["P"]
	var tp: Vector3 = g["target"]["p"]
	var to := Vector2(tp.x, tp.y) - Vector2(P.x, P.y)
	var dist := to.length()
	var ground := float(env.call("ground_height", P.x, P.y))
	var old_head: float = g["head"]
	if state == TAKEOFF or state == FLY:
		g["v"] = minf(cruise, float(g["v"]) + dt * cruise / 3.5)
		# Turn toward the target once clear of the site (1.5 s after takeoff).
		if t > 1.5 and dist > 1.0:
			var want := atan2(to.y, to.x)
			var turn := clampf(wrapf(want - old_head, -PI, PI), -0.45 * dt, 0.45 * dt)
			g["head"] = old_head + turn
		var tz := _surface(Vector2(tp.x, tp.y), g["target"]["hab"] in ["open_water", "pond_water", "slough_water"])
		var alt_want := ground + float(g["alt"])
		# Glide down over the last stretch (about 1 in 6).
		var glide := tz + dist / 6.0
		alt_want = minf(alt_want, maxf(glide, tz + 1.5))
		var climb := clampf(alt_want - P.z, -2.5 * dt, (2.8 if t < 8.0 else 2.0) * dt)
		P.z += climb
		if state == TAKEOFF and t > 5.0:
			g["state"] = FLY
		if dist < 18.0 and P.z - tz < 4.0 and t > 6.0:
			g["state"] = LAND
			g["t0"] = _clock
			var landing: Dictionary = g["target"]
			# The new rest spots, without writing them yet: _settle() at the end.
			var keep_pos: PackedVector3Array = g["pos"].duplicate()
			_settle(g, landing)
			g["landing_rest"] = g["rest"].duplicate()
			g["state"] = LAND
			g["from"] = keep_pos
			g["P"] = P
			g["t0"] = _clock
			_write_flock(g, 0.0)
			return
	elif state == LAND:
		g["v"] = maxf(0.0, float(g["v"]) - dt * cruise / 3.0)
		if t > 4.5:
			_write_rest(g)
			return
	var h: float = g["head"]
	var spd: float = g["v"]
	P.x += cos(h) * spd * dt
	P.y += sin(h) * spd * dt
	g["P"] = P
	var yr := wrapf(h - old_head, -PI, PI) / maxf(dt, 1e-3)
	g["bank"] = lerpf(float(g["bank"]), clampf(atan(yr * spd / 9.8), -0.6, 0.6), minf(1.0, dt * 3.0))
	_write_flock(g, dt)


## Each bird of a flying flock: from where it was (its rest spot, or its
## slot when landing) to its formation slot round P, and back down.
func _write_flock(g: Dictionary, dt: float) -> void:
	var sp: String = g["sp"]
	var n: int = g["n"]
	var mm: MultiMesh = _mm[sp]
	var t := _clock - float(g["t0"])
	var state := int(g["state"])
	var P: Vector3 = g["P"]
	var h: float = g["head"]
	var fwd := Vector3(cos(h), sin(h), 0.0)
	var right := Vector3(sin(h), -cos(h), 0.0)
	var up := Vector3(0, 0, 1)
	var slots: PackedVector3Array = g["slots"]
	var swirl: bool = BEHAVIOUR[sp]["form"] == "swirl"
	for k in n:
		var s := slots[k]
		var seed: float = g["seed"][k]
		var wob := Vector3(sin(_clock * 0.9 + seed * 30.0), sin(_clock * 1.3 + seed * 17.0) * 0.5,
			cos(_clock * 0.7 + seed * 11.0)) * (2.5 if swirl else 0.25)
		var air := P + right * s.x + up * s.y + fwd * s.z + Vector3(wob.x, wob.z, wob.y)
		var pos: Vector3
		var open := 1.0
		var amp := 1.0
		var yaw := h
		if state == TAKEOFF or state == FLY:
			var f := smoothstep(0.0, 3.5, t - float(g["delay"][k]))
			var from: Vector3 = g["from"][k] if k < (g["from"] as PackedVector3Array).size() else air
			pos = from.lerp(air, f)
			open = smoothstep(0.0, 0.5, t - float(g["delay"][k]))
			amp = 1.0 if (state == TAKEOFF or swirl or sp in ["duck", "shorebird"]) else \
				(0.25 + 0.75 * smoothstep(0.55, 0.7, sin(_clock * 0.4 + seed * 9.0)))
			if pos.z < float(g["from"][k].z) + 0.05:
				pos.z = float(g["from"][k].z)
			if f < 0.15 and t < float(g["delay"][k]):
				yaw = g["yaw"][k]
		else:
			var f := smoothstep(0.0, 4.0, t - float(g["delay"][k]) * 0.5)
			var dest: Vector3 = g["landing_rest"][k]
			var from: Vector3 = g["from"][k]
			pos = from.lerp(dest, f)
			open = 1.0 - smoothstep(0.75, 1.0, f)
			amp = 0.5 * (1.0 - f) + (0.6 if f > 0.7 and f < 0.95 else 0.0)
			if f > 0.95:
				yaw = g["yaw"][k]
		g["pos"][k] = pos
		var i: int = g["start"] + k
		var bank := float(g["bank"]) * open
		mm.set_instance_transform(i, _xf(pos, yaw, bank, _scale(g, k)))
		var fl := 4.0 * float(g["float"][k]) if (state == LAND and open < 0.05) else 0.0
		mm.set_instance_custom_data(i, Color(open, amp, 0.0, float(g["var"][k]) + fl + seed * 0.99))


func set_shadows(want: bool) -> void:
	for sp in _mmi:
		(_mmi[sp] as MultiMeshInstance3D).cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON \
			if (want and sp in SHADOW_SPECIES) else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF


func set_clock_uniform(now: float) -> void:
	for m: ShaderMaterial in BM.all_bird_materials():
		m.set_shader_parameter("now", now)


# ------------------------------------------------------------------ tests

## The nearest flock of `sp` to p (ENU) put in the air there, flying
## `heading` (rad, CCW from east) at `alt` m above the ground: a flock across
## an aircraft's path on demand (env_op life {"flight": ...}).
func force_flight(sp: String, p: Vector2, heading: float, alt: float) -> Dictionary:
	var g := _nearest_group(sp, p)
	if g.is_empty():
		return {"ok": false, "error": "no %s flock" % sp}
	var ground := float(env.call("ground_height", p.x, p.y))
	g["P"] = Vector3(p.x, p.y, ground + alt)
	g["head"] = heading
	g["v"] = float(BEHAVIOUR[sp]["speed"])
	g["alt"] = alt
	g["t0"] = _clock - 20.0
	g["state"] = FLY
	# Its destination: the site of its habitat nearest a point 900 m ahead.
	var far := Vector2(p.x, p.y) + Vector2(cos(heading), sin(heading)) * 900.0
	var tgt := {}
	var bd := INF
	for hab: String in BEHAVIOUR[sp]["rest"]:
		for s: Vector3 in _sites.get(hab, []):
			var d := Vector2(s.x, s.y).distance_to(far)
			if d < bd:
				bd = d
				tgt = {"p": s, "hab": hab}
	g["target"] = tgt if not tgt.is_empty() else g["site"]
	var air := PackedVector3Array()
	air.resize(int(g["n"]))
	g["from"] = air
	_write_flock(g, 0.0)
	for k in int(g["n"]):
		g["from"][k] = g["pos"][k]
	return {"ok": true, "species": sp, "birds": g["n"]}


## The nearest flock of `sp` to p settled on its habitat nearest p.
func force_rest(sp: String, p: Vector2) -> Dictionary:
	var g := _nearest_group(sp, p)
	if g.is_empty():
		return {"ok": false, "error": "no %s flock" % sp}
	var best := {}
	var bd := INF
	for hab: String in BEHAVIOUR[sp]["rest"]:
		for s: Vector3 in _sites.get(hab, []):
			var d := Vector2(s.x, s.y).distance_to(p)
			if d < bd and not _in_blocked(Vector2(s.x, s.y)):
				bd = d
				best = {"p": s, "hab": hab}
	if best.is_empty():
		return {"ok": false, "error": "no site"}
	_settle(g, best)
	g["next"] = 600.0
	return {"ok": true, "species": sp, "birds": g["n"], "at": [best["p"].x, best["p"].y], "habitat": best["hab"]}


func _nearest_group(sp: String, p: Vector2) -> Dictionary:
	var best := {}
	var bd := INF
	for g: Dictionary in groups:
		if g["sp"] != sp:
			continue
		var d := Vector2(g["P"].x, g["P"].y).distance_to(p)
		if d < bd:
			bd = d
			best = g
	return best


# ------------------------------------------------------------------ truth

## Birds within `radius` of (e, n): [{class, species, e, n, z, heading, speed, size}].
func actors_near(c: Vector2, radius: float, out: Array) -> void:
	if not on:
		return
	for gi in groups.size():
		var g: Dictionary = groups[gi]
		var P: Vector3 = g["P"]
		if Vector2(P.x, P.y).distance_to(c) > radius + 200.0:
			continue
		var s: Dictionary = BM.SPECIES[g["sp"]]
		var flying := int(g["state"]) != REST
		for k in int(g["n"]):
			var q: Vector3 = g["pos"][k]
			if Vector2(q.x, q.y).distance_to(c) > radius:
				continue
			var sc := _scale(g, k)
			out.append({"class": "bird", "species": _species_name(g["sp"], float(g["var"][k])),
				"id": "%s_%d_%d" % [g["sp"], gi, k], "flying": flying,
				"e": snappedf(q.x, 0.01), "n": snappedf(q.y, 0.01), "z": snappedf(q.z, 0.01),
				"heading": snappedf(float(g["head"]) if flying else float(g["yaw"][k]), 0.01),
				"speed": snappedf(float(g["v"]) if flying else 0.0, 0.1),
				"size": [snappedf(float(s["rl"]) * 2.6 * sc, 0.01), snappedf(float(s["span"]) * sc * (1.0 if flying else 0.3), 0.01),
					snappedf((float(s["leg"]) + float(s["rh"]) * 2.0 + float(s["neck"][0])) * sc, 0.01)]})
	var tnow := fposmod(_clock, 3600.0)
	for ci in circles.size():
		var cz: Dictionary = circles[ci]
		if (cz["c"] as Vector2).distance_to(c) > radius + float(cz["reach"]):
			continue
		var cu: PackedFloat32Array = cz["cu"]
		var s: Dictionary = BM.SPECIES[cz["sp"]]
		for k in int(cz["n"]):
			var arr := [cu[k * 4], cu[k * 4 + 1], cu[k * 4 + 2], cu[k * 4 + 3]]
			var q := circle_pos(cz["org"][k], arr, tnow, cz["sp"] == "swallow")
			var q2 := circle_pos(cz["org"][k], arr, tnow + 0.1, cz["sp"] == "swallow")
			var e := Vector3(q.x, -q.z, q.y)
			if Vector2(e.x, e.y).distance_to(c) > radius:
				continue
			var v := (q2 - q) / 0.1
			out.append({"class": "bird", "species": cz["sp"], "id": "%s_c%d_%d" % [cz["sp"], ci, k], "flying": true,
				"e": snappedf(e.x, 0.01), "n": snappedf(e.y, 0.01), "z": snappedf(e.z, 0.01),
				"heading": snappedf(atan2(-v.z, v.x), 0.01), "speed": snappedf(v.length(), 0.1),
				"size": [snappedf(float(s["rl"]) * 2.6, 0.01), snappedf(float(s["span"]), 0.01), 0.2]})


static func _species_name(sp: String, v: float) -> String:
	match sp:
		"goose":
			return "canada_goose"
		"egret":
			return "snowy_egret" if v > 0.5 else "great_egret"
		"heron":
			return "great_blue_heron"
		"pelican":
			return "american_white_pelican"
		"duck":
			return "mallard"
		"shorebird":
			return "black_necked_stilt" if v > 0.5 else "american_avocet"
	return sp


## A sample flock position (for pointing a camera): the first flying goose
## flock, else any.
func sample() -> Array:
	for g: Dictionary in groups:
		if int(g["state"]) != REST and g["sp"] == "goose":
			var P: Vector3 = g["P"]
			return [P.x, P.y, P.z, g["sp"]]
	for g: Dictionary in groups:
		var P: Vector3 = g["P"]
		return [P.x, P.y, P.z, g["sp"]]
	return []

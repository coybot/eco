## Istanbul's trams (a part of istanbul_life.gd), on roads.json's rails:
##   T1  Kabatas - Bagcilar: Kabatas, Karakoy, the Galata Bridge, Eminonu,
##       Sirkeci, Gulhane, Sultanahmet, Divan Yolu, Beyazit, Aksaray and west.
##       Low-floor trams in coupled pairs (2 x 3 modules, ~58 m).
##   T5  Eminonu - Alibeykoy along the Golden Horn: single 3-module trams.
##   T2  the nostalgic tram on Istiklal (Taksim - Tunel): one red-and-cream car.
##   T3  the nostalgic Kadikoy - Moda loop: two such cars.
##
## Lines are rebuilt from the OSM rail ways by name: each line's ways are
## joined end to end into tracks (crossovers and depot spurs are unnamed and
## left out). Two tracks of about the same length are a double-track line:
## the route runs out on one and back on the other, right-hand running as in
## Turkey. One track that closes on itself is a loop (Moda); one that does not
## is a shuttle (Istiklal), the car reversing at each end.
##
## Stops are OSM's tram stops (life.json) on the route: a tram brakes into
## each, dwells, and pulls away; it keeps a block's distance behind the tram
## ahead. Heights: the drawn ground; on the Galata Bridge the deck (the same
## profile the road traffic uses: interpolated, lifted over the water, then
## put on whatever solid deck the bridges script built); in a tunnel, out of
## sight below the ground.
##
## Each module is an instance in one of four MultiMeshes (modern cab / cab
## turned round / middle module, heritage car) drawn with the vehicle shader,
## and a kinematic box on collision layer 1: an aircraft hits a tram.
extends Node3D

const LM = preload("res://scripts/life_meshes.gd")
const IM = preload("res://scripts/istanbul_life_meshes.gd")

const LAYER_STRUCTURE := 1
const SNAP := 3.0
const STEP := 8.0                           # route resampled every STEP m
const GAP := 0.25                           # between modules
## line -> [name prefix, style, modules per train, trains (0: by headway),
##          headway s, cruise m/s, dwell s]
const LINES := {
	"T1": ["T1", "modern", 6, 0, 300.0, 10.0, 22.0],
	"T5": ["T5", "modern", 3, 0, 600.0, 10.0, 20.0],
	"T2": ["T2", "heritage", 1, 1, 0.0, 4.0, 40.0],
	"T3": ["", "heritage", 1, 2, 0.0, 5.0, 30.0],
}
## The Moda loop is unnamed in OSM: its ways are found by place.
const MODA_BOX := Rect2(3300.0, -2800.0, 900.0, 900.0)
const DRAW_M := 2500.0

var env: Node3D
var roads
var on := false
var routes: Array = []        # {line, pts PackedVector2Array, y PackedFloat32Array, cum, len, loop, shuttle, stops, style, modules, cruise, dwell}
var trains: Array = []        # {route, s, v, dir, wait, next_stop, body: [RIDs], insts: [[mm_index, slot]]}
var _mm: Array[MultiMesh] = []
var _mmi: Array[MultiMeshInstance3D] = []
var _mm_n := PackedInt32Array()
var _shape_modern: RID
var _shape_heritage: RID
var _clock := 0.0


func setup(env_node: Node3D, road_data) -> void:
	env = env_node
	roads = road_data
	name = "trams"


func _exit_tree() -> void:
	for t: Dictionary in trains:
		for r: RID in t["bodies"]:
			PhysicsServer3D.free_rid(r)
	for r in [_shape_modern, _shape_heritage]:
		if r.is_valid():
			PhysicsServer3D.free_rid(r)
	trains.clear()


func set_on(want: bool) -> void:
	if want and routes.is_empty():
		_build()
	on = want and not routes.is_empty()
	for m in _mmi:
		m.visible = on
	var space := get_world_3d().space
	for t: Dictionary in trains:
		for r: RID in t["bodies"]:
			PhysicsServer3D.body_set_space(r, space if on else RID())


func count() -> int:
	return trains.size() if on else 0


func modules() -> int:
	var n := 0
	for t: Dictionary in trains:
		n += (t["bodies"] as Array).size()
	return n if on else 0


## [line, e, n, y] of every train, for pointing a camera.
func samples() -> Array:
	var out: Array = []
	for t: Dictionary in trains:
		var r: Dictionary = routes[t["route"]]
		var p := _at(r, float(t["s"]))
		out.append([r["line"], snappedf(p.x, 0.1), snappedf(p.y, 0.1), snappedf(p.z, 0.1)])
	return out


func sample() -> Array:
	if trains.is_empty():
		return []
	var t: Dictionary = trains[0]
	var r: Dictionary = routes[t["route"]]
	var p := _at(r, float(t["s"]))
	return [p.x, p.y, p.z]


# ------------------------------------------------------------------ build

func _build() -> void:
	var t0 := Time.get_ticks_msec()
	var doc = JSON.parse_string(FileAccess.get_file_as_string("res://assets/istanbul/roads.json"))
	if not (doc is Dictionary):
		return
	var stops: Array = []
	var lf := FileAccess.open("res://assets/istanbul/life.json", FileAccess.READ)
	if lf != null:
		var l = JSON.parse_string(lf.get_as_text())
		if l is Dictionary:
			stops = l.get("tram_stops", [])
	for line: String in LINES:
		var spec: Array = LINES[line]
		var ways: Array = []
		for r: Dictionary in doc.get("rails", []):
			if str(r.get("type", "")) != "tram":
				continue
			var nm := str(r.get("name", ""))
			var pts: Array = r.get("pts", [])
			if pts.size() < 2:
				continue
			var mine := false
			if str(spec[0]) != "":
				mine = nm.begins_with(str(spec[0]))
			else:
				mine = nm == "" and MODA_BOX.has_point(Vector2(float(pts[0][0]), float(pts[0][1])))
			if mine:
				ways.append(r)
		var tracks := _tracks(ways)
		if tracks.is_empty():
			continue
		var route := _route(line, tracks)
		if route.is_empty():
			continue
		route["stops"] = _stops_on(route, stops)
		routes.append(route)
	refine_decks(get_world_3d().direct_space_state)
	_build_render()
	_spawn()
	print("[istanbul_life] trams: %s, %d trains, %d modules (%d ms)" % [
		", ".join(routes.map(func(r): return "%s %.1f km %d stops" % [r["line"], float(r["len"]) / 1000.0,
			(r["stops"] as PackedFloat32Array).size()])), trains.size(), modules(), Time.get_ticks_msec() - t0])


## Join a line's ways into tracks: polylines whose ends meet within SNAP.
## Each entry: {"pts": PackedVector2Array, "kind": PackedByteArray (0 ground,
## 1 bridge, 2 tunnel)}. Longest chains first.
func _tracks(ways: Array) -> Array:
	var segs: Array = []
	for w: Dictionary in ways:
		var pts := PackedVector2Array()
		for p: Array in w["pts"]:
			pts.append(Vector2(float(p[0]), float(p[1])))
		segs.append({"pts": pts, "kind": 2 if bool(w.get("tunnel", false)) else (1 if bool(w.get("bridge", false)) else 0)})
	var used := {}
	var out: Array = []
	# Greedy chaining from each unused segment, both ways.
	for i in segs.size():
		if used.has(i):
			continue
		used[i] = true
		var pts: PackedVector2Array = (segs[i]["pts"] as PackedVector2Array).duplicate()
		var kinds := PackedByteArray()
		kinds.resize(pts.size())
		kinds.fill(int(segs[i]["kind"]))
		for end in 2:
			var grew := true
			while grew:
				grew = false
				var tip: Vector2 = pts[pts.size() - 1] if end == 0 else pts[0]
				var best := -1
				var best_d := SNAP
				var flip := false
				for j in segs.size():
					if used.has(j):
						continue
					var sp: PackedVector2Array = segs[j]["pts"]
					var d0 := sp[0].distance_to(tip)
					var d1 := sp[sp.size() - 1].distance_to(tip)
					if d0 < best_d:
						best_d = d0
						best = j
						flip = false
					if d1 < best_d:
						best_d = d1
						best = j
						flip = true
				if best < 0:
					break
				used[best] = true
				var add: PackedVector2Array = (segs[best]["pts"] as PackedVector2Array).duplicate()
				var k := int(segs[best]["kind"])
				if end == 0:
					if flip:
						add.reverse()
					for p in add.slice(1):
						pts.append(p)
						kinds.append(k)
				else:
					if not flip:
						add.reverse()
					var np := add.slice(0, add.size() - 1)
					var nk := PackedByteArray()
					nk.resize(np.size())
					nk.fill(k)
					np.append_array(pts)
					nk.append_array(kinds)
					pts = np
					kinds = nk
				grew = true
		out.append({"pts": pts, "kind": kinds, "len": _length(pts)})
	out.sort_custom(func(a, b): return float(a["len"]) > float(b["len"]))
	return out


static func _length(pts: PackedVector2Array) -> float:
	var l := 0.0
	for k in range(1, pts.size()):
		l += pts[k].distance_to(pts[k - 1])
	return l


## The route a line's trams run.
func _route(line: String, tracks: Array) -> Dictionary:
	var spec: Array = LINES[line]
	var a: Dictionary = tracks[0]
	var pts: PackedVector2Array = a["pts"]
	var kinds: PackedByteArray = a["kind"]
	var loop := false
	var shuttle := false
	if tracks.size() >= 2 and float(tracks[1]["len"]) > float(a["len"]) * 0.6:
		var b: Dictionary = tracks[1]
		var bp: PackedVector2Array = (b["pts"] as PackedVector2Array).duplicate()
		var bk: PackedByteArray = (b["kind"] as PackedByteArray).duplicate()
		# Right-hand running: out on the track that has the other on its left.
		var mid := pts.size() / 2
		var dir := (pts[mini(mid + 1, pts.size() - 1)] - pts[maxi(mid - 1, 0)]).normalized()
		var q := _closest(bp, pts[mid])
		if dir.cross(q - pts[mid]) < 0.0:
			pts = pts.duplicate()
			pts.reverse()
			kinds = kinds.duplicate()
			kinds.reverse()
		if bp[0].distance_to(pts[pts.size() - 1]) > bp[bp.size() - 1].distance_to(pts[pts.size() - 1]):
			bp.reverse()
			bk.reverse()
		pts = pts + bp
		kinds = kinds + bk
		loop = true
	elif pts[0].distance_to(pts[pts.size() - 1]) < 40.0:
		loop = true
	else:
		shuttle = true
	# Resample every STEP m with heights.
	var rp := PackedVector2Array()
	var rk := PackedByteArray()
	for k in range(pts.size() - 1):
		var p0 := pts[k]
		var p1 := pts[k + 1]
		var n := maxi(1, int(ceil(p0.distance_to(p1) / STEP)))
		for j in n:
			rp.append(p0.lerp(p1, float(j) / float(n)))
			rk.append(kinds[k + 1] if kinds[k] != kinds[k + 1] and j > n / 2 else kinds[k])
	rp.append(pts[pts.size() - 1])
	rk.append(kinds[kinds.size() - 1])
	var ys := _heights(rp, rk)
	var cum := PackedFloat32Array()
	cum.resize(rp.size())
	for k in range(1, rp.size()):
		cum[k] = cum[k - 1] + rp[k].distance_to(rp[k - 1])
	var total := cum[cum.size() - 1] + (rp[0].distance_to(rp[rp.size() - 1]) if loop else 0.0)
	return {"line": line, "pts": rp, "y": ys, "cum": cum, "len": total, "loop": loop, "shuttle": shuttle,
		"style": spec[1], "modules": int(spec[2]), "trains": int(spec[3]), "headway": float(spec[4]),
		"cruise": float(spec[5]), "dwell": float(spec[6])}


static func _closest(pts: PackedVector2Array, p: Vector2) -> Vector2:
	var best := pts[0]
	for k in range(pts.size() - 1):
		var q := Geometry2D.get_closest_point_to_segment(p, pts[k], pts[k + 1])
		if q.distance_squared_to(p) < best.distance_squared_to(p):
			best = q
	return best


## Rail heights: the ground; a bridge run interpolated between its ends and
## lifted over the water (as the road decks), then put on a solid deck if one
## is there; a tunnel run well under the ground.
func _heights(pts: PackedVector2Array, kinds: PackedByteArray) -> PackedFloat32Array:
	var ys := PackedFloat32Array()
	ys.resize(pts.size())
	for k in pts.size():
		ys[k] = roads.surface_y(pts[k].x, pts[k].y)
		if kinds[k] == 2:
			ys[k] -= 12.0
	var k := 0
	while k < pts.size():
		if kinds[k] != 1:
			k += 1
			continue
		var j := k
		while j < pts.size() and kinds[j] == 1:
			j += 1
		var y0 := ys[maxi(k - 1, 0)]
		var y1 := ys[mini(j, pts.size() - 1)]
		for m in range(k, j):
			var t := float(m - k + 1) / float(j - k + 1)
			var y := lerpf(y0, y1, t)
			if env.call("is_water", pts[m].x, pts[m].y):
				y = maxf(y, roads.DECK_ABOVE_SEA)
			ys[m] = y
		k = j
	# Smooth the bridge ramps and the tunnel portals a little.
	for pass_i in 3:
		var sm := ys.duplicate()
		for m in range(1, pts.size() - 1):
			if kinds[m] != 0 or kinds[m - 1] != kinds[m + 1]:
				sm[m] = (ys[m - 1] + ys[m] * 2.0 + ys[m + 1]) * 0.25
		ys = sm
	return ys


## Put bridge stretches on the solid deck the bridges script built, if any.
func refine_decks(space: PhysicsDirectSpaceState3D) -> void:
	if space == null:
		return
	var q := PhysicsRayQueryParameters3D.new()
	q.collision_mask = 1
	for r: Dictionary in routes:
		var pts: PackedVector2Array = r["pts"]
		var ys: PackedFloat32Array = r["y"]
		for k in pts.size():
			if not env.call("is_water", pts[k].x, pts[k].y):
				continue
			q.from = Vector3(pts[k].x, ys[k] + 25.0, -pts[k].y)
			q.to = Vector3(pts[k].x, -2.0, -pts[k].y)
			var hit := space.intersect_ray(q)
			if not hit.is_empty() and (hit["position"] as Vector3).y > 0.5:
				ys[k] = (hit["position"] as Vector3).y
		r["y"] = ys


## Arc positions of the line's stops (OSM stops within 18 m of the route; two
## stop nodes of one station, one per track, both count on their own track).
func _stops_on(route: Dictionary, stops: Array) -> PackedFloat32Array:
	var pts: PackedVector2Array = route["pts"]
	var cum: PackedFloat32Array = route["cum"]
	var out := PackedFloat32Array()
	for s: Array in stops:
		var p := Vector2(float(s[0]), float(s[1]))
		var best := 18.0
		var at := -1.0
		for k in range(pts.size() - 1):
			var q := Geometry2D.get_closest_point_to_segment(p, pts[k], pts[k + 1])
			var dd := q.distance_to(p)
			if dd < best:
				best = dd
				at = cum[k] + pts[k].distance_to(q)
		if at >= 0.0:
			out.append(at)
	out.sort()
	# One stop per station per track: drop near-duplicates.
	var dedup := PackedFloat32Array()
	for v in out:
		if dedup.is_empty() or v - dedup[dedup.size() - 1] > 40.0:
			dedup.append(v)
	return dedup


func _build_render() -> void:
	var meshes := [IM.tram_modern("cab"), IM.tram_modern("cab_rear"), IM.tram_modern("mid"), IM.tram_heritage()]
	for k in meshes.size():
		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.use_custom_data = true
		mm.mesh = meshes[k]
		mm.instance_count = 400
		mm.visible_instance_count = 0
		mm.custom_aabb = AABB(Vector3(-15000, -60, -15000), Vector3(30000, 400, 30000))
		_mm.append(mm)
		_mm_n.append(0)
		var mmi := MultiMeshInstance3D.new()
		mmi.name = "tram_%d" % k
		mmi.multimesh = mm
		mmi.material_override = LM.material()
		mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
		mmi.physics_interpolation_mode = Node.PHYSICS_INTERPOLATION_MODE_OFF
		mmi.visible = false
		add_child(mmi)
		_mmi.append(mmi)
	_shape_modern = PhysicsServer3D.box_shape_create()
	PhysicsServer3D.shape_set_data(_shape_modern, Vector3(IM.MODERN_W, 3.6, IM.MODERN_L) * 0.5)
	_shape_heritage = PhysicsServer3D.box_shape_create()
	PhysicsServer3D.shape_set_data(_shape_heritage, Vector3(IM.HERITAGE_W, 3.5, IM.HERITAGE_L) * 0.5)


func _spawn() -> void:
	var rng := RandomNumberGenerator.new()
	rng.seed = 1453
	for ri in routes.size():
		var r: Dictionary = routes[ri]
		var n: int = r["trains"]
		if n <= 0:
			n = maxi(1, int(round(float(r["len"]) / (float(r["headway"]) * float(r["cruise"]) * 0.55))))
		var modern: bool = r["style"] == "modern"
		var mod_l: float = IM.MODERN_L if modern else IM.HERITAGE_L
		var nm: int = r["modules"]
		var train_l := nm * mod_l + (nm - 1) * GAP
		if not bool(r["loop"]):
			n = 1                           # a shuttle: one car on the single track
		for k in n:
			var s := float(r["len"]) * (float(k) + rng.randf_range(0.0, 0.3)) / float(n)
			if bool(r["shuttle"]):
				s = clampf(s, train_l, float(r["len"]) - 1.0)
			var t := {"route": ri, "s": s, "v": 0.0, "dir": 1.0, "wait": rng.randf_range(0.0, 10.0),
				"len": train_l, "mod_l": mod_l, "bodies": [], "slots": []}
			for m in nm:
				var mi := 3
				if modern:
					# Each unit has a cab at both ends; a coupled pair meets cab
					# to cab. A cab at a unit's back is turned round (index 1:
					# its lamps are tail lamps).
					var unit_m := m % 3 if nm == 6 else m
					var unit_n := 3 if nm == 6 else nm
					mi = 0 if unit_m == 0 else (1 if unit_m == unit_n - 1 else 2)
				var slot := _mm_n[mi]
				_mm_n[mi] += 1
				t["slots"].append([mi, slot, mi == 1])
				var body := PhysicsServer3D.body_create()
				PhysicsServer3D.body_set_mode(body, PhysicsServer3D.BODY_MODE_KINEMATIC)
				PhysicsServer3D.body_add_shape(body, _shape_modern if modern else _shape_heritage,
					Transform3D(Basis(), Vector3(0.0, 1.8, 0.0)))
				PhysicsServer3D.body_set_collision_layer(body, LAYER_STRUCTURE)
				PhysicsServer3D.body_set_collision_mask(body, 0)
				t["bodies"].append(body)
			t["paint"] = LM.pack_rgb(IM.TRAM_WHITE if modern else IM.HERITAGE_RED)
			trains.append(t)
	for k in _mm.size():
		_mm[k].visible_instance_count = _mm_n[k]


# ------------------------------------------------------------------ frame

func step(dt: float) -> void:
	if not on:
		return
	_clock += dt
	for ti in trains.size():
		_drive(ti, dt)
		_place(ti)


func _drive(ti: int, dt: float) -> void:
	var t: Dictionary = trains[ti]
	var r: Dictionary = routes[t["route"]]
	var L: float = r["len"]
	var s: float = t["s"]
	var v: float = t["v"]
	var dirn: float = t["dir"]
	if float(t["wait"]) > 0.0:
		t["wait"] = float(t["wait"]) - dt
		t["v"] = 0.0
		return
	# Distance to the next stop (or the end of a shuttle run) ahead.
	var gap := INF
	var stops: PackedFloat32Array = r["stops"]
	for st in stops:
		var ahead: float = (st - s) * dirn
		if bool(r["loop"]) and ahead < 0.0:
			ahead += L
		if ahead > 0.5 and ahead < gap:
			gap = ahead
	if bool(r["shuttle"]):
		var end_gap: float = (L - 1.0 - s) if dirn > 0.0 else (s - 1.0)
		gap = minf(gap, maxf(end_gap, 0.0))
	# Keep a block's distance behind the train ahead on a loop.
	var follow := INF
	if bool(r["loop"]):
		for tj in trains.size():
			if tj == ti or int(trains[tj]["route"]) != int(t["route"]):
				continue
			var ahead: float = fposmod(float(trains[tj]["s"]) - s, L)
			if ahead > 0.0:
				follow = minf(follow, ahead - float(trains[tj]["len"]) - 40.0)
	var stop_at := minf(gap, maxf(follow, 0.0))
	var cruise: float = r["cruise"]
	var want := minf(cruise, sqrt(maxf(0.0, 2.0 * 1.0 * (stop_at - 0.5))))
	var a := clampf((want - v) / maxf(dt, 0.02), -1.3, 1.0)
	v = maxf(0.0, v + a * dt)
	var move := minf(v * dt, maxf(stop_at, 0.0))
	s += move * dirn
	if gap < 1.0 and v < 0.4 and gap <= follow:
		# At a stop (or a shuttle's end): dwell, then go on (or reverse).
		t["wait"] = float(r["dwell"]) * randf_range(0.8, 1.25)
		v = 0.0
		s += gap * dirn
		if bool(r["shuttle"]) and ((dirn > 0.0 and s >= L - 2.0) or (dirn < 0.0 and s <= 2.0)):
			# Reverse: the car's other end leads.
			s = s - float(t["len"]) * dirn
			t["dir"] = -dirn
	if bool(r["loop"]):
		s = fposmod(s, L)
	t["s"] = s
	t["v"] = v


## Point on route r at arc s: Vector3(e, n, y).
func _at(r: Dictionary, s: float) -> Vector3:
	var pts: PackedVector2Array = r["pts"]
	var ys: PackedFloat32Array = r["y"]
	var cum: PackedFloat32Array = r["cum"]
	var L: float = r["len"]
	if bool(r["loop"]):
		s = fposmod(s, L)
	else:
		s = clampf(s, 0.0, cum[cum.size() - 1])
	var k := cum.bsearch(s, true) - 1
	if k >= pts.size() - 1:
		# Closing stretch of a loop (last point back to the first).
		var span := L - cum[cum.size() - 1]
		var f := (s - cum[cum.size() - 1]) / maxf(span, 0.01)
		var p := pts[pts.size() - 1].lerp(pts[0], f)
		return Vector3(p.x, p.y, lerpf(ys[ys.size() - 1], ys[0], f))
	k = maxi(k, 0)
	var seg := cum[k + 1] - cum[k]
	var f := (s - cum[k]) / maxf(seg, 0.01)
	var p := pts[k].lerp(pts[k + 1], f)
	return Vector3(p.x, p.y, lerpf(ys[k], ys[k + 1], f))


## Each module between its two bogie points along the track behind the head.
func _place(ti: int) -> void:
	var t: Dictionary = trains[ti]
	var r: Dictionary = routes[t["route"]]
	var dirn: float = t["dir"]
	var head: float = t["s"]
	var ml: float = t["mod_l"]
	var slots: Array = t["slots"]
	var bodies: Array = t["bodies"]
	var paint: float = t["paint"]
	for m in slots.size():
		var front := head - dirn * (float(m) * (ml + GAP) + ml * 0.15)
		var back := head - dirn * (float(m) * (ml + GAP) + ml * 0.85)
		var pf := _at(r, front)
		var pb := _at(r, back)
		var c := (pf + pb) * 0.5
		var fwd := Vector2(pf.x - pb.x, pf.y - pb.y)
		var yaw := atan2(fwd.x, -fwd.y)
		var flat := maxf(fwd.length(), 0.1)
		var pitch := atan2(pf.z - pb.z, flat)
		var sl: Array = slots[m]
		# A cab at the back of its unit faces backwards.
		if bool(sl[2]):
			yaw += PI
			pitch = -pitch
		var xf := Transform3D(Basis(Vector3.UP, yaw) * Basis(Vector3.RIGHT, -pitch), Vector3(c.x, c.z, -c.y))
		var mm := _mm[int(sl[0])]
		mm.set_instance_transform(int(sl[1]), xf)
		mm.set_instance_custom_data(int(sl[1]), Color(paint, 0.0, 0.0, 1.0))
		PhysicsServer3D.body_set_state(bodies[m], PhysicsServer3D.BODY_STATE_TRANSFORM, xf)


func set_night(_n: float) -> void:
	pass

## People in and around Baylands Park (a part of baylands_life.gd): walkers,
## joggers, cyclists and dog walkers on the park's paths and the Bay Trail
## levees, families picnicking at the real picnic tables and shelters, people
## sitting and lying out on the meadow, soccer games and practices on the
## Twin Creeks fields, and a few lunchtime walkers on the business park's
## sidewalks. No kites: kite flying is banned in this park.
##
## WHERE: the path network, picnic points, soccer pitches and open grass are
## all OpenStreetMap / lidar (life.json, bake_baylands_life.py).
##
## HOW MANY, BY THE CLOCK (world_clock's hour and date; a weekend vs a
## weekday). Plausible numbers for a South Bay shoreline park, not counts:
##   * levee trails: ~14 people per km on a weekend afternoon, ~5 on a weekday;
##   * park paths: ~45 per km weekend, ~14 weekday;
##   * picnic spots: about half taken on a weekend from 11 to 17 (groups of
##     4-12), a few on a weekday (2-5);
##   * soccer: most fields in use 8-18 on weekends (11 or 9 a side by the
##     field's size, a referee, families on the touchline), about half the
##     fields with youth practices 16:30-20:30 on weekdays;
##   * the park opens at 8 and closes at sunset; early joggers use the levees.
## Mode split on the levees: 40 % walkers, 22 % joggers, 28 % cyclists,
## 10 % with a dog. Speeds: walking 1.2-1.5 m/s, jogging 2.6-3.4 m/s,
## cycling 4.5-7.5 m/s (10-17 mph).
##
## HOW: every actor is a CPU agent stepped round-robin (a fixed number a
## frame, so the cost does not grow with the frame rate) on the path graph;
## between its steps the people shader carries it on along its heading (as
## the cars). People, dogs, bikes and the ball are four MultiMeshes.
##
## Solid: people are not physics bodies here; baylands_life.gd puts a
## capsule body on each person near an aircraft (so the strike sweep hits
## them) and reports every aircraft's closest person.
extends Node3D

const BM = preload("res://scripts/baylands_life_meshes.gd")
const LM = preload("res://scripts/life_meshes.gd")

enum { STAND, WALK, JOG, SIT, PEDAL, BENCH, LIE, RUN, CROUCH }
enum { M_PATH, M_STATIC, M_WANDER, M_SOCCER, M_DOG, M_PAIR }
## Actor classes, for the truth list.
const CLASSES := ["walker", "jogger", "cyclist", "dog_walker", "child", "picnicker", "sunbather",
	"soccer_player", "referee", "spectator", "dog"]
const STEPS_PER_FRAME := 220
const SHIRTS := [Color(0.92, 0.92, 0.9), Color(0.1, 0.1, 0.12), Color(0.2, 0.28, 0.48), Color(0.62, 0.14, 0.12),
	Color(0.35, 0.5, 0.36), Color(0.95, 0.55, 0.1), Color(0.55, 0.55, 0.57), Color(0.85, 0.2, 0.45),
	Color(0.15, 0.55, 0.75), Color(0.85, 0.78, 0.25), Color(0.45, 0.3, 0.55), Color(0.75, 0.68, 0.55)]
const COATS := [Color(0.85, 0.7, 0.4), Color(0.1, 0.08, 0.07), Color(0.45, 0.28, 0.14), Color(0.92, 0.9, 0.85),
	Color(0.6, 0.45, 0.3)]
const BIKES := [Color(0.1, 0.1, 0.12), Color(0.85, 0.85, 0.86), Color(0.7, 0.1, 0.1), Color(0.1, 0.3, 0.7),
	Color(0.2, 0.2, 0.22)]
const JERSEYS := [Color(0.85, 0.1, 0.1), Color(0.1, 0.25, 0.8), Color(0.95, 0.85, 0.1), Color(0.95, 0.95, 0.95),
	Color(0.05, 0.6, 0.25), Color(0.95, 0.45, 0.05), Color(0.5, 0.1, 0.6), Color(0.05, 0.05, 0.08)]

var env: Node3D
var life: Dictionary = {}
var on := false
var hour := 14.0
var weekend := false
var density := 1.0
var _rng := RandomNumberGenerator.new()
var _clock := 0.0

# Walk graph.
var wn := PackedVector2Array()
var we_a := PackedInt32Array()
var we_b := PackedInt32Array()
var we_cat := PackedByteArray()
var we_bike := PackedByteArray()
var we_len := PackedFloat32Array()
var wadj: Array = []

# Actors.
var a_cls := PackedByteArray()
var a_mode := PackedByteArray()
var a_pose := PackedByteArray()
var a_edge := PackedInt32Array()
var a_fwd := PackedByteArray()              # 1: along the edge a -> b
var a_s := PackedFloat32Array()
var a_v := PackedFloat32Array()
var a_lat := PackedFloat32Array()
var a_pos := PackedVector3Array()           # ENU, z = feet (m above NAVD88)
var a_head := PackedFloat32Array()          # ENU rad, CCW from east
var a_anchor := PackedVector2Array()        # wander centre / soccer home
var a_target := PackedVector2Array()
var a_link := PackedInt32Array()            # dog -> owner, pair -> leader, soccer -> pitch
var a_wait := PackedFloat32Array()
var a_tick := PackedFloat32Array()
var a_col := PackedFloat32Array()
var a_phase := PackedFloat32Array()
var a_scale := PackedFloat32Array()
var a_mesh := PackedByteArray()             # 0 person, 1 dog
var a_slot := PackedInt32Array()
var a_bike := PackedInt32Array()            # bike slot or -1
var _movers := PackedInt32Array()           # actors that step
var _cursor := 0

var _mm_person: MultiMesh
var _mm_dog: MultiMesh
var _mm_bike: MultiMesh
var _mm_ball: MultiMesh
var _mmis: Array = []
var pitches: Array = []                     # {c, a, L, W, game, ball, bv, btarget, team...}
var active_rects: Array = []                # [centre, angle, L, W] of fields in use
var shadows: bool = not "--life-noshadow" in OS.get_cmdline_user_args()


func setup(env_node: Node3D, data: Dictionary) -> void:
	env = env_node
	life = data
	name = "people"
	_load_graph()


func _load_graph() -> void:
	var w: Dictionary = life.get("walk", {})
	for p: Array in w.get("nodes", []):
		wn.append(Vector2(float(p[0]), float(p[1])))
	for e: Array in w.get("edges", []):
		var a := int(e[0])
		var b := int(e[1])
		we_a.append(a)
		we_b.append(b)
		we_cat.append(int(e[2]))
		we_bike.append(int(e[3]))
		we_len.append(maxf(wn[a].distance_to(wn[b]), 0.05))
	# Plain Arrays: a packed array taken out of an Array is a copy.
	wadj.resize(wn.size())
	for i in wn.size():
		wadj[i] = []
	for k in we_a.size():
		wadj[we_a[k]].append(k)
		wadj[we_b[k]].append(k)
	# OSM maps some of Twin Creeks' blocks of fields as one pitch (200 x 100,
	# 205 x 205 m): split anything over 150 m into fields of a real size.
	for p: Array in life.get("pitches", []):
		var c := Vector2(float(p[0]), float(p[1]))
		var a := float(p[2])
		var L := float(p[3])
		var W := float(p[4])
		var nu := 2 if L > 150.0 else 1
		var nv := 2 if W > 150.0 else 1
		for iu in nu:
			for iv in nv:
				var off := Vector2((float(iu) + 0.5) / nu - 0.5, (float(iv) + 0.5) / nv - 0.5) * Vector2(L, W)
				var l2 := L / nu
				var w2 := W / nv
				var a2 := a if l2 >= w2 else a + PI * 0.5
				pitches.append({"c": c + off.rotated(a), "a": a2, "L": maxf(l2, w2), "W": minf(l2, w2)})


func set_on(want: bool) -> void:
	if want and _mm_person == null:
		_populate()
	on = want and _mm_person != null
	for m: MultiMeshInstance3D in _mmis:
		m.visible = on


func set_clock(h: float, we: bool, dens: float) -> void:
	var changed := absf(h - hour) > 0.25 or we != weekend or absf(dens - density) > 0.01
	hour = h
	weekend = we
	density = dens
	if changed and _mm_person != null:
		_populate()
		for m: MultiMeshInstance3D in _mmis:
			m.visible = on


func count() -> int:
	return a_cls.size() if on else 0


func counts() -> Dictionary:
	var out := {}
	if not on:
		return out
	for i in a_cls.size():
		var k: String = CLASSES[a_cls[i]]
		out[k] = int(out.get(k, 0)) + 1
	return out


# ------------------------------------------------------------------ density

## 0..1 of the day's peak out on the paths at hour h.
static func hour_profile(h: float, park: bool) -> float:
	if h < 5.5 or h >= 21.5:
		return 0.01
	if park and h < 8.0:
		return 0.04                       # the park opens at 8
	var keys := [[5.5, 0.05], [6.5, 0.3], [8.0, 0.45], [10.0, 0.75], [11.0, 1.0], [16.0, 1.0],
		[18.0, 0.85], [19.5, 0.5], [20.5, 0.2], [21.5, 0.01]]
	for i in keys.size() - 1:
		if h < float(keys[i + 1][0]):
			var t := (h - float(keys[i][0])) / (float(keys[i + 1][0]) - float(keys[i][0]))
			return lerpf(float(keys[i][1]), float(keys[i + 1][1]), t)
	return 0.01


func _per_km(cat: int) -> float:
	match cat:
		0:
			return (45.0 if weekend else 14.0) * hour_profile(hour, true)
		1:
			return (14.0 if weekend else 5.0) * hour_profile(hour, false)
		_:
			if weekend:
				return 1.2 * hour_profile(hour, false)
			var lunch := 1.0 if hour >= 11.5 and hour < 13.5 else 0.0
			return (2.0 + 5.0 * lunch) * (1.0 if hour >= 7.5 and hour < 18.5 else 0.15)


# ------------------------------------------------------------------ populate

func _clear() -> void:
	for arr in [a_cls, a_mode, a_pose, a_edge, a_fwd, a_s, a_v, a_lat, a_pos, a_head, a_anchor, a_target,
			a_link, a_wait, a_tick, a_col, a_phase, a_scale, a_mesh, a_slot, a_bike, _movers]:
		arr.resize(0)
	_cursor = 0
	active_rects.clear()
	for p: Dictionary in pitches:
		p["game"] = 0


func _populate() -> void:
	var t0 := Time.get_ticks_msec()
	_rng.seed = 0x9E09 + int(hour * 4.0) * 7 + (1000 if weekend else 0)
	_clear()
	# Paths.
	var cat_len := [0.0, 0.0, 0.0]
	for k in we_a.size():
		cat_len[we_cat[k]] += we_len[k]
	for cat in 3:
		var n := int(cat_len[cat] / 1000.0 * _per_km(cat) * density + 0.5)
		var cum := PackedFloat32Array()
		var ids := PackedInt32Array()
		var acc := 0.0
		for k in we_a.size():
			if we_cat[k] == cat:
				acc += we_len[k]
				cum.append(acc)
				ids.append(k)
		if ids.is_empty():
			continue
		for j in n:
			var e := ids[mini(cum.bsearch(_rng.randf() * acc), ids.size() - 1)]
			_spawn_path(e, cat)
	_picnics()
	_meadow()
	_soccer()
	_build_meshes()
	print("[baylands_life] people: %d (%s, %s %.1f h), %d ms" % [a_cls.size(), str(counts_raw()),
		"weekend" if weekend else "weekday", hour, Time.get_ticks_msec() - t0])


func counts_raw() -> Dictionary:
	var out := {}
	for i in a_cls.size():
		var k: String = CLASSES[a_cls[i]]
		out[k] = int(out.get(k, 0)) + 1
	return out


func _new_actor(cls: int, mode: int, pose: int, pos: Vector3, head: float, col: Color, sc: float = 1.0,
		mesh: int = 0) -> int:
	var i := a_cls.size()
	a_cls.append(cls)
	a_mode.append(mode)
	a_pose.append(pose)
	a_edge.append(-1)
	a_fwd.append(1)
	a_s.append(0.0)
	a_v.append(0.0)
	a_lat.append(0.0)
	a_pos.append(pos)
	a_head.append(head)
	a_anchor.append(Vector2(pos.x, pos.y))
	a_target.append(Vector2(pos.x, pos.y))
	a_link.append(-1)
	a_wait.append(0.0)
	a_tick.append(_clock)
	a_col.append(LM.pack_rgb(col))
	a_phase.append(_rng.randf())
	a_scale.append(sc)
	a_mesh.append(mesh)
	a_slot.append(-1)
	a_bike.append(-1)
	if mode != M_STATIC:
		_movers.append(i)
	return i


func _ground(e: float, n: float) -> float:
	return float(env.call("ground_height", e, n))


func _shirt() -> Color:
	return SHIRTS[_rng.randi_range(0, SHIRTS.size() - 1)]


func _spawn_path(e: int, cat: int) -> void:
	var r := _rng.randf()
	var cls: int
	if cat == 1:
		cls = 0 if r < 0.4 else (1 if r < 0.62 else (2 if r < 0.9 else 3))
	elif cat == 0:
		cls = 0 if r < 0.55 else (1 if r < 0.65 else (2 if r < 0.75 else (3 if r < 0.87 else 4)))
	else:
		cls = 0 if r < 0.75 else (1 if r < 0.9 else (2 if r < 0.95 else 3))
	if cls == 2 and we_bike[e] == 0:
		cls = 0
	var speed: float
	var pose: int
	match cls:
		1:
			speed = _rng.randf_range(2.6, 3.4)
			pose = JOG
		2:
			speed = _rng.randf_range(4.5, 7.5)
			pose = PEDAL
		3:
			speed = _rng.randf_range(1.05, 1.3)
			pose = WALK
		_:
			speed = _rng.randf_range(1.15, 1.5)
			pose = WALK
	var s := _rng.randf() * we_len[e]
	var fwd := 1 if _rng.randf() < 0.5 else 0
	var lat := _rng.randf_range(0.5, 1.4) if cls != 2 else _rng.randf_range(0.8, 1.6)
	var i := _new_actor(0 if cls == 4 else cls, M_PATH, pose, Vector3.ZERO, 0.0, _shirt())
	a_edge[i] = e
	a_fwd[i] = fwd
	a_s[i] = s
	a_v[i] = speed
	a_lat[i] = lat
	_place_on_edge(i)
	if cls == 2:
		a_bike[i] = 0
	if cls == 3:
		var d := _new_actor(10, M_DOG, WALK, a_pos[i], a_head[i], COATS[_rng.randi_range(0, COATS.size() - 1)],
			_rng.randf_range(0.75, 1.1), 1)
		a_link[d] = i
	elif cls == 4 or (cls == 0 and _rng.randf() < 0.35):
		# A companion: walking alongside (a child with a parent in the park).
		var kid := cls == 4
		var j := _new_actor(4 if kid else 0, M_PAIR, WALK, a_pos[i], a_head[i], _shirt(),
			_rng.randf_range(0.55, 0.75) if kid else _rng.randf_range(0.93, 1.05))
		a_link[j] = i
		a_lat[j] = 0.75 if _rng.randf() < 0.5 else -0.75
	if cls != 4:
		a_scale[i] = _rng.randf_range(0.93, 1.06)


func _edge_point(e: int, fwd: int, s: float, lat: float) -> Vector2:
	var a := wn[we_a[e]]
	var b := wn[we_b[e]]
	var d := (b - a) / we_len[e]
	var p := a + d * s
	var dir := d if fwd == 1 else -d
	return p + Vector2(dir.y, -dir.x) * lat


func _place_on_edge(i: int) -> void:
	var e := a_edge[i]
	var a := wn[we_a[e]]
	var b := wn[we_b[e]]
	var d := (b - a) / we_len[e]
	var dir := d if a_fwd[i] == 1 else -d
	var p := a + d * a_s[i] + Vector2(dir.y, -dir.x) * a_lat[i]
	a_pos[i] = Vector3(p.x, p.y, _ground(p.x, p.y))
	a_head[i] = atan2(dir.y, dir.x)


func _picnics() -> void:
	var prof := hour_profile(hour, true)
	var occ := (0.55 if weekend else 0.12) * clampf((prof - 0.5) * 2.0, 0.0, 1.0) * density
	var used: Array = []
	for p: Array in life.get("picnic", []):
		var c := Vector2(float(p[0]), float(p[1]))
		var dup := false
		for q: Vector2 in used:
			if q.distance_to(c) < 6.0:
				dup = true
		if dup or _rng.randf() > occ:
			continue
		used.append(c)
		var n := _rng.randi_range(4, 12) if weekend else _rng.randi_range(2, 5)
		var kids := 0
		for k in n:
			var r := _rng.randf()
			var ang := _rng.randf_range(-PI, PI)
			if r < 0.5:
				# At the table: seated, facing it.
				var q := c + Vector2(cos(ang), sin(ang)) * 0.95
				_new_actor(5, M_STATIC, BENCH, Vector3(q.x, q.y, _ground(q.x, q.y)), ang + PI, _shirt())
			elif r < 0.75 or kids >= 3:
				var q := c + Vector2(cos(ang), sin(ang)) * _rng.randf_range(1.8, 5.0)
				var pose := STAND if _rng.randf() < 0.6 else SIT
				_new_actor(5, M_STATIC, pose, Vector3(q.x, q.y, _ground(q.x, q.y)), _rng.randf_range(-PI, PI), _shirt())
			else:
				kids += 1
				var q := c + Vector2(cos(ang), sin(ang)) * _rng.randf_range(3.0, 10.0)
				var i := _new_actor(4, M_WANDER, RUN, Vector3(q.x, q.y, _ground(q.x, q.y)), ang, _shirt(),
					_rng.randf_range(0.5, 0.72))
				a_anchor[i] = c
				a_v[i] = _rng.randf_range(1.4, 2.8)


func _meadow() -> void:
	var spots: Array = life.get("meadow", [])
	if spots.is_empty():
		return
	var prof := hour_profile(hour, true)
	var n := int(((30.0 if weekend else 8.0) * prof) * density)
	for g in n:
		var s: Array = spots[_rng.randi_range(0, spots.size() - 1)]
		var c := Vector2(float(s[0]), float(s[1]))
		var m := _rng.randi_range(1, 4)
		for k in m:
			var q := c + Vector2(_rng.randf_range(-1.6, 1.6), _rng.randf_range(-1.6, 1.6))
			var r := _rng.randf()
			var pose := SIT if r < 0.45 else (LIE if r < 0.7 else STAND)
			_new_actor(6, M_STATIC, pose, Vector3(q.x, q.y, _ground(q.x, q.y)), _rng.randf_range(-PI, PI), _shirt())
		if m == 1 and _rng.randf() < 0.3:
			# Someone with a dog off the lead, playing fetch.
			var d := _new_actor(10, M_WANDER, RUN, Vector3(c.x, c.y, _ground(c.x, c.y)), 0.0,
				COATS[_rng.randi_range(0, COATS.size() - 1)], 0.9, 1)
			a_anchor[d] = c
			a_v[d] = 4.0


## Soccer on the Twin Creeks fields: games on weekends, practices on weekday
## evenings.
func _soccer() -> void:
	var game_on := weekend and hour >= 8.0 and hour < 18.0
	var practice := not weekend and hour >= 16.5 and hour < 20.5
	if not (game_on or practice):
		return
	for pi in pitches.size():
		var p: Dictionary = pitches[pi]
		if _rng.randf() > (0.8 if game_on else 0.5) * density:
			continue
		p["game"] = 1 if game_on else 2
		active_rects.append([p["c"], p["a"], p["L"], p["W"]])
		var L: float = p["L"] * 0.92
		var W: float = p["W"] * 0.9
		var side := 11 if L >= 90.0 else (9 if L >= 65.0 else 7)
		var kids := _rng.randf() < (0.6 if game_on else 0.9)
		var sc := _rng.randf_range(0.65, 0.82) if kids else 1.0
		var cols := [JERSEYS[_rng.randi_range(0, JERSEYS.size() - 1)], Color()]
		cols[1] = JERSEYS[(JERSEYS.find(cols[0]) + _rng.randi_range(1, JERSEYS.size() - 1)) % JERSEYS.size()]
		p["players"] = PackedInt32Array()
		p["ball"] = Vector2.ZERO
		p["btarget"] = Vector2.ZERO
		p["bspeed"] = 0.0
		p["bnext"] = 0.0
		var n_team := side if game_on else _rng.randi_range(6, 10)
		var teams := 2 if game_on else 1
		for t in teams:
			for k in n_team:
				# Formation: keeper, then lines across the field.
				var u: float
				var v: float
				if k == 0 and game_on:
					u = -L * 0.46
					v = 0.0
				else:
					var line := (k - 1) % 3 if game_on else k % 3
					u = -L * 0.32 + float(line) * L * 0.22
					v = (float((k * 7) % 5) / 4.0 - 0.5) * W * 0.8
				if t == 1:
					u = -u
					v = -v
				var col: Color = cols[t]
				if k == 0 and game_on:
					col = Color(0.1, 0.8, 0.2) if t == 0 else Color(0.9, 0.9, 0.2)
				var home := _pitch_to_enu(p, Vector2(u, v))
				var i := _new_actor(7, M_SOCCER, STAND, Vector3(home.x, home.y, _ground(home.x, home.y)),
					p["a"] + (0.0 if t == 0 else PI), col, sc * _rng.randf_range(0.94, 1.06))
				a_anchor[i] = Vector2(u, v)
				a_link[i] = pi
				a_v[i] = _rng.randf_range(4.0, 6.0) * (0.8 if kids else 1.0)
				p["players"].append(i)
		if game_on:
			var r := _new_actor(8, M_SOCCER, STAND, Vector3(p["c"].x, p["c"].y, _ground(p["c"].x, p["c"].y)),
				p["a"], Color(0.95, 0.85, 0.1))
			a_anchor[r] = Vector2(0, W * 0.25)
			a_link[r] = pi
			a_v[r] = 4.5
			p["players"].append(r)
		else:
			for k in 2:
				var cpos := _pitch_to_enu(p, Vector2(_rng.randf_range(-L * 0.2, L * 0.2), -W * 0.3))
				_new_actor(9, M_STATIC, STAND, Vector3(cpos.x, cpos.y, _ground(cpos.x, cpos.y)), p["a"] + PI * 0.5, _shirt())
		# Families along one touchline (some in camp chairs).
		var fans := _rng.randi_range(10, 30) if game_on else _rng.randi_range(2, 8)
		for k in fans:
			var u := _rng.randf_range(-L * 0.4, L * 0.4)
			var q := _pitch_to_enu(p, Vector2(u, -p["W"] * 0.5 - _rng.randf_range(2.0, 5.0)))
			var pose := BENCH if _rng.randf() < 0.45 else STAND
			_new_actor(9, M_STATIC, pose, Vector3(q.x, q.y, _ground(q.x, q.y)), p["a"] + PI * 0.5, _shirt(),
				1.0 if _rng.randf() > 0.2 else 0.65)


func _pitch_to_enu(p: Dictionary, uv: Vector2) -> Vector2:
	return (p["c"] as Vector2) + uv.rotated(float(p["a"]))


func _build_meshes() -> void:
	for m: MultiMeshInstance3D in _mmis:
		m.queue_free()
	_mmis.clear()
	var np := 0
	var nd := 0
	var nb := 0
	for i in a_cls.size():
		if a_mesh[i] == 0:
			a_slot[i] = np
			np += 1
			if a_bike[i] >= 0:
				a_bike[i] = nb
				nb += 1
		else:
			a_slot[i] = nd
			nd += 1
	var nballs := 0
	for p: Dictionary in pitches:
		if int(p.get("game", 0)) == 1:
			p["ball_slot"] = nballs
			nballs += 1
	_mm_person = _mm(LM.person_mesh(), maxi(np, 1), BM.people_material(0), "people")
	_mm_dog = _mm(BM.dog_mesh(), maxi(nd, 1), BM.people_material(1), "dogs")
	_mm_bike = _mm(BM.bike_mesh(), maxi(nb, 1), BM.people_material(2), "bikes")
	_mm_ball = _mm(BM.ball_mesh(), maxi(nballs, 1), BM.people_material(3), "balls")
	_mm_person.visible_instance_count = np
	_mm_dog.visible_instance_count = nd
	_mm_bike.visible_instance_count = nb
	_mm_ball.visible_instance_count = nballs
	for i in a_cls.size():
		_write(i, 0.0)


func _mm(mesh: Mesh, n: int, mat: Material, nm: String) -> MultiMesh:
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_custom_data = true
	mm.mesh = mesh
	mm.instance_count = n
	mm.custom_aabb = AABB(Vector3(-6000, -20, -6000), Vector3(12000, 200, 12000))
	var mmi := MultiMeshInstance3D.new()
	mmi.name = "life_" + nm
	mmi.multimesh = mm
	mmi.material_override = mat
	mmi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON if (shadows and nm != "balls") \
		else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	mmi.visibility_range_end = 1500.0
	mmi.visible = on
	add_child(mmi)
	_mmis.append(mmi)
	return mm


# ------------------------------------------------------------------ step

func step(dt: float, clock: float) -> void:
	if not on:
		return
	_clock = clock
	var n := _movers.size()
	if n > 0:
		var k := mini(STEPS_PER_FRAME, n)
		for j in k:
			var i := _movers[_cursor % n]
			_cursor += 1
			var adt := minf(_clock - a_tick[i], 2.0)
			if adt <= 0.0:
				continue
			a_tick[i] = _clock
			match a_mode[i]:
				M_PATH:
					_step_path(i, adt)
				M_PAIR:
					_step_pair(i)
				M_DOG:
					_step_dog(i, adt)
				M_WANDER:
					_step_wander(i, adt)
				M_SOCCER:
					pass
			if a_mode[i] != M_SOCCER:
				_write(i, a_v[i] if a_pose[i] != STAND and a_pose[i] != SIT else 0.0)
	_step_soccer(dt)


func _step_path(i: int, dt: float) -> void:
	var e := a_edge[i]
	var s := a_s[i] + (a_v[i] * dt if a_fwd[i] == 1 else -a_v[i] * dt)
	var guard := 0
	while (s > we_len[e] or s < 0.0) and guard < 6:
		guard += 1
		var node := we_b[e] if s > we_len[e] else we_a[e]
		var over := (s - we_len[e]) if s > we_len[e] else -s
		var nx := _next_edge(e, node, a_cls[i] == 2)
		e = nx
		if we_a[e] == node:
			a_fwd[i] = 1
			s = over
		else:
			a_fwd[i] = 0
			s = we_len[e] - over
	a_edge[i] = e
	a_s[i] = clampf(s, 0.0, we_len[e])
	_place_on_edge(i)


## The edge to take at `node`, arriving by `e`: not straight back unless it
## is a dead end, weighted toward going straight on and staying on the same
## kind of path; cyclists keep to paths bikes may use.
func _next_edge(e: int, node: int, bike: bool) -> int:
	var adj: Array = wadj[node]
	var here := wn[node]
	var came := (here - (wn[we_a[e]] if we_b[e] == node else wn[we_b[e]])).normalized()
	var best := e
	var best_w := -1.0
	for c: int in adj:
		if c == e:
			continue
		if bike and we_bike[c] == 0:
			continue
		var other := we_b[c] if we_a[c] == node else we_a[c]
		var d := (wn[other] - here).normalized()
		var w := _rng.randf() * (1.5 + came.dot(d)) * (2.0 if we_cat[c] == we_cat[e] else 0.6)
		if w > best_w:
			best_w = w
			best = c
	return best


func _step_pair(i: int) -> void:
	var l := a_link[i]
	var h := a_head[l]
	var lp := a_pos[l]
	var off := Vector2(sin(h), -cos(h)) * a_lat[i]
	var p := Vector2(lp.x, lp.y) + off
	a_pos[i] = Vector3(p.x, p.y, _ground(p.x, p.y))
	a_head[i] = h
	a_v[i] = a_v[l]
	a_pose[i] = a_pose[l]


func _step_dog(i: int, dt: float) -> void:
	var o := a_link[i]
	var h := a_head[o]
	var op := a_pos[o]
	var want := Vector2(op.x, op.y) + Vector2(cos(h), sin(h)) * 0.9 + Vector2(sin(h), -cos(h)) * 1.0
	var p := Vector2(a_pos[i].x, a_pos[i].y)
	var d := want - p
	var sp := minf(d.length() / maxf(dt, 0.05), 3.5)
	if d.length() > 6.0:
		p = want
	else:
		p += d.normalized() * minf(d.length(), sp * dt) if d.length() > 0.05 else Vector2.ZERO
	a_pos[i] = Vector3(p.x, p.y, _ground(p.x, p.y))
	a_head[i] = h if d.length() < 0.3 else atan2(d.y, d.x)
	a_v[i] = a_v[o]
	a_pose[i] = WALK if a_v[i] > 0.2 else STAND


func _step_wander(i: int, dt: float) -> void:
	if a_wait[i] > 0.0:
		a_wait[i] -= dt
		a_pose[i] = STAND
		return
	var p := Vector2(a_pos[i].x, a_pos[i].y)
	var t := a_target[i]
	if p.distance_to(t) < 0.6:
		a_target[i] = a_anchor[i] + Vector2(_rng.randf_range(-9.0, 9.0), _rng.randf_range(-9.0, 9.0))
		a_wait[i] = _rng.randf_range(0.0, 3.0)
		return
	var d := (t - p).normalized()
	p += d * minf(a_v[i] * dt, p.distance_to(t))
	if env.call("is_water", p.x, p.y):
		a_target[i] = a_anchor[i]
		return
	a_pos[i] = Vector3(p.x, p.y, _ground(p.x, p.y))
	a_head[i] = atan2(d.y, d.x)
	a_pose[i] = RUN if a_v[i] > 2.2 else WALK


## Soccer: the ball goes from player to player of one side, then the other;
## everyone keeps their place in the formation shifted toward the ball, the
## nearest two chase it. 5 Hz.
var _soccer_due := 0.0


func _step_soccer(dt: float) -> void:
	_soccer_due -= dt
	if _soccer_due > 0.0:
		return
	var sdt := 0.2 - _soccer_due
	_soccer_due = 0.2
	for p: Dictionary in pitches:
		if int(p.get("game", 0)) == 0:
			continue
		var L: float = p["L"] * 0.92
		var W: float = p["W"] * 0.9
		var players: PackedInt32Array = p["players"]
		var ball: Vector2 = p["ball"]
		if _clock >= float(p["bnext"]):
			p["btarget"] = Vector2(clampf(ball.x + _rng.randf_range(-30.0, 30.0), -L * 0.5, L * 0.5),
				clampf(ball.y + _rng.randf_range(-22.0, 22.0), -W * 0.5, W * 0.5))
			p["bspeed"] = _rng.randf_range(6.0, 14.0)
			p["bnext"] = _clock + _rng.randf_range(1.5, 4.5)
		var bt: Vector2 = p["btarget"]
		var bd := bt - ball
		ball += bd.normalized() * minf(bd.length(), float(p["bspeed"]) * sdt) if bd.length() > 0.1 else Vector2.ZERO
		p["ball"] = ball
		# The two nearest to the ball chase it.
		var near := [-1, -1]
		var nd := [INF, INF]
		for i in players:
			var uv := _enu_to_pitch(p, Vector2(a_pos[i].x, a_pos[i].y))
			var dd := uv.distance_to(ball)
			if a_cls[i] == 7 and dd < nd[1]:
				if dd < nd[0]:
					near[1] = near[0]
					nd[1] = nd[0]
					near[0] = i
					nd[0] = dd
				else:
					near[1] = i
					nd[1] = dd
		for i in players:
			var home := a_anchor[i]
			var want := Vector2(home.x * 0.6 + ball.x * 0.45, home.y * 0.7 + ball.y * 0.3)
			if i == near[0] or i == near[1]:
				want = ball
			if a_cls[i] == 8:
				want = Vector2(ball.x * 0.8, ball.y * 0.5 + W * 0.15)
			var cur := _enu_to_pitch(p, Vector2(a_pos[i].x, a_pos[i].y))
			var d := want - cur
			var spd := minf(a_v[i] * (1.0 if (i == near[0] or i == near[1]) else 0.55), d.length() / sdt)
			var step := d.normalized() * spd * sdt if d.length() > 0.3 else Vector2.ZERO
			cur += step
			var q := _pitch_to_enu(p, cur)
			a_pos[i] = Vector3(q.x, q.y, _ground(q.x, q.y))
			var moving := spd > 0.8 and d.length() > 0.3
			if moving:
				var dw := step.rotated(float(p["a"]))
				a_head[i] = atan2(dw.y, dw.x)
			else:
				var tb := (ball - cur).rotated(float(p["a"]))
				a_head[i] = atan2(tb.y, tb.x)
			a_pose[i] = RUN if spd > 2.2 and moving else (WALK if moving else STAND)
			a_tick[i] = _clock
			_write(i, spd if moving else 0.0)
		if p.has("ball_slot"):
			var bq := _pitch_to_enu(p, ball)
			var bdir := (bt - ball).rotated(float(p["a"]))
			var bv := float(p["bspeed"]) if bd.length() > 0.2 else 0.0
			var xf := Transform3D(Basis(Vector3.UP, atan2(bdir.x, -bdir.y)), Vector3(bq.x, _ground(bq.x, bq.y), -bq.y))
			_mm_ball.set_instance_transform(int(p["ball_slot"]), xf)
			_mm_ball.set_instance_custom_data(int(p["ball_slot"]), Color(0.0, bv, fposmod(_clock, 3600.0), 0.0))


func _enu_to_pitch(p: Dictionary, q: Vector2) -> Vector2:
	return (q - (p["c"] as Vector2)).rotated(-float(p["a"]))


## Writes actor i's instance (and its bicycle): transform at its position
## and heading, custom data = colour, speed, tick time, pose + phase.
func _write(i: int, speed: float) -> void:
	var h := a_head[i]
	var p := a_pos[i]
	var sc := a_scale[i]
	var b := Basis(Vector3.UP, atan2(cos(h), -sin(h))).scaled(Vector3(sc, sc, sc))
	var xf := Transform3D(b, Vector3(p.x, p.z, -p.y))
	var cu := Color(a_col[i], speed, fposmod(_clock, 3600.0), float(a_pose[i]) + a_phase[i] * 0.999)
	if a_mesh[i] == 0:
		_mm_person.set_instance_transform(a_slot[i], xf)
		_mm_person.set_instance_custom_data(a_slot[i], cu)
		if a_bike[i] >= 0:
			var bxf := Transform3D(Basis(Vector3.UP, atan2(cos(h), -sin(h))), xf.origin)
			_mm_bike.set_instance_transform(a_bike[i], bxf)
			_mm_bike.set_instance_custom_data(a_bike[i], Color(LM.pack_rgb(BIKES[i % BIKES.size()]), speed,
				cu.b, a_phase[i] * 0.999))
	else:
		_mm_dog.set_instance_transform(a_slot[i], xf)
		_mm_dog.set_instance_custom_data(a_slot[i], cu)


# ------------------------------------------------------------------ truth

## Where actor i is now (ENU, feet): its last step carried on along its
## heading, as the shader draws it.
func pos_now(i: int) -> Vector3:
	var p := a_pos[i]
	var moving := a_pose[i] in [WALK, JOG, PEDAL, RUN]
	if not moving or a_mode[i] == M_STATIC:
		return p
	var dt := clampf(_clock - a_tick[i], 0.0, 1.5)
	var h := a_head[i]
	var v := a_v[i] if a_mode[i] != M_SOCCER else 0.0
	return p + Vector3(cos(h), sin(h), 0.0) * v * dt


## Size (length, width, height) of actor i, metres.
func size_of(i: int) -> Array:
	var sc := a_scale[i]
	if a_mesh[i] == 1:
		return [snappedf(0.95 * sc, 0.01), snappedf(0.3 * sc, 0.01), snappedf(0.72 * sc, 0.01)]
	if a_bike[i] >= 0:
		return [1.75, 0.6, snappedf(1.82 * sc, 0.01)]
	match a_pose[i]:
		SIT, BENCH, CROUCH:
			return [snappedf(0.9 * sc, 0.01), snappedf(0.45 * sc, 0.01), snappedf((0.95 if a_pose[i] == SIT else 1.3) * sc, 0.01)]
		LIE:
			return [snappedf(1.75 * sc, 0.01), snappedf(0.5 * sc, 0.01), snappedf(0.25 * sc, 0.01)]
	return [snappedf(0.35 * sc, 0.01), snappedf(0.5 * sc, 0.01), snappedf(1.75 * sc, 0.01)]


func actors_near(c: Vector2, radius: float, out: Array) -> void:
	if not on:
		return
	var r2 := radius * radius
	for i in a_cls.size():
		var p := pos_now(i)
		if Vector2(p.x, p.y).distance_squared_to(c) > r2:
			continue
		var moving := a_pose[i] in [WALK, JOG, PEDAL, RUN]
		out.append({"class": "dog" if a_mesh[i] == 1 else ("cyclist" if a_bike[i] >= 0 else "person"),
			"role": CLASSES[a_cls[i]], "id": "p%d" % i,
			"e": snappedf(p.x, 0.01), "n": snappedf(p.y, 0.01), "z": snappedf(p.z, 0.01),
			"heading": snappedf(a_head[i], 0.01), "speed": snappedf(a_v[i] if moving else 0.0, 0.1),
			"size": size_of(i)})


## People (not dogs) within `reach` of ENU point q: [[index, position]].
func people_near(q: Vector3, reach: float) -> Array:
	var out: Array = []
	if not on:
		return out
	var r2 := reach * reach
	for i in a_cls.size():
		if a_mesh[i] != 0:
			continue
		var p := pos_now(i)
		if (p.x - q.x) * (p.x - q.x) + (p.y - q.y) * (p.y - q.y) < r2:
			out.append([i, p])
	return out


func sample() -> Array:
	for i in a_cls.size():
		if a_mode[i] == M_PATH:
			var p := pos_now(i)
			return [p.x, p.y, p.z, CLASSES[a_cls[i]]]
	return []


## Shadows from people, dogs and bicycles on or off (env_op life {"set": {"shadows": false}}).
func set_shadows(want: bool) -> void:
	shadows = want
	for m: MultiMeshInstance3D in _mmis:
		m.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON if (want and not m.name.ends_with("balls")) \
			else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF


func set_clock_uniform(now: float) -> void:
	for m: ShaderMaterial in BM.people_materials():
		m.set_shader_parameter("now", now)

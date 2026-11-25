## The moving world of the Manhattan photo scene (env_manhattan_photo.gd):
## road traffic, parked cars, people on the sidewalks and ships on the rivers.
## Time of day and weather are not here: they are the scene's clock
## ("world_clock"); this reads the night from env.night_amount() for the
## vehicles' lamps, the ships' windows and how many people are out.
##
## OFF by default, all of it, parked cars included. A detector sees moving
## traffic just as it sees the scene's target and decoy, so a scene with
## traffic is a different test from one without, and the same goes for parked
## cars: Manhattan's kerbs hold some 200,000 of them, every one a "car" to a
## detector looking for the decoy car. The default scene stays the one the
## baselines were measured on; realism is switched on per run:
##
##   launch arg  --world-life            (everything), or
##               --world-life=traffic,parked,people,ships (any of them)
##   IPC         {"op": "fw_set_world", "traffic": true, "parked": true,
##                "people": true, "ships": true}
##               "traffic": N (an integer) also sizes the moving pool (once,
##               before it is first built; default 900)
##   IPC         {"op": "fw_world_state"} reports counts and a sample position
##               of each (sim e, n, height) for pointing a camera.
##
## Nothing here is a prop: none of it goes into env.props, so fw_detect,
## prop_truth and the GCS's counts are unchanged. Moving vehicles and ships
## ARE solid (kinematic bodies on collision layer 1): an aircraft's strike
## sweep hits a bus or a ferry's wheelhouse. Parked cars and people are not.
##
## The parts are children: traffic (life_traffic.gd), parked (life_parked.gd),
## people (life_people.gd), ships (life_ships.gd); the street network they
## share is life_roads.gd, loaded the first time any of them is switched on.
##
## Test hook: --life-night=0..1 forces the night amount this script uses (for
## checking lamps before the scene has a clock).
extends Node3D

const LifeRoads = preload("res://scripts/life_roads.gd")
const LifeTraffic = preload("res://scripts/life_traffic.gd")
const LifeParked = preload("res://scripts/life_parked.gd")
const LifePeople = preload("res://scripts/life_people.gd")
const LifeShips = preload("res://scripts/life_ships.gd")
const LM = preload("res://scripts/life_meshes.gd")

var env: Node3D
var roads = null
var traffic: Node3D
var parked: Node3D
var people: Node3D
var ships: Node3D
var _cams: Array = []
var _cams_due := 0.0
var _night_override := -1.0
## Not called `_night`: fixedwing_manager._world_night() takes the first env
## child with a `_night` as the scene's clock, and this is not the clock.
var _night_now := 0.0
var _cpu := {}                              # part -> mean ms a frame (EMA)


func _init() -> void:
	name = "world_life"


func _ready() -> void:
	env = get_parent()
	traffic = LifeTraffic.new()
	parked = LifeParked.new()
	people = LifePeople.new()
	ships = LifeShips.new()
	ships.setup(env)
	traffic.name = "traffic"
	parked.name = "parked"
	people.name = "people"
	add_child(traffic)
	add_child(parked)
	add_child(people)
	add_child(ships)
	for a: String in OS.get_cmdline_user_args():
		if a.begins_with("--life-night="):
			_night_override = clampf(float(a.substr(13)), 0.0, 1.0)
	var arg := _world_life_arg()
	if arg != "":
		var all := arg == "all"
		set_world({"traffic": all or "traffic" in arg, "parked": all or "parked" in arg,
			"people": all or "people" in arg, "ships": all or "ships" in arg})
	print("[world_life] moving world %s" % ("on (%s)" % arg if arg != "" else "off (fw_set_world / --world-life)"))


## "" without the flag; "all" for a bare --world-life (or =1/all/true); else its list.
static func _world_life_arg() -> String:
	for a: String in OS.get_cmdline_user_args():
		if a == "--world-life":
			return "all"
		if a.begins_with("--world-life="):
			var v := a.substr(13)
			return "all" if v in ["", "1", "all", "true"] else ("" if v in ["0", "false", "off"] else v)
	return ""


func _roads():
	if roads == null:
		var t0 := Time.get_ticks_msec()
		roads = LifeRoads.new()
		if not roads.load_from(env):
			push_warning("[world_life] no roads.json: no traffic, parked cars or people")
		roads.block_props(env.get("props") if env.get("props") != null else [])
		traffic.setup(env, roads)
		parked.setup(env, roads)
		people.setup(env, roads)
		print("[world_life] roads: %d nodes, %d directed edges, %d lanes closed by props (%d ms)" % [
			roads.nodes.size(), roads.edge_count(), roads.blocked.size(), Time.get_ticks_msec() - t0])
	return roads


# =================================================================== IPC
func set_world(req: Dictionary) -> Dictionary:
	if bool(req.get("traffic", false)) or bool(req.get("parked", false)) or bool(req.get("people", false)):
		_roads()
	if req.has("traffic") and roads != null:
		var v = req["traffic"]
		traffic.set_on(bool(v), int(v) if (v is int or v is float) and int(v) > 1 else -1)
	if req.has("parked") and roads != null:
		parked.set_on(bool(req["parked"]))
	if req.has("people") and roads != null:
		people.set_on(bool(req["people"]))
	if req.has("ships"):
		ships.set_on(bool(req["ships"]))
	_cams_due = 0.0
	return world_state()


func world_state() -> Dictionary:
	var sample := {}
	if traffic.on:
		var v: Array = traffic.sample()
		if not v.is_empty():
			sample["vehicle"] = v
	if ships.on:
		var s: Array = ships.sample()
		if not s.is_empty():
			sample["ship"] = s
		sample["ships"] = ships.samples()
	return {"ok": true, "traffic": traffic.count() if traffic.on else 0,
		"parked": parked.count() if parked.on else 0, "people": people.count() if people.on else 0,
		"ships": ships.count(), "night": _night_now,
		"road_nodes": roads.nodes.size() if roads != null else 0,
		"sample": sample, "traffic_speed": traffic.mean_speed() if traffic.on else {},
		"cpu_ms": _cpu}


## Time of day and weather belong to the scene's clock; ipc_server still sends
## fw_set_time / fw_set_weather here, so pass them on.
func set_time(req: Dictionary) -> Dictionary:
	var clock := env.get_node_or_null("world_clock")
	if clock != null and clock.has_method("set_time"):
		return clock.call("set_time", req)
	return {"ok": false, "error": "no world_clock in this scene"}


func set_weather(req: Dictionary) -> Dictionary:
	var clock := env.get_node_or_null("world_clock")
	if clock != null and clock.has_method("set_weather"):
		return clock.call("set_weather", req)
	return {"ok": false, "error": "no world_clock in this scene"}


# =================================================================== frame
func _process(dt: float) -> void:
	dt = minf(dt, 0.1)
	if not (traffic.on or parked.on or people.on or ships.on):
		return
	_cams_due -= dt
	if _cams_due <= 0.0:
		_cams_due = 0.5
		_cams = _cameras()
	else:
		# A vantage removed since the last look (a fresh one per still).
		for c in _cams:
			if not is_instance_valid(c):
				_cams = _cameras()
				break
	_night_now = _night_override if _night_override >= 0.0 else float(env.call("night_amount"))
	if traffic.on:
		var t0 := Time.get_ticks_usec()
		traffic.step(dt, _cams)
		_ema("traffic", t0)
	var mat := LM.material()
	mat.set_shader_parameter("now", fposmod(traffic._clock, 1000.0))
	mat.set_shader_parameter("night", _night_now)
	if parked.on:
		var t1 := Time.get_ticks_usec()
		parked.update(_cams)
		_ema("parked", t1)
		for m: ShaderMaterial in parked.materials():
			m.set_shader_parameter("night", _night_now)
	if people.on:
		var t2 := Time.get_ticks_usec()
		people.update(_cams)
		_ema("people", t2)
		LM.person_material().set_shader_parameter("crowd", lerpf(1.0, 0.25, _night_now))
	if ships.on:
		var t3 := Time.get_ticks_usec()
		ships.step(dt)
		ships.set_night(_night_now)
		_ema("ships", t3)


func _ema(part: String, t0: int) -> void:
	var ms := float(Time.get_ticks_usec() - t0) / 1000.0
	_cpu[part] = lerpf(float(_cpu.get(part, ms)), ms, 0.05)


## The cameras drawing the scene now: aircraft and vantage cameras
## (SubViewports) first, then the window's.
func _cameras() -> Array:
	var out: Array = []
	var main: Array = []
	for n in get_tree().root.find_children("*", "Camera3D", true, false):
		var cam := n as Camera3D
		if not (cam.is_current() and cam.is_visible_in_tree()):
			continue
		var vp := cam.get_viewport()
		if vp is SubViewport:
			if (vp as SubViewport).render_target_update_mode == SubViewport.UPDATE_DISABLED:
				continue
			out.append(cam)
		else:
			main.append(cam)
	return out + main

## The moving world on land in the Istanbul scene (env_istanbul.gd): road
## traffic, trams, parked cars, people and gulls. The Istanbul counterpart of
## Manhattan's world_life.gd, and called "world_life" so ipc_server's
## fw_set_world / fw_world_state find it; it reports the same counts.
##
## OFF by default, all of it, parked cars included, exactly as Manhattan: a
## detector sees a taxi or a crowd as it sees the scene's target and decoy, so
## realism is switched on per run:
##
##   launch arg  --world-life            (everything), or
##               --world-life=traffic,trams,parked,people,gulls,ships (any of them)
##   IPC         {"op": "fw_set_world", "traffic": true, "trams": true, "parked": true,
##                "people": true, "gulls": true, "ships": true}
##               "traffic": true also starts the trams unless "trams" is given;
##               "traffic": N (an integer) sizes the moving pool (once,
##               before it is first built; default 900).
##   IPC         {"op": "fw_world_state"}: counts and a sample position of each.
##
## Ships are not drawn here: "ships" is passed to the scene's ships node
## (istanbul_ships.gd) when it has Manhattan's ships API (set_on, count,
## sample, samples, step, set_night); its count is reported as "ships".
##
## Nothing here is a prop. Moving vehicles and tram modules are solid
## (kinematic bodies on collision layer 1); parked cars, people and gulls are
## not.
##
## Parts (children): traffic (istanbul_life_traffic.gd, Manhattan's traffic
## with Istanbul's mix and hills), trams (istanbul_life_trams.gd), parked
## (istanbul_life_parked.gd), people (istanbul_life_people.gd), gulls
## (istanbul_life_gulls.gd); the street network they share is
## istanbul_life_roads.gd, loaded the first time any of them is switched on.
##
## Test hook: --life-night=0..1 forces the night amount (as world_life.gd).
extends Node3D

const Roads = preload("res://scripts/istanbul_life_roads.gd")
const Traffic = preload("res://scripts/istanbul_life_traffic.gd")
const Parked = preload("res://scripts/istanbul_life_parked.gd")
const People = preload("res://scripts/istanbul_life_people.gd")
const Trams = preload("res://scripts/istanbul_life_trams.gd")
const Gulls = preload("res://scripts/istanbul_life_gulls.gd")
const LM = preload("res://scripts/life_meshes.gd")
const IM = preload("res://scripts/istanbul_life_meshes.gd")
const WorldLife = preload("res://scripts/world_life.gd")

var env: Node3D
var roads = null
var traffic: Node3D
var parked: Node3D
var people: Node3D
var trams: Node3D
var gulls: Node3D
var _cams: Array = []
var _cams_due := 0.0
var _night_override := -1.0
## Not `_night`: fixedwing_manager takes the first env child with one as the clock.
var _night_now := 0.0
var _cpu := {}
var _ships_on := false


func _init() -> void:
	name = "world_life"


func _ready() -> void:
	env = get_parent()
	traffic = Traffic.new()
	parked = Parked.new()
	people = People.new()
	trams = Trams.new()
	gulls = Gulls.new()
	traffic.name = "traffic"
	parked.name = "parked"
	people.name = "people"
	trams.name = "trams"
	gulls.setup(env)
	add_child(traffic)
	add_child(parked)
	add_child(people)
	add_child(trams)
	add_child(gulls)
	for a: String in OS.get_cmdline_user_args():
		if a.begins_with("--life-night="):
			_night_override = clampf(float(a.substr(13)), 0.0, 1.0)
	_register_env.call_deferred()
	var arg: String = WorldLife._world_life_arg()
	if arg != "":
		# After the scene's other nodes (bridges, ships) are in and their
		# bodies are in the physics space: decks are found by raycast.
		_start_deferred(arg)
	print("[istanbul_life] moving world %s" % ("on (%s)" % arg if arg != "" else "off (fw_set_world / --world-life)"))


## ipc_server reaches this node (fw_set_world, fw_world_state) through
## FixedWingManager._env. env_istanbul does not register itself there (yet);
## if nothing has by the end of the env's _ready, register it, once.
func _register_env() -> void:
	var fwm := get_node_or_null("/root/FixedWingManager")
	if fwm != null and fwm.get("_env") == null and fwm.has_method("register_env"):
		fwm.call("register_env", env)
		print("[istanbul_life] registered env_istanbul with FixedWingManager (the env does not)")


func _start_deferred(arg: String) -> void:
	await get_tree().physics_frame
	await get_tree().physics_frame
	var all := arg == "all"
	set_world({"traffic": all or "traffic" in arg, "trams": all or "tram" in arg or "traffic" in arg,
		"parked": all or "parked" in arg, "people": all or "people" in arg,
		"gulls": all or "gull" in arg, "ships": all or "ships" in arg})


func _roads():
	if roads == null:
		var t0 := Time.get_ticks_msec()
		roads = Roads.new()
		if not roads.load_istanbul(env):
			push_warning("[istanbul_life] no roads.json: no traffic, parked cars or people")
		roads.block_props(env.get("props") if env.get("props") != null else [])
		var moved: int = roads.refine_decks(get_world_3d().direct_space_state)
		traffic.setup(env, roads)
		parked.setup(env, roads)
		people.setup(env, roads)
		trams.setup(env, roads)
		print("[istanbul_life] roads: %d nodes, %d directed edges, %d signalled junctions, %d closed by OSM access, %d bridge edges driven, %d deck nodes on a solid deck (%d ms)" % [
			roads.nodes.size(), roads.edge_count(), roads.signals, roads.closed_edges, roads.bridge_edges,
			moved, Time.get_ticks_msec() - t0])
	return roads


func _ships() -> Node:
	for c in env.get_children():
		if c != self and c.has_method("set_on") and c.has_method("samples") \
				and str(c.get_script().resource_path).ends_with("istanbul_ships.gd"):
			return c
	return null


# =================================================================== IPC
func set_world(req: Dictionary) -> Dictionary:
	var need := false
	for k in ["traffic", "parked", "people", "trams"]:
		need = need or bool(req.get(k, false))
	if need:
		_roads()
	if req.has("traffic") and roads != null:
		var v = req["traffic"]
		traffic.set_on(bool(v), int(v) if (v is int or v is float) and int(v) > 1 else -1)
		if not req.has("trams"):
			trams.set_on(bool(v))
	if req.has("trams") and roads != null:
		trams.set_on(bool(req["trams"]))
	if req.has("parked") and roads != null:
		parked.set_on(bool(req["parked"]))
	if req.has("people") and roads != null:
		people.set_on(bool(req["people"]))
	if req.has("gulls"):
		gulls.set_on(bool(req["gulls"]))
	if req.has("ships"):
		_ships_on = bool(req["ships"])
		var sh := _ships()
		if sh != null:
			sh.call("set_on", _ships_on)
	_cams_due = 0.0
	return world_state()


func world_state() -> Dictionary:
	var sample := {}
	if traffic.on:
		var v: Array = traffic.sample()
		if not v.is_empty():
			sample["vehicle"] = v
	if trams.on:
		var t: Array = trams.sample()
		if not t.is_empty():
			sample["tram"] = t
		sample["trams"] = trams.samples()
	var ships_n := 0
	var sh := _ships()
	if sh != null:
		ships_n = int(sh.call("count")) if sh.has_method("count") else 0
		if _ships_on:
			var s = sh.call("sample") if sh.has_method("sample") else []
			if s is Array and not (s as Array).is_empty():
				sample["ship"] = s
			sample["ships"] = sh.call("samples")
	return {"ok": true, "traffic": traffic.count() if traffic.on else 0,
		"parked": parked.count() if parked.on else 0, "people": people.count() if people.on else 0,
		"ships": ships_n, "trams": trams.count(), "tram_modules": trams.modules(),
		"gulls": gulls.count(), "night": _night_now,
		"road_nodes": roads.nodes.size() if roads != null else 0,
		"sample": sample, "traffic_speed": traffic.mean_speed() if traffic.on else {},
		"cpu_ms": _cpu}


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
	if not (traffic.on or parked.on or people.on or trams.on or gulls.on or _ships_on):
		return
	_cams_due -= dt
	if _cams_due <= 0.0:
		_cams_due = 0.5
		_cams = _cameras()
	else:
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
	if trams.on:
		var t4 := Time.get_ticks_usec()
		trams.step(dt)
		_ema("trams", t4)
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
	if gulls.on:
		gulls.set_night(_night_now)
	if _ships_on:
		# A ships node that does not run itself (Manhattan's life_ships is
		# stepped by world_life) is stepped here.
		var sh := _ships()
		if sh != null and not sh.is_processing() and sh.has_method("step"):
			var t3 := Time.get_ticks_usec()
			sh.call("step", dt)
			if sh.has_method("set_night"):
				sh.call("set_night", _night_now)
			_ema("ships", t3)


func _ema(part: String, t0: int) -> void:
	var ms := float(Time.get_ticks_usec() - t0) / 1000.0
	_cpu[part] = lerpf(float(_cpu.get(part, ms)), ms, 0.05)


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

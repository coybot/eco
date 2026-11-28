## The moving life of the Baylands scene (env_baylands.gd): road traffic,
## cars in the car parks, people in the park and on the levees, and birds.
## The Baylands counterpart of Manhattan's world_life.gd and Istanbul's
## istanbul_life.gd, named "world_life" so ipc_server's fw_set_world /
## fw_world_state find it.
##
## OFF BY DEFAULT, all of it, exactly as Manhattan and Istanbul: a detector
## sees a car, a person or a goose as it sees a target, and training is not
## flooded unless a run asks for it:
##
##   launch arg  --world-life            (everything), or
##               --world-life=traffic,parked,people,birds (any of them)
##   IPC         {"op": "fw_set_world", "traffic": true, "parked": true,
##                "people": true, "birds": true}   ("gulls" = "birds")
##   IPC         {"op": "fw_world_state"}: counts and a sample of each.
##   IPC         {"op": "env_op", "name": "life", "args": {...}} (below)
##
## THE CLOCK. Density follows world_clock's hour and date (weekday or
## weekend; season for the birds). With no time set the scene is a weekday
## (15 July 2026, a Wednesday) at 14:00. Overrides: --life-hour=17.5,
## --life-day=weekend|weekday|sat|sun|mon..., --life-density=0.5, or
## env_op life {"set": {"hour": 10, "weekend": true, "density": 1}}.
##
## WHAT IS SOLID, AND WHY:
##   * Moving vehicles: kinematic boxes on collision layer 1 (as Manhattan's),
##     so the strike sweep crashes an aircraft into a bus; only those within
##     250 m of an aircraft are in the physics space (the rest cost nothing).
##   * Parked cars: static boxes on layer 1 in the chunks near the cameras.
##   * People: a capsule body (0.3 m radius, their height) on layer 1 for
##     each person within 40 m of an aircraft, moved at 10 Hz, attached to a
##     node named "person" so a crash says what it hit; and every tick each
##     aircraft's distance to the nearest person is measured: inside the
##     person's capsule is a "person_strike" event.
##   * Birds: not solid (a bird is not a wall). A hit within half the bird's
##     span is a "bird_strike" event with its species and mass.
##   Events also go to FixedWingManager's event log (fw_events) for the
##   fixed-wing / VTOL aircraft.
##
## env_op "life" args:
##   {}                                  counts, clock, proximity per aircraft, new events
##   {"near": [e, n], "radius": 300, "classes": ["person", "bird", ...]}
##                                       ground truth of every actor there:
##                                       class, role/species/kind, ENU position
##                                       (z m above NAVD88, feet / wheels), heading
##                                       (rad CCW from east), speed (m/s),
##                                       size [length, width, height] m
##   {"set": {...}}                      the clock overrides above; "shadows": false
##                                       drops people's and birds' shadows (also
##                                       --life-noshadow; for a laptop GPU)
##   {"events": true}                    all events since the start
##   {"flight": {"species": "goose", "at": [e, n], "bearing_deg": 90, "alt": 25}}
##                                       the nearest flock put in the air there
##                                       (true bearing), e.g. across a flight path
##   {"rest": {"species": "egret", "at": [e, n]}}
##                                       the nearest flock settled near there
## Training code can penalise proximity from "proximity" (metres to the
## nearest person, bird and vehicle per aircraft) and label frames from
## "near" (project the boxes with the camera pose, as godot_dataset_recorder
## does for its vehicles). Nothing here goes into env.props, so fw_detect and
## prop_truth are unchanged.
extends Node3D

const Roads = preload("res://scripts/baylands_life_roads.gd")
const Traffic = preload("res://scripts/baylands_life_traffic.gd")
const Parked = preload("res://scripts/baylands_life_parked.gd")
const People = preload("res://scripts/baylands_life_people.gd")
const Birds = preload("res://scripts/baylands_life_birds.gd")
const LM = preload("res://scripts/life_meshes.gd")
const WorldLife = preload("res://scripts/world_life.gd")

const LIFE_JSON := "res://assets/baylands/life.json"
const PERSON_R := 0.3
const BODY_POOL := 48

var env: Node3D
var roads = null
var traffic: Node3D
var parked: Node3D
var people: Node3D
var birds: Node3D
var life: Dictionary = {}
var _cams: Array = []
var _cams_due := 0.0
var _clock := 0.0
var _check_due := 0.0
var _clock_due := 0.0
var _cpu := {}
var hour := 14.0
var weekend := false
var doy := 196
var density := 1.0
var _fixed := {}                            # clock overrides (hour, weekend, density)
var events: Array = []
var _events_read := 0
var proximity := {}
var _prev_air := {}                         # id -> [clock, ENU position] for velocities
var _bodies: Array[RID] = []
var _person_node: Node
var _person_shape: RID
var _strike_cool := {}


func _init() -> void:
	name = "world_life"


func _ready() -> void:
	env = get_parent()
	var f := FileAccess.open(LIFE_JSON, FileAccess.READ)
	if f != null:
		var d = JSON.parse_string(f.get_as_text())
		life = d if d is Dictionary else {}
	if life.is_empty():
		push_warning("[baylands_life] no %s: run drone/sim/tools/bake_baylands_life.py" % LIFE_JSON)
	traffic = Traffic.new()
	traffic.name = "traffic"
	traffic.cap = 700
	parked = Parked.new()
	parked.setup_lots(env, life)
	people = People.new()
	people.setup(env, life)
	birds = Birds.new()
	birds.setup(env, life)
	add_child(traffic)
	add_child(parked)
	add_child(people)
	add_child(birds)
	_person_node = Node.new()
	_person_node.name = "person"
	add_child(_person_node)
	_read_args()
	_update_clock()
	if env.has_method("register_op"):
		env.call("register_op", "life", life_op)
	var arg: String = WorldLife._world_life_arg()
	if arg != "":
		_start_deferred(arg)
	print("[baylands_life] moving world %s" % ("on (%s)" % arg if arg != "" else "off (fw_set_world / --world-life)"))


func _exit_tree() -> void:
	for b in _bodies:
		PhysicsServer3D.free_rid(b)
	_bodies.clear()
	if _person_shape.is_valid():
		PhysicsServer3D.free_rid(_person_shape)


func _read_args() -> void:
	for a: String in OS.get_cmdline_user_args():
		if a.begins_with("--life-hour="):
			_fixed["hour"] = clampf(float(a.substr(12)), 0.0, 23.99)
		elif a.begins_with("--life-day="):
			var v := a.substr(11).to_lower()
			_fixed["weekend"] = v in ["weekend", "sat", "saturday", "sun", "sunday", "1", "true"]
		elif a.begins_with("--life-density="):
			_fixed["density"] = clampf(float(a.substr(15)), 0.0, 3.0)


func _start_deferred(arg: String) -> void:
	await get_tree().physics_frame
	var all := arg == "all"
	set_world({"traffic": all or "traffic" in arg, "parked": all or "parked" in arg,
		"people": all or "people" in arg, "birds": all or "bird" in arg or "gull" in arg})


func _roads():
	if roads == null:
		var t0 := Time.get_ticks_msec()
		roads = Roads.new()
		if not roads.load_baylands(env):
			push_warning("[baylands_life] no life_roads.json: no traffic")
		traffic.setup(env, roads)
		print("[baylands_life] roads: %d nodes, %d directed edges, %d signalled junctions, %d closed, %d bridge edges, %d lidar deck nodes (%d ms)" % [
			roads.nodes.size(), roads.edge_count(), roads.signals, roads.closed_edges, roads.bridge_edges,
			roads.decks, Time.get_ticks_msec() - t0])
	return roads


# =================================================================== clock

## Hour, weekday/weekend and day of year from world_clock (or the overrides).
func _update_clock() -> void:
	var h := 14.0
	var we := false
	var d := 196
	var clock := env.get_node_or_null("world_clock")
	var t: Dictionary = clock.get("_time") if clock != null and clock.get("_time") is Dictionary else {}
	if not t.is_empty():
		h = float(t.get("hour", 14.0))
		d = int(t.get("day_of_year", 196))
		var y := int(t.get("year", 2026))
		var jan1 := Time.get_unix_time_from_datetime_dict({"year": y, "month": 1, "day": 1, "hour": 12})
		var wd: int = Time.get_datetime_dict_from_unix_time(int(jan1 + (d - 1) * 86400))["weekday"]
		we = wd == 0 or wd == 6
	else:
		# 15 July 2026, the clock's default day, is a Wednesday.
		we = false
	h = float(_fixed.get("hour", h))
	we = bool(_fixed.get("weekend", we))
	var dens := float(_fixed.get("density", 1.0))
	hour = h
	weekend = we
	doy = d
	density = dens
	traffic.set_clock(h, we)
	parked.set_clock(h, we, dens)
	people.set_clock(h, we, dens)
	birds.set_day(d)
	birds.set_blocked_fields(people.active_rects)


# =================================================================== IPC

func set_world(req: Dictionary) -> Dictionary:
	if bool(req.get("traffic", false)):
		_roads()
	if req.has("traffic") and roads != null:
		var v = req["traffic"]
		traffic.set_on(bool(v), int(v) if (v is int or v is float) and int(v) > 1 else -1)
	if req.has("parked"):
		parked.set_on(bool(req["parked"]))
	if req.has("people"):
		people.set_on(bool(req["people"]))
		birds.set_blocked_fields(people.active_rects)
	var b = req.get("birds", req.get("gulls", null))
	if b != null:
		birds.set_on(bool(b))
	_cams_due = 0.0
	return world_state()


func world_state() -> Dictionary:
	var sample := {}
	if traffic.on:
		var v: Array = traffic.sample_on("motorway")
		if v.is_empty():
			v = traffic.sample()
		if not v.is_empty():
			sample["vehicle"] = v
	if people.on:
		var p: Array = people.sample()
		if not p.is_empty():
			sample["person"] = p
	if birds.on:
		var s: Array = birds.sample()
		if not s.is_empty():
			sample["birds"] = s
	return {"ok": true, "traffic": traffic.count() if traffic.on else 0,
		"parked": parked.count() if parked.on else 0, "people": people.count(),
		"birds": birds.count(), "ships": 0,
		"clock": {"hour": hour, "weekend": weekend, "day_of_year": doy, "density": density},
		"road_nodes": roads.nodes.size() if roads != null else 0,
		"sample": sample, "traffic_speed": traffic.mean_speed() if traffic.on else {},
		"cpu_ms": _cpu}


func set_time(req: Dictionary) -> Dictionary:
	var clock := env.get_node_or_null("world_clock")
	if clock != null and clock.has_method("set_time"):
		var r: Dictionary = clock.call("set_time", req)
		_update_clock()
		return r
	return {"ok": false, "error": "no world_clock in this scene"}


func set_weather(req: Dictionary) -> Dictionary:
	var clock := env.get_node_or_null("world_clock")
	if clock != null and clock.has_method("set_weather"):
		return clock.call("set_weather", req)
	return {"ok": false, "error": "no world_clock in this scene"}


func life_op(args: Dictionary) -> Dictionary:
	if args.has("set"):
		var s: Dictionary = args["set"]
		if s.has("shadows"):
			people.set_shadows(bool(s["shadows"]))
			birds.set_shadows(bool(s["shadows"]))
		for k in ["hour", "weekend", "density"]:
			if s.has(k):
				_fixed[k] = s[k]
			if s.has(k) and s[k] == null:
				_fixed.erase(k)
		_update_clock()
	if args.has("measure"):
		# Render cost per viewport (ms): the window and every camera's view.
		var out := {}
		var vps: Array = [get_viewport()]
		for c in _cameras():
			if not (c.get_viewport() in vps):
				vps.append(c.get_viewport())
		for vp: Viewport in vps:
			var rid := vp.get_viewport_rid()
			RenderingServer.viewport_set_measure_render_time(rid, true)
			out[str(vp.name)] = {"cpu_ms": snappedf(RenderingServer.viewport_get_measured_render_time_cpu(rid), 0.001),
				"gpu_ms": snappedf(RenderingServer.viewport_get_measured_render_time_gpu(rid), 0.001)}
		out["frame_setup_cpu_ms"] = snappedf(RenderingServer.get_frame_setup_time_cpu(), 0.001)
		out["life_cpu_ms"] = _cpu
		out["process_ms"] = snappedf(Performance.get_monitor(Performance.TIME_PROCESS) * 1000.0, 0.001)
		out["physics_ms"] = snappedf(Performance.get_monitor(Performance.TIME_PHYSICS_PROCESS) * 1000.0, 0.001)
		out["physics_objects"] = Performance.get_monitor(Performance.PHYSICS_3D_ACTIVE_OBJECTS)
		out["fps"] = Engine.get_frames_per_second()
		return {"ok": true, "render": out}
	if args.has("flight"):
		var f: Dictionary = args["flight"]
		return birds.force_flight(str(f.get("species", "goose")), Vector2(float(f["at"][0]), float(f["at"][1])),
			deg_to_rad(90.0 - float(f.get("bearing_deg", 90.0))), float(f.get("alt", 25.0)))
	if args.has("rest"):
		var f: Dictionary = args["rest"]
		return birds.force_rest(str(f.get("species", "goose")), Vector2(float(f["at"][0]), float(f["at"][1])))
	if args.has("near"):
		var c := Vector2(float(args["near"][0]), float(args["near"][1]))
		var r := float(args.get("radius", 300.0))
		var want: Array = args.get("classes", ["person", "dog", "cyclist", "vehicle", "bird"])
		var out: Array = []
		if "person" in want or "dog" in want or "cyclist" in want:
			people.actors_near(c, r, out)
		if "vehicle" in want:
			traffic.actors_near(c, r, out)
			parked.actors_near(c, r, out)
		if "bird" in want:
			birds.actors_near(c, r, out)
		out = out.filter(func(a: Dictionary) -> bool: return a["class"] in want)
		return {"ok": true, "t": snappedf(_clock, 0.01), "actors": out, "count": out.size()}
	var new_ev := events.slice(_events_read) if not args.get("events", false) else events.duplicate()
	_events_read = events.size()
	return {"ok": true, "counts": {"people": people.counts(), "birds": birds.counts(),
		"traffic": traffic.count() if traffic.on else 0, "parked": parked.count() if parked.on else 0},
		"flying_flocks": birds.flying_groups(),
		"clock": {"hour": hour, "weekend": weekend, "day_of_year": doy, "density": density},
		"proximity": proximity, "events": new_ev, "events_total": events.size()}


# =================================================================== frame

func _process(dt: float) -> void:
	dt = minf(dt, 0.1)
	if not (traffic.on or parked.on or people.on or birds.on):
		return
	_clock += dt
	var now := fposmod(_clock, 3600.0)
	_clock_due -= dt
	if _clock_due <= 0.0:
		_clock_due = 2.0
		var before := [hour, weekend, doy]
		_update_clock()
		if before != [hour, weekend, doy]:
			birds.set_blocked_fields(people.active_rects)
	_cams_due -= dt
	if _cams_due <= 0.0:
		_cams_due = 0.5
		_cams = _cameras()
	else:
		for c in _cams:
			if not is_instance_valid(c):
				_cams = _cameras()
				break
	if traffic.on:
		var t0 := Time.get_ticks_usec()
		traffic.step(dt, _cams)
		_ema("traffic", t0)
		var mat := LM.material()
		mat.set_shader_parameter("now", fposmod(traffic._clock, 1000.0))
		mat.set_shader_parameter("night", 0.0)
	if parked.on:
		var t1 := Time.get_ticks_usec()
		parked.update(_cams)
		_ema("parked", t1)
	if people.on:
		var t2 := Time.get_ticks_usec()
		people.step(dt, _clock)
		people.set_clock_uniform(now)
		_ema("people", t2)
	if birds.on:
		var t3 := Time.get_ticks_usec()
		birds.step(dt, _clock, [])
		birds.set_clock_uniform(now)
		_ema("birds", t3)
	_check_due -= dt
	if _check_due <= 0.0:
		_check_due = 0.1
		var t4 := Time.get_ticks_usec()
		_check_aircraft()
		_ema("aircraft", t4)


func _ema(part: String, t0: int) -> void:
	var ms := float(Time.get_ticks_usec() - t0) / 1000.0
	_cpu[part] = snappedf(lerpf(float(_cpu.get(part, ms)), ms, 0.05), 0.001)


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


# =================================================================== aircraft

## Every aircraft in the scene: fixed-wings / VTOLs (FixedWingManager) and
## the quad fleet (FleetManager). [{id, p: ENU, v: ENU m/s, src}].
func _aircraft() -> Array:
	var out: Array = []
	var fwm := get_node_or_null("/root/FixedWingManager")
	if fwm != null:
		var fw = fwm.get("_fw")
		if fw is Dictionary:
			for st in (fw as Dictionary).values():
				if st == null or not st.alive or st.crashed:
					continue
				out.append({"id": str(st.id), "p": st.position, "v": st.velocity, "src": "fw"})
	var fm := get_node_or_null("/root/FleetManager")
	if fm != null:
		var vs = fm.get("_vehicles")
		if vs is Dictionary:
			for st in (vs as Dictionary).values():
				if st == null or str(st.vtype) == "rover":
					continue
				var id := str(st.drone_id)
				var p: Vector3 = st.position
				var v := Vector3.ZERO
				if _prev_air.has(id):
					var pr: Array = _prev_air[id]
					var ddt := _clock - float(pr[0])
					if ddt > 1e-3:
						v = (p - (pr[1] as Vector3)) / ddt
				_prev_air[id] = [_clock, p]
				out.append({"id": id, "p": p, "v": v, "src": "fleet"})
	return out


func _check_aircraft() -> void:
	var air := _aircraft()
	var aps: Array = []
	for a: Dictionary in air:
		aps.append(a["p"])
	traffic.aircraft = aps
	if birds.on:
		birds.check_aircraft(air)
		for e: Dictionary in birds.events:
			events.append(e)
		birds.events.clear()
	var used := 0
	var space := get_world_3d().space
	proximity = {}
	for a: Dictionary in air:
		var ap: Vector3 = a["p"]
		var prox := {}
		if people.on:
			var near: Array = people.people_near(ap, 40.0)
			var best := INF
			for pr: Array in near:
				var i: int = pr[0]
				var pp: Vector3 = pr[1]
				var h: float = 1.75 * float(people.a_scale[i])
				var dz := 0.0 if ap.z >= pp.z and ap.z <= pp.z + h else minf(absf(ap.z - pp.z), absf(ap.z - pp.z - h))
				var dxy := Vector2(ap.x - pp.x, ap.y - pp.y).length()
				var d := Vector2(maxf(dxy - PERSON_R, 0.0), dz).length()
				best = minf(best, d)
				if d <= 0.0:
					_person_strike(a, i, pp)
				if used < BODY_POOL:
					_body(used, pp, h, space)
					used += 1
			if best < INF:
				prox["person_m"] = snappedf(best, 0.01)
		if birds.on:
			var bn: Array = []
			birds.actors_near(Vector2(ap.x, ap.y), 60.0, bn)
			var best := INF
			for b: Dictionary in bn:
				best = minf(best, ap.distance_to(Vector3(b["e"], b["n"], b["z"])))
			if best < INF:
				prox["bird_m"] = snappedf(best, 0.01)
		if traffic.on:
			var vn: Array = []
			traffic.actors_near(Vector2(ap.x, ap.y), 60.0, vn)
			var best := INF
			for v: Dictionary in vn:
				best = minf(best, ap.distance_to(Vector3(v["e"], v["n"], v["z"])))
			if best < INF:
				prox["vehicle_m"] = snappedf(best, 0.01)
		proximity[a["id"]] = prox
	for k in range(used, _bodies.size()):
		PhysicsServer3D.body_set_space(_bodies[k], RID())


## Capsule body k on the person at feet position p (ENU), height h.
func _body(k: int, p: Vector3, h: float, space: RID) -> void:
	if not _person_shape.is_valid():
		_person_shape = PhysicsServer3D.capsule_shape_create()
		PhysicsServer3D.shape_set_data(_person_shape, {"radius": PERSON_R, "height": 1.75})
	while _bodies.size() <= k:
		var b := PhysicsServer3D.body_create()
		PhysicsServer3D.body_set_mode(b, PhysicsServer3D.BODY_MODE_KINEMATIC)
		PhysicsServer3D.body_add_shape(b, _person_shape)
		PhysicsServer3D.body_set_collision_layer(b, 1)
		PhysicsServer3D.body_set_collision_mask(b, 0)
		PhysicsServer3D.body_attach_object_instance_id(b, _person_node.get_instance_id())
		_bodies.append(b)
	var s := h / 1.75
	PhysicsServer3D.body_set_state(_bodies[k], PhysicsServer3D.BODY_STATE_TRANSFORM,
		Transform3D(Basis.from_scale(Vector3(s, s, s)), Vector3(p.x, p.z + h * 0.5, -p.y)))
	PhysicsServer3D.body_set_space(_bodies[k], space)


func _person_strike(a: Dictionary, i: int, p: Vector3) -> void:
	var key := "%s/%d" % [a["id"], i]
	if _clock - float(_strike_cool.get(key, -100.0)) < 5.0:
		return
	_strike_cool[key] = _clock
	var ev := {"t": snappedf(_clock, 0.01), "kind": "person_strike", "aircraft": a["id"],
		"role": People.CLASSES[people.a_cls[i]], "e": snappedf(p.x, 0.01), "n": snappedf(p.y, 0.01),
		"z": snappedf(p.z, 0.01), "speed_mps": snappedf((a["v"] as Vector3).length(), 0.1)}
	events.append(ev)
	var fwm := get_node_or_null("/root/FixedWingManager")
	if fwm != null and a["src"] == "fw":
		fwm.call("log_event", str(a["id"]), "person_strike", ev)
	print("[baylands_life] PERSON STRIKE %s hit a %s at ENU (%.1f, %.1f, %.1f)" % [a["id"], ev["role"], p.x, p.y, p.z])

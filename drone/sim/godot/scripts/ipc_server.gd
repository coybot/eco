## IPC server: listens on TCP (default :9999) and speaks newline-delimited JSON.
##
## Implements the same protocol as sim_engine.py so engine_client.py works
## unchanged. Ops: get_state, get_camera_pose, set_goal, set_velocity,
## clear_velocity, set_yaw, grab_frame, add_vantage, auto_overhead, grab_vantage.
##
## Loaded as an autoload so it starts before any scene.
extends Node

const DEFAULT_PORT := 9999
const READ_CHUNK := 4096

var _server := TCPServer.new()
var _clients: Array[StreamPeerTCP] = []
var _buffers: Dictionary = {}  # peer id → String


func _ready() -> void:
	var port := _get_launch_arg("--ipc-port", str(DEFAULT_PORT)).to_int()
	var err := _server.listen(port)
	if err != OK:
		push_error("[IpcServer] Failed to listen on port %d: %s" % [port, error_string(err)])
		return
	print("[IpcServer] IPC ready on port %d" % port)


func _process(_delta: float) -> void:
	# Accept new connections.
	while _server.is_connection_available():
		var peer: StreamPeerTCP = _server.take_connection()
		_clients.append(peer)
		_buffers[peer.get_instance_id()] = ""

	# Read and dispatch.
	var to_remove: Array[StreamPeerTCP] = []
	for peer in _clients:
		peer.poll()
		var state := peer.get_status()
		if state == StreamPeerTCP.STATUS_NONE or state == StreamPeerTCP.STATUS_ERROR:
			to_remove.append(peer)
			continue
		var avail := peer.get_available_bytes()
		if avail > 0:
			var raw: PackedByteArray = peer.get_data(avail)[1]
			var chunk := raw.get_string_from_utf8()
			var pid := peer.get_instance_id()
			_buffers[pid] = _buffers.get(pid, "") + chunk
			_flush_lines(peer, pid)

	for peer in to_remove:
		_buffers.erase(peer.get_instance_id())
		_clients.erase(peer)


func _flush_lines(peer: StreamPeerTCP, pid: int) -> void:
	var buf: String = _buffers.get(pid, "")
	while "\n" in buf:
		var nl := buf.find("\n")
		var line := buf.substr(0, nl).strip_edges()
		buf = buf.substr(nl + 1)
		if line.length() > 0:
			var resp := _dispatch(line)
			peer.put_data((resp + "\n").to_utf8_buffer())
	_buffers[pid] = buf


func _dispatch(line: String) -> String:
	var req = JSON.parse_string(line)
	if req == null:
		return JSON.stringify({"ok": false, "error": "json parse error"})
	var op: String = req.get("op", "")
	var did: String = req.get("id", "")
	var fm := FleetManager
	var pm := PhroverManager

	match op:
		"get_state":
			var st = fm.get_state(did)
			if st == null:
				return JSON.stringify({"ok": false, "error": "unknown drone " + did})
			return JSON.stringify({
				"ok": true,
				"position": [st.position.x, st.position.y, st.position.z],
				"yaw": st.yaw
			})

		"fleet_detect":
			return JSON.stringify({"ok": true, "objects": fm.detect(did)})

		"get_camera_pose":
			var cp = fm.get_camera_pose(did)
			if cp == null:
				return JSON.stringify({"ok": false, "error": "unknown drone " + did})
			return JSON.stringify({"ok": true, "position": cp["position"],
									"forward": cp["forward"], "up": cp["up"]})

		"set_goal":
			var p: Array = req.get("p", [0.0, 0.0, 0.0])
			fm.set_goal(did, Vector3(p[0], p[1], p[2]))
			return JSON.stringify({"ok": true})

		"set_velocity":
			var v: Array = req.get("v", [0.0, 0.0, 0.0])
			fm.set_velocity(did, Vector3(v[0], v[1], v[2]))
			return JSON.stringify({"ok": true})

		"clear_velocity":
			fm.clear_velocity(did)
			return JSON.stringify({"ok": true})

		"set_yaw":
			fm.set_yaw(did, float(req.get("yaw", 0.0)))
			return JSON.stringify({"ok": true})

		"grab_frame":
			var jpg_b64 = fm.grab_frame_jpeg(did)
			return JSON.stringify({"ok": true, "jpg": jpg_b64})

		"add_vantage":
			var p: Array = req.get("p", [0.0, 0.0, 10.0])
			var look: Array = req.get("look", [0.0, 0.0, 0.0])
			fm.add_vantage(req.get("name", "v"), Vector3(p[0], p[1], p[2]),
						   Vector3(look[0], look[1], look[2]),
						   int(req.get("w", 1280)), int(req.get("h", 720)))
			return JSON.stringify({"ok": true})

		"auto_overhead":
			var name: String = req.get("name", "overhead")
			fm.auto_overhead(name)
			return JSON.stringify({"ok": true, "name": name})

		"grab_vantage":
			var jpg_b64 = fm.grab_vantage_jpeg(req.get("name", "overhead"))
			return JSON.stringify({"ok": true, "jpg": jpg_b64})

		"remove_vantage":
			fm.remove_vantage(req.get("name", "overhead"))
			return JSON.stringify({"ok": true})

		"move_vantage":
			var mv_p: Array = req.get("p", [0.0, 0.0, 10.0])
			var mv_look: Array = req.get("look", [0.0, 0.0, 0.0])
			fm.move_vantage(req.get("name", "overhead"), Vector3(mv_p[0], mv_p[1], mv_p[2]),
							Vector3(mv_look[0], mv_look[1], mv_look[2]))
			return JSON.stringify({"ok": true})

		"spawn":
			var vt: String = req.get("vtype", "quadcopter")
			if req.has("p"):
				var p: Array = req.get("p", [0.0, 0.0, 0.0])
				fm.spawn(did, vt, Vector3(p[0], p[1], p[2]))
			else:
				fm.spawn_auto(did, vt)
			return JSON.stringify({"ok": true})

		"despawn":
			fm.despawn(did)
			return JSON.stringify({"ok": true})

		"load_env":
			var env_name: String = req.get("env", "office")
			fm.load_env(env_name)
			return JSON.stringify({"ok": true, "env": env_name})

		# -- Phrover ops (2D ENU ground frame: x=east m, y=north m, yaw rad CCW from +x) --
		"phrover_spawn":
			var p: Array = req.get("p", [0.0, 0.0])
			var yaw: float = float(req.get("yaw", 0.0))
			pm.spawn(did, Vector2(p[0], p[1]), yaw)
			return JSON.stringify({"ok": true})

		"phrover_despawn":
			pm.despawn(did)
			return JSON.stringify({"ok": true})

		"phrover_state":
			var st = pm.get_state(did)
			if st == null:
				return JSON.stringify({"ok": false, "error": "unknown phrover " + did})
			return JSON.stringify({"ok": true, "pose": st["pose"], "battery": st["battery"],
									"guard_stopped": st["guard_stopped"],
									"person_stop_active": st["person_stop_active"],
									"clearance": st["clearance"]})

		"phrover_detect":
			return JSON.stringify({"ok": true, "objects": pm.detect(did)})

		"phrover_person_view":
			var view = pm.person_view(did, float(req.get("cam_h", 0.3)))
			if view == null:
				return JSON.stringify({"ok": false, "error": "unknown phrover " + did})
			view["ok"] = true
			return JSON.stringify(view)

		"phrover_unproject":
			var world = pm.unproject(did, float(req.get("nx", 0.5)), float(req.get("ny", 0.5)))
			if world == null:
				return JSON.stringify({"ok": true, "world": null})
			return JSON.stringify({"ok": true, "world": world})

		"phrover_grid":
			var grid = pm.get_grid(did)
			if grid == null:
				return JSON.stringify({"ok": false, "error": "unknown phrover " + did})
			return JSON.stringify({"ok": true, "res": grid["res"], "origin": grid["origin"],
									"w": grid["w"], "h": grid["h"], "occ": grid["occ"], "obs": grid["obs"]})

		"phrover_drive":
			pm.drive(did, float(req.get("v", 0.0)), float(req.get("w", 0.0)))
			return JSON.stringify({"ok": true})

		"phrover_stop":
			pm.stop(did)
			return JSON.stringify({"ok": true})

		"inject":
			var name: String = req.get("name", "")
			var params: Dictionary = req.get("params", {})
			pm.inject(name, params)
			return JSON.stringify({"ok": true})

		"fw_inject":
			# "inject" above is hard-wired to PhroverManager only — a fixed-wing
			# scenario calling the shared inject() would silently do nothing to
			# FixedWingManager's own state (found live: a raise_wall inject sent
			# via "inject" never actually appeared in the scene or its occupancy
			# grid). Separate op, same params shape, routed to FixedWingManager.
			var fw_name: String = req.get("name", "")
			var fw_params: Dictionary = req.get("params", {})
			FixedWingManager.inject(fw_name, fw_params)
			return JSON.stringify({"ok": true})

		"get_events":
			var since: float = float(req.get("since", 0.0))
			return JSON.stringify({"ok": true, "events": pm.get_events(since)})

		"reset":
			pm.reset(int(req.get("seed", 0)))
			return JSON.stringify({"ok": true})

		"prop_truth":
			return JSON.stringify({"ok": true, "props": pm.prop_truth()})

		# -- FixedWing ops (3D ENU frame: x=east m, y=north m, z=up m, yaw rad CCW from +x) --
		"fw_spawn":
			var p: Array = req.get("p", [0.0, 0.0, 0.0])
			var yaw: float = float(req.get("yaw", 0.0))
			# Optional per-airframe altitude ceiling, metres (default 120).
			var ceiling: float = float(req.get("ceiling", -1.0))
			# Optional airframe: "fixedwing" (default) or "vtol" (tilt-rotor,
			# T2 Cruza-like — can hover, land and take off).
			var airframe: String = str(req.get("airframe", ""))
			FixedWingManager.spawn(did, Vector3(p[0], p[1], p[2]), yaw, ceiling, airframe)
			return JSON.stringify({"ok": true})

		"fw_set_wind":
			# {speed, from_deg, gust, gusts_per_min, gust_s, turbulence, z0, seed}.
			return JSON.stringify({"ok": true, "wind": FixedWingManager.air.set_wind(req)})

		"fw_set_gps":
			# {on, sigma_m, jammers: [{e, n, radius_m, spoof: [de, dn] | null}]}.
			return JSON.stringify({"ok": true, "gps": FixedWingManager.air.set_gps(req)})

		"fw_set_link":
			# {on, station: [e, n, u], tx_dbm, freq_mhz, floor_dbm}.
			return JSON.stringify({"ok": true, "link": FixedWingManager.air.set_link(req)})

		"fw_set_physics":
			# {id ("" or "*": every aircraft), realistic: bool}.
			var n := FixedWingManager.set_physics(str(req.get("id", "")), bool(req.get("realistic", true)))
			return JSON.stringify({"ok": true, "aircraft": n})

		"fw_pose":
			# A SITL autopilot's pose. {id, p: [e, n, u], rpy: [roll, pitch, yaw],
			# v: [ve, vn, vu]} or {id, release: true} (drone/sim/godot/sitl_bridge.py).
			var pp: Array = req.get("p", [0.0, 0.0, 0.0])
			var rpy: Array = req.get("rpy", [0.0, 0.0, 0.0])
			var vv: Array = req.get("v", [0.0, 0.0, 0.0])
			if not FixedWingManager.set_pose(did, Vector3(pp[0], pp[1], pp[2]), Vector3(rpy[0], rpy[1], rpy[2]),
					Vector3(vv[0], vv[1], vv[2]), bool(req.get("release", false))):
				return JSON.stringify({"ok": false, "error": "unknown fw drone " + did})
			return JSON.stringify({"ok": true})

		"fw_despawn":
			FixedWingManager.despawn(did)
			return JSON.stringify({"ok": true})

		"fw_state":
			var st = FixedWingManager.get_state(did)
			if st == null:
				return JSON.stringify({"ok": false, "error": "unknown fw drone " + did})
			return JSON.stringify({
				"ok": true,
				"position": [st["position"][0], st["position"][1], st["position"][2]],
				"velocity": [st["velocity"][0], st["velocity"][1], st["velocity"][2]],
				"airspeed": st["airspeed"],
				"climb_rate": st["climb_rate"],
				"pitch": st["pitch"],
				"yaw": st["yaw"],
				"roll": st["roll"],
				"altitude": st["altitude"],
				"battery_level": st["battery_level"],
				# NOTE: this handler re-lists every field rather than forwarding
				# get_state() wholesale, so anything added there must be added
				# here too or it silently never reaches a client.
				"payload_remaining": st["payload_remaining"],
				"sensor_yaw_offset": st["sensor_yaw_offset"],
				"crashed": st["crashed"],
				"ceiling": st["ceiling"],
				"airframe": st["airframe"],
				"tilt": st["tilt"],
				"landed": st["landed"],
				"flight_mode": st["flight_mode"],
				"sim_time": st["sim_time"],
				# flight_env.gd: ground speed and the wind it flies in,
				# and the GPS receiver's and radio link's view when they are on.
				"groundspeed": st["groundspeed"],
				"wind": st["wind"],
				"realistic": st["realistic"],
				"external": st["external"],
				"gps": st["gps"],
				"link": st["link"],
				# The cameras' band, "eo" or "thermal" (fw_set_sensor).
				"band": st["band"]
			})

		"fw_detect":
			# {"truth": true} sees every prop in view at its EO
			# daylight range, whatever the band and the hour (sim truth).
			FixedWingManager._detect_truth = bool(req.get("truth", false))
			var objs := FixedWingManager.detect(did)
			FixedWingManager._detect_truth = false
			return JSON.stringify({"ok": true, "objects": objs})

		"fw_set_thermal":
			# The climate the thermal band draws with (thermal_kit.gd):
			# {air_day_c, air_night_c, sea_c, photo_hour}; any left out keep theirs.
			return JSON.stringify({"ok": true, "thermal": FixedWingManager.set_thermal_climate(req)})

		"fw_grab_frame":
			var jpg_b64 = FixedWingManager.grab_frame_jpeg(did)
			return JSON.stringify({"ok": true, "jpg": jpg_b64})

		"fw_grid":
			var grid = FixedWingManager.get_grid(did)
			return JSON.stringify({"ok": true, "grid": grid})

		"fw_unproject":
			var world = FixedWingManager.unproject(did, float(req.get("nx", 0.5)), float(req.get("ny", 0.5)))
			if world == null:
				return JSON.stringify({"ok": true, "world": null})
			return JSON.stringify({"ok": true, "world": world})

		"fw_drive":
			# "climb" defaults to 0 (hold altitude) so pre-existing clients that
			# never send the field keep their exact current behaviour.
			FixedWingManager.drive(did, float(req.get("airspeed", 0.0)),
				float(req.get("yaw_rate", 0.0)), float(req.get("climb", 0.0)))
			return JSON.stringify({"ok": true})

		"fw_stop":
			FixedWingManager.stop(did)
			return JSON.stringify({"ok": true})

		"fw_reset_camera":
			FixedWingManager.reset_camera(did)
			return JSON.stringify({"ok": true})

		"fw_set_sensor":
			# Either an explicit offset off the nose, or "aim at this ENU point"
			# (what an orbit uses to keep its centre in frame).
			# Or the camera model (sensor_fx.gd): "realism" off |
			# photometric | on, and per-effect overrides. Given only those keys,
			# the pointing is left alone.
			var model_keys: Array = []
			for k in (req as Dictionary).keys():
				if k in ["realism", "reset_overrides", "band", "palette", "halo", "probe"] or k in FixedWingManager.SensorFx.DEFAULTS:
					model_keys.append(k)
			if req.has("at"):
				var at: Array = req.get("at", [0.0, 0.0])
				FixedWingManager.aim_sensor_at(did, float(at[0]), float(at[1]))
			elif req.has("offset") or model_keys.is_empty():
				FixedWingManager.set_sensor_yaw_offset(did, float(req.get("offset", 0.0)))
			if not model_keys.is_empty():
				var sensor = FixedWingManager.configure_sensor(did, req)
				if sensor == null:
					return JSON.stringify({"ok": false, "error": "unknown fw drone " + did})
				return JSON.stringify({"ok": true, "sensor": sensor})
			return JSON.stringify({"ok": true})

		"fw_drop":
			var rel = FixedWingManager.drop_payload(did)
			return JSON.stringify({"ok": rel != null, "release": rel})

		"fw_all_states":
			return JSON.stringify({"ok": true, "states": FixedWingManager.all_states()})

		"fw_env_state":
			return JSON.stringify({"ok": true, "env": FixedWingManager.env_state()})

		"fw_set_world", "fw_set_time", "fw_set_weather", "fw_world_state":
			# The real-city scenes keep two children of the env: world_life.gd,
			# the moving world (traffic/ships/people, fw_set_world), and
			# world_clock.gd, the time of day and the weather (fw_set_time,
			# fw_set_weather). fw_world_state merges both. A scene whose
			# world_life keeps time itself still gets its time/weather calls.
			var env_node: Node = FixedWingManager._env
			var wl: Node = env_node.get_node_or_null("world_life") if env_node != null else null
			var wc: Node = env_node.get_node_or_null("world_clock") if env_node != null else null
			match op:
				"fw_set_world":
					if wl == null:
						return JSON.stringify({"ok": false, "error": "no moving world in this scene"})
					return JSON.stringify(wl.set_world(req))
				"fw_set_time", "fw_set_weather":
					var keeper: Node = wc if wc != null else wl
					if keeper == null or not keeper.has_method("set_time"):
						return JSON.stringify({"ok": false, "error": "no clock in this scene"})
					if op == "fw_set_time":
						return JSON.stringify(keeper.set_time(req))
					return JSON.stringify(keeper.set_weather(req))
			if wl == null and wc == null:
				return JSON.stringify({"ok": false, "error": "no moving world in this scene"})
			var st: Dictionary = wl.world_state() if wl != null else {"ok": true}
			if wc != null:
				st.merge(wc.world_state(), true)
			return JSON.stringify(st)

		"perf":
			# The window's frame rate and draw load, for checking what a scene
			# change costs without a restart flag.
			return JSON.stringify({"ok": true, "fps": Engine.get_frames_per_second(),
				"frame_ms": Performance.get_monitor(Performance.TIME_PROCESS) * 1000.0,
				"draw_calls": Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME),
				"primitives": Performance.get_monitor(Performance.RENDER_TOTAL_PRIMITIVES_IN_FRAME),
				"objects": Performance.get_monitor(Performance.RENDER_TOTAL_OBJECTS_IN_FRAME),
				"video_mem_mb": Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED) / 1048576.0,
				"static_mem_mb": Performance.get_monitor(Performance.MEMORY_STATIC) / 1048576.0})


		"fw_events":
			return JSON.stringify({"ok": true, "events": FixedWingManager.get_events(did)})

		"fw_reset":
			FixedWingManager.reset(did)
			return JSON.stringify({"ok": true})

		"fw_prop_truth":
			return JSON.stringify({"ok": true, "props": FixedWingManager.prop_truth()})

		"fw_log_event":
			var kind: String = req.get("kind", "")
			var data: Dictionary = req.get("data", {})
			FixedWingManager.log_event(did, kind, data)
			return JSON.stringify({"ok": true})

		_:
			return JSON.stringify({"ok": false, "error": "unknown op " + op})


# Parse a value from Godot launch args (format: --key=value or --key value).
static func _get_launch_arg(key: String, default_val: String = "") -> String:
	var args := OS.get_cmdline_user_args()
	for i in range(args.size()):
		var a: String = args[i]
		if a.begins_with(key + "="):
			return a.substr(key.length() + 1)
		if a == key and i + 1 < args.size():
			return args[i + 1]
	return default_val

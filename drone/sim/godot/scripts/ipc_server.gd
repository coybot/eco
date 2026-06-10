## IPC server: listens on TCP (default :9999) and speaks newline-delimited JSON.
##
## Implements the same protocol as sim_engine.py so engine_client.py works
## unchanged. Ops: get_state, set_goal, set_velocity, clear_velocity, set_yaw,
## grab_frame, add_vantage, auto_overhead, grab_vantage.
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
						   Vector3(look[0], look[1], look[2]))
			return JSON.stringify({"ok": true})

		"auto_overhead":
			var name: String = req.get("name", "overhead")
			fm.auto_overhead(name)
			return JSON.stringify({"ok": true, "name": name})

		"grab_vantage":
			var jpg_b64 = fm.grab_vantage_jpeg(req.get("name", "overhead"))
			return JSON.stringify({"ok": true, "jpg": jpg_b64})

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

## FleetManager: owns all vehicle nodes and their kinematic state.
##
## Mirrors IsaacVehicleBridge exactly:
##   - ENU coordinate space (x=east, y=north, z=up → Godot: x=east, z=-north, y=up)
##   - Kinematic integration: set_goal / set_velocity / set_yaw
##   - Per-vehicle SubViewport camera for grab_frame (JPEG, base64)
##   - Vantage cameras (fixed world viewpoints)
##
## Coordinate note: Isaac uses x=east, y=north, z=up (ROS ENU).
## Godot uses x=right, y=up, z=-forward (OpenGL). We map:
##   ENU (ex, ey, ez)  →  Godot (ex, ez, -ey)
## and reverse on state read-back. Yaw: ENU CCW from east (radians) →
## Godot rotation_degrees.y = -degrees(yaw).
extends Node

const VIEWPORT_W := 640
const VIEWPORT_H := 480
const JPEG_QUALITY := 85
const REACHED := 0.2          # metres — same as FleetWorker.REACHED
const MAX_LIN_QUAD := 3.0     # m/s
const MAX_LIN_ROVER := 1.5    # m/s
const MAX_YAW_RAD := 1.5708   # rad/s  (~90 deg/s)
const PHYSICS_DT := 1.0 / 60.0

# Per-vehicle runtime state (Dictionary, keyed by drone_id).
var _vehicles: Dictionary = {}   # id → VehicleState
var _vantages: Dictionary = {}   # name → {node, viewport, camera}

# Scene root — populated in _ready once the main scene is loaded.
var _scene_root: Node3D = null

# Active environment name — drives spawn layout and camera framing.
var _env_name: String = "office"

# Currently-loaded environment node (so load_env can swap it at runtime).
var _env_node: Node = null
# Main-scene vantage camera (re-created on env swap).
var _main_cam: Camera3D = null


# ------------------------------------------------------------------
# Internal state record
# ------------------------------------------------------------------
class VehicleState:
	var drone_id: String
	var vtype: String            # "quadcopter" | "rover"
	var node: Node3D             # mesh root
	var position: Vector3        # ENU metres
	var yaw: float               # radians, ENU CCW
	var goal: Vector3            # ENU; null-sentinel = Vector3.INF
	var goal_yaw: float          # NAN = none
	var velocity_cmd: Vector3    # ENU; zero = not active
	var velocity_active: bool
	var max_lin: float
	var viewport: SubViewport
	var camera: Camera3D

	func _init(did: String, vt: String, spawn: Vector3, n: Node3D,
			   vp: SubViewport, cam: Camera3D) -> void:
		drone_id = did
		vtype = vt
		node = n
		position = spawn
		yaw = 0.0
		goal = Vector3.INF
		goal_yaw = NAN
		velocity_cmd = Vector3.ZERO
		velocity_active = false
		max_lin = MAX_LIN_QUAD if vt == "quadcopter" else MAX_LIN_ROVER
		viewport = vp
		camera = cam


# ------------------------------------------------------------------
# Lifecycle
# ------------------------------------------------------------------
func _ready() -> void:
	# Defer setup until the main scene is fully loaded.
	call_deferred("_setup_fleet")


func _setup_fleet() -> void:
	_scene_root = get_tree().current_scene as Node3D
	if _scene_root == null:
		push_error("[FleetManager] current_scene is not a Node3D")
		return

	var fleet_str := IpcServer._get_launch_arg("--fleet", "quad:1")
	_env_name = IpcServer._get_launch_arg("--env", "office")

	# Load the appropriate environment scene as a child.
	_load_environment(_env_name)

	# Parse fleet spec: "quad:3,rover:2" or "quad:3,rover:2:photoreal"
	var photoreal := ":photoreal" in fleet_str
	fleet_str = fleet_str.replace(":photoreal", "")
	var roster := _parse_roster(fleet_str)

	# All vehicles face ENU -Y (Godot +Z, via 180°Y camera rotation) toward the subject.
	var n := roster.size()
	for i in range(n):
		var spec: Dictionary = roster[i]
		var spawn_enu := _spawn_pos(spec.type, i, n)
		_spawn_vehicle(spec.id, spec.type, spawn_enu, photoreal)

	_add_main_vantage()
	print("[FleetManager] fleet ready: %d vehicles in %s" % [roster.size(), _env_name])


# Per-environment spawn layout. Returns ENU spawn position.
func _spawn_pos(vtype: String, i: int, n: int) -> Vector3:
	if _env_name == "plaza":
		# Chair cluster centred ~ENU (0, 13). Spawn NORTH of it, facing -Y (toward chairs).
		# Quads at 8m altitude, rovers on ground. Spread 7m apart in x, centred on x=0.
		var gx: float = (float(i) - float(n - 1) * 0.5) * 7.0
		var gy: float = 28.0
		var gz: float = 0.0 if vtype == "rover" else 8.0
		return Vector3(gx, gy, gz)
	elif _env_name in ["city", "outdoor", "neighbourhood"]:
		# Kenney city: 1m grid cells, map centred near origin, streets at y=0.
		# Spawn above a clear road segment, spaced 6m apart in x.
		var gx: float = (float(i) - float(n - 1) * 0.5) * 6.0
		var gy: float = 5.0   # ENU north — roughly over a road
		var gz: float = 0.0 if vtype == "rover" else 10.0
		return Vector3(gx, gy, gz)
	else:
		# Office: glass facade center x=3. Spawn ENU y=150, 8m apart, quads at 15m altitude.
		var gx: float = 3.0 + (float(i) - float(n - 1) * 0.5) * 8.0
		var gy: float = 150.0
		var gz: float = 0.0 if vtype == "rover" else 15.0
		return Vector3(gx, gy, gz)


func _add_main_vantage() -> void:
	# Main-scene Camera3D for optional Xvfb screen capture.
	var main_cam := Camera3D.new()
	main_cam.name = "MainVantageCamera"
	main_cam.fov = 65.0
	if _env_name == "plaza":
		# Overlook chair cluster from the north-east, elevated.
		var mc_pos := Vector3(20.0, 14.0, -40.0)   # ENU(20,40,14)
		var mc_look := Vector3(0.0, 0.0, -13.0)    # ENU(0,13,0)
		main_cam.position = mc_pos
		main_cam.look_at(mc_look, Vector3.UP)
	elif _env_name in ["city", "outdoor", "neighbourhood"]:
		# Kenney city: angled overhead looking at street centre.
		# ENU cam at (0, -25, 35) → Godot (0, 35, 25); look at origin.
		main_cam.position = Vector3(0.0, 35.0, 25.0)
		main_cam.look_at(Vector3.ZERO, Vector3.UP)
	else:
		var mc_pos := Vector3(-12.0, 50.0, -220.0)
		var mc_look := Vector3(-12.0, 0.0, -3.0)
		main_cam.position = mc_pos
		main_cam.look_at(mc_look, Vector3.UP)
	main_cam.current = true
	_scene_root.add_child(main_cam)
	_main_cam = main_cam


# ------------------------------------------------------------------
# Environment loading
# ------------------------------------------------------------------
func _load_environment(env_name: String) -> void:
	var env_map := {
		"office":        "res://scenes/environments/office.tscn",
		"plaza":         "res://scenes/environments/plaza.tscn",
		"city":          "res://scenes/environments/city.tscn",
		"outdoor":       "res://scenes/environments/city.tscn",
		"neighbourhood": "res://scenes/environments/city.tscn",
		"warehouse":     "res://scenes/environments/city.tscn",
		"hospital":      "res://scenes/environments/office.tscn",
	}
	var path: String = env_map.get(env_name, "res://scenes/environments/office.tscn")
	if not ResourceLoader.exists(path):
		push_warning("[FleetManager] env scene not found: " + path + " — using default ground")
		_add_default_ground()
		return
	var env_scene: PackedScene = load(path)
	var env_node := env_scene.instantiate()
	_scene_root.add_child(env_node)
	_env_node = env_node


func _add_default_ground() -> void:
	var gm := MeshInstance3D.new()
	gm.mesh = PlaneMesh.new()
	(gm.mesh as PlaneMesh).size = Vector2(100.0, 100.0)
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.3, 0.3, 0.3)
	gm.material_override = mat
	_scene_root.add_child(gm)
	_env_node = gm


# ------------------------------------------------------------------
# Vehicle spawning
# ------------------------------------------------------------------
func _spawn_vehicle(drone_id: String, vtype: String,
					spawn_enu: Vector3, _photoreal: bool) -> void:
	var model_path: String
	if vtype == "rover":
		model_path = "res://scenes/vehicles/rover.tscn"
	else:
		model_path = "res://scenes/vehicles/quadcopter.tscn"

	var node: Node3D
	if ResourceLoader.exists(model_path):
		node = (load(model_path) as PackedScene).instantiate()
	else:
		# Fallback: coloured box so the fleet still works without art assets.
		node = _make_placeholder(vtype)

	node.name = drone_id
	_scene_root.add_child(node)
	_apply_pose_enu(node, spawn_enu, 0.0)

	# Disable shadow casting on vehicle meshes — prevents vehicle shadow
	# from appearing in the vehicle's own camera view.
	_disable_shadows(node)

	# Per-vehicle SubViewport for camera readback.
	# IMPORTANT: SubViewport is NOT a Node3D; it cannot inherit 3D transforms from its
	# Node3D parent. We therefore share the main scene's World3D (own_world_3d=false)
	# and manually sync the camera's global_position in _integrate() every frame.
	var vp := SubViewport.new()
	vp.size = Vector2i(VIEWPORT_W, VIEWPORT_H)
	vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	vp.transparent_bg = false
	vp.own_world_3d = false  # share main scene world — camera sees the building

	var cam := Camera3D.new()
	cam.fov = 70.0
	var spawn_godot := Vector3(spawn_enu.x, spawn_enu.z, -spawn_enu.y)
	var params := _cam_params(vtype)
	cam.position = spawn_godot + params[0]
	cam.rotation_degrees = Vector3(params[1], 180.0, 0.0)
	cam.current = true

	vp.add_child(cam)
	node.add_child(vp)

	var st := VehicleState.new(drone_id, vtype, spawn_enu, node, vp, cam)
	_vehicles[drone_id] = st
	print("[FleetManager] spawned %s (%s) at ENU %v" % [drone_id, vtype, spawn_enu])


static func _disable_shadows(node: Node) -> void:
	# Recursively disable shadow casting on all GeometryInstance3D nodes.
	# This prevents the vehicle from casting a shadow into its own camera view.
	if node is GeometryInstance3D:
		(node as GeometryInstance3D).cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	for child in node.get_children():
		_disable_shadows(child)


func _make_placeholder(vtype: String) -> Node3D:
	var n := Node3D.new()
	var mi := MeshInstance3D.new()
	mi.mesh = BoxMesh.new()
	if vtype == "rover":
		(mi.mesh as BoxMesh).size = Vector3(0.5, 0.3, 0.3)
	else:
		(mi.mesh as BoxMesh).size = Vector3(0.35, 0.1, 0.35)
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.9, 0.4, 0.1) if vtype == "rover" else Color(0.2, 0.6, 1.0)
	mi.material_override = mat
	n.add_child(mi)
	return n


# ------------------------------------------------------------------
# Physics integration (called every frame)
# ------------------------------------------------------------------
func _physics_process(delta: float) -> void:
	for st in _vehicles.values():
		_integrate(st, delta)


func _integrate(st: VehicleState, dt: float) -> void:
	# Linear movement.
	if st.velocity_active:
		var dpos := st.velocity_cmd * dt
		if st.vtype == "rover":
			dpos.z = 0.0
		st.position += dpos
	elif st.goal != Vector3.INF:
		var delta := st.goal - st.position
		var dist := delta.length()
		if dist < 0.05:
			st.position = st.goal
			st.goal = Vector3.INF
		else:
			st.position += delta.normalized() * min(st.max_lin * dt, dist)

	if st.vtype == "rover":
		st.position.z = 0.0

	# Yaw integration.
	if not is_nan(st.goal_yaw):
		var dy := wrapf(st.goal_yaw - st.yaw, -PI, PI)
		var step: float = sign(dy) * min(MAX_YAW_RAD * dt, abs(dy))
		st.yaw += step
		if abs(dy) < deg_to_rad(1.0):
			st.yaw = st.goal_yaw
			st.goal_yaw = NAN

	_apply_pose_enu(st.node, st.position, st.yaw)

	# Sync SubViewport camera to vehicle world position.
	var godot_pos := Vector3(st.position.x, st.position.z, -st.position.y)
	var params := _cam_params(st.vtype)
	st.camera.position = godot_pos + params[0]
	var yaw_deg: float = -rad_to_deg(st.yaw)
	st.camera.rotation_degrees = Vector3(params[1], yaw_deg + 180.0, 0.0)


# Returns [offset: Vector3 (Godot), tilt_degrees: float] for a vehicle's POV camera.
# 0.5m forward (Godot +Z) clears all vehicle body geometry (quad arms 0.3m, rover visor 0.21m).
func _cam_params(vtype: String) -> Array:
	if _env_name == "plaza":
		if vtype == "quadcopter":
			# 8m altitude, chairs ~15m ahead and below — look down to frame them.
			return [Vector3(0.0, 0.0, 0.5), -20.0]
		else:
			# Ground rover, chairs ahead at near-eye level — slight down tilt.
			return [Vector3(0.0, 1.0, 0.5), -3.0]
	elif _env_name in ["city", "outdoor", "neighbourhood"]:
		if vtype == "quadcopter":
			# 10m altitude — slight nose-down to frame the street below.
			return [Vector3(0.0, 0.0, 0.5), -15.0]
		else:
			# Ground rover — forward-looking from ~1m height.
			return [Vector3(0.0, 1.0, 0.5), 0.0]
	else:
		# Office
		if vtype == "quadcopter":
			return [Vector3(0.0, 0.0, 0.5), 0.0]
		else:
			return [Vector3(0.0, 1.5, 0.5), 5.0]


# ENU → Godot coordinate conversion and pose application.
static func _apply_pose_enu(node: Node3D, pos_enu: Vector3, yaw_enu: float) -> void:
	# ENU: x=east, y=north, z=up
	# Godot: x=right(east), y=up, z=-forward(-north)
	node.position = Vector3(pos_enu.x, pos_enu.z, -pos_enu.y)
	node.rotation_degrees = Vector3(0.0, -rad_to_deg(yaw_enu), 0.0)


# ------------------------------------------------------------------
# IPC handlers (called from IpcServer, main thread)
# ------------------------------------------------------------------
func get_state(drone_id: String):
	var st: VehicleState = _vehicles.get(drone_id)
	if st == null:
		return null
	return {"position": st.position, "yaw": st.yaw}


func set_goal(drone_id: String, pos_enu: Vector3) -> void:
	var st: VehicleState = _vehicles.get(drone_id)
	if st == null:
		return
	if st.vtype == "rover":
		pos_enu.z = 0.0
	st.goal = pos_enu
	st.velocity_active = false


func set_velocity(drone_id: String, vel_enu: Vector3) -> void:
	var st: VehicleState = _vehicles.get(drone_id)
	if st == null:
		return
	var n := vel_enu.length()
	if n > st.max_lin:
		vel_enu = vel_enu / n * st.max_lin
	st.velocity_cmd = vel_enu
	st.velocity_active = true
	st.goal = Vector3.INF


func clear_velocity(drone_id: String) -> void:
	var st: VehicleState = _vehicles.get(drone_id)
	if st == null:
		return
	st.velocity_active = false
	st.velocity_cmd = Vector3.ZERO


func set_yaw(drone_id: String, yaw_rad: float) -> void:
	var st: VehicleState = _vehicles.get(drone_id)
	if st == null:
		return
	st.goal_yaw = yaw_rad


# ------------------------------------------------------------------
# Runtime reconfigure (hot session start without relaunching Godot)
# ------------------------------------------------------------------
# Add one vehicle at an explicit ENU position. No-op if it already exists.
func spawn(drone_id: String, vtype: String, pos_enu: Vector3) -> void:
	if drone_id in _vehicles:
		return
	if _scene_root == null:
		push_error("[FleetManager] spawn before scene ready")
		return
	_spawn_vehicle(drone_id, vtype, pos_enu, false)


# Add one vehicle, auto-placing it using the per-environment layout.
func spawn_auto(drone_id: String, vtype: String) -> void:
	if drone_id in _vehicles:
		return
	var idx := _vehicles.size()
	spawn(drone_id, vtype, _spawn_pos(vtype, idx, idx + 1))


# Remove one vehicle and free its node + SubViewport/camera.
func despawn(drone_id: String) -> void:
	var st: VehicleState = _vehicles.get(drone_id)
	if st == null:
		return
	if is_instance_valid(st.node):
		st.node.queue_free()  # frees child SubViewport + camera too
	_vehicles.erase(drone_id)
	print("[FleetManager] despawned %s" % drone_id)


# Swap the environment scene at runtime. Clears vantages (env-specific framing)
# so a subsequent auto_overhead/add_vantage recomputes them. Does NOT touch
# vehicles — the caller manages vehicle lifecycle (despawn before/after).
func load_env(env_name: String) -> void:
	if _scene_root == null:
		return
	if env_name == _env_name and _env_node != null:
		return
	# Free the old environment + main vantage cam.
	if is_instance_valid(_env_node):
		_env_node.queue_free()
		_env_node = null
	if is_instance_valid(_main_cam):
		_main_cam.queue_free()
		_main_cam = null
	# Free all vantage cameras (positions are env-specific).
	for name in _vantages.keys():
		var v: Dictionary = _vantages[name]
		var holder = v.get("node")
		if is_instance_valid(holder):
			holder.queue_free()
	_vantages.clear()
	# Load the new environment.
	_env_name = env_name
	_load_environment(env_name)
	_add_main_vantage()
	print("[FleetManager] env swapped -> %s" % env_name)


func grab_frame_jpeg(drone_id: String) -> Variant:
	var st: VehicleState = _vehicles.get(drone_id)
	if st == null:
		return null
	var img := st.viewport.get_texture().get_image()
	if img == null:
		return null
	img.convert(Image.FORMAT_RGB8)
	var jpg_buf := img.save_jpg_to_buffer(float(JPEG_QUALITY) / 100.0)
	if jpg_buf.is_empty():
		return null
	return Marshalls.raw_to_base64(jpg_buf)


# ------------------------------------------------------------------
# Vantage cameras
# ------------------------------------------------------------------
func add_vantage(name: String, pos_enu: Vector3, look_enu: Vector3) -> void:
	if name in _vantages:
		return
	var godot_pos := Vector3(pos_enu.x, pos_enu.z, -pos_enu.y)
	var godot_look := Vector3(look_enu.x, look_enu.z, -look_enu.y)

	var vp := SubViewport.new()
	vp.size = Vector2i(1280, 720)
	vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	vp.own_world_3d = false  # share main scene world — sees the building

	var cam := Camera3D.new()
	cam.fov = 60.0
	cam.position = godot_pos
	cam.look_at(godot_look, Vector3.UP)
	vp.add_child(cam)

	# SubViewport goes in a Node3D holder so the scene tree is tidy.
	var holder := Node3D.new()
	holder.name = "vantage_" + name
	holder.add_child(vp)
	_scene_root.add_child(holder)

	_vantages[name] = {"node": holder, "viewport": vp, "camera": cam}
	print("[FleetManager] vantage '%s' at ENU %v -> %v" % [name, pos_enu, look_enu])


func auto_overhead(name: String = "overhead") -> void:
	if name in _vantages:
		return
	if _env_name in ["city", "outdoor", "neighbourhood"]:
		# Angled overhead looking into the street grid from above.
		add_vantage(name, Vector3(0.0, -25.0, 40.0), Vector3.ZERO)
		return
	if _vehicles.is_empty():
		add_vantage(name, Vector3(0.0, 0.0, 60.0), Vector3.ZERO)
		return
	var pts: Array[Vector3] = []
	for st in _vehicles.values():
		pts.append(st.position)
	var center := Vector3.ZERO
	for p in pts:
		center += p
	center /= float(pts.size())
	# Office/default: pull back to frame the building facade.
	var building_center := Vector3(-12.0, 3.0, 0.0)  # ENU (building centroid)
	var cam_pos := Vector3(0.0, 320.0, 120.0)        # ENU: far in front, elevated
	add_vantage(name, cam_pos, building_center)


func grab_vantage_jpeg(name: String) -> Variant:
	var v: Dictionary = _vantages.get(name, {})
	if v.is_empty():
		return null
	var vp: SubViewport = v.get("viewport")
	if vp == null:
		return null
	var img := vp.get_texture().get_image()
	if img == null:
		return null
	img.convert(Image.FORMAT_RGB8)
	var jpg_buf := img.save_jpg_to_buffer(float(JPEG_QUALITY) / 100.0)
	if jpg_buf.is_empty():
		return null
	return Marshalls.raw_to_base64(jpg_buf)


# ------------------------------------------------------------------
# Roster / grid helpers
# ------------------------------------------------------------------
static func _parse_roster(fleet_str: String) -> Array[Dictionary]:
	var roster: Array[Dictionary] = []
	var parts := fleet_str.split(",")
	for part in parts:
		part = part.strip_edges()
		if part.is_empty():
			continue
		var kv := part.split(":")
		var vtype := "quadcopter"
		var count := 1
		if kv.size() >= 1:
			var t := kv[0].strip_edges().to_lower()
			if t in ["rover", "rovers"]:
				vtype = "rover"
			elif t in ["quad", "quadcopter", "quadcopters"]:
				vtype = "quadcopter"
		if kv.size() >= 2:
			count = kv[1].strip_edges().to_int()
		for i in range(count):
			var suffix := "-%02d" % (roster.size() + 1)
			roster.append({"id": "sim-%s%s" % [vtype, suffix], "type": vtype})
	return roster


static func _grid(n: int, spacing: float = 3.0) -> Array:
	var cols: int = max(1, int(ceil(sqrt(float(n)))))
	var out := []
	for i in range(n):
		var r: int = i / cols
		var c: int = i % cols
		out.append([float(c) * spacing, float(r) * spacing])
	return out

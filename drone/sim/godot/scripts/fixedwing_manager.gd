## FixedWingManager: owns Depot fixed-wing simulation — kinematics, detection,
## occupancy/observed grids, battery, safety guard, injects, and the event log.
## Separate from FleetManager (which owns the quad/rover fleet); fixed-wings are not
## part of that roster.
##
## Coordinate convention: all IPC ops speak the ENU ground frame {x, y, z, yaw} —
## x=east metres, y=north metres, z=up metres, yaw radians CCW from +x (east).
## FixedWingManager converts to/from Godot space (Godot x = ENU x, Godot z = -ENU y,
## Godot y = ENU z) internally; nothing outside this file should need to know about
## Godot axes.
##
## Grid resolution / units: occupancy + observed grids are 1.0 m/cell, row-major u8
## (0=free/unobserved, 1=occupied/observed), base64-encoded for IPC transport.
extends Node

const LAYER_STRUCTURE := 1
const LAYER_PROPS := 2
const LAYER_FIXEDWING := 4

const MAX_AIRSPEED := 25.0        # m/s
const MIN_AIRSPEED := 12.0       # m/s
const MAX_YAW_RATE := 0.6        # rad/s
const MAX_ROLL := 45.0           # degrees
const MAX_CLIMB_RATE := 8.0      # m/s
const MIN_CLIMB_RATE := -12.0    # m/s
const DETECT_RANGE := 80.0       # m
const DETECT_HFOV_HALF := deg_to_rad(30.0)  # 60° horizontal FOV
const DETECT_VFOV_HALF := deg_to_rad(17.5)  # 35° vertical FOV
const DETECT_LOOK_DOWN := deg_to_rad(15.0)  # 15° downward look
const GRID_RES := 1.0
# Sized for the flightline env's prop spread (water_tower/silo/barn out to
# x~320, y~-90..90 — see env_flightline.gd), not the old ~100m depot world this
# was originally copied from. A 100x100 grid centered near the origin silently
# excluded every fixed-wing prop from ever being "observed" (coverage metrics
# would read ~0 regardless of actual flight coverage) — never noticed before
# because, per the Slice 1 investigation, fixed-wing sim had never actually run.
const GRID_ORIGIN := Vector2(-50.0, -150.0)
const GRID_W := 400
const GRID_H := 300
const NEAR_MISS_DIST := 2.0
const BASE_DRAIN_IDLE := 0.01   # %/s
const BASE_DRAIN_PER_M := 0.005   # %/m
const CAM_VIEWPORT_W := 640
const CAM_VIEWPORT_H := 480
const CAM_FOV := 70.0
const CAM_JPEG_QUALITY := 85
const NOSE_OFFSET_M := 3.0  # forward of body's own origin — see _sync_camera

var _env: Node3D = null
var _fw: Dictionary = {}   # id -> FixedWingState
var _events: Array = []    # [{t, kind, data}]
var _sim_time: float = 0.0
var _occ: PackedByteArray = PackedByteArray()


class FixedWingState:
	var id: String
	var node: Node3D
	var position := Vector3.ZERO   # ENU
	var velocity := Vector3.ZERO   # ENU
	var airspeed := 0.0
	var climb_rate := 0.0
	var pitch := 0.0
	var yaw := 0.0
	var roll := 0.0
	var altitude := 0.0
	var battery_level := 100.0
	var cmd_airspeed := 0.0
	var cmd_yaw_rate := 0.0
	var observed: PackedByteArray = PackedByteArray()
	var alive := true
	var last_pose_trace: float = 0.0
	var drain_mult: float = 1.0
	var viewport: SubViewport = null   # forward-facing camera readback (fw_grab_frame)
	var camera: Camera3D = null


func _ready() -> void:
	_occ.resize(GRID_W * GRID_H)


func register_env(env_node: Node3D) -> void:
	_env = env_node
	_rebuild_occ_grid()


# ------------------------------------------------------------------
# Spawn / despawn
# ------------------------------------------------------------------
func spawn(id: String, pos: Vector3, yaw: float) -> void:
	if _env == null:
		return
	if id in _fw:
		# Re-spawning an id that's still registered (e.g. a test harness looping
		# reset()+spawn() on the same id across attempts within one Godot process)
		# must actually move the fixed-wing back to `pos`/`yaw` — a prior no-op here let
		# the fixed-wing silently keep whatever position it drifted to in the last
		# attempt, so a 3-attempt "identical scenario" loop was secretly 3 different
		# scenarios with progressively worse geometry. Confirmed via a live trace.
		var st: FixedWingState = _fw[id]
		st.position = pos
		st.yaw = yaw
		st.velocity = Vector3.ZERO
		st.airspeed = MIN_AIRSPEED
		st.climb_rate = 0.0
		st.pitch = 0.0
		st.roll = 0.0
		_apply_pose(st)
		return
	var body := Node3D.new()
	body.name = "fw_" + id
	_env.add_child(body)

	var mesh := Node3D.new()
	mesh.set_script(load("res://scripts/fixedwing_visuals.gd"))
	body.add_child(mesh)

	# Forward-facing camera for fw_grab_frame — same SubViewport+Camera3D pattern
	# as fleet_manager.gd's per-vehicle quad/rover cameras (own_world_3d=false so
	# it sees the real scene). SubViewport doesn't inherit Node3D transforms even
	# as a child, so position/rotation are synced manually every tick in
	# _sync_camera(), same reason fleet_manager.gd's _integrate() does the same.
	var vp := SubViewport.new()
	vp.size = Vector2i(CAM_VIEWPORT_W, CAM_VIEWPORT_H)
	vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	vp.own_world_3d = false
	var cam := Camera3D.new()
	cam.fov = CAM_FOV
	# Camera3D.current is scoped PER-VIEWPORT, not global — it does not compete
	# with the main window's camera or other vehicles' cameras, each of which
	# lives in its own dedicated SubViewport. Setting this false (based on a
	# wrong assumption it was a global flag) meant this SubViewport had no
	# active camera at all and never rendered real scene geometry — every
	# fw_grab_frame capture was some camera-less fallback render (a flat
	# gradient), not actual terrain/props, confirmed by capturing the identical
	# position/orientation via the known-working vantage-camera path instead
	# and seeing a normal, correctly lit scene. fleet_manager.gd's own
	# per-vehicle camera already does this correctly (cam.current = true).
	cam.current = true
	vp.add_child(cam)
	body.add_child(vp)

	var st := FixedWingState.new()
	st.id = id
	st.node = body
	st.position = pos
	st.yaw = yaw
	st.airspeed = MIN_AIRSPEED
	st.climb_rate = 0.0
	st.pitch = 0.0
	st.roll = 0.0
	st.altitude = pos.z
	st.observed.resize(GRID_W * GRID_H)
	st.viewport = vp
	st.camera = cam
	_fw[id] = st
	_apply_pose(st)
	print("[FixedWingManager] spawned %s at ENU %v" % [id, pos])


func despawn(id: String) -> void:
	var st: FixedWingState = _fw.get(id)
	if st == null:
		return
	if is_instance_valid(st.node):
		st.node.queue_free()
	_fw.erase(id)


# ------------------------------------------------------------------
# Physics tick
# ------------------------------------------------------------------
func _physics_process(delta: float) -> void:
	_sim_time += delta
	for st in _fw.values():
		_step(st, delta)
	_check_geofence()


func _step(st: FixedWingState, dt: float) -> void:
	# Apply flight dynamics
	_apply_flight_dynamics(st, dt)
	
	# Update position and velocity
	st.position += st.velocity * dt
	st.altitude = st.position.z
	
	# Apply pose
	_apply_pose(st)
	
	# Update battery
	var moved := st.velocity.length() * dt
	st.battery_level = max(0.0, st.battery_level - (BASE_DRAIN_IDLE * dt + BASE_DRAIN_PER_M * moved) * st.drain_mult)
	
	# Sweep observed grid
	_sweep_observed(st)
	
	# Log events
	if _sim_time - st.last_pose_trace >= 1.0:
		st.last_pose_trace = _sim_time
		_log_event("pose_trace", {"id": st.id, "x": st.position.x, "y": st.position.y, "z": st.position.z})


func _apply_flight_dynamics(st: FixedWingState, dt: float) -> void:
	# Smooth airspeed toward command
	var airspeed_delta := st.cmd_airspeed - st.airspeed
	var airspeed_acc := 5.0  # m/s^2 acceleration
	st.airspeed += clamp(airspeed_delta * airspeed_acc * dt, -abs(airspeed_delta), abs(airspeed_delta))
	st.airspeed = clamp(st.airspeed, MIN_AIRSPEED, MAX_AIRSPEED)
	
	# Yaw integration
	st.yaw = wrapf(st.yaw + st.cmd_yaw_rate * dt, -PI, PI)
	
	# Pitch calculation: climb_rate / airspeed with clamping
	st.pitch = clamp(atan2(st.climb_rate, st.airspeed), deg_to_rad(-30.0), deg_to_rad(20.0))
	
	# Roll calculation: banking turn rate
	var roll_rate := st.cmd_yaw_rate * st.airspeed / 9.81  # g = 9.81 m/s^2
	st.roll = clamp(roll_rate, -deg_to_rad(MAX_ROLL), deg_to_rad(MAX_ROLL))
	
	# Climb rate integration
	st.climb_rate = clamp(st.climb_rate, MIN_CLIMB_RATE, MAX_CLIMB_RATE)
	
	# Convert to velocity vector in ENU coordinates
	var forward := Vector3(cos(st.yaw), sin(st.yaw), 0.0)
	var up := Vector3(0.0, 0.0, 1.0)
	var right := forward.cross(up).normalized()
	var pitch_vector := forward.rotated(up, st.pitch)
	st.velocity = pitch_vector * st.airspeed + Vector3(0.0, 0.0, st.climb_rate)


func _apply_pose(st: FixedWingState) -> void:
	# ENU: x=east, y=north, z=up
	# Godot: x=right(east), y=up, z=-forward(-north)
	st.node.position = Vector3(st.position.x, st.position.z, -st.position.y)

	# Rotation in YXZ Euler (pitch around x, yaw around y, roll around z)
	var rotation_degrees = Vector3(rad_to_deg(st.pitch), -rad_to_deg(st.yaw) + 90.0, rad_to_deg(st.roll))
	st.node.rotation_degrees = rotation_degrees
	_sync_camera(st)


func _sync_camera(st: FixedWingState) -> void:
	if st.camera == null:
		return
	# Camera position/orientation are set directly here every tick rather than
	# relying on scene-tree parenting, same pattern as fleet_manager.gd's
	# _integrate(). Uses look_at() — the exact mechanism add_vantage() already
	# uses successfully — rather than assigning rotation_degrees/global_rotation
	# directly: this file's Euler convention (-yaw+90 etc., calibrated for
	# fixedwing_visuals.gd's own +Z-forward mesh, in _apply_pose above) does not
	# carry over to Camera3D (whose local forward is Godot's default -Z), and
	# getting the axis/sign conversion right by hand proved genuinely
	# error-prone. look_at() sidesteps all of that by construction.
	var elevation := st.pitch - DETECT_LOOK_DOWN
	var forward_enu := Vector3(cos(st.yaw) * cos(elevation), sin(st.yaw) * cos(elevation), sin(elevation))
	var forward_godot := Vector3(forward_enu.x, forward_enu.z, -forward_enu.y)
	# NOSE_OFFSET_M forward of the airframe's own origin — without this the
	# camera sits exactly at body's position, which is INSIDE
	# fixedwing_visuals.gd's fuselage mesh (it surrounds the body's origin).
	# This was the actual root cause of every "washed out" forward-camera
	# capture: not a rotation/lighting/tonemap bug (all independently
	# eliminated first — a vantage camera at the mathematically identical
	# position/direction rendered correctly whenever no aircraft mesh was
	# present at that point), but the camera rendering from inside solid
	# geometry. Confirmed live: this exact change alone fixed it.
	var cam_pos := Vector3(st.position.x, st.position.z, -st.position.y) + forward_godot * NOSE_OFFSET_M
	st.camera.global_position = cam_pos
	st.camera.look_at(cam_pos + forward_godot * 50.0, Vector3.UP)


# ------------------------------------------------------------------
# ENU/Godot conversion helpers, used by the sensing functions below.
#
# ENU is (x=east, y=north, z=up); Godot is (x=east, y=up, z=-north). detect(),
# unproject(), and _sweep_observed() all used to build "3D" vectors as
# Vector3(east, north, up) and feed them straight into Godot-space math (which
# expects Vector3(east, up, -north)) — silently swapping the "north" and "up"
# components. This corrupted horizontal bearing, vertical elevation, and
# occlusion raycasts for any prop with nonzero altitude, live from day one
# (confirmed empirically: FixedWingManager's autoload never even loaded before
# the tab-indentation fix above, so this code had never been exercised against
# a real environment). Centralizing the conversion here so it can't drift again.
# ------------------------------------------------------------------
static func _enu_dir_to_godot(east: float, north: float, up: float) -> Vector3:
	return Vector3(east, up, -north)


static func _godot_rel_to_enu(rel: Vector3) -> Vector3:
	# rel is a Godot-space delta (target_godot_pos - cam_godot_pos); returns the
	# (east, north, up) components of that same delta.
	return Vector3(rel.x, -rel.z, rel.y)


# ------------------------------------------------------------------
# IPC: state / detect / unproject
# ------------------------------------------------------------------
func get_state(id: String) -> Variant:
	var st: FixedWingState = _fw.get(id)
	if st == null:
		return null
	return {
		"position": [st.position.x, st.position.y, st.position.z],
		"velocity": [st.velocity.x, st.velocity.y, st.velocity.z],
		"airspeed": st.airspeed,
		"climb_rate": st.climb_rate,
		"pitch": st.pitch,
		"yaw": st.yaw,
		"roll": st.roll,
		"altitude": st.altitude,
		"battery_level": st.battery_level
	}


## Forward-camera JPEG readback for the on-device VLM loop (backends.SimBackend.
## capture_frame) — same byte-for-byte pattern as fleet_manager.gd's
## grab_frame_jpeg/grab_vantage_jpeg (get_texture -> convert RGB8 -> JPEG ->
## base64). Requires a real rendering driver (gui=True on this Mac) — headless
## Godot's "dummy" driver leaves SubViewport textures blank, same limitation
## already documented for grab_vantage in fw_eval.py.
func grab_frame_jpeg(id: String) -> Variant:
	var st: FixedWingState = _fw.get(id)
	if st == null or st.viewport == null:
		return null
	var img := st.viewport.get_texture().get_image()
	if img == null:
		return null
	img.convert(Image.FORMAT_RGB8)
	var jpg_buf := img.save_jpg_to_buffer(float(CAM_JPEG_QUALITY) / 100.0)
	if jpg_buf.is_empty():
		return null
	return Marshalls.raw_to_base64(jpg_buf)


func detect(id: String) -> Array:
	var st: FixedWingState = _fw.get(id)
	if st == null or _env == null:
		return []
	
	var cam_pos := Vector3(st.position.x, st.position.z, -st.position.y)
	var forward := Vector2(cos(st.yaw), sin(st.yaw))
	var out: Array = []
	var candidates: Array = _env.props.duplicate()
	
	for node in candidates:
		if not is_instance_valid(node):
			continue
		var wp := Vector2(node.position.x, -node.position.z)
		var to_target := wp - Vector2(st.position.x, st.position.y)
		var dist := to_target.length()
		if dist > DETECT_RANGE or dist < 0.01:
			continue
		
		# node.position is already Godot-space, built the same way cam_pos is (see
		# env_flightline.gd / env_depot.gd _make_prop), so this delta is a genuine
		# Godot-space vector — convert to (east,north,up) to compute bearing/
		# elevation in the same ENU terms the rest of this file uses.
		var target_pos: Vector3 = node.position
		var rel_enu := _godot_rel_to_enu(target_pos - cam_pos)
		var rel_east := rel_enu.x
		var rel_north := rel_enu.y
		var rel_up := rel_enu.z

		# Project onto horizontal plane
		var horiz_dist := Vector2(rel_east, rel_north).length()
		if horiz_dist < 0.01:
			continue

		# Angle from forward
		var angle := forward.angle_to(Vector2(rel_east, rel_north).normalized())
		# atan2(rel_up, horiz_dist) is signed positive-up; DETECT_LOOK_DOWN pitches
		# the boresight down, i.e. to elevation -DETECT_LOOK_DOWN, so centering
		# vert_angle on that boresight means *adding* DETECT_LOOK_DOWN here (a
		# target level with the aircraft, rel_up=0, should read near the edge of
		# the downward-pitched FOV, not need to be near the aircraft's own altitude).
		var vert_angle := atan2(rel_up, horiz_dist) + DETECT_LOOK_DOWN
		if absf(angle) > DETECT_HFOV_HALF or absf(vert_angle) > DETECT_VFOV_HALF:
			continue

		# Check if occluded
		var hit := _raycast(cam_pos, target_pos, LAYER_STRUCTURE | LAYER_PROPS, [st.node, node])
		if not hit.is_empty():
			continue

		var label: String = node.get_meta("label", "unknown")

		# Calculate confidence based on distance
		var range_penalty: float = clamp((dist - 40.0) / 40.0, 0.0, 1.0) * 0.3
		var confidence: float = clamp(0.9 - range_penalty, 0.2, 0.9)

		# Calculate normalized coordinates
		var nx: float = clamp(0.5 + (angle / DETECT_HFOV_HALF) * 0.5, 0.0, 1.0)
		var ny: float = clamp(0.5 + (vert_angle / DETECT_VFOV_HALF) * 0.5, 0.0, 1.0)

		out.append({
			"label": label,
			"confidence": confidence,
			"nx": nx,
			"ny": ny,
			"world": [wp.x, wp.y, target_pos.y]
		})
	return out


func unproject(id: String, nx: float, ny: float) -> Variant:
	var st: FixedWingState = _fw.get(id)
	if st == null:
		return null
		
	var bearing_h := (nx - 0.5) * 2.0 * DETECT_HFOV_HALF
	# Inverse of detect()'s `vert_angle := atan2(rel_up, horiz_dist) + DETECT_LOOK_DOWN`:
	# bearing_v here is the absolute (signed-positive-up) elevation, so DETECT_LOOK_DOWN
	# is subtracted, not added.
	var bearing_v := (ny - 0.5) * 2.0 * DETECT_VFOV_HALF - DETECT_LOOK_DOWN

	# First, try to match a known visible object at this bearing
	if _env != null:
		var cam_pos := Vector3(st.position.x, st.position.z, -st.position.y)
		var forward := Vector2(cos(st.yaw), sin(st.yaw))
		var candidates: Array = _env.props.duplicate()
		var best_node = null
		var best_diff := 0.15  # radians
		for node in candidates:
			if not is_instance_valid(node):
				continue
			var wp := Vector2(node.position.x, -node.position.z)
			var to_target := wp - Vector2(st.position.x, st.position.y)
			var dist := to_target.length()
			if dist > DETECT_RANGE or dist < 0.01:
				continue
				
			var target_pos: Vector3 = node.position
			var rel_enu := _godot_rel_to_enu(target_pos - cam_pos)
			var horiz_dist := Vector2(rel_enu.x, rel_enu.y).length()
			if horiz_dist < 0.01:
				continue

			var angle := forward.angle_to(Vector2(rel_enu.x, rel_enu.y).normalized())
			var vert_angle := atan2(rel_enu.z, horiz_dist) + DETECT_LOOK_DOWN
			if absf(angle) > DETECT_HFOV_HALF or absf(vert_angle) > DETECT_VFOV_HALF:
				continue

			var hit_obj := _raycast(cam_pos, target_pos, LAYER_STRUCTURE | LAYER_PROPS, [st.node, node])
			if not hit_obj.is_empty():
				continue
			var diff := absf(angle - bearing_h)
			if diff < best_diff:
				best_diff = diff
				best_node = node
		if best_node != null:
			return [best_node.position.x, -best_node.position.z, best_node.position.y]

	# Fallback: horizontal raycast
	var dir_h := Vector2(cos(st.yaw + bearing_h), sin(st.yaw + bearing_h))
	var dir_v := _enu_dir_to_godot(dir_h.x, dir_h.y, tan(bearing_v)).normalized()
	var from3 := Vector3(st.position.x, st.position.z, -st.position.y)
	var to3 := from3 + dir_v * DETECT_RANGE
	var hit := _raycast(from3, to3, LAYER_STRUCTURE | LAYER_PROPS, [st.node])
	if hit.is_empty():
		return null
	var p: Vector3 = hit["position"]
	return [p.x, -p.z, p.y]


# ------------------------------------------------------------------
# IPC: drive / stop
# ------------------------------------------------------------------
func drive(id: String, airspeed: float, yaw_rate: float) -> void:
	var st: FixedWingState = _fw.get(id)
	if st == null:
		return
	st.cmd_airspeed = clamp(airspeed, -MAX_AIRSPEED, MAX_AIRSPEED)
	st.cmd_yaw_rate = clamp(yaw_rate, -MAX_YAW_RATE, MAX_YAW_RATE)


func stop(id: String) -> void:
	# Set airspeed to minimum, zero yaw and climb rate (loiter, not true stop)
	var st: FixedWingState = _fw.get(id)
	if st == null:
		return
	st.cmd_airspeed = MIN_AIRSPEED
	st.cmd_yaw_rate = 0.0
	st.climb_rate = 0.0


# ------------------------------------------------------------------
# IPC: occupancy/observed grid
# ------------------------------------------------------------------
func _rebuild_occ_grid() -> void:
	if _env == null:
		return
	_occ.fill(0)
	var rects: Array = []
	for r in _env.WALLS:
		rects.append(r)
	for door_id in _env.doors.keys():
		var panel = _env.doors[door_id]
		if panel.visible:
			var d: Array = _env.DOOR_PANELS[door_id]["rect"]
			rects.append(Rect2(d[0], d[1], d[2], d[3]))
	
	# Cell-vs-rect overlap, not cell-center containment
	for gy in range(GRID_H):
		for gx in range(GRID_W):
			var wx := GRID_ORIGIN.x + gx * GRID_RES
			var wy := GRID_ORIGIN.y + gy * GRID_RES
			var cell := Rect2(wx, wy, GRID_RES, GRID_RES)
			for rect in rects:
				if rect.intersects(cell):
					_occ[gy * GRID_W + gx] = 1
					break


func _sweep_observed(st: FixedWingState) -> void:
	var n_rays := 40
	var cam_pos := Vector3(st.position.x, st.position.z, -st.position.y)
	for i in range(n_rays):
		var t: float = float(i) / float(n_rays - 1)
		var bearing_h: float = lerp(-DETECT_HFOV_HALF, DETECT_HFOV_HALF, t)
		# Boresight elevation, signed positive-up: pitched down by DETECT_LOOK_DOWN.
		var bearing_v := -DETECT_LOOK_DOWN
		var dir_h := Vector2(cos(st.yaw + bearing_h), sin(st.yaw + bearing_h))
		var dir_v := _enu_dir_to_godot(dir_h.x, dir_h.y, tan(bearing_v)).normalized()
		var to3 := cam_pos + dir_v * DETECT_RANGE
		var hit := _raycast(cam_pos, to3, LAYER_STRUCTURE, [st.node])
		var end_pt: Vector2 = Vector2(st.position.x, st.position.y) + dir_h * DETECT_RANGE
		if not hit.is_empty():
			var p: Vector3 = hit["position"]
			end_pt = Vector2(p.x, -p.z)
		_mark_line_observed(st, Vector2(st.position.x, st.position.y), end_pt)


func _mark_line_observed(st: FixedWingState, a: Vector2, b: Vector2) -> void:
	var steps := int(a.distance_to(b) / (GRID_RES * 0.5)) + 1
	for i in range(steps + 1):
		var t: float = float(i) / float(max(steps, 1))
		var p: Vector2 = a.lerp(b, t)
		var gx := int((p.x - GRID_ORIGIN.x) / GRID_RES)
		var gy := int((p.y - GRID_ORIGIN.y) / GRID_RES)
		if gx >= 0 and gx < GRID_W and gy >= 0 and gy < GRID_H:
			st.observed[gy * GRID_W + gx] = 1


func get_grid(id: String) -> Variant:
	var st: FixedWingState = _fw.get(id)
	if st == null:
		return null
	return {
		"res": GRID_RES,
		"origin": [GRID_ORIGIN.x, GRID_ORIGIN.y],
		"w": GRID_W,
		"h": GRID_H,
		"occ": Marshalls.raw_to_base64(_occ),
		"obs": Marshalls.raw_to_base64(st.observed)
	}


# ------------------------------------------------------------------
# IPC: injects
# ------------------------------------------------------------------
func inject(name: String, params: Dictionary) -> void:
	match name:
		"block_door", "close_door":
			var door_id: String = params.get("door", "")
			if _env and door_id in _env.doors:
				_env.set_door_closed(door_id, true)
				_rebuild_occ_grid()
		"open_door":
			var door_id2: String = params.get("door", "")
			if _env and door_id2 in _env.doors:
				_env.set_door_closed(door_id2, false)
				_rebuild_occ_grid()
		"battery_drain":
			var rate: float = float(params.get("rate", 1.0))
			var target_id = params.get("id", "")
			for st in _fw.values():
				if target_id == "" or st.id == target_id:
					st.drain_mult = rate
		"tip_ladder":
			if _env:
				_env.tip_ladder()
		"kill_fixedwing":
			var rid: String = params.get("id", "")
			despawn(rid)
		"raise_wall":
			# "Wall Went Up" replan scenario: a real obstacle appears mid-flight,
			# not just a fleet-DSL no-fly zone. center/half are ENU (east,north)
			# meters — same convention prop_truth()/detect() already use.
			var center: Array = params.get("center", [0.0, 0.0])
			var half: Array = params.get("half", [30.0, 30.0])
			var height: float = float(params.get("height", 60.0))
			if _env and _env.has_method("add_wall"):
				var rect := Rect2(
					float(center[0]) - float(half[0]), float(center[1]) - float(half[1]),
					float(half[0]) * 2.0, float(half[1]) * 2.0
				)
				_env.add_wall(rect, height)
				_rebuild_occ_grid()
	_log_event("inject_fired", {"name": name, "params": params})


# ------------------------------------------------------------------
# IPC: ground-truth oracle (harness/scoring only — never fed to the brain's
# sensing API; use fw_detect/fw_grid for anything the agent itself sees).
# ------------------------------------------------------------------
func prop_truth() -> Array:
	if _env == null:
		return []
	var out: Array = []
	for p in _env.props:
		if not is_instance_valid(p):
			continue
		out.append({
			"label": p.get_meta("label", ""),
			"world": [p.position.x, -p.position.z, p.position.y],
			"is_anomaly": p.get_meta("is_anomaly", false)
		})
	return out


# ------------------------------------------------------------------
# IPC: events / reset
# ------------------------------------------------------------------
func _log_event(kind: String, data: Dictionary) -> void:
	_events.append({"t": _sim_time, "kind": kind, "data": data})


## Structured telemetry contract (Slice 2): lets the on-device reasoning layer
## (MissionLoop, via backends.SimBackend.log_event) push clarification/replan/
## memory_landmark events into this same event log, alongside the internal
## pose_trace/inject_fired events, so replan/memory behavior is inspectable via
## the existing fw_events() call rather than needing a second telemetry channel.
func log_event(id: String, kind: String, data: Dictionary) -> void:
	var merged := data.duplicate()
	merged["id"] = id
	_log_event(kind, merged)


func get_events(id: String) -> Array:
	var out: Array = []
	for e in _events:
		if e["data"].has("id") and e["data"]["id"] == id:
			out.append(e)
	return out


func reset(id: String) -> void:
	var st: FixedWingState = _fw.get(id)
	if st == null:
		return
	st.battery_level = 100.0
	st.cmd_airspeed = 0.0
	st.cmd_yaw_rate = 0.0
	st.position = Vector3.ZERO
	st.velocity = Vector3.ZERO
	st.airspeed = MIN_AIRSPEED
	st.climb_rate = 0.0
	st.pitch = 0.0
	st.yaw = 0.0
	st.roll = 0.0
	st.altitude = 0.0
	st.observed.fill(0)
	_apply_pose(st)


func _check_geofence() -> void:
	if _env == null:
		return
	# In this implementation, we don't enforce geofence as fixed-wings fly at altitudes
	# and have less constrained motion than rovers; the environment is just used for
	# obstacles and detection purposes
	pass


# ------------------------------------------------------------------
# Raycasting helpers
# ------------------------------------------------------------------
func _raycast(from3: Vector3, to3: Vector3, mask: int, exclude_nodes: Array) -> Dictionary:
	if _env == null:
		return {}
	var space := _env.get_world_3d().direct_space_state
	if space == null:
		return {}
	var q := PhysicsRayQueryParameters3D.create(from3, to3)
	q.collision_mask = mask
	var rids: Array[RID] = []
	for n in exclude_nodes:
		if n is CollisionObject3D:
			rids.append((n as CollisionObject3D).get_rid())
	q.exclude = rids
	return space.intersect_ray(q)
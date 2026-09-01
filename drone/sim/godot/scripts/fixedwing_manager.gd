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
const ALT_FLOOR_M := 2.0         # hard envelope floor (see _apply_flight_dynamics)
const ALT_CEILING_M := 120.0     # hard envelope ceiling
const DETECT_RANGE := 80.0       # m — default; see DETECT_RANGE_BY_LABEL
## Per-class sensing range. A single 80 m cutoff for everything was wrong in
## both directions: a 50 m wall is visible from far further away than a person,
## and a dropped bottle from far less. Keeping people at 80 m is deliberate —
## it is exactly what forces the aircraft to descend for a close identification
## pass instead of calling the target from cruise altitude.
const DETECT_RANGE_BY_LABEL := {
	# A 50 m wall is visible a long way off, and the number here is really
	# "how much warning does the aircraft get". At 220 m, starting 280 m out,
	# the model flew blind for the first 60 m and then had about two decisions
	# before the obstacle — it could describe the wall perfectly and still not
	# finish choosing in time. Warning distance is thinking time.
	"wall": 400.0,
	"aircraft": 300.0,
	"water_bottle": 40.0,
	# Ground vehicles: bigger and higher-contrast than a person, so picked up
	# from farther than the 80 m person default, but still well inside cruise
	# range — the aircraft must still fly out and close in to localize/confirm,
	# it can't call a truck from the launch point. All three spellings the
	# detector/tasking might use map to the same range (see mission_vocab's
	# vehicle nouns).
	"pickup truck": 150.0,
	"truck": 150.0,
	"car": 150.0,
}
const PEER_DETECT_RANGE := 300.0  # m — see the peer pass in detect()
# The sensing cone MUST be the camera's cone. detect() decides what the mission
# is told exists, the frame decides what the model can see, and when those two
# disagree the model is looking straight at something the system swears is not
# there — and the grounding guard then overrides its perfectly true report as
# unfounded. They disagreed badly: these were hand-set to a 60°x35° cone while
# the camera renders 86°x70°, so detect() denied roughly half the visible frame.
# Measured, not derived: a subject 16.1° below boresight rendered at ny 0.722,
# which is 70° vertical, not 35°.
#
# Godot's Camera3D.fov is the VERTICAL angle under the default KEEP_HEIGHT
# aspect policy, and horizontal follows from the viewport aspect ratio. Both are
# computed from CAM_FOV here so they cannot drift apart again.
var DETECT_VFOV_HALF := deg_to_rad(CAM_FOV * 0.5)
var DETECT_HFOV_HALF := atan(tan(deg_to_rad(CAM_FOV * 0.5))
	* float(CAM_VIEWPORT_W) / float(CAM_VIEWPORT_H))
const DETECT_LOOK_DOWN := deg_to_rad(15.0)  # 15° downward look
# Grid geometry is per-env, not global: an open-field scene wants 1 m cells over
# a few hundred metres, a city wants coarser cells over kilometres. These are
# `var` so register_env() can take an env's grid_config() override; the defaults
# below are the historical values, so any env that doesn't override keeps
# byte-identical behaviour.
var GRID_RES := 1.0
# Sized for the flightline env's prop spread (water_tower/silo/barn out to
# x~320, y~-90..90 — see env_flightline.gd), not the old ~100m depot world this
# was originally copied from. A 100x100 grid centered near the origin silently
# excluded every fixed-wing prop from ever being "observed" (coverage metrics
# would read ~0 regardless of actual flight coverage) — never noticed before
# because, per the Slice 1 investigation, fixed-wing sim had never actually run.
var GRID_ORIGIN := Vector2(-50.0, -150.0)
# Widened east (was 400, covering x[-50,350]) when the SAR wall moved out to
# e=250 to give the aircraft a realistic run-in. The grid has to span every
# structure the aircraft might fly at: _occupied() reports FALSE outside it, so
# a wall beyond the edge is invisible to both the envelope guard and the
# leg-crossing check, which fail open rather than closed.
var GRID_W := 600
var GRID_H := 300
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
var _bottles: Array = []   # released payloads awaiting rest — see _update_bottles()


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
	var cmd_climb_rate := 0.0
	var observed: PackedByteArray = PackedByteArray()
	## Where the sensor points relative to the airframe's nose, in radians.
	##
	## Without this the demo's central beat is impossible: the forward camera
	## AND detect() both derive from st.yaw, so an aircraft flying a circle can
	## never see the point it is circling — it always looks along the tangent.
	## "Climb, orbit the tunnel and watch for him to come out" would be a drone
	## staring at the horizon. A real aircraft would use a gimbal; this is that
	## gimbal, and it must be applied IDENTICALLY in _sync_camera() and
	## detect(), or the model reasons about a picture it was never shown.
	var sensor_yaw_offset := 0.0
	var payload_remaining := 1
	var alive := true
	var last_pose_trace: float = 0.0
	var drain_mult: float = 1.0
	var viewport: SubViewport = null   # forward-facing camera readback (fw_grab_frame)
	var camera: Camera3D = null


func _ready() -> void:
	_occ.resize(GRID_W * GRID_H)


func register_env(env_node: Node3D) -> void:
	_env = env_node
	# An env may resize the world it needs covered. Envs that don't implement
	# grid_config() keep the defaults above, so their grids are unchanged.
	if env_node.has_method("grid_config"):
		var g: Dictionary = env_node.grid_config()
		GRID_RES = float(g.get("res", GRID_RES))
		GRID_W = int(g.get("w", GRID_W))
		GRID_H = int(g.get("h", GRID_H))
		var o = g.get("origin", null)
		if o != null:
			GRID_ORIGIN = Vector2(float(o[0]), float(o[1]))
		_occ.resize(GRID_W * GRID_H)
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
		st.cmd_climb_rate = 0.0
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

	# Engine buzz, positional: an AudioStreamPlayer3D on the airframe attenuates
	# with distance to the active camera (the main window's camera is the audio
	# listener), so the drone gets louder as it flies toward the viewer and
	# quieter as it heads away. Looping so it's continuous while airborne.
	# Disabled per request — call _attach_engine_audio(body) here to re-enable.

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
	cam.far = 40000.0   # see fleet_manager: 4000 m default clips a city-scale world
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


## A synthesized, seamlessly-looping propeller/engine buzz. Built in code rather
## than loaded from a .wav so it needs no Godot import step (running from the
## binary, not the editor, there is no importer to turn a raw .wav into an
## AudioStreamWAV — load() returns null).
static func _make_engine_stream() -> AudioStreamWAV:
	var sr := 22050
	var n := int(sr * 2.0)               # 2 s loop
	var f0 := 95.0                        # fundamental
	var data := PackedByteArray()
	data.resize(n * 2)                    # 16-bit mono
	var harmonics := [[1, 1.0], [2, 0.5], [3, 0.32], [4, 0.18], [6, 0.10]]
	for i in n:
		var t := float(i) / sr
		var am := 0.6 + 0.4 * sin(TAU * 11.0 * t)   # blade-pass warble (integer cycles => seamless)
		var s := 0.0
		for h in harmonics:
			s += float(h[1]) * sin(TAU * f0 * float(h[0]) * t)
		s *= am / 2.1
		data.encode_s16(i * 2, int(clampf(s, -1.0, 1.0) * 22000.0))
	var st := AudioStreamWAV.new()
	st.format = AudioStreamWAV.FORMAT_16_BITS
	st.mix_rate = sr
	st.stereo = false
	st.data = data
	st.loop_mode = AudioStreamWAV.LOOP_FORWARD
	st.loop_begin = 0
	st.loop_end = n
	return st


## Attach a looping, distance-attenuated engine buzz to an airframe body.
func _attach_engine_audio(body: Node3D) -> void:
	var stream := _make_engine_stream()
	var player := AudioStreamPlayer3D.new()
	player.name = "engine_audio"
	player.stream = stream
	player.autoplay = true
	player.unit_size = 25.0          # full volume within ~25 m, rolls off beyond;
	                                 # small enough that the near/far swing across
	                                 # this scene (~100-450 m to the window camera)
	                                 # is clearly audible (~13 dB)
	player.max_distance = 900.0
	player.volume_db = 4.0
	player.attenuation_model = AudioStreamPlayer3D.ATTENUATION_INVERSE_DISTANCE
	body.add_child(player)
	player.play()


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
	_update_bottles()
	_check_geofence()


func _step(st: FixedWingState, dt: float) -> void:
	# Apply flight dynamics
	_apply_flight_dynamics(st, dt)
	
	# Update position and velocity
	st.position += st.velocity * dt
	# Backstop the envelope in POSITION as well as in rate: zeroing climb_rate
	# (in _apply_flight_dynamics) only stops further descent, it cannot undo the
	# fraction of a tick that carried the aircraft past the limit. Without this
	# the altitude floor leaks by a few centimetres and "z never below the floor"
	# stops being a checkable invariant.
	st.position.z = clamp(st.position.z, ALT_FLOOR_M, ALT_CEILING_M)
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
	
	# Bank angle for a coordinated turn: tan(phi) = v * omega / g.
	#
	# Two fixes over the previous form. It used the raw ratio as an angle, which
	# is only valid for small banks — at 25 m/s and 0.6 rad/s the ratio is 1.53
	# (88 deg), so it pinned to the 45 deg limit for any real turn. And it
	# assigned the result straight to st.roll, so a step change in commanded yaw
	# rate snapped the aircraft from level to fully banked in one frame. Climb
	# rate is eased just below for exactly this reason ("a step change ... would
	# make the chase camera jerk"); roll needs the same treatment and is far more
	# visible, since bank is the whole visual language of a turning aircraft.
	# Sign: the visual model's nose is local +Z and its wings run along local +-X
	# (fixedwing_visuals.gd swizzles the source asset's length onto Z and its span
	# onto X). With up at +Y, the RIGHT wing is therefore -X, so a positive roll
	# about +Z lifts the LEFT wing — a right bank. A positive yaw rate is CCW in
	# ENU, i.e. a LEFT turn. Taking the bank straight from the yaw rate therefore
	# banked the aircraft away from its own turn; hence the leading minus.
	var bank_target: float = -atan(st.cmd_yaw_rate * st.airspeed / 9.81)
	bank_target = clamp(bank_target, -deg_to_rad(MAX_ROLL), deg_to_rad(MAX_ROLL))
	# ~1.0 s to roll in or out, in the range real light aircraft manage.
	var roll_tc := 1.0
	st.roll += (bank_target - st.roll) * minf(1.0, dt / roll_tc)
	
	# Climb rate: ease toward the command, then clamp.
	#
	# Until the SAR demo work there was no climb COMMAND at all — climb_rate was
	# integrated into velocity and pitch (below) but nothing outside this file
	# could ever set it, so every fixed-wing flew the whole mission frozen at its
	# spawn altitude. The descend-to-identify and climb-to-orbit beats need real
	# vertical control, so the command is wired here.
	#
	# Eased with a ~1.5 s time constant rather than applied instantly: a step
	# change in climb rate would snap st.pitch (computed from it just above) and
	# make the chase camera jerk, which shows up directly in the demo footage.
	var climb_tc := 1.5
	st.climb_rate += (st.cmd_climb_rate - st.climb_rate) * minf(1.0, dt / climb_tc)
	# Aerodynamic limit: a fixed-wing cannot out-climb its own airspeed. Capping
	# to a 20° flight-path angle keeps commanded climbs physically honest at low
	# airspeed instead of letting a 12 m/s aircraft climb at 8 m/s (a 42° angle).
	var climb_limit: float = tan(deg_to_rad(20.0)) * st.airspeed
	st.climb_rate = clamp(st.climb_rate, -climb_limit, climb_limit)
	st.climb_rate = clamp(st.climb_rate, MIN_CLIMB_RATE, MAX_CLIMB_RATE)

	# Envelope protection: hard altitude floor/ceiling. The demo deliberately
	# flies low run-ins, so a model that commands an over-aggressive descent must
	# be caught by the vehicle rather than allowed to fly into the ground — and
	# the intervention must be VISIBLE in telemetry, since "zero envelope
	# interventions" is one of the demo's take-selection gates.
	if st.position.z <= ALT_FLOOR_M and st.climb_rate < 0.0:
		st.climb_rate = 0.0
		st.cmd_climb_rate = 0.0
		_log_event("envelope_protection", {"id": st.id, "limit": "alt_floor",
			"alt": st.position.z, "floor": ALT_FLOOR_M})
	elif st.position.z >= ALT_CEILING_M and st.climb_rate > 0.0:
		st.climb_rate = 0.0
		st.cmd_climb_rate = 0.0
		_log_event("envelope_protection", {"id": st.id, "limit": "alt_ceiling",
			"alt": st.position.z, "ceiling": ALT_CEILING_M})
	
	# Convert to velocity vector in ENU coordinates.
	#
	# The nose must point where the aircraft is going. This previously read
	# `forward.rotated(up, st.pitch)` — a rotation of the heading about the UP
	# axis by the PITCH angle, which is a yaw, not a pitch. Any climb or descent
	# therefore swung the velocity sideways off the nose by the pitch angle and
	# the aircraft visibly crabbed through climbing turns. (The `right` vector
	# computed alongside it was never used, which is the tell.)
	#
	# Vertical motion is already carried by climb_rate, and st.pitch is *derived*
	# from climb_rate above — so pitching the vector at all would double-count
	# it. Horizontal velocity is along the nose; vertical is the climb rate.
	var forward := Vector3(cos(st.yaw), sin(st.yaw), 0.0)
	st.velocity = forward * st.airspeed + Vector3(0.0, 0.0, st.climb_rate)


func _apply_pose(st: FixedWingState) -> void:
	# ENU: x=east, y=north, z=up
	# Godot: x=right(east), y=up, z=-forward(-north)
	st.node.position = Vector3(st.position.x, st.position.z, -st.position.y)

	# Rotation in YXZ Euler (pitch around x, yaw around y, roll around z).
	# The mesh's nose is local +Z (fixedwing_visuals.gd). Under a Godot +Y
	# rotation φ, local +Z points to world (sinφ, 0, cosφ); to make the nose
	# follow the ENU heading (cos yaw, sin yaw) — i.e. Godot (cos yaw, 0,
	# -sin yaw) — we need φ = yaw + 90°, NOT -yaw + 90° (that reflected the north
	# component, so the nose pointed the right way only for due-east/west travel
	# and backwards for north/south).
	# Pitch is NEGATED here, and only here.
	#
	# st.pitch is atan2(climb_rate, airspeed): positive means climbing, which is
	# the sign the sensor code wants (see the elevation calc in detect()). But the
	# mesh's nose is local +Z, and a positive Godot rotation about X maps
	# (0,0,1) -> (0, -sin, cos) — it drives the nose DOWN. So feeding st.pitch
	# straight in flew the aircraft nose-down in a climb and nose-up in a dive.
	#
	# Negating at the render site rather than flipping st.pitch itself keeps
	# "positive = climbing" true everywhere else in the file; the same split the
	# roll sign already needs, for the same reason (local +X is the LEFT wing).
	var rotation_degrees = Vector3(-rad_to_deg(st.pitch), rad_to_deg(st.yaw) + 90.0, rad_to_deg(st.roll))
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
	# sensor_yaw_offset must be applied here and in detect() with the same sign,
	# or the model is shown one scene and told about another.
	var look_yaw := st.yaw + st.sensor_yaw_offset
	var forward_enu := Vector3(cos(look_yaw) * cos(elevation), sin(look_yaw) * cos(elevation), sin(elevation))
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
		"battery_level": st.battery_level,
		"payload_remaining": st.payload_remaining,
		"sensor_yaw_offset": st.sensor_yaw_offset
	}


## Forward-camera JPEG readback for the on-device VLM loop (backends.SimBackend.
## capture_frame) — same byte-for-byte pattern as fleet_manager.gd's
## grab_frame_jpeg/grab_vantage_jpeg (get_texture -> convert RGB8 -> JPEG ->
## base64). Requires a real rendering driver (gui=True on this Mac) — headless
## Godot's "dummy" driver leaves SubViewport textures blank, same limitation
## already documented for grab_vantage in fw_eval.py.
## Rebuild an aircraft's forward-camera SubViewport from scratch.
##
## Necessary because the render target can freeze permanently: once another
## Metal client on this machine starts heavy GPU work — in practice the
## on-device VLM, which is exactly what runs alongside the sim in every real
## mission run — the SubViewport stops updating and grab_frame_jpeg() keeps
## returning the same bytes forever, while detect() (pure CPU geometry) carries
## on reporting the truth. That divergence is silent and dangerous: it looks
## like a blind model rather than a stale camera, and it invalidated an entire
## VLM gate run before being caught by comparing capture bytes across poses.
##
## Waiting longer does not clear it — measured, the bytes stay identical
## indefinitely. Rebuilding the viewport does. This is the same class of
## frozen-render-target failure already documented for vantage cameras in
## papers/fixed_wing_sitl_lessons_learned.md, whose recovery is likewise
## remove-and-recreate.
##
## The new viewport needs at least one rendered frame before it has content, so
## callers must let a frame or two pass between reset_camera() and the next
## grab_frame_jpeg().
func reset_camera(id: String) -> void:
	var st: FixedWingState = _fw.get(id)
	if st == null or st.node == null:
		return
	if st.viewport != null:
		st.viewport.queue_free()
	var vp := SubViewport.new()
	vp.size = Vector2i(CAM_VIEWPORT_W, CAM_VIEWPORT_H)
	vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	vp.own_world_3d = false
	var cam := Camera3D.new()
	cam.fov = CAM_FOV
	cam.far = 40000.0   # see fleet_manager: 4000 m default clips a city-scale world
	cam.current = true   # per-viewport scoped — see spawn()
	vp.add_child(cam)
	st.node.add_child(vp)
	st.viewport = vp
	st.camera = cam
	_sync_camera(st)


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
	# Same sensor offset the camera uses (see FixedWingState.sensor_yaw_offset).
	var forward := Vector2(cos(st.yaw + st.sensor_yaw_offset), sin(st.yaw + st.sensor_yaw_offset))
	var out: Array = []
	var candidates: Array = _env.props.duplicate()

	for node in candidates:
		if not is_instance_valid(node):
			continue
		var wp := Vector2(node.position.x, -node.position.z)
		var to_target := wp - Vector2(st.position.x, st.position.y)
		var dist := to_target.length()
		# Range is per class, so the label has to be read before the cutoff.
		var node_label: String = node.get_meta("label", "unknown")
		var max_range: float = DETECT_RANGE_BY_LABEL.get(node_label, DETECT_RANGE)
		if dist > max_range or dist < 0.01:
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

		var label: String = node_label

		# Calculate confidence based on distance
		var range_penalty: float = clamp((dist - 40.0) / 40.0, 0.0, 1.0) * 0.3
		var confidence: float = clamp(0.9 - range_penalty, 0.2, 0.9)

		# Calculate normalized coordinates. ny is IMAGE convention (0 = top row),
		# so it runs opposite to elevation — see unproject() for how getting this
		# backwards mirrored every pixel the model picked.
		var nx: float = clamp(0.5 + (angle / DETECT_HFOV_HALF) * 0.5, 0.0, 1.0)
		var ny: float = clamp(0.5 - (vert_angle / DETECT_VFOV_HALF) * 0.5, 0.0, 1.0)

		var row := {
			"label": label,
			"confidence": confidence,
			"nx": nx,
			"ny": ny,
			"world": [wp.x, wp.y, target_pos.y]
		}
		# Vertical extent, when the prop knows it. A sensed point tells you where
		# something is, not how tall it is, and for structure that difference
		# decides whether it can be overflown — see the marker note in env_sar.gd.
		if node.has_meta("top_z"):
			row["top_z"] = node.get_meta("top_z")
		out.append(row)

	# --- structure pass ------------------------------------------------------
	# Buildings are not props, and in a city that silence is dangerous.
	#
	# The props loop above only ever walks `_env.props`. In env_manhattan the
	# entire city is ONE MultiMesh with no per-building node and no "label"
	# metadata, and `props` holds just the target truck and the decoy car — so
	# 44,958 buildings are invisible to detect(). Since detect() is the only
	# obstacle channel the VLM has (situation.ObstacleTracker filters it for
	# label == "wall"), a model flying Manhattan would be told the route ahead
	# was clear while heading into midtown.
	#
	# An env that owns its structure analytically can therefore offer it here.
	# Rows come back in the same shape as the props pass and are labelled "wall",
	# which is what ObstacleTracker already understands — no change is needed
	# anywhere upstream, and envs without the method behave exactly as before.
	if _env.has_method("structure_detections"):
		var sensor_yaw: float = st.yaw + st.sensor_yaw_offset
		var cfg := {
			"hfov_half": DETECT_HFOV_HALF,
			"vfov_half": DETECT_VFOV_HALF,
			"look_down": DETECT_LOOK_DOWN,
			"range": float(DETECT_RANGE_BY_LABEL.get("wall", DETECT_RANGE)),
		}
		for row in _env.structure_detections(st.position, sensor_yaw, cfg):
			out.append(row)

	# --- peer pass -----------------------------------------------------------
	# Other aircraft are sensed with the same FOV and occlusion rules as props,
	# but out to PEER_DETECT_RANGE. This is the entire mechanism behind the
	# demo's comms-denied swarm claim: with no radio link between them, the only
	# way one aircraft can learn anything from another is by LOOKING at it — so
	# a teammate that has descended and started circling is readable as "it has
	# probably found something". A longer range than ground objects is a
	# disclosed stand-in for the fact that real aircraft carry transponders and
	# are far easier to spot against sky than a person is against terrain.
	for other_st in _fw.values():
		if other_st == st or not other_st.alive or other_st.node == null:
			continue
		var opos := Vector2(other_st.position.x, other_st.position.y)
		var odist := opos.distance_to(Vector2(st.position.x, st.position.y))
		if odist > PEER_DETECT_RANGE or odist < 0.01:
			continue
		var opos_godot := Vector3(other_st.position.x, other_st.position.z, -other_st.position.y)
		var orel := _godot_rel_to_enu(opos_godot - cam_pos)
		var ohoriz := Vector2(orel.x, orel.y).length()
		if ohoriz < 0.01:
			continue
		var oangle := forward.angle_to(Vector2(orel.x, orel.y).normalized())
		var overt := atan2(orel.z, ohoriz) + DETECT_LOOK_DOWN
		if absf(oangle) > DETECT_HFOV_HALF or absf(overt) > DETECT_VFOV_HALF:
			continue
		if not _raycast(cam_pos, opos_godot, LAYER_STRUCTURE, [st.node, other_st.node]).is_empty():
			continue
		out.append({
			"label": "aircraft",
			"confidence": clamp(0.9 - clamp((odist - 100.0) / 200.0, 0.0, 1.0) * 0.4, 0.2, 0.9),
			"nx": clamp(0.5 + (oangle / DETECT_HFOV_HALF) * 0.5, 0.0, 1.0),
			"ny": clamp(0.5 - (overt / DETECT_VFOV_HALF) * 0.5, 0.0, 1.0),  # image convention, see detect()
			"world": [other_st.position.x, other_st.position.y, other_st.position.z],
			"peer_id": other_st.id,
			"peer_alt": other_st.altitude,
		})
	return out


## Release the payload: a real ballistic body with the aircraft's velocity, not
## a teleport to the aim point. Where it lands is therefore a genuine
## consequence of the release solution the aircraft flew, which is the whole
## reason the delivery beat is worth filming — and it means a bad release
## visibly misses.
##
## Once the bottle comes to rest it is frozen and registered as a normal prop
## labelled "water_bottle", which makes it detectable BY BOTH AIRCRAFT. That is
## the demo's stigmergy channel: with no radio link, a bottle on the ground is
## a message the second drone can read from the environment itself.
func drop_payload(id: String) -> Variant:
	var st: FixedWingState = _fw.get(id)
	if st == null or _env == null:
		return null
	if st.payload_remaining <= 0:
		_log_event("payload_release_refused", {"id": id, "reason": "no payload remaining"})
		return null
	st.payload_remaining -= 1

	var body := RigidBody3D.new()
	body.name = "payload_%s_%d" % [id, _bottles.size() + 1]
	var col := CollisionShape3D.new()
	var shape := CylinderShape3D.new()
	shape.height = 0.28
	shape.radius = 0.05
	col.shape = shape
	body.add_child(col)
	var mesh := MeshInstance3D.new()
	var cm := CylinderMesh.new()
	cm.height = 0.28
	cm.top_radius = 0.05
	cm.bottom_radius = 0.05
	mesh.mesh = cm
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.25, 0.55, 0.95)
	mat.roughness = 0.6
	mesh.material_override = mat
	body.add_child(mesh)

	# Released from just below the airframe, carrying its velocity.
	body.position = Vector3(st.position.x, maxf(st.position.z - 0.6, 0.2), -st.position.y)
	body.linear_velocity = _enu_dir_to_godot(st.velocity.x, st.velocity.y, st.velocity.z)
	body.collision_layer = 0     # nothing senses it until it has landed
	body.collision_mask = 1      # but it does fall onto the ground
	_env.add_child(body)

	var release := {
		"id": id,
		"release_enu": [st.position.x, st.position.y, st.position.z],
		"velocity_enu": [st.velocity.x, st.velocity.y, st.velocity.z],
		"airspeed": st.airspeed,
		"t": _sim_time,
	}
	_bottles.append({"body": body, "t0": _sim_time, "landed": false, "release": release})
	_log_event("payload_released", release)
	return release


## Freeze bottles that have come to rest and promote them to sensable props.
## Time-capped as well as sleep-checked: a body that ends up on a slope or is
## nudged by geometry can jitter indefinitely without ever sleeping, and an
## un-promoted bottle would silently break the second drone's stigmergy cue.
func _update_bottles() -> void:
	for b in _bottles:
		if b["landed"]:
			continue
		var body: RigidBody3D = b["body"]
		if not is_instance_valid(body):
			b["landed"] = true
			continue
		var settled: bool = body.linear_velocity.length() < 0.25
		if not (settled or _sim_time - b["t0"] > 6.0):
			continue
		b["landed"] = true
		body.freeze = true
		body.linear_velocity = Vector3.ZERO
		body.set_meta("label", "water_bottle")
		body.set_meta("is_anomaly", false)
		body.collision_layer = 2   # LAYER_PROPS — now detectable by either aircraft
		_env.props.append(body)
		var rest := {"enu": [body.position.x, -body.position.z, body.position.y]}
		var rel: Dictionary = b["release"]
		var miss := Vector2(body.position.x, -body.position.z) - Vector2(
			rel["release_enu"][0], rel["release_enu"][1])
		_log_event("payload_landed", {"id": rel["id"], "rest_enu": rest["enu"],
			"throw_m": miss.length()})


## Point the sensor `offset_rad` off the nose (see FixedWingState.
## sensor_yaw_offset). Passing 0 re-centres it.
func set_sensor_yaw_offset(id: String, offset_rad: float) -> void:
	var st: FixedWingState = _fw.get(id)
	if st == null:
		return
	st.sensor_yaw_offset = wrapf(offset_rad, -PI, PI)
	_sync_camera(st)


## Aim the sensor at an ENU ground point, whatever the aircraft's heading.
## This is what an ORBIT_POINT action uses to keep the thing being watched in
## frame while flying a circle around it.
func aim_sensor_at(id: String, east: float, north: float) -> void:
	var st: FixedWingState = _fw.get(id)
	if st == null:
		return
	var bearing := atan2(north - st.position.y, east - st.position.x)
	set_sensor_yaw_offset(id, wrapf(bearing - st.yaw, -PI, PI))


func unproject(id: String, nx: float, ny: float) -> Variant:
	var st: FixedWingState = _fw.get(id)
	if st == null:
		return null
		
	var bearing_h := (nx - 0.5) * 2.0 * DETECT_HFOV_HALF
	# Inverse of detect()'s `vert_angle := atan2(rel_up, horiz_dist) + DETECT_LOOK_DOWN`:
	# bearing_v here is the absolute (signed-positive-up) elevation, so DETECT_LOOK_DOWN
	# is subtracted, not added.
	#
	# ny is IMAGE convention — 0 is the top row, 1 the bottom — because the only
	# thing that ever picks a pixel is a model looking at the rendered frame.
	# Elevation runs the other way (up is +), hence 0.5 - ny. This was `ny - 0.5`
	# for the whole life of the function, i.e. vertically mirrored: measured live,
	# unproject(0.5, 0.05) returned ground 56 m ahead while unproject(0.5, 0.95)
	# returned null for pointing at the sky, when the near ground is plainly at the
	# BOTTOM of the frame. Every navigate_to_point the model ever made was aimed at
	# the mirror image of what it picked, and a sensible pick low in the frame —
	# "the open ground just ahead" — was reported back as unresolvable.
	var bearing_v := (0.5 - ny) * 2.0 * DETECT_VFOV_HALF - DETECT_LOOK_DOWN

	# First, try to match a known visible object at this bearing
	if _env != null:
		var cam_pos := Vector3(st.position.x, st.position.z, -st.position.y)
		# Same sensor offset detect()/_sync_camera use — a pixel the model picked
		# out of the frame has to unproject through the direction the sensor was
		# actually pointing, not the airframe's nose.
		var forward := Vector2(cos(st.yaw + st.sensor_yaw_offset), sin(st.yaw + st.sensor_yaw_offset))
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
			# Angular distance in BOTH axes. This compared horizontal bearing
			# only, so every pixel in a column snapped to whatever prop stood in
			# that column — pointing at the open ground below someone, or at the
			# sky above them, both returned the person. `vert_angle` is measured
			# from the boresight while `bearing_v` is absolute elevation, hence
			# the DETECT_LOOK_DOWN term putting them in the same frame.
			var diff := Vector2(angle - bearing_h,
				vert_angle - (bearing_v + DETECT_LOOK_DOWN)).length()
			if diff < best_diff:
				best_diff = diff
				best_node = node
		if best_node != null:
			return [best_node.position.x, -best_node.position.z, best_node.position.y]

	# Fallback: horizontal raycast
	var dir_h := Vector2(cos(st.yaw + st.sensor_yaw_offset + bearing_h),
		sin(st.yaw + st.sensor_yaw_offset + bearing_h))
	var dir_v := _enu_dir_to_godot(dir_h.x, dir_h.y, tan(bearing_v)).normalized()
	var from3 := Vector3(st.position.x, st.position.z, -st.position.y)
	# Reach as far as the longest thing this sensor can see, not the default
	# people range — a pixel near the horizon corresponds to a point hundreds of
	# metres out, and clipping at 80 m made every such pick unresolvable.
	var reach: float = maxf(DETECT_RANGE, PEER_DETECT_RANGE)
	var to3 := from3 + dir_v * reach
	var hit := _raycast(from3, to3, LAYER_STRUCTURE | LAYER_PROPS, [st.node])
	if not hit.is_empty():
		var p: Vector3 = hit["position"]
		return [p.x, -p.z, p.y]

	# Nothing solid along the ray. If it is pointing downward at all it still
	# meets the ground, and that intersection is the honest answer — returning
	# null instead made the caller treat a perfectly ordinary "point at open
	# terrain ahead" pick as unresolvable, and (worse) fall through to a
	# no-op navigation path that let the aircraft keep flying straight ahead.
	if dir_v.y < -0.01:
		var t: float = (from3.y - 0.0) / -dir_v.y
		if t > 0.0 and t < reach * 4.0:
			var g := from3 + dir_v * t
			return [g.x, -g.z, 0.0]
	return null


# ------------------------------------------------------------------
# IPC: drive / stop
# ------------------------------------------------------------------
## `climb` is the commanded climb rate in m/s (positive up). It is optional so
## every existing two-argument caller keeps its current behaviour of holding
## altitude, rather than silently starting to descend.
func drive(id: String, airspeed: float, yaw_rate: float, climb: float = 0.0) -> void:
	var st: FixedWingState = _fw.get(id)
	if st == null:
		return
	st.cmd_airspeed = clamp(airspeed, -MAX_AIRSPEED, MAX_AIRSPEED)
	st.cmd_yaw_rate = clamp(yaw_rate, -MAX_YAW_RATE, MAX_YAW_RATE)
	st.cmd_climb_rate = clamp(climb, MIN_CLIMB_RATE, MAX_CLIMB_RATE)


func stop(id: String) -> void:
	# Set airspeed to minimum, zero yaw and climb rate (loiter, not true stop)
	var st: FixedWingState = _fw.get(id)
	if st == null:
		return
	st.cmd_airspeed = MIN_AIRSPEED
	st.cmd_yaw_rate = 0.0
	st.cmd_climb_rate = 0.0
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
	
	# Cell-vs-rect overlap, not cell-center containment.
	#
	# Iterates per RECT over just the cells it covers, rather than per cell over
	# every rect. Same result, but the cost is the total footprint area instead
	# of cells x rects — the old form was 180k x rects, which is fine for an env
	# with a dozen walls and completely infeasible for a city with tens of
	# thousands of buildings (it would hang on load rather than run slowly).
	for rect in rects:
		var r: Rect2 = rect
		# Half-open cell range covering the rect, clamped to the grid.
		var gx0 := int(floor((r.position.x - GRID_ORIGIN.x) / GRID_RES))
		var gy0 := int(floor((r.position.y - GRID_ORIGIN.y) / GRID_RES))
		var gx1 := int(ceil((r.position.x + r.size.x - GRID_ORIGIN.x) / GRID_RES))
		var gy1 := int(ceil((r.position.y + r.size.y - GRID_ORIGIN.y) / GRID_RES))
		gx0 = maxi(gx0, 0)
		gy0 = maxi(gy0, 0)
		gx1 = mini(gx1, GRID_W)
		gy1 = mini(gy1, GRID_H)
		# Still the exact Rect2.intersects() test, so a rect whose edge lands on
		# a cell boundary marks the same cells it always did — only the set of
		# cells considered has narrowed.
		for gy in range(gy0, gy1):
			var row := gy * GRID_W
			var wy := GRID_ORIGIN.y + gy * GRID_RES
			for gx in range(gx0, gx1):
				if _occ[row + gx] == 1:
					continue
				var cell := Rect2(GRID_ORIGIN.x + gx * GRID_RES, wy, GRID_RES, GRID_RES)
				if r.intersects(cell):
					_occ[row + gx] = 1


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

		"sar_config":
			# Scenario knobs for env_sar (tunnel dwell, forced actor phase,
			# target placement) reusing the existing inject channel rather than
			# adding a scenario-specific IPC op.
			if _env and _env.has_method("sar_config"):
				_env.sar_config(params)
	_log_event("inject_fired", {"name": name, "params": params})


# ------------------------------------------------------------------
# IPC: ground-truth oracle (harness/scoring only — never fed to the brain's
# sensing API; use fw_detect/fw_grid for anything the agent itself sees).
# ------------------------------------------------------------------
## Every live aircraft's ENU pose, for consumers that need the whole formation
## rather than one id: environments with proximity-triggered actors, and (from
## M4) the peer-sensing pass in detect(). Returns plain Dictionaries so the same
## shape can go over IPC unchanged.
func all_states() -> Array:
	var out: Array = []
	for st in _fw.values():
		if not st.alive:
			continue
		out.append({
			"id": st.id,
			"position": [st.position.x, st.position.y, st.position.z],
			"yaw": st.yaw,
			"altitude": st.altitude,
		})
	return out


## Environment-specific introspection, forwarded to whatever env is loaded.
## Scenario envs expose their actors' internal state (which phase the runner is
## in, where the target actually is) so a harness can assert on real scene truth
## instead of inferring it from detections — the same reason prop_truth() exists.
func env_state() -> Dictionary:
	if _env != null and _env.has_method("env_state"):
		return _env.env_state()
	return {}


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
	st.cmd_climb_rate = 0.0
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
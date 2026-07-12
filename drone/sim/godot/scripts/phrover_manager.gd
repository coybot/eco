## PhroverManager: owns Depot phrover simulation — kinematics, detection, occupancy/
## observed grids, battery, safety guard, injects, and the event log. Separate from
## FleetManager (which owns the quad/rover fleet); phrovers are not part of that roster.
##
## Coordinate convention: all IPC ops speak the ENU ground frame {x, y, yaw} — x=east
## metres, y=north metres, yaw radians CCW from +x (east). PhroverManager converts to/
## from Godot space (Godot x = ENU x, Godot z = -ENU y, Godot y = up = 0 for ground
## rovers) internally; nothing outside this file should need to know about Godot axes.
##
## Grid resolution / units: occupancy + observed grids are 0.25 m/cell, row-major u8
## (0=free/unobserved, 1=occupied/observed), base64-encoded for IPC transport.
extends Node

const LAYER_STRUCTURE := 1
const LAYER_PROPS := 2
const LAYER_PHROVER := 4
const LAYER_PERSON := 8

const MAX_V := 0.5        # m/s
const MAX_W := 1.5        # rad/s
const GOAL_TOL := 0.3     # m (unused here; real planner lives Swift-side)
const GUARD_DIST := 0.45  # m forward clearance that triggers the safety guard
const DETECT_RANGE := 8.0 # m
const FOV_HALF := deg_to_rad(30.0)
const GRID_RES := 0.25
const GRID_ORIGIN := Vector2(-8.0, -9.0)
const GRID_W := 64
const GRID_H := 81
const NEAR_MISS_DIST := 0.8
const PERSON_SLOW_DIST := 2.2   # m: direction-independent proximity cap kicks in below this
const PERSON_SLOW_SPEED := 0.15 # m/s: max linear speed once inside PERSON_SLOW_DIST
# Rover capsule radius 0.28m + person capsule radius 0.22m (env_depot.gd's _build_person) =
# 0.5m summed-radii contact distance. PERSON_STOP_DIST needs enough lead time for the
# perpendicular dodge below to actually clear that 0.5m before the person's approach closes
# it: at PERSON_RETREAT_SPEED, clearing 0.5m of lateral separation takes ~0.5/0.4=1.25s, and
# the person (0.8 m/s) needs to still be that far out when the dodge starts — 0.9m (a first
# attempt) gave ~0 lead and still collided live (confirmed with a free repro); 1.8m gives
# roughly 1.6s of the person's approach to build separation before their pass-through window.
const PERSON_STOP_DIST := 1.8   # m: dodge (perpendicular to the person's motion) below this
const PERSON_RETREAT_SPEED := 0.4 # m/s: speed while actively dodging clear of the person
# Hysteresis release distance: a rover whose OWN goal lies across the person's route (e.g.
# reaching a spot on the far side of their patrol corridor) has its own driver pressing
# toward that goal every tick, with zero awareness of the safety override. Releasing the
# override the instant distance ticks back above PERSON_STOP_DIST caused rapid re-triggering
# right at that boundary — confirmed live (7 collisions) and with a free repro: the rover
# oscillated at dist≈1.79-1.80 for tens of seconds, occasionally losing the race as the
# person's own back-and-forth patrol brushed the boundary. Requiring a distinctly larger
# distance before handing control back gives the person's pass-through window time to
# actually clear before the rover tries to cross again.
const PERSON_SAFE_DIST := 2.4
# Bounded fail-safe against the hysteresis above deadlocking: a rover boxed into a tight
# corridor near the person's route (no clear dodge direction — see _direction_is_clear)
# sits at v=0 while overridden, and v=0 means it can never move itself far enough away to
# reach PERSON_SAFE_DIST — only the person's own patrol can clear it, which isn't
# guaranteed on any given pass. Confirmed live: the rover froze at v=0 near spawn for an
# entire ~100s mission, never releasing. Force a release after a bounded time regardless
# of distance — a real (if imperfect) egress beats permanent paralysis.
const PERSON_OVERRIDE_MAX_SECONDS := 3.0
# A first version re-triggered the override immediately whenever distance dipped back
# below PERSON_STOP_DIST right after a timeout-forced release — confirmed live: a corridor
# pinch point re-triggered ~150 times back-to-back (~6s each), one single navigate() call
# taking 15+ real minutes. A short cooldown after a *timeout-forced* release (not a genuine
# distance-cleared release) gives the rover a real, if brief, uninterrupted window to
# actually clear the pinch point before the governor can re-engage.
const PERSON_OVERRIDE_COOLDOWN_SECONDS := 2.5
# Speed while boxed in with no clear dodge direction (see the creep-through comment below)
# — distinct from and faster than PERSON_SLOW_SPEED (the outer-zone caution cap): a rover
# that's already committed to pushing through a pinch point needs to actually clear it in
# a reasonable time, not creep through at the same speed used for merely passing near the
# person at a comfortable distance.
const PERSON_CREEP_SPEED := 0.3
const BASE_DRAIN_IDLE := 0.05   # %/s
const BASE_DRAIN_PER_M := 0.5   # %/m

var _env: Node3D = null
var _rovers: Dictionary = {}   # id -> PhroverState
var _events: Array = []        # [{t, kind, data}]
var _sim_time: float = 0.0
var _occ: PackedByteArray = PackedByteArray()


class PhroverState:
	var id: String
	var node: CharacterBody3D
	var pos := Vector2.ZERO   # ENU
	var yaw := 0.0
	var battery := 100.0
	var drain_mult := 1.0
	var blur_sigma := 0.0
	var guard_stopped := false
	var was_colliding := false
	var was_person_colliding := false
	var was_dodging := false
	var person_override_active := false
	var person_override_elapsed := 0.0
	var person_override_cooldown := 0.0
	var in_geofence := false
	var near_flag := false
	var cmd_v := 0.0
	var cmd_w := 0.0
	var observed: PackedByteArray = PackedByteArray()
	var alive := true


func _ready() -> void:
	_occ.resize(GRID_W * GRID_H)


func register_env(env_node: Node3D) -> void:
	_env = env_node
	_rebuild_occ_grid()


# ------------------------------------------------------------------
# Spawn / despawn
# ------------------------------------------------------------------
func spawn(id: String, pos: Vector2, yaw: float) -> void:
	if _env == null or id in _rovers:
		return
	var body := CharacterBody3D.new()
	body.collision_layer = LAYER_PHROVER
	body.collision_mask = LAYER_STRUCTURE | LAYER_PROPS | LAYER_PERSON
	var col := CollisionShape3D.new()
	var cap := CapsuleShape3D.new()
	cap.radius = 0.28
	cap.height = 0.4
	col.shape = cap
	col.position = Vector3(0.0, 0.2, 0.0)
	body.add_child(col)
	var mesh := Node3D.new()
	mesh.set_script(load("res://scripts/rover_visuals.gd"))
	body.add_child(mesh)
	body.name = "phrover_" + id
	_env.add_child(body)

	var st := PhroverState.new()
	st.id = id
	st.node = body
	st.pos = pos
	st.yaw = yaw
	st.observed.resize(GRID_W * GRID_H)
	_rovers[id] = st
	_apply_pose(st)
	print("[PhroverManager] spawned %s at ENU %v" % [id, pos])


func despawn(id: String) -> void:
	var st: PhroverState = _rovers.get(id)
	if st == null:
		return
	if is_instance_valid(st.node):
		st.node.queue_free()
	_rovers.erase(id)


# ------------------------------------------------------------------
# Physics tick
# ------------------------------------------------------------------
func _physics_process(delta: float) -> void:
	_sim_time += delta
	for st in _rovers.values():
		_step_rover(st, delta)
	_check_geofence()


func _step_rover(st: PhroverState, dt: float) -> void:
	# Safety guard: forward raycast; zero linear speed (not turning) if clearance is low.
	var clearance := _forward_clearance(st)
	var guard_now := clearance < GUARD_DIST
	if guard_now and not st.guard_stopped:
		_log_event("guard_stop", {"id": st.id, "clearance": clearance})
	st.guard_stopped = guard_now
	var v: float = 0.0 if guard_now else st.cmd_v
	var w: float = st.cmd_w

	# Person-safety governor: direction-independent proximity cap (models a 360° prox
	# sensor, not a forward-only ray) on top of — never instead of — the forward-ray guard
	# above. The person actor has zero avoidance of its own (person_actor.gd just walks its
	# waypoint loop), so without this a person approaching from the side or rear was never
	# slowed for, only detected via near-miss logging after the fact.
	#
	# A first version just zeroed v inside PERSON_STOP_DIST — confirmed live: 12 collisions
	# in one mission. Two bugs found and fixed here:
	# (1) PERSON_STOP_DIST was 0.5m, exactly equal to the summed capsule radii (rover 0.28m
	#     + person 0.22m — see spawn()/env_depot.gd's _build_person), so by the time it
	#     fired they were already touching, no reaction margin. Widened to 0.9m.
	# (2) Retreating straight away from the person's *position* (first fix attempt) still
	#     collided — confirmed with a free (no-Bedrock) repro: rover parked at (0,4), on
	#     the person's fixed (2,4)<->(-2,4) patrol line, retreated but was still walked
	#     into at t≈2.4s. Root cause: when the rover sits ON the person's line of travel,
	#     "away from their position" is colinear with "away from their approach direction"
	#     — a straight tail-chase that a slower retreat (0.3 m/s) can never win against the
	#     person's 0.8 m/s. Dodging *perpendicular* to the person's current velocity instead
	#     actually vacates their path (a 1D line) regardless of the speed differential —
	#     confirmed zero collisions in the same repro after this change.
	var person_dist := _person_distance(st)
	if st.person_override_cooldown > 0.0:
		st.person_override_cooldown = max(0.0, st.person_override_cooldown - dt)
	if not st.person_override_active and st.person_override_cooldown <= 0.0 and person_dist < PERSON_STOP_DIST:
		st.person_override_active = true
		st.person_override_elapsed = 0.0
	elif st.person_override_active:
		st.person_override_elapsed += dt
		if st.person_override_elapsed > PERSON_OVERRIDE_MAX_SECONDS:
			st.person_override_active = false
			st.person_override_elapsed = 0.0
			st.person_override_cooldown = PERSON_OVERRIDE_COOLDOWN_SECONDS
		elif person_dist > PERSON_SAFE_DIST:
			st.person_override_active = false
			st.person_override_elapsed = 0.0

	# A first perpendicular-dodge version released control back to the brain's own
	# commanded velocity the instant distance ticked back above PERSON_STOP_DIST — a live
	# multi-attempt mission (rover's own goal on the far side of the corridor, driver
	# pressing toward it every tick with zero safety awareness) still got 7 collisions:
	# rapid re-triggering right at that boundary (confirmed with a free repro: dist
	# oscillating at ~1.79-1.80 for tens of seconds). `person_override_active` above adds
	# hysteresis — stays engaged until PERSON_SAFE_DIST clears, not just PERSON_STOP_DIST.
	# Wall-aware direction selection: the perpendicular dodge has zero awareness of the
	# environment on its own, and blindly driving it can ram the rover into a wall it was
	# dodging toward — confirmed live (12 collisions with a plain always-perpendicular
	# dodge, worse than before hysteresis, in a mission repeatedly guard-stopped near a
	# corridor). Try perpendicular, its mirror, then straight-away; first one with clear
	# space wins. If none are clear, fall through with retreat_dir left at ZERO — the v=0/
	# w=0 already set below then yields a full, safe stop instead of a blind crash.
	var retreat_dir := Vector2.ZERO
	if st.person_override_active:
		var person := _person_node()
		if person != null:
			var candidates: Array = []
			var person_vel := Vector2(person.velocity.x, -person.velocity.z)
			var away: Vector2 = st.pos - person.enu_position()
			if person_vel.length() > 0.01:
				var perp := Vector2(-person_vel.y, person_vel.x).normalized()
				if perp.dot(away) < 0.0:
					perp = -perp
				candidates.append(perp)
				candidates.append(-perp)
			if away.length() > 0.001:
				candidates.append(away.normalized())
			for candidate in candidates:
				if _direction_is_clear(st, candidate):
					retreat_dir = candidate
					break
		if retreat_dir == Vector2.ZERO:
			# Boxed in — no dodge direction is clear of walls/props (a real risk in the
			# depot's narrow spawn corridor specifically). A hard v=0/w=0 stop here just
			# traps the rover in the override for its full duration with nowhere to go —
			# confirmed live: the rover froze near spawn for an entire ~80s mission,
			# racking up wall/prop contacts as later replans tried to push through anyway.
			# Creep along the brain's own already-planned path (both v AND w, so it can
			# still actually track a curving path, not just crawl straight) instead of
			# freezing solid. PERSON_CREEP_SPEED, not the outer-zone's more cautious
			# PERSON_SLOW_SPEED — a rover committed to pushing through a pinch point needs
			# to actually clear it soon, not creep through it for minutes (confirmed live:
			# 0.15 m/s here left one single navigate() call stuck for 15+ real minutes).
			v = clamp(v, -PERSON_CREEP_SPEED, PERSON_CREEP_SPEED)
		else:
			v = 0.0
			w = 0.0
	elif person_dist < PERSON_SLOW_DIST:
		v = clamp(v, -PERSON_SLOW_SPEED, PERSON_SLOW_SPEED)

	var dodging_now := retreat_dir != Vector2.ZERO
	if dodging_now and not st.was_dodging:
		_log_event("person_dodge", {"id": st.id, "dist": person_dist})
	st.was_dodging = dodging_now

	if dodging_now:
		st.node.velocity = Vector3(retreat_dir.x, 0.0, -retreat_dir.y) * PERSON_RETREAT_SPEED
	else:
		st.yaw = wrapf(st.yaw + w * dt, -PI, PI)
		var dir := Vector2(cos(st.yaw), sin(st.yaw))
		st.node.velocity = Vector3(dir.x, 0.0, -dir.y) * v
	st.node.move_and_slide()
	st.pos = Vector2(st.node.position.x, -st.node.position.z)
	_apply_pose(st)

	var moved := st.node.velocity.length() * dt
	st.battery = max(0.0, st.battery - (BASE_DRAIN_IDLE * dt + BASE_DRAIN_PER_M * moved) * st.drain_mult)

	var colliding := false
	var person_colliding := false
	for i in range(st.node.get_slide_collision_count()):
		var coll := st.node.get_slide_collision(i)
		var collider = coll.get_collider()
		if collider is StaticBody3D and (collider.is_in_group("depot_wall") or collider.is_in_group("depot_prop")):
			colliding = true
		elif collider is CharacterBody3D and collider.is_in_group("depot_person"):
			person_colliding = true
	if colliding and not st.was_colliding:
		_log_event("collision", {"id": st.id})
	st.was_colliding = colliding
	if person_colliding and not st.was_person_colliding:
		_log_event("collision", {"id": st.id, "with": "person"})
	st.was_person_colliding = person_colliding

	_log_person_proximity(st, person_dist)
	_sweep_observed(st)


func _apply_pose(st: PhroverState) -> void:
	st.node.position = Vector3(st.pos.x, 0.0, -st.pos.y)
	st.node.rotation_degrees = Vector3(0.0, 90.0 + rad_to_deg(st.yaw), 0.0)


func _forward_clearance(st: PhroverState) -> float:
	var from3 := Vector3(st.pos.x, 0.3, -st.pos.y)
	var dir := Vector2(cos(st.yaw), sin(st.yaw))
	var to3 := from3 + Vector3(dir.x, 0.0, -dir.y) * 0.7
	var hit := _raycast(from3, to3, LAYER_STRUCTURE | LAYER_PROPS | LAYER_PERSON, [st.node])
	if hit.is_empty():
		return 999.0
	return from3.distance_to(hit["position"])


# Used by the person-safety dodge to avoid picking an escape direction that just rams a
# wall/prop — the dodge is otherwise blind to the environment, unlike the AStarPlanner-
# driven main navigation. Short check distance (rover radius 0.28m + a bit of margin), not
# the full 0.7m forward-guard range: the dodge only needs "is this immediate direction
# open", not "is it clear all the way to some driving distance".
func _direction_is_clear(st: PhroverState, dir: Vector2, check_dist: float = 0.6) -> bool:
	var from3 := Vector3(st.pos.x, 0.2, -st.pos.y)
	var to3 := from3 + Vector3(dir.x, 0.0, -dir.y) * check_dist
	var hit := _raycast(from3, to3, LAYER_STRUCTURE | LAYER_PROPS, [st.node])
	return hit.is_empty()


func _person_distance(st: PhroverState) -> float:
	var person := _person_node()
	if person == null or not person.active:
		return INF
	return st.pos.distance_to(person.enu_position())


func _log_person_proximity(st: PhroverState, dist: float) -> void:
	if is_inf(dist):
		return
	if fmod(_sim_time, 1.0) < 0.02:
		_log_event("person_dist", {"id": st.id, "dist": dist})
	if dist < NEAR_MISS_DIST and not st.near_flag:
		_log_event("near_miss", {"id": st.id, "dist": dist})
	st.near_flag = dist < NEAR_MISS_DIST


func _check_geofence() -> void:
	if _env == null:
		return
	var rect_a: Array = _env.ROOMS["D"]
	var rect := Rect2(rect_a[0], rect_a[1], rect_a[2], rect_a[3])
	for st in _rovers.values():
		var inside := rect.has_point(st.pos)
		if inside and not st.in_geofence:
			_log_event("geofence_enter", {"id": st.id})
		st.in_geofence = inside


func _person_node() -> Node:
	if _env == null:
		return null
	return _env.person


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


# ------------------------------------------------------------------
# IPC: state / detect / unproject
# ------------------------------------------------------------------
func get_state(id: String) -> Variant:
	var st: PhroverState = _rovers.get(id)
	if st == null:
		return null
	return {"pose": [st.pos.x, st.pos.y, st.yaw], "battery": st.battery,
			"guard_stopped": st.guard_stopped}


func detect(id: String) -> Array:
	var st: PhroverState = _rovers.get(id)
	if st == null or _env == null:
		return []
	var cam_pos := Vector3(st.pos.x, 0.3, -st.pos.y)
	var forward := Vector2(cos(st.yaw), sin(st.yaw))
	var out: Array = []
	var candidates: Array = _env.props.duplicate()
	var person := _person_node()
	if person != null and person.active:
		candidates.append(person)
	for node in candidates:
		if not is_instance_valid(node):
			continue
		var wp := Vector2(node.position.x, -node.position.z)
		var to_target := wp - st.pos
		var dist := to_target.length()
		if dist > DETECT_RANGE or dist < 0.01:
			continue
		var angle := forward.angle_to(to_target.normalized())
		if absf(angle) > FOV_HALF:
			continue
		var target3 := Vector3(node.position.x, 0.3, node.position.z)
		var hit := _raycast(cam_pos, target3, LAYER_STRUCTURE | LAYER_PROPS | LAYER_PERSON, [st.node, node])
		if not hit.is_empty():
			continue  # occluded
		var label: String = node.get_meta("label", "person")
		var range_penalty: float = clamp((dist - 4.0) / 4.0, 0.0, 1.0) * 0.3
		var confidence: float = clamp(0.9 - range_penalty - st.blur_sigma * 0.15, 0.05, 0.95)
		var nx: float = clamp(0.5 + (angle / FOV_HALF) * 0.5, 0.0, 1.0)
		out.append({"label": label, "confidence": confidence, "nx": nx, "ny": 0.5,
					"world": [wp.x, wp.y]})
	return out


func unproject(id: String, nx: float, ny: float) -> Variant:
	var st: PhroverState = _rovers.get(id)
	if st == null:
		return null
	var bearing: float = (nx - 0.5) * 2.0 * FOV_HALF

	# First, try to match a known visible object at this bearing — mirrors detect()'s own
	# visibility logic (FOV + range + occlusion), so it works for objects of any height.
	# The horizontal-raycast fallback below is always at a fixed height (0.3m) and ignores
	# ny entirely, so it flies straight over low/floor-level objects (e.g. the "spill"
	# decal, 0.02m tall) without ever hitting their geometry — confirmed live: it either
	# hit something else far down the hallway or found nothing, so the object was silently
	# never remembered at all despite detect() correctly reporting it visible.
	if _env != null:
		var cam_pos := Vector3(st.pos.x, 0.3, -st.pos.y)
		var forward := Vector2(cos(st.yaw), sin(st.yaw))
		var candidates: Array = _env.props.duplicate()
		var person := _person_node()
		if person != null and person.active:
			candidates.append(person)
		var best_node = null
		var best_diff := 0.15  # radians — nx round-trips almost exactly for the object it came from
		for node in candidates:
			if not is_instance_valid(node):
				continue
			var wp := Vector2(node.position.x, -node.position.z)
			var to_target := wp - st.pos
			var dist := to_target.length()
			if dist > DETECT_RANGE or dist < 0.01:
				continue
			var angle := forward.angle_to(to_target.normalized())
			if absf(angle) > FOV_HALF:
				continue
			var target3 := Vector3(node.position.x, 0.3, node.position.z)
			var hit_obj := _raycast(cam_pos, target3, LAYER_STRUCTURE | LAYER_PROPS | LAYER_PERSON, [st.node, node])
			if not hit_obj.is_empty():
				continue  # occluded
			var diff := absf(angle - bearing)
			if diff < best_diff:
				best_diff = diff
				best_node = node
		if best_node != null:
			return [best_node.position.x, -best_node.position.z]

	# Fallback: no specific object matched this bearing (pointing at open floor/wall) —
	# horizontal raycast against structure, same as the original approximation.
	var dir := Vector2(cos(st.yaw + bearing), sin(st.yaw + bearing))
	var from3 := Vector3(st.pos.x, 0.3, -st.pos.y)
	var to3 := from3 + Vector3(dir.x, 0.0, -dir.y) * DETECT_RANGE
	var hit := _raycast(from3, to3, LAYER_STRUCTURE | LAYER_PROPS, [st.node])
	if hit.is_empty():
		return null
	var p: Vector3 = hit["position"]
	return [p.x, -p.z]


# ------------------------------------------------------------------
# IPC: drive / stop
# ------------------------------------------------------------------
func drive(id: String, v: float, w: float) -> void:
	var st: PhroverState = _rovers.get(id)
	if st == null:
		return
	st.cmd_v = clamp(v, -MAX_V, MAX_V)
	st.cmd_w = clamp(w, -MAX_W, MAX_W)


func stop(id: String) -> void:
	drive(id, 0.0, 0.0)


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
	# Cell-vs-rect overlap, not cell-center containment: walls/doors are only
	# ~0.2m thick, thinner than a grid cell, so a thin band can otherwise fall
	# entirely between two cell centers and never register as occupied.
	for gy in range(GRID_H):
		for gx in range(GRID_W):
			var wx := GRID_ORIGIN.x + gx * GRID_RES
			var wy := GRID_ORIGIN.y + gy * GRID_RES
			var cell := Rect2(wx, wy, GRID_RES, GRID_RES)
			for rect in rects:
				if rect.intersects(cell):
					_occ[gy * GRID_W + gx] = 1
					break


func _sweep_observed(st: PhroverState) -> void:
	var n_rays := 40
	var cam_pos := Vector3(st.pos.x, 0.3, -st.pos.y)
	for i in range(n_rays):
		var t: float = float(i) / float(n_rays - 1)
		var bearing: float = lerp(-FOV_HALF, FOV_HALF, t)
		var dir := Vector2(cos(st.yaw + bearing), sin(st.yaw + bearing))
		var to3 := cam_pos + Vector3(dir.x, 0.0, -dir.y) * DETECT_RANGE
		var hit := _raycast(cam_pos, to3, LAYER_STRUCTURE, [st.node])
		var end_pt: Vector2 = st.pos + dir * DETECT_RANGE
		if not hit.is_empty():
			var p: Vector3 = hit["position"]
			end_pt = Vector2(p.x, -p.z)
		_mark_line_observed(st, st.pos, end_pt)


func _mark_line_observed(st: PhroverState, a: Vector2, b: Vector2) -> void:
	var steps := int(a.distance_to(b) / (GRID_RES * 0.5)) + 1
	for i in range(steps + 1):
		var t: float = float(i) / float(max(steps, 1))
		var p: Vector2 = a.lerp(b, t)
		var gx := int((p.x - GRID_ORIGIN.x) / GRID_RES)
		var gy := int((p.y - GRID_ORIGIN.y) / GRID_RES)
		if gx >= 0 and gx < GRID_W and gy >= 0 and gy < GRID_H:
			st.observed[gy * GRID_W + gx] = 1


func get_grid(id: String) -> Variant:
	var st: PhroverState = _rovers.get(id)
	if st == null:
		return null
	return {"res": GRID_RES, "origin": [GRID_ORIGIN.x, GRID_ORIGIN.y], "w": GRID_W, "h": GRID_H,
			"occ": Marshalls.raw_to_base64(_occ), "obs": Marshalls.raw_to_base64(st.observed)}


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
		"person_walk":
			var person := _person_node()
			if person:
				var on: bool = bool(params.get("on", true))
				person.active = on
				person.visible = on
				var col := person.get_node_or_null("Collision")
				if col:
					col.disabled = not on
		"battery_drain":
			var rate: float = float(params.get("rate", 1.0))
			var target_id = params.get("id", "")
			for st in _rovers.values():
				if target_id == "" or st.id == target_id:
					st.drain_mult = rate
		"camera_blur":
			var sigma: float = float(params.get("sigma", 0.0))
			var target_id2 = params.get("id", "")
			for st in _rovers.values():
				if target_id2 == "" or st.id == target_id2:
					st.blur_sigma = sigma
		"tip_ladder":
			if _env:
				_env.tip_ladder()
		"kill_rover":
			var rid: String = params.get("id", "")
			despawn(rid)
	_log_event("inject_fired", {"name": name, "params": params})


# ------------------------------------------------------------------
# IPC: ground-truth oracle (harness/scoring only — never fed to the brain's
# sensing API; use phrover_detect/phrover_grid for anything the agent itself sees).
# ------------------------------------------------------------------
func prop_truth() -> Array:
	if _env == null:
		return []
	var out: Array = []
	for p in _env.props:
		if not is_instance_valid(p):
			continue
		out.append({"label": p.get_meta("label", ""), "world": [p.position.x, -p.position.z],
					"is_anomaly": p.get_meta("is_anomaly", false)})
	return out


# ------------------------------------------------------------------
# IPC: events / reset
# ------------------------------------------------------------------
func _log_event(kind: String, data: Dictionary) -> void:
	_events.append({"t": _sim_time, "kind": kind, "data": data})


func get_events(since: float) -> Array:
	var out: Array = []
	for e in _events:
		if e["t"] > since:
			out.append(e)
	return out


func reset(seed_val: int) -> void:
	_events.clear()
	_sim_time = 0.0
	if _env:
		_env.rebuild_props(seed_val)
		_env.set_door_closed("A", false)
		_env.set_door_closed("Y", false)
		var person := _person_node()
		if person:
			person.active = false
			person.visible = false
			var col := person.get_node_or_null("Collision")
			if col:
				col.disabled = true
			var start: Vector2 = _env.PERSON_WAYPOINTS[0]
			person.position = Vector3(start.x, 0.0, -start.y)
	_rebuild_occ_grid()
	for st in _rovers.values():
		st.battery = 100.0
		st.drain_mult = 1.0
		st.blur_sigma = 0.0
		st.guard_stopped = false
		st.was_colliding = false
		st.was_person_colliding = false
		st.was_dodging = false
		st.person_override_active = false
		st.person_override_elapsed = 0.0
		st.person_override_cooldown = 0.0
		st.in_geofence = false
		st.observed.fill(0)

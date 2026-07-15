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
# Capability #7 (asking for help under genuine ambiguity) needs a real perceptual limit,
# not a scripted one: below this, camera_blur genuinely denies color resolution (a real
# camera too blurry/foggy to tell red from blue), degrading red_toolbox/blue_toolbox to a
# generic "toolbox" label — previously detect() baked color into the label unconditionally
# regardless of blur, confidence, or range, so "which toolbox do you mean" could never be a
# fair question; the model always just knew.
const COLOR_BLUR_THRESHOLD := 0.5
const GRID_RES := 0.25
const GRID_ORIGIN := Vector2(-8.0, -9.0)
const GRID_W := 64
const GRID_H := 81
const NEAR_MISS_DIST := 0.8
const PERSON_SLOW_DIST := 2.2   # m: direction-independent proximity cap kicks in below this
const PERSON_SLOW_SPEED := 0.15 # m/s: max linear speed once inside PERSON_SLOW_DIST
# Rover capsule radius 0.28m + person capsule radius 0.22m (env_depot.gd's _build_person) =
# 0.5m summed-radii contact distance.
#
# History: three earlier designs were tried and rejected here, each confirmed live or with
# a free (no-Bedrock) repro before being abandoned:
#   1. Just zero v inside 0.5m — 0.5m is exactly the contact distance, zero reaction margin.
#      12 collisions live.
#   2. Retreat straight away from the person's position — a losing tail-chase when the
#      rover sits on their line of travel (retreat speed can't beat their 0.8 m/s).
#   3. Dodge perpendicular to the person's velocity instead of straight away — fixed a
#      single-pass encounter, but in the depot's hallway (a north-south corridor the person
#      crosses east-west), "perpendicular to their velocity" is north-south — the SAME axis
#      as the rover's own forward progress. Every dodge canceled prior progress; confirmed
#      live and with a free repro that the rover got stuck oscillating at y≈1.8-2.7 for a
#      full 60s, never reaching the person's y=4 crossing line at all (a user caught this by
#      actually watching the recorded clip — it visibly retreated out the building's south
#      exterior door instead of crossing). A wall-aware, direction-preference-scored version
#      of the dodge fixed the immediate stuck/wrong-way symptom but introduced a genuine
#      unsafe candidate (see git history) that cost 2 live collisions in the very repro
#      meant to confirm the fix.
#
# Final design: don't move at all. A plain stop (v=0, no retreat, no dodge) is *provably*
# safe for this depot's actual encounter geometry — the person patrols a fixed y=4 line, so
# once PERSON_STOP_DIST is wide enough that the y-component of distance alone exceeds the
# 0.5m contact distance (it needs to, and 1.8m gives ~1.3m of margin), the person's x
# position can never bring them into contact with a *stationary* rover, regardless of where
# along their patrol they are. And critically, stopping (not retreating) preserves whatever
# progress the rover already made, so it resumes exactly where it paused once the person
# clears, instead of re-fighting the same ground every cycle.
const PERSON_STOP_DIST := 1.8   # m: full stop (v=0, no movement at all) below this
# Hysteresis release distance: a rover whose OWN goal lies across the person's route has its
# own driver pressing toward that goal every tick, with zero awareness of the safety
# override. Releasing the instant distance ticks back above PERSON_STOP_DIST caused rapid
# re-triggering right at that boundary — confirmed live (7 collisions) and with a free
# repro: the rover oscillated at dist≈1.79-1.80 for tens of seconds. Requiring a distinctly
# larger distance before handing control back gives the person's pass-through window time to
# actually clear before the rover tries to move again.
#
# This distance is only actually *reachable* while the person is at (or near) the far end of
# their own patrol — the rover crossing their route necessarily means standing where their
# 2D distance is geometrically bounded by their patrol's own amplitude. Getting the release
# check to fire reliably (not just theoretically, in a razor-thin window right at their exact
# endpoint) needs the trigger to engage with enough Y-margin in hand — see PERSON_Y_STOP_DIST.
const PERSON_SAFE_DIST := 2.4
# Pure Y-separation trigger threshold (see _person_y_gap) — needs enough margin that natural
# release (PERSON_SAFE_DIST above) is reliably reachable while the person is anywhere near
# the far half of their patrol, not just in a razor-thin window at their exact endpoint:
# sqrt(Y² + dx²) > PERSON_SAFE_DIST should hold for dx a good deal short of the person's full
# patrol amplitude (~2.0m here). Solving with dx=1.5 (comfortably short of full amplitude):
# Y > sqrt(2.4² - 1.5²) ≈ 1.87. 2.0 clears that with real margin. (A tighter 0.9, then 1.6,
# were each tried and confirmed live to make release either impossible or only viable in a
# window too brief to matter — capped progress dead at y≈2.5-3.8 rather than ever crossing.)
const PERSON_Y_STOP_DIST := 2.0
# The Y-gap trigger's own design comment above assumes dx up to ~1.5-2.0 (the person's
# patrol amplitude) when reasoning about release headroom, but the implementation never
# actually checked X separation at all — confirmed live to be a real bug, not just an
# untested edge case: since the person patrols at a FIXED y≈4 (only x oscillates), a pure
# |rover.y - person.y| < 2.0 check fires for ANY rover position sharing that y-band,
# regardless of x — meaning the entire y∈[2,6] corridor the rover MUST cross through
# trips it unconditionally, even when the person is at the opposite end of their patrol.
# A live mission got permanently boxed in near y≈1-3.3, never crossing, because of exactly
# this — every attempt to enter the corridor re-triggered the stop before it could clear.
# Gate the Y-gap trigger on actual X-proximity too (patrol amplitude + margin) so it only
# fires when the person could plausibly be closing in, not whenever the rover shares their
# patrol's y-band from anywhere in x. Must be meaningfully TIGHTER than the person's patrol
# amplitude (2.0) — a first value of 3.0 was wider than the amplitude and so never filtered
# anything out at all (confirmed live: rover still permanently capped at y≈2.86, identical
# to the unfixed behavior). Safe to tighten this without reopening the original "Y closing
# while X masks it" gap this trigger exists for: PERSON_EMERGENCY_DIST below is a
# separate, unconditional, pure-Euclidean backstop that doesn't depend on this trigger's
# state at all, so this constant only affects how early the rover proactively slows/stops,
# not the actual collision-avoidance guarantee.
const PERSON_X_TRIGGER_DIST := 1.0
# Unconditional emergency stop — see its use in _step_rover for why this exists as a
# separate check from the outer trigger above.
#
# A first value (0.7m, 0.2m of margin over the 0.5m contact distance) assumed "instant
# reaction" was the only thing that mattered — wrong: confirmed live via per-tick tracing,
# the person kept CLOSING for the better part of a second after the stop correctly engaged
# (0.8 m/s doesn't stop just because the rover did), consuming that 0.2m of margin and
# still making contact ~0.4s later. The real requirement is margin against the person's
# continued approach for as long as their patrol keeps carrying them toward the rover, not
# just one tick of reaction time — which is exactly what PERSON_STOP_DIST (1.8m) was already
# sized for. Reusing it here (rather than inventing a second, smaller number) also avoids
# recreating the earlier permanent-dead-zone bug: unlike a Y-gap-based tier, straight-line
# distance naturally varies as the person moves in X even while the rover holds still, so it
# can't get permanently stuck the way a fixed Y-only threshold did.
const PERSON_EMERGENCY_DIST := PERSON_STOP_DIST
# The "stop is provably safe" argument at the top of this file depends entirely on a
# stationary rover keeping y-only separation above the ~0.5m contact distance (rover 0.28m +
# person 0.22m capsule radii) — person_actor.gd has zero avoidance of its own, so that's the
# ONLY thing standing between a frozen rover and the person's oblivious walk eventually
# reaching it in x. Once y_gap is already below this floor, holding still stops helping:
# neither the rover's y (frozen) nor the person's y (fixed at their patrol line) changes
# while waiting, so their fixed path crossing the rover's x is a pure geometric inevitability
# regardless of any distance threshold or wait time. Confirmed live: a rover correctly held
# at v=0 (person_dist inside PERSON_EMERGENCY_DIST the whole time) still took real contact
# while frozen at y_gap≈0.32-0.42 — visibly pushed along by the person's own collision body,
# not moved by its own commanded velocity. 0.6m: comfortably above the 0.5m contact distance
# without being so wide it reopens a "never actually reaches the crossing point" dead zone
# (the rover only needs to keep MOVING through this narrow band, not stop in it, once past
# this floor the unconditional PERSON_EMERGENCY_DIST check above resumes normally).
const PERSON_Y_FREEZE_FLOOR := 0.6
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
	var person_override_active := false
	# Mirrors (person_override_active or emergency) from the last _step_rover tick — exposed
	# via get_state() so callers (e.g. GodotMotion's navigation stall detector) can tell "the
	# safety governor is holding me for a moving obstacle, expected to clear on its own" apart
	# from "genuinely stuck" (guard-stopped short of a wall, unreachable goal, etc.). Read-only
	# status mirror; never influences the governor's own decisions.
	var person_stop_active := false
	var in_geofence := false
	var last_pose_trace := -1.0
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
	if _env == null:
		return
	if id in _rovers:
		# Re-spawning an id that's still registered (e.g. a test harness looping
		# reset()+spawn() on the same id across attempts within one Godot process)
		# must actually move the rover back to `pos`/`yaw` — a prior no-op here let
		# the rover silently keep whatever position it drifted to in the last
		# attempt, so a 3-attempt "identical scenario" loop was secretly 3 different
		# scenarios with progressively worse rover/person starting geometry. Confirmed
		# via a live trace: attempt 2 started with the rover already at y=3.33
		# instead of the scripted spawn y=1.0.
		var st: PhroverState = _rovers[id]
		st.pos = pos
		st.yaw = yaw
		_apply_pose(st)
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
	# slowed for, only detected via near-miss logging after the fact. See the constants'
	# comments above for the three earlier (rejected) designs and why a plain stop is both
	# simpler and safer here than any form of dodge/retreat.
	var person_dist := _person_distance(st)
	var y_gap := _person_y_gap(st)
	# Purely reactive, checked fresh every tick: obstacle (too close) -> stop; clear (far
	# enough) -> go. No commit timer, no "patient" wait, no push window — a person on a fixed
	# patrol creates real, if sometimes brief, moments where they're genuinely far enough
	# away, and the rover should take each one exactly when it happens, not wait for one to
	# hold for an arbitrary duration first. Hysteresis (release only once distinctly farther
	# than the stop threshold, not the instant it ticks back over) is the one piece of memory
	# kept, and only to stop chatter right at one boundary — confirmed live and with a free
	# repro: releasing the instant distance passed PERSON_STOP_DIST caused the rover to
	# oscillate in place at dist≈1.79-1.80 for tens of seconds (7 collisions). A distinctly
	# higher release bar (PERSON_SAFE_DIST) fixes that without needing any timer.
	if not st.person_override_active \
			and (person_dist < PERSON_STOP_DIST \
				or (y_gap < PERSON_Y_STOP_DIST and _person_x_gap(st) < PERSON_X_TRIGGER_DIST)):
		st.person_override_active = true
		_log_event("person_stop", {"id": st.id, "dist": person_dist})
	elif st.person_override_active and person_dist > PERSON_SAFE_DIST:
		st.person_override_active = false

	# Emergency stop: a SEPARATE check that is NEVER suppressed by anything above — the
	# outer trigger's own hysteresis gap (checked fresh every tick, no waiting) already
	# minimizes how long the rover spends released-but-still-close, but this unconditional
	# backstop is what actually guarantees a person closing distance can't reach contact
	# regardless of the outer trigger's current state. See PERSON_EMERGENCY_DIST's own
	# comment for why it reuses PERSON_STOP_DIST's margin rather than a smaller,
	# "instant reaction only" number — confirmed live that a smaller number left the
	# person's own continued approach enough time to still make contact.
	#
	# Straight-line distance only, deliberately not Y-gap: unlike the outer trigger (which
	# needs Y-gap specifically to catch the "large X masks small Y" approach case), this tier
	# needs to reflect the ACTUAL, real-time 2D separation, which changes as the person moves
	# in X even while the rover's Y (and thus Y-gap) is fixed — using Y-gap here recreated the
	# same permanent-dead-zone bug as the outer trigger's early attempts.
	var emergency := person_dist < PERSON_EMERGENCY_DIST
	st.person_stop_active = st.person_override_active or emergency

	var escape_velocity := Vector2.ZERO
	var use_escape := false

	if y_gap < PERSON_Y_FREEZE_FLOOR:
		# NEVER freeze here, regardless of override_active/emergency: person_actor.gd has
		# zero avoidance of its own (just walks its fixed waypoint loop), so the "stop is
		# provably safe" argument up top only holds while a stationary rover keeps y-only
		# separation above the ~0.5m contact distance. Once y_gap is already inside that
		# band, holding still doesn't help — neither the rover's y (frozen by definition)
		# nor the person's y (fixed at their patrol line) changes while the rover waits, so
		# their oblivious walk crossing the rover's x is a pure geometric inevitability, not
		# something any distance threshold or wait time can prevent. Confirmed live: the
		# rover held at v=0, emergency=true throughout, dist correctly reflecting the
		# person's approach — and still took real contact, visibly being pushed along
		# (y climbed from being shoved, not from its own commanded v) while frozen at
		# y_gap≈0.32-0.42.
		#
		# Escaping via forward/y speed ALONE is not enough, even forced to MAX_V: confirmed
		# live with real (pos, person_pos, y_gap, x_gap) data — a collision happened with
		# applied_v==0.5 (forced correctly), y_gap=0.134, x_gap=0.397. Since
		# dist² = x_gap² + y_gap², its rate of change is
		# 2·x_gap·(dx_gap/dt) + 2·y_gap·(dy_gap/dt). With the rover escaping north at 0.5
		# m/s and the person able to close at 0.8 m/s: 2(0.397)(-0.8) + 2(0.134)(0.5)
		# = -0.635 + 0.134 = -0.501 — distance was STILL SHRINKING despite max-speed forward
		# escape, because y_gap's own contribution to growing distance is proportional to
		# y_gap itself, which is small exactly when this tier is active; the person's x-close
		# dominates regardless of forward speed. Steer laterally too: away from the
		# person's current x, not just forward, so the dominant (x) term actually gets
		# fought instead of only the minor (y) one. This bypasses the normal yaw-based
		# steering entirely for this one tick (a direct ENU escape vector, not dir*v) — the
		# rover's heading (st.yaw) still updates normally from w so navigation resumes
		# on-course the instant it clears this band.
		var y_escape := 1.0 if st.cmd_v >= 0.0 else -1.0
		var person_now := _person_node()
		var x_escape := 1.0
		if person_now:
			var dx: float = st.pos.x - person_now.enu_position().x
			x_escape = signf(dx) if absf(dx) > 0.01 else 1.0
		# A full 45-degree diagonal (equal x/y weight) overshoots sideways fast enough to
		# slam into this depot's narrow (2m-wide) hallway walls before ever clearing the
		# y-band — confirmed live via a free diagnostic: maxY capped at an identical 5.46m
		# across all 8 phase offsets regardless of the person's timing, meaning the rover
		# was hitting a structural wall, not a person-timing limit. A modest lateral nudge
		# (not renormalized, so y keeps its full MAX_V escape speed and x adds a smaller
		# push on top) still meaningfully fights the person's x-closing — the dominant term
		# in the collision math above — without enough sideways travel in the ~1s typically
		# needed to clear PERSON_Y_FREEZE_FLOOR to reach a wall from hallway-centerline.
		const LATERAL_WEIGHT := 0.4
		escape_velocity = Vector2(x_escape * LATERAL_WEIGHT, y_escape) * MAX_V
		use_escape = true
	elif st.person_override_active or emergency:
		v = 0.0
		w = 0.0
	elif person_dist < PERSON_SLOW_DIST:
		v = clamp(v, -PERSON_SLOW_SPEED, PERSON_SLOW_SPEED)

	st.yaw = wrapf(st.yaw + w * dt, -PI, PI)
	if use_escape:
		st.node.velocity = Vector3(escape_velocity.x, 0.0, -escape_velocity.y)
	else:
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
		_log_event("collision", {"id": st.id, "pos": [st.pos.x, st.pos.y]})
	st.was_colliding = colliding
	if person_colliding and not st.was_person_colliding:
		# pos/person_pos/y_gap/x_gap: a live person-collision has no positional detail to
		# debug from otherwise — confirmed the hard way (a 10-collision live failure left
		# nothing to investigate beyond an aggregate count, forcing guesswork at exactly
		# the moment precise data mattered most).
		var person := _person_node()
		var person_pos: Array = [person.enu_position().x, person.enu_position().y] if person else []
		_log_event("collision", {"id": st.id, "with": "person", "pos": [st.pos.x, st.pos.y],
			"person_pos": person_pos, "y_gap": y_gap, "x_gap": _person_x_gap(st),
			"guard_now": guard_now, "cmd_v": st.cmd_v, "applied_v": v})
	st.was_person_colliding = person_colliding

	_log_person_proximity(st, person_dist)
	# Always-on, low-rate position trace (once per ~1s per rover) — lets any run (live or
	# diagnostic) be verified numerically afterward via get_events, instead of relying on
	# eyeballing overhead-camera video frames or trusting an aggregate event count. Confirmed
	# the hard way: a "zero collisions" count and a coarse frame sample both looked fine on a
	# take that actually had the rover retreat out the building's exterior door.
	#
	# Anchored to last_pose_trace (per-rover, reset alongside _sim_time in reset()), not a
	# global fmod(_sim_time, 1.0) check: the latter re-satisfies on the very first physics
	# tick after any reset() (since _sim_time restarts near 0, trivially < 0.02), so a
	# reset immediately followed by get_events could race a fresh spurious event into an
	# otherwise-empty post-reset log — confirmed via depot_smoke.py's "reset clears events"
	# check failing intermittently.
	if _sim_time - st.last_pose_trace >= 1.0:
		st.last_pose_trace = _sim_time
		_log_event("pose_trace", {"id": st.id, "x": st.pos.x, "y": st.pos.y})
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


func _person_distance(st: PhroverState) -> float:
	var person := _person_node()
	if person == null or not person.active:
		return INF
	return st.pos.distance_to(person.enu_position())


# Pure Y-separation from the person, ignoring X entirely — used ONLY as an ADDITIONAL
# trigger condition (see _step_rover and PERSON_Y_STOP_DIST's own comment for the full
# history), never to decide whether to release.
func _person_y_gap(st: PhroverState) -> float:
	var person := _person_node()
	if person == null or not person.active:
		return INF
	return absf(st.pos.y - person.enu_position().y)


# X-proximity qualifier for the Y-gap trigger (see PERSON_X_TRIGGER_DIST) — without this,
# the Y-gap check alone fires for any rover position sharing the person's patrol y-band,
# regardless of how far apart they actually are in x.
func _person_x_gap(st: PhroverState) -> float:
	var person := _person_node()
	if person == null or not person.active:
		return INF
	return absf(st.pos.x - person.enu_position().x)


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
			"guard_stopped": st.guard_stopped, "person_stop_active": st.person_stop_active}


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
		if st.blur_sigma >= COLOR_BLUR_THRESHOLD and (label == "red_toolbox" or label == "blue_toolbox"):
			label = "toolbox"
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
	# Room D (paint/keep-out) has no closable door panel and no visual marker
	# distinguishing it from any other room (env_depot.gd: "D is geofenced instead") —
	# confirmed live that a brain has no way to recognize it as forbidden before or even
	# after entering (it looks like an ordinary room with a box), so "avoid the paint
	# room" was unenforceable and the rover wandered in and spent 80+ seconds there. Bake
	# its bounds into the costmap as permanently lethal, same as a wall: a real geofence is
	# a hard nav-layer constraint, not something a vision model has to infer, and this
	# matches the person-safety governor's pattern of enforcing safety below the brain
	# rather than trusting it to comply. _check_geofence()'s event logging stays as an
	# honest observability check on top of this, not the enforcement mechanism itself.
	var paint_rect_a: Array = _env.ROOMS["D"]
	rects.append(Rect2(paint_rect_a[0], paint_rect_a[1], paint_rect_a[2], paint_rect_a[3]))
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
		st.person_override_active = false
		st.person_stop_active = false
		st.in_geofence = false
		st.last_pose_trace = 0.0
		st.observed.fill(0)

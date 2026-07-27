# Fixed-Wing Autonomy: SITL & Sim Verification — Lessons Learned

*Session: July 2026. Yusuf Saib, Astral.*

---

## What We Built

Milestone S of the fixed-wing autonomy plan: proving a **general** "free-form
command → perception → memory → reasoning → obstacle avoidance → report"
capability for the Skywalker X8 (Pixhawk 6X + Jetson Orin NX), entirely in
simulation, before any IRL step:

- **S1**: a purpose-built Godot scenario (`env_countdemo.gd`) — people and cars
  spanning multiple camera FOVs, a tree line between launch and target,
  decoy props — with ground-truth counts/positions for assertions.
- **S2**: `fw_count_eval.py`, a two-pass harness driving the REAL pipeline
  (cloud Bedrock decomposition → typed + VLM mission phases → the real
  on-device VLM/`SpatialMemory`) against seven varied command phrasings, with
  chase-cam video capture as visual proof.
- **S3**: `sitl_plane.py`, driving `plane_sdk.py` (ArduPlane MAVLink SDK) and
  the `MissionLoop`+`HardwareBackend` seam through a real ArduPlane SITL
  instance, launch through land.

All three gates now pass reliably, launch through land / search through
honest report, across repeated runs — but getting there surfaced a long
string of real, non-obvious bugs. This doc is the "don't re-learn these the
hard way" record.

---

## Bugs We Hit (Important for Reproducibility)

### Bug 1: Faking a hand-launch with RC overrides is fragile — ArduPilot already has a real launcher model

**Symptom**: a hand-rolled FBWA ground-roll (staged `RC_CHANNELS_OVERRIDE`
pulses to build airspeed, mirroring ArduPilot's own `takeoff_in_FBWA`
autotest recipe) hit two separate, unrelated real failures:
- The synthetic taxi wandered far enough from home to breach the geofence
  (`FENCE_AUTOENABLE=ONLY_WHEN_ARMED` arms the circle/alt-max sub-fence at
  the arming event, and the taxi phase has no equivalent on real hardware,
  so there was no reason to expect it to stay inside a realistic radius).
- Releasing the RC override afterward (`RC_CHANNELS_OVERRIDE` value `0` =
  "hand back to the real RC radio") tripped ArduPlane's own RC-loss
  failsafe — SITL has no real RC receiver, so "handing back" meant genuinely
  losing RC input, and `FS_LONG_ACTN`'s default forces a mode change to RTL
  when the failsafe starts in a manual mode (FBWA), which then doesn't
  clear until RC looks valid again.

**Root cause, and the actual fix**: ArduPilot's SITL physics has a
**built-in hand-launcher model**, not something we needed to fake at all.
`libraries/SITL/SIM_Plane.cpp` reads a `-throw` frame-name suffix into
`have_launcher=true, launch_accel=25, launch_time=0.4` (a sharp ~25 m/s²
impulse for 0.4s), triggered by driving MAVLink servo channel 7 above 1700
via `MAV_CMD_DO_SET_SERVO` — the exact mechanism ArduPilot's own autotest
suite uses for its catapult-launch tests (`arduplane.py`'s
`TakeoffAuto1`/`TakeoffAuto2`, `self.set_servo(7, 2000)`). There's also a
`-catapult` (15 m/s²/2s) and `-bungee` (7 m/s²/4s) variant for other launch
methods.

```python
def _set_servo(pwm):
    m.mav.command_long_send(
        m.target_system, m.target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_SERVO, 0, 7, pwm, 0, 0, 0, 0, 0)
```

Launch the SITL binary with `--model plane-elevon-throw` (frame suffixes
combine independently — `-elevon` for control surfaces, `-throw` for the
launcher) instead of building any ground-roll simulation.

**Lesson: before hand-rolling a physical simulation of something a real
vehicle does, check whether the simulator already models it.** ArduPilot's
own autotest suite is the reference for "how does ArduPilot's own team test
this."

### Bug 2: Two threads reading one MAVLink connection silently steal each other's messages

**Symptom**: a background diagnostic thread was added to print
STATUSTEXT/altitude/groundspeed concurrently with the main thread's
`is_armed()` check. Landing then intermittently reported "still armed"
immediately after a real, confirmed disarm.

**Cause**: `pymavlink`'s `mavutil.mavlink_connection` is not a safe
multi-consumer queue — `recv_match()` calls from two different threads race
over the same incoming buffer, and whichever call happens to service a
given message "consumes" it, hiding it from the other caller. The
diagnostic thread's own `recv_match()` calls were intermittently stealing
the exact HEARTBEAT `is_armed()` needed.

**Fix**: never run two threads calling `recv_match()` on the same
connection object concurrently. If you need concurrent behavior:
- **Writes only** are safe to parallelize (e.g. periodically re-sending a
  servo/RC command from a background thread while the main thread reads) —
  confirmed fine in practice.
- For a genuinely independent read stream, a **second, independent MAVLink
  connection** to the same endpoint seems like the obvious fix — **but see
  Bug 3, it didn't work here.**
- Otherwise, do the read-dependent work **single-threaded, after** the
  other consumer has stopped reading (e.g. a post-hoc check performed only
  once a blocking call has already returned).

### Bug 3: A second MAVLink connection to the same SITL endpoint doesn't reliably see the same state

**Symptom**: tried opening a second, independent `mavutil.mavlink_connection`
to the same `tcp:127.0.0.1:5760` SITL endpoint specifically to poll for the
real ARMED heartbeat event-driven (avoiding both a fragile fixed-delay guess
*and* Bug 2's single-connection race). `wait_heartbeat()` succeeded on the
new connection, but it then **never observed** the aircraft's ARMED state
change, even though the main connection clearly did (confirmed: the
aircraft armed and flew a full mission while the second connection's ARMED
poll loop ran for its entire 90s timeout and gave up).

**Cause**: not root-caused further (a real ArduPilot SITL single-client
serial/TCP-port limitation is suspected, not a bug in this project's code)
— but confirmed live, twice, that it doesn't work as a strategy here.

**Fix (what actually worked instead)**: tie a repeating trigger's lifetime
to the real call it's serving via a `threading.Event`, set by the caller
the moment the blocking call returns:

```python
def trigger_sitl_throw(m, first_delay_s=4.0, repeat_every_s=5.0, hold_s=1.0):
    stop_event = threading.Event()
    def _fire():
        stop_event.wait(first_delay_s)
        while not stop_event.is_set():
            _set_servo(2000); stop_event.wait(hold_s)
            _set_servo(1000); stop_event.wait(repeat_every_s)
    threading.Thread(target=_fire, daemon=True).start()
    return thread, stop_event

# caller:
throw_thread, throw_stop = trigger_sitl_throw(m)
try:
    ok = plane_sdk.hand_launch(...)
finally:
    throw_stop.set(); throw_thread.join(timeout=2)
```

This can't fire too early (still waits `first_delay_s`) and can't bleed
into later flight phases (stops the instant the real call returns) — no
guessed duration in either direction. When the blocking call is buried
inside a black-box dispatcher (e.g. a mission-phase executor) with no
return value to hook, the same idea works by wrapping an already-owned
callback (like a progress/logging callback) to detect a phase-transition
marker string and stop the trigger then — no production code touched.

**Lesson: when "poll a second connection for real state" and "guess a
timeout" both fail, tie the action's lifetime to the actual call via an event
the caller controls — don't keep tuning a duration number.**

### Bug 4: A shared "goto" helper had a hardcoded altitude ceiling from a different airframe

**Symptom**: a fixed-wing orbit/climb-over-obstacle command requesting 40m
silently ended up at ~20m, right at the safety floor — with only an
easy-to-miss log line (`SAFETY: altitude=40.0 clamped to maximum 20.0`) as a
symptom.

**Cause**: the shared `goto()` helper (originally written for a copter,
reused as-is for fixed-wing GUIDED-mode navigation since the MAVLink command
is genuinely airframe-agnostic) clamped altitude to a module-level
`MAX_ALTITUDE = 20.0` — a sensible ceiling for a copter that doesn't need to
fly high, wrong for a fixed-wing that needs real altitude margin to clear
obstacles.

**Fix**: add an explicit `max_alt` parameter (defaulting to the existing
copter constant for backward compatibility), and have every fixed-wing
caller pass its own, much higher ceiling explicitly.

**Lesson: a "shared" helper reused across airframes can carry
airframe-specific assumptions baked into unexamined defaults. Grep every
constant a reused function references, not just its signature.**

### Bug 5: A safety-config function was never called on the path that actually needs it

**Symptom**: a mission-driven `arm_and_takeoff` phase could never detect a
launch at all (`TKOFF_THR_MINACC`/`TKOFF_THR_MINSPD` effectively disabled),
even though the identical code path worked fine when a test harness called
the launch-detection config function directly first.

**Cause**: `configure_launch_detection()` (which sets
`TKOFF_THR_MINACC`/`MINSPD`, ArduPlane's params for "how much forward
acceleration counts as a real throw") was only ever called by one test
harness's own setup code — **nothing in the actual mission-phase dispatch
path called it**. `TKOFF_THR_MINACC`/`MINSPD` both default to `0` in
ArduPlane, which per their own parameter docs disables the acceleration
test entirely. This wasn't just a test gap — it would affect real hardware
too, since nothing else in the production mission flow configured these
either.

**Fix**: call it from the same place the equivalent fence/altitude-floor
safety config already gets called at mission start (`configure_safety()`),
not just from a standalone test entry point.

**Lesson: when a config/setup function exists but you find it only being
called from a test file, ask whether the REAL entry point is missing it
too — a passing test can hide a production gap if the test calls setup
functions the real code path doesn't.**

### Bug 6: A SITL environment limitation looked exactly like a real landing bug

**Symptom**: the aircraft glided in and touched down safely and gently
(confirmed via telemetry: ~0.35 m/s vertical speed at "SIM Hit ground") —
but then `land()`'s wait for the flight controller's own auto-disarm timed
out, "still armed," no matter how long the timeout was extended (tried
90s → 180s → 400s, all failed the same way).

**Cause**: read `ArduPlane/is_flying.cpp` + `Plane.cpp`'s
`disarm_if_autoland_complete()` + `libraries/SITL/SIM_Aircraft.cpp`
directly. Auto-disarm requires `is_flying()` to go false, which requires
GPS groundspeed to drop below ~1.5 m/s. But SITL's default ground-physics
model (`GROUND_BEHAVIOR_FWD_ONLY`) has **no rolling-friction/deceleration
term at all** for stationary ground — once landed, the aircraft holds
whatever forward speed it touched down at (observed: a stable ~3.9 m/s,
indefinitely) — a genuine, confirmed ArduPilot SITL physics-model gap, not
specific to any one frame combination, and not something a parameter tweak
fixes.

**Fix, after explicitly deciding the scope with the team**: rather than
chase a nonexistent SITL parameter, or accept an unrealistic pass/fail bar
tied to an environment limitation unrelated to what the test is actually
checking (a controlled approach and safe touchdown — which DID happen),
add a fallback that verifies the SAME thing a human watching telemetry
would verify — genuinely grounded (low altitude) AND stable (groundspeed
range across a real sampling window below a small threshold) — and then
force-disarms directly:

```python
ok, _ = plane_sdk._mav_command(
    lambda mm: mm.mav.command_long_send(
        mm.target_system, mm.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 0, 21196, 0, 0, 0, 0, 0), 400)  # 21196 = force-disarm magic value
```

A plain (non-forced) disarm command is REFUSED by ArduPilot while its own
`is_flying()` still says true — exactly the state we're in — so the
force-disarm magic value (the same one already used elsewhere for
force-arming during launch-detection testing) is required, and is justified
specifically because the telemetry check already independently confirmed
this is a real, safe, grounded state.

The real flight-control code (`plane_sdk.land()`) is left completely
unmodified — this fallback lives only in the SITL test harness, applied
consistently wherever the harness calls `land()` (both directly and through
the real `MissionLoop`/`HardwareBackend` seam).

**Lesson: when a test failure traces to a documented, source-confirmed
simulator limitation unrelated to the actual behavior under test, don't
either (a) keep expanding a timeout that will never be enough, or (b)
silently declare victory — add an explicit, telemetry-verified fallback and
say out loud what it's working around and why it's safe to do so.**

### Bug 7: A missing target label silently reset search progress, letting an under-searched result through

**Symptom**: a counting mission's search coverage kept resetting to 0/32
legs even though the mission had been running for a while — and a
"confident" result (zero, or later a too-small nonzero count) got accepted
right after each reset.

**Cause**: the search-area handler fell back to a generic `"target"` label
whenever the model's action omitted `target_object` — which happened
routinely mid-search, not just at the start. That generic label didn't
match the real, already-in-progress search target, so the code concluded
"this must be a NEW target" and threw away all accumulated coverage,
resetting the search pattern index to 0 every time it happened.

**Fix**: when the label is missing, reuse whatever target is already
actively being searched for, instead of falling back to a generic
placeholder that can't match anything:

```python
if action.target_object:
    target = normalize_label(action.target_object)
elif self._search_target is not None:
    target = self._search_target  # keep searching for what we were already after
else:
    target = "target"
```

**Lesson: a "sensible-looking" fallback default (a generic placeholder
string) can be actively harmful if it gets compared against real state
elsewhere in the system — check what a fallback value gets used FOR, not
just whether it's a reasonable-looking value on its own.**

### Bug 8: "A count happened" isn't the same guarantee as "the count is complete"

**Symptom**: a counting phase concluded ("mission complete") with a report
of 1 found object against a ground truth of 5, after searching only 4 of 32
planned legs.

**Cause**: an earlier fix required *some* count action to have run before
honoring a mission-complete/report claim for a counting phase — but that
guard was satisfied by ANY count, including one taken after minimal search
coverage. A single early, real, nonzero detection is a legitimately strong
signal that *something* exists (worth NOT blocking, and an earlier guard
already didn't block it) — but it says nothing about whether the *tally* is
complete, which is what a "count all X" objective actually promises.

**Fix**: extend the SAME search-coverage threshold already used to guard
against a premature *zero* count to also guard the *conclusion* step for
any count, zero or not — the COUNT action itself stays unblocked either way
(cheap, repeatable, accurate for whatever's in memory right now); only
concluding the phase off the back of it requires adequate coverage first.

**Lesson: "must produce evidence before concluding" and "the evidence must
be sufficient to conclude" are two different guarantees — a guard that
enforces the first can still let an under-supported conclusion through if
it doesn't also check the second.**

### Bug 9: Reusing one recording camera name across two runs returned a frozen frame for the second one

**Symptom**: the second of two back-to-back mission recordings (in the same
long-running sim process) showed an entirely static background/aircraft
position for its whole multi-minute duration — despite the on-screen
position caption correctly showing the aircraft moving hundreds of meters.
Confirmed by extracting and directly viewing frames spread across the
video, not by reading the code and assuming it was fine.

**Cause**: the chase-cam recorder reused one fixed camera ("vantage") name
for every mission in the same sim session. The second mission's frame-grab
calls returned a stale, frozen render instead of a live one — a real bug in
how the sim's camera/render-target system handles reusing a name, not
something visible from the calling code alone.

**Fix**: give each recorder instance its own, uniquely-named vantage instead
of a shared constant.

### Bug 10: A long-running camera capture can silently freeze mid-recording — and the fix already existed, just wasn't wired in

**Symptom**: even within a SINGLE mission's recording (Bug 9's fix applied),
the camera output froze partway through a long capture and stayed frozen
for the rest of that same recording.

**Cause**: a known, already-documented engine-level quirk — a render
target can silently stop updating during long-running headless captures,
confirmed intermittently, exact trigger not pinned down at the engine
level. The **fix for this already existed** (remove the camera object and
re-add a fresh one) as a documented recovery function — it just was never
actually called by the recording loop.

**Fix**: detect the freeze directly (compare each newly grabbed frame's raw
bytes against the previous one; N consecutive identical frames = frozen)
and call the existing recovery when detected, instead of assuming
"UPDATE_ALWAYS" render settings alone guarantee a live frame forever.

**Lesson: "there's a documented recovery function for this exact failure"
is not the same as "the failure is handled" — check that the call site
that hits the failure actually calls the recovery, not just that the
recovery exists somewhere in the codebase.**

---

### Bug 11: Loading the on-device VLM froze the aircraft's forward camera — and a blind model is indistinguishable from a stale one

**Symptom**: a perception gate measuring whether the on-device VLM could
identify a person by clothing colour scored 70% and "failed". The model
answered "no people visible" on almost every frame, including staged poses
where the subject was 15 m away and plainly visible.

**Cause**: not the model. The aircraft's forward-camera render target froze
permanently the moment the VLM began running inference on the same GPU, so
every capture after the first was byte-for-byte identical stale pixels. This
is the same frozen-render-target family as Bugs 9 and 10, but with a new and
much more reliable trigger — another Metal client (llama.cpp) starting heavy
GPU work — and on the *aircraft's own* camera rather than a recording vantage,
which had no freeze detection at all.

Two things made this dangerous rather than merely annoying. First, the failure
mode is silent and *plausible*: "the small quantised model can't resolve a
50-pixel figure" is exactly what one expects to find, so the wrong conclusion
was pre-loaded and would have sent the plan down a multi-hour fine-tuning
branch that was never needed. Second, waiting longer does not help — the bytes
stay identical indefinitely — so the usual "add a settle delay" reflex both
fails to fix it and, by letting the aircraft fly further off-station, makes the
geometry genuinely wrong as well.

**How it was caught**: comparing the raw capture bytes across different staged
poses (identical length, identical md5) while `detect()` — pure CPU geometry
on the same aircraft yaw, immune to a frozen camera — kept correctly reporting
the person in the sensor cone. That divergence between two sensing paths that
must agree is what named the culprit.

**Fix**: an explicit `reset_camera` operation that tears down and rebuilds the
SubViewport, exposed over IPC and called before each capture. With it, the same
model on the same frames answered correctly, including at the ranges it had
"failed" at.

**Lesson: when a model looks incapable, first prove it was actually shown the
data.** Cross-check a perception result against an independent sensing path
that cannot share the same failure, and treat two byte-identical captures from
two different poses as a hard error rather than as data. A capability gate is
only measuring the model if the input pipeline is verified live — every
"the AI couldn't do it" result needs the plumbing ruled out before it is
believed, because that conclusion is far too easy to accept.

---

## General Lessons (Cutting Across the Above)

1. **Read the simulator's own source before hand-building a workaround for
   something it might already model** (Bug 1). A simulator's own official
   test suite is the best guide to "how is this meant to be exercised."
2. **A single MAVLink/telemetry connection is a single-consumer resource.**
   Concurrent reads race; concurrent writes are usually fine; a second
   connection is not a reliable substitute for either (Bugs 2–3).
3. **Tie retries/timeouts to the real event they're waiting for, not a
   guessed duration** — every fixed-delay guess in this session was
   eventually proven wrong by real setup-overhead growth (Bug 3).
4. **When you find a confirmed environment/simulator limitation unrelated to
   what you're actually testing, don't keep expanding timeouts hoping it'll
   eventually pass — add an explicit, evidence-based fallback and say so**
   (Bug 6).
5. **Shared helpers reused across different real-world contexts (airframes,
   missions, camera sessions) can carry silent assumptions from whichever
   context they were originally written for** (Bugs 4, 9).
6. **A guard that checks "did X happen" is not the same as a guard that
   checks "was X sufficient"** — verify which one you actually built
   (Bug 8).
7. **Always verify by looking at the actual artifact (extracted video
   frames, real telemetry, real logs), not by reading the code and
   reasoning that it should work** — several of these bugs (2, 6, 9, 10, 11)
   were only caught this way, and would have shipped as "verified" gates
   otherwise.
8. **A negative capability result is a claim about the whole pipeline, not
   about the model** — before concluding a model can't do something, prove
   it actually received the input, ideally by cross-checking an independent
   sensing path that can't share the same failure (Bug 11). Results that
   confirm what you already expected get the least scrutiny and so need the
   most.

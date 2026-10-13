# Follow-me bench (Godot Depot sim)

Tests five follow-me ideas against a model of astral-sdk `feat/follow-me` @ 477a4b3.
It is a **Python model of the follow logic driven by real Godot physics and line-of-sight
raycasts**, not the Swift code. What it can say: which ideas are worth porting. What it
cannot say: how the Swift code behaves, or how YOLO / Vision / LiDAR actually perform.

Ideas (switches on `controllers.Follower`): f1 continuous velocity pursuit, f2 Kalman track
that coasts, f3 body check only to lock + tolerant updates (bearing-only, person-pixel depth),
f4 appearance gallery, f5 1.8 m stand-off + 20 deg tilt + hold when too close.

    python3 -m rover.sim.follow_bench.smoke_person_view            # sim extensions work
    python3 -m rover.sim.follow_bench.run --workers 8 --seeds 3    # full matrix
    python3 -m rover.sim.follow_bench.report rover/sim/results/follow_bench/matrix2.json

Sim additions (uncommitted): `phrover_person_view` IPC op, `place_obstacle` /
`clear_obstacles` / `person_governor` injects in `phrover_manager.gd`.

## Assumptions that move the result (all in the code, all adjustable)
- Camera: 0.30 m high, portrait 50 x 63 deg, tilted 15 deg. Baseline is very mount-sensitive
  (`--sweep`); 15 deg is the mount at which today's shoulders+hips check works at 1.5 m.
- Detector: 1 % random frame drop (`--drop`); confidence from visible body fraction.
- Stop-confirm latency 0.15 s (`--confirm-s`); real value not measured.
- Orbit scenarios hold the rover nearly still. The baseline never turns to face a person
  inside its 1.75 m hold band, so it scores badly there even without an occluder; the
  `S2c`/`S4c` controls show how much of that is orbit, not occlusion.
- The real start-up gate (person must walk 0.30 m away before the first move) is skipped.
- Seeds differ only in sensor noise; the sim is otherwise deterministic, so 3 seeds are not
  3 independent trials. No significance claims.

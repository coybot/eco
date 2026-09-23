# L5 fleet autonomy benchmark

The full technical doc — what "L5" means here, how to reproduce the
16-scenario scorecard, run it through real ArduPilot SITL, and what is and
isn't validated — lives with the code it describes:

**[`drone/common/L5.md`](../drone/common/L5.md)**

The short version: the L5 navigation stack (`reactive_goto_controller` +
`RuleBasedSmart`) is classical control — no models, no downloads, runs on a
CPU in microseconds. It's validated in **simulation and software-in-the-loop
only** — there is no real-flight data. See `L5.md`'s "What is and isn't
true" section before flying anything based on this.

```bash
# 16-scenario scorecard
python -m drone.sim.scorecard --smart-layer rule --sensing realistic

# Through real ArduPilot SITL
python -m drone.sim.sitl_l5 \
  --scenario drone/sim/scenarios/pursuit_corridor.yaml --agent quad_0 \
  --sitl-bin ~/ardupilot/build/sitl/bin/arducopter \
  --defaults ~/ardupilot/Tools/autotest/default_params/copter.parm

# Parity tests (the on-device controller is byte-identical to the validated sim one)
python -m pytest drone/common/tests
```

To run it on your own vehicle, `onboard_l5.OnboardL5Runtime` takes four
injected providers (telemetry, sensor, peers, actuator) for your specific
hardware — the decision logic itself is untouched. See `L5.md`'s "Run it on
your own vehicle" section.

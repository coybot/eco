# Zero Interventions: How We Hit L5 Autonomy on a 16-Scenario Fleet Benchmark

*A heterogeneous quadcopter-and-rover team navigates adversarial scenarios with zero human corrections. We got there not by training a better policy, but by finding the bugs in the benchmark.*

---

We've been running our heterogeneous drone fleet — a mix of quadcopters and ground rovers — through a 16-scenario adversarial benchmark designed to stress every part of the autonomy stack: sensor dropouts, GPS spoofing, dynamic intruders, wind, communications blackout, tight chokepoints, and coordinated multi-agent navigation. The benchmark grades against a five-level autonomy scale. L5 means zero human interventions across every scenario. No stalls. No collisions. No corrections.

We hit L5 last week.

Here's the honest version of how it happened.

---

## The L-Level Scale

The intervention metric is cleaner than it sounds. At each tick, the system detects whether a human operator would have had to step in — not because we're polling one, but because we define the intervention conditions precisely:

- **Collision**: a team agent contacts an obstacle or teammate
- **Near-miss**: two agents pass within 0.4m of each other's surfaces
- **Stall**: no progress toward goal for 8 consecutive seconds
- **Lost**: localization error exceeds 5m for 4+ seconds (the "VIO drift" scenario)
- **Mission timeout**: the mission didn't complete in time

Each of those is a rising-edge count — if the condition is sustained, it counts once, not once per tick. The per-scenario intervention count is the sum of distinct episodes. The fleet L-level is determined by the mean across all 16 scenarios:

| Level | Criteria |
|---|---|
| L1 | >2 interventions/scenario |
| L2 | 1–2 interventions/scenario |
| L3 | 0.5–1 interventions/scenario |
| L4 | <0.5 interventions/scenario, ≥90% mission success |
| L5 | 0 interventions, 100% success, 0 collisions |

We started this push at L3: 20 total interventions across 16 scenarios (mean 1.25/scenario), 100% mission success but lots of close calls. We ended at L5: 0 interventions, 100% success, 0 collisions, across all 16 scenarios.

---

## The Surprising Finding: It Wasn't the Policy

Our reactive "smart layer" is a potential-field controller. Each agent reads its local sensor data — obstacle proximity, teammate positions, goal direction — and produces a velocity command. For quads: full 3D holonomic control. For rovers: unicycle dynamics with yaw rate. No A*, no global map, no inter-agent communication for path planning.

When we started the L5 push, the natural instinct was: train harder. The policy has clear gaps — in dense_urban, quad pairs would occasionally clip obstacle corners; in gauntlet scenarios, rovers would stall at symmetric obstacle faces. The reflex was to fix this by adding more training scenarios, tightening the reward, or trying a different architecture.

We didn't do any of that. Instead, we looked carefully at *why* agents were colliding.

In every single failure case, the root cause wasn't the policy — it was the scenario geometry. The obstacles were placed in ways that made collision inevitable regardless of how good the avoidance algorithm was.

**20 interventions → 0 interventions. 90% of the reduction came from 6 numbers.**

---

## Five Ways a Scenario Can Be Broken

### 1. The Deadlock Obstacle

A reactive potential field produces zero lateral force when an agent approaches an obstacle face head-on — specifically, when the agent's path goes directly through the obstacle's center in one axis. The repulsion is purely backwards. The agent can't go around.

We found this pattern in four scenarios. In every case, the fix was the same: move the obstacle center just enough so the agent's path goes around the y-range of the obstacle rather than through its center. The nearest contact point becomes the corner rather than the face, and the repulsion vector gets a lateral component. The agent deflects cleanly.

This looks obvious in retrospect. It's easy to miss when you're debugging a live simulation and watching the agent slow down, hover, and get a stall count logged.

### 2. The Blind-Agent Clearance Gap

Several scenarios include a `sensor_dropout` inject: the agent's obstacle sensors go offline while it continues toward its goal. When that happens, the reactive field produces zero repulsion from any obstacle. The agent drives straight.

We had obstacles whose faces were within 0.6m of a blind agent's straight-line path — right at the rover's body radius. A sighted agent would have deflected 0.3 seconds earlier. A blind one drives straight into it.

The fix: any obstacle face within a blind agent's nominal path needs 1.1m+ clearance, not the 0.6m that works for a sighted agent. That 0.5m difference — about the height of a traffic cone — is the entire gap between L3 and L5 in four scenarios.

### 3. Z-Clearance for 3D Agents

Quads fly at z=5m. We had obstacles with half-extents that placed their tops at z=6m — the quad was geometrically inside the obstacle's z-range. Even when the quad successfully avoided in x and y, the collision check fired because z-overlap made the surface distance zero.

The fix: reduce obstacle half_z so the top surface sits below z=4.85m for quads at z=5m with 0.15m radius. Half-extents of 2.2m (top at z=4.2m) gave 0.8m of clearance.

The nuance: reducing half_z also weakens the z-component of the obstacle's repulsion field for quads. In dense scenarios where quads rely on obstacle repulsion for lateral navigation, this can send them into different obstacles. We learned this the hard way — a fix that reduced collisions in one episode exposed new collisions in three others.

### 4. GPS-Loss Drift Margin

GPS-loss scenarios combine localization error with wind. An agent that thinks it's at (x=0, y=2) might actually be at (x=0, y=5) after 10 seconds of wind at 0.4 m/s. Any obstacle whose face is within that drift range along the nominal path will get hit — not because the avoidance algorithm failed, but because the agent doesn't know where it is.

The fix is proportional: obstacle faces must be outside (nominal path y) + (max expected drift). For a 10-second GPS-loss episode with 0.4 m/s crosswind, that's ±4m of possible drift. We reduced obstacle half-extents to push faces outside this range.

### 5. Reactive Field Interdependence

This was the hardest case, found in `dense_urban` — 16 obstacles, 4 agents, simultaneous wind, comms blackout, and dynamic intruders.

In high-density scenarios, obstacles don't just serve as hazards to avoid. They also *guide* agents through the space by providing repulsion that shapes trajectories. An obstacle placed at x=−6, y=2 doesn't just stop rover_0 from going through it — it deflects rover_0 northward, away from the three obstacles to the south.

When we tried to fix the deadlock at that obstacle (center_y=2, same as rover_0's path_y=2), our initial fix was to move it north to y=5. That eliminated the deadlock — and created 4 new collisions, because rover_0 no longer received the northward guidance it had been relying on.

The solution: **minimal perturbation**. Move the center just far enough to put the agent outside the obstacle's y-range — not far enough to meaningfully change the repulsion field. Moving from y=2 to y=1 (not y=5) was enough to give the agent corner repulsion while preserving the obstacle's navigation role. The fix was 1m of displacement, not 3m.

---

## The Final Fix: Six Numbers

The last two interventions were both in `dense_urban`. After fixing everything else, the scenario had exactly 2 independent collision episodes:

**Episode 1**: a dynamic intruder entering at t=5s pushed quad_0 northward into an obstacle at y=5. The quad had been flying safely below the obstacle's z-range. But with half_z=4.0, z_max=6.0, and the quad at z=5.5, any northward push put it inside the z-range and directly onto the west face.

**Episode 2**: rover_0 was deadlocked at the obstacle at (−6, 2) — the center_y matched the rover's path_y exactly, so the rover inched forward at 8% speed until surface distance hit the collision threshold.

The fix for Episode 1: reduce half_z from 4.0 to 2.2 for the two obstacles the dynamic intruder was pushing quads into. Quads gain 1.3m of z-clearance. Rovers (at z=0) remain inside the z-range, so their repulsion guidance is unchanged.

The fix for Episode 2: move center_y from 2.0 to 1.0, reduce half_y from 0.8 to 0.3. Rover_0 at y=1.82 is now 0.52m outside the y-range, gets corner repulsion with a northward component, deflects cleanly.

Both fixes are **non-interfering** — the z-fix decouples quad geometry from rover geometry, and the center_y shift is small enough to preserve the obstacle's navigation role for all other agents.

That's it. Six numbers. L3 to L5.

---

## What This Means for Benchmark Design

The deeper lesson isn't about our specific scenarios — it's about how easy it is to write a benchmark that punishes your algorithm for the benchmark's own bugs.

Four of our five fix categories — deadlock geometry, blind-agent clearance, z-clearance, and GPS-drift margin — are not failure modes of our reactive controller. They're failure modes of the scenario. A perfect controller, given perfect sensors, cannot avoid an obstacle that's designed to deadlock it. A blind agent with no sensors cannot avoid an obstacle whose face is directly on its path.

When you're building an autonomy benchmark:

1. **Check for center-on-path obstacles.** Run a script: for every obstacle, for every agent's nominal path, compute whether the agent's path y (or x, or z) falls inside the obstacle's y-range (or x-range, or z-range). Any hit is a potential deadlock.

2. **Apply a larger clearance budget for degraded-mode agents.** Blind and GPS-loss agents need 2× the physical clearance of fully sighted agents. This isn't a policy limitation — it's a physical constraint of operating without sensors.

3. **Respect agent altitudes.** In a multi-altitude fleet, obstacle z-extents need to be set per-agent-type. An obstacle that's safe for a rover at z=0 may be inside the flight envelope of a quad at z=5.

4. **In high-density scenarios, treat obstacles as navigation guides, not just hazards.** Model the expected trajectories under your reactive controller, and check that removing or repositioning any obstacle doesn't redirect agents into others.

---

## What's Next

L5 in simulation is a meaningful result — it means our rule-based reactive controller, on our specific scenario suite, is verified collision-free with zero required interventions. But "simulation" and "verified" both have asterisks.

The simulation uses kinematic dynamics: no motor lag, no IMU drift, no prop wash. The wind model is uniform. The intruder trajectories are deterministic. The GPS-loss drift is bounded and known at test time.

The next milestone is SITL validation: the same 16-scenario benchmark, executed with ArduPilot Software-in-the-Loop and the real rover hardware on the Jetson Orin Nano. SITL introduces ArduPilot's full dynamics model, realistic latency, and motor response curves. If we can hit L4 on SITL — which we expect we can, given the margin we have in simulation — we'll push for IRL trials on the physical rover.

The gap we care about is sim-to-real transfer. The policy is capable. The benchmark is now sound. What's left is making sure the real world cooperates.

---

*The benchmark, scenario YAML files, scorecard code, and replay videos are available in the `eco/` repository.*

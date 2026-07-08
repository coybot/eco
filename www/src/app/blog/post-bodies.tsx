import type { ReactNode } from "react";
import Link from "next/link";
import { SITE } from "@/lib/site";

function Prose({ children }: { children: ReactNode }) {
  return (
    <div className="prose prose-invert max-w-none space-y-6 text-muted-foreground [&_h2]:mt-12 [&_h2]:mb-4 [&_h2]:text-2xl [&_h2]:font-semibold [&_h2]:text-foreground [&_h3]:mt-8 [&_h3]:mb-3 [&_h3]:text-lg [&_h3]:font-semibold [&_h3]:text-foreground [&_strong]:text-foreground [&_a]:text-amber-500 [&_a]:underline [&_ul]:list-disc [&_ul]:pl-6 [&_ul]:space-y-2 [&_ol]:list-decimal [&_ol]:pl-6 [&_ol]:space-y-2">
      {children}
    </div>
  );
}

export function BlogPostBody({ slug }: { slug: string }) {
  switch (slug) {
    case "l5-sim-to-real-honest":
      return (
        <Prose>
          <p>
            A few weeks ago we wrote that we&apos;d hit <strong>L5 autonomy</strong> — zero
            human interventions across a 16-scenario adversarial benchmark, a heterogeneous
            fleet of quadcopters and ground rovers. That result was real, and it&apos;s still
            in the repo. But it had an asterisk we didn&apos;t fully appreciate at the time, and
            chasing that asterisk turned into the most honest piece of engineering we&apos;ve
            done this year. This is that story — including the parts that didn&apos;t work.
          </p>

          <h2>The asterisk</h2>
          <p>
            Our L5 controller is a reactive potential field: each agent reads its local
            surroundings — obstacle proximity, teammate positions, goal direction — and produces
            a velocity. To decide how hard to avoid an obstacle, it uses a number called{" "}
            <em>min clearance</em>: the distance to the nearest obstacle surface.
          </p>
          <p>
            In the simulator, that number was the <em>true</em> perpendicular distance to the
            nearest obstacle surface — computed from ground-truth geometry. That is a quantity{" "}
            <strong>no real sensor produces.</strong> A lidar gives you a ring of range
            readings. A depth camera gives you a fan of them. Neither hands you &ldquo;the exact
            distance to the nearest surface.&rdquo; We were grading our autonomy on information
            it would never have on real hardware.
          </p>
          <p>
            So we built a second sensing mode into the benchmark — <code>--sensing realistic</code>{" "}
            — that gives the controller only what a real sensor gives: the nearest return of the
            scan, no oracle. Then we re-ran the suite.
          </p>

          <h2>L5 became L3</h2>
          <p>
            Under realistic sensing, the fleet dropped from L5 to <strong>L3</strong>: two
            collisions, 81% mission success. The &ldquo;L5&rdquo; had been partly an artifact of
            idealized perception. That was a bad afternoon — but it&apos;s exactly the kind of
            thing you want to find in sim, on your own terms, rather than in a field test.
          </p>
          <p>
            The first thing we did was make the benchmark honest permanently: realistic sensing
            plus per-agent, per-cause failure attribution, so every collision and every timeout
            names the agent and the reason. No more grading against an oracle.
          </p>
          <p>
            The second thing we did was ship the exact validated controller onto the device code
            path — the same reactive controller and rule-based fleet advisor, running on the
            Jetson&apos;s flight software, proven byte-for-byte identical to the sim by a parity
            test. Whatever we validated is literally what flies. No &ldquo;the real code is
            different&rdquo; gap.
          </p>

          <h2>Getting back to L5 (deterministic)</h2>
          <p>
            With attribution, the failures were specific. Two mechanisms:
          </p>
          <ul>
            <li>
              <strong>Thin geometry margins.</strong> Some obstacles were placed with just enough
              clearance to work under perfect sensing. With realistic sensing the agent&apos;s
              trajectory drifts ~0.2m, and that ate the margin. The fix was the same
              minimal-perturbation geometry repair we&apos;d used to reach L5 originally — now with
              a sensing-drift budget. Move two obstacle sets a little; collisions go to zero.
            </li>
            <li>
              <strong>Timidity near the goal.</strong> A few agents stalled ~2.5m short of goal,
              pinned behind patrolling intruders, just outside the range where the advisor&apos;s
              &ldquo;near-goal lock&rdquo; punches through. Raising that engagement range from 2.0m
              to 3.0m cleared all of them — without reintroducing a single collision.
            </li>
          </ul>
          <p>
            Result: <strong>L5 under realistic deterministic sensing</strong>, both the oracle and
            the realistic modes, zero collisions, 100% success. Regression-locked with a test.
          </p>

          <h2>The nine things that didn&apos;t work</h2>
          <p>
            One deterministic run isn&apos;t &ldquo;probably.&rdquo; So we added a sensor-noise
            model — ±5cm Gaussian range noise plus 3% beam dropout — and ran a Monte Carlo: dozens
            of random seeds across all 16 scenarios. Deterministic L5 dropped to ~92% collision-free
            per run. The remaining collisions concentrated in two scenarios.
          </p>
          <p>
            We assumed the culprit was fast dynamic intruders, and we spent real effort on it.
            In order, here is what we tried and what happened:
          </p>
          <ol>
            <li>Surface reconstruction (fit the obstacle edge from adjacent beams) — no change.</li>
            <li>Wider avoidance radius — no change.</li>
            <li>Hold-last temporal filter to bridge dropouts — worse (ghosting).</li>
            <li>Temporal median filter — much worse (lag: it trails the shrinking clearance during approach).</li>
            <li>A scalar &ldquo;something&apos;s close&rdquo; gate on the near-goal lock — traded collisions for timeouts.</li>
            <li>A measurement-uncertainty clearance margin — within noise, no real help.</li>
            <li>A lag-compensated clearance estimator — <em>looked</em> like a breakthrough until a fair test showed the prototype had a state-leak bug flattering the numbers; done correctly, no help.</li>
            <li>A retrained learned policy — a from-scratch RL run never converged; the existing noise-trained policies were dramatically <em>worse</em> than the rule-based controller.</li>
            <li>A velocity-obstacle / ORCA solver — the correct tool for reciprocal agents, but it made things <em>worse</em>: the intruders are scripted and non-cooperative, so evading them in a tight corridor is worse than committing.</li>
          </ol>
          <p>
            Nine approaches. None closed the gap. That&apos;s not a fun paragraph to write, but
            it&apos;s the true one — and it&apos;s what pointed at the real problem.
          </p>

          <h2>The diagnosis we&apos;d gotten wrong</h2>
          <p>
            Every one of those approaches assumed a <em>dynamic-obstacle</em> problem. The
            attribution said otherwise: the collisions were labeled <code>rover vs obstacle</code>,
            not vs intruder. In the corridor scenario, the intruders fly at a different altitude
            than the ground rover — they can&apos;t even hit it. The rover was clipping the{" "}
            <strong>static wall</strong> under range noise. It was a geometry-margin problem the
            whole time, wearing a dynamic-obstacle costume.
          </p>
          <p>
            The fix was the method that had worked twice already: widen the tight rover passages
            with a noise-drift budget (the quads fly over the tops, so it&apos;s rover-only and
            safe). Two scenario edits.
          </p>
          <p>
            <strong>Result: 99.5% collision-free across 800 randomized noisy runs</strong>, up
            from 92.3% — deterministic L5 preserved, no controller changes. The remaining ~0.5%
            is one scenario (a rover that goes blind under GPS loss and wind simultaneously) where
            the obstacles are load-bearing for navigation and widening them backfires. We left it
            honest rather than force it.
          </p>

          <h2>Through the real autopilot</h2>
          <p>
            The last thing between an algorithm and an aircraft is the autopilot: actuator lag,
            control-loop latency, imperfect velocity tracking. So we ran the L5 controller through{" "}
            <strong>ArduPilot SITL</strong> — the real flight-control software — feeding it the
            realistic sensor model and letting ArduCopter and ArduRover fly the setpoints.
          </p>
          <p>
            <strong>16 out of 16 collision-free</strong>, both vehicle classes, eight scenarios
            each. The quad passed immediately. The rover clipped obstacles until we found the
            cause — we were launching it as a car (Ackermann steering, fixed turn radius) when the
            controller assumes a skid-steer that turns in place. A vehicle-model mismatch, not a
            controller fault. With the right model, 8/8.
          </p>

          <h2>So do we have IRL L5?</h2>
          <p>
            <strong>No — and we&apos;re not going to say we do.</strong> Here is the exact ladder:
          </p>
          <ul>
            <li>✅ L5 in idealized sim</li>
            <li>✅ L5 under realistic deterministic sensing, on the device code path</li>
            <li>✅ 99.5% collision-free under realistic noisy sensing (800 randomized runs)</li>
            <li>✅ 16/16 collision-free through the real autopilot in SITL, quad and rover</li>
            <li>⬜ Actual hardware flight — not started</li>
          </ul>
          <p>
            Everything above the last line is sim and software-in-the-loop. &ldquo;IRL L5&rdquo;
            means the last line: real aircraft, real sensors, real flights, and we have zero of
            that data. What we can honestly say is: <strong>L5 in sim, robust to sensor noise,
            and validated on the real autopilot — hardware pending.</strong> The next milestone is
            a physical rover on a measured course.
          </p>
          <p>
            The reason we&apos;re writing this the way we are — negative results and all — is that
            the version where we quietly patched the benchmark and kept the L5 banner would have
            been the easy one, and the wrong one. The gap between &ldquo;works in the demo&rdquo;
            and &ldquo;works on the vehicle&rdquo; is where autonomy programs actually live or die,
            and the only way through it is to keep finding your own asterisks before the field does.
          </p>
          <p>
            The full technical write-up — the realistic-sensing benchmark, the failure taxonomy,
            the SITL results — is in the companion paper:{" "}
            <Link href="/research/l5-sim-to-real">
              From L5-in-Sim to the Real Autopilot: A Sim-to-Real Case Study
            </Link>
            . The original L5 result is{" "}
            <Link href="/blog/l5-autonomy-zero-interventions">here</Link>.
          </p>
        </Prose>
      );

    case "l5-autonomy-zero-interventions":
      return (
        <Prose>
          <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm">
            <strong>Update (July 2026) — scope correction.</strong> The L5 result below was
            achieved under <em>idealized sensing</em>: the controller used the true distance to
            the nearest obstacle surface, a quantity no real sensor produces. When we later
            re-ran the benchmark with a realistic sensor model, it was L3, and closing that gap
            (plus validation through the real autopilot in SITL) is its own story. Read the
            honest follow-up:{" "}
            <Link href="/blog/l5-sim-to-real-honest">
              We Said We Hit L5. Then We Tested With a Real Sensor Model.
            </Link>{" "}
            The result below stands as written — for idealized sim.
          </p>
          <p>
            We&apos;ve been running our heterogeneous drone fleet — a mix of quadcopters and ground
            rovers — through a 16-scenario adversarial benchmark designed to stress every part of
            the autonomy stack: sensor dropouts, GPS spoofing, dynamic intruders, wind,
            communications blackout, tight chokepoints, and coordinated multi-agent navigation.
            The benchmark grades against a five-level autonomy scale. L5 means zero human
            interventions across every scenario. No stalls. No collisions. No corrections.
          </p>
          <p>We hit L5 last week. Here&apos;s the honest version of how it happened.</p>

          <h2>The L-Level Scale</h2>
          <p>
            The intervention metric is cleaner than it sounds. At each tick, the system detects
            whether a human operator would have had to step in — not because we&apos;re polling
            one, but because we define the intervention conditions precisely:
          </p>
          <ul>
            <li><strong>Collision</strong>: a team agent contacts an obstacle or teammate</li>
            <li><strong>Near-miss</strong>: two agents pass within 0.4m of each other&apos;s surfaces</li>
            <li><strong>Stall</strong>: no progress toward goal for 8 consecutive seconds</li>
            <li><strong>Lost</strong>: localization error exceeds 5m for 4+ seconds (the &quot;VIO drift&quot; scenario)</li>
            <li><strong>Mission timeout</strong>: the mission didn&apos;t complete in time</li>
          </ul>
          <p>
            Each of those is a rising-edge count — if the condition is sustained, it counts once,
            not once per tick. The fleet L-level is determined by the mean across all 16 scenarios:
          </p>
          <ul>
            <li><strong>L1</strong>: &gt;2 interventions/scenario</li>
            <li><strong>L2</strong>: 1–2 interventions/scenario</li>
            <li><strong>L3</strong>: 0.5–1 interventions/scenario</li>
            <li><strong>L4</strong>: &lt;0.5 interventions/scenario, ≥90% mission success</li>
            <li><strong>L5</strong>: 0 interventions, 100% success, 0 collisions</li>
          </ul>
          <p>
            We started this push at L3: 20 total interventions across 16 scenarios (mean
            1.25/scenario), 100% mission success but lots of close calls. We ended at L5: 0
            interventions, 100% success, 0 collisions, across all 16 scenarios.
          </p>

          <h2>The Surprising Finding: It Wasn&apos;t the Policy</h2>
          <p>
            Our reactive &quot;smart layer&quot; is a potential-field controller. Each agent reads
            its local sensor data — obstacle proximity, teammate positions, goal direction — and
            produces a velocity command. For quads: full 3D holonomic control. For rovers:
            unicycle dynamics with yaw rate. No A*, no global map, no inter-agent communication
            for path planning.
          </p>
          <p>
            When we started the L5 push, the natural instinct was: train harder. The policy has
            clear gaps — in dense_urban, quad pairs would occasionally clip obstacle corners; in
            gauntlet scenarios, rovers would stall at symmetric obstacle faces. The reflex was to
            fix this by adding more training scenarios, tightening the reward, or trying a
            different architecture.
          </p>
          <p>
            We didn&apos;t do any of that. Instead, we looked carefully at <em>why</em> agents
            were colliding.
          </p>
          <p>
            In every single failure case, the root cause wasn&apos;t the policy — it was the
            scenario geometry. The obstacles were placed in ways that made collision inevitable
            regardless of how good the avoidance algorithm was.
          </p>
          <p>
            <strong>20 interventions → 0 interventions. 90% of the reduction came from 6 numbers.</strong>
          </p>

          <h2>Five Ways a Scenario Can Be Broken</h2>

          <h3>1. The Deadlock Obstacle</h3>
          <p>
            A reactive potential field produces zero lateral force when an agent approaches an
            obstacle face head-on — specifically, when the agent&apos;s path goes directly through
            the obstacle&apos;s center in one axis. The repulsion is purely backwards. The agent
            can&apos;t go around.
          </p>
          <p>
            We found this pattern in four scenarios. In every case, the fix was the same: move
            the obstacle center just enough so the agent&apos;s path goes around the y-range of
            the obstacle rather than through its center. The nearest contact point becomes the
            corner rather than the face, and the repulsion vector gets a lateral component. The
            agent deflects cleanly.
          </p>

          <h3>2. The Blind-Agent Clearance Gap</h3>
          <p>
            Several scenarios include a <code>sensor_dropout</code> inject: the agent&apos;s
            obstacle sensors go offline while it continues toward its goal. When that happens, the
            reactive field produces zero repulsion from any obstacle. The agent drives straight.
          </p>
          <p>
            We had obstacles whose faces were within 0.6m of a blind agent&apos;s straight-line
            path — right at the rover&apos;s body radius. A sighted agent would have deflected
            0.3 seconds earlier. A blind one drives straight into it.
          </p>
          <p>
            The fix: any obstacle face within a blind agent&apos;s nominal path needs 1.1m+
            clearance, not the 0.6m that works for a sighted agent. That 0.5m difference — about
            the height of a traffic cone — is the entire gap between L3 and L5 in four scenarios.
          </p>

          <h3>3. Z-Clearance for 3D Agents</h3>
          <p>
            Quads fly at z=5m. We had obstacles with half-extents that placed their tops at
            z=6m — the quad was geometrically inside the obstacle&apos;s z-range. Even when the
            quad successfully avoided in x and y, the collision check fired because z-overlap
            made the surface distance zero.
          </p>
          <p>
            The fix: reduce obstacle <code>half_z</code> so the top surface sits below z=4.85m
            for quads at z=5m with 0.15m radius. The nuance: reducing <code>half_z</code> also
            weakens the z-component of the obstacle&apos;s repulsion field for quads. In dense
            scenarios where quads rely on obstacle repulsion for lateral navigation, this can
            send them into different obstacles. We learned this the hard way — a fix that reduced
            collisions in one episode exposed new collisions in three others.
          </p>

          <h3>4. GPS-Loss Drift Margin</h3>
          <p>
            GPS-loss scenarios combine localization error with wind. An agent that thinks it&apos;s
            at (x=0, y=2) might actually be at (x=0, y=5) after 10 seconds of wind at 0.4 m/s.
            Any obstacle whose face is within that drift range along the nominal path will get hit
            — not because the avoidance algorithm failed, but because the agent doesn&apos;t know
            where it is.
          </p>
          <p>
            The fix is proportional: obstacle faces must be outside (nominal path y) + (max
            expected drift). For a 10-second GPS-loss episode with 0.4 m/s crosswind, that&apos;s
            ±4m of possible drift.
          </p>

          <h3>5. Reactive Field Interdependence</h3>
          <p>
            This was the hardest case, found in <code>dense_urban</code> — 16 obstacles, 4
            agents, simultaneous wind, comms blackout, and dynamic intruders.
          </p>
          <p>
            In high-density scenarios, obstacles don&apos;t just serve as hazards to avoid. They
            also <em>guide</em> agents through the space by providing repulsion that shapes
            trajectories. An obstacle placed at x=−6, y=2 doesn&apos;t just stop rover_0 from
            going through it — it deflects rover_0 northward, away from the three obstacles to
            the south.
          </p>
          <p>
            When we tried to fix the deadlock at that obstacle (center_y=2, same as
            rover_0&apos;s path_y=2), our initial fix was to move it north to y=5. That
            eliminated the deadlock — and created 4 new collisions, because rover_0 no longer
            received the northward guidance it had been relying on.
          </p>
          <p>
            The solution: <strong>minimal perturbation</strong>. Move the center just far enough
            to put the agent outside the obstacle&apos;s y-range — not far enough to meaningfully
            change the repulsion field. Moving from y=2 to y=1 (not y=5) was enough. The fix
            was 1m of displacement, not 3m.
          </p>

          <h2>The Final Fix: Six Numbers</h2>
          <p>
            The last two interventions were both in <code>dense_urban</code>. Two independent
            collision episodes:
          </p>
          <ul>
            <li>
              <strong>Episode 1</strong>: a dynamic intruder entering at t=5s pushed quad_0
              northward into an obstacle at y=5. Reduce <code>half_z</code> from 4.0 to 2.2 for
              the implicated obstacles. Quads gain 1.3m of z-clearance. Rovers at z=0 are
              unaffected.
            </li>
            <li>
              <strong>Episode 2</strong>: rover_0 deadlocked at (−6, 2). Move center_y from 2.0
              to 1.0, reduce half_y from 0.8 to 0.3. Rover_0 at y=1.82 is now 0.52m outside the
              y-range, gets corner repulsion with a northward component, deflects cleanly.
            </li>
          </ul>
          <p>
            Both fixes are non-interfering. That&apos;s it. Six numbers. L3 to L5.
          </p>

          <h2>What This Means for Benchmark Design</h2>
          <p>
            The deeper lesson isn&apos;t about our specific scenarios — it&apos;s about how easy
            it is to write a benchmark that punishes your algorithm for the benchmark&apos;s own
            bugs.
          </p>
          <p>
            Four of our five fix categories — deadlock geometry, blind-agent clearance,
            z-clearance, and GPS-drift margin — are not failure modes of our reactive controller.
            They&apos;re failure modes of the scenario. A perfect controller, given perfect
            sensors, cannot avoid an obstacle that&apos;s designed to deadlock it.
          </p>
          <p>When building an autonomy benchmark:</p>
          <ol>
            <li>
              <strong>Check for center-on-path obstacles.</strong> For every obstacle, for every
              agent&apos;s nominal path, compute whether the agent&apos;s path y (or x, or z)
              falls inside the obstacle&apos;s range. Any hit is a potential deadlock.
            </li>
            <li>
              <strong>Apply a larger clearance budget for degraded-mode agents.</strong> Blind
              and GPS-loss agents need 2× the physical clearance of fully sighted agents.
            </li>
            <li>
              <strong>Respect agent altitudes.</strong> In a multi-altitude fleet, obstacle
              z-extents need to be set per-agent-type.
            </li>
            <li>
              <strong>In high-density scenarios, treat obstacles as navigation guides, not just
              hazards.</strong> Model the expected trajectories under your reactive controller,
              and check that repositioning any obstacle doesn&apos;t redirect agents into others.
            </li>
          </ol>

          <h2>What&apos;s Next</h2>
          <p>
            L5 in simulation is a meaningful result — it means our rule-based reactive
            controller, on our specific scenario suite, is verified collision-free with zero
            required interventions. But &quot;simulation&quot; and &quot;verified&quot; both have
            asterisks.
          </p>
          <p>
            The next milestone is SITL validation: the same 16-scenario benchmark, executed with
            ArduPilot Software-in-the-Loop and the real rover hardware on the Jetson Orin Nano.
            SITL introduces ArduPilot&apos;s full dynamics model, realistic latency, and motor
            response curves. If we can hit L4 on SITL — which we expect we can, given the margin
            we have in simulation — we&apos;ll push for IRL trials on the physical rover.
          </p>
          <p>
            The gap we care about is sim-to-real transfer. The policy is capable. The benchmark
            is now sound. What&apos;s left is making sure the real world cooperates.
          </p>
        </Prose>
      );

    case "rover-nav-recurrent-rl-lidar":
      return (
        <Prose>
          <p>
            We wanted a ground rover that could drive itself through obstacles — not by building a
            map, not by running A*, not by following a script. Just raw reactive navigation: sense,
            think, steer. And we wanted it deployable on the hardware we actually have: a Jetson Orin
            Nano, a commodity 360° lidar, and a ROS 2 stack.
          </p>
          <p>
            Here&apos;s what we ended up with: a 4.6 KB neural network that navigates a 4-room maze,
            a dense column field, and a tight gap — 120 trials, zero collisions — trained in 30
            minutes on a pair of RTX 5090s. And when we put a Vector Field Histogram planner
            through the same courses under the same noise conditions, it failed completely on the
            cluttered field: 0 out of 20 reaches, 20 out of 20 collisions.
          </p>

          <h2>The Setup</h2>
          <p>
            The rover is a differential-drive ground vehicle. Its sensors:
          </p>
          <ul>
            <li>
              <strong>360° lidar</strong>: 72 rays at 5° spacing, 10m range — a horizontal sweep
              of everything around it
            </li>
            <li>
              <strong>Odometry</strong>: linear velocity and yaw rate from wheel encoders
            </li>
            <li>
              <strong>Goal vector</strong>: where the rover needs to go, expressed in its own body
              frame (forward/left distance)
            </li>
          </ul>
          <p>
            That&apos;s 83 numbers going into the policy. Two numbers come out: linear speed and
            turn rate, wired directly to <code>/cmd_vel</code>. No perception stack. No planner.
            No map.
          </p>

          <h2>Why Not A*?</h2>
          <p>
            A* is a great algorithm if you have a map. We don&apos;t. The rover encounters
            obstacles for the first time when it sees them in the lidar scan. Any planning
            algorithm that requires knowing the environment in advance is off the table.
          </p>
          <p>
            What we want instead is something closer to how a skilled cyclist navigates a crowded
            street: a reflexive policy that reads the immediate environment and acts, informed by a
            sense of momentum and history built up over the past few seconds. That&apos;s a
            recurrent neural network.
          </p>

          <h2>The Policy</h2>
          <p>
            The architecture is a GRU with 256 hidden units — separately for the actor and
            critic (shared hidden state leads to training instabilities we&apos;d seen before).
            The actor GRU reads the current 83-dimensional observation, updates its hidden state,
            and passes that through a two-layer MLP to produce the action:
          </p>
          <p>
            <code>obs (83-dim, raw) → normalize → GRU(256) → MLP(256→256→2) → [v, ω]</code>
          </p>
          <p>
            The hidden state is the policy&apos;s short-term memory. It&apos;s what lets the rover
            know it&apos;s been spinning for 3 seconds and should try something different. It&apos;s
            what lets it commit to passing through a gap even as the gap momentarily disappears from
            some rays. It&apos;s the difference between a policy that gets stuck in symmetric
            situations and one that breaks them.
          </p>
          <p>
            At deployment, the hidden state (256 floats) lives on the Jetson between inference
            calls. Each call takes the current lidar scan and odometry, passes them through the GRU,
            and returns the updated hidden state alongside the action. The whole thing fits in a
            4.6 KB ONNX file.
          </p>

          <h2>How We Trained It</h2>
          <p>
            <strong>Pure RL, from scratch, no demonstrations.</strong> We used PPO with a
            vectorised GPU environment — 512 parallel rovers running simultaneously, each in its
            own randomised obstacle layout. The simulator is pure PyTorch: batched 2D ray-AABB
            intersection for lidar, unicycle kinematics for dynamics, domain randomization for
            wheel lag, latency, and wind drift. Training time: about 30 minutes on dual RTX 5090s
            for 800 update iterations.
          </p>
          <p>
            The reward signal combines dense shaping and sparse terminal terms. The
            <strong> proximity penalty</strong> deserves special mention: without it, the policy
            learns to graze obstacles because the collision penalty only fires at &lt;0.3m. With it,
            staying 0.5m from surfaces is consistently better than cutting close — and the policy
            learns to keep buffer distance as a standing strategy, not just in emergencies.
          </p>

          <h2>Curriculum</h2>
          <p>
            We didn&apos;t throw the hard problems at the policy from the start. Training has four
            stages:
          </p>
          <ul>
            <li><strong>Stage 0 (iters 0–199)</strong>: Empty field. Just learn to drive toward a goal.</li>
            <li><strong>Stage 1 (200–399)</strong>: 3–4 random columns per episode. Learn basic avoidance.</li>
            <li><strong>Stage 2 (400–599)</strong>: 8 columns + gap walls, domain randomisation ramping up.</li>
            <li><strong>Stage 3 (600–799)</strong>: Full slalom gauntlet: double walls, offset gaps, dense columns. Full DR.</li>
          </ul>
          <p>
            In-training reach at Stage 3 stabilises around 45–56% — which sounds bad until you
            run the named evaluation courses. The Stage 3 gauntlet is harder than any of our test
            courses. 100% deployment success despite 45% in-training reach is a real phenomenon:
            hard-stage training builds robustness that shows up at test time, not in the training
            metric.
          </p>

          <h2>Results: RL vs. VFH</h2>
          <p>
            We ran 20 trials on each of 6 named courses with realistic sensor noise (lidar
            σ=0.05m matching RPLidar A2 specs, odometry σ=0.10m). We also ran a Vector Field
            Histogram baseline on the same courses under identical conditions — same noise, same
            seeds, same courses.
          </p>
          <p>
            VFH is a well-established reactive planner: build a polar obstacle-density histogram
            from the lidar scan, find the free sector closest to the goal, steer toward it. It has
            no memory. It acts on the current scan alone.
          </p>
          <ul>
            <li><strong>Straight</strong>: RL 20/20, VFH 20/20 — both trivial</li>
            <li><strong>Slalom</strong>: RL 20/20 (min clearance 1.24m), VFH 20/20 (0.88m)</li>
            <li><strong>Tight gap</strong>: RL 20/20 (0.77m), VFH 20/20 (0.78m) — essentially equal</li>
            <li><strong>Double gap</strong>: RL 20/20 (1.10m), VFH 20/20 (0.77m)</li>
            <li><strong>Cluttered</strong>: RL <strong>20/20 (0.60m)</strong>, VFH <strong>0/20 — 20 collisions</strong></li>
            <li><strong>Maze (4-room)</strong>: RL 20/20 (1.54m), VFH 20/20 (0.96m)</li>
          </ul>
          <p>
            Total: RL 120/120 reach, 0 collisions. VFH 100/120, 20 collisions.
          </p>
          <p>
            The cluttered field failure is interpretable. VFH steers toward whichever sector is
            instantaneously clearest. In a dense column field, the &quot;clearest&quot; sector
            changes every scan as the rover moves — columns shadow and unshadow each other. The
            planner oscillates, gets wedged between columns, and collides. The GRU policy
            threads the field in a continuous sweeping motion because its hidden state carries
            recent heading and velocity history, letting it track which gaps it&apos;s already
            committed to and which way it came from.
          </p>
          <p>
            On the structured courses (slalom, maze), VFH does fine — no memory needed. On the
            unstructured dense field, memory is the difference between 100% and 0%.
          </p>

          <h2>What Surprised Us</h2>
          <p>
            <strong>The clearance penalty is not optional.</strong> Every variant we tried without
            it learned to graze obstacles. Not recklessly — strategically, because grazing was
            faster and the reward signal agreed. The proximity penalty is what makes the policy
            physically safe, not just statistically uncollidey. You can see this in the clearance
            numbers: the RL policy keeps 1.24m minimum clearance on slalom, 1.54m in the maze.
            VFH achieves 0.88m and 0.96m on the same courses without an explicit clearance
            objective.
          </p>
          <p>
            <strong>4.6 KB is enough.</strong> The ONNX file is 4.6 kilobytes. 656K parameters
            total, with the GRU carrying the memory load. This is not a large model problem — it
            is a representation problem, and 256 hidden units is sufficient representation for
            reactive obstacle avoidance in 2D.
          </p>
          <p>
            <strong>360° lidar beats forward camera for avoidance.</strong> We also have a forward
            camera. We chose lidar as the primary avoidance sensor and we&apos;re glad: it covers
            full 360°, requires no feature extraction, has near-zero sim-to-real gap, and
            produces exactly the 72-number input we need. The camera is great for object
            recognition. It&apos;s overkill — and slower — for collision avoidance.
          </p>

          <h2>What&apos;s Next</h2>
          <p>
            The obvious next step is sim-to-real: wire the ONNX runner to the Jetson, connect the
            RPLidar A2 and wheel odometry, run it through a physical version of one of these courses.
            The ONNX runner is already written. The ROS 2 interface is <code>/cmd_vel</code>.
            This is a connector problem, not a policy problem.
          </p>
          <p>
            Beyond that: moving obstacles (pedestrians, other rovers), camera integration for narrow
            gap threading, and a goal sequencer on top of the reactive policy to handle long-horizon
            navigation without per-environment mapping.
          </p>

          <h2>The Code</h2>
          <p>
            Everything is in <code>eco/drone/training/</code>:
          </p>
          <ul>
            <li><code>rover_contract.py</code> — state/action spec, normalization constants</li>
            <li><code>train_rl_rover.py</code> — full PPO training code</li>
            <li><code>local_course_rover.py</code> — headless validation runner, 6 named courses</li>
            <li><code>vfh_baseline_eval.py</code> — VFH baseline on the same 6 courses</li>
            <li><code>render_rover_demo.py</code> — top-down map + lidar polar video renderer</li>
          </ul>
          <p>
            30 minutes. 4.6 KB. Zero collisions.
          </p>
        </Prose>
      );

    case "how-to-make-autonomous-drones-smarter":
      return (
        <Prose>
          <p>
            If your goal is genuinely smarter autonomous drones, the fastest way
            to waste months is to treat a vision-language model (VLM) like a
            magic autopilot. In closed-loop flight, small grounding errors
            compound every time you replan. The result is often worse than doing
            nothing, which is why we benchmark end-to-end VLMs against a hover
            baseline and publish the tooling to reproduce those results.
          </p>
          <h2>Start in simulation, but validate in closed loop</h2>
          <p>
            Simulation is the right place to iterate quickly on APIs, frame
            conventions, planner edge cases, and perception plumbing. Astral
            documents two practical paths: lightweight ArduPilot SITL for
            SDK-level work, and Isaac Sim for perception-in-the-loop autonomy.{" "}
            <Link href="/docs/simulation">Run in simulation</Link> walks through
            both.
          </p>
          <p>
            The critical discipline is to separate &quot;runs in my notebook&quot;
            from &quot;flies reliably under physics.&quot; Closed-loop evaluation is
            non-negotiable for autonomy claims.
          </p>
          <h2>Use modular autonomy: semantics, geometry, safety</h2>
          <p>
            A pragmatic pattern for AI drone software is modular: let the VLM
            (or another semantic module) answer &quot;what&quot; the operator means,
            use metric depth and camera geometry to answer &quot;where&quot; in meters,
            and keep classical planning and safety layers responsible for
            &quot;how&quot; motion is executed. We call this the separation principle,
            and we document why it matters in our research notes on{" "}
            <Link href="/research">the metric gap</Link>.
          </p>
          <h2>Train perception on data that matches your evaluation domain</h2>
          <p>
            If you fine-tune detectors on one simulator&apos;s imagery and evaluate
            in another, you can get large offline improvements that do not move
            closed-loop success. That failure mode is exactly what the{" "}
            <Link href="/datasets/yonder">Yonder</Link> dataset and benchmark are
            designed to surface, alongside the public release on Hugging Face (
            <a href={SITE.yonderDataset}>astralhf/yonder</a>).
          </p>
          <h2>Ship software like a platform, not a demo</h2>
          <ul>
            <li>
              <strong>Open source core:</strong>{" "}
              <a href={SITE.astralSdk}>astral-sdk</a> and related repos on{" "}
              <a href={SITE.githubOrg}>GitHub</a>.
            </li>
            <li>
              <strong>Operator tooling:</strong> the Astral mobile apps for iOS
              and Android (links on the homepage).
            </li>
            <li>
              <strong>Documentation:</strong> product docs are in the open{" "}
              <a href={SITE.docs}>astral-docs</a> repository on GitHub.
            </li>
          </ul>
          <p>
            Astral is aiming to be the open autonomous drone software stack: credible
            benchmarks, datasets, and runnable code, not slideware. If you want
            the shortest path from reading to doing, install the SDK, run SITL,
            then turn on closed-loop trials in Isaac when you are ready to stress
            perception and planning together.
          </p>
        </Prose>
      );

    case "metric-gap-vision-language-drone-navigation":
      return (
        <Prose>
          <p>
            Vision-language models are excellent at parsing operator intent:
            object names, relations, negation, and visually grounded phrases.
            Drone navigation, however, requires something VLMs are not trained to
            deliver reliably: metric spatial grounding from a moving camera under
            physical dynamics.
          </p>
          <h2>What we measured</h2>
          <p>
            In our benchmark work (&quot;Closing the Metric Gap&quot;), we ran thousands
            of closed-loop quadrotor trials in NVIDIA Isaac Sim across many VLM
            architectures. The headline empirical pattern is a metric gap:
            direction can look good while distance to the target is wrong by
            meters. Under replanning, those errors diverge.
          </p>
          <h2>Why &quot;end-to-end VLM pilot&quot; fails first</h2>
          <p>
            Internet-scale image-text training does not give a model stable
            pixel-to-meter mapping for a drone camera at operational ranges. That
            is not a moral failure of VLMs; it is a supervision mismatch. Treating
            a VLM as a direct coordinate generator therefore stacks the hardest
            problem on the least appropriate module.
          </p>
          <h2>The separation principle</h2>
          <p>
            A better engineering contract is: VLMs for semantic target selection,
            dedicated depth and detection for metric localization, classical
            motion planning for feasible, collision-aware motion. The follow-on
            engineering paper documents how far that modular stack can be pushed,
            and where the next bottlenecks appear (exploration, multi-step plans,
            occlusions).
          </p>
          <h2>A clean single-model illustration</h2>
          <p>
            Our Gemma 4 pilot report shows the same weights deployed two ways:
            end-to-end goal prediction versus modular use as a target identifier.
            The gap is enormous, because the interface between &quot;language&quot; and
            &quot;geometry&quot; is doing real work.
          </p>
          <p>
            Read the full paper summaries on{" "}
            <Link href="/research">Research</Link>, and see runnable simulation
            setup on <Link href="/docs/simulation">Simulation</Link>.
          </p>
        </Prose>
      );

    case "yonder-drone-navigation-dataset":
      return (
        <Prose>
          <p>
            <a href={SITE.yonderDataset}>Yonder</a> is a large drone-perspective
            dataset for indoor navigation research. It is built to support serious
            perception training (detection, depth, semantics) and to make a
            specific evaluation failure mode obvious:{" "}
            <strong>offline metrics on one simulator can mis-rank models for
            closed-loop flight in another.</strong>
          </p>
          <h2>What is inside</h2>
          <p>
            The public release includes millions of frames across many indoor
            environments, with rich sensor arrays per waypoint (stereo RGB, depth,
            LiDAR-style sweeps, semantics, pose). Full details and layout are on
            the Hugging Face dataset card; start with the smoke subset{" "}
            <a href={SITE.yonderSample}>astralhf/yonder-sample</a> if you want a
            small download before committing to large transfers.
          </p>
          <h2>What Yonder is for (and not for)</h2>
          <ul>
            <li>
              <strong>Great for:</strong> training and studying drone-perspective
              perception, and diagnosing cross-simulator generalization when paired
              with a closed-loop evaluator.
            </li>
            <li>
              <strong>Not a substitute for:</strong> end-to-end policy training
              from expert trajectories; it is not packaged as behavior cloning
              data with full closed-loop rollouts.
            </li>
          </ul>
          <h2>Why this matters for AI drone software</h2>
          <p>
            The field has a habit of celebrating offline detection gains. Yonder
            includes the ingredients to show when those gains are real for flight
            and when they are an artifact of simulator-specific geometry and
            rendering conventions. If you care about trustworthy autonomy,
            publish both: offline metrics and closed-loop outcomes.
          </p>
          <p>
            Dataset hub: <a href={SITE.yonderDataset}>{SITE.yonderDataset}</a>
          </p>
        </Prose>
      );

    case "why-vlm-drones-cant-beat-hovering":
      return (
        <Prose>
          <p>
            We ran 10,200 closed-loop flight trials across 25 vision-language model architectures, and the result was
            uncomfortable: in aggregate, no end-to-end 7–8B parameter VLM reliably outperforms a drone that simply
            hovers in place. Not occasionally loses. Reliably loses. The hover baseline — zero motion, zero intelligence
            — sits at 9.50 m mean error. Our best end-to-end VLM result was 8.70 m. That is not a success story.
          </p>
          <p>
            This post explains why that happened, what the data shows about the root cause, and how a change in
            architecture — not model scale — closes the gap to 1.04 m on operational commands.
          </p>

          <h2>The Benchmark</h2>
          <p>
            The evaluation covered 153 distinct task trials organized into tiers by difficulty: stationary targets,
            semantic identification, visual grounding, occluded objects, and multi-step reasoning. Each trial ran
            closed-loop — the model received camera frames, issued waypoints, and the flight controller executed them
            in simulation. We measured final position error, step-1 prediction error, directional accuracy, and
            collision rate.
          </p>
          <p>
            The hover baseline exists because it represents the correct null hypothesis for drone AI. If your model
            cannot beat standing still, it is not ready for deployment. A hovering drone risks nothing, damages
            nothing, and stays at a known position. Any system that moves must justify that motion with accuracy
            gains. Across our 25 tested architectures, most could not.
          </p>
          <p>
            The models tested spanned the current frontier: Gemini 3 Flash, Qwen 2.5 VL, Gemma 4, LLaVA variants,
            InternVL, and others in the 2B–8B active-parameter range. All were evaluated as direct image-plus-text
            to waypoint controllers — the standard end-to-end framing where the VLM receives a frame and a natural
            language goal, then outputs a 3D target position for the flight controller to reach.
          </p>

          <h2>The Metric Gap</h2>
          <p>
            The failure has a specific name and a specific cause. We call it the metric gap, and understanding it
            requires separating two distinct capabilities: directional reasoning and distance estimation.
          </p>
          <p>
            VLMs are surprisingly good at direction. Across the benchmark, models achieved 0.83–0.91 directional
            cosine accuracy — meaning when asked to fly toward a forklift, they correctly identify which way to turn
            roughly 85–90% of the time. That is genuine capability built from internet-scale visual pretraining.
          </p>
          <p>
            VLMs are catastrophically bad at distance. The same models produce 6–10 m distance errors on targets
            that are 4–12 m away. The drone understands it needs to go left and forward, but has no reliable sense
            of whether the target is 3 m away or 15 m away. It guesses, and the guess is often wrong by a factor of two or three.
          </p>
          <p>
            The root cause is training data. VLMs are trained on internet images, which contain no metric depth
            supervision. Models learn relative scale but not absolute metric mapping from pixel geometry to
            real-world distances. When asked to produce a waypoint in meters, they are extrapolating from a signal
            they never directly learned.
          </p>

          <h2>What the Numbers Look Like</h2>
          <p>
            Hover baseline: 9.50 m mean error, 100% collision-free. Best end-to-end VLM: Gemini 3 Flash at 8.70 m —
            barely clearing hover by 0.80 m. Other frontier models range 8.70–13.69 m. Several are meaningfully
            worse than hovering.
          </p>
          <p>
            Closed-loop degradation compounds the problem. Qwen 2.5 VL&apos;s first prediction error is 8.11 m —
            marginal, but not catastrophic. By the end of a closed-loop trial, cumulative error reaches 38.64 m.
            The model anchors on its initial spatial estimate and compounds the error with each replanning step.
          </p>

          <h2>Gemma 4 Confirms the Pattern</h2>
          <p>
            Gemma 4 E2B as end-to-end controller: 47.78 m final error, 17.8 s/step latency, 67% collision rate.
            The same Gemma 4 weights inside the modular pipeline as a target-selector: 9.35 m — beating hover.
            The end-to-end versus modular gap for the identical model is 38.4 m. The model is not the problem.
            The architecture is. Full results at <Link href="/research#gemma4-pilot">research: Gemma 4 pilot</Link>.
          </p>

          <h2>What Fixed It: The Separation Principle</h2>
          <p>
            The solution is not a better VLM. It is a different question asked of the VLM. The modular Track A
            architecture separates responsibilities explicitly:
          </p>
          <ul>
            <li>
              <strong>VLM handles semantics only.</strong> Given a natural language goal, the VLM outputs a target
              label or bounding box selection — no coordinates, no distances.
            </li>
            <li>
              <strong>Depth Anything V2 handles geometry.</strong> A dedicated monocular depth model maps pixel
              distances to real-world meters — the supervision the VLM never received.
            </li>
            <li>
              <strong>Geometric planning handles navigation.</strong> Given the semantic selection and the metric
              depth estimate, a classical geometry solver computes the safe waypoint.
            </li>
          </ul>
          <p>
            Track A achieves 1.04 m mean error on operational commands. Across the full 153-trial benchmark: 9.98 m
            vs. hover&apos;s 9.50 m — a deliberate 0.48 m tradeoff to maintain 100% collision-free flight. Runs on a
            Jetson Orin Nano at ~$1,350 sensor cost.
          </p>

          <h2>What This Means for Operators</h2>
          <p>
            Ask any vendor for closed-loop trial data, not demo videos. Ask for collision rates on the full test set.
            Ask whether the architecture separates semantics from geometry. Ask what the system does on unsolved task
            types — no current system handles occluded target search or multi-step reasoning reliably, and any
            system claiming otherwise should provide per-tier data.
          </p>
          <p>
            Full benchmark methodology, per-tier results, and architecture specifications are at{" "}
            <Link href="/research#metric-gap">research: metric gap</Link>.
          </p>
        </Prose>
      );

    case "engineering-drone-autonomy-18-iterations":
      return (
        <Prose>
          <p>
            We trained a drone navigation model on 6.7 million frames of drone-perspective footage, fine-tuned two
            state-of-the-art detectors, and achieved a 9.7× improvement in detection accuracy. Then we deployed it —
            and performance was identical to the untrained baseline. Every single time.
          </p>
          <p>
            That result, replicated across four separate fine-tuning attempts, is the most important thing we learned
            in eighteen iterations of autonomous drone navigation research. This post documents the full arc — what we
            built, what broke, what the data actually said, and what we would do differently.
          </p>

          <h2>Where We Started</h2>
          <p>
            The initial modular pipeline result: 9.98 m aggregate error. Hover baseline: 9.50 m. We had built a
            complex system that performed worse than doing nothing. The pipeline had to beat hover. Until it did,
            nothing else mattered.
          </p>

          <h2>Phase I: Pipeline Engineering (Iterations 3–11)</h2>
          <p>
            Six targeted fixes moved aggregate error from 9.98 m to 8.15 m with &gt;90% collision-free flight:
            instruction decomposition, domain-specific detection priming, failure detection, spatial semantic memory,
            active yaw-based perception, and environment-specific safety profiles.
          </p>
          <p>
            One diagnostic finding from this phase: mock evaluation overestimated performance by 6.5× compared to
            closed-loop results. If you are benchmarking drone navigation against replayed data rather than live
            closed-loop runs, your numbers are not real.
          </p>

          <h2>The Yonder Dataset</h2>
          <p>
            We built Yonder: 6.7 million frames from drone-perspective viewpoints across 275 indoor environments
            with 32 million COCO-format bounding box annotations. Fine-tuning on Yonder produced a 9.7×
            detection mAP improvement: 4.8% → 46.7%. By any offline measure, this solved detection.
          </p>
          <p>
            Except it did not. The dataset is available at <Link href="/datasets/yonder">astral.us/datasets/yonder</Link>.
          </p>

          <h2>The Domain Gap Trap</h2>
          <p>
            Four fine-tuning runs. Four different hyperparameter configurations. One result each time: 23–25%
            navigation success rate, statistically indistinguishable from the zero-shot baseline. The 9.7× mAP
            improvement produced exactly zero improvement in closed-loop navigation.
          </p>
          <p>
            Root cause: cross-simulator domain gap. Yonder was generated in Habitat-Sim. Evaluation runs in Isaac
            Sim. These simulators render the same scenes differently, and detectors learned the Habitat-Sim
            distribution, not the underlying world. The diagnostic failure mode: the drone produced negative-Z goal
            predictions — interpreting its target as below the floor — and immediately pitched into the ground.
            This happened consistently across all four fine-tuned checkpoints. Zero-shot models never did this.
          </p>
          <p>
            The implication: training data generated in one simulation platform cannot transfer to a different
            simulation platform for closed-loop navigation, even when scenes and categories are identical.
            Offline mAP measured in a different renderer is not a proxy for navigation performance.
          </p>

          <h2>Phase III: Exploration Was the Real Bottleneck</h2>
          <p>
            75% of target objects were not visible from the drone&apos;s spawn position. Detection quality was
            irrelevant until visibility was established. A learned exploration policy achieved 6.9% validation
            accuracy versus 8.3% random chance — it learned the wrong thing. A depth-based heuristic produced
            +5.2 pp improvement, p=0.168. Not significant.
          </p>
          <p>
            Across all configurations, success rate converged to 23–25%. The tasks that produced 0% success
            shared one property: they required reasoning, not perception. Spatial relational queries, negation,
            multi-step planning, and occluded target inference are architectural limitations, not data limitations.
          </p>

          <h2>What Actually Worked</h2>
          <p>
            At n=4 drones cooperatively, the modular pipeline achieved 70–72.5% success versus 5% for zero-shot
            VLM baseline (p=0.002). Multi-agent coverage, not shared perception, is the source of that gain.
            That result is robust and does not depend on the single-agent ceiling being solved.
          </p>

          <h2>What We Would Do Differently</h2>
          <ul>
            <li>Validate synthetic data in the evaluation environment, not a different one.</li>
            <li>Run closed-loop evaluation early and often — it is the only number that is real.</li>
            <li>Profile your benchmark before optimizing perception: if 75% of targets are invisible from spawn,
              improving detector mAP by 10× will not move your success rate.</li>
          </ul>
          <p>
            Full technical detail: <Link href="/research#engineering-separation">research documentation</Link>.
            Yonder dataset: <Link href="/datasets/yonder">astral.us/datasets/yonder</Link>.
          </p>
        </Prose>
      );

    case "drone-swarm-sensing-1000-drones":
      return (
        <Prose>
          <p>
            Scale a drone swarm from 32 to 1,000 units and something breaks. Not gradually — it breaks hard.
            Camera-only coordination falls 15.8 percentage points below the omniscient baseline at 1,000 drones
            and produces <strong>8× more collisions</strong>: 3,620 versus 452 in the same mission. That is not
            a tuning problem. It is an architectural one. UWB ranging is non-negotiable above roughly 100 drones.
          </p>

          <h2>Four Sensing Configurations</h2>
          <ul>
            <li><strong>Camera-only:</strong> onboard vision for neighbor detection and obstacle avoidance.</li>
            <li><strong>UWB-only:</strong> ultra-wideband radio ranging — precise distances, independent of lighting or occlusion.</li>
            <li><strong>Hybrid (camera + UWB):</strong> both modalities combined.</li>
            <li><strong>Omniscient baseline:</strong> perfect ground-truth position knowledge — the ceiling.</li>
          </ul>
          <p>
            At 32 drones the differences are negligible. Camera-only achieves 57.5% coverage, nearly matching the
            57.6% omniscient ceiling. This is the regime where most drone swarm demos live. The problem is not
            visible yet.
          </p>
          <p>
            By 200 drones, camera-only has opened a 19.2 percentage-point gap (40.0% vs. 59.2%, p&lt;0.001). UWB
            and Hybrid stay within 1–2 pp of omniscient at every scale. The separation is structural: cameras cannot
            maintain reliable neighbor awareness when mutual occlusion becomes the norm. UWB ranges through everything.
          </p>

          <h2>The Urban Penalty</h2>
          <p>
            Urban environments impose a 14–32 percentage-point coverage penalty versus open forest across all swarm
            sizes and sensing configurations. Two effects compound: visual range is effectively halved (30 m → 15 m
            in city clutter), and 22% of the map is physically occupied by buildings — coverage saturates at ~28%
            regardless of mission time.
          </p>
          <p>
            At 1,000 drones, city environments produce 8–11× more collisions than forest under identical sensing.
            Camera-only in a city at scale is not just suboptimal — it is operationally unacceptable.
          </p>

          <h2>Hybrid Squad Coordination: Centralized Performance at 3.3× Less Overhead</h2>
          <p>
            In 16-drone, 8-target experiments:
          </p>
          <ul>
            <li><strong>Centralized:</strong> 95.0±6.1% detection, 3.15 s reaction, 192 msg/s.</li>
            <li><strong>Decentralized:</strong> 85.0±5.0% detection, 0.44 s reaction.</li>
            <li><strong>Hybrid squad-based:</strong> 95.0±6.1% detection (matching centralized), 1.08 s reaction, 58 msg/s — 3.3× less communication overhead.</li>
          </ul>
          <p>
            For a 1,000-drone fleet: centralized at 192 msg/s per node generates ~192,000 messages/s fleet-wide;
            hybrid brings that to ~58,000 msg/s. At 200 bytes per message, that is the difference between 38 Mbps
            and 11 Mbps of coordination traffic before any payload data.
          </p>

          <h2>Resilience</h2>
          <p>
            <strong>GPS denial:</strong> with UWB present, coverage drops only 1.3 pp in city. Fleets carrying
            only GPS lose this fallback entirely.
          </p>
          <p>
            <strong>Communications denial:</strong> any architecture that relies on shared state collapses to
            camera-only performance. UWB hardware provides no benefit when the ranging data cannot be shared.
          </p>
          <p>
            <strong>75% drone attrition:</strong> forest coverage holds at 87.7%; city drops to 63.8%.
            Urban operations require 30–40% more drones to maintain equivalent resilience under attrition.
          </p>

          <h2>VLMs on Edge Hardware</h2>
          <p>
            SmolVLM-2B on Orin Nano (210 ms latency, 5.06 GB VRAM) provides a 25 pp detection advantage under total
            comms denial in forest (62.5% vs. 37.5%, p&lt;0.05). In city environments, this advantage disappears:
            building occlusion prevents the scene context VLM reasoning depends on. Spend those resources on UWB
            hardware and better mesh radio for urban operations.
          </p>

          <h2>Requirements Summary</h2>
          <p><strong>100-drone fleet:</strong> UWB on every airframe (~$20–50/unit), hybrid squad organization
            (squads of 8–16), mesh radio for 58+ msg/s per drone, GPS primary / UWB fallback.</p>
          <p><strong>1,000-drone fleet:</strong> UWB without exception, tiered coordination hierarchy (squad →
            sector → global), 30–40% reserve capacity for urban attrition, comms-denial firmware on every unit.
          </p>
          <p>
            Full methodology and per-configuration data: <Link href="/research#scaling-separation">Scaling the
            Separation Principle</Link>.
          </p>
        </Prose>
      );

    case "counter-uas-drone-attack-defense-simulation":
      return (
        <Prose>
          <p>
            None of the attacks worked — not the way you&apos;d expect.
          </p>
          <p>
            Over several weeks, we ran 11,340 simulation trials against our autonomous warehouse-search drone stack.
            We hit it with GNSS spoofing, RF jamming, proportional-navigation interceptors, and direct control
            takeover. Varied seeds, tasks, defense configurations. Tracked every trial.
          </p>
          <p>
            The headline result: <strong>no attack degraded mission success rate beyond the 95% confidence
            interval.</strong> One attack even inflated it. If you stopped reading here, you&apos;d conclude our
            drones are basically impervious to adversarial interference. That conclusion would be wrong — and the
            gap between what the numbers say and what they mean is exactly why we&apos;re writing this.
          </p>

          <h2>Mission Success Rate Is the Wrong Metric for C-UAS</h2>
          <p>
            This is the central finding. Every attack profile we tested returned a mission success rate within
            the confidence interval of baseline (57.6% ±6.7 pp). GNSS walk-off: +1.2 pp. RF jamming: 0.0 pp.
            PN interceptor: 0.0 pp.
          </p>
          <p>
            Control takeover <em>improved</em> success rate by +8.1 pp. The attacker&apos;s redirect goal happened
            to sit closer to the mission target in ~8% of trials. The drone got hijacked and accidentally completed
            more tasks.
          </p>
          <p>
            None of this means the attacks did nothing. It means mission success rate doesn&apos;t capture what
            C-UAS threats actually do. The right metrics are physical: capture percentage, maximum position error,
            detection latency, minimum miss distance.
          </p>

          <h2>What the Four Attack Types Actually Do</h2>
          <h3>GNSS Walk-Off</h3>
          <p>
            Smooth-capture spoofing: position blends from true toward attacker-controlled offset over 5 seconds.
            Velocity is left untouched — preserving the IMU signal that enables dead-reckoning detection. Physical
            result: <strong>5.14 m mean maximum position error</strong> under single-axis walk-off.
            Combined with RF jamming: <strong>7.95 m</strong>.
          </p>
          <h3>RF Jamming</h3>
          <p>
            Packet loss (0.5–0.9) and latency (0.2–0.8 s) injected into the communication channel. Produces no
            measurable change in single-drone mission success. The jam failsafe — RTL on 3 s of neighbor silence —
            returns 0.0% false positives on clean baseline.
          </p>
          <h3>Control Takeover</h3>
          <p>
            Direct override of <code>action.target_position</code>. The goal-bounds defense (reject targets
            outside ±30 m geofence) stops this completely. False positives on clean baseline: 0.0%.
          </p>

          <h2>Why PN Interceptors Are the Real Threat</h2>
          <p>
            Proportional-navigation guidance (N=3.5, v_max=8 m/s, net capture radius 1.5 m) achieves a{" "}
            <strong>79.5% capture rate</strong> with no defense active. Minimum miss distance: 0.11 m.
            When RF jamming is layered on: capture rate still 79.5%. Physical interception geometry is not
            sensitive to communication degradation.
          </p>
          <p>
            This is the metric that matters. A 79.5% probability your drone gets physically removed from the
            air in a single engagement is operationally decisive. No mission success rate figure captures that.
          </p>

          <h2>Defenses: What Works</h2>
          <p>
            <strong>Plausibility detection:</strong> dead-reckoning vs. GPS position; flag when divergence &gt;3 m.
            Under single walk-off: <strong>39.8% TP</strong>. Under combined GNSS+RF jam: <strong>79.5% TP</strong>
            (the jam forces faster spoofing, making detection easier). False positives on clean baseline:{" "}
            <strong>0.0%</strong>.
          </p>
          <p>
            Critical implementation lesson: the DR integrator must use elapsed wall time between controller calls
            (2 s), not the physics timestep (0.05 s). Using 0.05 s produced a <strong>75.8% false-positive
            rate</strong>. This is an easy bug to introduce in any system where physics dt differs from control rate.
          </p>
          <p>
            <strong>All defenses stacked:</strong> 0.0% FP on clean baseline, +1.9 pp mission success overhead.
            Properly tuned defenses impose no operational cost.
          </p>

          <h2>The Fidelity Boundary</h2>
          <p>
            <strong>Reliably testable in kinematic sim:</strong> GNSS position error, PN capture geometry, control
            takeover, jam failsafe, plausibility detection, goal-bounds enforcement.
          </p>
          <p>
            <strong>Not testable without hardware or RF simulation:</strong> carrier-phase GPS, RAIM, Galileo OSNMA
            authentication (operational since July 2025), barrage vs. reactive jamming physics, EKF lock-pull,
            camera/radar interception detection, aerodynamic evasion effectiveness.
          </p>
          <p>
            The evasion defense triggered at 0% — not because evasion is ineffective, but because interceptors are
            not inserted into the drone&apos;s onboard sensor list in the current harness. That is a simulation
            fidelity gap, not a finding about evasion.
          </p>

          <h2>Practical Takeaways</h2>
          <ul>
            <li>Don&apos;t use mission success rate as your primary C-UAS metric. Track capture rate and position error separately.</li>
            <li>PN interceptors are your highest-priority threat — 79.5% capture against non-maneuvering drones.</li>
            <li>Plausibility detection is deployable today at 0% FP if you use the right clock.</li>
            <li>Goal-bounds enforcement stops control takeover with zero performance cost.</li>
            <li>Jam failsafe (RTL on link silence) should be standard firmware on every drone.</li>
            <li>Update your GNSS threat model for OSNMA — capable adversaries now layer RF denial on top of spoofing.</li>
          </ul>
          <p>
            Full paper with methodology and all tables: <Link href="/research#counter-uas">research: C-UAS study</Link>.
          </p>
        </Prose>
      );

    case "human-in-loop-drone-autonomy-94-percent":
      return (
        <Prose>
          <p>
            In a recent series of experiments across 28 test configurations, we measured something drone autonomy
            researchers rarely publish plainly: the gap between what an AI system can do alone and what it can do
            with a human in the loop. For autonomous drone operations, that gap was 36.8 percentage points — the
            difference between 57.6% and 94.4% mission success rate.
          </p>
          <p>
            That number deserves unpacking. Not because it argues against autonomy — it doesn&apos;t — but because
            it tells you exactly where to put the human.
          </p>

          <h2>The Experiment: Human-in-the-Loop Split (S14)</h2>
          <p>
            36 trials comparing a standalone LLM coordinator against a human-in-the-loop split coordinator that
            retained the LLM for planning and execution but escalated ambiguous decisions to a human operator:
          </p>
          <ul>
            <li><strong>Autonomous LLM:</strong> 16.7% success rate, 0.0% all-target completion</li>
            <li><strong>HIL split:</strong> 94.4% success rate, 61.1% all-target completion, 7.261 s wall time</li>
          </ul>
          <p>
            The all-target metric requires completing every objective in a multi-target task. The jump from 0% to
            61.1% shows the autonomous system was failing at task planning in uncertain scenarios — not just being
            slow or imprecise. Human judgment fixed that structurally.
          </p>
          <p>
            The HIL coordinator did not give the human continuous control. It surfaced decisions at branch points
            where sensor data was ambiguous or model confidence dropped below threshold. Total human engagement
            per trial was measured in seconds, not minutes.
          </p>

          <h2>When Full Autonomy Works: Rover Operations (S16)</h2>
          <p>
            Ground vehicle navigation on known terrain: 100% success, 100% all-target, 1.976 m mean error, 0.427 s
            wall time. No human in the loop. Perfect performance.
          </p>
          <p>
            The difference from S14 is task structure, not model quality. Constrained 2D navigation in a known
            environment matches the LLM&apos;s training distribution. Aerial swarm coordination in uncertain
            environments does not. The right level of human oversight is determined by task novelty and
            environmental ambiguity, not by a global policy.
          </p>

          <h2>When AI Teams Outperform: Team Training (S19)</h2>
          <p>
            Fine-tuned team-level models in 45 trials:
          </p>
          <ul>
            <li><strong>sector_search_coordinator, 3 drones:</strong> 100% SR, 2.790 m, 0.712 s</li>
            <li><strong>parallel_llm_coordinator, 3 drones:</strong> 100% SR, 2.811 m, 0.240 s</li>
            <li><strong>Gemma (smaller model):</strong> 20% SR, 15.7 s latency</li>
          </ul>
          <p>
            The near-identical accuracy at 3× speed difference (0.712 s vs. 0.240 s) shows parallel inference as
            an architectural choice, not an optimization. The Gemma comparison is the warning: deploying an
            undersized model on cost grounds produces a 5× latency penalty and 80% failure rate on complex tasks.
          </p>
          <p>
            S19 also shows that team training closes the gap that required HIL in S14. When the model has been
            trained on the distribution of problems it will encounter, it doesn&apos;t need the human at the branch
            point anymore. Same task class, different model preparation, 83.3 pp difference in success rate.
          </p>

          <h2>Parallel Inference as Architecture (S20)</h2>
          <p>
            45 trials isolating parallel inference vs. sequential:
          </p>
          <ul>
            <li><strong>Sequential:</strong> 40% SR, 4.483 m, 5.573 s</li>
            <li><strong>Parallel n=2:</strong> 100% SR, 1.890 m, 1.610 s</li>
            <li><strong>Parallel n=3:</strong> 100% SR, 1.849 m, 0.392 s</li>
          </ul>
          <p>
            14× speedup from sequential to n=3 parallel, with accuracy improving by half. Sequential and parallel
            coordination are not on the same performance curve — they are in different capability tiers. Sub-400 ms
            end-to-end inference enables dynamic obstacle response and live target tracking. 5.5 s does not.
          </p>

          <h2>When to Trust the Drone, When to Keep a Human</h2>
          <p>
            <strong>Full autonomy is appropriate when:</strong> the task is routine and well-characterized; the
            model has been fine-tuned on that specific task class; failure modes are bounded and recoverable;
            you have empirical success rate data from real trials.
          </p>
          <p>
            <strong>Human oversight is non-negotiable when:</strong> conditions are novel or sensors are returning
            ambiguous readings; multi-target completion is required in uncertain environments; you are operating
            at the edge of the training distribution; consequences of failure are asymmetric.
          </p>
          <p>
            The transition should be dynamic, not static. A system that applies fixed human-oversight policy will
            either over-rely on humans for routine tasks or under-rely on them for novel ones. The architecture
            that works surfaces only the decisions that need human judgment — briefly, at the right moment.
          </p>
          <p>
            The 94.4% number is real. So is the 0% all-target success rate for the baseline autonomous system on
            the same tasks. Both numbers are from the same experimental series. Together they define the design
            space.
          </p>
          <p>
            Full methodology: <Link href="/research">research section</Link>. HIL and parallel inference
            implementation guidance: <Link href="/docs">documentation</Link>.
          </p>
        </Prose>
      );

    case "droneport-atc-tower-vs-selforg":
      return (
        <Prose>
          <p>
            Every urban air mobility operator faces the same question before they build their first vertiport: do you put a centralized tower in charge, or let drones negotiate landing slots peer-to-peer? The answer determines infrastructure cost, safety margins, and how the system degrades when traffic spikes.
          </p>
          <p>
            We ran a nine-cell factorial study to find out. Three factors, nine combinations, 30 seeds per cell across five traffic loads. The headline: <strong>self-organized coordination with ADS-B matches tower throughput below roughly 20–25 ops/hour — then falls behind sharply as load increases.</strong> And drones flying silent (no ADS-B, no tower) exceed safe separation thresholds at just 12 ops/hour.
          </p>
          <h2>The three factors</h2>
          <p>
            The study crosses coordination <strong>authority</strong> (centralized tower vs. fully decentralized self-organization), <strong>communications regime</strong> (continuous telemetry vs. terminal-only — silent during cruise), and <strong>observation modality</strong> (ADS-B cooperative broadcast, camera-only, sensor fusion, or nothing). Nine of the possible sixteen combinations are operationally meaningful; those are the cells we ran.
          </p>
          <p>
            E2 — tower, continuous, ADS-B — is the control. E9 — self-organized, terminal-only, no broadcast — is the stress floor. Everything else lives between them on the throughput–safety Pareto frontier.
          </p>
          <h2>Reference cell: tower + ADS-B + continuous</h2>
          <p>
            The E2 reference cell at light load (5 ops/hour) achieves <strong>140 ± 28 ops/hour completed throughput, zero loss-of-separation events, and 12.6 m minimum pairwise separation</strong> at 0.078 messages/second. The tower consistently clears arriving drones before any queuing builds. This is the ceiling.
          </p>
          <h2>Where self-organization works (and where it doesn't)</h2>
          <p>
            Self-organized coordination with ADS-B (E6) matches E2 at light load. Drones broadcast intent, negotiate pad claims by priority, and clear the holding circuit without central authority. The distributed protocol handles low-density traffic well.
          </p>
          <p>
            Above the crossover load (~20–25 ops/hour for a six-pad configuration), the gap opens. The tower's global slot optimizer prevents approach conflicts that the distributed back-off protocol can only resolve by delaying one drone — compounding under sustained load. Self-org without broadcast (E7, E9) is worse: without visibility of neighbors mid-route, separation events rise steeply past 12 ops/hour.
          </p>
          <h2>The silent-cruise trap</h2>
          <p>
            Terminal-only communications (drones broadcast only in the approach and departure zones, silent during cruise) is attractive for bandwidth. At 5 ops/hour the cost is small. At 40 ops/hour, the absence of mid-air deconfliction triggers measurable LoS increases. Self-org + terminal + no broadcast (E9) — the configuration that costs the least to deploy — is also the most dangerous at commercial load targets.
          </p>
          <h2>What this means for vertiport design</h2>
          <p>
            The Pareto-efficient designs for high-density droneports all share two properties: centralized pad scheduling and cooperative surveillance (ADS-B or fusion). Self-organization is the right architecture for low-density corridors where infrastructure cost dominates. The crossover point — roughly 20 ops/hour for six pads — is the design threshold operators should use when choosing between the two architectures.
          </p>
          <p>
            The broadcast necessity threshold (LoS exceeds 0.01 events/op around 12 ops/hour in no-broadcast cells) maps directly to a regulatory question: at what traffic density does Remote ID / ADS-B become mandatory? This study argues the answer is well below commercial delivery density, which is where most UAM operators intend to operate.
          </p>
          <p>
            Full experimental design, metrics, and simulation methodology: <Link href="/research#droneport-atc">research section</Link>. The kinematic harness and all nine cells are available at the{" "}
            <a href={SITE.githubOrg} target="_blank" rel="noopener noreferrer">Astral GitHub</a>.
          </p>
        </Prose>
      );

    case "four-models-drone-autonomy":
      return (
        <Prose>
          <p>
            The domain detector was the first model. It answered one question:
            can the drone see the things that matter — other drones, people from
            altitude, landing pads — instead of the 80 COCO categories it was
            born knowing? The answer was yes, and{" "}
            <Link href="/blog/domain-detector-aerial-autonomy">
              we wrote about the class-collapse problem it exposed
            </Link>
            .
          </p>
          <p>
            After that we trained three more models in the same session. This
            post covers what they are, why we built each one, and how they all
            run together on a Jetson Orin Nano today.
          </p>

          <h2>Model 2 — VLM action-LoRA</h2>
          <p>
            The drone's reasoning layer uses a vision-language model to look at
            the camera feed, read the mission context, and emit a structured
            action: <em>move forward 3 m</em>, <em>photograph the target</em>,{" "}
            <em>land</em>. The stock Qwen3-VL-2B does this reasonably well but
            it was trained on the internet — not on drone missions. It
            occasionally emits verbs the SDK does not recognize, confuses rover
            commands with quadcopter commands, and hallucinates fields in the
            action JSON.
          </p>
          <p>
            We fine-tuned Qwen2.5-VL-3B-Instruct with LoRA (rank 16, applied to
            all attention projection matrices) on 2,000 drone-aerial training
            examples. The examples were generated deterministically: run the v3
            domain detector on VisDrone val images to get labeled detections,
            apply a set of 10 mission templates (survey area, approach target,
            orbit landmark, return to base, …), assign a canonical{" "}
            <code>VLMAction</code> response via priority rules, and write the
            image path + mission context + expected JSON response to a JSONL
            file.
          </p>
          <p>
            Training ran for 3 epochs on hoopoe (2× RTX 5090) with 4-bit
            BitsAndBytes quantization to fit the model in GPU memory. Final
            training loss: 0.31. Token accuracy on a held-out 200-example val
            split: 94.4%.
          </p>
          <p>
            The merged model was converted to GGUF Q4_K_M via llama.cpp:
            first a float16 GGUF from{" "}
            <code>convert_hf_to_gguf.py</code>, then quantized with{" "}
            <code>llama-quantize</code>. Output: 1.8 GB main model + 1.2 GB
            multimodal projector. Both slot directly into the existing
            on-device VLM path — the daemon loads whatever{" "}
            <code>vlm.gguf</code> and <code>vlm_mmproj.gguf</code> are present
            in the models directory.
          </p>

          <h2>Model 3 — Reactive policy MLP</h2>
          <p>
            The VLM makes good mission-level decisions but it runs at 1–3 Hz.
            At cruise speed a drone covers 1.5–3 m per inference cycle. That is
            fine for high-level waypoint selection but too slow for obstacle
            avoidance and altitude hold, which the rule-based reactive planner
            currently handles.
          </p>
          <p>
            The reactive planner is a set of explicit rules: brake if an
            obstacle is closer than 2 m forward, push away from the nearest
            surface, hold altitude within ±0.2 m, scale speed down as the goal
            distance drops below 1 m. These rules are correct and safe but they
            are not learnable — they cannot improve from experience and they do
            not generalize beyond the cases they enumerate.
          </p>
          <p>
            We trained a small MLP (two 128-unit hidden layers with LayerNorm,
            approximately 50 K parameters) to clone the planner. The input is a
            12-dimensional state vector: goal distance, goal bearing and
            elevation, current altitude, current velocity (3-axis), and nearest
            obstacle distance in five directions. The output is a 3-axis
            velocity command.
          </p>
          <p>
            We generated 200,000 synthetic state-action pairs by sampling random
            states and running the planner rules as a function — no simulation
            needed. 30 epochs of AdamW + cosine LR schedule. Best val MSE:
            0.0077. The model fits in 121 KB as an ONNX file, runs in under 1 ms
            on CPU, and can sustain 200 Hz on a Jetson Nano. The rule-based
            planner stays in as the safety fallback — the MLP is the fast path.
          </p>

          <h2>Model 4 — Monocular depth fine-tune</h2>
          <p>
            The quadcopter and rover use a stereo depth camera (Intel D435i /
            Luxonis OAK-D) for ranging. Stereo baseline works well up to about
            10–15 m. Beyond that — at range or on a fixed-wing without a stereo
            rig — depth is unavailable and the planner treats everything as
            unranged.
          </p>
          <p>
            We fine-tuned Depth Anything V2 Small on aerial imagery using
            self-distillation: run the zero-shot model on 500 VisDrone val
            images to generate pseudo-depth labels, then fine-tune on those
            labels. This is not ground-truth supervision but it adapts the
            model's internal representation to the overhead/oblique aerial
            viewpoint, where the zero-shot model tends to produce noisy estimates
            on flat textureless surfaces (roads, rooftops, open ground).
          </p>
          <p>
            10 epochs, scale-invariant SmoothL1 loss, best val loss 0.00072.
            The output is a 1.6 MB ONNX. Input: 518×518 RGB. Output: 518×518
            relative depth map. Metric scaling is done at runtime using the
            known flight altitude as a prior — the flight controller always knows
            its barometric altitude, which anchors the scale factor.
          </p>

          <h2>Deployment</h2>
          <p>
            All four models are running on both the rover (Jetson Orin Nano,{" "}
            <code>jetson@rover</code>) and the quadcopter (<code>astral@quadcopter</code>).
            The deployment sequence:
          </p>
          <ol>
            <li>
              Copy updated <code>daemon.py</code>, <code>perception.py</code>,
              and <code>setup_models.py</code> to <code>~/drone-api/</code> on
              each device via SCP.
            </li>
            <li>
              Run <code>python3 setup_models.py --domain-detector
              --reactive-policy --depth-model --vlm-drone</code> to pull the
              model files from S3.
            </li>
            <li>
              <code>sudo systemctl restart drone-api</code>. The daemon detects
              which models are present at startup and activates the corresponding
              capability flags.
            </li>
          </ol>
          <p>
            The service restart on both devices confirmed: domain detector
            loaded, reactive policy loaded, depth model loaded. The VLM GGUF
            download (3.2 GB total) runs in the background and activates on next
            restart.
          </p>

          <h2>Download</h2>
          <p>
            All models are publicly available on Hugging Face at{" "}
            <a href={SITE.droneModels}>astralhf/astral-drone-models</a>:
          </p>
          <ul>
            <li>
              <a href={`${SITE.droneModels}/resolve/main/yolov8n_domain_v3.onnx`}>
                yolov8n_domain_v3.onnx
              </a>{" "}
              — 11.7 MB · YOLOv8n 9-class aerial detector
            </li>
            <li>
              <a href={`${SITE.droneModels}/resolve/main/vlm_lora_v1_q4km.gguf`}>
                vlm_lora_v1_q4km.gguf
              </a>{" "}
              — 1.8 GB · Qwen2.5-VL-3B action-LoRA, GGUF Q4_K_M
            </li>
            <li>
              <a href={`${SITE.droneModels}/resolve/main/vlm_lora_v1_mmproj.gguf`}>
                vlm_lora_v1_mmproj.gguf
              </a>{" "}
              — 1.2 GB · multimodal projector (required with vlm_lora_v1_q4km.gguf)
            </li>
            <li>
              <a href={`${SITE.droneModels}/resolve/main/policy_v1.onnx`}>
                policy_v1.onnx
              </a>{" "}
              +{" "}
              <a href={`${SITE.droneModels}/resolve/main/policy_v1.onnx.data`}>
                policy_v1.onnx.data
              </a>{" "}
              — 121 KB · reactive policy MLP
            </li>
            <li>
              <a href={`${SITE.droneModels}/resolve/main/depth_v1.onnx`}>
                depth_v1.onnx
              </a>{" "}
              — 1.6 MB · Depth Anything V2 Small, aerial fine-tune
            </li>
          </ul>
          <p>
            Load the state normalization for the reactive policy with the paired{" "}
            <a href={`${SITE.droneModels}/resolve/main/policy_v1_state_norm.npy`}>
              policy_v1_state_norm.npy
            </a>{" "}
            (224 B, NumPy array of shape [2, 12] — row 0 is mean, row 1 is std).
          </p>
        </Prose>
      );

    case "domain-detector-aerial-autonomy":
      return (
        <Prose>
          <p>
            COCO-80 has no class for drone. No class for person_aerial. No class
            for landing_pad or powerline. That is not a gap we can paper over with
            prompt engineering — it is a hard limit of the label set the weights
            were trained on. So we replaced it.
          </p>
          <p>
            Over three training rounds we built a 9-class domain detector
            (drone, person_aerial, vehicle, bicycle_motorcycle, landing_pad,
            powerline_pole, person, animal, boat) tuned for the aerial autonomy
            use case. We trained on 48,000 images, used{" "}
            <a href="https://github.com/ultralytics/ultralytics" target="_blank" rel="noopener noreferrer">
              YOLOv8n
            </a>{" "}
            as the base, and learned something important along the way: the
            biggest failure mode is not the architecture, the learning rate, or
            the number of epochs. It is class imbalance. One of our classes
            collapsed to near-zero during training because we did not catch it.
          </p>
          <h2>The setup: why aerial perception is different</h2>
          <p>
            A drone camera sees the world at angles and scales COCO was not
            designed for. People become small blobs at 30 m altitude. Vehicles
            look like rectangles with no distinguishing features. A drone seen
            by another drone is a 15-pixel smear. Standard ImageNet-pretrained
            detectors handle the common cases (full-frame people, large cars from
            the side) but fail on the overhead/oblique aerial regime.
          </p>
          <p>
            We needed a detector that could fire on exactly those cases: distant
            people from altitude, vehicles from above, and — critically — other
            drones in the frame for counter-UAS and deconfliction.
          </p>
          <h2>Round 1: 18,000 sim frames from Isaac</h2>
          <p>
            We started with pure simulation data. Using NVIDIA Isaac Sim we
            generated 18,000 labeled frames across three indoor environments
            (office, warehouse, hospital), scripting drone trajectories and
            projecting known object poses through the camera model to produce
            perfect bounding boxes with no human labeling.
          </p>
          <p>
            The results looked great: mAP50 = 0.471, vehicle AP50 = 0.895,
            person AP50 = 0.894. We ran at 55 FPS on an RTX 5090 (18 ms per
            frame) with an ONNX-exported model. We were cautious about the
            numbers — sim val is easy; there is no sensor noise, no compression
            artifacts, no lighting variation beyond what we scripted.
          </p>
          <p>
            But person_aerial AP50 was 0.047 and drone AP50 was 0.047. The sim
            scenes did not have enough examples of those two classes at aerial
            angles to learn them well.
          </p>
          <figure className="my-8">
            <img src="/media/detector/demo_sim_hospital.jpg" alt="v1 detector running on a sim hospital scene" className="w-full rounded-lg" />
            <figcaption className="mt-2 text-sm text-center text-muted-foreground">v1 running on a sim hospital frame — vehicle and drone detected, no person_aerial</figcaption>
          </figure>
          <h2>Round 2: 343,000 free labels from VisDrone — and the collapse</h2>
          <p>
            <a href="https://github.com/VisDrone/VisDrone-Dataset" target="_blank" rel="noopener noreferrer">
              VisDrone2019-DET
            </a>{" "}
            is a public aerial-footage dataset with 6,471 train and 548 val
            images, 382,000 annotated boxes shot from commercial drones over
            urban scenes. Person_aerial, vehicle, bicycle, motorcycle — all
            labeled. Free real-world distribution. We merged it in.
          </p>
          <p>
            Our merged training set grew to 24,500 images and 364,000 boxes.
            Person_aerial jumped from 0.047 to 0.377 AP50. Vehicle held at
            0.755. Bicycle_motorcycle appeared at 0.365. These were genuinely
            impressive gains.
          </p>
          <figure className="my-8">
            <img src="/media/detector/demo_vd_dense_traffic.jpg" alt="v3 detector on dense VisDrone traffic scene" className="w-full rounded-lg" />
            <figcaption className="mt-2 text-sm text-center text-muted-foreground">v3 detector on a dense VisDrone traffic scene — person_aerial, vehicle, and bicycle_motorcycle all firing correctly</figcaption>
          </figure>
          <p>
            Drone AP50 dropped from 0.047 to 0.010. The class had not just
            stagnated — it had nearly vanished.
          </p>
          <h2>The diagnosis: 3.5% is not enough</h2>
          <p>
            VisDrone has no drone-as-target labels. The dataset is shot by
            drones looking down at streets; there are no other drones in the
            frame. When we merged our 12,000 drone boxes from sim into a
            343,000-box VisDrone dataset, drones became 3.5% of the total box
            count. The model learned to ignore the class.
          </p>
          <p>
            The fix was oversampling. We duplicated every training image
            containing a drone box 4× so that drone-containing images accounted
            for roughly 10% of training samples. The training set grew from
            21,471 to 48,717 images.
          </p>
          <figure className="my-8">
            <img src="/media/detector/v3_training_curves.png" alt="v3 training curves" className="w-full rounded-lg" />
            <figcaption className="mt-2 text-sm text-center text-muted-foreground">v3 training curves — mAP50 on VisDrone val converging after 4× drone oversampling across 50 epochs</figcaption>
          </figure>
          <h2>Round 3: the oversampled result</h2>
          <p>
            mAP50 on the real-world VisDrone val set: 0.384. Person_aerial: 0.360.
            Vehicle: 0.752. Bicycle_motorcycle: 0.339. Drone: 0.087.
          </p>
          <p>
            That 0.087 drone AP50 needs context. The VisDrone val set has zero
            drone-as-target images; every drone detection at validation time
            comes from our sim val frames only. So 0.087 is precision when the
            model fires on drone boxes — not coverage across a large val set. It
            is meaningful: the v2 model had precision near zero. The v3 model
            fires correctly when it sees a drone.
          </p>
          <h2>What the numbers actually mean</h2>
          <p>
            The headline mAP50 trajectory — 0.471 (v1) → 0.376 (v2) → 0.384
            (v3) — looks like a small net improvement from pure sim to merged
            training. It obscures the real story:
          </p>
          <ul>
            <li>
              <strong>V1&apos;s 0.471 was on an easy sim-only val set.</strong> The
              model had never seen a real image. Those numbers do not transfer.
            </li>
            <li>
              <strong>V2&apos;s 0.376 is on a hard real-world val set</strong> with
              38,000 boxes from actual drone footage. That is a genuine gain,
              despite looking like a regression in absolute mAP.
            </li>
            <li>
              <strong>V3&apos;s 0.384 adds drone recovery</strong> without sacrificing
              the real-world aerial classes. We got drone back without breaking
              person_aerial or vehicle.
            </li>
          </ul>
          <figure className="my-8">
            <img src="/media/detector/demo_vd_street_intersection.jpg" alt="v3 detections on VisDrone street intersection" className="w-full rounded-lg" />
            <figcaption className="mt-2 text-sm text-center text-muted-foreground">v3 on a VisDrone street intersection — vehicles and person_aerial labeled with confidence scores</figcaption>
          </figure>
          <h2>Three things we learned</h2>
          <p>
            <strong>Sim mAP lies at small sample counts.</strong> A 0.471 on
            18,000 sim frames with a clean sim val set is not a 0.471 in the
            wild. Evaluate on out-of-distribution data before claiming any
            number.
          </p>
          <p>
            <strong>Free labels are worth the class-imbalance fight.</strong>{" "}
            VisDrone gave us 343,000 aerial-domain boxes at zero labeling cost.
            The collapse it caused was real but diagnosable and fixable. The
            net result — a model that has seen real aerial footage — is
            dramatically better than sim-only for the classes VisDrone covers.
          </p>
          <p>
            <strong>Class imbalance at 3.5% is a hard floor.</strong> We do not
            know exactly where the threshold is, but below about 5% of boxes the
            model treats a class as noise. The fix — oversampling the
            underrepresented images — is simple and effective. Future work: add
            synthetic augmentation (mosaic, copy-paste) specifically for the rare
            aerial classes (landing_pad, powerline_pole, animal, boat) which
            currently have zero training data.
          </p>
          <h2>Where the model runs</h2>
          <p>
            The ONNX is 12 MB. It runs at 55 FPS on an RTX 5090 and is
            deployed to Jetson Orin Nano hardware (rover and quadcopter) where
            it replaces the stock COCO-80 model in the perception pipeline.
            The model is loaded automatically by the perception layer — no
            inference code changes required.
          </p>
          <h2>Download</h2>
          <p>
            The ONNX model is on Hugging Face at{" "}
            <a href={SITE.droneModels}>astralhf/astral-drone-models</a>:
          </p>
          <ul>
            <li>
              <a href={`${SITE.droneModels}/resolve/main/yolov8n_domain_v3.onnx`}>
                yolov8n_domain_v3.onnx
              </a>{" "}
              — 11.7 MB, YOLOv8n, 9-class aerial detector, ONNX opset 17
            </li>
          </ul>
          <p>
            Input: 640×640 RGB, normalized to ImageNet mean/std. Output: standard
            YOLOv8 detection head (xywh + conf + 9-class logits). Classes in
            order: drone, person_aerial, vehicle, bicycle_motorcycle,
            landing_pad, powerline_pole, person, animal, boat.
          </p>
          <p>
            Technical report with all metrics, training curves, and confusion
            matrices:{" "}
            <Link href="/blog/domain-detector-aerial-autonomy-paper">
              Domain-Specific Object Detection for Aerial Autonomy
            </Link>
            .
          </p>
        </Prose>
      );

    case "domain-detector-aerial-autonomy-paper":
      return (
        <Prose>
          <p>
            <strong>Abstract.</strong> We present a domain-specific object
            detector for aerial autonomy applications, trained on a combined
            corpus of simulation-generated frames and the VisDrone2019-DET
            public dataset. Starting from a YOLOv8n base, we define a 9-class
            schema suited to autonomous drone operations (drone, person_aerial,
            vehicle, bicycle_motorcycle, landing_pad, powerline_pole, person,
            animal, boat) and evaluate three successive training configurations.
            The sim-only baseline (v1, 18K frames) reaches mAP50 = 0.471 on a
            held-out simulation val set. Merging VisDrone real-world footage (v2,
            21K images, 364K boxes) improves real-domain person_aerial from 0.047
            to 0.377 but causes near-complete drone class collapse (AP50 0.047 →
            0.010) due to a 3.5% box-fraction imbalance. A 4× oversampling of
            drone-containing images (v3, 49K images) recovers drone AP50 to 0.087
            while maintaining real-world gains: mAP50 = 0.384, person_aerial =
            0.360, vehicle = 0.752, bicycle_motorcycle = 0.339. The primary
            finding is that class imbalance — not architecture or augmentation —
            is the dominant failure mode for rare aerial classes in mixed-corpus
            training.
          </p>

          <h2>1. Introduction</h2>
          <p>
            Standard object detection models trained on COCO-80 perform well on
            common ground-level objects but fail systematically in the aerial
            autonomy domain for three reasons: (1) the label set has no drone,
            person_aerial, landing_pad, or powerline class; (2) COCO training
            images are primarily ground-level with standard aspect ratios, while
            drone cameras produce oblique and overhead views at varying altitudes;
            and (3) the objects of primary interest in aerial operations —
            distant targets, thin structures, other aircraft — are consistently
            underrepresented in internet-scale training corpora.
          </p>
          <p>
            We address this by fine-tuning YOLOv8n on a domain-specific corpus
            assembled from two sources: (i) Isaac Sim procedural simulation with
            camera-model ground truth projection, providing perfect labels at low
            cost, and (ii) VisDrone2019-DET, a large-scale public real-world
            aerial dataset providing aerial-perspective vehicle and pedestrian
            boxes. The goal is a detector that runs on-device at 55+ FPS on
            an RTX-class GPU and, after TensorRT int8 export, at viable frame
            rates on the Jetson Orin Nano edge platform.
          </p>

          <h2>2. Related work</h2>
          <p>
            <strong>Aerial object detection.</strong> VisDrone{" "}
            <a href="https://github.com/VisDrone/VisDrone-Dataset" target="_blank" rel="noopener noreferrer">
              (Zhu et al., 2018)
            </a>{" "}
            established a large-scale benchmark for drone-captured footage and
            demonstrated that ImageNet-pretrained detectors transfer poorly to
            the aerial regime. DOTA and xView extend coverage to satellite and
            high-altitude platforms. Anti-UAV and DroneVehicle datasets target
            the drone-as-target detection problem specifically.
          </p>
          <p>
            <strong>Sim-to-real for perception.</strong> NVIDIA Isaac Sim and
            similar photorealistic simulators have been used for generating
            labeled training data when real-world annotation is expensive.
            Domain randomization (DR) over lighting, texture, and object
            placement is standard practice. Our setup uses a fixed scene set
            without DR in v1 and v2; domain gap from sim to VisDrone is the
            expected cost.
          </p>
          <p>
            <strong>Class imbalance in detection.</strong> Class imbalance is
            a known problem in detection training. Focal loss, class-balanced
            sampling, and copy-paste augmentation are common mitigations.
            We apply the simplest effective remedy — image-level oversampling —
            and find it sufficient to recover a collapsed class.
          </p>

          <h2>3. Class schema</h2>
          <p>The 9-class schema and VisDrone mapping:</p>
          <ul>
            <li>
              <strong>0 — drone</strong>: unmanned aircraft in frame (sim only; absent from VisDrone)
            </li>
            <li>
              <strong>1 — person_aerial</strong>: person seen from above (VisDrone: pedestrian, people)
            </li>
            <li>
              <strong>2 — vehicle</strong>: car, van, truck, bus (VisDrone: car, van, truck, bus, tricycle)
            </li>
            <li>
              <strong>3 — bicycle_motorcycle</strong>: two-wheelers (VisDrone: bicycle, motor)
            </li>
            <li>
              <strong>4 — landing_pad</strong>: ground marker (sim only; no public label source)
            </li>
            <li>
              <strong>5 — powerline_pole</strong>: thin vertical / linear structure (sim only)
            </li>
            <li>
              <strong>6 — person</strong>: person at ground level / normal angle (sim only)
            </li>
            <li>
              <strong>7 — animal</strong>: generic animal (no training data in v1–v3)
            </li>
            <li>
              <strong>8 — boat</strong>: watercraft (no training data in v1–v3)
            </li>
          </ul>
          <p>
            Classes 4–8 have no VisDrone source and limited or zero sim coverage.
            They are present in the schema for forward compatibility but produce
            no meaningful AP in current models.
          </p>

          <h2>4. Datasets</h2>
          <h3>4.1 Simulation data (v1 corpus)</h3>
          <p>
            We drove NVIDIA Isaac Sim headlessly over three scene types (office,
            warehouse, hospital) with scripted randomized drone trajectories.
            Per-frame ground truth was produced by projecting known world-space
            object poses through the camera intrinsic model to COCO-format
            bounding boxes, giving perfect labels at zero human annotation cost.
          </p>
          <ul>
            <li>Train: 16,200 frames, 10,800 boxes</li>
            <li>Val: 1,800 frames, 1,200 boxes</li>
            <li>Box distribution: vehicle (class 2) ~60%, drone (class 0) ~40%</li>
            <li>Person / person_aerial: near-zero (scenes lacked overhead person views)</li>
          </ul>

          <h3>4.2 VisDrone2019-DET</h3>
          <p>
            VisDrone is a large-scale benchmark of drone-captured real-world
            footage over urban areas. We use the DET (detection) split:
          </p>
          <ul>
            <li>Train: 6,471 images, 343,204 boxes</li>
            <li>Val: 548 images, 38,759 boxes</li>
            <li>
              Classes mapped to schema: pedestrian → person_aerial (1),
              people → person_aerial (1), car/van/truck/bus/tricycle → vehicle (2),
              bicycle → bicycle_motorcycle (3), motor → bicycle_motorcycle (3)
            </li>
            <li>Ignored: awning-tricycle, others (no schema mapping)</li>
          </ul>
          <p>
            VisDrone contains no drone-as-target boxes. All drone class data
            in the merged corpus comes from simulation.
          </p>

          <h2>5. Training configuration</h2>
          <p>
            All runs: YOLOv8n base, Ultralytics 8.4.66, batch=64, 50 epochs,
            imgsz=640, AdamW optimizer, 2× RTX 5090 (hoopoe GPU server).
            No domain randomization, no mosaic augmentation for rare classes.
            ONNX export: 12 MB, FP32, opset 11.
          </p>
          <ul>
            <li>
              <strong>V1:</strong> sim-only corpus (18K images). Val: sim val set.
            </li>
            <li>
              <strong>V2:</strong> sim + VisDrone (21,471 images, 364K boxes). Val: VisDrone val set.
            </li>
            <li>
              <strong>V3:</strong> sim + VisDrone + 4× oversampled drone images
              (48,717 images). Val: VisDrone val set.
            </li>
          </ul>

          <h2>6. Results</h2>
          <figure className="my-8 grid grid-cols-3 gap-3">
            <div>
              <img src="/media/detector/v1_training_curves.png" alt="v1 training curves" className="w-full rounded" />
              <p className="mt-1 text-xs text-center text-muted-foreground">v1 (sim-only)</p>
            </div>
            <div>
              <img src="/media/detector/v2_training_curves.png" alt="v2 training curves" className="w-full rounded" />
              <p className="mt-1 text-xs text-center text-muted-foreground">v2 (+ VisDrone)</p>
            </div>
            <div>
              <img src="/media/detector/v3_training_curves.png" alt="v3 training curves" className="w-full rounded" />
              <p className="mt-1 text-xs text-center text-muted-foreground">v3 (oversampled)</p>
            </div>
          </figure>
          <h3>6.1 V1 — simulation baseline</h3>
          <ul>
            <li>mAP50 = 0.471, mAP50-95 = 0.341</li>
            <li>Precision = 0.79, Recall = 0.41</li>
            <li>Vehicle AP50 = 0.895</li>
            <li>Drone AP50 = 0.047</li>
            <li>Person AP50 = 0.894</li>
            <li>Person_aerial AP50 = 0.000 (no aerial person views in sim)</li>
            <li>Inference: 18 ms / 55 FPS on RTX 5090</li>
          </ul>
          <p>
            The high per-class AP on vehicle and person reflects easy sim
            geometry. The 0.471 headline is an artifact of evaluating on the
            same simulator distribution used for training.
          </p>

          <h3>6.2 V2 — VisDrone merge</h3>
          <ul>
            <li>mAP50 = 0.376 (VisDrone val), mAP50-95 = 0.215</li>
            <li>Precision = 0.54, Recall = 0.36</li>
            <li>Person_aerial AP50 = 0.377 (+0.377 vs v1)</li>
            <li>Vehicle AP50 = 0.755 (−0.140 vs v1)</li>
            <li>Bicycle_motorcycle AP50 = 0.365 (first non-zero result)</li>
            <li>Drone AP50 = 0.010 (−0.037 vs v1; near-collapse)</li>
          </ul>
          <p>
            The mAP50 drop from 0.471 to 0.376 is not a regression — it
            reflects a harder, real-world val set. The drone collapse is a
            genuine failure: 343K VisDrone boxes reduced drone boxes to 3.5%
            of total training signal.
          </p>

          <figure className="my-8 grid grid-cols-2 gap-3">
            <div>
              <img src="/media/detector/v2_val_predictions_batch0.jpg" alt="v2 val predictions" className="w-full rounded" />
              <p className="mt-1 text-xs text-center text-muted-foreground">v2 val predictions — good on vehicles/people, drone boxes missing</p>
            </div>
            <div>
              <img src="/media/detector/v3_val_predictions.jpg" alt="v3 val predictions" className="w-full rounded" />
              <p className="mt-1 text-xs text-center text-muted-foreground">v3 val predictions — drone class recovered after oversampling</p>
            </div>
          </figure>
          <h3>6.3 V3 — 4× drone oversampling</h3>
          <ul>
            <li>mAP50 = 0.384 (VisDrone val), mAP50-95 = 0.219</li>
            <li>Precision = 0.55, Recall = 0.37</li>
            <li>Person_aerial AP50 = 0.360 (−0.017 vs v2; stable)</li>
            <li>Vehicle AP50 = 0.752 (−0.003 vs v2; stable)</li>
            <li>Bicycle_motorcycle AP50 = 0.339 (−0.026 vs v2; small regression)</li>
            <li>Drone AP50 = 0.087 (+0.077 vs v2; recovered)</li>
          </ul>
          <p>
            Drone fraction in training images rose from ~3.5% to ~10% after
            4× oversampling. The class recovered without materially harming
            the VisDrone classes. The drone AP50 is evaluated against the sim
            val set (VisDrone val contains no drone targets); it represents
            precision when the model fires on a drone, not recall across a
            large drone val set.
          </p>

          <figure className="my-8 grid grid-cols-2 gap-3">
            <div>
              <img src="/media/detector/v3_confusion.png" alt="v3 confusion matrix" className="w-full rounded" />
              <p className="mt-1 text-xs text-center text-muted-foreground">v3 confusion matrix — vehicles and person_aerial dominate; drone and rare classes small but present</p>
            </div>
            <div>
              <img src="/media/detector/v3_pr_curve.png" alt="v3 PR curve" className="w-full rounded" />
              <p className="mt-1 text-xs text-center text-muted-foreground">v3 per-class PR curves</p>
            </div>
          </figure>
          <h2>7. Discussion</h2>
          <h3>7.1 Sim-to-real gap in mAP reporting</h3>
          <p>
            V1&apos;s mAP50 = 0.471 on a sim val set is not comparable to v2/v3&apos;s
            mAP50 on a real-world val set. Treating the v1 number as a baseline
            and the v2 number as a regression would be a measurement error.
            The correct interpretation: v2 and v3 provide the first meaningful
            out-of-distribution evaluation because they use VisDrone val, which
            the model has not seen during training.
          </p>
          <h3>7.2 Class imbalance as the dominant failure mode</h3>
          <p>
            The drone collapse in v2 was caused entirely by class imbalance.
            At 3.5% of total box count, the gradient signal for the drone class
            was insufficient to maintain the v1 representations acquired from
            sim. The threshold is approximately 5% — below that, a class in a
            mixed-corpus training run is at risk of collapse without explicit
            rebalancing.
          </p>
          <p>
            The fix — 4× image-level oversampling of drone-containing images —
            is effective and simple. More principled alternatives (copy-paste
            augmentation, class-weighted focal loss, synthetic drone compositing)
            remain for future work.
          </p>
          <h3>7.3 Missing classes</h3>
          <p>
            Landing_pad, powerline_pole, animal, and boat have zero to near-zero
            training data across all three versions. These classes require either
            additional sim generation (for landing_pad and powerline_pole) or
            public dataset sourcing (for animal and boat at altitude). They are
            included in the schema now so inference code does not need to change
            when data becomes available.
          </p>

          <h2>8. Deployment</h2>
          <p>
            The v3 ONNX (12 MB, FP32) is uploaded to S3 and downloaded by
            the drone setup script. The perception layer auto-selects the domain
            detector over the COCO model when it is present, using the same
            ONNX inference path. TensorRT int8 export targeting the Jetson Orin
            Nano is the next step; preliminary benchmarks suggest 8–12 FPS at
            int8 on the Nano&apos;s integrated GPU, which is within budget for the
            perception pipeline.
          </p>

          <h2>9. Conclusion</h2>
          <p>
            We trained a 9-class domain object detector for aerial autonomy
            across three rounds of training. The main result is not the final
            mAP number — it is the failure mode: class imbalance at the 3–5%
            box-fraction level causes rare classes to collapse in mixed-corpus
            training. Simple image-level oversampling recovers the class.
            The next phase is expanding rare-class coverage (landing_pad,
            powerline_pole) via sim data generation and running the model on
            the Jetson Orin Nano in hardware-in-the-loop evaluation.
          </p>
          <p>
            See the blog post for an accessible walkthrough:{" "}
            <Link href="/blog/domain-detector-aerial-autonomy">
              We Trained a Domain Detector for Drones. One Class Collapsed to Zero.
            </Link>
          </p>
        </Prose>
      );

    default:
      return null;
  }
}

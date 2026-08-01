// Turns a policy's raw (unbounded) velocity command into a position/yaw
// update, respecting the same vehicle limits and safety clamps as
// eco/drone/common/reactive_planner.py and eco/drone/common/vehicle_class.py.

import * as THREE from "three";
import {
  CONTROL_DT,
  QUAD_LIMITS,
  ROVER_LIMITS,
  SEQ_LEN_WARMUP,
  STALL_STEPS,
  STALL_PROGRESS,
  MAX_LEG_TICKS,
  bodyForward,
  bodyLeft,
  BODY_UP,
} from "./contract";

// First-order lag + accel cap, matching eco/drone/training/dynamics.py's DynamicsDR.apply():
// the commanded velocity is not applied instantly — it's eased toward at a lag time constant,
// then the resulting delta is capped by an accel limit. Constants below are representative
// (roughly mid-range) picks from that file's domain-randomization ranges; actuation latency
// and wind are intentionally omitted (a marketing sim shouldn't drift or lag by ticks).
const VEL_LAG_TAU = 0.15; // s, DR range [0.08, 0.30]
const YAW_LAG_TAU = 0.12; // s, DR range [0.05, 0.25]
const YAW_ACCEL_MAX = 8.0; // rad/s^2, DR range [5, 15]

export interface QuadKinematics {
  pos: THREE.Vector3; // world (y-up)
  vel: THREE.Vector3; // world, REALIZED velocity (lagged + accel-capped, not the raw command)
  yaw: number;
  yawRate: number; // REALIZED yaw rate
  altitude: number; // AGL, world y here (ground assumed at y=0 for the quad's takeoff frame)
}

/** Advance quad kinematics by one CONTROL_DT tick given a raw [vx,vy,vz,yawRate] body-frame action. */
export function stepQuad(k: QuadKinematics, action: Float32Array): void {
  let [vx, vy, vz, yawRateCmd] = action;

  // speed-norm clamp (reactive_planner.py: clamp ||v|| to max_speed)
  const spd = Math.sqrt(vx * vx + vy * vy + vz * vz);
  if (spd > QUAD_LIMITS.maxSpeed && spd > 0) {
    const s = QUAD_LIMITS.maxSpeed / spd;
    vx *= s;
    vy *= s;
    vz *= s;
  }
  // altitude floor: never command further descent near the ground
  if (k.altitude < QUAD_LIMITS.altitudeFloor && vz < 0) vz = 0;

  yawRateCmd = clamp(yawRateCmd, -QUAD_LIMITS.maxYawRate, QUAD_LIMITS.maxYawRate);

  const fwd = bodyForward(k.yaw);
  const left = bodyLeft(k.yaw);
  const up = BODY_UP;
  const commandedWorld = new THREE.Vector3(
    vx * fwd[0] + vy * left[0] + vz * up[0],
    vx * fwd[1] + vy * left[1] + vz * up[1],
    vx * fwd[2] + vy * left[2] + vz * up[2],
  );

  lagAndCapVec3(k.vel, commandedWorld, VEL_LAG_TAU, QUAD_LIMITS.maxAccel, CONTROL_DT);
  k.yawRate = lagAndCap(k.yawRate, yawRateCmd, YAW_LAG_TAU, YAW_ACCEL_MAX, CONTROL_DT);

  k.pos.addScaledVector(k.vel, CONTROL_DT);
  k.yaw += k.yawRate * CONTROL_DT;
  k.altitude = k.pos.y;
}

export interface RoverKinematics {
  pos: THREE.Vector3; // world (y-up), y pinned externally to the env's roverY
  speed: number; // signed forward speed, m/s, REALIZED (lagged + accel-capped)
  yaw: number;
  yawRate: number; // REALIZED yaw rate
}

/** Advance rover kinematics by one CONTROL_DT tick given a raw [v_linear, yaw_rate] action. */
export function stepRover(k: RoverKinematics, action: Float32Array): void {
  let [vLinear, yawRateCmd] = action;
  vLinear = clamp(vLinear, -ROVER_LIMITS.maxSpeed, ROVER_LIMITS.maxSpeed);
  yawRateCmd = clamp(yawRateCmd, -ROVER_LIMITS.maxYawRate, ROVER_LIMITS.maxYawRate);

  k.speed = lagAndCap(k.speed, vLinear, VEL_LAG_TAU, ROVER_LIMITS.maxAccel, CONTROL_DT);
  k.yawRate = lagAndCap(k.yawRate, yawRateCmd, YAW_LAG_TAU, YAW_ACCEL_MAX, CONTROL_DT);

  const fwd = bodyForward(k.yaw);
  k.pos.x += fwd[0] * k.speed * CONTROL_DT;
  k.pos.z += fwd[2] * k.speed * CONTROL_DT;
  k.yaw += k.yawRate * CONTROL_DT;
}

function clamp(x: number, lo: number, hi: number): number {
  return Math.min(Math.max(x, lo), hi);
}

/** Scalar first-order lag toward `commanded`, then accel-capped (mirrors DynamicsDR.apply's yaw path). */
function lagAndCap(current: number, commanded: number, tau: number, aMax: number, dt: number): number {
  const alpha = clamp(dt / tau, 0, 1);
  const desired = current + (commanded - current) * alpha;
  const delta = clamp(desired - current, -aMax * dt, aMax * dt);
  return current + delta;
}

/** Vector3 first-order lag toward `commanded`, then norm-capped by accel (mirrors DynamicsDR.apply's velocity path). In-place on `current`. */
function lagAndCapVec3(
  current: THREE.Vector3,
  commanded: THREE.Vector3,
  tau: number,
  aMax: number,
  dt: number,
): void {
  const alpha = clamp(dt / tau, 0, 1);
  const dx = (commanded.x - current.x) * alpha;
  const dy = (commanded.y - current.y) * alpha;
  const dz = (commanded.z - current.z) * alpha;
  const dLen = Math.sqrt(dx * dx + dy * dy + dz * dz);
  const cap = aMax * dt;
  const s = dLen > cap && dLen > 0 ? cap / dLen : 1;
  current.x += dx * s;
  current.y += dy * s;
  current.z += dz * s;
}

// --- soft collision ---------------------------------------------------------

const _raycaster = new THREE.Raycaster();

/**
 * Clip a tentative move from `prevPos` to `nextPos` so the vehicle stops
 * `radius` metres short of any surface instead of penetrating it. Returns
 * `nextPos` unchanged when the path is clear. Deliberately a hard stop, not a
 * tangent-plane slide — simpler, and the policy already avoids obstacles in
 * the overwhelming majority of single-obstacle runs, so this should rarely
 * fire in practice.
 */
export function resolveSoftCollision(
  prevPos: THREE.Vector3,
  nextPos: THREE.Vector3,
  radius: number,
  targets: THREE.Mesh[],
): THREE.Vector3 {
  const delta = new THREE.Vector3().subVectors(nextPos, prevPos);
  const dist = delta.length();
  if (dist < 1e-6 || targets.length === 0) return nextPos;
  const dir = delta.clone().normalize();
  _raycaster.set(prevPos, dir);
  _raycaster.far = dist + radius;
  const hits = _raycaster.intersectObjects(targets, false);
  if (hits.length === 0) return nextPos;
  const hit = hits[0];
  if (hit.distance >= dist + radius) return nextPos;
  const allowed = Math.max(hit.distance - radius, 0);
  return prevPos.clone().addScaledVector(dir, allowed);
}

// --- stall watch -------------------------------------------------------------

/**
 * Ports reactive_planner.py's stall detector: force-advance a waypoint if
 * distance-to-target hasn't improved by STALL_PROGRESS metres over
 * STALL_STEPS ticks. Suppressed for the first SEQ_LEN_WARMUP ticks while the
 * GRU hidden state warms up from zero (it emits transient junk on spawn).
 * Also enforces MAX_LEG_TICKS as a hard cap regardless of progress, matching
 * the trainers' own MAX_STEPS=250 (25s) episode timeout — beyond that the
 * policy is operating outside anything it was ever rewarded for.
 */
export class StallWatch {
  private buffer: number[] = [];
  private warmupTicks = 0;
  private legTicks = 0;

  reset(): void {
    this.buffer = [];
    this.warmupTicks = 0;
    this.legTicks = 0;
  }

  /** Feed the current distance-to-target; returns true if stalled (caller should force-advance). */
  tick(dist: number): boolean {
    this.legTicks += 1;
    if (this.legTicks >= MAX_LEG_TICKS) return true;

    this.warmupTicks += 1;
    if (this.warmupTicks === SEQ_LEN_WARMUP + 1) this.buffer = [];
    if (this.warmupTicks <= SEQ_LEN_WARMUP) return false;

    this.buffer.push(dist);
    if (this.buffer.length > STALL_STEPS) this.buffer.shift();
    if (this.buffer.length === STALL_STEPS) {
      const progress = this.buffer[0] - this.buffer[this.buffer.length - 1];
      if (progress < STALL_PROGRESS) return true;
    }
    return false;
  }
}

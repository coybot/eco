// Direct port of the training-time state/action contracts. Field order and
// geometry constants must match eco/drone/training/contract.py and
// rover_contract.py exactly — the ONNX export depends on this order.
// Do not reorder, rescale, or "clean up" without bumping the policy version.

// --- vehicle kinds ---------------------------------------------------------
export const VEHICLE_QUAD = 0.0;
export const VEHICLE_ROVER = 1.0;

// --- quad depth-grid geometry (contract.py) --------------------------------
export const DEPTH_COLS = 9;
export const DEPTH_ROWS = 5;
export const DEPTH_RAYS = DEPTH_COLS * DEPTH_ROWS; // 45
export const DEPTH_HFOV = (90.0 * Math.PI) / 180;
export const DEPTH_VFOV = (70.0 * Math.PI) / 180;
export const DEPTH_MAX = 10.0;

const YAW_OFFSETS = Array.from(
  { length: DEPTH_COLS },
  (_, c) => DEPTH_HFOV / 2 - c * (DEPTH_HFOV / (DEPTH_COLS - 1)),
); // [+45deg .. 0 .. -45deg]

const PITCH_OFFSETS = Array.from(
  { length: DEPTH_ROWS },
  (_, r) => DEPTH_VFOV / 2 - r * (DEPTH_VFOV / (DEPTH_ROWS - 1)),
); // [+35deg (top) .. 0 .. -35deg (bottom)]

// Unit ray directions in BODY frame (fwd, left, up), row-major top->bottom,
// left->right — 45 entries of 3 floats each.
export const RAY_DIRS: Float32Array = (() => {
  const dirs = new Float32Array(DEPTH_RAYS * 3);
  let i = 0;
  for (const pitch of PITCH_OFFSETS) {
    for (const yaw of YAW_OFFSETS) {
      const ce = Math.cos(pitch);
      dirs[i * 3 + 0] = ce * Math.cos(yaw); // fwd
      dirs[i * 3 + 1] = ce * Math.sin(yaw); // left
      dirs[i * 3 + 2] = Math.sin(pitch); // up
      i += 1;
    }
  }
  return dirs;
})();

// --- rover lidar-ring geometry (rover_contract.py) -------------------------
export const LIDAR_RAYS = 72;
export const LIDAR_MAX = 10.0;

// Unit directions in BODY frame (fwd, left) — ray 0 = forward, CCW positive.
export const LIDAR_DIRS: Float32Array = (() => {
  const dirs = new Float32Array(LIDAR_RAYS * 2);
  for (let i = 0; i < LIDAR_RAYS; i++) {
    const a = (2 * Math.PI * i) / LIDAR_RAYS;
    dirs[i * 2 + 0] = Math.cos(a); // fwd
    dirs[i * 2 + 1] = Math.sin(a); // left
  }
  return dirs;
})();

// --- field order (do not reorder) ------------------------------------------
// Quad:  11 base + 45 depth  = 56
// Rover: 11 base + 72 lidar  = 83
export const QUAD_STATE_DIM = 11 + DEPTH_RAYS;
export const ROVER_STATE_DIM = 11 + LIDAR_RAYS;

export function wrapPi(angle: number): number {
  return ((angle + Math.PI) % (2 * Math.PI)) - Math.PI;
}

// --- three.js y-up <-> body-frame (fwd, left, up) bridge -------------------
//
// sim-canvas.tsx measures yaw as atan2(-dx, -dz) toward a travel target, so
// yaw = 0 points along world -z. Basis vectors below are derived from that
// convention (see plan doc for the cross-product derivation) — verified at
// runtime by the axis assertion in the parity harness, not just by inspection.
export function bodyForward(yaw: number): [number, number, number] {
  return [-Math.sin(yaw), 0, -Math.cos(yaw)];
}
export function bodyLeft(yaw: number): [number, number, number] {
  return [-Math.cos(yaw), 0, Math.sin(yaw)];
}
export const BODY_UP: [number, number, number] = [0, 1, 0];

/** Project a world-frame (x,y,z) vector onto the body (fwd,left,up) basis. */
export function worldToBody(
  wx: number,
  wy: number,
  wz: number,
  yaw: number,
): [number, number, number] {
  const [fx, , fz] = bodyForward(yaw);
  const [lx, , lz] = bodyLeft(yaw);
  return [wx * fx + wz * fz, wx * lx + wz * lz, wy];
}

// --- quad state ---------------------------------------------------------
export interface QuadStateInputs {
  targetFwd: number;
  targetLeft: number;
  targetUp: number;
  velFwd: number;
  velLeft: number;
  velUp: number;
  yawRate: number;
  altitude: number;
  /** length-45 depth grid, metres, row-major top->bottom, left->right */
  depth: Float32Array;
}

/** Mirrors contract.py build_state(). Returns a length-56 Float32Array, raw SI (no normalization). */
export function buildQuadState(inp: QuadStateInputs): Float32Array {
  const { targetFwd: tf, targetLeft: tl, targetUp: tu } = inp;
  const dist = Math.sqrt(tf * tf + tl * tl + tu * tu);
  const yawErr = tf !== 0 || tl !== 0 ? wrapPi(Math.atan2(tl, tf)) : 0;

  const out = new Float32Array(QUAD_STATE_DIM);
  out[0] = tf;
  out[1] = tl;
  out[2] = tu;
  out[3] = dist;
  out[4] = inp.velFwd;
  out[5] = inp.velLeft;
  out[6] = inp.velUp;
  out[7] = yawErr;
  out[8] = inp.yawRate;
  out[9] = inp.altitude;
  out[10] = VEHICLE_QUAD;
  for (let i = 0; i < DEPTH_RAYS; i++) {
    out[11 + i] = Math.min(Math.max(inp.depth[i], 0), DEPTH_MAX);
  }
  return out;
}

// --- rover state ----------------------------------------------------------
export interface RoverStateInputs {
  targetFwd: number;
  targetLeft: number;
  velFwd: number;
  yawRate: number;
  /** length-72 lidar ring, metres, ray 0 = fwd, CCW */
  lidar: Float32Array;
}

/** Mirrors rover_contract.py build_obs(). Returns a length-83 Float32Array, raw SI. */
export function buildRoverState(inp: RoverStateInputs): Float32Array {
  const { targetFwd: tf, targetLeft: tl } = inp;
  const dist = Math.hypot(tf, tl);
  const yawErr = dist > 1e-4 ? wrapPi(Math.atan2(tl, tf)) : 0;

  const out = new Float32Array(ROVER_STATE_DIM);
  out[0] = tf;
  out[1] = tl;
  out[2] = 0.0; // target_up: always 0 (ground constrained)
  out[3] = dist;
  out[4] = inp.velFwd;
  out[5] = 0.0; // vel_left: unicycle, no lateral slip
  out[6] = 0.0; // vel_up
  out[7] = yawErr;
  out[8] = inp.yawRate;
  out[9] = 0.0; // altitude: always 0
  out[10] = VEHICLE_ROVER;
  for (let i = 0; i < LIDAR_RAYS; i++) {
    out[11 + i] = Math.min(Math.max(inp.lidar[i], 0), LIDAR_MAX);
  }
  return out;
}

// --- vehicle limits (eco/drone/common/vehicle_class.py) --------------------
export const QUAD_LIMITS = {
  radius: 0.15,
  maxSpeed: 3.0,
  maxAccel: 4.0,
  maxYawRate: 1.5,
  ceiling: 30,
  altitudeFloor: 0.8,
  reachThreshold: 1.0,
  collideR: 0.3,
} as const;

export const ROVER_LIMITS = {
  radius: 0.5,
  maxSpeed: 1.5,
  maxAccel: 1.5,
  maxYawRate: 2.0,
  reachThreshold: 1.0,
  collideR: 0.5,
} as const;

// --- control rate + stall watch (train_rl.py / reactive_planner.py) -------
export const CONTROL_DT = 0.1; // 10 Hz
export const SEQ_LEN_WARMUP = 16; // GRU warm-up ticks; suppress stall detection here
export const STALL_STEPS = 30; // ticks (3.0s @ 10Hz)
export const STALL_PROGRESS = 0.1; // metres of required improvement over STALL_STEPS
export const TARGET_RANGE_GATE: [number, number] = [0.3, 15.0]; // reactive_planner.py range_gate
export const CRUISE_TARGET_CLAMP_M = 12.0; // clamp far waypoints into the trained goal distribution
export const MAX_LEG_TICKS = 250; // train_rl.py MAX_STEPS / train_rl_rover.py MAX_STEPS (25s hard cap)

// Both trainers hard-clamp altitude (state idx 9) to ALT_CAP=4.0 during training
// (train_rl.py: `self.z = (self.z + rvz*DT).clamp(max=ALT_CAP)`, spawn 1.0-2.2m, goal
// altitude clamped to [0.6, ALT_CAP-0.4]=[0.6,3.6]). The city env's quadY=8 is far
// outside that support. Feed the STATE only a clamped altitude so idx 9 stays in
// distribution; real altitude (for dynamics/collision/render) is unaffected.
export const QUAD_STATE_ALTITUDE_CLAMP = 3.6;

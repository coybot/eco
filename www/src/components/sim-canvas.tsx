"use client";

import { useRef, useEffect, Suspense } from "react";
import type { RefObject } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls, useGLTF, Environment } from "@react-three/drei";
import * as THREE from "three";
import type { MissionPlan, EnvironmentType, Vehicle, Waypoint } from "@/lib/mission-types";
import { ENV_CONFIG } from "@/lib/mission-types";
import {
  DEPTH_RAYS,
  DEPTH_MAX,
  LIDAR_RAYS,
  LIDAR_MAX,
  QUAD_LIMITS,
  ROVER_LIMITS,
  QUAD_STATE_ALTITUDE_CLAMP,
  CRUISE_TARGET_CLAMP_M,
  CONTROL_DT,
  worldToBody,
  buildQuadState,
  buildRoverState,
} from "@/lib/sim/contract";
import { PolicyRunner } from "@/lib/sim/policy-runner";
import { buildBvh, sampleDepthGrid, sampleLidarRing } from "@/lib/sim/perception";
import { stepQuad, stepRover, resolveSoftCollision, StallWatch } from "@/lib/sim/dynamics";

useGLTF.setDecoderPath("https://www.gstatic.com/draco/versioned/decoders/1.5.7/");

const QUAD_MODEL_URL = "/models/policy_v26rnn_dr.onnx";
const ROVER_MODEL_URL = "/models/policy_rover_v2.onnx";
const ROVER_DATA_URL = "/models/policy_rover_v2.onnx.data";
const ROVER_DATA_NAME = "policy_rover_v2.onnx.data"; // must match the name baked into the rover .onnx

// ─── Default plan ─────────────────────────────────────────────────────────────

export const DEFAULT_PLAN: MissionPlan = {
  environment: "city",
  missionTitle: "City Survey",
  vehicles: [
    { id: "qc-01", type: "quadcopter", label: "QC-01" },
    { id: "qc-02", type: "quadcopter", label: "QC-02" },
    { id: "rv-01", type: "rover",      label: "RV-01" },
  ],
  waypoints: [
    { vehicleId: "qc-01", x: -8,  y: 8, z:  8, action: "move",    duration: 10, statusLabel: "deploying" },
    { vehicleId: "qc-01", x: -8,  y: 8, z: -4, action: "scan",    duration: 14, statusLabel: "scanning"  },
    { vehicleId: "qc-01", x:  2,  y: 8, z:-12, action: "hover",   duration: 10, statusLabel: "hovering"  },
    { vehicleId: "qc-02", x:  8,  y: 8, z:  8, action: "move",    duration: 10, statusLabel: "deploying" },
    { vehicleId: "qc-02", x:  8,  y: 8, z: -4, action: "scan",    duration: 14, statusLabel: "scanning"  },
    { vehicleId: "qc-02", x:  2,  y: 8, z:-12, action: "hover",   duration: 10, statusLabel: "hovering"  },
    { vehicleId: "rv-01", x:  2,  y: 0.3, z:  8, action: "move",    duration: 10, statusLabel: "advancing" },
    { vehicleId: "rv-01", x:  2,  y: 0.3, z: -4, action: "inspect", duration: 18, statusLabel: "inspecting"},
    { vehicleId: "rv-01", x:  2,  y: 0.3, z:-12, action: "report",  duration: 7,  statusLabel: "reporting" },
  ],
  targetType: "object",
  targetCount: 10,
  reportLines: ["Deploying fleet", "Scanning city block", "Mission complete"],
  missionSummary: "City survey complete.",
};

// ─── Scene lighting (lerps dim↔bright based on missionActive) ─────────────────

function SceneLighting({ missionActive }: { missionActive: boolean }) {
  const ambRef  = useRef<THREE.AmbientLight>(null);
  const dir1Ref = useRef<THREE.DirectionalLight>(null);
  const dir2Ref = useRef<THREE.DirectionalLight>(null);
  const { scene } = useThree();

  useFrame((_, delta) => {
    const t = Math.min(delta * 3, 1);
    if (ambRef.current)
      ambRef.current.intensity  = THREE.MathUtils.lerp(ambRef.current.intensity,  missionActive ? 0.08 : 2.0, t);
    if (dir1Ref.current)
      dir1Ref.current.intensity = THREE.MathUtils.lerp(dir1Ref.current.intensity, missionActive ? 0.10 : 2.5, t);
    if (dir2Ref.current)
      dir2Ref.current.intensity = THREE.MathUtils.lerp(dir2Ref.current.intensity, missionActive ? 0.05 : 1.2, t);
    // IBL from <Environment> ignores the light refs — dim it directly on the scene
    scene.environmentIntensity = THREE.MathUtils.lerp(
      scene.environmentIntensity ?? 1,
      missionActive ? 0.04 : 1.0,
      t
    );
  });

  return (
    <>
      <ambientLight ref={ambRef} intensity={2} />
      <directionalLight ref={dir1Ref} position={[20, 40, 20]} intensity={2.5} castShadow />
      <directionalLight ref={dir2Ref} position={[-10, 20, -10]} intensity={1.2} />
    </>
  );
}

// ─── Environment GLB ──────────────────────────────────────────────────────────

function EnvModel({ path, onLoaded }: { path: string; onLoaded: (root: THREE.Object3D) => void }) {
  const { scene } = useGLTF(path);
  useEffect(() => {
    onLoaded(scene);
  }, [scene, onLoaded]);
  return <primitive object={scene} />;
}

// ─── Vehicle meshes ───────────────────────────────────────────────────────────

function QuadMesh({ glowing }: { glowing: boolean }) {
  const emInt   = glowing ? 4.5 : 0.4;
  const emColor = glowing ? "#ddeeff" : "#aaccff";
  return (
    <group>
      <mesh castShadow>
        <boxGeometry args={[0.7, 0.16, 0.7]} />
        <meshStandardMaterial color="#e8edf4" emissive={emColor} emissiveIntensity={emInt} />
      </mesh>
      {[[-1,1],[-1,-1],[1,1],[1,-1]].map(([sx,sz], i) => (
        <mesh key={i} position={[sx * 0.28, 0, sz * 0.28]}>
          <boxGeometry args={[0.5, 0.04, 0.09]} />
          <meshStandardMaterial color="#c0c8d8" emissive={emColor} emissiveIntensity={glowing ? 2.0 : 0} />
        </mesh>
      ))}
      {[[-1,1],[-1,-1],[1,1],[1,-1]].map(([sx,sz], i) => (
        <mesh key={i} position={[sx * 0.48, 0.03, sz * 0.48]}>
          <cylinderGeometry args={[0.2, 0.2, 0.02, 8]} />
          <meshStandardMaterial color="#667788" transparent opacity={0.6} emissive="#88aaff" emissiveIntensity={glowing ? 3.0 : 0} />
        </mesh>
      ))}
    </group>
  );
}

function RoverMesh({ glowing }: { glowing: boolean }) {
  return (
    <group>
      {/* body — bigger so it's visible from the overhead camera */}
      <mesh castShadow>
        <boxGeometry args={[1.1, 0.55, 1.6]} />
        <meshStandardMaterial color="#3a3f4a" emissive="#334488" emissiveIntensity={glowing ? 2.5 : 0} />
      </mesh>
    </group>
  );
}

// ─── Policy-driven vehicle simulation state ──────────────────────────────────

interface VehicleSimState {
  pos: THREE.Vector3;
  yaw: number;
  wpIdx: number;
  elapsed: number; // lerp fallback: time-at-waypoint timer; policy: dwell timer once arrived
  done: boolean;

  // policy-driven fields
  vel: THREE.Vector3; // world-frame realized velocity (quad)
  speed: number; // signed forward realized speed (rover)
  yawRate: number; // realized yaw rate
  usingPolicy: boolean;
  everUsedPolicy: boolean;
  phase: "transit" | "dwell" | "done";
  warmupTicks: number;
  stall: StallWatch;
  ticking: boolean;
  prevPos: THREE.Vector3;
  prevYaw: number;
}

function makeVehicleState(startPos: THREE.Vector3): VehicleSimState {
  return {
    pos: startPos.clone(),
    yaw: 0,
    wpIdx: 0,
    elapsed: 0,
    done: false,
    vel: new THREE.Vector3(),
    speed: 0,
    yawRate: 0,
    usingPolicy: false,
    everUsedPolicy: false,
    phase: "transit",
    warmupTicks: 0,
    stall: new StallWatch(),
    ticking: false,
    prevPos: startPos.clone(),
    prevYaw: 0,
  };
}

function reachThresholdFor(type: Vehicle["type"]): number {
  return type === "quadcopter" ? QUAD_LIMITS.reachThreshold : ROVER_LIMITS.reachThreshold;
}

// ─── Animated fleet ───────────────────────────────────────────────────────────

const PIP_W = 192;
const PIP_H = 108;

interface FleetProps {
  plan: MissionPlan;
  onWaypointLabel: (vehicleId: string, label: string) => void;
  onTargetDetected: (count: number) => void;
  missionActive: boolean;
  selectedVehicleIds?: string[];
  pipCanvasesRef?: RefObject<Map<string, HTMLCanvasElement>>;
  envRoot: RefObject<THREE.Object3D | null>;
}

function Fleet({ plan, onWaypointLabel, onTargetDetected, missionActive, selectedVehicleIds, pipCanvasesRef, envRoot }: FleetProps) {
  const { gl, scene } = useThree();
  const cfg = ENV_CONFIG[plan.environment];

  const stateRef = useRef<Record<string, VehicleSimState>>({});

  const planKeyRef = useRef("");
  const planKey = `${plan.planVersion ?? 0}-${plan.vehicles.map(v => v.id).join(",")}-${plan.environment}`;

  const meshRefs = useRef<Record<string, THREE.Group | null>>({});
  const detectedRef = useRef(0);

  // --- policy runtime: lazy-loaded ORT sessions, one PolicyRunner per vehicle ---
  const policyReadyRef = useRef(false);
  const runnersRef = useRef<Map<string, PolicyRunner>>(new Map());
  const pendingRunnerLoadsRef = useRef<Set<string>>(new Set());
  const observedRef = useRef(false);

  useEffect(() => {
    if (observedRef.current) return;
    const el = gl.domElement;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some(e => e.isIntersecting) && !observedRef.current) {
          observedRef.current = true;
          obs.disconnect();
          warmUpPolicies();
        }
      },
      { threshold: 0.1 }
    );
    obs.observe(el);
    return () => obs.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gl]);

  async function warmUpPolicies() {
    try {
      await Promise.all([
        PolicyRunner.load(QUAD_MODEL_URL, { defaultHiddenDim: 128 }),
        PolicyRunner.load(ROVER_MODEL_URL, {
          externalDataUrl: ROVER_DATA_URL,
          externalDataName: ROVER_DATA_NAME,
          defaultHiddenDim: 256,
        }),
      ]);
      policyReadyRef.current = true;
      if (typeof window !== "undefined") console.info("[sim] policy runtime ready (onnxruntime-web)");
    } catch (err) {
      policyReadyRef.current = false;
      if (typeof window !== "undefined") console.info("[sim] policy runtime unavailable, using scripted fallback", err);
    }
  }

  function ensureRunnerForVehicle(v: Vehicle): PolicyRunner | undefined {
    const existing = runnersRef.current.get(v.id);
    if (existing) return existing;
    if (!policyReadyRef.current) return undefined;
    if (pendingRunnerLoadsRef.current.has(v.id)) return undefined;
    pendingRunnerLoadsRef.current.add(v.id);
    const load =
      v.type === "quadcopter"
        ? PolicyRunner.load(QUAD_MODEL_URL, { defaultHiddenDim: 128 })
        : PolicyRunner.load(ROVER_MODEL_URL, {
            externalDataUrl: ROVER_DATA_URL,
            externalDataName: ROVER_DATA_NAME,
            defaultHiddenDim: 256,
          });
    load
      .then(r => runnersRef.current.set(v.id, r))
      .catch(() => {
        /* leave unset; vehicle stays on lerp fallback */
      })
      .finally(() => pendingRunnerLoadsRef.current.delete(v.id));
    return undefined;
  }

  function beginLeg(v: Vehicle, state: VehicleSimState) {
    const runner = ensureRunnerForVehicle(v);
    if (runner) {
      if (!state.everUsedPolicy) {
        state.everUsedPolicy = true;
        runner.reset();
        state.warmupTicks = 0;
      }
      state.usingPolicy = true;
    } else {
      state.usingPolicy = false;
    }
    state.stall.reset();
    if (state.phase !== "done") state.phase = "transit";
    state.prevPos.copy(state.pos);
    state.prevYaw = state.yaw;
  }

  function advanceWaypoint(v: Vehicle, state: VehicleSimState, vWaypoints: Waypoint[]) {
    if (state.wpIdx < vWaypoints.length - 1) {
      state.wpIdx++;
      state.elapsed = 0;
      beginLeg(v, state);
      const nextWp = vWaypoints[state.wpIdx];
      onWaypointLabel(v.id, nextWp.statusLabel);
    } else if (!state.done) {
      state.done = true;
      state.phase = "done";
      onWaypointLabel(v.id, "mission ✓");
    }
  }

  // --- BVH perception targets, built once per env root (rebuilt on env change) ---
  const bvhTargetsRef = useRef<THREE.Mesh[] | null>(null);
  const bvhEnvRootRef = useRef<THREE.Object3D | null>(null);

  async function tickVehiclePolicy(v: Vehicle, state: VehicleSimState, vWaypoints: Waypoint[]) {
    if (state.phase === "done") return;

    if (state.phase === "dwell") {
      state.elapsed += CONTROL_DT;
      const wp = vWaypoints[Math.min(state.wpIdx, vWaypoints.length - 1)];
      if (state.elapsed >= wp.duration) advanceWaypoint(v, state, vWaypoints);
      return;
    }

    const wp = vWaypoints[Math.min(state.wpIdx, vWaypoints.length - 1)];
    const wpPos = new THREE.Vector3(wp.x, wp.y, wp.z);
    const toTarget = new THREE.Vector3().subVectors(wpPos, state.pos);
    const trueDist = toTarget.length();

    const stalled = state.stall.tick(trueDist);
    const reach = reachThresholdFor(v.type);

    if (trueDist <= reach) {
      state.phase = "dwell";
      state.elapsed = 0;
      return;
    }
    if (stalled) {
      advanceWaypoint(v, state, vWaypoints);
      return;
    }

    // clamp the goal into the trained goal-distance distribution (goal clamping, not path planning)
    const clampedDist = Math.min(trueDist, CRUISE_TARGET_CLAMP_M);
    const dirWorld = trueDist > 1e-6 ? toTarget.clone().normalize() : new THREE.Vector3();
    const goalWorld = dirWorld.multiplyScalar(clampedDist);
    const [tf, tl, tu] = worldToBody(goalWorld.x, goalWorld.y, goalWorld.z, state.yaw);
    const [vf, vl, vu] = worldToBody(state.vel.x, state.vel.y, state.vel.z, state.yaw);

    const runner = runnersRef.current.get(v.id);
    if (!runner) {
      state.usingPolicy = false;
      return;
    }

    const bvhTargets = bvhTargetsRef.current;
    let action: Float32Array;
    try {
      if (v.type === "quadcopter") {
        const depth = bvhTargets
          ? sampleDepthGrid(state.pos, state.yaw, bvhTargets)
          : new Float32Array(DEPTH_RAYS).fill(DEPTH_MAX);
        const stateVec = buildQuadState({
          targetFwd: tf,
          targetLeft: tl,
          targetUp: tu,
          velFwd: vf,
          velLeft: vl,
          velUp: vu,
          yawRate: state.yawRate,
          // state idx 9 only: both trainers hard-clamp altitude to ALT_CAP=4.0 (train_rl.py),
          // so the city env's quadY=8 must be clamped here to stay in distribution. Real
          // altitude (state.pos.y) is unaffected — used for dynamics/collision/render.
          altitude: Math.min(state.pos.y, QUAD_STATE_ALTITUDE_CLAMP),
          depth,
        });
        action = await runner.step(stateVec);
      } else {
        const lidar = bvhTargets
          ? sampleLidarRing(state.pos, state.yaw, bvhTargets)
          : new Float32Array(LIDAR_RAYS).fill(LIDAR_MAX);
        const stateVec = buildRoverState({
          targetFwd: tf,
          targetLeft: tl,
          velFwd: vf,
          yawRate: state.yawRate,
          lidar,
        });
        action = await runner.step(stateVec);
      }
    } catch {
      state.usingPolicy = false;
      return;
    }

    state.warmupTicks += 1;
    if (state.warmupTicks <= 16) return; // hold still while the GRU hidden state warms up

    const beforePos = state.pos.clone();
    if (v.type === "quadcopter") {
      const k = { pos: state.pos, vel: state.vel, yaw: state.yaw, yawRate: state.yawRate, altitude: state.pos.y };
      stepQuad(k, action);
      state.yaw = k.yaw;
      state.yawRate = k.yawRate;
    } else {
      const k = { pos: state.pos, speed: state.speed, yaw: state.yaw, yawRate: state.yawRate };
      stepRover(k, action);
      state.speed = k.speed;
      state.yaw = k.yaw;
      state.yawRate = k.yawRate;
    }

    if (bvhTargets && bvhTargets.length) {
      const collideR = v.type === "quadcopter" ? QUAD_LIMITS.collideR : ROVER_LIMITS.collideR;
      const clipped = resolveSoftCollision(beforePos, state.pos, collideR, bvhTargets);
      state.pos.copy(clipped);
    }
  }

  if (planKey !== planKeyRef.current) {
    planKeyRef.current = planKey;
    detectedRef.current = 0;
    stateRef.current = {};
    plan.vehicles.forEach(v => {
      const firstWp = plan.waypoints.find(w => w.vehicleId === v.id);
      const startPos = firstWp
        ? new THREE.Vector3(firstWp.x, firstWp.y, firstWp.z)
        : new THREE.Vector3(0, v.type === 'quadcopter' ? cfg.quadY : cfg.roverY, 0);
      const state = makeVehicleState(startPos);
      stateRef.current[v.id] = state;
      beginLeg(v, state);
    });
  }

  const targetPositions = useRef<THREE.Vector3[]>([]);
  if (targetPositions.current.length !== plan.targetCount) {
    const [xMin, xMax] = cfg.xRange;
    const [zMin, zMax] = cfg.zRange;
    targetPositions.current = Array.from({ length: plan.targetCount }, (_, i) => {
      const t = i / Math.max(plan.targetCount - 1, 1);
      return new THREE.Vector3(
        xMin + (xMax - xMin) * ((i * 0.618) % 1),
        cfg.roverY + 0.5,
        zMin + (zMax - zMin) * t
      );
    });
  }

  // Per-vehicle PiP rendering state
  const pipTargetsRef = useRef<Map<string, THREE.WebGLRenderTarget>>(new Map());
  const pipCamsRef = useRef<Map<string, THREE.PerspectiveCamera>>(new Map());
  const pipPixelsRef = useRef<Map<string, Uint8Array>>(new Map());
  const pipTempCanvasesRef = useRef<Map<string, HTMLCanvasElement>>(new Map());
  const pipFrameRef = useRef(0);

  const accRef = useRef(0);

  useFrame((_, delta) => {
    // rebuild BVH targets if the env root changed (env switch, or GLB just finished loading)
    if (envRoot.current && envRoot.current !== bvhEnvRootRef.current) {
      bvhEnvRootRef.current = envRoot.current;
      bvhTargetsRef.current = buildBvh(envRoot.current);
    }

    let detected = 0;
    const vehiclePositions: THREE.Vector3[] = [];

    // --- lerp-fallback vehicles: original per-frame behavior, unchanged ---
    plan.vehicles.forEach(v => {
      const state = stateRef.current[v.id];
      if (!state || state.usingPolicy) return;
      const vWaypoints = plan.waypoints.filter(w => w.vehicleId === v.id);
      if (!vWaypoints.length) return;

      const wp = vWaypoints[Math.min(state.wpIdx, vWaypoints.length - 1)];
      const target = new THREE.Vector3(wp.x, wp.y, wp.z);
      const toTarget = target.clone().sub(state.pos);
      const dist = toTarget.length();

      if (dist > 0.5) {
        const targetYaw = Math.atan2(-toTarget.x, -toTarget.z);
        let diff = targetYaw - state.yaw;
        while (diff > Math.PI) diff -= 2 * Math.PI;
        while (diff < -Math.PI) diff += 2 * Math.PI;
        state.yaw += diff * Math.min(delta * 2.5, 1);
      }

      state.pos.lerp(target, Math.min(delta * 0.35, 1));

      state.elapsed += delta;
      if (state.elapsed >= wp.duration) {
        advanceWaypoint(v, state, vWaypoints); // may upgrade to policy mode via beginLeg()
      }
    });

    // --- fixed 10Hz accumulator for policy-driven vehicles ---
    accRef.current += delta;
    while (accRef.current >= CONTROL_DT) {
      accRef.current -= CONTROL_DT;
      plan.vehicles.forEach(v => {
        const state = stateRef.current[v.id];
        if (!state || !state.usingPolicy || state.ticking) return;
        state.prevPos.copy(state.pos);
        state.prevYaw = state.yaw;
        state.ticking = true;
        const vWaypoints = plan.waypoints.filter(w => w.vehicleId === v.id);
        tickVehiclePolicy(v, state, vWaypoints).finally(() => {
          state.ticking = false;
        });
      });
    }

    // --- render all vehicle meshes (policy path interpolated, lerp path direct) ---
    const alpha = Math.min(accRef.current / CONTROL_DT, 1);
    let anyPolicy = false;
    let allPolicy = true;
    plan.vehicles.forEach(v => {
      const state = stateRef.current[v.id];
      if (!state) return;
      const mesh = meshRefs.current[v.id];
      if (state.usingPolicy) {
        anyPolicy = true;
        if (mesh) {
          mesh.position.lerpVectors(state.prevPos, state.pos, alpha);
          let dy = state.yaw - state.prevYaw;
          while (dy > Math.PI) dy -= 2 * Math.PI;
          while (dy < -Math.PI) dy += 2 * Math.PI;
          mesh.rotation.y = state.prevYaw + dy * alpha;
        }
      } else {
        allPolicy = false;
        if (mesh) {
          mesh.position.copy(state.pos);
          mesh.rotation.y = state.yaw;
        }
      }
      vehiclePositions.push(state.pos.clone());
    });
    if (!anyPolicy) allPolicy = false;

    const detectionR = 4;
    targetPositions.current.forEach(tp => {
      for (const vp of vehiclePositions) {
        if (vp.distanceTo(tp) < detectionR) { detected++; break; }
      }
    });
    if (detected !== detectedRef.current) {
      detectedRef.current = detected;
      onTargetDetected(detected);
    }

    // machine-checkable sim-mode indicator (no user-visible UI)
    const mode = !anyPolicy ? "lerp" : allPolicy ? "policy" : "mixed";
    if (gl.domElement.dataset.simMode !== mode) {
      gl.domElement.dataset.simMode = mode;
      console.info(`[sim] mode -> ${mode}`);
    }

    // PiP: render all selected vehicles' POV to their canvases every 3rd frame
    pipFrameRef.current++;
    if (selectedVehicleIds?.length && pipCanvasesRef?.current && pipFrameRef.current % 3 === 0) {
      for (const vid of selectedVehicleIds) {
        const state = stateRef.current[vid];
        const vehicle = plan.vehicles.find(v => v.id === vid);
        const outCanvas = pipCanvasesRef.current.get(vid);
        if (!state || !outCanvas) continue;

        if (!pipTargetsRef.current.has(vid)) {
          pipTargetsRef.current.set(vid, new THREE.WebGLRenderTarget(PIP_W, PIP_H));
          pipCamsRef.current.set(vid, new THREE.PerspectiveCamera(75, PIP_W / PIP_H, 0.1, 200));
          pipPixelsRef.current.set(vid, new Uint8Array(PIP_W * PIP_H * 4));
          if (typeof document !== 'undefined') {
            const tc = document.createElement('canvas');
            tc.width = PIP_W; tc.height = PIP_H;
            pipTempCanvasesRef.current.set(vid, tc);
          }
        }

        const target = pipTargetsRef.current.get(vid)!;
        const cam = pipCamsRef.current.get(vid)!;
        const pixels = pipPixelsRef.current.get(vid)!;
        const tempCanvas = pipTempCanvasesRef.current.get(vid);

        if (vehicle?.type === 'rover') {
          cam.position.set(state.pos.x, state.pos.y + 0.4, state.pos.z + 0.5);
          cam.lookAt(state.pos.x, state.pos.y + 0.1, state.pos.z - 6);
        } else {
          cam.position.copy(state.pos);
          cam.lookAt(state.pos.x, 0, state.pos.z - 2);
        }

        gl.setRenderTarget(target);
        gl.render(scene, cam);
        const glCtx = gl.getContext() as WebGLRenderingContext;
        glCtx.readPixels(0, 0, PIP_W, PIP_H, glCtx.RGBA, glCtx.UNSIGNED_BYTE, pixels);
        gl.setRenderTarget(null);

        if (tempCanvas) {
          const tempCtx = tempCanvas.getContext('2d');
          const outCtx = outCanvas.getContext('2d');
          if (tempCtx && outCtx) {
            const imgData = new ImageData(new Uint8ClampedArray(pixels), PIP_W, PIP_H);
            tempCtx.putImageData(imgData, 0, 0);
            outCtx.save();
            outCtx.translate(0, PIP_H);
            outCtx.scale(1, -1);
            outCtx.drawImage(tempCanvas, 0, 0);
            outCtx.restore();
          }
        }
      }
    }
  });

  return (
    <>
      {plan.vehicles.map(v => (
        <group
          key={v.id}
          ref={el => { meshRefs.current[v.id] = el; }}
          position={[0, v.type === 'quadcopter' ? cfg.quadY : cfg.roverY, 0]}
        >
          {v.type === 'quadcopter' ? <QuadMesh glowing={missionActive} /> : <RoverMesh glowing={missionActive} />}
          {missionActive && (
            <pointLight color="#aaccff" intensity={12} distance={20} decay={2} />
          )}
        </group>
      ))}
    </>
  );
}

// ─── Main canvas ──────────────────────────────────────────────────────────────

interface SimCanvasProps {
  plan: MissionPlan;
  onTargetDetected: (count: number) => void;
  onWaypointLabel: (vehicleId: string, label: string) => void;
  missionActive: boolean;
  selectedVehicleIds?: string[];
  pipCanvasesRef?: RefObject<Map<string, HTMLCanvasElement>>;
  isMobile?: boolean;
}

export function SimCanvas({ plan, onTargetDetected, onWaypointLabel, missionActive, selectedVehicleIds, pipCanvasesRef, isMobile }: SimCanvasProps) {
  const cfg = ENV_CONFIG[plan.environment];
  const envRootRef = useRef<THREE.Object3D | null>(null);

  return (
    <Canvas
      camera={{ position: cfg.camera, fov: cfg.fov }}
      shadows={!isMobile}
      dpr={isMobile ? [1, 1] : [1, 2]}
      gl={{ preserveDrawingBuffer: true, powerPreference: isMobile ? 'low-power' : 'high-performance' }}
      style={{ background: '#0d1117' }}
    >
      <SceneLighting missionActive={missionActive} />

      <Suspense fallback={null}>
        <EnvModel path={cfg.model} onLoaded={(root) => { envRootRef.current = root; }} />
        <Environment preset={cfg.envPreset as any} background={false} />
      </Suspense>

      <Fleet
        plan={plan}
        onTargetDetected={onTargetDetected}
        onWaypointLabel={onWaypointLabel}
        missionActive={missionActive}
        selectedVehicleIds={selectedVehicleIds}
        pipCanvasesRef={pipCanvasesRef}
        envRoot={envRootRef}
      />

      <OrbitControls
        target={cfg.target}
        minDistance={4}
        maxDistance={100}
        autoRotate
        autoRotateSpeed={0.35}
        enableRotate={!isMobile}
        enablePan={false}
      />
    </Canvas>
  );
}

// Preload both models eagerly
useGLTF.preload('/models/neighbourhood.glb');
useGLTF.preload('/models/apartment.glb');

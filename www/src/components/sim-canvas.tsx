"use client";

import { useRef, Suspense } from "react";
import type { RefObject } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls, useGLTF, Environment } from "@react-three/drei";
import * as THREE from "three";
import type { MissionPlan, EnvironmentType } from "@/lib/mission-types";
import { ENV_CONFIG } from "@/lib/mission-types";

useGLTF.setDecoderPath("https://www.gstatic.com/draco/versioned/decoders/1.5.7/");

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
    { vehicleId: "qc-01", x: -8,  y: 8, z:  8, action: "move",    duration: 3, statusLabel: "deploying" },
    { vehicleId: "qc-01", x: -8,  y: 8, z: -4, action: "scan",    duration: 4, statusLabel: "scanning"  },
    { vehicleId: "qc-01", x:  2,  y: 8, z:-12, action: "hover",   duration: 3, statusLabel: "hovering"  },
    { vehicleId: "qc-02", x:  8,  y: 8, z:  8, action: "move",    duration: 3, statusLabel: "deploying" },
    { vehicleId: "qc-02", x:  8,  y: 8, z: -4, action: "scan",    duration: 4, statusLabel: "scanning"  },
    { vehicleId: "qc-02", x:  2,  y: 8, z:-12, action: "hover",   duration: 3, statusLabel: "hovering"  },
    { vehicleId: "rv-01", x:  2,  y: 0.3, z:  8, action: "move",   duration: 3, statusLabel: "advancing" },
    { vehicleId: "rv-01", x:  2,  y: 0.3, z: -4, action: "inspect",duration: 5, statusLabel: "inspecting"},
    { vehicleId: "rv-01", x:  2,  y: 0.3, z:-12, action: "report", duration: 2, statusLabel: "reporting" },
  ],
  targetType: "object",
  targetCount: 10,
  reportLines: ["Deploying fleet", "Scanning city block", "Mission complete"],
  missionSummary: "City survey complete.",
};

// ─── Environment GLB ──────────────────────────────────────────────────────────

function EnvModel({ path }: { path: string }) {
  const { scene } = useGLTF(path);
  return <primitive object={scene} />;
}

// ─── Vehicle meshes ───────────────────────────────────────────────────────────

function QuadMesh() {
  return (
    <group>
      <mesh castShadow>
        <boxGeometry args={[0.7, 0.16, 0.7]} />
        <meshStandardMaterial color="#e8edf4" emissive="#aaccff" emissiveIntensity={0.4} />
      </mesh>
      {[[-1,1],[-1,-1],[1,1],[1,-1]].map(([sx,sz], i) => (
        <mesh key={i} position={[sx * 0.28, 0, sz * 0.28]}>
          <boxGeometry args={[0.5, 0.04, 0.09]} />
          <meshStandardMaterial color="#c0c8d8" />
        </mesh>
      ))}
      {[[-1,1],[-1,-1],[1,1],[1,-1]].map(([sx,sz], i) => (
        <mesh key={i} position={[sx * 0.48, 0.03, sz * 0.48]}>
          <cylinderGeometry args={[0.2, 0.2, 0.02, 8]} />
          <meshStandardMaterial color="#667788" transparent opacity={0.6} />
        </mesh>
      ))}
    </group>
  );
}

function RoverMesh() {
  return (
    <group>
      <mesh castShadow>
        <boxGeometry args={[0.9, 0.4, 1.3]} />
        <meshStandardMaterial color="#3a3f4a" />
      </mesh>
      <mesh position={[0, 0.28, 0]}>
        <sphereGeometry args={[0.18, 12, 8]} />
        <meshStandardMaterial color="#1166ff" emissive="#0055ff" emissiveIntensity={0.5} />
      </mesh>
    </group>
  );
}

// ─── Animated fleet ───────────────────────────────────────────────────────────

const PIP_W = 192;
const PIP_H = 108;

interface FleetProps {
  plan: MissionPlan;
  onWaypointLabel: (vehicleId: string, label: string) => void;
  onTargetDetected: (count: number) => void;
  selectedVehicleId?: string | null;
  pipCanvasRef?: RefObject<HTMLCanvasElement | null>;
}

function Fleet({ plan, onWaypointLabel, onTargetDetected, selectedVehicleId, pipCanvasRef }: FleetProps) {
  const { gl, scene } = useThree();
  const cfg = ENV_CONFIG[plan.environment];

  const stateRef = useRef<Record<string, {
    pos: THREE.Vector3;
    wpIdx: number;
    elapsed: number;
    done: boolean;
  }>>({});

  const planKeyRef = useRef("");
  const planKey = plan.vehicles.map(v => v.id).join(",") + plan.environment;

  const meshRefs = useRef<Record<string, THREE.Group | null>>({});
  const detectedRef = useRef(0);

  if (planKey !== planKeyRef.current) {
    planKeyRef.current = planKey;
    detectedRef.current = 0;
    stateRef.current = {};
    plan.vehicles.forEach(v => {
      const firstWp = plan.waypoints.find(w => w.vehicleId === v.id);
      const startPos = firstWp
        ? new THREE.Vector3(firstWp.x, firstWp.y, firstWp.z)
        : new THREE.Vector3(0, v.type === 'quadcopter' ? cfg.quadY : cfg.roverY, 0);
      stateRef.current[v.id] = { pos: startPos.clone(), wpIdx: 0, elapsed: 0, done: false };
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

  // PiP rendering state
  const pipTargetRef = useRef<THREE.WebGLRenderTarget | null>(null);
  const pipCamRef = useRef<THREE.PerspectiveCamera | null>(null);
  const pipPixelsRef = useRef<Uint8Array | null>(null);
  const pipTempCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const pipFrameRef = useRef(0);

  if (!pipTargetRef.current) {
    pipTargetRef.current = new THREE.WebGLRenderTarget(PIP_W, PIP_H);
    pipCamRef.current = new THREE.PerspectiveCamera(75, PIP_W / PIP_H, 0.1, 200);
    pipPixelsRef.current = new Uint8Array(PIP_W * PIP_H * 4);
  }
  if (typeof document !== 'undefined' && !pipTempCanvasRef.current) {
    pipTempCanvasRef.current = document.createElement('canvas');
    pipTempCanvasRef.current.width = PIP_W;
    pipTempCanvasRef.current.height = PIP_H;
  }

  useFrame((_, delta) => {
    let detected = 0;
    const vehiclePositions: THREE.Vector3[] = [];

    plan.vehicles.forEach(v => {
      const state = stateRef.current[v.id];
      if (!state) return;
      const mesh = meshRefs.current[v.id];
      const vWaypoints = plan.waypoints.filter(w => w.vehicleId === v.id);
      if (!vWaypoints.length) return;

      const wp = vWaypoints[Math.min(state.wpIdx, vWaypoints.length - 1)];
      const target = new THREE.Vector3(wp.x, wp.y, wp.z);
      state.pos.lerp(target, Math.min(delta * 1.2, 1));
      if (mesh) mesh.position.copy(state.pos);

      state.elapsed += delta;
      if (state.elapsed >= wp.duration) {
        if (state.wpIdx < vWaypoints.length - 1) {
          state.wpIdx++;
          state.elapsed = 0;
          const nextWp = vWaypoints[state.wpIdx];
          onWaypointLabel(v.id, nextWp.statusLabel);
        } else if (!state.done) {
          state.done = true;
          onWaypointLabel(v.id, "mission ✓");
        }
      }

      vehiclePositions.push(state.pos.clone());
    });

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

    // PiP: render selected drone's POV to canvas every 3rd frame
    pipFrameRef.current++;
    if (
      selectedVehicleId &&
      pipCanvasRef?.current &&
      pipTargetRef.current &&
      pipCamRef.current &&
      pipPixelsRef.current &&
      pipTempCanvasRef.current &&
      pipFrameRef.current % 3 === 0
    ) {
      const state = stateRef.current[selectedVehicleId];
      const vehicle = plan.vehicles.find(v => v.id === selectedVehicleId);
      if (state) {
        const cam = pipCamRef.current;
        if (vehicle?.type === 'rover') {
          cam.position.set(state.pos.x, state.pos.y + 0.4, state.pos.z + 0.5);
          cam.lookAt(state.pos.x, state.pos.y + 0.1, state.pos.z - 6);
        } else {
          cam.position.copy(state.pos);
          cam.lookAt(state.pos.x, 0, state.pos.z - 2);
        }

        gl.setRenderTarget(pipTargetRef.current);
        gl.render(scene, cam);

        const glCtx = gl.getContext() as WebGLRenderingContext;
        glCtx.readPixels(0, 0, PIP_W, PIP_H, glCtx.RGBA, glCtx.UNSIGNED_BYTE, pipPixelsRef.current);
        gl.setRenderTarget(null);

        const tempCtx = pipTempCanvasRef.current.getContext('2d');
        const outCtx = pipCanvasRef.current.getContext('2d');
        if (tempCtx && outCtx) {
          const imgData = new ImageData(new Uint8ClampedArray(pipPixelsRef.current), PIP_W, PIP_H);
          tempCtx.putImageData(imgData, 0, 0);
          // WebGL framebuffer is upside-down relative to canvas
          outCtx.save();
          outCtx.translate(0, PIP_H);
          outCtx.scale(1, -1);
          outCtx.drawImage(pipTempCanvasRef.current, 0, 0);
          outCtx.restore();
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
          {v.type === 'quadcopter' ? <QuadMesh /> : <RoverMesh />}
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
  selectedVehicleId?: string | null;
  pipCanvasRef?: RefObject<HTMLCanvasElement | null>;
}

export function SimCanvas({ plan, onTargetDetected, onWaypointLabel, selectedVehicleId, pipCanvasRef }: SimCanvasProps) {
  const cfg = ENV_CONFIG[plan.environment];

  return (
    <Canvas
      camera={{ position: cfg.camera, fov: cfg.fov }}
      shadows
      gl={{ preserveDrawingBuffer: true }}
      style={{ background: '#0d1117' }}
    >
      <ambientLight intensity={2} />
      <directionalLight position={[20, 40, 20]} intensity={2.5} castShadow />
      <directionalLight position={[-10, 20, -10]} intensity={1.2} />

      <Suspense fallback={null}>
        <EnvModel path={cfg.model} />
        <Environment preset={cfg.envPreset as any} background={false} />
      </Suspense>

      <Fleet
        plan={plan}
        onTargetDetected={onTargetDetected}
        onWaypointLabel={onWaypointLabel}
        selectedVehicleId={selectedVehicleId}
        pipCanvasRef={pipCanvasRef}
      />

      <OrbitControls
        target={cfg.target}
        minDistance={4}
        maxDistance={100}
        autoRotate
        autoRotateSpeed={0.35}
      />
    </Canvas>
  );
}

// Preload both models eagerly
useGLTF.preload('/models/neighbourhood.glb');
useGLTF.preload('/models/apartment.glb');

"use client";

import { useRef, Suspense } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
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
    { id: "qd-01", type: "quadcopter", label: "QD-01" },
    { id: "qd-02", type: "quadcopter", label: "QD-02" },
    { id: "rv-01", type: "rover",      label: "RV-01" },
  ],
  waypoints: [
    { vehicleId: "qd-01", x: -8,  y: 8, z:  8, action: "move", duration: 3, statusLabel: "deploying" },
    { vehicleId: "qd-01", x: -8,  y: 8, z: -4, action: "scan", duration: 4, statusLabel: "scanning" },
    { vehicleId: "qd-01", x:  2,  y: 8, z:-12, action: "hover", duration: 3, statusLabel: "hovering" },
    { vehicleId: "qd-02", x:  8,  y: 8, z:  8, action: "move", duration: 3, statusLabel: "deploying" },
    { vehicleId: "qd-02", x:  8,  y: 8, z: -4, action: "scan", duration: 4, statusLabel: "scanning" },
    { vehicleId: "qd-02", x:  2,  y: 8, z:-12, action: "hover", duration: 3, statusLabel: "hovering" },
    { vehicleId: "rv-01", x:  2,  y: 0.3, z:  8, action: "move", duration: 3, statusLabel: "advancing" },
    { vehicleId: "rv-01", x:  2,  y: 0.3, z: -4, action: "inspect", duration: 5, statusLabel: "inspecting" },
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
      {/* body */}
      <mesh castShadow>
        <boxGeometry args={[0.7, 0.16, 0.7]} />
        <meshStandardMaterial color="#e8edf4" emissive="#aaccff" emissiveIntensity={0.4} />
      </mesh>
      {/* arms */}
      {[[-1,1],[-1,-1],[1,1],[1,-1]].map(([sx,sz], i) => (
        <mesh key={i} position={[sx * 0.28, 0, sz * 0.28]}>
          <boxGeometry args={[0.5, 0.04, 0.09]} />
          <meshStandardMaterial color="#c0c8d8" />
        </mesh>
      ))}
      {/* rotors */}
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

interface FleetProps {
  plan: MissionPlan;
  onWaypointLabel: (vehicleId: string, label: string) => void;
  onTargetDetected: (count: number) => void;
}

function Fleet({ plan, onWaypointLabel, onTargetDetected }: FleetProps) {
  const cfg = ENV_CONFIG[plan.environment];

  // per-vehicle state: current pos, waypoint index, time into waypoint
  const stateRef = useRef<Record<string, {
    pos: THREE.Vector3;
    wpIdx: number;
    elapsed: number;
  }>>({});

  // initialise on plan change
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
      stateRef.current[v.id] = { pos: startPos.clone(), wpIdx: 0, elapsed: 0 };
    });
  }

  // target positions — spread across scene bounds
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
      if (state.elapsed >= wp.duration && state.wpIdx < vWaypoints.length - 1) {
        state.wpIdx++;
        state.elapsed = 0;
        const nextWp = vWaypoints[state.wpIdx];
        onWaypointLabel(v.id, nextWp.statusLabel);
      }

      vehiclePositions.push(state.pos.clone());
    });

    // detect targets near any vehicle
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
}

export function SimCanvas({ plan, onTargetDetected, onWaypointLabel }: SimCanvasProps) {
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

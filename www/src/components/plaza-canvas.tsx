"use client";

import { useRef, useEffect } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";

// ─── Chair ───────────────────────────────────────────────────────────────────

function Chair({
  position,
  rotation,
  detected,
}: {
  position: [number, number, number];
  rotation: number;
  detected: boolean;
}) {
  const seatColor = detected ? "#ff6633" : "#e05020";
  const emissive = detected ? "#ff4400" : "#000000";
  const emissiveIntensity = detected ? 0.4 : 0;

  const legOffsets: [number, number, number][] = [
    [-0.18, 0.225, -0.18],
    [0.18, 0.225, -0.18],
    [-0.18, 0.225, 0.18],
    [0.18, 0.225, 0.18],
  ];

  return (
    <group position={position} rotation={[0, rotation, 0]}>
      <mesh position={[0, 0.45, 0]} castShadow>
        <boxGeometry args={[0.45, 0.05, 0.45]} />
        <meshStandardMaterial color={seatColor} emissive={emissive} emissiveIntensity={emissiveIntensity} />
      </mesh>
      <mesh position={[0, 0.7, -0.2]} castShadow>
        <boxGeometry args={[0.45, 0.45, 0.05]} />
        <meshStandardMaterial color={seatColor} emissive={emissive} emissiveIntensity={emissiveIntensity} />
      </mesh>
      {legOffsets.map((lp, i) => (
        <mesh key={i} position={lp} castShadow>
          <cylinderGeometry args={[0.02, 0.02, 0.45, 6]} />
          <meshStandardMaterial color="#333333" metalness={0.7} roughness={0.3} />
        </mesh>
      ))}
    </group>
  );
}

// ─── Planter ─────────────────────────────────────────────────────────────────

function Planter({ position }: { position: [number, number, number] }) {
  return (
    <group position={position}>
      <mesh position={[0, 0.25, 0]} castShadow>
        <boxGeometry args={[1.6, 0.5, 1.6]} />
        <meshStandardMaterial color="#6b5e4e" roughness={0.9} />
      </mesh>
      <mesh position={[0, 1.1, 0]} castShadow>
        <sphereGeometry args={[0.9, 12, 10]} />
        <meshStandardMaterial color="#2d6b2a" roughness={0.8} />
      </mesh>
    </group>
  );
}

// ─── Ground ───────────────────────────────────────────────────────────────────

function Ground() {
  return (
    <>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.01, 0]} receiveShadow>
        <planeGeometry args={[100, 100]} />
        <meshStandardMaterial color="#1c1e22" roughness={0.97} />
      </mesh>
      <gridHelper args={[100, 50, "#2a2d35", "#2a2d35"]} position={[0, 0.001, 0]} />
    </>
  );
}

// ─── Mission phase config ─────────────────────────────────────────────────────

type Phase = 0 | 1 | 2 | 3 | 4 | 5;

const QUAD_X = [-7, 0, 7];
const ROVER_X = [-5, 5];
const PHASE_DURATIONS = [3, 6, 5, 5, 4, 2];

function getQuadTarget(phase: Phase, idx: number): THREE.Vector3 {
  switch (phase) {
    case 0: return new THREE.Vector3(QUAD_X[idx], 6, -18);  // spawn closer
    case 1: return new THREE.Vector3(QUAD_X[idx], 5, -12);
    case 2: return new THREE.Vector3([-8, 0, 8][idx], 4, -8);
    case 3: return new THREE.Vector3([-8, 0, 8][idx], 4, -5);
    case 4: return new THREE.Vector3(QUAD_X[idx], 5, -12);
    case 5: return new THREE.Vector3(QUAD_X[idx], 6, -18);
    default: return new THREE.Vector3(QUAD_X[idx], 6, -18);
  }
}

function getRoverTarget(phase: Phase, idx: number): THREE.Vector3 {
  switch (phase) {
    case 0: return new THREE.Vector3(ROVER_X[idx], 0, -18);
    case 1: return new THREE.Vector3(ROVER_X[idx], 0, -12);
    case 2: return new THREE.Vector3([6, -6][idx], 0, -8);
    case 3: return new THREE.Vector3([-7, 7][idx], 0, -5);
    case 4: return new THREE.Vector3(ROVER_X[idx], 0, -12);
    case 5: return new THREE.Vector3(ROVER_X[idx], 0, -18);
    default: return new THREE.Vector3(ROVER_X[idx], 0, -18);
  }
}

// ─── Chair positions ──────────────────────────────────────────────────────────

const CHAIR_ENU: [number, number][] = [
  [-6, 4], [-3, 6], [-7.5, 8], [2, 5], [5, 7], [3.5, 9.5],
  [-2, 12], [1, 14], [-5, 15], [7, 13], [-9, 11], [9, 9],
];
const CHAIR_POSITIONS: [number, number, number][] = CHAIR_ENU.map(([x, y]) => [x, 0, -y]);

// ─── Main scene ───────────────────────────────────────────────────────────────

function Scene({ onChairCount }: { onChairCount?: (n: number) => void }) {
  // Quad groups
  const q0 = useRef<THREE.Group>(null);
  const q1 = useRef<THREE.Group>(null);
  const q2 = useRef<THREE.Group>(null);
  // Rover groups
  const rv0 = useRef<THREE.Group>(null);
  const rv1 = useRef<THREE.Group>(null);
  // Quad rotor sets
  const qr0 = useRef<THREE.Mesh[]>([]);
  const qr1 = useRef<THREE.Mesh[]>([]);
  const qr2 = useRef<THREE.Mesh[]>([]);

  const quadPos = useRef([
    new THREE.Vector3(-7, 6, -18),
    new THREE.Vector3(0, 6, -18),
    new THREE.Vector3(7, 6, -18),
  ]);
  const roverPos = useRef([
    new THREE.Vector3(-5, 0, -18),
    new THREE.Vector3(5, 0, -18),
  ]);

  const phase = useRef<Phase>(0);
  const phaseTime = useRef(0);
  const detectedChairs = useRef(new Set<number>());
  const lastCount = useRef(-1);
  const onChairCountRef = useRef(onChairCount);
  useEffect(() => { onChairCountRef.current = onChairCount; }, [onChairCount]);

  // Detected state array for re-render
  const detectedArr = useRef<boolean[]>(new Array(12).fill(false));

  useFrame((_, delta) => {
    phaseTime.current += delta;
    if (phaseTime.current >= PHASE_DURATIONS[phase.current]) {
      phaseTime.current = 0;
      phase.current = ((phase.current + 1) % 6) as Phase;
      if (phase.current === 0) detectedChairs.current = new Set();
    }

    const quads = [q0, q1, q2];
    const rovers = [rv0, rv1];
    const rotorSets = [qr0, qr1, qr2];

    for (let i = 0; i < 3; i++) {
      quadPos.current[i].lerp(getQuadTarget(phase.current, i), 0.05);
      if (quads[i].current) quads[i].current!.position.copy(quadPos.current[i]);
      for (const r of rotorSets[i].current) r.rotation.y += 0.3;
    }
    for (let i = 0; i < 2; i++) {
      roverPos.current[i].lerp(getRoverTarget(phase.current, i), 0.05);
      if (rovers[i].current) rovers[i].current!.position.copy(roverPos.current[i]);
    }

    // Detection
    let changed = false;
    for (let c = 0; c < 12; c++) {
      const cp = CHAIR_POSITIONS[c];
      for (let q = 0; q < 3; q++) {
        const qp = quadPos.current[q];
        const dx = qp.x - cp[0];
        const dz = qp.z - cp[2];
        if (Math.sqrt(dx * dx + dz * dz) < 5 && !detectedChairs.current.has(c)) {
          detectedChairs.current.add(c);
          detectedArr.current[c] = true;
          changed = true;
        }
      }
    }
    const count = detectedChairs.current.size;
    if (count !== lastCount.current) {
      lastCount.current = count;
      onChairCountRef.current?.(count);
    }
    void changed; // suppress warning
  });

  // Vehicles scaled 2× for visibility
  const armPositions: [number, number, number, number][] = [
    [0.24, 0, 0.24, Math.PI / 4],
    [-0.24, 0, 0.24, -Math.PI / 4],
    [0.24, 0, -0.24, -Math.PI / 4],
    [-0.24, 0, -0.24, Math.PI / 4],
  ];
  const rotorOffsets: [number, number, number][] = [
    [0.34, 0.03, 0.34],
    [-0.34, 0.03, 0.34],
    [0.34, 0.03, -0.34],
    [-0.34, 0.03, -0.34],
  ];
  const wheelPositions: [number, number, number][] = [
    [-0.44, 0.14, 0.42],
    [0.44, 0.14, 0.42],
    [-0.44, 0.14, -0.42],
    [0.44, 0.14, -0.42],
  ];

  function QuadMesh({
    groupRef,
    rotorSetRef,
  }: {
    groupRef: React.RefObject<THREE.Group | null>;
    rotorSetRef: React.MutableRefObject<THREE.Mesh[]>;
  }) {
    return (
      <group ref={groupRef as React.RefObject<THREE.Group>}>
        {/* Body — bright white so visible against dark ground */}
        <mesh castShadow>
          <boxGeometry args={[0.6, 0.14, 0.6]} />
          <meshStandardMaterial color="#ffffff" emissive="#aaccff" emissiveIntensity={0.15} metalness={0.4} roughness={0.3} />
        </mesh>
        {armPositions.map(([x, y, z, ry], i) => (
          <mesh key={i} position={[x, y, z]} rotation={[0, ry, 0]} castShadow>
            <boxGeometry args={[0.48, 0.04, 0.08]} />
            <meshStandardMaterial color="#d0d4dc" metalness={0.5} roughness={0.4} />
          </mesh>
        ))}
        {rotorOffsets.map((pos, i) => (
          <mesh
            key={i}
            position={pos}
            ref={(el) => {
              if (el) rotorSetRef.current[i] = el;
            }}
          >
            <cylinderGeometry args={[0.18, 0.18, 0.02, 8]} />
            <meshStandardMaterial color="#666" metalness={0.6} roughness={0.4} transparent opacity={0.75} />
          </mesh>
        ))}
        {/* Nav light */}
        <mesh position={[0, 0, 0.32]}>
          <sphereGeometry args={[0.06, 6, 6]} />
          <meshStandardMaterial color="#00ff88" emissive="#00ff88" emissiveIntensity={1.5} />
        </mesh>
      </group>
    );
  }

  function RoverMesh({ groupRef }: { groupRef: React.RefObject<THREE.Group | null> }) {
    return (
      <group ref={groupRef as React.RefObject<THREE.Group>}>
        {/* Body — slightly lighter grey so it reads against dark ground */}
        <mesh position={[0, 0.175, 0]} castShadow>
          <boxGeometry args={[0.88, 0.35, 1.3]} />
          <meshStandardMaterial color="#3a3f4a" metalness={0.4} roughness={0.6} />
        </mesh>
        {/* Sensor dome — bright blue glow */}
        <mesh position={[0, 0.46, 0]}>
          <sphereGeometry args={[0.17, 10, 8]} />
          <meshStandardMaterial color="#1a9fff" emissive="#0088ff" emissiveIntensity={0.6} transparent opacity={0.9} />
        </mesh>
        {wheelPositions.map((wp, i) => (
          <mesh key={i} position={wp} rotation={[0, 0, Math.PI / 2]} castShadow>
            <cylinderGeometry args={[0.14, 0.14, 0.12, 10]} />
            <meshStandardMaterial color="#1a1c20" roughness={0.9} />
          </mesh>
        ))}
      </group>
    );
  }

  return (
    <>
      <fog attach="fog" args={["#0d1117", 60, 120]} />
      <ambientLight intensity={0.5} />
      <directionalLight position={[10, 20, 5]} intensity={1.2} color="#fff8f0" castShadow shadow-mapSize={[1024, 1024] as unknown as THREE.Vector2} />
      <directionalLight position={[-8, 8, -5]} intensity={0.3} color="#a0c0ff" />

      <Ground />

      {CHAIR_POSITIONS.map((pos, i) => (
        <Chair
          key={i}
          position={pos}
          rotation={(i * 37 * Math.PI) / 180}
          detected={detectedArr.current[i]}
        />
      ))}

      {(
        [[-14, 0, -10], [14, 0, -10], [0, 0, -22]] as [number, number, number][]
      ).map((pos, i) => (
        <Planter key={i} position={pos} />
      ))}

      <QuadMesh groupRef={q0} rotorSetRef={qr0} />
      <QuadMesh groupRef={q1} rotorSetRef={qr1} />
      <QuadMesh groupRef={q2} rotorSetRef={qr2} />
      <RoverMesh groupRef={rv0} />
      <RoverMesh groupRef={rv1} />

      <OrbitControls
        autoRotate
        autoRotateSpeed={0.4}
        enableZoom={false}
        enablePan={false}
        maxPolarAngle={Math.PI / 2.3}
        minPolarAngle={0.4}
        target={new THREE.Vector3(0, 0, -9)}
      />
    </>
  );
}

// ─── Camera ───────────────────────────────────────────────────────────────────

function CameraSetup() {
  const { camera } = useThree();
  useEffect(() => {
    camera.position.set(0, 20, 18);
    camera.lookAt(0, 0, -9);
  }, [camera]);
  return null;
}

// ─── Export ───────────────────────────────────────────────────────────────────

export function PlazaCanvas({ onChairCount }: { onChairCount?: (n: number) => void }) {
  return (
    <Canvas
      shadows
      gl={{ antialias: true }}
      camera={{ fov: 50, position: [0, 20, 18] }}
      style={{ background: "#0d1117" }}
    >
      <CameraSetup />
      <Scene onChairCount={onChairCount} />
    </Canvas>
  );
}

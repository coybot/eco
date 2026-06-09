"use client";

import { Canvas } from "@react-three/fiber";
import { useGLTF, OrbitControls, Environment, Grid } from "@react-three/drei";
import { Suspense } from "react";

// Point useGLTF at Google's Draco CDN decoder (required for Draco-compressed GLBs)
useGLTF.setDecoderPath("https://www.gstatic.com/draco/versioned/decoders/1.5.7/");

function Neighbourhood() {
  const { scene } = useGLTF("/models/neighbourhood.glb");
  return <primitive object={scene} />;
}

export default function Scene() {
  return (
    <Canvas
      camera={{ position: [0, 22, 32], fov: 55 }}
      shadows
    >
      <ambientLight intensity={2.5} />
      <directionalLight position={[15, 30, 15]} intensity={3} castShadow />
      <directionalLight position={[-15, 20, -10]} intensity={1.5} />

      <Suspense fallback={null}>
        <Neighbourhood />
        <Environment preset="city" background />
      </Suspense>

      <OrbitControls
        target={[2, 3, -2]}
        minDistance={5}
        maxDistance={80}
        autoRotate
        autoRotateSpeed={0.5}
      />
    </Canvas>
  );
}

useGLTF.preload("/models/neighbourhood.glb");

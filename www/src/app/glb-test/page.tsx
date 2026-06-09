"use client";
import dynamic from "next/dynamic";

const Scene = dynamic(() => import("./scene"), { ssr: false });

export default function GlbTestPage() {
  return (
    <div className="w-screen h-screen bg-black">
      <Scene />
    </div>
  );
}

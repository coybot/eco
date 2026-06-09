"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import dynamic from "next/dynamic";
import { motion, AnimatePresence } from "framer-motion";
import type { MissionPlan, EnvironmentType, Vehicle } from "@/lib/mission-types";
import { ENV_CONFIG } from "@/lib/mission-types";

const SimCanvas = dynamic(
  () => import("@/components/sim-canvas").then((m) => ({ default: m.SimCanvas })),
  {
    ssr: false,
    loading: () => (
      <div className="w-full h-full bg-[#0d1117] animate-pulse rounded-lg flex items-center justify-center">
        <span className="text-white/20 text-sm font-mono">initializing simulation…</span>
      </div>
    ),
  }
);

let _defaultPlan: MissionPlan | null = null;
async function getDefaultPlan(): Promise<MissionPlan> {
  if (_defaultPlan) return _defaultPlan;
  const mod = await import("@/components/sim-canvas");
  _defaultPlan = mod.DEFAULT_PLAN;
  return _defaultPlan;
}

function statusColor(s: string) {
  if (!s || s === "standby" || s === "mission ✓") return "text-white/40";
  if (s.includes("scan") || s.includes("sweep")) return "text-green-400";
  if (s.includes("inspect") || s.includes("rendez") || s.includes("find")) return "text-blue-400";
  if (s.includes("return") || s.includes("report")) return "text-yellow-400";
  if (s.includes("hover") || s.includes("hold")) return "text-purple-400";
  return "text-white/70";
}

// ── Number stepper ────────────────────────────────────────────────────────────
function Stepper({
  label, value, onChange, icon,
}: {
  label: string; value: number; onChange: (n: number) => void; icon: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] text-white/40 uppercase tracking-widest font-mono">{label}</span>
      <div className="flex items-center gap-2">
        <span className="text-white/50 text-base">{icon}</span>
        <button
          onClick={() => onChange(Math.max(0, value - 1))}
          className="w-6 h-6 rounded bg-white/8 hover:bg-white/15 text-white/60 text-sm leading-none flex items-center justify-center transition-colors"
        >−</button>
        <span className="text-white font-mono text-sm w-4 text-center">{value}</span>
        <button
          onClick={() => onChange(Math.min(10, value + 1))}
          className="w-6 h-6 rounded bg-white/8 hover:bg-white/15 text-white/60 text-sm leading-none flex items-center justify-center transition-colors"
        >+</button>
      </div>
    </div>
  );
}

// ── Env picker ────────────────────────────────────────────────────────────────
function EnvPicker({
  value, onChange,
}: { value: EnvironmentType; onChange: (e: EnvironmentType) => void }) {
  const envs = Object.entries(ENV_CONFIG) as [EnvironmentType, typeof ENV_CONFIG[EnvironmentType]][];
  return (
    <div className="flex gap-2">
      {envs.map(([key, cfg]) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          className={`px-3 py-1.5 rounded-lg text-xs font-mono border transition-colors ${
            value === key
              ? "border-white/50 bg-white/10 text-white"
              : "border-white/10 bg-transparent text-white/40 hover:text-white/60 hover:border-white/25"
          }`}
        >
          {cfg.label}
        </button>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

export function PlazaSimSection() {
  const [plan, setPlan] = useState<MissionPlan | null>(null);
  const [targetCount, setTargetCount] = useState(0);
  const [displayCount, setDisplayCount] = useState(0);
  const [vehicleLabels, setVehicleLabels] = useState<Record<string, string>>({});
  const [reportLineIdx, setReportLineIdx] = useState(0);

  // controls
  const [env, setEnv] = useState<EnvironmentType>("city");
  const [nQuads, setNQuads] = useState(2);
  const [nRovers, setNRovers] = useState(1);
  const [instruction, setInstruction] = useState("");
  const [isPlanning, setIsPlanning] = useState(false);
  const [planError, setPlanError] = useState("");

  // Load default plan on mount
  useEffect(() => {
    getDefaultPlan().then((p) => {
      setPlan(p);
      const labels: Record<string, string> = {};
      p.vehicles.forEach((v) => { labels[v.id] = "standby"; });
      setVehicleLabels(labels);
    });
  }, []);

  // Count-up animation
  useEffect(() => {
    if (displayCount >= targetCount) return;
    const t = setTimeout(() => setDisplayCount((c) => Math.min(c + 1, targetCount)), 90);
    return () => clearTimeout(t);
  }, [displayCount, targetCount]);

  // Advance report line
  useEffect(() => {
    if (!plan?.reportLines.length) return;
    const t = setInterval(() => {
      setReportLineIdx((i) => Math.min(i + 1, (plan?.reportLines.length ?? 1) - 1));
    }, 5000);
    return () => clearInterval(t);
  }, [plan]);

  const onTargetDetected = useCallback((count: number) => setTargetCount(count), []);
  const onWaypointLabel = useCallback((vehicleId: string, label: string) => {
    setVehicleLabels((prev) => ({ ...prev, [vehicleId]: label }));
  }, []);

  const fly = async (overrideInstruction?: string) => {
    const text = (overrideInstruction ?? instruction).trim();
    if (isPlanning) return;
    setIsPlanning(true);
    setPlanError("");
    setTargetCount(0);
    setDisplayCount(0);
    setReportLineIdx(0);
    try {
      const res = await fetch("/api/plan-mission", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction: text, environment: env, quadcopters: nQuads, rovers: nRovers }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const newPlan: MissionPlan = await res.json();
      setPlan(newPlan);
      const labels: Record<string, string> = {};
      newPlan.vehicles.forEach((v) => { labels[v.id] = "standby"; });
      setVehicleLabels(labels);
    } catch {
      setPlanError("Couldn't reach mission planner — try again.");
    } finally {
      setIsPlanning(false);
    }
  };

  const vehicles: Vehicle[] = plan?.vehicles ?? [];
  const totalTargets = plan?.targetCount ?? 0;
  const currentReportLine = plan?.reportLines?.[reportLineIdx] ?? "";
  const missionTitle = plan?.missionTitle ?? "Fleet demo";
  const targetType = plan?.targetType ?? "object";

  return (
    <section className="py-20 bg-background overflow-hidden">
      <div className="container mx-auto px-4 sm:px-6 lg:px-8 max-w-6xl">

        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="mb-10 text-center"
        >
          <h2 className="text-3xl sm:text-4xl font-bold mb-4">
            Describe a mission. Watch it fly.
          </h2>
          <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
            Claude plans the mission in real time — environment, fleet, waypoints — and the sim executes it.
          </p>
        </motion.div>

        {/* Controls row */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.1 }}
          className="mb-4"
        >
          {/* Environment + fleet pickers */}
          <div className="flex flex-wrap items-end gap-6 mb-4">
            <div className="flex flex-col gap-1">
              <span className="text-[10px] text-white/40 uppercase tracking-widest font-mono">Environment</span>
              <EnvPicker value={env} onChange={setEnv} />
            </div>

            <Stepper
              label="Quadcopters"
              value={nQuads}
              onChange={setNQuads}
              icon={
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="3"/><path d="M5 5l2 2M17 5l-2 2M5 19l2-2M17 19l-2-2"/>
                  <line x1="2" y1="2" x2="6" y2="6"/><line x1="18" y1="2" x2="22" y2="6"/>
                  <line x1="2" y1="22" x2="6" y2="18"/><line x1="18" y1="22" x2="22" y2="18"/>
                </svg>
              }
            />

            <Stepper
              label="Rovers"
              value={nRovers}
              onChange={setNRovers}
              icon={
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="3" y="8" width="18" height="9" rx="2"/>
                  <circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>
                  <path d="M7 8V5h10v3"/>
                  <circle cx="12" cy="13" r="1.5"/>
                </svg>
              }
            />
          </div>

          {/* Instruction input */}
          <form
            onSubmit={(e) => { e.preventDefault(); fly(); }}
            className="flex gap-2 items-start"
          >
            <div className="flex-1 relative">
              <input
                type="text"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder={`Give the fleet an instruction — e.g. "sweep the area and count all cars"`}
                disabled={isPlanning}
                className="w-full bg-white/5 border border-white/15 rounded-lg px-4 py-3 text-sm text-white placeholder:text-white/30 focus:outline-none focus:border-white/40 transition-colors disabled:opacity-50 font-mono"
              />
              {planError && (
                <p className="absolute -bottom-5 left-0 text-xs text-red-400">{planError}</p>
              )}
            </div>
            <button
              type="submit"
              disabled={isPlanning || (nQuads + nRovers === 0)}
              className="px-5 py-3 bg-white text-black text-sm font-semibold rounded-lg hover:bg-white/90 active:bg-white/80 transition-colors disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
            >
              {isPlanning ? (
                <span className="flex items-center gap-2">
                  <span className="inline-block h-3.5 w-3.5 rounded-full border-2 border-black/30 border-t-black animate-spin" />
                  Planning…
                </span>
              ) : "Fly mission"}
            </button>
          </form>
        </motion.div>

        {/* Canvas */}
        <motion.div
          initial={{ opacity: 0, scale: 0.97 }}
          whileInView={{ opacity: 1, scale: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.7, delay: 0.15 }}
          className="relative h-[520px] rounded-xl overflow-hidden border border-white/10 shadow-2xl"
        >
          {plan && (
            <SimCanvas
              plan={plan}
              onTargetDetected={onTargetDetected}
              onWaypointLabel={onWaypointLabel}
            />
          )}

          {/* Planning overlay */}
          <AnimatePresence>
            {isPlanning && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="absolute inset-0 bg-black/70 backdrop-blur-sm flex flex-col items-center justify-center gap-4 z-10"
              >
                <div className="h-8 w-8 rounded-full border-2 border-white/20 border-t-white animate-spin" />
                <p className="text-white/60 text-sm font-mono">Claude is planning the mission…</p>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Status overlay — top right */}
          <div className="absolute top-4 right-4 pointer-events-none z-10">
            <div className="bg-black/70 backdrop-blur-md border border-white/10 rounded-lg p-4 text-xs font-mono w-56">
              <div className="flex items-center gap-2 mb-3 pb-2 border-b border-white/10">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
                </span>
                <span className="text-green-400 font-semibold tracking-widest text-[10px] uppercase truncate">
                  {missionTitle}
                </span>
              </div>

              <div className="space-y-1.5 mb-3 min-h-[40px]">
                {vehicles.length === 0 ? (
                  <div className="text-white/20 text-[10px]">Awaiting fleet…</div>
                ) : (
                  vehicles.map((v) => (
                    <div key={v.id} className="flex items-center justify-between gap-2">
                      <span className="text-white/50">{v.label}</span>
                      <span className={`${statusColor(vehicleLabels[v.id] ?? "standby")} text-right truncate max-w-[120px]`}>
                        ▸ {vehicleLabels[v.id] ?? "standby"}
                      </span>
                    </div>
                  ))
                )}
              </div>

              {totalTargets > 0 && (
                <div className="border-t border-white/10 pt-2">
                  <div className="flex items-center justify-between">
                    <span className="text-white/40 text-[10px] tracking-widest uppercase">
                      {targetType}s found
                    </span>
                    <span className="text-white font-bold text-sm">
                      {displayCount}
                      <span className="text-white/30 font-normal"> / {totalTargets}</span>
                    </span>
                  </div>
                  <div className="mt-1.5 h-1 bg-white/10 rounded-full overflow-hidden">
                    <motion.div
                      className="h-full bg-gradient-to-r from-orange-500 to-amber-400 rounded-full"
                      animate={{ width: `${(displayCount / totalTargets) * 100}%` }}
                      transition={{ duration: 0.4 }}
                    />
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Report line — bottom */}
          <div className="absolute bottom-4 left-4 right-4 pointer-events-none z-10">
            <AnimatePresence mode="wait">
              <motion.div
                key={currentReportLine}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.4 }}
                className="inline-block bg-black/60 backdrop-blur-sm border border-white/10 rounded-md px-3 py-1.5"
              >
                <span className="text-white/60 text-[11px] font-mono">
                  {currentReportLine || `Mission: ${missionTitle}`}
                </span>
              </motion.div>
            </AnimatePresence>
          </div>
        </motion.div>

        {/* Sim footer */}
        <motion.p
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.4 }}
          className="mt-4 text-center text-xs text-white/30"
        >
          Want a higher-fidelity sim?{" "}
          <a
            href="https://github.com/astral-us/eco/tree/main/drone/sim/godot"
            target="_blank"
            rel="noopener noreferrer"
            className="text-white/50 hover:text-white/80 underline underline-offset-2 transition-colors"
          >
            Godot-based
          </a>{" "}
          or{" "}
          <a
            href="https://github.com/astral-us/eco/tree/main/drone/sim/ishmael"
            target="_blank"
            rel="noopener noreferrer"
            className="text-white/50 hover:text-white/80 underline underline-offset-2 transition-colors"
          >
            Isaac Sim–based
          </a>{" "}
          versions are available on GitHub.
        </motion.p>
      </div>
    </section>
  );
}

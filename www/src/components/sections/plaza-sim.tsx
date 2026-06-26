"use client";

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
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

// ── Message type ──────────────────────────────────────────────────────────────
interface ChatMsg {
  id: string;
  sender: "user" | "dispatch" | string;
  label: string;
  text: string;
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

  // chat + mission state
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [missionSent, setMissionSent] = useState(false);
  const [selectedVehicleIds, setSelectedVehicleIds] = useState<string[]>([]);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const planRef = useRef<MissionPlan | null>(null);
  const msgIdRef = useRef(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const pipCanvasesRef = useRef<Map<string, HTMLCanvasElement>>(new Map());
  const simCanvasWrapperRef = useRef<HTMLDivElement>(null);
  const prevMissionCompleteRef = useRef(false);

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)');
    setIsMobile(mq.matches);
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  const toggleFullscreen = () => {
    const el = simCanvasWrapperRef.current;
    if (!el) return;
    if (!document.fullscreenElement) {
      el.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  };

  useEffect(() => {
    const onFsChange = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', onFsChange);
    return () => document.removeEventListener('fullscreenchange', onFsChange);
  }, []);
  const planVersionRef = useRef(0);

  useEffect(() => { planRef.current = plan; }, [plan]);

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

  // Scroll the chat container (not the page) when new messages arrive
  useEffect(() => {
    const el = messagesContainerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  const onTargetDetected = useCallback((count: number) => setTargetCount(count), []);

  const onWaypointLabel = useCallback((vehicleId: string, label: string) => {
    setVehicleLabels((prev) => {
      if (prev[vehicleId] === label) return prev;
      return { ...prev, [vehicleId]: label };
    });
    const vLabel = planRef.current?.vehicles.find((v) => v.id === vehicleId)?.label ?? vehicleId;
    setMessages((prevMsgs) => {
      const lastFromDrone = [...prevMsgs].reverse().find((m) => m.sender === vehicleId);
      if (lastFromDrone?.text === label) return prevMsgs;
      return [
        ...prevMsgs,
        { id: `msg-${++msgIdRef.current}`, sender: vehicleId, label: vLabel, text: label },
      ];
    });
  }, []);

  const fly = async (overrideInstruction?: string) => {
    const text = (overrideInstruction ?? instruction).trim();
    if (isPlanning) return;
    setIsPlanning(true);
    setPlanError("");
    setTargetCount(0);
    setDisplayCount(0);
    setReportLineIdx(0);
    setMissionSent(true);
    setSelectedVehicleIds([]);
    msgIdRef.current = 0;
    prevMissionCompleteRef.current = false;
    setMessages([
      { id: `msg-${++msgIdRef.current}`, sender: "user", label: "You", text: text || "Search the area and report" },
      { id: `msg-${++msgIdRef.current}`, sender: "dispatch", label: "Central Dispatch", text: "Analyzing environment, assembling fleet…" },
    ]);

    // Auto-enter fullscreen and select first vehicle for camera view
    setTimeout(() => {
      const el = simCanvasWrapperRef.current;
      if (el && !document.fullscreenElement) {
        el.requestFullscreen().catch(() => {});
      }
    }, 100);

    // Capture the current 3D scene so the AI can see what's actually there
    const glCanvas = simCanvasWrapperRef.current?.querySelector('canvas');
    const sceneImage = glCanvas ? glCanvas.toDataURL('image/jpeg', 0.7).split(',')[1] : undefined;

    try {
      const res = await fetch("/api/plan-mission", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction: text, environment: env, quadcopters: nQuads, rovers: nRovers, sceneImage }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const newPlan: MissionPlan = await res.json();
      // Clamp waypoint durations so the sim never runs unrealistically fast
      newPlan.waypoints = newPlan.waypoints.map(wp => ({ ...wp, duration: Math.max(wp.duration, 10) }));
      newPlan.planVersion = ++planVersionRef.current;
      setPlan(newPlan);
      const labels: Record<string, string> = {};
      newPlan.vehicles.forEach((v) => { labels[v.id] = "standby"; });
      setVehicleLabels(labels);
      // Auto-select all vehicles so all cameras show simultaneously
      setSelectedVehicleIds(newPlan.vehicles.map(v => v.id));
      setMessages((prev) => [
        ...prev,
        {
          id: `msg-${++msgIdRef.current}`,
          sender: "dispatch",
          label: "Central Dispatch",
          text: `${newPlan.missionTitle} — ${newPlan.vehicles.length} unit${newPlan.vehicles.length !== 1 ? "s" : ""} deployed`,
        },
        ...newPlan.vehicles.map((v) => ({
          id: `msg-${++msgIdRef.current}`,
          sender: "dispatch",
          label: "Central Dispatch",
          text: `${v.label}: proceeding to first waypoint`,
        })),
      ]);
    } catch {
      setPlanError("Couldn't reach mission planner — try again.");
    } finally {
      setIsPlanning(false);
    }
  };

  const vehicles: Vehicle[] = plan?.vehicles ?? [];
  const totalTargets = plan?.targetCount ?? 0;
  const targetType = plan?.targetType ?? "object";
  const missionTitle = plan?.missionTitle ?? "Fleet demo";

  // Mission complete: all vehicles report "mission ✓"
  const missionComplete = useMemo(() => {
    if (!missionSent || vehicles.length === 0) return false;
    return vehicles.every((v) => (vehicleLabels[v.id] ?? "") === "mission ✓");
  }, [missionSent, vehicles, vehicleLabels]);

  // Exit fullscreen when mission completes
  useEffect(() => {
    if (missionComplete && document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    }
  }, [missionComplete]);

  // Dispatch final count message when all units reach mission ✓
  useEffect(() => {
    if (missionComplete && !prevMissionCompleteRef.current && missionSent) {
      prevMissionCompleteRef.current = true;
      const n = planRef.current?.targetCount ?? 0;
      const t = planRef.current?.targetType ?? 'object';
      const needsPlural = n !== 1 && !t.endsWith('s');
      const text = n > 0
        ? `Mission complete — ${n} ${t}${needsPlural ? 's' : ''} identified`
        : 'Mission complete — all units returned to base';
      setMessages((prev) => [
        ...prev,
        { id: `msg-${++msgIdRef.current}`, sender: 'dispatch', label: 'Central Dispatch', text },
      ]);
    }
    if (!missionComplete) prevMissionCompleteRef.current = false;
  }, [missionComplete, missionSent]);

  // Progress: targets found, or report line progression
  const missionProgress = useMemo(() => {
    if (!missionSent) return 0;
    if (missionComplete) return 100;
    if (totalTargets > 0 && displayCount > 0) return Math.min(95, (displayCount / totalTargets) * 100);
    if (plan && plan.reportLines.length > 1) return Math.min(85, (reportLineIdx / (plan.reportLines.length - 1)) * 80);
    return 5;
  }, [missionSent, missionComplete, totalTargets, displayCount, plan, reportLineIdx]);

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
            Natural language mission control, try it in our simulator
          </h2>
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

        {/* 3-column sim layout */}
        <motion.div
          initial={{ opacity: 0, scale: 0.97 }}
          whileInView={{ opacity: 1, scale: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.7, delay: 0.15 }}
          className="flex flex-col md:flex-row gap-3 md:h-[400px] lg:h-[520px]"
        >
          {/* LEFT: Chat / dispatch feed — hidden on mobile */}
          <div className="hidden md:flex w-44 lg:w-56 shrink-0 flex-col bg-[#0d1117] border border-white/10 rounded-xl overflow-hidden">
            {/* Mission progress bar */}
            {missionSent && (
              <div className="px-3 pt-2.5 pb-2 border-b border-white/10 shrink-0">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-[10px] text-white/40 uppercase tracking-widest font-mono">Mission</span>
                  <span className={`text-[10px] font-semibold font-mono ${
                    missionComplete ? "text-green-400" : "text-amber-400"
                  }`}>
                    {missionComplete ? "✓ Complete" : isPlanning ? "Planning…" : `${Math.round(missionProgress)}%`}
                  </span>
                </div>
                <div className="h-[2px] bg-white/10 rounded-full overflow-hidden">
                  {isPlanning ? (
                    <div className="h-full w-full bg-amber-500/40 animate-pulse" />
                  ) : (
                    <div
                      className={`h-full rounded-full transition-all duration-700 ${
                        missionComplete ? "bg-green-500" : "bg-amber-500"
                      }`}
                      style={{ width: `${missionProgress}%` }}
                    />
                  )}
                </div>
              </div>
            )}

            {/* Messages feed */}
            <div ref={messagesContainerRef} className="flex-1 overflow-y-auto p-3 flex flex-col gap-3 min-h-0">
              {messages.length === 0 ? (
                <p className="text-white/20 m-auto text-center text-[11px] font-mono leading-relaxed">
                  Dispatch and drone<br />reports appear here.
                </p>
              ) : (
                messages.map((m) => (
                  <div key={m.id} className="flex flex-col gap-0.5">
                    <span className={`text-[10px] font-medium ${
                      m.sender === "user"
                        ? "text-amber-400"
                        : m.sender === "dispatch"
                        ? "text-green-400"
                        : "text-blue-400"
                    }`}>
                      {m.label}
                    </span>
                    <span className="text-white/60 text-[11px] font-mono leading-snug">{m.text}</span>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* CENTER: Canvas */}
          <div ref={simCanvasWrapperRef} className="relative h-64 sm:h-80 md:h-auto md:flex-1 min-w-0 rounded-xl overflow-hidden border border-white/10 shadow-2xl">
            {plan && (
              <SimCanvas
                plan={plan}
                onTargetDetected={onTargetDetected}
                onWaypointLabel={onWaypointLabel}
                missionActive={missionSent && !missionComplete}
                selectedVehicleIds={selectedVehicleIds}
                pipCanvasesRef={pipCanvasesRef}
                isMobile={isMobile}
              />
            )}

            {/* Fullscreen toggle */}
            <button
              onClick={toggleFullscreen}
              className="absolute top-3 right-3 z-20 p-1.5 rounded-lg bg-black/40 hover:bg-black/70 text-white/60 hover:text-white transition-all backdrop-blur-sm"
              title={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
            >
              {isFullscreen ? (
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <path d="M6 2H2v4M10 2h4v4M6 14H2v-4M10 14h4v-4" />
                </svg>
              ) : (
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" />
                </svg>
              )}
            </button>

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
                  <p className="text-white/60 text-sm font-mono">Astral is planning the mission…</p>
                </motion.div>
              )}
            </AnimatePresence>

            {/* PiP cameras — one per selected vehicle, stacked bottom-left */}
            {selectedVehicleIds.length > 0 && (
              <div className="absolute bottom-3 left-3 flex flex-col gap-1.5 z-10 max-h-[calc(100%-1.5rem)] overflow-y-auto">
                {selectedVehicleIds.map(vid => {
                  const vehicle = vehicles.find(v => v.id === vid);
                  if (!vehicle) return null;
                  return (
                    <div key={vid} className="w-44 rounded-xl overflow-hidden border border-white/20 bg-black shadow-2xl shrink-0">
                      <div className="flex items-center justify-between px-2.5 pt-1.5 pb-1.5 border-b border-white/10">
                        <div className="flex items-center gap-1.5">
                          <div className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                          <span className="text-[10px] text-white/70 font-mono font-medium truncate">
                            {vehicle.label} · CAM
                          </span>
                        </div>
                        <button
                          onClick={() => setSelectedVehicleIds(prev => prev.filter(id => id !== vid))}
                          className="text-white/40 hover:text-white/80 transition-colors ml-2 shrink-0"
                        >
                          <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor">
                            <path d="M1 1l8 8M9 1l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                          </svg>
                        </button>
                      </div>
                      <div className="relative bg-[#050d18]">
                        <canvas
                          ref={el => {
                            if (el) pipCanvasesRef.current.set(vid, el);
                            else pipCanvasesRef.current.delete(vid);
                          }}
                          width={192}
                          height={108}
                          className="w-full block"
                        />
                        <div className="absolute inset-0 pointer-events-none">
                          <div className="absolute inset-0 bg-[repeating-linear-gradient(0deg,transparent,transparent_3px,rgba(0,0,0,0.07)_3px,rgba(0,0,0,0.07)_4px)]" />
                          <div className="absolute inset-2">
                            <div className="absolute top-0 left-0 w-3 h-3 border-t border-l border-amber-400/50" />
                            <div className="absolute top-0 right-0 w-3 h-3 border-t border-r border-amber-400/50" />
                            <div className="absolute bottom-0 left-0 w-3 h-3 border-b border-l border-amber-400/50" />
                            <div className="absolute bottom-0 right-0 w-3 h-3 border-b border-r border-amber-400/50" />
                          </div>
                          <div className="absolute bottom-1.5 left-0 right-0 flex justify-center">
                            <span className={`text-[9px] font-mono uppercase tracking-wider ${statusColor(vehicleLabels[vid] ?? "standby")}`}>
                              {vehicleLabels[vid] ?? "standby"}
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

          </div>

          {/* RIGHT: Drone list — hidden on mobile */}
          <div className="hidden md:flex w-36 lg:w-44 shrink-0 flex-col gap-2 pt-0.5">
            <span className="text-[10px] text-white/40 uppercase tracking-widest font-mono px-1">Fleet</span>

            {vehicles.length === 0 ? (
              <div className="text-white/20 text-[10px] font-mono px-1">Awaiting fleet…</div>
            ) : (
              vehicles.map((v) => {
                const isSelected = selectedVehicleIds.includes(v.id);
                const status = vehicleLabels[v.id] ?? "standby";
                return (
                  <button
                    key={v.id}
                    onClick={() => setSelectedVehicleIds(prev =>
                      prev.includes(v.id) ? prev.filter(id => id !== v.id) : [...prev, v.id]
                    )}
                    className={`rounded-lg border px-3 py-2.5 text-left text-xs font-mono transition-colors ${
                      isSelected
                        ? "border-amber-500/60 bg-amber-500/10 text-amber-400"
                        : "border-white/10 bg-white/5 text-white/60 hover:text-white/80 hover:border-white/20"
                    }`}
                  >
                    <div className="font-semibold text-[11px] truncate">{v.label}</div>
                    <div className={`text-[10px] mt-0.5 ${statusColor(status)}`}>
                      ▸ {status}
                    </div>
                  </button>
                );
              })
            )}

            {missionTitle && missionSent && (
              <div className="mt-auto border border-white/10 bg-white/5 rounded-lg px-3 py-2.5">
                <div className="text-[9px] text-white/30 uppercase tracking-widest font-mono">Mission</div>
                <div className="text-[10px] text-white/50 font-mono mt-0.5 leading-snug">{missionTitle}</div>
              </div>
            )}
          </div>
        </motion.div>

        {/* Mobile-only compact dispatch feed */}
        {isMobile && messages.length > 0 && (
          <div className="md:hidden mt-2 bg-[#0d1117] border border-white/10 rounded-xl px-3 py-2.5 flex flex-col gap-1.5 max-h-24 overflow-y-auto">
            {messages.slice(-3).map((m) => (
              <div key={m.id} className="flex items-start gap-1.5">
                <span className={`text-[10px] font-medium shrink-0 ${
                  m.sender === "user" ? "text-amber-400" : m.sender === "dispatch" ? "text-green-400" : "text-blue-400"
                }`}>{m.label}:</span>
                <span className="text-white/60 text-[10px] font-mono leading-snug">{m.text}</span>
              </div>
            ))}
          </div>
        )}

        {/* Sim footer */}
        <motion.p
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.4 }}
          className="mt-4 text-center text-xs text-white/30"
        >
          Our simulator is open source and{" "}
          <a
            href="https://github.com/astral-us/eco/tree/main/drone/sim/godot"
            target="_blank"
            rel="noopener noreferrer"
            className="text-white/50 hover:text-white/80 underline underline-offset-2 transition-colors"
          >
            available on GitHub
          </a>
          .
        </motion.p>
      </div>
    </section>
  );
}

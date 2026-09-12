"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import {
  Loader2,
  LogOut,
  Radio,
  Minus,
  Plus,
  Send,
  Plane,
  Car,
  Camera,
  PlayCircle,
  Square,
  X,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import { useAuthState, useConversationMessages } from "@/lib/operator/hooks"
import { clearSession, cognitoSignIn } from "@/lib/operator/auth"
import {
  simPrewarm,
  simStartSession,
  simEndSession,
  sendMessage,
  ApiError,
} from "@/lib/operator/api"
import { useSimStream } from "@/lib/operator/sim-hooks"
import { OPERATOR_CONFIG } from "@/lib/operator/config"
import type { SimSession, SimDrone, ChatMessage } from "@/lib/operator/types"

type Phase = "build" | "launching" | "live"
type EnvName = "office" | "plaza" | "city"
const ENVS: EnvName[] = ["office", "plaza", "city"]
const SCENE_CAM = "scene"

interface SentCommand {
  id: string
  text: string
  timestamp: number
}

type DisplayMsg =
  | { kind: "command"; id: string; text: string; timestamp: number }
  | { kind: "drone"; msg: ChatMessage; droneId: string; label: string }

// ─── Sign-in gate ─────────────────────────────

function SignInScreen() {
  const [error, setError] = useState<string | null>(null)
  const handleSignIn = () => {
    if (!OPERATOR_CONFIG.cognitoHostedUiUrl) {
      setError("Cognito configuration is not available.")
      return
    }
    cognitoSignIn()
  }
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-8 px-4 bg-black">
      <div className="flex flex-col items-center gap-4">
        <div className="flex size-20 items-center justify-center rounded-2xl bg-amber-500/10 border border-amber-500/20">
          <Radio className="size-10 text-amber-500" />
        </div>
        <div className="text-center">
          <h1 className="text-2xl font-semibold text-white">Coybot Simulator</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Sign in to run drones in the simulator
          </p>
        </div>
      </div>
      <div className="w-full max-w-sm flex flex-col gap-3">
        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}
        <button
          onClick={handleSignIn}
          className="flex h-14 items-center justify-center gap-3 rounded-2xl bg-amber-500 text-black text-sm font-medium hover:bg-amber-600 transition-colors w-full"
        >
          Sign in with Google
        </button>
      </div>
    </div>
  )
}

// ─── Fleet builder ────────────────────────────

function Stepper({
  label,
  icon,
  value,
  onChange,
  max = 4,
}: {
  label: string
  icon: React.ReactNode
  value: number
  onChange: (v: number) => void
  max?: number
}) {
  return (
    <div className="flex items-center justify-between rounded-xl border border-border bg-card/50 px-4 py-3">
      <div className="flex items-center gap-2 text-sm">
        {icon}
        {label}
      </div>
      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          size="icon"
          className="size-8"
          onClick={() => onChange(Math.max(0, value - 1))}
          disabled={value <= 0}
        >
          <Minus className="size-4" />
        </Button>
        <span className="w-6 text-center font-mono text-base">{value}</span>
        <Button
          variant="outline"
          size="icon"
          className="size-8"
          onClick={() => onChange(Math.min(max, value + 1))}
          disabled={value >= max}
        >
          <Plus className="size-4" />
        </Button>
      </div>
    </div>
  )
}

function FleetBuilder({
  counts,
  setCounts,
  env,
  setEnv,
  onLaunch,
}: {
  counts: { quadcopter: number; rover: number }
  setCounts: (c: { quadcopter: number; rover: number }) => void
  env: EnvName
  setEnv: (e: EnvName) => void
  onLaunch: () => void
}) {
  const total = counts.quadcopter + counts.rover
  return (
    <div className="mx-auto w-full max-w-md flex flex-col gap-5 py-10">
      <div>
        <h1 className="text-xl font-semibold">Build your fleet</h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Pick vehicles and an environment, then launch the simulator.
        </p>
      </div>

      <div className="flex flex-col gap-3">
        <Stepper
          label="Quadcopters"
          icon={<Plane className="size-4 text-amber-500" />}
          value={counts.quadcopter}
          onChange={(v) => setCounts({ ...counts, quadcopter: v })}
        />
        <Stepper
          label="Rovers"
          icon={<Car className="size-4 text-amber-500" />}
          value={counts.rover}
          onChange={(v) => setCounts({ ...counts, rover: v })}
        />
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-sm text-muted-foreground">Environment</span>
        <div className="grid grid-cols-3 gap-2">
          {ENVS.map((e) => (
            <button
              key={e}
              onClick={() => setEnv(e)}
              className={`rounded-xl border px-3 py-2 text-sm capitalize transition-colors ${
                env === e
                  ? "border-amber-500 bg-amber-500/10 text-amber-500"
                  : "border-border bg-card/50 text-muted-foreground hover:text-foreground"
              }`}
            >
              {e}
            </button>
          ))}
        </div>
      </div>

      <Button
        className="h-12 bg-amber-500 text-black hover:bg-amber-600"
        disabled={total === 0}
        onClick={onLaunch}
      >
        <PlayCircle className="size-5" />
        Launch {total > 0 ? `${total} drone${total > 1 ? "s" : ""}` : ""}
      </Button>
    </div>
  )
}

// ─── Message collectors (null-rendering, lift messages up) ──

function DroneMsgCollector({
  drone,
  onMessages,
}: {
  drone: SimDrone
  onMessages: (droneId: string, msgs: ChatMessage[]) => void
}) {
  const { messages } = useConversationMessages(drone.droneId, drone.conversationId, 2000)
  useEffect(() => {
    onMessages(drone.droneId, messages)
  }, [messages, drone.droneId, onMessages])
  return null
}

// ─── Mission progress bar ──────────────────────

type MissionStatus = "idle" | "running" | "complete" | "failed"

function MissionBar({ status, progress }: { status: MissionStatus; progress: number }) {
  if (status === "idle") return null
  const indeterminate = status === "running" && progress <= 5
  return (
    <div className="px-3 pt-2.5 pb-2 border-b border-border/60 shrink-0">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
          Mission
        </span>
        <span
          className={`text-[10px] font-semibold ${
            status === "complete"
              ? "text-green-400"
              : status === "failed"
              ? "text-red-400"
              : "text-amber-400"
          }`}
        >
          {status === "complete"
            ? "✓ Complete"
            : status === "failed"
            ? "✗ Failed"
            : indeterminate
            ? "In progress…"
            : `${Math.round(progress)}%`}
        </span>
      </div>
      <div className="h-[3px] bg-muted rounded-full overflow-hidden">
        {indeterminate ? (
          <div className="h-full w-full bg-amber-500/50 animate-pulse" />
        ) : (
          <div
            className={`h-full rounded-full transition-all duration-700 ${
              status === "complete"
                ? "bg-green-500"
                : status === "failed"
                ? "bg-red-500"
                : "bg-amber-500"
            }`}
            style={{ width: `${progress}%` }}
          />
        )}
      </div>
    </div>
  )
}

// ─── Single message row in fleet chat ─────────

function DisplayMessageRow({
  item,
  drones,
}: {
  item: DisplayMsg
  drones: SimDrone[]
}) {
  if (item.kind === "command") {
    const label = drones.length > 1 ? "Fleet command" : "You"
    return (
      <div className="flex flex-col items-end gap-0.5">
        <span className="text-[10px] text-muted-foreground px-1">{label}</span>
        <div className="bg-amber-500 text-black rounded-2xl rounded-tr-sm px-3 py-2 text-sm max-w-[90%] break-words">
          {item.text}
        </div>
      </div>
    )
  }

  const { msg, label } = item

  if (msg.contentType === "loading") {
    return (
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground pl-0.5">
        <Loader2 className="size-3 animate-spin shrink-0" />
        <span className="text-amber-500/70 font-medium">{label}</span>
        <span>{msg.loading ?? "working…"}</span>
      </div>
    )
  }

  if (msg.contentType === "mission_progress") {
    const mp = msg.missionProgress
    if (!mp) return null
    return (
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground px-0.5">
        <span className="text-amber-400 font-medium shrink-0">{label}</span>
        <span className="text-muted-foreground/60">·</span>
        <span>
          {mp.phase}
          {mp.detail ? ` — ${mp.detail}` : ""}
        </span>
      </div>
    )
  }

  const images =
    msg.imageOptions?.map((o) => o.url) ?? (msg.imageUrl ? [msg.imageUrl] : [])

  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] text-amber-400 font-medium px-1">{label}</span>
      <div
        className={`rounded-2xl rounded-tl-sm px-3 py-2 text-sm max-w-[90%] break-words ${
          msg.contentType === "error"
            ? "bg-destructive/15 text-destructive border border-destructive/20"
            : "bg-card border border-border"
        }`}
      >
        {msg.text && <p className="whitespace-pre-wrap">{msg.text}</p>}
        {msg.error && <p className="whitespace-pre-wrap">{msg.error}</p>}
        {images.length > 0 && (
          <div className="mt-2 grid grid-cols-2 gap-1">
            {images.map((url) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={url}
                src={url}
                alt="drone view"
                className="rounded-lg border border-border object-cover w-full h-20"
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Fleet chat panel (left) ──────────────────

function FleetChatPanel({
  session,
  sentCommands,
  droneMessages,
  onSend,
}: {
  session: { drones: SimDrone[] }
  sentCommands: SentCommand[]
  droneMessages: Map<string, ChatMessage[]>
  onSend: (text: string) => Promise<void>
}) {
  const [text, setText] = useState("")
  const [sending, setSending] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Merge display items: local commands + API drone responses (exclude user messages)
  const displayItems = useMemo<DisplayMsg[]>(() => {
    const items: DisplayMsg[] = sentCommands.map((c) => ({
      kind: "command" as const,
      ...c,
    }))
    session.drones.forEach((drone) => {
      const msgs = droneMessages.get(drone.droneId) ?? []
      msgs
        .filter((m) => m.sender !== "user")
        .forEach((m) =>
          items.push({ kind: "drone", msg: m, droneId: drone.droneId, label: drone.label })
        )
    })
    return items.sort((a, b) => {
      const ta = a.kind === "command" ? a.timestamp : a.msg.timestamp
      const tb = b.kind === "command" ? b.timestamp : b.msg.timestamp
      return ta - tb
    })
  }, [sentCommands, droneMessages, session.drones])

  // Mission status: prefer mission_progress messages from API, fall back to heuristic
  const { missionStatus, missionProgress } = useMemo<{
    missionStatus: MissionStatus
    missionProgress: number
  }>(() => {
    if (sentCommands.length === 0) return { missionStatus: "idle", missionProgress: 0 }

    const allDroneMsgs: ChatMessage[] = []
    session.drones.forEach((d) => {
      const msgs = droneMessages.get(d.droneId) ?? []
      msgs.filter((m) => m.sender !== "user").forEach((m) => allDroneMsgs.push(m))
    })

    const progressMsgs = allDroneMsgs.filter(
      (m) => m.contentType === "mission_progress" && m.missionProgress
    )
    const latest = progressMsgs.at(-1)?.missionProgress ?? null

    if (latest?.status === "complete") return { missionStatus: "complete", missionProgress: 100 }
    if (latest?.status === "failed")
      return { missionStatus: "failed", missionProgress: latest.progress ?? 0 }
    if (latest) return { missionStatus: "running", missionProgress: latest.progress ?? 0 }

    // Fallback heuristic: count non-progress drone messages
    const responseMsgs = allDroneMsgs.filter((m) => m.contentType !== "mission_progress")
    return {
      missionStatus: "running",
      missionProgress: Math.min(90, responseMsgs.length * 15),
    }
  }, [sentCommands, droneMessages, session.drones])

  // Auto-scroll to bottom when messages arrive
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }, [displayItems.length])

  const submit = async () => {
    const t = text.trim()
    if (!t || sending) return
    setSending(true)
    setText("")
    try {
      await onSend(t)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="flex flex-col w-80 shrink-0 rounded-2xl border border-border bg-card overflow-hidden">
      <MissionBar status={missionStatus} progress={missionProgress} />

      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-3 flex flex-col gap-3 min-h-0"
      >
        {displayItems.length === 0 ? (
          <p className="text-xs text-muted-foreground m-auto text-center leading-relaxed">
            Commands and drone responses appear here.
            <br />
            <span className="text-[11px] opacity-70">
              Try "search the area and report".
            </span>
          </p>
        ) : (
          displayItems.map((item, i) => (
            <DisplayMessageRow
              key={item.kind === "command" ? item.id : (item.msg.id ?? `${item.droneId}-${i}`)}
              item={item}
              drones={session.drones}
            />
          ))
        )}
      </div>

      <div className="p-3 border-t border-border flex items-end gap-2 shrink-0">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
          placeholder={
            session.drones.length > 1
              ? "Command the fleet…"
              : "Tell the drone what to do…"
          }
          rows={2}
          className="resize-none text-sm"
        />
        <Button
          className="h-10 bg-amber-500 text-black hover:bg-amber-600 shrink-0"
          onClick={submit}
          disabled={sending || !text.trim()}
        >
          {sending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Send className="size-4" />
          )}
        </Button>
      </div>
    </div>
  )
}

// ─── Live session: 3-column layout ────────────

function LiveSession({ session }: { session: SimSession }) {
  const [sentCommands, setSentCommands] = useState<SentCommand[]>([])
  const [droneMessages, setDroneMessages] = useState<Map<string, ChatMessage[]>>(new Map())
  const [pipCam, setPipCam] = useState<string | null>(null)

  // Main view: always scene. PiP: selected drone (second WS connection).
  const { videoRef, connected } = useSimStream(session.wsUrl, SCENE_CAM)
  const { videoRef: pipRef } = useSimStream(
    pipCam ? session.wsUrl : null,
    pipCam ?? ""
  )

  const handleDroneMessages = useCallback((droneId: string, msgs: ChatMessage[]) => {
    setDroneMessages((prev) => {
      const next = new Map(prev)
      next.set(droneId, msgs)
      return next
    })
  }, [])

  const sendToFleet = useCallback(
    async (text: string) => {
      const cmd: SentCommand = { id: `cmd-${Date.now()}`, text, timestamp: Date.now() }
      setSentCommands((prev) => [...prev, cmd])
      await Promise.all(
        session.drones.map((d) =>
          sendMessage(d.droneId, d.conversationId, text).catch(() => {})
        )
      )
    },
    [session.drones]
  )

  const pipLabel = useMemo(
    () => session.drones.find((d) => d.droneId === pipCam)?.label ?? "",
    [pipCam, session.drones]
  )

  return (
    <div className="flex gap-4 h-[calc(100vh-3.5rem)] py-4 overflow-hidden">
      {/* Invisible per-drone message collectors */}
      {session.drones.map((d) => (
        <DroneMsgCollector key={d.droneId} drone={d} onMessages={handleDroneMessages} />
      ))}

      {/* LEFT: Fleet chat + command box */}
      <FleetChatPanel
        session={session}
        sentCommands={sentCommands}
        droneMessages={droneMessages}
        onSend={sendToFleet}
      />

      {/* CENTER: Scene video + optional PiP */}
      <div className="relative flex-1 min-w-0 rounded-2xl border border-border bg-black overflow-hidden">
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        <video
          ref={videoRef}
          autoPlay
          muted
          playsInline
          className="h-full w-full object-contain"
        />
        {!connected && (
          <div className="absolute inset-0 flex items-center justify-center gap-2 text-muted-foreground">
            <Loader2 className="size-5 animate-spin" />
            Connecting…
          </div>
        )}
        <Badge className="absolute left-3 top-3 gap-1.5" variant="secondary">
          <span
            className={`size-1.5 rounded-full ${
              connected ? "bg-amber-500" : "bg-muted-foreground"
            }`}
          />
          Scene
        </Badge>

        {/* PiP overlay */}
        {pipCam && (
          <div className="absolute bottom-3 right-3 w-52 rounded-xl overflow-hidden border border-white/25 bg-black shadow-2xl">
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <video
              ref={pipRef}
              autoPlay
              muted
              playsInline
              className="w-full aspect-video object-contain"
            />
            <div className="absolute top-0 left-0 right-0 flex items-center justify-between px-2 pt-1 pb-3 bg-gradient-to-b from-black/70 to-transparent pointer-events-none">
              <span className="text-[11px] text-white/90 font-medium">{pipLabel}</span>
            </div>
            <button
              onClick={() => setPipCam(null)}
              className="absolute top-1.5 right-1.5 flex items-center justify-center size-5 rounded-full bg-black/50 text-white/80 hover:text-white hover:bg-black/70 transition-colors"
            >
              <X className="size-3" />
            </button>
          </div>
        )}
      </div>

      {/* RIGHT: Video feed selector (click for PiP) */}
      <div className="flex flex-col gap-2 w-52 shrink-0 pt-0.5">
        <p className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground px-1">
          Video feeds
        </p>
        {session.drones.map((d) => {
          const isPip = pipCam === d.droneId
          return (
            <button
              key={d.droneId}
              onClick={() => setPipCam((prev) => (prev === d.droneId ? null : d.droneId))}
              className={`flex items-center gap-2 rounded-lg border px-3 py-2.5 text-sm transition-colors text-left ${
                isPip
                  ? "border-amber-500 bg-amber-500/10 text-amber-500"
                  : "border-border bg-card/50 text-foreground hover:bg-card"
              }`}
            >
              {d.vehicleType === "quadcopter" ? (
                <Plane className="size-4 shrink-0" />
              ) : (
                <Car className="size-4 shrink-0" />
              )}
              <span className="flex-1 truncate">{d.label}</span>
              {isPip && (
                <span className="text-[10px] font-semibold tracking-wide">PiP</span>
              )}
            </button>
          )
        })}
        <div className="mt-1 flex items-center gap-2 rounded-lg border border-border/40 px-3 py-2.5 text-sm text-muted-foreground">
          <Camera className="size-4 shrink-0" />
          <span className="text-xs">Scene (main)</span>
        </div>
      </div>
    </div>
  )
}

// ─── Root page ────────────────────────────────

export default function SimulatorPage() {
  const router = useRouter()
  const { isAuthenticated } = useAuthState()

  const [phase, setPhase] = useState<Phase>("build")
  const [counts, setCounts] = useState({ quadcopter: 2, rover: 1 })
  const [env, setEnv] = useState<EnvName>("office")
  const [session, setSession] = useState<SimSession | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Pre-warm the EC2 sim host as soon as the page opens, so Launch is fast.
  useEffect(() => {
    if (isAuthenticated) simPrewarm()
  }, [isAuthenticated])

  const launch = async () => {
    setError(null)
    setPhase("launching")
    try {
      const s = await simStartSession(counts, env)
      setSession(s)
      setPhase("live")
    } catch (e) {
      const msg =
        e instanceof ApiError && e.status === 409
          ? "Simulator is busy — try again in a moment."
          : e instanceof Error
          ? e.message
          : "Failed to launch the simulator."
      setError(msg)
      setPhase("build")
    }
  }

  const endSession = () => {
    if (session) simEndSession(session.sessionId).catch(() => {})
    setSession(null)
    setPhase("build")
  }

  if (isAuthenticated === null) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="size-6 animate-spin text-amber-500" />
      </div>
    )
  }
  if (!isAuthenticated) return <SignInScreen />

  return (
    <div className="flex flex-col min-h-screen">
      <header className="sticky top-0 z-10 flex h-14 items-center justify-between border-b border-border bg-background/95 px-4 backdrop-blur">
        <div className="flex items-center gap-2">
          <Radio className="size-5 text-amber-500" />
          <span className="font-semibold text-sm">Coybot Simulator</span>
        </div>
        <div className="flex items-center gap-4">
          {phase === "live" && (
            <button
              onClick={endSession}
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              <Square className="size-3.5" />
              End session
            </button>
          )}
          <button
            onClick={() => {
              clearSession()
              router.replace("/operator")
            }}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <LogOut className="size-3.5" />
            Sign out
          </button>
        </div>
      </header>

      <main className={`flex-1 px-4 ${phase === "live" ? "overflow-hidden" : ""}`}>
        {error && (
          <div className="mx-auto mt-4 max-w-md rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {phase === "build" && (
          <FleetBuilder
            counts={counts}
            setCounts={setCounts}
            env={env}
            setEnv={setEnv}
            onLaunch={launch}
          />
        )}

        {phase === "launching" && (
          <div className="flex flex-col items-center justify-center gap-3 py-32 text-center">
            <Loader2 className="size-7 animate-spin text-amber-500" />
            <p className="text-sm font-medium">Launching the simulator…</p>
            <p className="text-xs text-muted-foreground max-w-xs">
              Warming the world and bringing your drones online. This is fastest a
              few seconds after the page opens.
            </p>
          </div>
        )}

        {phase === "live" && session && <LiveSession session={session} />}
      </main>
    </div>
  )
}

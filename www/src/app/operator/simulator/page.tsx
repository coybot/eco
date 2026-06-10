"use client"

import { useEffect, useMemo, useRef, useState } from "react"
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
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
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
          <h1 className="text-2xl font-semibold text-white">Astral Simulator</h1>
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

// ─── Camera list (click to switch the main view) ──

function CamList({
  drones,
  activeCam,
  onSelect,
}: {
  drones: SimDrone[]
  activeCam: string
  onSelect: (cam: string) => void
}) {
  const rows = [
    ...drones.map((d) => ({ cam: d.droneId, label: d.label, vt: d.vehicleType })),
    { cam: SCENE_CAM, label: "Scene", vt: "scene" },
  ]
  return (
    <div className="flex flex-col gap-1.5">
      {rows.map((r) => {
        const active = activeCam === r.cam
        return (
          <button
            key={r.cam}
            onClick={() => onSelect(r.cam)}
            className={`flex items-center justify-between rounded-lg border px-3 py-2 text-sm transition-colors ${
              active
                ? "border-amber-500 bg-amber-500/10 text-amber-500"
                : "border-border bg-card/50 text-foreground hover:bg-card"
            }`}
          >
            <span className="flex items-center gap-2">
              {r.vt === "quadcopter" ? (
                <Plane className="size-4" />
              ) : r.vt === "rover" ? (
                <Car className="size-4" />
              ) : (
                <Camera className="size-4" />
              )}
              {r.label}
            </span>
            {active && <span className="text-[10px] uppercase tracking-wide">Viewing</span>}
          </button>
        )
      })}
    </div>
  )
}

// ─── Command box + reply stream (per-drone autonomy path) ──

function CommandPanel({ target }: { target: SimDrone }) {
  const { messages, addOptimistic } = useConversationMessages(
    target.droneId,
    target.conversationId,
    3000
  )
  const [text, setText] = useState("")
  const [sending, setSending] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages.length])

  const send = async () => {
    const t = text.trim()
    if (!t || sending) return
    setSending(true)
    setText("")
    addOptimistic({
      id: `local-${Date.now()}`,
      sender: "user",
      contentType: "text",
      text: t,
      timestamp: Date.now(),
    })
    try {
      await sendMessage(target.droneId, target.conversationId, t)
    } catch {
      // reply polling will surface any error from the drone
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-1 pb-2 text-xs text-muted-foreground">
        Commanding <span className="text-amber-500 font-medium">{target.label}</span>
      </div>
      <div
        ref={scrollRef}
        className="flex-1 min-h-40 overflow-y-auto rounded-xl border border-border bg-card/30 p-3 flex flex-col gap-2"
      >
        {messages.length === 0 ? (
          <p className="text-xs text-muted-foreground m-auto">
            Try “take off to 2 m, look around, and tell me what you see”.
          </p>
        ) : (
          messages.map((m) => <MessageBubble key={m.id} m={m} />)
        )}
      </div>
      <div className="mt-3 flex items-end gap-2">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
          placeholder="Tell the drone what to do…"
          rows={2}
          className="resize-none"
        />
        <Button
          className="h-10 bg-amber-500 text-black hover:bg-amber-600"
          onClick={send}
          disabled={sending || !text.trim()}
        >
          {sending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
        </Button>
      </div>
    </div>
  )
}

function MessageBubble({ m }: { m: ChatMessage }) {
  const isUser = m.sender === "user"
  const images =
    m.imageOptions?.map((o) => o.url) ?? (m.imageUrl ? [m.imageUrl] : [])
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm ${
          isUser
            ? "bg-amber-500 text-black"
            : m.contentType === "error"
            ? "bg-destructive/15 text-destructive"
            : "bg-card border border-border"
        }`}
      >
        {m.text && <p className="whitespace-pre-wrap">{m.text}</p>}
        {m.error && <p className="whitespace-pre-wrap">{m.error}</p>}
        {images.length > 0 && (
          <div className="mt-2 grid grid-cols-2 gap-1.5">
            {images.map((url) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={url}
                src={url}
                alt="drone view"
                className="rounded-lg border border-border object-cover w-full h-24"
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Live session view ────────────────────────

function LiveSession({ session }: { session: SimSession }) {
  const [activeCam, setActiveCam] = useState<string>(SCENE_CAM)
  const { videoRef, connected } = useSimStream(session.wsUrl, activeCam)

  // Command target: the selected drone, or the first drone when viewing "Scene".
  const target = useMemo<SimDrone | null>(() => {
    if (activeCam !== SCENE_CAM) {
      return session.drones.find((d) => d.droneId === activeCam) ?? session.drones[0] ?? null
    }
    return session.drones[0] ?? null
  }, [activeCam, session.drones])

  const activeLabel =
    activeCam === SCENE_CAM
      ? "Scene"
      : session.drones.find((d) => d.droneId === activeCam)?.label ?? activeCam

  return (
    <div className="mx-auto w-full max-w-5xl py-6 grid gap-5 lg:grid-cols-[1fr_300px]">
      {/* Viewport + camera list */}
      <div className="flex flex-col gap-3">
        <div className="relative aspect-video w-full overflow-hidden rounded-2xl border border-border bg-black">
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
          <Badge className="absolute left-3 top-3 gap-1" variant="secondary">
            <span className={`size-1.5 rounded-full ${connected ? "bg-amber-500" : "bg-muted-foreground"}`} />
            {activeLabel}
          </Badge>
        </div>
        <CamList drones={session.drones} activeCam={activeCam} onSelect={setActiveCam} />
      </div>

      {/* Command panel */}
      <Card className="flex flex-col">
        <CardContent className="flex flex-col flex-1 pt-5">
          {target ? (
            <CommandPanel key={target.droneId} target={target} />
          ) : (
            <p className="text-sm text-muted-foreground m-auto">No drones in session.</p>
          )}
        </CardContent>
      </Card>
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
          <span className="font-semibold text-sm">Astral Simulator</span>
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

      <main className="flex-1 px-4">
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

"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import {
  BatteryMedium,
  Wifi,
  WifiOff,
  Plus,
  LogOut,
  Loader2,
  Drone,
  ChevronRight,
  Radio,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { useAuthState } from "@/lib/operator/hooks"
import { useDrones, isDroneOnline } from "@/lib/operator/hooks"
import { clearSession, cognitoSignIn } from "@/lib/operator/auth"
import type { Drone as DroneType, DroneStatus } from "@/lib/operator/types"
import { OPERATOR_CONFIG } from "@/lib/operator/config"

// ─── Sign-in screen ───────────────────────────

function SignInScreen() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSignIn = async () => {
    try {
      setError(null)
      if (!OPERATOR_CONFIG.cognitoHostedUiUrl) {
        setError("Cognito configuration is not available.")
        return
      }
      setLoading(true)
      cognitoSignIn()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed")
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-8 px-4 bg-black">
      {/* Hero */}
      <div className="flex flex-col items-center gap-4">
        <div className="flex size-20 items-center justify-center rounded-2xl bg-amber-500/10 border border-amber-500/20">
          <Radio className="size-10 text-amber-500" />
        </div>
        <div className="text-center">
          <h1 className="text-2xl font-semibold text-white">Astral Operator</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Sign in to manage your drone fleet
          </p>
        </div>
      </div>

      {/* Sign-in card */}
      <div className="w-full max-w-sm flex flex-col gap-3">
        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}
        <button
          onClick={handleSignIn}
          disabled={loading}
          className="flex h-14 items-center justify-center gap-3 rounded-2xl bg-amber-500 text-black text-sm font-medium hover:bg-amber-600 transition-colors disabled:opacity-60 disabled:pointer-events-none w-full"
        >
          {loading ? (
            <Loader2 className="size-4 animate-spin" />
          ) : null}
          Sign in
        </button>
      </div>

      <p className="text-muted-foreground text-xs text-center max-w-xs">
        Operator access is restricted to registered Astral fleet accounts.
      </p>
    </div>
  )
}

// ─── Drone card ───────────────────────────────

function DroneCard({ drone }: { drone: DroneType }) {
  const online = isDroneOnline(drone.status ?? null)
  const battery = drone.status?.battery?.remaining
  const alt = drone.status?.position?.alt

  return (
    <Link href={`/operator/drones/${drone.droneId}`}>
      <Card className="hover:border-amber-500/40 hover:bg-card/80 transition-all cursor-pointer group h-40">
        <CardContent className="flex flex-col justify-between h-full pt-6">
          {/* Top row */}
          <div className="flex items-start justify-between">
            <div className="flex flex-col gap-1">
              <span className="text-sm font-medium text-foreground leading-none">
                {drone.name}
              </span>
              <span className="text-xs text-muted-foreground font-mono">
                {drone.droneId.slice(0, 8)}…
              </span>
            </div>
            <Badge variant={online ? "default" : "secondary"} className="gap-1">
              <span
                className={`size-1.5 rounded-full ${
                  online ? "bg-black" : "bg-muted-foreground"
                }`}
              />
              {online ? "Online" : "Offline"}
            </Badge>
          </div>

          {/* Bottom row */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              {battery !== undefined && (
                <span className="flex items-center gap-1">
                  <BatteryMedium className="size-3.5" />
                  {Math.round(battery)}%
                </span>
              )}
              {alt !== undefined && (
                <span className="flex items-center gap-1">
                  <span className="text-[10px] font-medium text-muted-foreground/60">
                    ALT
                  </span>
                  {alt.toFixed(1)}m
                </span>
              )}
            </div>
            <ChevronRight className="size-4 text-muted-foreground/40 group-hover:text-amber-500 transition-colors" />
          </div>
        </CardContent>
      </Card>
    </Link>
  )
}

// ─── Drone list screen ────────────────────────

function DroneListScreen() {
  const router = useRouter()
  const { drones, loading, error, refresh } = useDrones(5000)

  const handleSignOut = () => {
    clearSession()
    router.replace("/operator")
    router.refresh()
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="size-6 animate-spin text-amber-500" />
      </div>
    )
  }

  if (error === "session_expired") {
    clearSession()
    router.replace("/operator")
    return null
  }

  return (
    <div className="flex flex-col min-h-screen">
      {/* Header */}
      <header className="sticky top-0 z-10 flex h-14 items-center justify-between border-b border-border bg-background/95 px-4 backdrop-blur">
        <div className="flex items-center gap-2">
          <Radio className="size-5 text-amber-500" />
          <span className="font-semibold text-sm">Astral Operator</span>
        </div>
        <button
          onClick={handleSignOut}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          <LogOut className="size-3.5" />
          Sign out
        </button>
      </header>

      {/* Content */}
      <main className="flex-1 p-4 max-w-2xl mx-auto w-full">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold">Fleet</h1>
            <p className="text-sm text-muted-foreground mt-0.5">
              {drones.length} drone{drones.length !== 1 ? "s" : ""} registered
            </p>
          </div>
        </div>

        {error && error !== "session_expired" && (
          <div className="mb-4 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {drones.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-4 py-24 text-center">
            <div className="flex size-16 items-center justify-center rounded-2xl bg-muted">
              <Radio className="size-8 text-muted-foreground" />
            </div>
            <div>
              <p className="text-sm font-medium">No drones registered</p>
              <p className="text-xs text-muted-foreground mt-1">
                Set up a drone using the mobile app to get started.
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {drones.map((drone) => (
              <DroneCard key={drone.droneId} drone={drone} />
            ))}
          </div>
        )}
      </main>
    </div>
  )
}

// ─── Root page ────────────────────────────────

export default function OperatorPage() {
  const { isAuthenticated } = useAuthState()

  if (isAuthenticated === null) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="size-6 animate-spin text-amber-500" />
      </div>
    )
  }

  if (!isAuthenticated) {
    return <SignInScreen />
  }

  return <DroneListScreen />
}

"use client"

import { use, useEffect, useState, useCallback, useRef } from "react"
import { useRouter } from "next/navigation"
import {
  ArrowLeft,
  Radio,
  BatteryMedium,
  BatteryLow,
  BatteryFull,
  Wifi,
  WifiOff,
  AlertTriangle,
  Loader2,
  Send,
  RotateCcw,
  Terminal,
  Settings2,
  Map,
  MessageSquare,
  Activity,
  Trash2,
  Plus,
  Minus,
  Eye,
  EyeOff,
  RefreshCw,
  ChevronDown,
  TriangleAlert,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import {
  useDroneStatus,
  useDroneLogs,
  useConversationMessages,
  usePersistedConvId,
  isDroneOnline,
  useAuthState,
} from "@/lib/operator/hooks"
import {
  getDrone,
  createConversation,
  sendMessage,
  updateDroneName,
  deleteDrone,
  getBatteryConfig,
  saveBatteryConfig,
  getWifiNetworks,
  saveWifiNetwork,
  deleteWifiNetwork,
  getVideoViewer,
  ApiError,
} from "@/lib/operator/api"
import { clearSession } from "@/lib/operator/auth"
import type {
  Drone,
  ChatMessage,
  DroneLog,
  WifiNetwork,
  BatteryConfig,
  VideoViewerResponse,
} from "@/lib/operator/types"
import { cn } from "@/lib/utils"

// ─────────────────────────────────────────────
// Telemetry / overview tab
// ─────────────────────────────────────────────

function TelemetryRow({
  label,
  value,
}: {
  label: string
  value: React.ReactNode
}) {
  return (
    <div className="flex items-center justify-between py-2.5 border-b border-border/50 last:border-0">
      <span className="text-xs text-muted-foreground uppercase tracking-wide">
        {label}
      </span>
      <span className="text-sm font-mono">{value}</span>
    </div>
  )
}

function BatteryIcon({ pct }: { pct?: number }) {
  if (!pct) return <BatteryMedium className="size-4 text-muted-foreground" />
  if (pct < 20) return <BatteryLow className="size-4 text-destructive" />
  if (pct > 70) return <BatteryFull className="size-4 text-success" />
  return <BatteryMedium className="size-4 text-warning" />
}

function OverviewTab({ drone }: { drone: Drone }) {
  const { status } = useDroneStatus(drone.droneId, 5000)
  const online = isDroneOnline(status)
  const bat = status?.battery
  const pos = status?.position
  const att = status?.attitude

  return (
    <div className="p-4 space-y-4">
      {/* Status banner */}
      <div
        className={cn(
          "flex items-center gap-2 rounded-xl px-4 py-3",
          online
            ? "bg-green-500/10 border border-green-500/20"
            : "bg-muted border border-border"
        )}
      >
        <span
          className={cn(
            "size-2 rounded-full",
            online ? "bg-green-500 animate-pulse" : "bg-muted-foreground"
          )}
        />
        <span className="text-sm font-medium">
          {online ? "Online" : "Offline"}
        </span>
        {status?.mode && (
          <Badge variant="outline" className="ml-auto text-xs">
            {status.mode}
          </Badge>
        )}
        {status?.armed && (
          <Badge className="text-xs">Armed</Badge>
        )}
      </div>

      {/* Telemetry */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Telemetry</CardTitle>
        </CardHeader>
        <CardContent className="pb-4">
          {bat && (
            <>
              <TelemetryRow
                label="Battery"
                value={
                  <span className="flex items-center gap-1">
                    <BatteryIcon pct={bat.remaining} />
                    {bat.remaining !== undefined
                      ? `${Math.round(bat.remaining)}%`
                      : "—"}
                    {bat.voltage !== undefined && (
                      <span className="text-muted-foreground text-xs ml-1">
                        {bat.voltage.toFixed(2)} V
                      </span>
                    )}
                  </span>
                }
              />
            </>
          )}
          {pos && (
            <>
              {pos.lat !== undefined && pos.lng !== undefined && (
                <TelemetryRow
                  label="Position"
                  value={`${pos.lat.toFixed(6)}, ${pos.lng.toFixed(6)}`}
                />
              )}
              {pos.alt !== undefined && (
                <TelemetryRow label="Altitude" value={`${pos.alt.toFixed(1)} m`} />
              )}
            </>
          )}
          {att && (
            <>
              {att.roll !== undefined && (
                <TelemetryRow label="Roll" value={`${att.roll.toFixed(1)}°`} />
              )}
              {att.pitch !== undefined && (
                <TelemetryRow label="Pitch" value={`${att.pitch.toFixed(1)}°`} />
              )}
              {att.yaw !== undefined && (
                <TelemetryRow label="Yaw" value={`${att.yaw.toFixed(1)}°`} />
              )}
            </>
          )}
          {!status && (
            <p className="text-xs text-muted-foreground py-2">
              No telemetry data available
            </p>
          )}
        </CardContent>
      </Card>

      {/* Drone info */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Info</CardTitle>
        </CardHeader>
        <CardContent className="pb-4">
          <TelemetryRow label="Drone ID" value={drone.droneId} />
          {drone.droneType && (
            <TelemetryRow
              label="Type"
              value={drone.droneType === "sim" ? "Simulator" : "Physical"}
            />
          )}
          {drone.vehicleType && (
            <TelemetryRow label="Vehicle" value={drone.vehicleType} />
          )}
          <TelemetryRow
            label="Registered"
            value={new Date(drone.registeredAt).toLocaleDateString()}
          />
        </CardContent>
      </Card>
    </div>
  )
}

// ─────────────────────────────────────────────
// Chat tab
// ─────────────────────────────────────────────

function ChatBubble({ message }: { message: ChatMessage }) {
  const isUser = message.sender === "user"

  return (
    <div
      className={cn(
        "flex flex-col gap-1",
        isUser ? "items-end" : "items-start"
      )}
    >
      {message.contentType === "text" && message.text && (
        <div
          className={cn(
            "max-w-[80%] rounded-2xl px-4 py-2.5 text-sm",
            isUser
              ? "bg-amber-500 text-black rounded-br-sm"
              : "bg-muted text-foreground rounded-bl-sm"
          )}
        >
          <p className="whitespace-pre-wrap">{message.text}</p>
        </div>
      )}

      {message.contentType === "loading" && (
        <div className="bg-muted rounded-2xl rounded-bl-sm px-4 py-2.5 flex items-center gap-2">
          <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
          <span className="text-xs text-muted-foreground">
            {message.loading ?? "Processing…"}
          </span>
        </div>
      )}

      {message.contentType === "error" && (
        <div className="bg-destructive/10 border border-destructive/20 rounded-2xl rounded-bl-sm px-4 py-2.5 max-w-[80%]">
          <p className="text-xs text-destructive">{message.error}</p>
        </div>
      )}

      {message.contentType === "image" && message.imageUrl && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={message.imageUrl}
          alt="Drone capture"
          className="max-w-[80%] rounded-2xl rounded-bl-sm object-cover"
        />
      )}

      {message.contentType === "image_choice" && message.imageOptions && (
        <div className="flex flex-wrap gap-2 max-w-[90%]">
          {message.imageOptions.map((opt) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={opt.id}
              src={opt.url}
              alt={opt.label ?? opt.id}
              className="w-28 h-28 rounded-xl object-cover cursor-pointer hover:ring-2 hover:ring-amber-500 transition-all"
            />
          ))}
        </div>
      )}

      {message.contentType === "mission_progress" && message.missionProgress && (
        <div className="bg-muted rounded-2xl rounded-bl-sm px-4 py-3 max-w-[80%] w-72">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium">{message.missionProgress.phase}</span>
            <span className="text-xs text-muted-foreground">
              {Math.round(message.missionProgress.progress * 100)}%
            </span>
          </div>
          <div className="h-1.5 w-full bg-border rounded-full overflow-hidden">
            <div
              className="h-full bg-amber-500 rounded-full transition-all"
              style={{ width: `${message.missionProgress.progress * 100}%` }}
            />
          </div>
          {message.missionProgress.detail && (
            <p className="text-xs text-muted-foreground mt-1.5">
              {message.missionProgress.detail}
            </p>
          )}
        </div>
      )}

      <span className="text-[10px] text-muted-foreground/60 px-1">
        {new Date(message.timestamp).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        })}
      </span>
    </div>
  )
}

function ChatTab({ droneId }: { droneId: string }) {
  const { convId, setConvId } = usePersistedConvId(droneId)
  const [initError, setInitError] = useState<string | null>(null)
  const [initializing, setInitializing] = useState(false)
  const [input, setInput] = useState("")
  const [sending, setSending] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const { messages, loading, addOptimistic } = useConversationMessages(
    droneId,
    convId,
    3000
  )

  // Auto-create conversation on mount if none persisted
  useEffect(() => {
    if (convId) return
    setInitializing(true)
    createConversation(droneId)
      .then((res) => {
        setConvId(res.conversationId)
        setInitError(null)
      })
      .catch((e) => setInitError(e instanceof Error ? e.message : "Failed to start"))
      .finally(() => setInitializing(false))
  }, [droneId, convId, setConvId])

  // Scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages.length])

  const handleSend = async () => {
    const text = input.trim()
    if (!text || !convId || sending) return

    const optimistic: ChatMessage = {
      id: `opt_${Date.now()}`,
      sender: "user",
      contentType: "text",
      text,
      timestamp: Date.now(),
    }
    addOptimistic(optimistic)
    setInput("")
    setSending(true)

    try {
      await sendMessage(droneId, convId, text)
    } catch {
      // message will re-appear from poll
    } finally {
      setSending(false)
    }
  }

  const handleNewConv = async () => {
    setInitializing(true)
    try {
      const res = await createConversation(droneId)
      setConvId(res.conversationId)
    } catch {
      // ignore
    } finally {
      setInitializing(false)
    }
  }

  if (initError) {
    return (
      <div className="flex flex-col items-center justify-center h-48 gap-3 p-4">
        <p className="text-sm text-destructive">{initError}</p>
        <Button size="sm" onClick={handleNewConv}>
          Retry
        </Button>
      </div>
    )
  }

  if (initializing || (loading && messages.length === 0)) {
    return (
      <div className="flex items-center justify-center h-48">
        <Loader2 className="size-5 animate-spin text-amber-500" />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-end gap-2 px-4 py-2 border-b border-border">
        <button
          onClick={handleNewConv}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          <RotateCcw className="size-3" />
          New chat
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-36 gap-2">
            <MessageSquare className="size-8 text-muted-foreground/30" />
            <p className="text-xs text-muted-foreground">
              Send a message to start controlling the drone
            </p>
          </div>
        )}
        {messages.map((msg) => (
          <ChatBubble key={msg.id} message={msg} />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-4 py-3 border-t border-border">
        <div className="flex items-center gap-2">
          <Input
            placeholder="Send a command…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                handleSend()
              }
            }}
            className="flex-1"
            disabled={!convId || sending}
          />
          <Button
            size="icon"
            onClick={handleSend}
            disabled={!input.trim() || !convId || sending}
          >
            {sending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────
// Map tab (Leaflet via iframe / static fallback)
// ─────────────────────────────────────────────

function MapTab({ droneId }: { droneId: string }) {
  const { status } = useDroneStatus(droneId, 5000)
  const pos = status?.position

  if (!pos?.lat || !pos?.lng) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 p-8 text-center">
        <Map className="size-8 text-muted-foreground/30" />
        <p className="text-sm text-muted-foreground">
          No position data available
        </p>
      </div>
    )
  }

  // OpenStreetMap static tile centered on drone
  const zoom = 15
  const tileUrl = `https://staticmap.openstreetmap.de/staticmap.php?center=${pos.lat},${pos.lng}&zoom=${zoom}&size=600x300&markers=${pos.lat},${pos.lng}`

  return (
    <div className="p-4 space-y-4">
      <div className="rounded-xl overflow-hidden border border-border aspect-video bg-muted flex items-center justify-center">
        {/* Leaflet via srcdoc */}
        <iframe
          srcDoc={buildLeafletHtml(pos.lat, pos.lng, pos.alt)}
          className="w-full h-full border-0"
          sandbox="allow-scripts"
          title="Drone map"
        />
      </div>
      <Card>
        <CardContent className="pt-4 pb-4">
          <TelemetryRow label="Latitude" value={pos.lat.toFixed(6)} />
          <TelemetryRow label="Longitude" value={pos.lng.toFixed(6)} />
          {pos.alt !== undefined && (
            <TelemetryRow label="Altitude" value={`${pos.alt.toFixed(1)} m`} />
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function buildLeafletHtml(lat: number, lng: number, alt?: number): string {
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#map{margin:0;padding:0;width:100%;height:100%;background:#000}</style>
</head>
<body>
<div id="map"></div>
<script>
const map = L.map('map', { zoomControl: false, attributionControl: false }).setView([${lat}, ${lng}], 16);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  maxZoom: 19
}).addTo(map);
const icon = L.divIcon({
  html: '<div style="width:12px;height:12px;background:#f59e0b;border-radius:50%;border:2px solid #fff;box-shadow:0 0 8px rgba(245,158,11,0.8)"></div>',
  iconSize: [12, 12], iconAnchor: [6, 6]
});
L.marker([${lat}, ${lng}], { icon }).addTo(map)${alt !== undefined ? `.bindTooltip('${alt.toFixed(1)} m', {permanent:true, direction:'top', offset:[0,-8], className:'leaflet-tooltip-dark'})` : ""};
</script>
</body>
</html>`
}

// ─────────────────────────────────────────────
// Logs tab
// ─────────────────────────────────────────────

const LOG_LEVEL_COLOR: Record<string, string> = {
  ERROR: "text-destructive",
  WARN: "text-warning",
  INFO: "text-foreground/80",
  DEBUG: "text-muted-foreground",
}

function LogsTab({ droneId }: { droneId: string }) {
  const { logs, loading } = useDroneLogs(droneId, 2000)
  const bottomRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)

  useEffect(() => {
    if (autoScroll) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" })
    }
  }, [logs.length, autoScroll])

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-2 border-b border-border">
        <span className="text-xs text-muted-foreground flex items-center gap-1.5">
          <Terminal className="size-3" />
          {loading ? "Loading…" : `${logs.length} entries`}
        </span>
        <button
          onClick={() => setAutoScroll((a) => !a)}
          className="text-xs text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1"
        >
          <ChevronDown
            className={cn("size-3 transition-transform", autoScroll && "rotate-180")}
          />
          {autoScroll ? "Auto-scroll on" : "Auto-scroll off"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto bg-black p-4 font-mono text-xs space-y-0.5">
        {logs.length === 0 && !loading && (
          <span className="text-muted-foreground">No log entries</span>
        )}
        {logs.map((log, i) => (
          <div key={i} className="flex gap-2 leading-5">
            <span className="text-muted-foreground/60 shrink-0">
              {new Date(log.timestamp).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })}
            </span>
            <span
              className={cn(
                "shrink-0 w-12",
                LOG_LEVEL_COLOR[log.level?.toUpperCase()] ?? "text-muted-foreground"
              )}
            >
              {log.level?.toUpperCase()}
            </span>
            <span className="text-muted-foreground/70 shrink-0">{log.source}</span>
            <span className="text-foreground/90 break-all">{log.message}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────
// Configure tab
// ─────────────────────────────────────────────

function ConfigureTab({
  drone,
  onDroneUpdated,
  onDroneDeleted,
}: {
  drone: Drone
  onDroneUpdated: (d: Drone) => void
  onDroneDeleted: () => void
}) {
  const [name, setName] = useState(drone.name)
  const [savingName, setSavingName] = useState(false)

  // Battery
  const [battery, setBattery] = useState<BatteryConfig | null>(null)
  const [savingBattery, setSavingBattery] = useState(false)

  // WiFi
  const [networks, setNetworks] = useState<WifiNetwork[]>([])
  const [newSsid, setNewSsid] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [savingWifi, setSavingWifi] = useState(false)

  // Delete
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    getBatteryConfig(drone.droneId)
      .then(setBattery)
      .catch(() => setBattery({}))

    getWifiNetworks(drone.droneId)
      .then(setNetworks)
      .catch(() => setNetworks([]))
  }, [drone.droneId])

  const handleSaveName = async () => {
    if (!name.trim() || name === drone.name) return
    setSavingName(true)
    try {
      await updateDroneName(drone.droneId, name.trim())
      onDroneUpdated({ ...drone, name: name.trim() })
    } catch {
      setName(drone.name)
    } finally {
      setSavingName(false)
    }
  }

  const handleSaveBattery = async () => {
    if (!battery) return
    setSavingBattery(true)
    try {
      await saveBatteryConfig(drone.droneId, battery)
    } catch {
      // ignore
    } finally {
      setSavingBattery(false)
    }
  }

  const handleAddWifi = async () => {
    if (!newSsid.trim()) return
    setSavingWifi(true)
    try {
      const net: WifiNetwork = { ssid: newSsid.trim(), password: newPassword }
      await saveWifiNetwork(drone.droneId, net)
      setNetworks((n) => [...n.filter((x) => x.ssid !== net.ssid), net])
      setNewSsid("")
      setNewPassword("")
    } catch {
      // ignore
    } finally {
      setSavingWifi(false)
    }
  }

  const handleDeleteWifi = async (ssid: string) => {
    try {
      await deleteWifiNetwork(drone.droneId, ssid)
      setNetworks((n) => n.filter((x) => x.ssid !== ssid))
    } catch {
      // ignore
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await deleteDrone(drone.droneId)
      onDroneDeleted()
    } catch {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  return (
    <div className="p-4 space-y-6">
      {/* Name */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Name</CardTitle>
        </CardHeader>
        <CardContent className="pb-4 flex gap-2">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Drone name"
          />
          <Button
            size="sm"
            onClick={handleSaveName}
            disabled={savingName || !name.trim() || name === drone.name}
          >
            {savingName ? <Loader2 className="size-4 animate-spin" /> : "Save"}
          </Button>
        </CardContent>
      </Card>

      {/* Battery */}
      {battery && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Battery</CardTitle>
          </CardHeader>
          <CardContent className="pb-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Cell count</span>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="icon-sm"
                  onClick={() =>
                    setBattery((b) => ({
                      ...b,
                      cellCount: Math.max(1, (b?.cellCount ?? 4) - 1),
                    }))
                  }
                >
                  <Minus className="size-3.5" />
                </Button>
                <span className="w-6 text-center text-sm font-mono">
                  {battery.cellCount ?? "—"}
                </span>
                <Button
                  variant="outline"
                  size="icon-sm"
                  onClick={() =>
                    setBattery((b) => ({
                      ...b,
                      cellCount: (b?.cellCount ?? 4) + 1,
                    }))
                  }
                >
                  <Plus className="size-3.5" />
                </Button>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">
                  Min V/cell
                </label>
                <Input
                  type="number"
                  step="0.1"
                  value={battery.minCellVoltage ?? ""}
                  onChange={(e) =>
                    setBattery((b) => ({
                      ...b,
                      minCellVoltage: parseFloat(e.target.value),
                    }))
                  }
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">
                  Max V/cell
                </label>
                <Input
                  type="number"
                  step="0.1"
                  value={battery.maxCellVoltage ?? ""}
                  onChange={(e) =>
                    setBattery((b) => ({
                      ...b,
                      maxCellVoltage: parseFloat(e.target.value),
                    }))
                  }
                />
              </div>
            </div>
            <Button size="sm" onClick={handleSaveBattery} disabled={savingBattery}>
              {savingBattery ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                "Save battery config"
              )}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* WiFi */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">WiFi networks</CardTitle>
        </CardHeader>
        <CardContent className="pb-4 space-y-3">
          {networks.length === 0 && (
            <p className="text-xs text-muted-foreground">No networks configured</p>
          )}
          {networks.map((net) => (
            <div
              key={net.ssid}
              className="flex items-center justify-between py-1.5 border-b border-border/50 last:border-0"
            >
              <span className="text-sm">{net.ssid}</span>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => handleDeleteWifi(net.ssid)}
              >
                <Trash2 className="size-3.5 text-muted-foreground" />
              </Button>
            </div>
          ))}
          <div className="flex flex-col gap-2 pt-2">
            <Input
              placeholder="SSID"
              value={newSsid}
              onChange={(e) => setNewSsid(e.target.value)}
            />
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Input
                  type={showPassword ? "text" : "password"}
                  placeholder="Password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((s) => !s)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {showPassword ? (
                    <EyeOff className="size-3.5" />
                  ) : (
                    <Eye className="size-3.5" />
                  )}
                </button>
              </div>
              <Button size="sm" onClick={handleAddWifi} disabled={savingWifi || !newSsid.trim()}>
                {savingWifi ? <Loader2 className="size-4 animate-spin" /> : "Add"}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Delete */}
      <Card className="border-destructive/30">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm text-destructive">Danger zone</CardTitle>
        </CardHeader>
        <CardContent className="pb-4">
          <Button
            variant="destructive"
            size="sm"
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 className="size-4" />
            Delete drone
          </Button>
        </CardContent>
      </Card>

      {/* Confirm delete dialog */}
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete drone</DialogTitle>
            <DialogDescription>
              This will permanently remove <strong>{drone.name}</strong> and send a
              factory reset command to the device. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
              {deleting ? <Loader2 className="size-4 animate-spin" /> : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// ─────────────────────────────────────────────
// Video tab (WebRTC KVS viewer)
// ─────────────────────────────────────────────

function VideoTab({ droneId }: { droneId: string }) {
  const [viewerInfo, setViewerInfo] = useState<VideoViewerResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const start = async () => {
    setLoading(true)
    setError(null)
    try {
      const info = await getVideoViewer(droneId)
      setViewerInfo(info)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start video")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-4 space-y-4">
      {!viewerInfo ? (
        <div className="flex flex-col items-center justify-center gap-4 py-12">
          <div className="size-16 rounded-2xl bg-muted flex items-center justify-center">
            <Activity className="size-7 text-muted-foreground" />
          </div>
          <p className="text-sm text-muted-foreground">Video stream not started</p>
          <Button onClick={start} disabled={loading}>
            {loading ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              "Start video"
            )}
          </Button>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="rounded-xl overflow-hidden border border-border bg-black aspect-video">
            <iframe
              srcDoc={buildWebRtcHtml(viewerInfo)}
              className="w-full h-full border-0"
              sandbox="allow-scripts"
              allow="camera; microphone"
              title="Drone video"
            />
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setViewerInfo(null)}
          >
            Stop
          </Button>
        </div>
      )}
    </div>
  )
}

function buildWebRtcHtml(info: VideoViewerResponse): string {
  const iceServersJson = JSON.stringify(info.iceServers)
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html,body{margin:0;padding:0;background:#000;width:100%;height:100%}
  video{width:100%;height:100%;object-fit:cover;display:block}
  #status{position:absolute;top:8px;left:8px;color:#facc15;font:11px/1 monospace;background:rgba(0,0,0,0.6);padding:4px 8px;border-radius:4px}
</style>
</head>
<body>
<video id="v" autoplay playsinline muted></video>
<div id="status">Connecting…</div>
<script>
(async () => {
  const ws = new WebSocket(${JSON.stringify(info.signedWssUrl)});
  const pc = new RTCPeerConnection({ iceServers: ${iceServersJson} });
  const video = document.getElementById('v');
  const status = document.getElementById('status');

  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.ontrack = e => { video.srcObject = e.streams[0]; status.textContent = 'Live'; };
  pc.onicecandidate = ({ candidate }) => {
    if (candidate && ws.readyState === 1) {
      ws.send(JSON.stringify({
        action: 'ICE_CANDIDATE',
        recipientClientId: 'drone-master',
        messagePayload: btoa(JSON.stringify(candidate))
      }));
    }
  };

  ws.onopen = async () => {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    ws.send(JSON.stringify({
      action: 'SDP_OFFER',
      recipientClientId: 'drone-master',
      messagePayload: btoa(JSON.stringify(offer))
    }));
  };

  ws.onmessage = async ({ data }) => {
    const msg = JSON.parse(data);
    if (!msg.messagePayload) return;
    const payload = JSON.parse(atob(msg.messagePayload));
    if (msg.messageType === 'SDP_ANSWER') {
      await pc.setRemoteDescription(new RTCSessionDescription(payload));
    } else if (msg.messageType === 'ICE_CANDIDATE') {
      await pc.addIceCandidate(new RTCIceCandidate(payload));
    }
  };

  ws.onerror = () => { status.textContent = 'Connection error'; status.style.color = '#ef4444'; };
  ws.onclose = () => { status.textContent = 'Disconnected'; };

  // Heartbeat every 5s
  setInterval(() => {
    if (ws.readyState === 1) ws.send(JSON.stringify({ action: 'heartbeat' }));
  }, 5000);
})();
</script>
</body>
</html>`
}

// ─────────────────────────────────────────────
// Main drone detail page
// ─────────────────────────────────────────────

export default function DroneDetailPage({
  params,
}: {
  params: Promise<{ droneId: string }>
}) {
  const { droneId } = use(params)
  const router = useRouter()
  const { isAuthenticated } = useAuthState()
  const [drone, setDrone] = useState<Drone | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)

  useEffect(() => {
    if (isAuthenticated === false) {
      router.replace("/operator")
      return
    }
    if (isAuthenticated === null) return

    getDrone(droneId)
      .then(setDrone)
      .catch((e) => {
        if (e instanceof ApiError && (e.status === 404 || e.status === 403)) {
          setNotFound(true)
        } else if (e instanceof ApiError && e.status === 401) {
          clearSession()
          router.replace("/operator")
        }
      })
      .finally(() => setLoading(false))
  }, [droneId, isAuthenticated, router])

  if (isAuthenticated === null || loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="size-6 animate-spin text-amber-500" />
      </div>
    )
  }

  if (notFound || !drone) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4">
        <p className="text-sm text-muted-foreground">Drone not found</p>
        <Button variant="outline" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="size-4" />
          Back
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col min-h-screen">
      {/* Header */}
      <header className="sticky top-0 z-10 flex h-14 items-center gap-3 border-b border-border bg-background/95 px-4 backdrop-blur">
        <button
          onClick={() => router.push("/operator")}
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="size-5" />
        </button>
        <div className="flex flex-col gap-0">
          <span className="font-semibold text-sm leading-none">{drone.name}</span>
          <span className="text-xs text-muted-foreground font-mono mt-0.5">
            {drone.droneId.slice(0, 12)}…
          </span>
        </div>
      </header>

      {/* Tabs */}
      <Tabs defaultValue="overview" className="flex-1 flex flex-col">
        <TabsList
          variant="line"
          className="px-4 border-b border-border rounded-none w-full justify-start gap-1 h-10"
        >
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="chat">Chat</TabsTrigger>
          <TabsTrigger value="video">Video</TabsTrigger>
          <TabsTrigger value="map">Map</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
          <TabsTrigger value="configure">Configure</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="flex-1 overflow-y-auto m-0">
          <OverviewTab drone={drone} />
        </TabsContent>

        <TabsContent value="chat" className="flex-1 flex flex-col overflow-hidden m-0">
          <ChatTab droneId={drone.droneId} />
        </TabsContent>

        <TabsContent value="video" className="flex-1 overflow-y-auto m-0">
          <VideoTab droneId={drone.droneId} />
        </TabsContent>

        <TabsContent value="map" className="flex-1 overflow-y-auto m-0">
          <MapTab droneId={drone.droneId} />
        </TabsContent>

        <TabsContent value="logs" className="flex-1 flex flex-col overflow-hidden m-0">
          <LogsTab droneId={drone.droneId} />
        </TabsContent>

        <TabsContent value="configure" className="flex-1 overflow-y-auto m-0">
          <ConfigureTab
            drone={drone}
            onDroneUpdated={(updated) => setDrone(updated)}
            onDroneDeleted={() => router.replace("/operator")}
          />
        </TabsContent>
      </Tabs>
    </div>
  )
}

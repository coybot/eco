// ─────────────────────────────────────────────
// Core domain types for the web operator client
// ─────────────────────────────────────────────

export interface Drone {
  userId: string
  droneId: string
  name: string
  registeredAt: string
  status?: DroneStatus
  droneType?: "irl" | "sim"
  vehicleType?: string
  simEnvironment?: string
  isaacHost?: string
}

export interface DroneStatus {
  droneId?: string
  position?: {
    lat?: number
    lng?: number
    alt?: number
  }
  attitude?: {
    roll?: number
    pitch?: number
    yaw?: number
  }
  battery?: {
    voltage?: number
    current?: number
    remaining?: number
  }
  armed?: boolean
  mode?: string
  lastUpdate?: string | number
  isOnline?: boolean
  ttl?: number
}

export interface DroneLog {
  timestamp: string
  level: string
  source: string
  message: string
  code?: string
}

export type MessageSender = "user" | "drone"

export type MessageContentType =
  | "text"
  | "image"
  | "image_choice"
  | "loading"
  | "error"
  | "mission_progress"

export interface ImageOption {
  id: string
  url: string
  label?: string
}

export interface MissionProgress {
  phase: string
  progress: number
  detail?: string
  status?: "running" | "complete" | "failed"
}

export interface ChatMessage {
  id: string
  sender: MessageSender
  contentType: MessageContentType
  text?: string
  imageUrl?: string
  imageOptions?: ImageOption[]
  loading?: string
  error?: string
  missionProgress?: MissionProgress
  timestamp: number
}

export interface Conversation {
  conversationId: string
  droneId: string
  createdAt: string
  updatedAt?: string
  title?: string
}

export interface WifiNetwork {
  ssid: string
  password?: string
  configured?: boolean
}

export interface BatteryConfig {
  cellCount?: number
  minCellVoltage?: number
  maxCellVoltage?: number
  warningVoltage?: number
}

export interface VideoViewerResponse {
  signedWssUrl: string
  clientId: string
  iceServers: Array<{ urls: string | string[]; username?: string; credential?: string }>
}

// ─────────────────────────────────────────────
// Web simulator (Godot) session types
// ─────────────────────────────────────────────

export interface SimDrone {
  droneId: string
  vehicleType: string
  label: string          // "Quadcopter 1", "Rover 1", …
  conversationId: string
}

export interface SimSession {
  sessionId: string
  wsUrl: string          // wss://sim.coy.bot/stream?session=…
  drones: SimDrone[]
}

// ─────────────────────────────────────────────
// Auth session types
// ─────────────────────────────────────────────

export interface AuthSession {
  idToken: string
  refreshToken?: string
  provider: "google" | "cognito"
  expiresAt: number
}

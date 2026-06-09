"use client"

// ─────────────────────────────────────────────
// REST API client for the drone operator backend
// ─────────────────────────────────────────────

import { OPERATOR_CONFIG } from "./config"
import { getValidToken, clearSession } from "./auth"
import type {
  Drone,
  DroneStatus,
  DroneLog,
  Conversation,
  ChatMessage,
  WifiNetwork,
  BatteryConfig,
  VideoViewerResponse,
} from "./types"

// ─── Internal fetch wrapper ───────────────────

async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  retry = true
): Promise<T> {
  const token = await getValidToken()
  if (!token) {
    // Signal caller that auth is gone
    clearSession()
    throw new ApiError(401, "Unauthorized")
  }

  const url = `${OPERATOR_CONFIG.apiEndpoint}${path}`
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(options.headers ?? {}),
    },
  })

  if (res.status === 401 || res.status === 403) {
    if (retry) {
      // Token may have just expired — getValidToken tries refresh inside, but
      // a concurrent request could still hit a 401 window. Try once more.
      return apiFetch<T>(path, options, false)
    }
    clearSession()
    throw new ApiError(res.status, res.status === 401 ? "Unauthorized" : "Forbidden")
  }

  if (res.status === 404) throw new ApiError(404, "Not found")
  if (res.status === 409) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(409, body?.message ?? "Conflict")
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(res.status, body?.message ?? `Server error ${res.status}`)
  }

  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T
  }

  return res.json() as Promise<T>
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message)
    this.name = "ApiError"
  }
}

// ─── Drone endpoints ──────────────────────────

export async function listDrones(): Promise<Drone[]> {
  const data = await apiFetch<{ drones: Drone[] }>("/drones")
  return data.drones ?? []
}

export async function getDrone(droneId: string): Promise<Drone> {
  return apiFetch<Drone>(`/drones/${droneId}`)
}

export async function updateDroneName(droneId: string, name: string): Promise<void> {
  await apiFetch(`/drones/${droneId}`, {
    method: "PUT",
    body: JSON.stringify({ name }),
  })
}

export async function deleteDrone(droneId: string): Promise<void> {
  await apiFetch(`/drones/${droneId}`, { method: "DELETE" })
}

export async function getDroneStatus(droneId: string): Promise<DroneStatus> {
  return apiFetch<DroneStatus>(`/drones/${droneId}/status`)
}

// ─── Logs ─────────────────────────────────────

export async function getDroneLogs(droneId: string): Promise<DroneLog[]> {
  const data = await apiFetch<{ logs: DroneLog[] }>(`/drones/${droneId}/logs`)
  return data.logs ?? []
}

// ─── WiFi ─────────────────────────────────────

export async function getWifiNetworks(droneId: string): Promise<WifiNetwork[]> {
  const data = await apiFetch<{ networks: WifiNetwork[] }>(`/drones/${droneId}/wifi`)
  return data.networks ?? []
}

export async function saveWifiNetwork(
  droneId: string,
  network: WifiNetwork
): Promise<void> {
  await apiFetch(`/drones/${droneId}/wifi`, {
    method: "PUT",
    body: JSON.stringify(network),
  })
}

export async function deleteWifiNetwork(droneId: string, ssid: string): Promise<void> {
  await apiFetch(`/drones/${droneId}/wifi`, {
    method: "DELETE",
    body: JSON.stringify({ ssid }),
  })
}

// ─── Battery ──────────────────────────────────

export async function getBatteryConfig(droneId: string): Promise<BatteryConfig> {
  return apiFetch<BatteryConfig>(`/drones/${droneId}/battery-config`)
}

export async function saveBatteryConfig(
  droneId: string,
  config: BatteryConfig
): Promise<void> {
  await apiFetch(`/drones/${droneId}/battery-config`, {
    method: "PUT",
    body: JSON.stringify(config),
  })
}

// ─── Conversations ────────────────────────────

export async function listConversations(droneId: string): Promise<Conversation[]> {
  const data = await apiFetch<{ conversations: Conversation[] }>(
    `/drones/${droneId}/conversations`
  )
  return data.conversations ?? []
}

export async function createConversation(
  droneId: string
): Promise<{ conversationId: string }> {
  return apiFetch<{ conversationId: string }>(`/drones/${droneId}/conversations`, {
    method: "POST",
    body: JSON.stringify({}),
  })
}

export async function getConversationMessages(
  droneId: string,
  convId: string
): Promise<ChatMessage[]> {
  const data = await apiFetch<{ messages: ChatMessage[] }>(
    `/drones/${droneId}/conversations/${convId}/messages`
  )
  return data.messages ?? []
}

export async function sendMessage(
  droneId: string,
  convId: string,
  text: string
): Promise<{ immediateResponse?: ChatMessage }> {
  return apiFetch(`/drones/${droneId}/conversations/${convId}/messages`, {
    method: "POST",
    body: JSON.stringify({ message: text }),
  })
}

export async function selectConversation(
  droneId: string,
  convId: string
): Promise<void> {
  await apiFetch(`/drones/${droneId}/conversations/${convId}/select`, {
    method: "POST",
  })
}

// ─── Video ────────────────────────────────────

export async function getVideoViewer(
  droneId: string
): Promise<VideoViewerResponse> {
  return apiFetch<VideoViewerResponse>(`/drones/${droneId}/video/viewer`)
}

export async function startVideoStream(droneId: string): Promise<void> {
  await apiFetch(`/drones/${droneId}/video/start`, { method: "POST" })
}

// ─── IoT policy ───────────────────────────────

export async function attachIotPolicy(): Promise<void> {
  await apiFetch("/auth/iot-policy", { method: "POST" })
}

"use client"

// ─────────────────────────────────────────────
// React hooks for data fetching in operator UI
// ─────────────────────────────────────────────

import { useState, useEffect, useCallback, useRef } from "react"
import {
  listDrones,
  getDroneStatus,
  getDroneLogs,
  getConversationMessages,
  ApiError,
} from "./api"
import { getSession, isSessionValid } from "./auth"
import type { Drone, DroneStatus, DroneLog, ChatMessage } from "./types"

// ─── Auth state ───────────────────────────────

export function useAuthState() {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null)

  useEffect(() => {
    const session = getSession()
    setIsAuthenticated(isSessionValid(session))
  }, [])

  const recheck = useCallback(() => {
    const session = getSession()
    setIsAuthenticated(isSessionValid(session))
  }, [])

  return { isAuthenticated, recheck }
}

// ─── Drones list ──────────────────────────────

export function useDrones(pollIntervalMs = 5000) {
  const [drones, setDrones] = useState<Drone[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetch = useCallback(async () => {
    try {
      const data = await listDrones()
      setDrones(data)
      setError(null)
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setError("session_expired")
      } else {
        setError(e instanceof Error ? e.message : "Failed to load drones")
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch()
    const id = setInterval(fetch, pollIntervalMs)
    return () => clearInterval(id)
  }, [fetch, pollIntervalMs])

  return { drones, loading, error, refresh: fetch }
}

// ─── Single drone status ──────────────────────

export function useDroneStatus(droneId: string | null, pollIntervalMs = 5000) {
  const [status, setStatus] = useState<DroneStatus | null>(null)
  const [loading, setLoading] = useState(true)

  const fetch = useCallback(async () => {
    if (!droneId) return
    try {
      const data = await getDroneStatus(droneId)
      setStatus(data)
    } catch {
      // Silently continue – stale status is better than crashing
    } finally {
      setLoading(false)
    }
  }, [droneId])

  useEffect(() => {
    fetch()
    const id = setInterval(fetch, pollIntervalMs)
    return () => clearInterval(id)
  }, [fetch, pollIntervalMs])

  return { status, loading, refresh: fetch }
}

// ─── Drone logs ───────────────────────────────

export function useDroneLogs(droneId: string | null, pollIntervalMs = 2000) {
  const [logs, setLogs] = useState<DroneLog[]>([])
  const [loading, setLoading] = useState(true)

  const fetch = useCallback(async () => {
    if (!droneId) return
    try {
      const data = await getDroneLogs(droneId)
      setLogs(data)
    } catch {
      // keep stale
    } finally {
      setLoading(false)
    }
  }, [droneId])

  useEffect(() => {
    fetch()
    const id = setInterval(fetch, pollIntervalMs)
    return () => clearInterval(id)
  }, [fetch, pollIntervalMs])

  return { logs, loading, refresh: fetch }
}

// ─── Conversation messages ────────────────────

export function useConversationMessages(
  droneId: string | null,
  convId: string | null,
  pollIntervalMs = 3000
) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(true)

  const fetchMessages = useCallback(async () => {
    if (!droneId || !convId) return
    try {
      const data = await getConversationMessages(droneId, convId)
      setMessages(data)
    } catch {
      // keep stale
    } finally {
      setLoading(false)
    }
  }, [droneId, convId])

  useEffect(() => {
    setMessages([])
    setLoading(true)
    fetchMessages()
    const id = setInterval(fetchMessages, pollIntervalMs)
    return () => clearInterval(id)
  }, [fetchMessages, pollIntervalMs])

  const addOptimistic = useCallback((msg: ChatMessage) => {
    setMessages((prev) => [...prev, msg])
  }, [])

  return { messages, loading, refresh: fetchMessages, addOptimistic }
}

// ─── Persisted conversation ID ────────────────

const CONV_KEY_PREFIX = "operator_conv_"

export function usePersistedConvId(droneId: string | null) {
  const [convId, setConvIdState] = useState<string | null>(null)

  useEffect(() => {
    if (!droneId) return
    const stored = localStorage.getItem(`${CONV_KEY_PREFIX}${droneId}`)
    setConvIdState(stored)
  }, [droneId])

  const setConvId = useCallback(
    (id: string) => {
      if (!droneId) return
      localStorage.setItem(`${CONV_KEY_PREFIX}${droneId}`, id)
      setConvIdState(id)
    },
    [droneId]
  )

  const clearConvId = useCallback(() => {
    if (!droneId) return
    localStorage.removeItem(`${CONV_KEY_PREFIX}${droneId}`)
    setConvIdState(null)
  }, [droneId])

  return { convId, setConvId, clearConvId }
}

// ─── Drone online detection ───────────────────

export function isDroneOnline(status: DroneStatus | null): boolean {
  if (!status) return false
  if (typeof status.isOnline === "boolean") return status.isOnline
  if (status.ttl) {
    const ttlMs = status.ttl * 1000
    return ttlMs > Date.now()
  }
  if (status.lastUpdate) {
    const last =
      typeof status.lastUpdate === "number"
        ? status.lastUpdate
        : new Date(status.lastUpdate).getTime()
    return Date.now() - last < 30_000
  }
  return false
}

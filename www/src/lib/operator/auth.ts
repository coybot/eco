"use client"

// ─────────────────────────────────────────────
// Auth helpers – AWS Cognito OAuth flow for web
// ─────────────────────────────────────────────

import { OPERATOR_CONFIG } from "./config"
import type { AuthSession } from "./types"

const SESSION_KEY = "operator_session"

// ─── Session store (localStorage) ────────────

export function getSession(): AuthSession | null {
  if (typeof window === "undefined") return null
  try {
    const raw = localStorage.getItem(SESSION_KEY)
    if (!raw) return null
    const session: AuthSession = JSON.parse(raw)
    return session
  } catch {
    return null
  }
}

export function saveSession(session: AuthSession): void {
  if (typeof window === "undefined") return
  localStorage.setItem(SESSION_KEY, JSON.stringify(session))
}

export function clearSession(): void {
  if (typeof window === "undefined") return
  localStorage.removeItem(SESSION_KEY)
}

export function isSessionValid(session: AuthSession | null): boolean {
  if (!session) return false
  // 60-second buffer
  return session.expiresAt > Date.now() + 60_000
}

// ─── Cognito OAuth flow ──────────────────────

export function cognitoSignIn(): void {
  if (!OPERATOR_CONFIG.cognitoHostedUiUrl) {
    throw new Error("Cognito Hosted UI URL not configured")
  }

  const redirectUri = encodeURIComponent(
    OPERATOR_CONFIG.cognitoCallbackUrl ||
      `${window.location.origin}/operator/auth/callback`
  )

  const params = new URLSearchParams({
    client_id: OPERATOR_CONFIG.cognitoAppClientId,
    response_type: "code",
    scope: "openid email profile",
    redirect_uri: redirectUri,
  })

  window.location.href = `${OPERATOR_CONFIG.cognitoHostedUiUrl}/oauth2/authorize?${params}`
}

export async function handleCognitoCallback(code: string): Promise<AuthSession> {
  if (!OPERATOR_CONFIG.cognitoHostedUiUrl) {
    throw new Error("Cognito Hosted UI URL not configured")
  }

  const redirectUri =
    OPERATOR_CONFIG.cognitoCallbackUrl ||
    `${window.location.origin}/operator/auth/callback`

  const tokenEndpoint = `${OPERATOR_CONFIG.cognitoHostedUiUrl}/oauth2/token`

  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: OPERATOR_CONFIG.cognitoAppClientId,
    code,
    redirect_uri: redirectUri,
  })

  const res = await fetch(tokenEndpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  })

  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Token exchange failed: ${err}`)
  }

  const data = await res.json()
  const { id_token, refresh_token, expires_in } = data

  const session: AuthSession = {
    idToken: id_token,
    refreshToken: refresh_token,
    provider: "cognito",
    expiresAt: Date.now() + expires_in * 1000,
  }

  saveSession(session)
  return session
}

export async function refreshCognitoToken(session: AuthSession): Promise<AuthSession> {
  if (!session.refreshToken) throw new Error("No refresh token")
  if (!OPERATOR_CONFIG.cognitoHostedUiUrl) {
    throw new Error("Cognito Hosted UI URL not configured")
  }

  const tokenEndpoint = `${OPERATOR_CONFIG.cognitoHostedUiUrl}/oauth2/token`

  const body = new URLSearchParams({
    grant_type: "refresh_token",
    client_id: OPERATOR_CONFIG.cognitoAppClientId,
    refresh_token: session.refreshToken,
  })

  const res = await fetch(tokenEndpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  })

  if (!res.ok) throw new Error("Token refresh failed")

  const data = await res.json()
  const updated: AuthSession = {
    ...session,
    idToken: data.id_token,
    expiresAt: Date.now() + data.expires_in * 1000,
  }

  saveSession(updated)
  return updated
}

export async function getValidToken(): Promise<string | null> {
  let session = getSession()
  if (!session) return null

  if (!isSessionValid(session)) {
    if (session.refreshToken) {
      try {
        session = await refreshCognitoToken(session)
      } catch {
        clearSession()
        return null
      }
    } else {
      clearSession()
      return null
    }
  }

  return session.idToken
}

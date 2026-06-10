"use client"

// ─────────────────────────────────────────────
// Web simulator: H.264/fMP4 frames over WebSocket decoded via MediaSource.
// ~10× better bandwidth than JPEG; smooth playback; hardware-decoded.
//
// Server protocol:
//   binary  — fMP4 chunks (ffmpeg frag_keyframe+empty_moov+default_base_moof)
//   text    — {"type":"cam_reset"} when a cam switch starts a fresh stream
// ─────────────────────────────────────────────

import { useEffect, useRef, useState, useCallback } from "react"

const MIME = 'video/mp4; codecs="avc1.42E01E"'

export function useSimStream(wsUrl: string | null, cam: string) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const msRef = useRef<MediaSource | null>(null)
  const msUrlRef = useRef<string | null>(null)
  const sbRef = useRef<SourceBuffer | null>(null)
  const pendingRef = useRef<ArrayBuffer[]>([])
  const camRef = useRef(cam)

  camRef.current = cam

  const releaseMse = useCallback(() => {
    sbRef.current = null
    pendingRef.current = []
    if (msUrlRef.current) {
      URL.revokeObjectURL(msUrlRef.current)
      msUrlRef.current = null
    }
    msRef.current = null
    if (videoRef.current) videoRef.current.src = ""
  }, [])

  const attachMse = useCallback(() => {
    releaseMse()
    if (typeof MediaSource === "undefined") return
    const ms = new MediaSource()
    msRef.current = ms
    const url = URL.createObjectURL(ms)
    msUrlRef.current = url
    if (videoRef.current) videoRef.current.src = url

    ms.addEventListener(
      "sourceopen",
      () => {
        if (!MediaSource.isTypeSupported(MIME)) {
          console.warn("[sim] MIME not supported:", MIME)
          return
        }
        const sb = ms.addSourceBuffer(MIME)
        sbRef.current = sb
        sb.addEventListener("updateend", () => {
          if (!sb.updating && pendingRef.current.length > 0) {
            const next = pendingRef.current.shift()!
            try { sb.appendBuffer(next) } catch {}
          }
        })
      },
      { once: true }
    )
  }, [releaseMse])

  useEffect(() => {
    if (!wsUrl) return
    let dead = false

    const ws = new WebSocket(wsUrl)
    ws.binaryType = "arraybuffer"
    wsRef.current = ws

    ws.onopen = () => {
      if (dead) return
      setConnected(true)
      // Subscribe only — server always replies with cam_reset before frames,
      // so attachMse() is called exactly once per stream in the cam_reset handler.
      ws.send(JSON.stringify({ type: "subscribe", cam: camRef.current }))
    }
    ws.onclose = () => { setConnected(false); releaseMse() }
    ws.onerror = () => setConnected(false)

    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        try {
          const msg = JSON.parse(ev.data as string)
          if (msg.type === "cam_reset") attachMse()
        } catch {}
        return
      }
      const sb = sbRef.current
      if (!sb) return
      const buf = ev.data as ArrayBuffer
      if (sb.updating || pendingRef.current.length > 0) {
        if (pendingRef.current.length < 60) pendingRef.current.push(buf)
      } else {
        try {
          sb.appendBuffer(buf)
        } catch {
          // QuotaExceededError — trim old buffer; next frame will retry
          if (sb.buffered.length > 0 && !sb.updating) {
            try { sb.remove(sb.buffered.start(0), sb.buffered.end(0) - 4) } catch {}
          }
        }
      }
    }

    return () => {
      dead = true
      ws.close()
      wsRef.current = null
      releaseMse()
    }
  }, [wsUrl, attachMse, releaseMse])

  // Camera change — signal server; it replies cam_reset + starts fresh stream
  useEffect(() => {
    const ws = wsRef.current
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "subscribe", cam }))
    }
  }, [cam])

  return { videoRef, connected }
}

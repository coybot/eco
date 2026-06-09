"use client"

import { useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { handleCognitoCallback } from "@/lib/operator/auth"
import { Loader2 } from "lucide-react"
import { Suspense } from "react"

function CallbackInner() {
  const router = useRouter()
  const params = useSearchParams()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const code = params.get("code")
    const err = params.get("error")

    if (err) {
      setError(err)
      return
    }

    if (!code) {
      setError("Missing authorization code")
      return
    }

    handleCognitoCallback(code)
      .then(() => router.replace("/operator"))
      .catch((e) => setError(e instanceof Error ? e.message : "Authentication failed"))
  }, [params, router])

  if (error) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-8">
        <p className="text-destructive text-sm">{error}</p>
        <a href="/operator" className="text-amber-500 hover:underline text-sm">
          Back to operator
        </a>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <Loader2 className="size-6 animate-spin text-amber-500" />
    </div>
  )
}

export default function CallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <Loader2 className="size-6 animate-spin text-amber-500" />
        </div>
      }
    >
      <CallbackInner />
    </Suspense>
  )
}

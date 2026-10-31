import { useEffect, useState } from 'react'
import { api } from './api'
import type { AIStatus } from './types'

/** Where the AI runs, for the places that suggest OpenRouter while it is
 *  local. Shared and briefly cached: a queue of fifty jobs asks once. */

let cached: { at: number; request: Promise<AIStatus> } | null = null
const FRESH_MS = 30_000

/** Sent on window when Settings → AI has changed something, so the sidebar's
 *  line about where the AI runs follows at once rather than at its next look.
 *  Saving a Meta key alone moves the assistant to Muse Spark (server/agent.py,
 *  muse_backend), and that line must not say "no cloud AI" meanwhile. */
export const AI_CHANGED = 'ai-changed'

/** Called after every change in Settings → AI: the cached answer is stale. */
export function forgetAIStatus(): void {
  cached = null
  window.dispatchEvent(new Event(AI_CHANGED))
}

export function useAIStatus(): AIStatus | null {
  const [status, setStatus] = useState<AIStatus | null>(null)
  useEffect(() => {
    if (!cached || Date.now() - cached.at > FRESH_MS) {
      cached = { at: Date.now(), request: api.ai() }
    }
    let live = true
    cached.request.then((s) => live && setStatus(s)).catch(() => live && setStatus(null))
    return () => {
      live = false
    }
  }, [])
  return status
}

/** True only once the engine has said the AI runs on this PC. */
export function useLocalAI(): boolean {
  const status = useAIStatus()
  return !!status && status.active.local
}

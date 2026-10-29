import { API_BASE } from './api'
import { sessionSecret } from './plugins'

/** Settings → Notifications & Remote Approval: the engine's /remote-approval
 *  routes (server/remote_approval_api.py), new for the Meta hackathon
 *  edition. Routes that change something need the desktop app's session
 *  secret (the plugin routes' one), so in a plain browser the card can read
 *  but not change. Nothing here holds a token, a secret or a phone number:
 *  the engine only ever knows the number's last four digits. */

export type Platform = 'instagram' | 'facebook' | 'threads'

export interface Account {
  connected: boolean
  name?: string
  expires_at?: number
}

export interface RemoteSettings {
  whatsapp_enabled: boolean
  relay_url: string
  notify_on_finish: boolean
  notify_on_fail: boolean
  notify_on_publish: boolean
  remote_approvals: boolean
  preferred_destinations: Platform[]
  approval_hours: number
  remind_minutes: number
  max_clips: number
  basketball_only: boolean
  muse_captions: boolean
  muse_summary: boolean
  muse_model: string
  pairing: { state?: 'pending' | 'awaiting_confirm' | 'confirmed' | 'opted_out' | 'none'; tail?: string }
  pin_set: boolean
  accounts: Partial<Record<Platform, Account>>
  templates: Record<string, string>
  registered: boolean
  platform_labels: Record<Platform, string>
}

export interface Post {
  platform: Platform
  state: 'published' | 'failed' | 'unconfirmed' | 'skipped'
  url: string
  error: string
}

export interface Review {
  id: number
  job_id: number
  title: string
  state: string
  summary: string
  clips: { clip_id: number; title: string; category: string }[]
  decision: { clips?: number[]; platforms?: Platform[] }
  result_text: string
  results: { clips?: { clip_id: number; title: string; posts: Post[] }[] }
  error: string
  expires_at: string
  remind_at: string
  created_at: string
  updated_at: string
  captions_by: string
  can_cancel: boolean
  can_retry: boolean
  can_ask_again: boolean
  can_resend: boolean
}

export interface Usage {
  muse_spark: { requests: number; prompt_tokens: number; cached_tokens: number; completion_tokens: number; usd: number }
  muse_voice: { requests: number; audio_seconds: number; usd: number }
  whatsapp: { templates: number; free_form: number; usd: number }
  meta_graph: { steps: number; uploads: number; bytes: number }
  estimated_usd: number
}

export interface RemoteStatus {
  settings: RemoteSettings
  can_enable_remote_approval: string
  pending: Review[]
  outbox_pending: number
  usage: Usage
  relay_error?: string
}

export interface PairCode {
  code: string
  text: string
  link: string
  expires_at: number
}

async function failure(res: Response): Promise<Error> {
  const text = await res.text().catch(() => '')
  try {
    const detail = JSON.parse(text).detail
    if (typeof detail === 'string') return new Error(detail)
  } catch {
    // not JSON: fall through to the raw text
  }
  return new Error(text.slice(0, 300) || `The engine answered ${res.status}`)
}

async function call<T>(path: string, init: RequestInit = {}, guarded = false): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (guarded) {
    const s = await sessionSecret()
    if (!s) throw new Error('Changing these settings needs the Clips Kitty desktop app.')
    headers['X-Clips-Kitty-Session'] = s
  }
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) throw await failure(res)
  return res.json() as Promise<T>
}

const send = (method: string, body?: unknown): RequestInit =>
  body === undefined ? { method } : { method, body: JSON.stringify(body) }

export const remoteApproval = {
  status: () => call<RemoteStatus>('/remote-approval/status'),
  save: (patch: Partial<RemoteSettings>) =>
    call<RemoteStatus>('/remote-approval/settings', send('PATCH', patch), true),
  /** Register this PC with the creator's relay, with the relay owner's enrolment key (used once, not kept). */
  register: (relayUrl: string, enrollKey: string) =>
    call<RemoteStatus>('/remote-approval/register', send('POST', { relay_url: relayUrl, enroll_key: enrollKey }), true),
  refresh: () => call<RemoteStatus>('/remote-approval/refresh', send('POST'), true),
  pairStart: () => call<PairCode>('/remote-approval/pair/start', send('POST'), true),
  pairConfirm: (confirm: boolean) =>
    call<RemoteStatus>('/remote-approval/pair/confirm', send('POST', { confirm }), true),
  setPin: (pin: string) => call<RemoteStatus>('/remote-approval/pin', send('POST', { pin }), true),
  test: () => call<{ queued: boolean }>('/remote-approval/test', send('POST'), true),
  disconnect: () => call<RemoteStatus>('/remote-approval/disconnect', send('POST'), true),
  /** The Meta sign-in page (on Meta's own site) to open in the browser. */
  connectAccount: (platform: 'facebook' | 'threads') =>
    call<{ url: string }>('/remote-approval/accounts/connect', send('POST', { platform }), true),
  disconnectAccount: (platform: Platform) =>
    call<RemoteStatus>('/remote-approval/accounts/disconnect', send('POST', { platform }), true),
  templates: (create = false) =>
    call<{ templates?: Record<string, string> }>('/remote-approval/templates', send('POST', { create }), true),
  reviews: (limit = 10) => call<{ reviews: Review[] }>(`/remote-approval/reviews?limit=${limit}`),
  act: (id: number, action: 'cancel' | 'retry' | 'ask-again' | 'resend') =>
    call<{ review: Review }>(`/remote-approval/reviews/${id}/${action}`, send('POST'), true)
}

/** How an offer's state reads in Settings. */
export const STATE_TEXT: Record<string, string> = {
  READY_FOR_REVIEW: 'Ready',
  NOTIFICATION_PENDING: 'Sending to WhatsApp',
  NOTIFICATION_SENT: 'Sent to WhatsApp',
  AWAITING_APPROVAL: 'Waiting for your approval',
  DEFERRED: 'Remind me later',
  APPROVED: 'Approved',
  PUBLISH_QUEUED: 'Approved, queued to publish',
  PUBLISHING: 'Publishing',
  PUBLISHED: 'Published',
  PARTIAL_FAILURE: 'Partly published',
  FAILED: 'Not published',
  CANCELLED: 'Cancelled',
  EXPIRED: 'Link expired'
}

export const PLATFORMS: Platform[] = ['instagram', 'facebook', 'threads']

/** A wa.me link from the relay, only in the shape the main process will open. */
export const isPairLink = (link: string): boolean => /^https:\/\/wa\.me\/\d{6,15}\?text=LINK%20[A-Z0-9]{6}$/.test(link)

import { useEffect, useRef, useState } from 'react'
import { t } from '../lib/i18n'
import { canManage } from '../lib/plugins'
import {
  isPairLink,
  MUSE_STATE_TEXT,
  museAddresses,
  museSentence,
  PLATFORMS,
  remoteApproval,
  STATE_TEXT,
  type PairCode,
  type Platform,
  type RemoteStatus,
  type Review
} from '../lib/remoteApproval'
import { useEvents } from '../lib/useEvents'

/** Settings → Notifications & Remote Approval (new for the Meta hackathon edition).
 *
 *  Word through the creator's Muse agent, or a WhatsApp message, when a
 *  video's highlights are ready (basketball ones, unless the creator opts in
 *  to gaming and every other video), and publishing to Instagram, Facebook
 *  and Threads approved from the phone. All of it goes through the creator's
 *  own relay (meta-relay/), which holds the WhatsApp and Meta tokens and is
 *  where Muse connects; this PC only calls out to it, so nothing here opens
 *  a port. Remote approval stays off until Muse is connected or a number is
 *  paired and confirmed, and an approval PIN is set. The card never sees a
 *  token, a secret or a full phone number; a new Muse key is shown once and
 *  then forgotten. */

const POLL_MS = 4000

export default function NotificationsApprovalCard(): JSX.Element {
  const [status, setStatus] = useState<RemoteStatus | null>(null)
  const [reviews, setReviews] = useState<Review[]>([])
  const [allowed, setAllowed] = useState(true)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [relayUrl, setRelayUrl] = useState('')
  const [enrollKey, setEnrollKey] = useState('')
  const [pin, setPin] = useState('')
  const [pair, setPair] = useState<PairCode | null>(null)
  const [waiting, setWaiting] = useState(false)
  // A Muse key just made: shown until Done, held nowhere else.
  const [museKey, setMuseKey] = useState('')
  const mounted = useRef(true)

  const load = (): void => {
    remoteApproval
      .status()
      .then((s) => {
        if (!mounted.current) return
        setStatus(s)
        setRelayUrl((r) => r || s.settings.relay_url)
      })
      .catch(() => {})
    remoteApproval
      .reviews(8)
      .then((r) => mounted.current && setReviews(r.reviews))
      .catch(() => {})
  }

  useEffect(() => {
    mounted.current = true
    load()
    canManage().then((ok) => mounted.current && setAllowed(ok))
    return () => {
      mounted.current = false
    }
  }, [])

  useEvents((e) => {
    if (e.type === 'remote_approval' || e.type === 'publish') load()
  })

  // While the phone is pairing or a Meta sign-in is open in the browser, ask
  // the relay how it went every few seconds (it can't call this PC).
  useEffect(() => {
    if (!waiting && !pair) return
    const timer = setInterval(() => {
      remoteApproval
        .refresh()
        .then((s) => {
          if (!mounted.current) return
          setStatus(s)
          if (pair && s.settings.pairing.state === 'awaiting_confirm') setPair(null)
        })
        .catch(() => {})
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [waiting, pair])

  const run = async (work: () => Promise<void>): Promise<void> => {
    if (busy) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await work()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      load()
    } finally {
      setBusy(false)
    }
  }

  const save = (patch: Parameters<typeof remoteApproval.save>[0]): Promise<void> =>
    run(async () => {
      const s = await remoteApproval.save(patch)
      setStatus(s)
      if (s.relay_error) setError(s.relay_error)
    })

  const open = async (url: string): Promise<boolean> => {
    if (await window.studio.openExternal(url)) return true
    setError(t('Could not open your browser.'))
    return false
  }

  if (!status) {
    return (
      <div className="card space-y-2">
        <h3 className="font-semibold">{t('Notifications & Remote Approval')}</h3>
        <p className="text-sm text-muted">{t('Loading…')}</p>
      </div>
    )
  }

  const s = status.settings
  const paired = s.pairing.state === 'confirmed'
  const museOn = Boolean(s.muse?.enabled)
  // Muse or a paired number: either way an offer can reach the phone.
  const reachable = paired || museOn
  const connected = (p: Platform): boolean => Boolean(s.accounts[p]?.connected)
  const label = (p: Platform): string => s.platform_labels[p] ?? p
  const disabled = busy || !allowed
  const addresses = museAddresses(s.relay_url)

  const makeMuseKey = (): Promise<void> =>
    run(async () => {
      if (museOn && !window.confirm(t('Make a new key? The old one stops working, so Muse needs the new one.'))) return
      const { key, ...next } = await remoteApproval.museToken()
      setStatus(next)
      setMuseKey(key)
    })

  // `invert`: the box is ticked when the setting is off (basketball_only reads
  // as "Offer gaming and other videos too").
  const toggle = (key: keyof typeof s, text: string, hint = '', off = false, invert = false): JSX.Element => (
    <label className={`flex items-start gap-2 text-sm ${off ? 'opacity-60' : 'cursor-pointer'}`}>
      <input
        type="checkbox"
        className="size-4 mt-0.5 accent-[#38BDF8]"
        checked={Boolean(s[key]) !== invert}
        disabled={disabled || off}
        onChange={(e) => void save({ [key]: e.target.checked !== invert })}
      />
      <span>
        {text}
        {hint && <span className="block text-[11px] text-muted">{hint}</span>}
      </span>
    </label>
  )

  return (
    <div className="card space-y-4">
      <div>
        <h3 className="font-semibold">{t('Notifications & Remote Approval')}</h3>
        <p className="text-sm text-muted">
          {t(
            "Hear from your Muse agent or on WhatsApp when a video's highlights are ready, and choose from your phone which clips go to Instagram, Facebook and Threads. Everything goes through your own relay; this PC only calls out to it."
          )}
        </p>
      </div>

      {!allowed && (
        <p className="text-xs text-warn">{t('Changing these settings needs the Clips Kitty desktop app.')}</p>
      )}
      {notice && <p className="text-xs text-success">{notice}</p>}
      {error && <p className="text-xs text-error">{error}</p>}

      {/* 1. The relay */}
      <section className="space-y-2">
        <h4 className="text-sm font-semibold">{t('1. Your relay')}</h4>
        {s.registered ? (
          <div className="flex items-center gap-2 flex-wrap text-sm">
            <span className="text-success">{t('Connected')}</span>
            <span className="text-muted truncate">{s.relay_url}</span>
            <button
              className="btn-ghost !py-1 !px-3 text-xs ml-auto"
              disabled={disabled}
              onClick={() =>
                void run(async () => {
                  if (
                    !window.confirm(
                      t('Disconnect this PC from your relay? Open offers are cancelled, WhatsApp messages stop and your Muse key stops working.')
                    )
                  )
                    return
                  setStatus(await remoteApproval.disconnect())
                  setPair(null)
                  setMuseKey('')
                  setNotice(t('Disconnected.'))
                })
              }
            >
              {t('Disconnect')}
            </button>
          </div>
        ) : (
          <div className="space-y-1.5">
            <input
              className="input w-full !py-1 text-sm"
              placeholder="https://clips-kitty-relay.example.workers.dev"
              value={relayUrl}
              disabled={disabled}
              onChange={(e) => setRelayUrl(e.target.value)}
            />
            <input
              className="input w-full !py-1 text-sm"
              type="password"
              placeholder={t('Enrolment key (from your relay set-up)')}
              value={enrollKey}
              disabled={disabled}
              onChange={(e) => setEnrollKey(e.target.value)}
            />
            <button
              className="btn-accent !py-1 !px-3 text-xs"
              disabled={disabled || !relayUrl.trim() || !enrollKey.trim()}
              onClick={() =>
                void run(async () => {
                  setStatus(await remoteApproval.register(relayUrl.trim(), enrollKey.trim()))
                  setEnrollKey('')
                  setNotice(t('This PC is connected to your relay.'))
                })
              }
            >
              {t('Connect this PC')}
            </button>
            <p className="text-[11px] text-muted">
              {t('The relay is a small Cloudflare Worker you deploy once. See docs/hackathon/meta/WHATSAPP_SETUP.md.')}
            </p>
          </div>
        )}
      </section>

      {/* 2. The creator's Muse agent: a custom connector on the relay */}
      {s.registered && (
        <section className="space-y-2 border-t border-raised/60 pt-3">
          <h4 className="text-sm font-semibold">{t('2. Your Muse agent')}</h4>
          <p className="text-xs text-muted">
            {t(
              'Muse can tell you when highlights are ready, give you the approval link, say what got posted, remind you later or cancel an offer. It can never post: you approve on the approval page with your PIN.'
            )}
          </p>
          {museOn ? (
            <div className="flex items-center gap-2 flex-wrap text-sm">
              <span className="text-success">{t('Connected')}</span>
              <span className="text-xs text-muted">
                {s.muse.last_used
                  ? `${t('Last used')} ${new Date(s.muse.last_used * 1000).toLocaleString()}`
                  : t('Not used yet')}
              </span>
              <div className="flex gap-1.5 ml-auto">
                <button className="btn-ghost !py-1 !px-3 text-xs" disabled={disabled} onClick={() => void makeMuseKey()}>
                  {t('Make a new key')}
                </button>
                <button
                  className="btn-ghost !py-1 !px-3 text-xs"
                  disabled={disabled}
                  onClick={() =>
                    void run(async () => {
                      if (!window.confirm(t('Revoke the Muse key? Muse stops working with Clips Kitty until you make a new key.')))
                        return
                      setStatus(await remoteApproval.museRevoke())
                      setMuseKey('')
                      setNotice(t('The Muse key no longer works.'))
                    })
                  }
                >
                  {t('Revoke')}
                </button>
              </div>
            </div>
          ) : (
            <button className="btn-accent !py-1 !px-3 text-xs" disabled={disabled} onClick={() => void makeMuseKey()}>
              {t('Make a Muse key')}
            </button>
          )}
          {museKey && (
            <div className="rounded bg-raised/30 p-2 space-y-1.5 text-xs">
              <p className="font-semibold">{t('Your Muse key. It is shown only this once:')}</p>
              <div className="flex items-start gap-2">
                <code className="font-mono break-all select-all">{museKey}</code>
                <CopyButton text={museKey} />
              </div>
              <p className="text-warn">
                {t("When Muse asks for the key, paste it into Muse's credential prompt, never into the chat.")}
              </p>
              <button className="btn-ghost !py-0.5 !px-2 text-xs" onClick={() => setMuseKey('')}>
                {t('Done')}
              </button>
            </div>
          )}
          {(museOn || museKey) && (
            <div className="text-xs space-y-1">
              <p className="text-muted">{t('Say this to Muse:')}</p>
              <div className="flex items-start gap-2">
                <code className="break-all">{museSentence(s.relay_url)}</code>
                <CopyButton text={museSentence(s.relay_url)} />
              </div>
              <p className="text-muted">
                {t('Connector address')}: <code className="break-all">{addresses.mcp}</code>
              </p>
              <p className="text-muted">
                {t('If Muse asks for API details')}: <code className="break-all">{addresses.openapi}</code>
              </p>
            </div>
          )}
        </section>
      )}

      {/* 3. The WhatsApp number, for messages as well as (or instead of) Muse */}
      {s.registered && (
        <section className="space-y-2 border-t border-raised/60 pt-3">
          <h4 className="text-sm font-semibold">{t('3. WhatsApp (optional)')}</h4>
          {paired && (
            <p className="text-sm">
              {t('Paired with the number ending in')} <span className="font-semibold">{s.pairing.tail}</span>
            </p>
          )}
          {s.pairing.state === 'opted_out' && (
            <p className="text-xs text-warn">{t('This number sent STOP. Send START from WhatsApp to get messages again.')}</p>
          )}
          {s.pairing.state === 'awaiting_confirm' && (
            <div className="space-y-1.5">
              <p className="text-sm">
                {t('Is the number ending in')} <span className="font-semibold">{s.pairing.tail}</span> {t('yours?')}
              </p>
              <div className="flex gap-2">
                <button
                  className="btn-accent !py-1 !px-3 text-xs"
                  disabled={disabled}
                  onClick={() => void run(async () => setStatus(await remoteApproval.pairConfirm(true)))}
                >
                  {t('Yes, pair it')}
                </button>
                <button
                  className="btn-ghost !py-1 !px-3 text-xs"
                  disabled={disabled}
                  onClick={() => void run(async () => setStatus(await remoteApproval.pairConfirm(false)))}
                >
                  {t('No')}
                </button>
              </div>
            </div>
          )}
          {s.pairing.state !== 'awaiting_confirm' && (
            <div className="space-y-1.5">
              {pair ? (
                <div className="text-sm space-y-1">
                  <p>
                    {t('From the phone that should get messages, send this to your WhatsApp business number:')}{' '}
                    <span className="font-mono font-semibold">{pair.text}</span>
                  </p>
                  <div className="flex gap-2">
                    {isPairLink(pair.link) && (
                      <button className="btn-ghost !py-1 !px-3 text-xs" onClick={() => void open(pair.link)}>
                        {t('Open WhatsApp')}
                      </button>
                    )}
                    <button className="btn-ghost !py-1 !px-3 text-xs" onClick={() => setPair(null)}>
                      {t('Cancel')}
                    </button>
                  </div>
                  <p className="text-[11px] text-muted">{t('This card updates once the message arrives.')}</p>
                </div>
              ) : (
                <button
                  className="btn-ghost !py-1 !px-3 text-xs"
                  disabled={disabled}
                  onClick={() => void run(async () => setPair(await remoteApproval.pairStart()))}
                >
                  {paired ? t('Pair a different number') : t('Pair my WhatsApp number')}
                </button>
              )}
            </div>
          )}

          {paired && (
            <div className="space-y-1.5 pt-1">
              {toggle('whatsapp_enabled', t('Send me WhatsApp messages'))}
              {s.whatsapp_enabled && (
                <div className="pl-6 space-y-1.5">
                  {toggle('notify_on_finish', t('When highlights are ready'))}
                  {toggle('notify_on_fail', t('When a video could not be processed'))}
                  {toggle('notify_on_publish', t('When publishing finishes'))}
                  <button
                    className="btn-ghost !py-1 !px-3 text-xs"
                    disabled={disabled}
                    onClick={() =>
                      void run(async () => {
                        await remoteApproval.test()
                        setNotice(t('Test message queued. It should arrive within a few seconds.'))
                      })
                    }
                  >
                    {t('Send a test message')}
                  </button>
                  <Templates templates={s.templates} disabled={disabled} run={run} reload={load} />
                </div>
              )}
            </div>
          )}
        </section>
      )}

      {/* 4. Approval PIN and Meta accounts */}
      {s.registered && reachable && (
        <section className="space-y-2 border-t border-raised/60 pt-3">
          <h4 className="text-sm font-semibold">{t('4. Approval PIN and accounts')}</h4>
          <div className="flex items-center gap-2 flex-wrap">
            <input
              className="input !w-28 !py-1 text-sm"
              type="password"
              inputMode="numeric"
              maxLength={8}
              placeholder={s.pin_set ? t('New PIN') : t('4-8 digits')}
              value={pin}
              disabled={disabled}
              onChange={(e) => setPin(e.target.value.replace(/\D/g, ''))}
            />
            <button
              className="btn-ghost !py-1 !px-3 text-xs"
              disabled={disabled || pin.length < 4}
              onClick={() =>
                void run(async () => {
                  setStatus(await remoteApproval.setPin(pin))
                  setPin('')
                  setNotice(t('PIN saved. The approval page asks for it before anything is posted.'))
                })
              }
            >
              {s.pin_set ? t('Change PIN') : t('Set PIN')}
            </button>
            {s.pin_set && <span className="text-xs text-success">{t('PIN set')}</span>}
          </div>
          <div className="space-y-1.5">
            {(['facebook', 'threads'] as const).map((p) => (
              <div key={p} className="flex items-center gap-2 flex-wrap text-sm">
                <span className="w-40">{p === 'facebook' ? t('Facebook Page + Instagram') : t('Threads')}</span>
                {connected(p) ? (
                  <>
                    <span className="text-success text-xs">
                      {s.accounts[p]?.name}
                      {p === 'facebook' && connected('instagram') ? ` · ${s.accounts.instagram?.name}` : ''}
                    </span>
                    <button
                      className="btn-ghost !py-0.5 !px-2 text-xs ml-auto"
                      disabled={disabled}
                      onClick={() => void run(async () => setStatus(await remoteApproval.disconnectAccount(p)))}
                    >
                      {t('Disconnect')}
                    </button>
                  </>
                ) : (
                  <button
                    className="btn-ghost !py-0.5 !px-2 text-xs"
                    disabled={disabled}
                    onClick={() =>
                      void run(async () => {
                        const { url } = await remoteApproval.connectAccount(p)
                        if (await open(url)) {
                          setWaiting(true)
                          setNotice(t('Finish signing in with Meta in your browser. This card updates when you are done.'))
                        }
                      })
                    }
                  >
                    {t('Connect')}
                  </button>
                )}
              </div>
            ))}
            {s.accounts.facebook?.connected && !connected('instagram') && (
              <p className="text-[11px] text-warn">
                {t('No Instagram professional account is linked to this Facebook Page, so Instagram is not available.')}
              </p>
            )}
          </div>
        </section>
      )}

      {/* 5. Remote approval */}
      {s.registered && reachable && (
        <section className="space-y-2 border-t border-raised/60 pt-3">
          <h4 className="text-sm font-semibold">{t('5. Approve from your phone')}</h4>
          {toggle(
            'remote_approvals',
            t('Let me approve publishing from my phone'),
            status.can_enable_remote_approval && !s.remote_approvals
              ? status.can_enable_remote_approval
              : t('Nothing is posted until you pick the clips and destinations on the approval page and enter your PIN.'),
            Boolean(status.can_enable_remote_approval) && !s.remote_approvals
          )}
          {s.remote_approvals && (
            <div className="pl-6 space-y-2 text-sm">
              <div>
                <span className="text-xs text-muted">{t('Tick in advance on the approval page:')}</span>
                <div className="flex gap-3 flex-wrap">
                  {PLATFORMS.map((p) => (
                    <label key={p} className={`inline-flex items-center gap-1.5 ${connected(p) ? 'cursor-pointer' : 'opacity-50'}`}>
                      <input
                        type="checkbox"
                        className="size-4 accent-[#38BDF8]"
                        disabled={disabled || !connected(p)}
                        checked={s.preferred_destinations.includes(p)}
                        onChange={(e) =>
                          void save({
                            preferred_destinations: e.target.checked
                              ? [...s.preferred_destinations, p]
                              : s.preferred_destinations.filter((x) => x !== p)
                          })
                        }
                      />
                      {label(p)}
                    </label>
                  ))}
                </div>
              </div>
              <label className="flex items-center gap-2">
                {t('Link expires after')}
                <select
                  className="input !w-auto !py-0.5 text-sm"
                  value={s.approval_hours}
                  disabled={disabled}
                  onChange={(e) => void save({ approval_hours: Number(e.target.value) })}
                >
                  {[6, 12, 24, 48, 72].map((h) => (
                    <option key={h} value={h}>
                      {h} {t('hours')}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2">
                {t('"Remind me later" waits')}
                <select
                  className="input !w-auto !py-0.5 text-sm"
                  value={s.remind_minutes}
                  disabled={disabled}
                  onChange={(e) => void save({ remind_minutes: Number(e.target.value) })}
                >
                  {[30, 60, 120, 240, 480].map((m) => (
                    <option key={m} value={m}>
                      {m >= 60 ? `${m / 60} ${t('hours')}` : `${m} ${t('minutes')}`}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}
          {toggle(
            'basketball_only',
            t('Offer gaming and other videos too'),
            t('Basketball videos are always offered. This adds every other finished video.'),
            false,
            true
          )}
          {toggle(
            'muse_captions',
            t('Write captions with Muse Spark'),
            t('One request per video for every clip and platform, kept and reused. Needs a Meta Model API key in Settings → AI.')
          )}
          {toggle('muse_summary', t('Add a one-line Muse Spark summary to the message'))}
        </section>
      )}

      {/* Offers */}
      {reviews.length > 0 && (
        <section className="space-y-2 border-t border-raised/60 pt-3">
          <h4 className="text-sm font-semibold">{t('Recent offers')}</h4>
          {reviews.map((r) => (
            <ReviewRow
              key={r.id}
              review={r}
              disabled={disabled}
              museOnly={museOn && !(paired && s.whatsapp_enabled)}
              label={label}
              open={open}
              run={run}
              reload={load}
            />
          ))}
        </section>
      )}

      {/* What it cost */}
      {(status.usage.muse_spark.requests > 0 ||
        status.usage.muse_voice.requests > 0 ||
        status.usage.whatsapp.templates > 0) && (
        <p className="text-[11px] text-muted border-t border-raised/60 pt-2">
          {t('Estimated cost so far')}: ${status.usage.estimated_usd.toFixed(4)} · Muse Spark{' '}
          {status.usage.muse_spark.requests} {t('requests')} · Muse Voice{' '}
          {Math.round(status.usage.muse_voice.audio_seconds / 60)} {t('min')} · WhatsApp{' '}
          {status.usage.whatsapp.templates} {t('templates')}
        </p>
      )}
    </div>
  )
}

function ReviewRow(props: {
  review: Review
  disabled: boolean
  /** Offers go to Muse alone: no WhatsApp message is sent for them. */
  museOnly: boolean
  label: (p: Platform) => string
  open: (url: string) => Promise<boolean>
  run: (work: () => Promise<void>) => Promise<void>
  reload: () => void
}): JSX.Element {
  const { review: r, disabled, museOnly, label, open, run, reload } = props
  const stateText = (museOnly ? MUSE_STATE_TEXT[r.state] : undefined) ?? STATE_TEXT[r.state] ?? r.state
  const act = (action: 'cancel' | 'retry' | 'ask-again' | 'resend'): Promise<void> =>
    run(async () => {
      await remoteApproval.act(r.id, action)
      reload()
    })
  const tone =
    r.state === 'PUBLISHED' ? 'text-success' : ['FAILED', 'PARTIAL_FAILURE'].includes(r.state) ? 'text-warn' : 'text-muted'
  return (
    <div className="text-sm space-y-1 rounded bg-raised/30 p-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-medium truncate max-w-[16rem]">{r.title || t('Untitled video')}</span>
        <span className={`text-xs ${tone}`}>{t(stateText)}</span>
        <div className="flex gap-1.5 ml-auto">
          {r.can_resend && (
            <button className="btn-ghost !py-0.5 !px-2 text-xs" disabled={disabled} onClick={() => void act('resend')}>
              {t('Send again')}
            </button>
          )}
          {r.can_retry && (
            <button className="btn-ghost !py-0.5 !px-2 text-xs" disabled={disabled} onClick={() => void act('retry')}>
              {t('Retry failed')}
            </button>
          )}
          {r.can_ask_again && (
            <button className="btn-ghost !py-0.5 !px-2 text-xs" disabled={disabled} onClick={() => void act('ask-again')}>
              {t('Ask again')}
            </button>
          )}
          {r.can_cancel && (
            <button className="btn-ghost !py-0.5 !px-2 text-xs" disabled={disabled} onClick={() => void act('cancel')}>
              {t('Cancel')}
            </button>
          )}
        </div>
      </div>
      <p className="text-xs text-muted">{r.result_text || r.summary}</p>
      {r.error && <p className="text-xs text-error">{r.error}</p>}
      {(r.results.clips ?? []).map((c) => (
        <div key={c.clip_id} className="text-xs pl-2">
          <span className="text-muted">{c.title}: </span>
          {c.posts.map((p) => (
            <span key={p.platform} className="mr-2">
              {p.state === 'published' && p.url ? (
                <button className="underline text-accent" onClick={() => void open(p.url)}>
                  {label(p.platform)}
                </button>
              ) : (
                <span className={p.state === 'published' ? 'text-success' : 'text-warn'} title={p.error}>
                  {label(p.platform)} ({t(p.state)})
                </span>
              )}
            </span>
          ))}
        </div>
      ))}
    </div>
  )
}

/** Copies text (a Muse key, the sentence for Muse) and says so. */
function CopyButton({ text }: { text: string }): JSX.Element {
  const [copied, setCopied] = useState(false)
  return (
    <button
      className="btn-ghost !py-0.5 !px-2 text-xs shrink-0"
      onClick={() =>
        void navigator.clipboard.writeText(text).then(
          () => setCopied(true),
          () => setCopied(false)
        )
      }
    >
      {copied ? t('Copied') : t('Copy')}
    </button>
  )
}

/** The WhatsApp message templates Meta has to approve before a message can
 *  reach a phone that hasn't written to the business number in 24 hours. */
function Templates(props: {
  templates: Record<string, string>
  disabled: boolean
  run: (work: () => Promise<void>) => Promise<void>
  reload: () => void
}): JSX.Element {
  const { templates, disabled, run, reload } = props
  const names = Object.keys(templates)
  const approved = names.filter((n) => templates[n] === 'APPROVED').length
  return (
    <div className="text-xs space-y-1">
      <p className="text-muted">
        {names.length === 0
          ? t('Message templates: not submitted yet. Until Meta approves them, messages only reach you within 24 hours of your last message to the business number.')
          : `${t('Message templates approved by Meta')}: ${approved} / ${names.length}` +
            (approved < names.length
              ? ` (${names
                  .filter((n) => templates[n] !== 'APPROVED')
                  .map((n) => `${n}: ${templates[n].toLowerCase()}`)
                  .join(', ')})`
              : '')}
      </p>
      <div className="flex gap-2">
        {approved < 4 && (
          <button
            className="btn-ghost !py-0.5 !px-2 text-xs"
            disabled={disabled}
            onClick={() =>
              void run(async () => {
                await remoteApproval.templates(true)
                reload()
              })
            }
          >
            {t('Submit templates to Meta')}
          </button>
        )}
        <button
          className="btn-ghost !py-0.5 !px-2 text-xs"
          disabled={disabled}
          onClick={() =>
            void run(async () => {
              await remoteApproval.templates(false)
              reload()
            })
          }
        >
          {t('Check status')}
        </button>
      </div>
    </div>
  )
}

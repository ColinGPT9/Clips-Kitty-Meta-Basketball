# Architecture

Three pieces: the creator's Windows PC (Clips Kitty, unchanged pipeline),
a small relay the creator deploys once (Cloudflare Worker + D1), and Meta's
official APIs. The PC never accepts a connection from the internet; it only
calls out to the relay.

```
 Creator's Windows PC (127.0.0.1 only)                Relay (Cloudflare Worker, D1)              Meta
 ─────────────────────────────────────                ─────────────────────────────              ────
 Basketball pipeline (E-BARD, YOLO, OpenCV,           holds: WhatsApp token, Meta + Threads
   scoreboard, events, crop, render)  [unchanged]       app secrets, page/IG/Threads tokens
        │ job finishes                                   (sealed with TOKEN_KEY)
        ▼
 server/jobs.py _announce ──► remote_approval/notifier
        │  offer: clip snapshot (size+mtime pinned),
        │  fingerprint, counts from the pipeline's events
        ▼
 remote_outbox (SQLite) ── publish worker tick ──► POST /v1/notify (HMAC-signed) ──► WhatsApp Cloud API
                                                    makes approval apr_…, link token         │ template
                                                    (hash stored), thumbnails                ▼
                                                                                        creator's phone
                                                    GET/POST /a/<token>  ◄──────────── opens "Review clips"
                                                    page: clips, platforms, confirm,
                                                    PIN (PBKDF2, lockout), CSRF, Origin
                                                    → signed command (approve/defer/cancel)
 publish worker tick ── GET /v1/commands ─────────►
   commands.handle: signature, offer, fingerprint,
   expiry, age, clips ⊆ offer, platforms connected,
   files unchanged → APPROVED → publish_jobs (provider "meta")
   ── POST /v1/commands/<id>/ack ──────────────────► command accepted (now its steps are allowed)

 publish worker _publish → remote_approval/publishing → publish/meta_graph
   POST /v1/meta/<op>  (each Graph step, named   ──► checks the step against the accepted ──► Graph API v26:
   by command + clip + platform)                      command, clip, platform, 24h window       IG Reels (resumable),
   POST /v1/meta/upload (the rendered file,      ──► streams bytes to rupload with the     ──► FB Page Reels,
   streamed; no re-render)                            account token                            Threads
                                                 ◄── ids and permalinks the API returned
   clip_publishes rows (provider "meta")
   settle → PUBLISHED / PARTIAL_FAILURE / FAILED
   outbox "result" ── POST /v1/notify ───────────► ck_publish_update template ─────────────► phone
```

## On the PC

| Module | Role |
|---|---|
| `remote_approval/notifier.py` | Called once from the job announcer. Basketball jobs only; makes the offer, never sends anything itself. |
| `remote_approval/summary.py` | Counts by category from the clips' own `sport_event` (clutch, dunks, threes, blocks, steals, assists, fan reactions, other), the snapshot of each offered file (path, size, mtime) and the offer's fingerprint. |
| `remote_approval/states.py`, `store.py` | The state machine (allowed moves only, `BEGIN IMMEDIATE`), the outbox (one row per event key: the duplicate guard), commands seen (replay guard), audit log. |
| `remote_approval/service.py` | The loop on the publish worker's thread: expire, settle, send the outbox, collect decisions. Paces itself: seconds while something is open, two minutes otherwise, nothing while off. Plus the actions Settings takes. |
| `remote_approval/commands.py` | Checks each decision from the phone against the offer on this PC. Only this file turns an approval into publish jobs. |
| `remote_approval/relay.py` | The signed HTTP client. Every request carries the device id, a timestamp, a nonce and an HMAC-SHA256 over method, path, timestamp, nonce and body hash. |
| `remote_approval/publishing.py` | One approved clip to the chosen platforms, Instagram and Facebook first so Threads can link to a confirmed Reel. Called by the existing publish worker. |
| `publish/meta_graph.py` | Meta's steps in Meta's order, through the relay; error codes turned into messages a creator can act on. |
| `remote_approval/captions.py` | Muse Spark: one strict-JSON request per offer, cached by fingerprint and model. |
| `remote_approval/usage.py` | Token, audio-second and message counts with estimated cost. |
| `server/remote_approval_api.py` | Settings routes; every change needs the desktop session header. |

Integration points with existing code, kept deliberately small: one call in
`server/jobs.py`, one step and one branch in `server/publisher.py`, four new
tables in `core/state.py`, one `install()` in `server/api.py`.

## On the relay

`meta-relay/worker.js` (routes), `lib/whatsapp.js` (Cloud API sends,
templates, error meanings), `lib/meta.js` (Facebook Login, Threads Login,
the Graph steps, uploads), `lib/page.js` (the approval page: server-rendered
HTML, no JavaScript, strict CSP), `lib/crypto.js` (HMAC, AES-GCM sealing,
PBKDF2 PIN hashing), `schema.sql` (D1). A cron every 10 minutes expires
offers, sends reminders and refreshes Threads tokens.

## Why a relay at all

WhatsApp webhooks and OAuth redirects need a public HTTPS address, and the
Meta app secret must live on a server. The brief also says the PC's FastAPI
service must not be exposed and the public desktop client must not hold app
secrets or long-lived tokens. A relay the creator owns satisfies all of
that with the least: no always-on processing (the video work stays on the
PC), one Worker and one small database, inside Cloudflare's free tier for a
single creator.

## Muse models

- **Muse Spark** (`muse-spark-1.3`) writes text: titles, Instagram captions,
  Facebook descriptions, Threads posts, hashtags, and an optional one-line
  summary, in one structured request per offer. It does not send anything.
- **Muse Voice Transcribe** (`muse-voice-transcribe-1.0`) is an option in
  Settings → AI → Transcription, on the existing interface, with local
  Whisper as the fallback.
- **WhatsApp** delivers. The ready message is deterministic (from the job's
  real metadata); the Muse Spark summary line is added only when the
  creator turns it on.

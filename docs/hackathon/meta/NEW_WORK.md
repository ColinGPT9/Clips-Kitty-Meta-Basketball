# What is new in this fork

This repository is a fork of [Clips Kitty](https://github.com/ColinGPT9/clips-studio)
made for the Meta Global AI Developer Hackathon. This page separates what
existed before the hackathon from what was built for it, as the rules ask.

## Original commit

The fork starts from Clips Kitty at:

```
6245e02332ba07e0c9b12d47a95ee81502f4122d
"Tests: a made-up Twitch channel, and the four open code scanning notes (#200)"
```

Everything in that commit and before it is prior work. Run
`git diff 6245e02 HEAD` to see exactly what the hackathon added.

## What existed before (not claimed as new)

- The whole local Windows app: the Electron + React desktop UI, the FastAPI
  engine on `127.0.0.1:8765`, the job queue, the editor, and rendering.
- The basketball pipeline: E-BARD tracking, YOLO and OpenCV ball and player
  tracking, scoreboard reading, event detection (dunks, threes, blocks and
  so on), 9:16 crop planning, captions, and rendering. **None of it was
  changed, retrained, or swapped for another model in this fork.**
- Local Whisper transcription, and online transcription through OpenAI,
  OpenRouter and xAI on the user's own key.
- The Meta (Muse Spark) chat provider in Settings → AI. Muse Spark could
  already pick clips and write metadata; this fork adds a new use of it (below).
- The publish worker and its queue (`server/publisher.py`, `publish_jobs`,
  `clip_publishes`), and the existing publishing providers: YouTube,
  Upload-Post and WoopSocial. WoopSocial stays available as an alternative;
  it is not how the new Meta publishing works.
- Desktop notifications, Discord campaign posting, remote rendering.

## What was built for the hackathon

| Area | What it does | Where |
|---|---|---|
| Finished-job hook | When a basketball job finishes or fails, an offer is made from the job's real clips and queued for WhatsApp. It hooks the one place jobs already announce themselves; no new job manager. | `server/jobs.py` (`_announce`), `remote_approval/notifier.py` |
| Approval state machine | PROCESSING → … → READY_FOR_REVIEW → NOTIFICATION_PENDING → NOTIFICATION_SENT → AWAITING_APPROVAL → (DEFERRED / CANCELLED / EXPIRED) → APPROVED → PUBLISH_QUEUED → PUBLISHING → PUBLISHED / PARTIAL_FAILURE / FAILED. Persisted in SQLite, every move audited, survives restarts. | `remote_approval/states.py`, `store.py`, `core/state.py` (4 new tables) |
| Highlight counts | The message's counts come from the pipeline's own clip events (2 dunks, 1 three, …), deterministic, never invented. | `remote_approval/summary.py` |
| Relay | A small Cloudflare Worker the creator deploys: WhatsApp Cloud API sends and webhook, the phone approval page, Facebook Login and Threads Login, and the Graph API calls. It holds every Meta token and the app secret; the PC never does. | `meta-relay/` |
| WhatsApp notifications | Official WhatsApp Cloud API only, with four utility templates (ready, failed, publish update, test). Pairing with a one-time code, confirmed on the PC. STOP and START. Quick replies: Remind me later, Cancel. A bare "yes" never approves anything. | `meta-relay/lib/whatsapp.js`, `meta-relay/worker.js` |
| Secure remote approval | Expiring single-purpose links, a PIN, an explicit confirm box, CSRF and Origin checks, signed commands, replay and duplicate protection, job- and platform-specific checks on the PC, fingerprinted clips. | `meta-relay/worker.js`, `remote_approval/commands.py` |
| PC ↔ relay link | Outbound only. Every request HMAC-signed with the PC's device secret, with a timestamp and a one-time nonce. No port is opened on the PC. | `remote_approval/relay.py`, `service.py` |
| Meta publishing | Instagram Reels (resumable upload), Facebook Page Reels, and a Threads post linking to the Reel, all through the official Graph APIs, uploading the file the pipeline already rendered (no re-render). A post counts as published only when the API says so; links are only what the API returned. | `publish/meta_graph.py`, `remote_approval/publishing.py`, `meta-relay/lib/meta.js` |
| Publishing through the existing queue | Approved clips become ordinary publish jobs on the existing worker (`provider: "meta"`), so they inherit its one-at-a-time and never-retry-an-interrupted-upload rules. | `server/publisher.py` |
| Results back to WhatsApp | "3 of 4 posts published (1 failed)", counted from confirmed results only; the status page shows each confirmed link. Partial failures are reported as such and can be retried from the app without posting twice. | `remote_approval/service.py` |
| Muse Spark captions | One structured (strict JSON schema) request per offer writes every clip's title, Instagram caption, Facebook description, Threads post and hashtags, plus an optional one-line summary. Cached and reused; never called for routine confirmations; facts only. | `remote_approval/captions.py` |
| Muse Voice Transcribe | Added to the existing online-transcription path. It returns turn-level timings only, so turns are kept as segments and no word timings are made up. If it fails, local Whisper transcribes instead. Player and team names are sent as keywords. | `transcription/cloud.py`, `transcriber.py`, `llm/providers/catalog.py` |
| Usage and cost | Muse Spark tokens (from the API's own usage block), Muse Voice audio seconds, WhatsApp template sends and Graph uploads, with estimated dollars. | `remote_approval/usage.py` |
| Settings | Settings → Notifications & Remote Approval: connect to the relay, pair and confirm a number, PIN, connect Facebook/Instagram and Threads, which messages to get, remote approval on/off (off by default), preferred destinations, Muse options, templates, recent offers with their links and actions, estimated cost. | `server/remote_approval_api.py`, `ui/.../NotificationsApprovalCard.tsx`, `ui/.../lib/remoteApproval.ts` |
| Demo | A narrated end-to-end run with Meta faked: `python scripts/meta_demo.py`. | `scripts/meta_demo.py`, `meta-relay/test/serve.mjs` |
| Tests | 29 relay tests (Node), and Python unit, end-to-end and API tests for everything above. | `meta-relay/test/`, `tests/test_remote_approval*.py`, `tests/test_meta_graph.py`, `tests/test_muse.py` |

## Files changed outside the new folders

Small, targeted edits to existing code (everything else is in new files):

- `core/state.py`: four new tables (`remote_reviews`, `remote_outbox`,
  `remote_commands`, `remote_audit`), created with `IF NOT EXISTS`.
- `server/jobs.py`: one contained call in `_announce` to queue the WhatsApp offer.
- `server/publisher.py`: the remote approval step in the worker loop, and a
  `provider: "meta"` branch at the top of `_publish`.
- `server/api.py`: installs the new routes.
- `transcription/cloud.py`, `transcription/transcriber.py`,
  `llm/providers/catalog.py`, `llm/providers/speech.py`: Muse Voice.
- `ui/src/renderer/src/pages/Settings.tsx`, `lib/types.ts`,
  `ui/src/main/index.ts`: the new card, its event type, and the `wa.me`
  pairing link allowed to open.
- `.github/workflows/ci.yml`, `clips-studio.spec`,
  `_clipskitty_script_host.py`, `sdk/python/clipskitty_sdk/lint.py`: the new
  package compiled, bundled and listed as engine code; a CI job for the relay tests.
- `docs/developers/api-reference.md`: regenerated (the new routes are internal).

## Tests performed

See [TEST_LOG.md](TEST_LOG.md) for the commands and results. In short: all
new tests pass, including an end-to-end test that runs the real relay code
against the real engine code with only Meta's servers faked. The full
existing suite shows the same environment-only failures as the baseline
(missing fonts in the test container) and no new ones.

## What was demonstrated

With Meta faked (no accounts, no keys): a finished basketball video → a
WhatsApp template message with the real counts → the approval page on a
phone-sized screen → three clips approved to Instagram, Facebook and Threads
with the PIN → the PC picks it up, checks it, and uploads the rendered files
→ nine posts confirmed → "9 of 9 posts published" back on WhatsApp. See
[DEMO_SCRIPT.md](DEMO_SCRIPT.md) and the screenshots in [img/](img/).

**Not yet demonstrated against live Meta services.** That needs a Meta app,
a WhatsApp business number, a deployed relay and connected accounts, which
need the creator's own logins. The steps are in
[JUDGE_SETUP.md](JUDGE_SETUP.md) and [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md).

## Remaining limitations

- **Meta App Review.** Without Advanced Access, `instagram_content_publish`,
  `pages_manage_posts` and `threads_content_publish` work only for accounts
  that have a role on the Meta app (admin, developer or tester). That is
  enough for a creator's own accounts and for the demo; publishing for
  other people's accounts needs App Review. See [META_PUBLISHING.md](META_PUBLISHING.md).
- **WhatsApp templates** must be approved by Meta before a message can reach
  a phone outside the 24-hour customer service window. Until then the relay
  falls back to a free-form message, which only works within 24 hours of the
  creator's last message to the business number.
- **Clip size.** Every byte goes through the relay in one request, and
  Cloudflare limits a request to 100 MB on the Free and Pro plans, so a clip
  over 95 MB is refused with a clear message. Highlight clips are far smaller.
- **Threads** has no upload from a file (video only from a public URL), so
  the Threads post is a text post linking to the Instagram or Facebook Reel
  just published. If neither went up, Threads is skipped, not faked.
- **Muse Voice** times turns, not words. Word-by-word captions built from it
  are spread across each turn, so they are approximate; keep Whisper for
  word-exact captions.
- **Remote approval needs the PC to come back.** An approval made while the
  PC is off waits on the relay and is honoured when it returns, within 48
  hours; after that the creator is asked again rather than having it posted late.
- **One relay per creator.** The relay is single-tenant by design (the
  creator's own Meta app and tokens); it is not a hosted multi-user service.

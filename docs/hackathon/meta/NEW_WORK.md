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
- The basketball pipeline: [E-BARD](https://github.com/GabrieleGiudic/E-BARD) tracking (used unmodified under CC BY 4.0), YOLO and OpenCV ball and player
  tracking, scoreboard reading, event detection (dunks, threes, blocks and
  so on), 9:16 crop planning, captions, and rendering. **None of it was
  changed, retrained, or swapped for another model in this fork.**
- Local Whisper transcription, and online transcription through OpenAI,
  OpenRouter and xAI on the user's own key.
- The Meta (Muse Spark) chat provider in Settings → AI. Muse Spark could
  already pick clips and write metadata; this fork adds a new use of it (below).
- The publish worker and its queue (`server/publisher.py`, `publish_jobs`,
  `clip_publishes`), and the existing publishing providers: YouTube and two
  third-party posting services. They stay available as alternatives; none of
  them is how the new Meta publishing works.
- Desktop notifications, Discord campaign posting, remote rendering.

## What was built for the hackathon

| Area | What it does | Where |
|---|---|---|
| Finished-job hook | When a job finishes, an offer is made from the clips that run made and queued for the relay, which sends it on WhatsApp or keeps it for Muse; when one fails, WhatsApp gets a plain failure message. Basketball only by default (see Gaming and other videos below). It hooks the one place jobs already announce themselves; no new job manager. | `server/jobs.py` (`_announce`), `remote_approval/notifier.py` |
| Approval state machine | PROCESSING → … → READY_FOR_REVIEW → NOTIFICATION_PENDING → NOTIFICATION_SENT → AWAITING_APPROVAL → (DEFERRED / CANCELLED / EXPIRED) → APPROVED → PUBLISH_QUEUED → PUBLISHING → PUBLISHED / PARTIAL_FAILURE / FAILED. Persisted in SQLite, every move audited, survives restarts. | `remote_approval/states.py`, `store.py`, `core/state.py` (4 new tables) |
| Highlight counts | The message's counts come from the pipeline's own clip events (2 dunks, 1 three, …), deterministic, never invented. | `remote_approval/summary.py` |
| Relay | A small Cloudflare Worker the creator deploys: WhatsApp Cloud API sends and webhook, the phone approval page, Facebook Login and Threads Login, and the Graph API calls. It holds every WhatsApp and Graph API token and the app secret; the PC never does. The PC keeps only the Meta Model API key for Muse Spark and Muse Voice (Settings → AI). | `meta-relay/` |
| WhatsApp notifications | Official WhatsApp Cloud API only, with four utility templates (ready, failed, publish update, test). Pairing with a one-time code, confirmed on the PC. STOP and START. Quick replies: Remind me later, Cancel. A bare "yes" never approves anything. | `meta-relay/lib/whatsapp.js`, `meta-relay/worker.js` |
| Secure remote approval | Expiring single-purpose links, a PIN, an explicit confirm box, CSRF and Origin checks, signed commands, replay and duplicate protection, job- and platform-specific checks on the PC, fingerprinted clips. | `meta-relay/worker.js`, `remote_approval/commands.py` |
| PC ↔ relay link | Outbound only. Every request after registration (which uses the relay owner's enrolment key) is HMAC-signed with the PC's device secret, with a timestamp and a one-time nonce. No port is opened on the PC. | `remote_approval/relay.py`, `service.py` |
| Meta publishing | Instagram Reels (resumable upload), Facebook Page Reels, and a Threads post linking to the Reel, all through the official Graph APIs, uploading the file the pipeline already rendered (no re-render). A post counts as published only when the API says so; links are only what the API returned. | `publish/meta_graph.py`, `remote_approval/publishing.py`, `meta-relay/lib/meta.js` |
| Publishing through the existing queue | Approved clips become ordinary publish jobs on the existing worker (`provider: "meta"`), so they inherit its one-at-a-time and never-retry-an-interrupted-upload rules. | `server/publisher.py` |
| Results back to WhatsApp | "3 of 4 posts published (1 failed)", counted from confirmed results only; the status page shows each confirmed link. Partial failures are reported as such and can be retried from the app without posting twice. | `remote_approval/service.py` |
| Muse Spark captions | One structured (strict JSON schema) request per offer writes every clip's title, Instagram caption, Facebook description, Threads post and hashtags, plus an optional one-line summary. Cached and reused; never called for routine confirmations; facts only. | `remote_approval/captions.py` |
| Muse Voice Transcribe | Added to the existing online-transcription path. It returns turn-level timings only, so turns are kept as segments and no word timings are made up. If it fails, local Whisper transcribes instead. Player and team names are sent as keywords. | `transcription/cloud.py`, `transcriber.py`, `llm/providers/catalog.py` |
| Usage and cost | Muse Spark tokens (from the API's own usage block), Muse Voice audio seconds, WhatsApp template sends and Graph uploads, with estimated dollars. | `remote_approval/usage.py` |
| Settings | Settings → Notifications & Remote Approval: connect to the relay, pair and confirm a number, PIN, connect Facebook/Instagram and Threads, which messages to get, remote approval on/off (off by default), preferred destinations, Muse options, templates, recent offers with their links and actions, estimated cost. | `server/remote_approval_api.py`, `ui/.../NotificationsApprovalCard.tsx`, `ui/.../lib/remoteApproval.ts` |
| Demo | A narrated end-to-end run with Meta faked: `python scripts/meta_demo.py`. | `scripts/meta_demo.py`, `meta-relay/test/serve.mjs` |
| Tests | Relay tests under Node (29 in the first version, 58 now), and Python unit, end-to-end and API tests for everything above and below. | `meta-relay/test/`, `tests/test_remote_approval*.py`, `tests/test_meta_graph.py`, `tests/test_muse.py` |

### Added on October 30, 2026

| Area | What it does | Where |
|---|---|---|
| Your Muse agent | A connector for Muse, Meta's personal agent app, on the creator's own relay: a remote MCP server (`POST /mcp`, the `initialize` handshake and the stateless 2026-07-28 revision), the same six tools as plain HTTPS routes (`/muse/v1/...`) and an OpenAPI description (`/muse/openapi.json`). Muse can list the highlights waiting, hand over the approval link, say what got posted with the platforms' own links, and remind or cancel. No tool approves, publishes or takes a PIN. Offers work with Muse alone, with no WhatsApp number. See [MUSE_CONNECTOR.md](MUSE_CONNECTOR.md). | `meta-relay/lib/muse.js`, `meta-relay/worker.js`, `meta-relay/schema.sql` (`connector_tokens`) |
| Muse keys | Made in Settings, one per PC, shown once and never kept on the PC. The relay keeps only a SHA-256 of each `ckm_` key, takes it only in the `Authorization` header, allows about 60 calls a minute, and refuses it on the PC's own routes. **Revoke** stops it at once. | `meta-relay/lib/muse.js`, `server/remote_approval_api.py`, `NotificationsApprovalCard.tsx` |
| A harder PIN | Each PIN try is counted before it is checked, so guesses sent all at once can't get past the lockout. Three 15-minute lockouts in a day block approving from the phone until a new PIN is set on the PC. | `meta-relay/worker.js`, `meta-relay/lib/db.js` (`pin_lockouts`), `meta-relay/lib/page.js` |
| Gaming and other videos | Offers stay basketball-only by default. **Offer gaming and other videos too** (Settings, **5. Approve from your phone**) adds every other finished video. Each offer is counted in its own words: plays for basketball, a game pipeline's own labels for gaming ("2 chases and 1 crash"), and "3 highlights" for anything else. The captions follow the same kind. | `remote_approval/summary.py`, `settings.py`, `captions.py` |
| Only this run's clips | An offer holds only the clips the finished run made (files written after the job started), never an earlier run's. Longform cuts and story reels are never offered. | `remote_approval/notifier.py`, `server/jobs.py` |
| Never posted twice | A platform where a clip is already posted, or on its way there (scheduled or uploading), by any publisher, is left out of the approval and shown as skipped in the results. The page says where a clip is already posted. | `remote_approval/store.py`, `commands.py`, `service.py` |
| Watch page: Ask me on my phone | A watched YouTube, Twitch or Kick channel can send its clips to the phone for approval, so a new upload or a finished stream's VOD becomes an offer. In this edition the Watch page offers only **Ask me on my phone** (the default) and **Only clip**: posting goes through Meta's own APIs, after approval. A phone watch's videos are offered whatever kind they are. **Check now** also looks again at videos still waiting to be ready. | `server/automation.py`, `remote_approval/notifier.py`, `ui/.../pages/Watch.tsx`, `ui/.../watch/` |
| Watch a folder | A folder on this PC, such as a Google Drive for desktop folder, watched like a channel. A video counts once it has stopped changing for 60 seconds and no other program is still writing it; online-only files wait until they are on the PC. What is in the folder when watching starts counts as earlier. A file gets the same id as when it is added by hand, so it is never clipped twice, and the original is only read. | `sources/folder_feed.py`, `sources/local_file.py`, `core/paths.py` |
| Ask Clips Kitty on Muse Spark | With a Meta Model API key saved, the box at the bottom of the app runs on Muse Spark, whatever model picks the clips, and its corner says **Muse Spark**. Without a key it runs as before on a model that can call tools; only when there is none does it say where the key goes. Settings → AI and the sidebar say when the assistant uses Muse Spark. | `server/agent.py` (`muse_backend`), `server/api.py`, `llm/providers/catalog.py`, `ui/.../Assistant.tsx` |
| Tests | 28 connector tests under Node, the Muse flow end to end against the real relay (with and without WhatsApp), the folder watch, the phone mode and the box on Muse Spark. | `meta-relay/test/muse.test.mjs`, `tests/test_remote_approval_e2e.py`, `tests/test_folder_feed.py`, `tests/test_automation.py`, `tests/test_agent_muse.py` |

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
- `README.md`: replaced by a README for this edition only. The original's
  README is in ColinGPT9/clips-studio, and links into it now point there.

For the October 30 additions:

- `server/automation.py`: the **Ask me on my phone** mode, folder watches,
  **Check now** looking again at videos still waiting, and what a phone
  watch's row says.
- `server/api.py`: adding a file by hand now uses the same id and copy step
  as a watched folder (moved to `sources/local_file.py`, unchanged); the
  assistant's status and chat routes run on Muse Spark when a Meta key is saved.
- `server/agent.py`: `muse_backend`, and the box's wording without a key.
- `server/jobs.py`: hands the notifier the job's own start time, so an offer
  holds only that run's clips, and the data folder, so it can tell a game's
  own pipeline from any other.
- `core/paths.py`: `picked_folder`, the checks on a folder chosen to watch.
- `llm/providers/catalog.py`: Meta tool requests ask for brief reasoning
  (`reasoning_effort: "low"`).
- `ui/src/main/index.ts`, `ui/src/preload/index.ts`, `ui/.../env.d.ts`: the
  **Choose folder** dialog.
- `ui/.../pages/Watch.tsx`, `components/watch/WatchCard.tsx`,
  `WatchPublishSettings.tsx`, `components/queue/QueueItemSettings.tsx`,
  `components/ConnectedAccountsCard.tsx`, `lib/steps.ts`, `lib/types.ts`:
  Folder, the two publishing choices, the phone status, and the gaming
  detector on an existing watch.
- `ui/.../components/Assistant.tsx`, `AICard.tsx`, `App.tsx`,
  `lib/aiSetup.ts`, `lib/useAIStatus.ts`, `lib/api.ts`: the box on Muse
  Spark, and the lines that say when the assistant uses it. `lib/api.ts`
  also sends a local file's Sports choice and clip direction, which it
  dropped before (found in the Windows run).
- `docs/API.md`: the folder platform, the phone mode and **Check now**.
- `docs/developers/` (five pages): links into the original README now point at
  ColinGPT9/clips-studio.
- Six test files (`test_launch_drafts.py`, `test_plugin_docs.py`,
  `test_plugin_sdk_install.py`, `test_plugin_sdk_package.py`,
  `test_site_developers.py`, `test_ui_store_update.py`): their checks of the
  original README skip here, where that README isn't.
- Removed: `docs/hackathon/JUDGE_SETUP.md`, `SUBMISSION.md` and `TEST_LOG.md`,
  older records from the original repository, where they stay.
- `ARCHITECTURE.md`, `docs/SPORTS.md`, `docs/CONNECTED_ACCOUNTS.md`: the box
  at the bottom on Muse Spark, and phone approval for a new Twitch watch.
- `tests/test_automation.py`, `tests/test_llm_adapters.py`: tests for the above.

## Tests performed

See [TEST_LOG.md](TEST_LOG.md) for the commands and results. In short: all
new tests pass, including an end-to-end test that runs the real relay code
against the real engine code with only Meta's servers faked. The full
existing suite was last run on October 29, in a Linux container: every
failure there was an environment one (fonts and mascot images that
container can't reproduce) or failed on the original commit too. After the
October 30 merge, the Meta, watcher and job test files were run again on
Windows: one existing test fails, a schedule preview that spans the
1 November clock change, and it fails the same way before these features.

## What was demonstrated

With Meta faked (no accounts, no keys): a finished basketball video → a
WhatsApp template message with the real counts → the approval page on a
phone-sized screen → three clips approved to Instagram, Facebook and Threads
with the PIN → the PC picks it up, checks it, and uploads the rendered files
→ nine posts confirmed → "9 of 9 posts published" back on WhatsApp. See
[DEMO_SCRIPT.md](DEMO_SCRIPT.md) and the screenshots in [img/](img/).

The end-to-end tests also play Muse against the real relay code: it lists
the offer, hands over the approval link, and reads what got posted, with
and without a WhatsApp number. On Windows, the app itself was run against
the mock relay before these October 30 features, up to the offer reaching
the fake phone. The new Settings and Watch page screens are not tried by
hand yet: see [TEST_LOG.md](TEST_LOG.md).

**Not yet demonstrated against live Meta services.** That needs a Meta app,
a WhatsApp business number, a deployed relay, connected accounts and a Muse
account with the connector added, which need the creator's own logins. The
steps are in [JUDGE_SETUP.md](JUDGE_SETUP.md), [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md)
and [MUSE_CONNECTOR.md](MUSE_CONNECTOR.md).

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
  hours; after that it is refused and the creator is told to ask for a new offer (Ask again in Settings), rather than having it posted late.
- **One relay per creator.** The relay is single-tenant by design (the
  creator's own Meta app and tokens); it is not a hosted multi-user service.
- **Muse's connectors are not tried live yet.** Which MCP revision Muse
  speaks, whether it accepts a key in a header or only OAuth (the relay has
  no OAuth sign-in for connectors), and whether its servers send an `Origin` header are not
  confirmed; [MUSE_CONNECTOR.md](MUSE_CONNECTOR.md) lists what to try.
  Muse can't receive a message from an outside app (only calendars can), so
  it learns about new highlights when asked, or when asked to check on a
  schedule.
- **The box on Muse Spark** sends `reasoning_effort: "low"` with its tools.
  Meta's acceptance of that pairing is not yet confirmed with a live request.
- **Folder watch and Google Drive.** Google Drive for desktop's **Stream
  files** mode doesn't mark a file as online-only, so reading one waits on
  Drive's download, and a streamed folder hasn't been tried. Use **Mirror
  files**, or make the folder available offline.

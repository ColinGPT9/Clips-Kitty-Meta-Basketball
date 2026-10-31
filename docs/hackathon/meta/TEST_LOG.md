# Test log

All runs below are with Meta's servers faked: no real WhatsApp message was
sent and nothing was posted. Live runs against Meta will be added here.

Environment: Linux container, Python 3.13, Node 22.22, ffmpeg 6; and a clean
virtual environment with only the listed packages, to match CI.

## October 29, 2026: new tests

| Suite | Command | Result |
|---|---|---|
| Relay (Node) | `cd meta-relay && npm test` | **29 passed**, 0 failed |
| PC: offers, state machine, decisions, expiry, retries, results | `pytest tests/test_remote_approval.py` | **39 passed** |
| PC: Graph steps, error mapping, caption limits, publishing through the publish worker | `pytest tests/test_meta_graph.py` | **25 passed** |
| PC + real relay end to end (relay under Node, Meta faked) | `pytest tests/test_remote_approval_e2e.py` | **6 passed** |
| Settings routes, incl. set-up against the real relay | `pytest tests/test_remote_approval_api.py` | **21 passed** (skipped without FastAPI, as in CI's main job) |
| Muse Voice Transcribe and Muse Spark captions | `pytest tests/test_muse.py` | **12 passed** |
| Clean venv (`pyyaml requests pytest fastapi httpx`) | all five files above | **103 passed** |
| Clean venv with CI's main-job packages (`pyyaml ruff pytest requests`) | the five files + `test_cloud_transcription.py` + `test_script_host.py` | all passed, API module skipped |
| Lint | `ruff check .` | clean |
| Desktop UI | `cd ui && npm run typecheck && npm run build` | both pass |
| API reference | `python scripts/gen_api_reference.py` then `pytest tests/test_api_contract.py` | up to date, passes |

What the end-to-end tests drive, against the real relay code: registering a
PC with the enrolment key; pairing (phone sends `LINK CODE`, PC confirms);
the PIN; Facebook + Instagram and Threads sign-in; a finished game's offer;
the approval page with CSRF, Origin, confirm box and PIN; three clips to
three platforms uploaded byte for byte; results settled from confirmed
posts and sent back; an approval made while the PC is off; a bare "yes";
Remind me later and Cancel buttons; a wrong PIN; the fixed failure message
with no error text; an undelivered message sent again with a new link.

## Bugs the tests found (fixed)

- One WhatsApp number paired with two PCs: button taps were matched to
  whichever PC came first and silently dropped. Taps now go to the PC that
  made the offer, and STOP/START apply to the number on every PC.
- A failed registration (wrong enrolment key) left an unaccepted device
  secret behind, so Settings showed the PC as connected.
- "Send again" after a failed delivery reused the old message key, which the
  relay deduplicates, so nothing was resent. It now uses a new key and a new link.
- A message queued by a finished job could wait up to two minutes for the
  relay loop's slow pace; it now goes on the next pass.

## The existing test suite

`python -m pytest` over the whole repository, in this container:
**5,473 passed, 29 skipped, 12 failed**. The original commit (`6245e02`) in
the same container gives 5,371 passed, 29 skipped and 11 failed. The extra
passes are the new tests.

None of the 12 failures come from this branch:

- 11 fail the same way on the original commit:
  - 10 are end-card and outro tests (`test_outro`, `test_outro_join`,
    `test_vertical_live`). Pillow here can't open the bundled font
    (`OSError: cannot open resource`).
  - `test_clip_intent::test_a_time_range_gets_windows_and_points` fails too.
- `test_outro::test_mascot_files_still_regenerate_identically` fails here
  only while the committed mascot PNGs are untouched. This container's
  image library writes different bytes. After one run of
  `scripts/make_mascot.py`, the test passes, which is how it passed in the
  baseline run. The branch doesn't touch the mascot, `video/` or that script.

The suite also rewrites `docs/brand/mascot*.png` and `ui/build/icon.ico` as
a side effect. They were restored before every commit.

## The mock demo

`python scripts/meta_demo.py` (also in the clean venv with only
`pyyaml requests`): 5 rendered clips → template message "5 clips: 2 dunks,
1 three, 1 block and 1 steal" → 3 clips approved to Instagram, Facebook and
Threads with the PIN → 3 publish jobs → 9 posts confirmed with their links
→ "9 of 9 posts published" sent back. Screenshots: [img/](img/).

## Windows run (October 30, 2026)

The Windows test steps on the creator's Windows 10 PC, still with Meta
faked. The branch was at `3cbccd8` to start.

Tools: Python 3.11.0, Node v22.11.0, FFmpeg (June 2026 build).

| Step | Result |
|---|---|
| The five PC test files in a venv with `pyyaml requests pytest fastapi httpx` | Before the fix: 96 passed, 7 errors. After: **103 passed** |
| Relay: `cd meta-relay; npm test` | Before the fix: the tests could not load. After: **29 passed** |
| `python scripts\meta_demo.py` | Ran cleanly: offer, template message, approval with the PIN, 9 of 9 fake posts, results message, audit trail |
| The app from source (`ui`: `npm run dev`) with the mock relay (`meta-relay`: `npm run dev`) | Relay connected with the enrolment key, number paired from the fake phone, PIN set, Facebook + Instagram and Threads connected through the mock. A 90-second cut of the public-domain 1995 game, run as Sports → Basketball, made an offer, and the fake phone got the `ck_highlights_ready` message |

Fixed on this branch:

- `f1b5ebd`: Node 22.5 to 22.12 only have `node:sqlite` when started with
  `--experimental-sqlite`; Node 22.13 dropped the flag. The 7 errors were
  the tests that start the relay. The flag is now passed everywhere the
  relay starts (the npm scripts, `meta_demo.py`, the end-to-end test and
  CI), and Node 22.13.0 and 24.22.2 accept it too. Not a Windows problem:
  any Node from 22.5 to 22.12 hits it.
- `6991bf6`: a video added from the computer lost its Sports choice, clip
  direction and plugin options before it reached the engine. The first run
  was processed as an ordinary video, so, correctly, nothing was offered.
  `addLocalVideo` now sends them, and `tests/test_ui_local_upload.py` runs
  the real `api.ts` under Node to check what it sends.

Found in this run, and fixed afterwards (see the Meta-edition features
below), with tests including
`test_only_the_clips_this_run_rendered_are_offered` and
`test_a_clip_counts_once_in_its_first_category`:

- The offer also held the clips of an earlier, non-basketball run of the
  same video: 4 clips, only 1 of them from this run. Clips are kept per
  video, so the offer now takes only the clips whose files were written
  after the job started.
- A clip labelled `big_moment` was counted as "other plays" in the message.
  It now reads "1 big moment".

Not done in the app: approving on the `/a/...` page with the PIN, the fake
uploads and the result in the card. The end-to-end tests and the demo above
cover that flow against the real relay code.

The test steps pointed to section 3 of `MORNING_RUNBOOK.md`, which is not in
the repository; the run followed the Settings card and the docs in this folder.

## Meta-edition features (October 30, 2026)

Four features built after the Windows run, each reviewed and fixed on its
own branch before merging, still with Meta faked:

- Offers for gaming and other videos behind **Offer gaming and other videos
  too**, counted in each kind's own words. An offer now holds only the clips
  the finished run made, and `big_moment` clips count as "big moments": the
  two problems the Windows run found. A clip is never posted twice to a
  platform.
- The Muse connector on the relay, with offers for Muse alone
  ([MUSE_CONNECTOR.md](MUSE_CONNECTOR.md)).
- **Ask me on my phone** and folder watches on the Watch page.
- **Ask Clips Kitty** on Muse Spark.

Results after merging, at `4d7078d`, on the same Windows PC:

| Suite | Result |
|---|---|
| Full dependencies: the Meta, assistant, folder, local upload, API contract, MCP, clip-intent and plugin-docs test files | **381 passed** |
| Full dependencies: the watcher and job test files | **695 passed**, 1 failed (below) |
| Venv with `pyyaml requests pytest fastapi httpx`: the five Meta files, `test_agent_muse.py`, `test_folder_feed.py`, `test_ui_local_upload.py` | **180 passed**, 13 skipped (they need numpy) |
| Relay: `cd meta-relay; npm test` | **58 passed** (30 relay, 28 connector) |
| UI `npm run typecheck`, `ruff check .`, `git diff --check` | clean |
| The Watch page's regression scripts (`ui/scripts/test-watch-detector.cjs`, `test-connected-accounts.cjs`) | pass |

Re-run later on 30 October, after this edition got its own README: the first
row's files gave **380 passed** and 1 skipped, a check that reads the original
app's README, which is in ColinGPT9/clips-studio. The other rows are unchanged:
none of their files reads the README.

The one failure, `test_automation.py::test_the_preview_shows_the_slots_that_would_be_used`,
is in the original app's posting schedule preview: its third slot comes out
an hour early because the preview spans the 1 November 2026 clock change. It
fails the same way on the commit before these features.

Bugs the reviews found (fixed):

- **Wrong PINs sent all at once got past the lockout.** Each guess read the
  same failure count before any was added, so 120 guesses in bursts of 40
  locked nothing, and the right PIN in a fourth burst approved. Each try is
  now counted before it is checked: the same bursts check 5 PINs, lock once,
  and approve nothing.
- A clip another publisher had scheduled, or was still uploading, could be
  approved for the same platform and go up twice. Posts on their way now
  count too.
- **Retry failed** could send an upload again that a restart had cut off,
  which may already be up.
- A plugin pipeline that isn't a game's own was counted as gaming.
- A watched folder clipped a file whose copy had paused for over a minute
  (half copied), and an online-only file held up every watch while it
  downloaded. The watch now waits for a file no program is still writing,
  and for online-only files to be on the PC.
- A phone watch's row said its clips were sent based on the settings, not
  on whether an offer was made.
- After merging (`4d7078d`): with WhatsApp off and Muse connected, a phone
  watch could look before its offer was made and say WhatsApp was needed.
  It now checks Muse too, and the row says *"Ready to approve from your
  phone."*, which is true for both.

## Not yet tested

- Against live Meta services (needs the creator's Meta app, business
  number, relay deployment and accounts): see
  [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md) and
  [META_PUBLISHING.md](META_PUBLISHING.md).
- Approving a real offer from the Windows app (the PIN page, the fake
  uploads and the card's result): see the Windows run above.
- The Muse connector with Muse itself: which MCP revision Muse speaks,
  whether it takes a key in a header or needs OAuth, and whether its
  servers send an `Origin` header ([MUSE_CONNECTOR.md](MUSE_CONNECTOR.md)).
- **Ask Clips Kitty** against Meta's real API: its tool requests send
  `reasoning_effort: "low"` with `max_tokens`, which only the faked API has
  accepted so far. One live message settles it.
- A folder in Google Drive for desktop's **Stream files** mode, which marks
  no file as online-only.
- The new Settings and Watch page screens by hand: so far they are checked
  by the type check and the tests.
- With a real basketball game through the vision pipeline: unchanged by
  this fork, so the hook was tested with clip rows and real rendered files.

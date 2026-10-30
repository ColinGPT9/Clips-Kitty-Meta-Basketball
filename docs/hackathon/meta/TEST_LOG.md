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

Found in this run, fixed afterwards in the cloud with tests
(`test_only_the_clips_this_run_rendered_are_offered`,
`test_a_clip_counts_once_in_its_first_category`):

- The offer also held the clips of an earlier, non-basketball run of the
  same video: 4 clips, only 1 of them from this run. Clips are kept per
  video, so the offer now takes only the clips whose files were written
  after the job was queued.
- A clip labelled `big_moment` was counted as "other plays" in the message.
  It now reads "1 big moment".

Not done in the app: approving on the `/a/...` page with the PIN, the fake
uploads and the result in the card. The end-to-end tests and the demo above
cover that flow against the real relay code.

The test steps pointed to section 3 of `MORNING_RUNBOOK.md`, which is not in
the repository; the run followed the Settings card and the docs in this folder.

## Not yet tested

- Against live Meta services (needs the creator's Meta app, business
  number, relay deployment and accounts): see
  [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md) and
  [META_PUBLISHING.md](META_PUBLISHING.md).
- Approving a real offer from the Windows app (the PIN page, the fake
  uploads and the card's result): see the Windows run above.
- With a real basketball game through the vision pipeline: unchanged by
  this fork, so the hook was tested with clip rows and real rendered files.

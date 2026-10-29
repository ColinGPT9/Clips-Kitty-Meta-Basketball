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

## Not yet tested

- Against live Meta services (needs the creator's Meta app, business
  number, relay deployment and accounts): see the morning runbook.
- On Windows (the container is Linux). The PC side uses the app's existing
  Windows paths and DPAPI secret storage, which the original test suite covers.
- With a real basketball game through the vision pipeline: unchanged by
  this fork, so the hook was tested with clip rows and real rendered files.

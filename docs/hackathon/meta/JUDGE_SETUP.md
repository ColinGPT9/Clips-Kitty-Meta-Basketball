# For judges: running it

## Fastest: the mock run (2 minutes, no accounts)

Requirements: Python 3.11+, Node 22.5+, ffmpeg.

```
git clone https://github.com/ColinGPT9/Clips-Kitty-Meta-Basketball
cd Clips-Kitty-Meta-Basketball
pip install pyyaml requests
python scripts/meta_demo.py
```

This runs the whole notification → approval → publishing → confirmation
flow with the real relay and engine code and Meta's servers faked. See
[DEMO_SCRIPT.md](DEMO_SCRIPT.md) for what each step shows.

## The tests

```
pip install pyyaml requests pytest fastapi httpx
python -m pytest -q tests/test_remote_approval.py tests/test_remote_approval_e2e.py \
    tests/test_remote_approval_api.py tests/test_meta_graph.py tests/test_muse.py
cd meta-relay && npm test
```

`test_remote_approval_e2e.py` starts the relay under Node and drives the
phone (pairing, the approval page with PIN and CSRF, quick replies, a
delivery failure) against the real engine code.

## The full app with live Meta services

This needs your own Meta app, WhatsApp business number, Cloudflare account,
and Instagram/Facebook/Threads accounts with a role on the app.

1. Install Clips Kitty from this repository (Windows): see the main
   [README](../../../README.md) for running from source.
2. Deploy the relay and connect WhatsApp: [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md).
3. Add Facebook Login for Business and the Threads API, and connect the
   accounts: [META_PUBLISHING.md](META_PUBLISHING.md).
4. In Settings → Notifications & Remote Approval: set the PIN, switch on
   **Let me approve publishing from my phone**, pick preferred destinations.
5. Optional: Settings → AI, add a Meta Model API key; turn on **Write
   captions with Muse Spark**, and choose **Muse Voice Transcribe** under
   transcription.
6. Process a short basketball clip with the Basketball sport selected.

## What to look for

- The WhatsApp counts match the clips in the app.
- The approval page lists only connected platforms and refuses without the
  PIN and the confirm box.
- A plain "yes" in WhatsApp gets guidance, not an approval.
- Only confirmed posts are reported as published, with the API's links.
- Settings shows the offer, its state, its links, and the estimated cost.

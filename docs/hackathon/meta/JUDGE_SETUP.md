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
    tests/test_remote_approval_api.py tests/test_meta_graph.py tests/test_muse.py \
    tests/test_folder_feed.py tests/test_agent_muse.py
cd meta-relay && npm test
```

`test_remote_approval_e2e.py` starts the relay under Node and drives the
phone (pairing, the approval page with PIN and CSRF, quick replies, a
delivery failure) against the real engine code. It also plays Muse: listing
the offer, getting the approval link and reading what got posted, with and
without a WhatsApp number. The whole-app tests in `test_agent_muse.py`, and
one in `test_folder_feed.py`, skip without the app's full dependencies (numpy
and the rest of `requirements.txt`).

## Your Muse agent, on the mock relay

Muse itself needs the relay on a public address, but its connector can be
called on your machine. This needs the app running from source in this
repository, as in the README's [Quick start](../../../README.md#quick-start).

1. `cd meta-relay && npm run dev`. It prints the address
   (`http://127.0.0.1:8787`) and the enrolment key.
2. In Clips Kitty, **Settings → Notifications & Remote Approval → 1. Your
   relay**: that address and key, then **Connect this PC**.
3. **2. Your Muse agent → Make a Muse key**. The key (`ckm_...`) is shown
   once. Put it in an environment variable rather than typing it into commands.
4. Call the connector the way Muse would (from Git Bash or another Unix shell):

   ```
   curl http://127.0.0.1:8787/muse/openapi.json
   curl -H "Authorization: Bearer $MUSE_KEY" http://127.0.0.1:8787/muse/v1/status
   curl -H "Authorization: Bearer $MUSE_KEY" http://127.0.0.1:8787/muse/v1/offers
   curl -X POST http://127.0.0.1:8787/mcp -H "Authorization: Bearer $MUSE_KEY" \
     -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
   ```

5. To have something to list: set a PIN in section 4, connect the accounts,
   tick **Let me approve publishing from my phone**, and process a short
   basketball clip with Sports → Basketball. The offer shows as **Ready for Muse** in Settings;
   `GET /muse/v1/offers/{approval_id}/link` returns the link to its
   approval page, which still needs the PIN.

   **Connect** opens Meta's real sign-in page, which can't finish against the
   mock. Copy that page's address from the browser and finish the sign-in
   with the mock instead:

   ```
   curl -X POST http://127.0.0.1:8787/__mock/oauth -H "Content-Type: application/json" \
     -d '{"url":"<that address>"}'
   ```

## A watched folder

1. **Watched channels** page: switch **Watch channels** on (it starts off,
   and nothing is checked until it is). In **Add a channel**, set the platform
   to **Folder**, press **Choose folder** and pick an empty folder on this PC
   (not a network path, not inside Clips Kitty's own data folder). Turn on
   Sports → Basketball; **When a video is added** is **Ask me on my phone**.
   Then press **Add**.
2. A watch skips videos shorter than 3 minutes. For a short test clip, set
   **Skip videos shorter than (minutes)** to 0 in the watch's publishing
   settings before adding the file.
3. Copy a short basketball clip into the folder. The watch queues it a
   minute or two after the copy finishes (a file counts once it has stopped
   changing for 60 seconds, and the watch looks every 30). The original file
   is only read.
4. When the job finishes, with a relay connected, Muse or WhatsApp set
   up and **Let me approve publishing from my phone** on, the row says
   *"Ready to approve from your phone."* and the offer appears in Settings.
   Without them, the row says what to switch on.

For Google Drive for desktop, use **Mirror files**, or make the folder
available offline: a streamed (online-only) file isn't on the PC until
it's opened.

## The full app with live Meta services

This needs your own Meta app, WhatsApp business number, Cloudflare account,
and Instagram/Facebook/Threads accounts with a role on the app. These steps
have not been run against live Meta, WhatsApp or Muse services yet: every
run so far had Meta's servers faked.

1. Run Clips Kitty from source in this repository (Windows), as in the
   README's [Quick start](../../../README.md#quick-start).
2. Deploy the relay and connect WhatsApp: [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md).
3. Add Facebook Login for Business and the Threads API, and connect the
   accounts: [META_PUBLISHING.md](META_PUBLISHING.md).
4. In Settings → Notifications & Remote Approval: set the PIN, switch on
   **Let me approve publishing from my phone**, pick preferred destinations.
5. Optional: Settings → AI, add a Meta Model API key, and under
   **Transcription** choose Meta's `muse-voice-transcribe-1.0`. Then turn on
   **Write captions with Muse Spark** in section 5 of Settings →
   Notifications & Remote Approval. The same key runs **Ask Clips Kitty**,
   the box at the bottom of the app, on Muse Spark. For Muse Spark to pick
   the plays and write the titles too, choose Meta (Muse Spark) under **Clip
   picking, titles and the assistant**.
6. Optional: connect your Muse agent ([MUSE_CONNECTOR.md](MUSE_CONNECTOR.md)).
   With Muse connected, a WhatsApp number is optional.
7. Process a short basketball clip with the Basketball sport selected, or
   let a watched channel or folder set to **Ask me on my phone** do it, with
   **Watch channels** switched on.

## What to look for

- The WhatsApp counts match the clips in the app, and so does what Muse lists.
- The approval page offers only connected platforms (the others are greyed
  out) and refuses without the PIN and the confirm box.
- A plain "yes" in WhatsApp gets guidance, not an approval. Muse has no
  tool that approves.
- Only confirmed posts are reported as published, with the API's links.
- Settings shows the offer, its state, its links, and the estimated cost.
- The box at the bottom says **Muse Spark** in its corner once a Meta key is saved.

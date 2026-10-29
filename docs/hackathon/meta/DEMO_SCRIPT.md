# Demo script

Two versions: a mock run anyone can reproduce in two minutes with no
accounts, and the live demo video for the submission (about 3 minutes).

## A. The mock run (no accounts, no keys)

```
git clone https://github.com/ColinGPT9/Clips-Kitty-Meta-Basketball
cd Clips-Kitty-Meta-Basketball
pip install pyyaml requests            # the engine's light dependencies for this path
python scripts/meta_demo.py            # needs Node 22.5+ and ffmpeg on PATH
```

It runs the real relay code (`meta-relay/worker.js`) under Node with Meta's
servers faked, and the real engine code against a temporary database. What
it prints, step by step:

1. Settings: connect to the relay, pair a WhatsApp number (the phone sends
   `LINK CODE`, the PC confirms "ending in 4567"), set the PIN, connect
   Facebook + Instagram and Threads, switch on remote approval.
2. A basketball video finishes with five rendered highlights.
3. The WhatsApp message: `ck_highlights_ready` with *"Raptors vs Celtics, 4th
   quarter"* and *"5 clips: 2 dunks, 1 three, 1 block and 1 steal"*.
4. The phone approves three clips to Instagram, Facebook and Threads with
   the PIN; the page says it is waiting for the PC.
5. The PC picks the approval up, checks it, and queues three uploads.
6. The publish worker uploads the rendered files: nine posts, each with the
   link the (fake) API returned.
7. The offer settles as `PUBLISHED` and WhatsApp gets *"9 of 9 posts published"*.
8. Usage estimates and the full audit trail.

`--keep-open` leaves the relay running so you can open the approval page in
a browser (phone-sized window recommended).

## B. The live demo video (about 3 minutes)

Before recording: relay deployed, templates approved, number paired, PIN
set, Facebook + Instagram and Threads connected, remote approval on (see
[JUDGE_SETUP.md](JUDGE_SETUP.md)). Use a short basketball clip (1–3
minutes) so processing finishes on camera; never a full game. Use a test
Instagram/Facebook/Threads account you're happy to post to.

| Time | Show | Say |
|---|---|---|
| 0:00 | Clips Kitty with a basketball video queued | "Clips Kitty finds basketball highlights on my own PC: dunks, threes, blocks. This fork adds Meta: WhatsApp tells me when they're ready, and I approve publishing from my phone." |
| 0:15 | Settings → Notifications & Remote Approval | "My PC talks out to a small relay I own. Meta's tokens live there, never on this PC, and nothing on the PC is exposed. My number is paired and confirmed here, and approving needs a PIN." |
| 0:35 | Start the job; cut to it finishing | "The vision pipeline is unchanged: E-BARD, YOLO and OpenCV, all local." |
| 0:50 | Phone: the WhatsApp message arrives | "The counts are the pipeline's real results, not generated text." |
| 1:05 | Phone: tap Review clips; scroll the page | "Thumbnails of the rendered clips, Muse Spark's captions for each platform, only my connected accounts." |
| 1:25 | Untick a clip, tick Threads, confirm, enter PIN, Approve | "Nothing is posted until I choose clips and destinations and enter my PIN. A plain 'yes' in WhatsApp does nothing." |
| 1:40 | PC: the offer goes to Publishing | "The PC checks the approval against the exact files it offered, then uploads the files it already rendered. No re-render." |
| 2:00 | Phone: "3 of 3 posts published"; open the status page; tap a link | "Only what Meta's APIs confirmed, with the links they returned. If one fails, it says so, and I can retry just that one." |
| 2:25 | Instagram Reel and Threads post on the phone | "Instagram Reels, Facebook Page Reels, and a Threads post linking to the Reel." |
| 2:40 | Settings: usage line, audit; Settings → AI → Muse Voice | "Muse Spark writes the captions in one cached request; Muse Voice can transcribe the commentary, with Whisper as the fallback. Costs are tracked." |
| 2:55 | Close | "Local-first highlights, Meta for the last mile." |

### Backup shots (if something live misbehaves)

- The mock run above, recorded in a terminal.
- The approval page screenshots in [img/](img/): the offer, filled in,
  waiting for the PC, and the results.
- Partial failure: disconnect Threads after approving. Threads fails with
  a "reconnect" message, WhatsApp says "2 of 3 posts published (1 failed)",
  and after reconnecting, **Retry failed** posts only the Threads one.

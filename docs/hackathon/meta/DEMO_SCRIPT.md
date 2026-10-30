# Demo script

Two versions: a mock run anyone can reproduce in two minutes with no
accounts, and the live demo video for the submission (under 3 minutes).

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
a browser (phone-sized window recommended). The mock run doesn't play Muse;
[JUDGE_SETUP.md](JUDGE_SETUP.md) shows how to call the Muse connector on the
same mock relay, and `tests/test_remote_approval_e2e.py` plays Muse end to end.

## B. The live demo video (2:50)

**The story:** Clips Kitty is built for a basketball team's social media
manager who is away from the office. The team posts a new game video to
YouTube, or drops one in a shared Google Drive folder. Clips Kitty, on the
office PC, picks it up by itself and clips the highlights. The manager deals
with it from their phone through their **Muse agent**, and the clips go out
through Instagram's, Facebook's and Threads' own APIs. Muse is the only
messaging app on screen; WhatsApp is mentioned, not shown.

The **Needs live** column says which shots need real services. A shot whose
service isn't live by recording day is cut, never faked.

| Time | Section | Show | Say | Needs live |
|---|---|---|---|---|
| 0:00 | Why I built it | A finished basketball clip in 9:16, the crop following the ball (public-domain game only) | "I'm a Toronto Raptors fan, and the best highlight pages always get the plays up first. So I built Clips Kitty for the person behind a team's Instagram: the social media manager." | No |
| 0:15 | Away from the office | The Watch page: the team's YouTube channel and a shared Drive folder, both on **Ask me on my phone** with Sports → Basketball. A game video lands in the folder, the watch picks it up, the job runs, the row says *"Ready to approve from your phone."* | "When the team posts a new game video, or drops one in our shared Drive folder, Clips Kitty picks it up on its own. On the office PC it finds the dunks, threes and blocks, and cuts each one for Reels with the crop following the ball." | YouTube watch: a real upload. Folder: no |
| 0:40 | Ask your Muse agent | Phone, in the Muse app: "What did Clips Kitty find from tonight's game?", the counts and clips, then "Send me the approval link" | "I'm not at my desk, so I ask my Muse agent. It's connected to Clips Kitty, so it can tell me what's ready and send me the link to approve. It can't post anything by itself." | Relay deployed, connector added in Muse |
| 1:15 | Approve and post | Phone: the approval page, thumbnails, Muse Spark captions for each platform. Untick one clip, choose Instagram, Facebook and Threads, tick confirm, enter the PIN (masked), Approve. Then the Reel on Instagram | "I choose the clips and where they go, confirm, and enter my PIN. Back at the office, the PC checks the approval against the exact files and posts them as Instagram Reels, Facebook Reels and a Threads post." | Meta app with the accounts connected; Meta Model API key for the captions |
| 1:35 | What got posted | Muse: "What got posted?", with the links | "Muse only reports what Meta confirmed, with the real links." | As above |
| 1:50 | Inside the app | Settings → **2. Your Muse agent**: making a key (never show the key), the connector address, Revoke. Then **Ask Clips Kitty** at the bottom, with **Muse Spark** in its corner, doing something with the app's tools | "Connecting Muse takes one key from Settings. And inside the app, the assistant at the bottom runs on Muse Spark, so I can ask it to do things right in Clips Kitty too." | Meta Model API key for the box |
| 2:15 | Gaming and Just Chatting too | Quick cuts: a GTA clip from a stream's VOD, then a Just Chatting clip | "It's made for basketball, but it works for gaming and Just Chatting videos too, clipped the same way once the stream ends." | No (real runs on the PC) |
| 2:30 | How it's built | A diagram: the PC runs the vision locally; a relay on Cloudflare is the only thing that talks to Meta | "The heavy work stays on the team's own PC. A small relay I own is the only thing that talks to Meta. It works with your Muse agent and WhatsApp, and it posts to Instagram, Facebook and Threads." | No |
| 2:45 | Close | Logo and repo link | "Clips Kitty: the best plays first, from anywhere." | No |

The vision on the PC is the existing pipeline, unchanged: the
[E-BARD](https://github.com/GabrieleGiudic/E-BARD) detector (used unmodified
under CC BY 4.0), YOLO, OpenCV and local Whisper.

### Before recording

1. Relay deployed ([WHATSAPP_SETUP.md](WHATSAPP_SETUP.md) step 2, schema
   included) and connected in **Settings → Notifications & Remote Approval →
   1. Your relay**.
2. **2. Your Muse agent**: make a key and add the connector in Muse with the
   sentence Settings gives ([MUSE_CONNECTOR.md](MUSE_CONNECTOR.md)). The key
   goes in Muse's credential prompt, never in the chat.
3. **4. Approval PIN and accounts**: a PIN, Facebook + Instagram and Threads
   ([META_PUBLISHING.md](META_PUBLISHING.md)). Use accounts you're happy to post to.
4. **5. Approve from your phone**: tick **Let me approve publishing from my
   phone**, and **Offer gaming and other videos too** for the gaming section.
5. **Settings → AI**: the Meta Model API key (captions and the box at the bottom).
6. Watches on **Ask me on my phone** with Sports → Basketball. A watch skips
   videos shorter than 3 minutes until **Skip videos shorter than** is
   lowered. For Google Drive for desktop, use **Mirror files** or make the
   folder available offline.
7. The GTA plugin from the Marketplace, for the gaming section.
8. A full dry run off camera. Never show a key, the PIN digits, NBA footage
   or a team's logo.

Not built, so not said: Messenger or Instagram DMs. Muse can't receive a
message from an outside app (only calendars can), so "Muse tells me when
highlights are ready" relies on asking Muse to check on a schedule. If that
doesn't work on the day, show only the asking.

### Backup shots (if something live misbehaves)

- The mock run above, recorded in a terminal.
- The approval page screenshots in [img/](img/): the offer, filled in,
  waiting for the PC, and the results.
- Partial failure: disconnect Threads after approving. Threads fails with
  a "reconnect" message, the results say "2 of 3 posts published (1 failed)",
  and after reconnecting, **Retry failed** posts only the Threads one.

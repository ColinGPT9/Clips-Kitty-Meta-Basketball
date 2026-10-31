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

**The story:** a social media manager's game day, told as an example (the
manager is an example, not the developer's job).
- **The job:** say you're the social media manager for an NBA team, running
  its Instagram, Facebook and Threads. Sports highlights are huge on
  Instagram, and while a game is on, it's trending, so the clips need to go up
  during the game or right after, while people are still talking about it.
- **Before tip-off:** Clips Kitty on the office PC watches the team's shared
  Google Drive folder.
- **During the game:**
  - The video team drops each quarter into the folder as it ends, and Clips
    Kitty clips it by itself.
  - The manager, at the game, asks their **Muse agent** what's ready and
    approves with a PIN.
  - The clips go out through Instagram's, Facebook's and Threads' own APIs.
- **On screen:** Muse is the only messaging app; WhatsApp is mentioned, not
  shown. Gaming and Just Chatting get one slide.

The **Needs live** column says which shots need real services. None of them
has been tried live yet: the tests and the mock run fake Meta and Muse, and
whether Muse's custom connectors take an MCP server with a header key is not
confirmed ([MUSE_CONNECTOR.md](MUSE_CONNECTOR.md)). A shot whose service
isn't live by recording day is cut, never faked.

| Time | Section | Show | Say | Needs live |
|---|---|---|---|---|
| 0:00 | Why I built it | Finished 9:16 clips, the crop following the ball (public-domain game only) | "I'm a Toronto Raptors fan, inspired by the big Instagram highlight accounts. Sports highlights are huge on Instagram, and while a game is on, it's trending on social media and at the top of Google search." | No |
| 0:15 | What was missing | A wide frame of the public-domain game beside its 9:16 clip | "There's a lot of open-source basketball computer vision, but I couldn't find an open-source tool that turns an NBA broadcast into vertical clips that follow the ball. So I built one." | No |
| 0:27 | The job | More finished clips | "Say you're the social media manager for an NBA team, running its Instagram, Facebook and Threads. Your clips need to go up during the game or right after, while people are still talking about it." | No |
| 0:42 | Before tip-off | The Watch page: **Watch channels** on, the Drive folder watch on **Ask me on my phone** with Sports → Basketball. Then Settings → **2. Your Muse agent**, connected. Press nothing there: a new key or Revoke stops the key Muse has | "Before tip-off, you point Clips Kitty at the team's shared Drive folder on the office PC, and connect your Muse agent with one key." | Relay deployed, for the Muse section |
| 0:57 | During the game | A quarter-length file lands in the folder, the watch picks it up, the job runs, and the row says *"Ready to approve from your phone."* | "During the game, the video team drops each quarter into that folder as it ends. Clips Kitty picks it up by itself, finds the dunks, threes and blocks, and cuts each one for Reels." | No |
| 1:17 | Ask your Muse agent | Phone, in the Muse app: "What highlights are ready?", the counts and clips, then "Send me the approval link" | "You're at the game, not at your desk, so you ask your Muse agent. It tells you what's ready and sends you the link to approve. It can't post anything by itself." | Relay deployed, connector added in Muse |
| 1:42 | Approve and post | Phone: the approval page, thumbnails, and a clip's **What will be posted** opened to show its Muse Spark captions. Choose Instagram, Facebook and Threads, tick confirm, enter the PIN (masked), **Approve and publish**. Then the Reel on Instagram | "You pick the clips and where they go, and enter your PIN. The office PC checks the approval against the exact files and posts them through Meta's own APIs: Instagram Reels, Facebook Reels and Threads." | Meta app with the accounts connected; Meta Model API key for the captions |
| 2:02 | What got posted | Muse: "What got posted?", with the links | "Muse only reports what Meta confirmed, with the real links." | As above |
| 2:12 | After the game | **Ask Clips Kitty** at the bottom, with **Muse Spark** in its corner, doing something with the app's tools | "Back at the office, the assistant inside Clips Kitty runs on Muse Spark too, so you can just ask it to do things in the app." | Meta Model API key for the box |
| 2:22 | Gaming and Just Chatting | One slide, text only: "Also supports gaming and Just Chatting content" | "And it clips gaming and Just Chatting videos too." | No |
| 2:27 | How it's built | A diagram: the PC runs the vision locally and calls Muse Spark with its own Meta Model API key; a relay on Cloudflare holds the account tokens and is the only thing that talks to WhatsApp, Instagram, Facebook and Threads, and the place Muse connects | "The heavy work stays on the team's own PC, which calls Meta only for Muse Spark. A small relay you own does the rest: your Muse agent, WhatsApp, and posting to Instagram, Facebook and Threads." | No |
| 2:42 | Close | Logo, repo link and the disclaimer | "Clips Kitty: the best plays first, from anywhere." | No |

The vision on the PC is the existing pipeline, unchanged: the
[E-BARD](https://github.com/GabrieleGiudic/E-BARD) detector (used unmodified
under CC BY 4.0), YOLO, OpenCV and local Whisper.

The open-source line says what the developer could find in a related-work
search (October 2026). That search turned up:
- plenty of open basketball computer vision;
- a few shot-detection highlight tools, mostly for a fixed camera;
- nothing that turns a broadcast into 9:16 clips that follow the ball.

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
   phone** and **Write captions with Muse Spark**.
5. **Settings → AI**: the Meta Model API key (the box at the bottom, and the
   captions once **Write captions with Muse Spark** is ticked).
6. **The Drive folder watch:**
   - Switch **Watch channels** on.
   - Watch the shared Drive folder on **Ask me on my phone** with Sports →
     Basketball. For Google Drive for desktop, use **Mirror files** or make the
     folder available offline.
   - A watch skips videos shorter than 3 minutes until **Skip videos shorter
     than (minutes)** is lowered in its settings.
   - Ask Muse "What highlights are ready?". When Muse narrows the list, every
     word it searches for must be in the video's title (a folder video is
     titled by its file name) or in its clips.
7. A full dry run off camera, timing one quarter-length file from the drop to
   *"Ready to approve from your phone."*. Say "during the game" only if it's
   ready well before a game would end; otherwise say "right after the game".
   Never show a key, the PIN digits, NBA footage or a team's logo.

Not built, so not said: Messenger or Instagram DMs. The relay can't message
Muse: its connector only answers when Muse calls it. So the video shows Muse
being asked, never announcing anything.

### Backup shots (if something live misbehaves)

- The mock run above, recorded in a terminal and labelled on screen as the
  mock run, with Meta faked.
- The approval page screenshots in [img/](img/), from the mock run and
  labelled the same way: the offer, filled in, waiting for the PC, and the
  results. They were taken before the PIN box gained "Enter your PIN
  yourself. Never give it to an assistant."
- Partial failure: approve one clip to all three places, wait until the page
  says "Your PC accepted it", then **Disconnect** Threads in Settings (before
  then, the PC refuses the whole approval). Threads fails with "Threads is not
  connected on the relay.", the page says "2 published, 1 failed.",
  **Recent offers** in Settings says "2 of 3 posts published (1 failed)", and
  after reconnecting, **Retry failed** posts only the Threads one.

# Submission: Clips Kitty - Meta AI Basketball Highlights & Remote Publishing

**Tagline:** AI basketball highlights with E-BARD tracking, Meta Muse models,
WhatsApp notifications, and remote Instagram/Facebook/Threads publishing.

**Track:** Indie

**Repository:** https://github.com/ColinGPT9/Clips-Kitty-Meta-Basketball
(public; AGPL-3.0, like the original)

**Built on:** [Clips Kitty](https://github.com/ColinGPT9/clips-studio) at
commit `6245e02332ba07e0c9b12d47a95ee81502f4122d`. What is new is listed in
[NEW_WORK.md](NEW_WORK.md).

**Demo video:** _to be added after the live recording ([DEMO_SCRIPT.md](DEMO_SCRIPT.md))._

## The problem

Sports highlights are huge on Instagram, and while a game is on, it's
trending on social media and at the top of Google search. The big highlight
accounts win by being first. Say you're the social media manager for an NBA
team, running its Instagram, Facebook and Threads: the clips need to go up
during the game or right after, while people are still talking about it.

There's a lot of open-source basketball computer vision, but a search for
this project found no open-source tool that turns an NBA broadcast into
vertical clips that follow the ball. Clips Kitty does, and finds the dunks,
threes, blocks and steals on the team's own PC. But then the clips wait there
until someone at the desk posts them, and the manager is at the game.

## What it does

When the PC finishes a game, the creator gets a WhatsApp message with the
real counts ("5 clips: 2 dunks, 1 three, 1 block and 1 steal"). One tap
opens a page with a still from each rendered clip and Muse Spark's captions for each
platform. They pick the clips and where they go, confirm, enter their PIN,
and the PC publishes the files it already rendered as Instagram Reels,
Facebook Page Reels and a Threads post. WhatsApp then says what Meta's APIs
confirmed, "3 of 3 posts published", and its View status button opens the confirmed links.

The person running a team's socials doesn't have to start anything either.
Clips Kitty can watch the team's shared Google Drive folder (with Google
Drive for desktop set to Mirror files), clip each quarter or game dropped in
it by itself, and send the clips to the phone for approval. From anywhere, they
can ask their **Muse agent** what's ready, get the approval link, and later
ask what got posted. Muse never posts: the PIN page is the only way to approve.

None of this has been tried against Meta's live services yet. Every run so
far fakes Meta's servers: no WhatsApp message was sent, nothing was
posted, and neither Muse Spark nor the Muse app was used. See
[TEST_LOG.md](TEST_LOG.md).

The heavy work stays local and private: the video, the vision models (YOLO, OpenCV,
scoreboard reading, and [E-BARD](https://github.com/GabrieleGiudic/E-BARD) tracking, used unmodified under CC BY 4.0) and rendering all run on the
creator's PC, which never opens a port. A tiny relay the creator owns (one
Cloudflare Worker) is the only thing Meta talks to.

## How Meta's tools are used

| Meta tool | Use |
|---|---|
| **WhatsApp Business Cloud API** | Utility templates for highlights ready / processing failed / publishing update; quick replies (Remind me later, Cancel); webhook with signature checks; pairing a number with a one-time code; STOP/START |
| **Instagram Graph API** | Reels via resumable upload, container status, publish, permalink |
| **Facebook Pages API (Video Reels)** | Page Reels: start, upload, finish, status, permalink |
| **Threads API** | A post linking to the Reel, with Threads Login and token refresh |
| **Facebook Login for Business** | Connecting the Page and its Instagram account; tokens kept on the relay only |
| **Muse Spark** (Meta Model API) | One strict-JSON request per video writes every clip's title, Instagram caption, Facebook description, Threads post and hashtags, plus an optional summary line; cached; cost tracked from its usage block |
| **Muse Voice Transcribe** | An option on the existing transcription path for commentary; turn-level timings kept honest (no invented word times); Whisper as fallback |
| **Muse** (Meta's personal agent app) | A custom connector on the creator's relay: a remote MCP server with six tools, plus the same tools as REST routes with an OpenAPI description. Muse lists the highlights waiting, hands over the approval link, says what got posted, and reminds or cancels; no tool approves or posts. Works without WhatsApp. Built and tested against the real relay code; **not yet tried in the Muse app** ([MUSE_CONNECTOR.md](MUSE_CONNECTOR.md)) |
| **Muse Spark** (the in-app assistant) | **Ask Clips Kitty**, the box at the bottom of the app, runs on Muse Spark with the app's own tools once a Meta Model API key is saved. Built and tested with Meta's API faked; one live request still to make |

## Technical depth

- A persistent approval state machine on the PC (13 states, every move
  checked and audited, survives restarts) integrated with the existing job
  and publish queues rather than beside them.
- Defence in depth for "never publish without approval": expiring
  single-purpose links, PIN with lockout, CSRF and Origin checks, signed
  PC↔relay requests with nonces, relay-signed commands, fingerprinted clip
  files, job- and platform-specific checks on both sides, duplicate and
  replay protection, and a relay that can only act inside an accepted approval.
- Honest results: a post is "published" only with an API-returned id, links
  are only API-returned permalinks, partial failures are reported and
  retried without double-posting, and an upload cut off by a restart is
  "check the account", never assumed.
- Automation to the phone: a watch set to **Ask me on my phone** turns a
  file dropped in a watched folder (a shared Google Drive folder, say) into
  an approval offer. A folder file counts only once it
  has stopped changing and nothing is still writing it, gets the same id as
  a file added by hand (never clipped twice), and is only read.
- A Muse connector speaking both MCP eras (the `initialize` handshake and
  the stateless 2026-07-28 revision) with per-PC keys stored only as hashes,
  a rate limit, and a PIN that blocks phone approval after repeated lockouts.
- Tests: 58 relay tests under Node, and about 200 Python tests for this
  work, including end-to-end runs of the real relay code against the real
  engine, with only Meta's servers faked, for both WhatsApp and Muse.

## Inspiration and disclaimer

Inspired by the Toronto Raptors and by basketball highlight accounts. This
project is **not affiliated with, endorsed by, or sponsored by Meta, the NBA,
or the Toronto Raptors**.
Team and player names in examples are illustrative.

## Team

[@ColinGPT9](https://github.com/ColinGPT9). _(Add any teammates here; the Indie track allows up to 4.)_

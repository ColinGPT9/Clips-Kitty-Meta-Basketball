# Submission: Clips Kitty — Meta AI Basketball Highlights & Remote Publishing

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

A small basketball creator records a game, and Clips Kitty finds the
highlights on their own PC overnight: dunks, threes, blocks, steals. Then
the clips sit there until the creator is back at the desk to post them,
and the moment passes. House of Highlights-style accounts win by being
first; a one-person channel can't watch a render bar.

## What it does

When the PC finishes a game, the creator gets a WhatsApp message with the
real counts ("5 clips: 2 dunks, 1 three, 1 block and 1 steal"). One tap
opens a page with the rendered clips and Muse Spark's captions for each
platform. They pick the clips and where they go, confirm, enter their PIN,
and the PC publishes the files it already rendered as Instagram Reels,
Facebook Page Reels and a Threads post. WhatsApp then says what Meta's APIs
confirmed, "3 of 3 posts published", with the links.

The heavy work stays local and private: the video, the vision models (E-BARD
tracking, YOLO, OpenCV, scoreboard reading) and rendering all run on the
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
- Tests: 29 relay tests under Node, and over 100 Python tests including an
  end-to-end run of the real relay code against the real engine with only
  Meta's servers faked.

## Inspiration and disclaimer

Inspired by the Toronto Raptors and by highlight accounts like House of
Highlights. This project is **not affiliated with, endorsed by, or
sponsored by Meta, House of Highlights, the NBA, or the Toronto Raptors**.
Team and player names in examples are illustrative.

## Team

[@ColinGPT9](https://github.com/ColinGPT9). _(Add any teammates here; the Indie track allows up to 4.)_

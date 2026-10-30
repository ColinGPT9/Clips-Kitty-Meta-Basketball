"""Remote approval: a finished video's clips, offered on WhatsApp, approved
on the creator's phone, published from this PC. Basketball videos only,
unless the creator opts in to gaming and other videos in Settings.

New for the Meta hackathon edition. The pieces, in the order a clip moves
through them:

* notifier.py   - the pipeline worker's terminal announce calls in here. It
                  snapshots the clips this run made, counts the highlights
                  and queues a notification. A database write only: no
                  network on the pipeline thread.
* service.py    - runs on the publish worker's loop. Sends what the outbox
                  holds to the relay, asks the relay for decisions made on the
                  phone, and reports results back.
* commands.py   - checks every decision against the offer it names before
                  anything is queued (job, clips, fingerprint, expiry,
                  platforms), and queues the publishes it allows.
* publishing.py - one approved clip, uploaded to Instagram, Facebook and
                  Threads through publish/meta_graph.py, recorded in the same
                  clip_publishes table every other provider uses.
* states.py     - the states an offer moves through and the moves allowed.
* relay.py      - the signed HTTP client for the relay (meta-relay/).

Everything here is stdlib-only, like publish/base.py, so CI's main job (no
FastAPI, no Google libraries) runs every test of it.
"""

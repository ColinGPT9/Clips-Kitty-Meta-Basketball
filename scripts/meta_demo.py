"""The Meta hackathon flow, end to end on this machine, with Meta faked.

    python scripts/meta_demo.py                 # narrated run, then exits
    python scripts/meta_demo.py --keep-open     # leave the relay up to click around the phone page

A finished basketball video -> a WhatsApp message with the real highlight
counts -> the phone opens the approval page, picks clips and platforms and
enters the PIN -> this PC picks the approval up -> the publish worker uploads
the rendered files to Instagram, Facebook and Threads -> the confirmed
results go back to WhatsApp.

Everything on the PC side is the real engine code (remote_approval/, the
publish worker, publish/meta_graph.py) against a real state database in a
temporary folder. The relay is the real meta-relay/worker.js under Node
(meta-relay/test/serve.mjs); only Meta's servers are stand-ins, so nothing
is sent to WhatsApp or posted anywhere, and no key or account is needed.
No vision model runs: the clips are short test videos made with ffmpeg,
standing in for the basketball pipeline's rendered highlights.

Needs Node 22.5+ (for the relay's node:sqlite) and ffmpeg.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from core.state import StateDB  # noqa: E402

ENROLL_KEY = "demo-enrolment-key-0123456789abcdef"
PIN = "4826"
PLAYS = [
    # (sport_event, label, score, title)
    ("poster_dunk", "Poster dunk", 94, "Barnes posterizes on the break"),
    ("corner_three", "Corner three", 88, "Corner three beats the shot clock"),
    ("alley_oop", "Alley-oop", 86, "Lob to the rim off the inbound"),
    ("block", "Block", 79, "Chase-down block in transition"),
    ("steal", "Steal", 72, "Pick-six steal at half court"),
]


def say(text: str = "") -> None:
    print(text, flush=True)


def step(n: int, text: str) -> None:
    say(f"\n[{n}] {text}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_relay(port: int) -> subprocess.Popen:
    node = shutil.which("node")
    if not node:
        sys.exit("Node 22.5+ is needed to run the relay.")
    env = {**os.environ, "PORT": str(port), "ENROLL_KEY": ENROLL_KEY}
    # --experimental-sqlite: node:sqlite needs it before Node 22.13; later versions accept it.
    proc = subprocess.Popen([node, "--no-warnings", "--experimental-sqlite", "test/serve.mjs"],
                            cwd=ROOT / "meta-relay", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if requests.get(url + "/health", timeout=1).ok:
                return proc
        except requests.ConnectionError:
            time.sleep(0.1)
    proc.terminate()
    sys.exit("The relay did not start.")


def make_clip(path: Path, seconds: float, hue: int) -> None:
    """A short 9:16 test video, standing in for a rendered highlight."""
    from core.binaries import ffmpeg

    subprocess.run(
        [ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=360x640:rate=30:duration={seconds}",
         "-vf", f"hue=h={hue}", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", str(path)],
        check=True, capture_output=True, timeout=60,
    )


def finished_game(db: StateDB, folder: Path) -> dict:
    """A basketball job that finished, with one rendered clip per play."""
    title = "Raptors vs Celtics, 4th quarter"
    db.upsert_video("vid_demo_raptors", title=title)
    payload = {"url": "https://example.com/raptors-celtics.mp4", "sport": {"name": "basketball", "highlights": "best"}}
    job_id = db.add_job("process", json.dumps(payload), video_id="vid_demo_raptors", title=title)
    folder.mkdir(parents=True, exist_ok=True)
    for i, (event, label, score, clip_title) in enumerate(PLAYS):
        path = folder / f"clip{i + 1}.mp4"
        make_clip(path, 12.0, i * 60)
        db.add_clip("vid_demo_raptors", 30.0 * i, 30.0 * i + 12, score, f"{label}!", path=str(path),
                    title=clip_title, hashtags=json.dumps(["#nba", "#raptors", "#wethenorth"]),
                    scores=json.dumps({"sport_event": event, "sport_label": label, "sport_t": 30.0 * i + 6}),
                    render_opts=json.dumps({"sport": "basketball"}))
    db.finish_job(job_id, "done")
    return db.get_job(job_id)


class Phone:
    def __init__(self, url: str):
        self.url = url

    def state(self) -> dict:
        return requests.get(self.url + "/__mock/state", timeout=10).json()

    def send(self, text: str) -> None:
        requests.post(self.url + "/__mock/phone", json={"text": text}, timeout=10)

    def oauth(self, url: str) -> None:
        requests.post(self.url + "/__mock/oauth", json={"url": url}, timeout=10)

    def last(self) -> dict:
        return self.state()["messages"][-1]


def show_message(message: dict) -> None:
    """A WhatsApp message as the phone would show it."""
    if message.get("type") == "text":
        body = message["text"]["body"]
        say(f"      WhatsApp: {body}")
        return
    if message.get("type") == "interactive":
        i = message["interactive"]
        say(f"      WhatsApp: {i['body']['text']}")
        say(f"                [{i['action']['parameters']['display_text']}] -> {i['action']['parameters']['url']}")
        return
    template = message.get("template") or {}
    params = [p.get("text") for c in template.get("components") or [] if c.get("type") == "body"
              for p in c.get("parameters") or []]
    say(f"      WhatsApp template {template.get('name')}: {params}")


def link_token(message: dict) -> str:
    if message.get("type") == "template":
        button = next(c for c in message["template"]["components"] if c.get("sub_type") == "url")
        return button["parameters"][0]["text"]
    return re.search(r"/a/([A-Za-z0-9_-]{20,})", json.dumps(message))[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep-open", action="store_true", help="leave the relay running at the end")
    args = parser.parse_args()

    from publish.meta_graph import MetaGraph
    from remote_approval import notifier, publishing, service, store, usage
    from remote_approval import settings as ra_settings
    from remote_approval.relay import Relay
    from server import publisher

    port = free_port()
    relay_url = f"http://127.0.0.1:{port}"
    relay_proc = start_relay(port)
    phone = Phone(relay_url)
    data_dir = Path(tempfile.mkdtemp(prefix="ck-meta-demo-"))
    db = StateDB(data_dir / "state.db")
    try:
        say("Clips Kitty: Meta AI basketball highlights with remote WhatsApp approval (mock Meta)")
        say(f"  relay  {relay_url}   (real worker.js, Meta faked)")
        say(f"  data   {data_dir}")

        step(1, "Settings: connect this PC to the relay, pair a WhatsApp number, set a PIN, connect accounts")
        ra_settings.save(db, {"whatsapp_enabled": True, "relay_url": relay_url})
        service.register(db, data_dir, ENROLL_KEY)
        relay = service.client(db, data_dir)
        code = relay.pair_start()
        say(f"      PC shows: send \"{code['text']}\" from your phone")
        phone.send(code["text"])
        show_message(phone.last())
        relay.pair_confirm(True)
        say("      PC: confirmed the number ending in " + service.refresh_status(db, relay)["pairing"]["tail"])
        relay.set_pin(PIN)
        for platform in ("facebook", "threads"):
            phone.oauth(relay.accounts_connect(platform)["url"])
        accounts = service.refresh_status(db, relay)["accounts"]
        say("      Connected: " + ", ".join(f"{k} ({v.get('name')})" for k, v in accounts.items() if v.get("connected")))
        ra_settings.save(db, {"remote_approvals": True, "preferred_destinations": ["instagram", "facebook"]})
        service.push_preferences(db, relay)

        step(2, "The basketball pipeline finishes a video (5 rendered highlights)")
        job = finished_game(db, data_dir / "clips")
        review_id = notifier.on_job_finished(db, job, "done")
        review = store.get_review(db, review_id)
        say(f"      Offer #{review_id}: {review['summary']['counts_text']} -> {review['state']}")

        step(3, "The PC's publish worker talks to the relay (outbound only) and the message goes out")
        state = service.TickState()
        clock = [0.0]

        def tick() -> int:
            clock[0] += 1000
            return service.tick(db, data_dir, state, clock=lambda: clock[0])

        tick()
        message = phone.last()
        show_message(message)
        review = store.get_review(db, review_id)
        say(f"      Offer state: {review['state']}  (approval {review['approval_id']})")

        step(4, "On the phone: open the link, pick 3 clips and 3 platforms, tick confirm, enter the PIN")
        token = link_token(message)
        page = requests.get(f"{relay_url}/a/{token}", timeout=10).text
        csrf = re.search(r'name="csrf" value="([0-9a-f]{64})"', page)[1]
        chosen = [c["clip_id"] for c in review["clips"][:3]]
        fields = [("csrf", csrf), ("action", "approve"), *[("clip", str(c)) for c in chosen],
                  ("platform", "instagram"), ("platform", "facebook"), ("platform", "threads"),
                  ("confirm", "yes"), ("pin", PIN)]
        answer = requests.post(f"{relay_url}/a/{token}", data=fields, headers={"Origin": relay_url}, timeout=10)
        said = re.sub(r"<[^>]+>", " ", answer.text)
        say("      Page says: " + re.sub(r"\s+", " ", re.search(r"Approved\.[^<]{0,160}", answer.text)[0]
                                        if "Approved." in answer.text else said[:160]).strip())
        say(f"      Phone page: {relay_url}/a/{token}")

        step(5, "The PC picks the approval up, checks it against the offer, and queues the uploads")
        queued = tick()
        say(f"      {queued} publish job(s) queued -> {store.get_review(db, review_id)['state']}")

        step(6, "The existing publish worker uploads the rendered files (no re-render)")
        publishing.make_graph = lambda config, device: MetaGraph(Relay(config["relay_url"], **device),
                                                                 sleep=lambda s: None)
        worker = publisher.PublishWorker({}, data_dir / "state.db", data_dir)
        while (pj := db.claim_next_publish_job()) is not None:
            try:
                worker._publish(db, pj)
            except Exception as e:
                worker._fail(db, pj, e)
        for post in phone.state()["posts"]:
            say(f"      {post['platform']:<9} {post['permalink']}")

        step(7, "Results settle from what the APIs confirmed, and go back to WhatsApp")
        tick()
        review = store.get_review(db, review_id)
        say(f"      Offer state: {review['state']} - {service.result_text(review['results'])}")
        show_message(phone.last())

        step(8, "Usage (estimates) and the audit trail")
        u = usage.summary(db)
        say(f"      WhatsApp messages: {u['whatsapp']['templates']} template, {u['whatsapp']['free_form']} free-form;"
            f" Graph uploads: {u['meta_graph']['uploads']}; estimated ${u['estimated_usd']:.4f}")
        for row in reversed(store.audit_log(db, review_id, 30)):
            say(f"      {row['at'][11:19]}  {row['actor']:<9} {row['action']:<22} {row['detail'][:60]}")

        if args.keep_open:
            say(f"\nThe relay stays up. Open {relay_url}/a/{token} in a browser (it shows the result now).")
            say("Ctrl+C to stop.")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        # Ctrl+C is how --keep-open ends.
        pass
    finally:
        db.close()
        relay_proc.terminate()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()

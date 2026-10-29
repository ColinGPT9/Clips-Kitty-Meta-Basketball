"""The whole remote approval flow, PC and relay together, with Meta faked.

The relay here is the real meta-relay/worker.js, run under Node by
meta-relay/test/serve.mjs: real routes, real signatures, real D1 schema (on
node:sqlite), real approval page. Only Meta's servers are stand-ins, so
nothing is sent to WhatsApp or posted anywhere. The PC side is the real
engine code against a real state database.

    finished job -> WhatsApp offer -> phone opens the page, enters the PIN,
    picks clips and platforms -> PC picks the approval up -> the publish
    worker uploads the rendered file -> Instagram, Facebook, Threads ->
    results back to WhatsApp, with only the links the APIs returned

Skipped when Node 22.5+ (for node:sqlite) is not installed.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
import requests
from remote_approval_fake import finished_job

from remote_approval import commands, notifier, publishing, service, states, store
from remote_approval import settings as ra_settings

ROOT = Path(__file__).resolve().parent.parent
RELAY = ROOT / "meta-relay"
ENROLL_KEY = "e2e-enrolment-key-0123456789abcdef"
PIN = "4826"


def _node_ok() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return False
    m = re.match(r"v(\d+)\.(\d+)", out.strip())
    return bool(m) and (int(m[1]), int(m[2])) >= (22, 5)


pytestmark = pytest.mark.skipif(not _node_ok(), reason="needs Node 22.5+ for the relay")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def relay_url():
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "ENROLL_KEY": ENROLL_KEY}
    proc = subprocess.Popen(["node", "--no-warnings", "test/serve.mjs"], cwd=RELAY, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if requests.get(url + "/health", timeout=1).ok:
                    break
            except requests.ConnectionError:
                time.sleep(0.1)
        else:
            raise RuntimeError("the relay did not start: " + (proc.stdout.read() if proc.stdout else ""))
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


class Phone:
    """The creator's phone and browser, against the mock relay."""

    def __init__(self, url):
        self.url = url

    def state(self):
        return requests.get(self.url + "/__mock/state", timeout=10).json()

    def send(self, text):
        return requests.post(self.url + "/__mock/phone", json={"text": text}, timeout=10).json()

    def tap(self, payload, context=""):
        return requests.post(self.url + "/__mock/tap", json={"payload": payload, "context": context}, timeout=10).json()

    def oauth(self, url):
        return requests.post(self.url + "/__mock/oauth", json={"url": url}, timeout=10).json()

    def last_message(self):
        return self.state()["messages"][-1]

    def link_token(self, message):
        """The approval link's token, from a template's URL button or the
        cta_url fallback."""
        if message.get("type") == "template":
            button = next(c for c in message["template"]["components"] if c.get("sub_type") == "url")
            return button["parameters"][0]["text"]
        return re.search(r"/a/([A-Za-z0-9_-]{20,})", json.dumps(message))[1]

    def open(self, token):
        r = requests.get(f"{self.url}/a/{token}", timeout=10)
        return r.status_code, r.text

    def submit(self, token, fields):
        r = requests.post(f"{self.url}/a/{token}", data=fields, headers={"Origin": self.url}, timeout=10)
        return r.status_code, r.text


def csrf_of(html):
    return re.search(r'name="csrf" value="([0-9a-f]{64})"', html)[1]


@pytest.fixture
def pc(db, tmp_path, relay_url, monkeypatch):
    """A PC set up the way Settings does it: registered with the relay,
    WhatsApp paired and confirmed, a PIN, Facebook (with its Instagram) and
    Threads connected, remote approval on."""
    phone = Phone(relay_url)
    ra_settings.save(db, {"whatsapp_enabled": True, "relay_url": relay_url})
    # Each test gets its own PC (a new device) on the shared relay.
    service.register(db, tmp_path, ENROLL_KEY)
    relay = service.client(db, tmp_path)
    code = relay.pair_start()["code"]
    phone.send(f"LINK {code}")
    assert "confirm that the number" in phone.last_message()["text"]["body"]
    relay.pair_confirm(True)
    relay.set_pin(PIN)
    for platform in ("facebook", "threads"):
        assert phone.oauth(relay.accounts_connect(platform)["url"])["ok"]
    service.refresh_status(db, relay)
    assert service.can_enable_remote_approval(db) == ""
    ra_settings.save(db, {"remote_approvals": True, "preferred_destinations": ["instagram"]})
    service.push_preferences(db, relay)

    from publish.meta_graph import MetaGraph
    from remote_approval.relay import Relay
    from server import publisher

    # The real client through the real relay; only its waits between status
    # reads are skipped.
    monkeypatch.setattr(publishing, "make_graph",
                        lambda config, device: MetaGraph(Relay(config["relay_url"], **device), sleep=lambda s: None))
    worker = publisher.PublishWorker({}, tmp_path / "state.db", tmp_path)
    clock = [1000.0]

    def tick():
        clock[0] += service.SLOW_SECONDS + service.STATUS_SECONDS
        return service.tick(db, tmp_path, state, clock=lambda: clock[0])

    state = service.TickState()

    def publish_all():
        n = 0
        while (job := db.claim_next_publish_job()) is not None:
            try:
                worker._publish(db, job)
            except Exception as e:
                worker._fail(db, job, e)
            n += 1
        return n

    return {"phone": phone, "relay": relay, "tick": tick, "publish_all": publish_all, "dir": tmp_path}


def test_a_finished_game_goes_from_whatsapp_to_three_platforms(db, pc):
    phone = pc["phone"]
    before = len(phone.state()["posts"])
    job = finished_job(db, pc["dir"])
    review_id = notifier.on_job_finished(db, job, "done")
    pc["tick"]()

    review = store.get_review(db, review_id)
    assert review["state"] == states.NOTIFICATION_SENT
    message = phone.last_message()
    text = json.dumps(message)
    assert "Raptors vs Celtics" in text and "2 dunks, 1 three, 1 block and 1 other play" in text
    assert "colin" not in text.lower() and str(pc["dir"]) not in text  # no local paths

    # The phone opens the page: the clips, the connected accounts, a PIN box.
    token = phone.link_token(message)
    status, html = phone.open(token)
    assert status == 200
    assert "Poster dunk in the third" in html and "@hoopsdaily" in html and 'name="pin"' in html
    first, second = review["clips"][0]["clip_id"], review["clips"][1]["clip_id"]
    status, html = phone.submit(token, [
        ("csrf", csrf_of(html)), ("action", "approve"), ("clip", str(first)), ("clip", str(second)),
        ("platform", "instagram"), ("platform", "facebook"), ("platform", "threads"),
        ("confirm", "yes"), ("pin", PIN),
    ])
    assert status == 200 and "Waiting for your PC" in html

    # The PC picks it up on its next pass and queues the uploads.
    assert pc["tick"]() == 2
    assert store.get_review(db, review_id)["state"] == states.PUBLISH_QUEUED
    assert pc["publish_all"]() == 2

    posts = phone.state()["posts"][before:]
    assert sorted(p["platform"] for p in posts) == ["facebook", "facebook", "instagram", "instagram",
                                                    "threads", "threads"]
    uploads = phone.state()["uploads"]
    sizes = {c["size"] for c in review["clips"][:2]}
    assert {u["bytes"] for u in uploads[-4:]} == sizes  # the rendered files, byte for byte
    threads = [p for p in posts if p["platform"] == "threads"]
    assert all(p["link"].startswith("https://www.instagram.com/reel/") for p in threads)

    # Settled from what the APIs confirmed, and said on WhatsApp.
    pc["tick"]()
    review = store.get_review(db, review_id)
    assert review["state"] == states.PUBLISHED
    links = [p["url"] for c in review["results"]["clips"] for p in c["posts"]]
    assert sorted(links) == sorted(p["permalink"] for p in posts)
    assert "6 of 6 posts published" in json.dumps(phone.last_message())

    # The status page shows the confirmed links; the offer can't be approved again.
    status, html = phone.open(token)
    assert all(p["permalink"] in html for p in posts)
    assert 'name="pin"' not in html


def test_an_approval_made_while_the_pc_is_off_waits_for_it(db, pc):
    phone = pc["phone"]
    job = finished_job(db, pc["dir"], title="Raptors vs Knicks")
    review_id = notifier.on_job_finished(db, job, "done")
    pc["tick"]()
    token = phone.link_token(phone.last_message())
    _, html = phone.open(token)
    clip = store.get_review(db, review_id)["clips"][0]["clip_id"]
    phone.submit(token, [("csrf", csrf_of(html)), ("action", "approve"), ("clip", str(clip)),
                         ("platform", "instagram"), ("confirm", "yes"), ("pin", PIN)])
    # The PC is off: the page says so and nothing has been posted.
    _, html = phone.open(token)
    assert "Waiting for your PC" in html
    assert store.get_review(db, review_id)["state"] == states.NOTIFICATION_SENT
    # Back on: the approval is picked up and published.
    pc["tick"]()
    pc["publish_all"]()
    pc["tick"]()
    assert store.get_review(db, review_id)["state"] == states.PUBLISHED


def test_a_plain_yes_never_approves_and_buttons_only_defer_or_cancel(db, pc):
    phone = pc["phone"]
    job = finished_job(db, pc["dir"], title="Raptors vs Heat")
    review_id = notifier.on_job_finished(db, job, "done")
    pc["tick"]()
    review = store.get_review(db, review_id)
    reply = phone.send("yes")["last"]
    assert "link" in json.dumps(reply).lower()  # guidance, not an approval
    pc["tick"]()
    assert commands.jobs_for_review(db, review_id) == []
    phone.tap(f"REMIND:{review['approval_id']}")
    pc["tick"]()
    assert store.get_review(db, review_id)["state"] == states.DEFERRED
    phone.tap(f"CANCEL:{review['approval_id']}")
    pc["tick"]()
    assert store.get_review(db, review_id)["state"] == states.CANCELLED
    # The link is dead now, and a new offer is a new link.
    token = phone.link_token(next(m for m in reversed(phone.state()["messages"])
                                  if "/a/" in json.dumps(m) or m.get("type") == "template"))
    _, html = phone.open(token)
    assert 'name="pin"' not in html
    new_id = service.ask_again(db, review_id)
    pc["tick"]()
    assert store.get_review(db, new_id)["approval_id"] != review["approval_id"]


def test_a_wrong_pin_publishes_nothing(db, pc):
    phone = pc["phone"]
    job = finished_job(db, pc["dir"], title="Raptors vs Bucks")
    review_id = notifier.on_job_finished(db, job, "done")
    pc["tick"]()
    token = phone.link_token(phone.last_message())
    _, html = phone.open(token)
    clip = store.get_review(db, review_id)["clips"][0]["clip_id"]
    status, _ = phone.submit(token, [("csrf", csrf_of(html)), ("action", "approve"), ("clip", str(clip)),
                                     ("platform", "instagram"), ("confirm", "yes"), ("pin", "0000")])
    assert status in (400, 403)
    pc["tick"]()
    assert commands.jobs_for_review(db, review_id) == []


def test_a_failed_job_sends_the_plain_failure_message(db, pc):
    phone = pc["phone"]
    job = finished_job(db, pc["dir"], title="Raptors vs Nets")
    db.finish_job(job["id"], "failed", "CUDA out of memory at C:\\Users\\someone\\clips")
    notifier.on_job_finished(db, db.get_job(job["id"]), "failed")
    pc["tick"]()
    message = phone.last_message()
    wording = "Clips Kitty could not finish processing your basketball video. Open the app for details."
    if message["type"] == "template":
        # The approved template carries the wording; nothing from the job goes with it.
        assert message["template"]["name"] == "ck_processing_failed"
        assert message["template"]["components"] == []
        assert wording in (RELAY / "lib" / "whatsapp.js").read_text(encoding="utf-8")
    else:
        assert message["text"]["body"] == wording
    assert "CUDA" not in json.dumps(message) and "Users" not in json.dumps(message)


def test_an_undelivered_offer_can_be_sent_again_with_a_new_link(db, pc):
    phone = pc["phone"]
    job = finished_job(db, pc["dir"], title="Raptors vs Magic")
    review_id = notifier.on_job_finished(db, job, "done")
    pc["tick"]()
    first = store.get_review(db, review_id)
    sent = phone.last_message()
    old_token = phone.link_token(sent)
    requests.post(phone.url + "/__mock/status", json={"wamid": sent["wamid"], "status": "failed", "code": 131026},
                  timeout=10)
    pc["tick"]()
    review = store.get_review(db, review_id)
    assert review["state"] == states.NOTIFICATION_PENDING and review["error"]
    assert service.resend(db, review_id)
    pc["tick"]()
    review = store.get_review(db, review_id)
    assert review["state"] == states.NOTIFICATION_SENT
    assert review["approval_id"] != first["approval_id"]
    new_token = phone.link_token(phone.last_message())
    assert new_token != old_token
    _, html = phone.open(old_token)
    assert 'name="pin"' not in html
    _, html = phone.open(new_token)
    assert 'name="pin"' in html

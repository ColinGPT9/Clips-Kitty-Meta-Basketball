"""Remote approval on the PC: offers, the state machine, decisions from the
phone, and the loop that talks to the relay (remote_approval/).

Stdlib only, like the package, so CI's main job runs all of it. The relay is
faked here (tests/remote_approval_fake.py); tests/test_remote_approval_e2e.py
runs the real one.
"""

import json
import os
from datetime import datetime, timedelta

import pytest
from remote_approval_fake import SECRET, FakeRelay, finished_job, sign_command

from remote_approval import commands, notifier, service, states, store, summary
from remote_approval import settings as ra_settings
from remote_approval.relay import RelayError


@pytest.fixture
def on(db, tmp_path):
    """WhatsApp and remote approval switched on, the PC registered, every
    platform connected."""
    ra_settings.save(db, {"whatsapp_enabled": True, "relay_url": "https://relay.test", "remote_approvals": True})
    ra_settings.save(db, {"pairing": {"state": "confirmed", "tail": "4567"}, "pin_set": True,
                          "accounts": FakeRelay().accounts}, internal=True)
    from core import secrets

    secrets.save(tmp_path, ra_settings.DEVICE_SECRET, {"device_id": "dev_0123456789abcdef", "device_secret": SECRET})
    return tmp_path


def offered(db, tmp_path, relay=None, **kw):
    """A finished job, its offer made and sent: (review, relay)."""
    job = finished_job(db, tmp_path, **kw)
    review_id = notifier.on_job_finished(db, job, "done")
    relay = relay or FakeRelay()
    service.flush_outbox(db, tmp_path, relay)
    return store.get_review(db, review_id), relay


def approve_command(review, *, cid="cmd_" + "a" * 24, clips=None, platforms=("instagram",), decided=None, **extra):
    return sign_command({
        "id": cid, "kind": "approve", "approval_id": review["approval_id"], "review_ref": review["id"],
        "fingerprint": review["fingerprint"], "clips": clips or [review["clips"][0]["clip_id"]],
        "platforms": list(platforms), "decided_at": store.stamp(decided), **extra,
    })


# ---- the state machine ------------------------------------------------------------


def test_the_happy_path_is_allowed_and_shortcuts_are_not():
    path = [states.READY_FOR_REVIEW, states.NOTIFICATION_PENDING, states.NOTIFICATION_SENT,
            states.AWAITING_APPROVAL, states.APPROVED, states.PUBLISH_QUEUED, states.PUBLISHING, states.PUBLISHED]
    for a, b in zip(path, path[1:], strict=False):
        assert states.can_move(a, b), (a, b)
    assert not states.can_move(states.NOTIFICATION_PENDING, states.APPROVED)  # never approved unseen
    assert not states.can_move(states.EXPIRED, states.APPROVED)               # expiry needs a new offer
    assert not states.can_move(states.CANCELLED, states.APPROVED)
    assert not states.can_move(states.PUBLISHED, states.PUBLISH_QUEUED)       # never posted twice
    with pytest.raises(states.IllegalMove):
        states.check(states.READY_FOR_REVIEW, states.PUBLISHED)


def test_processing_and_rendering_come_from_the_job_row():
    assert states.for_job("running") == states.PROCESSING
    assert states.for_job("running", "render") == states.RENDERING
    assert states.for_job("done") is None


# ---- counting what was found ------------------------------------------------------


def test_counts_come_from_the_clips_and_read_plainly():
    clips = [{"category": c} for c in ("dunks", "dunks", "threes", "blocks", "other")]
    found = summary.counts(clips)
    assert found == {"dunks": 2, "threes": 1, "blocks": 1, "other": 1}
    assert summary.counts_text(found) == "2 dunks, 1 three, 1 block and 1 other play"
    assert summary.counts_text({}) == "no highlights"
    assert "\n" not in summary.counts_text(found)


def test_a_clip_counts_once_in_its_first_category():
    events = summary.category_events()
    assert summary.category_of({"sport_event": "alley_oop"}, events) == "dunks"     # also an assist
    assert summary.category_of({"sport_event": "game_winner"}, events) == "clutch"  # also scoring
    assert summary.category_of({"sport_event": "free_throw"}, events) == "other"
    assert summary.category_of({}, events) == "other"
    # Marked by the crowd and the signals without a confirmed type: named as
    # what it is, not lumped in with other plays.
    assert summary.category_of({"sport_event": "big_moment"}, events) == "big_moments"
    assert summary.counts_text({"dunks": 1, "big_moments": 1, "other": 2}) == \
        "1 dunk, 1 big moment and 2 other plays"


def test_the_snapshot_pins_each_file_and_the_fingerprint_follows_it(db, tmp_path):
    job = finished_job(db, tmp_path)
    clips = db.clips_for_video(job["video_id"])
    snap = summary.snapshot(clips, limit=3)
    assert [c["score"] for c in snap] == [92, 88, 80]      # best first, capped
    assert all(summary.file_matches(c) for c in snap)
    before = summary.fingerprint(snap)
    with open(snap[0]["path"], "ab") as f:
        f.write(b"re-rendered")
    assert not summary.file_matches(snap[0])
    assert summary.fingerprint(summary.snapshot(clips, limit=3)) != before


def test_the_message_text_is_deterministic(db, tmp_path):
    job = finished_job(db, tmp_path)
    clips = db.clips_for_video(job["video_id"])
    snap = summary.snapshot(clips, limit=3)
    info = summary.build("Raptors vs Celtics", clips, snap)
    assert summary.ready_text(info) == "3 clips (best 3 of 5): 2 dunks and 1 three"
    assert summary.ready_text(info) == summary.ready_text(summary.build("Raptors vs Celtics", clips, snap))


# ---- the offer -----------------------------------------------------------------------


def test_nothing_happens_while_switched_off(db, tmp_path):
    job = finished_job(db, tmp_path)
    assert notifier.on_job_finished(db, job, "done") is None
    assert store.pending_outbox(db) == 0


def test_a_finished_basketball_job_is_offered_once(db, on):
    job = finished_job(db, on)
    review_id = notifier.on_job_finished(db, job, "done")
    review = store.get_review(db, review_id)
    assert review["state"] == states.NOTIFICATION_PENDING
    assert len(review["clips"]) == 5
    assert review["summary"]["counts"]["dunks"] == 2
    assert store.pending_outbox(db) == 1
    # The same job announced again: no second offer, no second message.
    assert notifier.on_job_finished(db, job, "done") is None
    assert store.pending_outbox(db) == 1


def test_only_the_clips_this_run_rendered_are_offered(db, on):
    # The same video was processed before, as an ordinary video: its clip is
    # still in the table, with a file written before this job was queued.
    job = finished_job(db, on)
    earlier = on / "clips" / "earlier.mp4"
    earlier.write_bytes(b"x" * 5000)
    old = (datetime.fromisoformat(job["created_at"]) - timedelta(hours=2)).timestamp()
    os.utime(earlier, (old, old))
    db.add_clip(job["video_id"], 500.0, 530.0, 99, "Earlier run", path=str(earlier),
                scores=json.dumps({"sport_event": "highlight"}))

    review = store.get_review(db, notifier.on_job_finished(db, job, "done"))
    assert str(earlier) not in [c["path"] for c in review["clips"]]
    assert len(review["clips"]) == 5
    assert review["summary"]["total_clips"] == 5
    assert review["summary"]["counts"]["other"] == 1  # this run's free throw only


def test_other_sports_are_left_alone(db, on):
    job = finished_job(db, on, sport="soccer", title="Derby")
    assert notifier.on_job_finished(db, job, "done") is None


def test_a_failure_is_announced_without_its_error(db, on):
    job = finished_job(db, on)
    db.finish_job(job["id"], "failed", "Traceback: C:\\Users\\colin\\secret path")
    notifier.on_job_finished(db, db.get_job(job["id"]), "failed")
    item = store.due_outbox(db)[0]
    assert item["kind"] == "failed"
    assert "secret" not in json.dumps(item["body"])
    assert service.build_body(db, on, item) == {"kind": "failed", "event_key": item["event_key"]}


def test_sending_the_offer_records_the_relay_id(db, on):
    review, relay = offered(db, on)
    assert review["state"] == states.NOTIFICATION_SENT
    assert review["approval_id"].startswith("apr_")
    sent = relay.notified[0]
    assert sent["summary_text"] == "5 clips: 2 dunks, 1 three, 1 block and 1 other play"
    assert sent["fingerprint"] == review["fingerprint"]
    assert sent["remote_approvals"] is True
    assert all("path" not in c for c in sent["clips"])  # no local path leaves the PC
    assert store.pending_outbox(db) == 0


def test_a_relay_that_is_down_is_retried_later_and_a_refusal_is_not(db, on):
    job = finished_job(db, on)
    review_id = notifier.on_job_finished(db, job, "done")
    relay = FakeRelay()
    relay.fail["notify"] = RelayError("network", "Couldn't reach your relay.", retryable=True)
    with pytest.raises(RelayError):
        service.flush_outbox(db, on, relay)
    row = db.conn.execute("SELECT * FROM remote_outbox").fetchone()
    assert row["status"] == "pending" and row["attempts"] == 1 and row["next_try_at"] > store.stamp()
    relay.fail["notify"] = RelayError("not_paired", "No WhatsApp number is paired.")
    service.flush_outbox(db, on, relay, now=store.now_utc() + timedelta(hours=1))
    row = db.conn.execute("SELECT * FROM remote_outbox").fetchone()
    assert row["status"] == "failed"
    assert store.get_review(db, review_id)["error"] == "No WhatsApp number is paired."


# ---- decisions from the phone ----------------------------------------------------------


def test_an_approval_queues_one_publish_per_clip(db, on):
    review, _ = offered(db, on)
    ids = [c["clip_id"] for c in review["clips"][:2]]
    out = commands.handle(db, approve_command(review, clips=ids, platforms=["instagram", "threads"]), secret=SECRET)
    assert out.outcome == "accepted" and out.queued == 2
    review = store.get_review(db, review["id"])
    assert review["state"] == states.PUBLISH_QUEUED
    assert review["decision"]["platforms"] == ["instagram", "threads"]
    jobs = commands.jobs_for_review(db, review["id"])
    assert [j["clip_id"] for j in jobs] == ids
    request = json.loads(jobs[0]["request"])
    assert request == {"provider": "meta", "review_id": review["id"], "approval_id": review["approval_id"],
                       "command_id": "cmd_" + "a" * 24, "platforms": ["instagram", "threads"]}


def test_a_command_delivered_twice_is_applied_once(db, on):
    review, _ = offered(db, on)
    command = approve_command(review)
    assert commands.handle(db, command, secret=SECRET).outcome == "accepted"
    again = commands.handle(db, command, secret=SECRET)
    assert again.outcome == "accepted" and again.queued == 0
    assert len(commands.jobs_for_review(db, review["id"])) == 1


def test_a_second_approval_of_the_same_offer_is_ignored(db, on):
    review, _ = offered(db, on)
    commands.handle(db, approve_command(review), secret=SECRET)
    out = commands.handle(db, approve_command(review, cid="cmd_" + "b" * 24), secret=SECRET)
    assert out.outcome == "ignored"
    assert len(commands.jobs_for_review(db, review["id"])) == 1


@pytest.mark.parametrize("tamper, reason", [
    (lambda c: {**c, "sig": "0" * 64}, "signature"),
    (lambda c: sign_command({**c, "fingerprint": "f" * 32}), "not the clips"),
    (lambda c: sign_command({**c, "review_ref": 999}), "not one this PC made"),
    (lambda c: sign_command({**c, "approval_id": "apr_" + "9" * 24}), "not one this PC made"),
    (lambda c: sign_command({**c, "clips": [123456]}), "not in this offer"),
    (lambda c: sign_command({**c, "clips": []}), "No clips"),
    (lambda c: sign_command({**c, "platforms": ["tiktok"]}), "destinations"),
    (lambda c: sign_command({**c, "platforms": []}), "destinations"),
])
def test_a_command_that_does_not_match_its_offer_is_refused(db, on, tamper, reason):
    review, _ = offered(db, on)
    out = commands.handle(db, tamper(approve_command(review)), secret=SECRET)
    assert out.outcome == "rejected"
    assert reason in out.reason
    assert commands.jobs_for_review(db, review["id"]) == []
    assert store.get_review(db, review["id"])["state"] == states.NOTIFICATION_SENT


def test_a_clip_changed_after_the_offer_is_not_published(db, on):
    review, _ = offered(db, on)
    with open(review["clips"][0]["path"], "ab") as f:
        f.write(b"edited")
    out = commands.handle(db, approve_command(review), secret=SECRET)
    assert out.outcome == "rejected" and "changed" in out.reason


def test_a_platform_not_connected_is_refused(db, on):
    review, _ = offered(db, on)
    ra_settings.save(db, {"accounts": {"instagram": {"connected": False}}}, internal=True)
    out = commands.handle(db, approve_command(review), secret=SECRET)
    assert out.outcome == "rejected" and "not connected" in out.reason


def test_switching_remote_approval_off_refuses_approvals(db, on):
    review, _ = offered(db, on)
    ra_settings.save(db, {"remote_approvals": False})
    out = commands.handle(db, approve_command(review), secret=SECRET)
    assert out.outcome == "rejected" and "switched off" in out.reason


def test_an_approval_after_expiry_needs_a_new_offer(db, on):
    review, _ = offered(db, on)
    late = store.parse(review["expires_at"]) + timedelta(minutes=1)
    out = commands.handle(db, approve_command(review, decided=late), secret=SECRET, now=late)
    assert out.outcome == "rejected"
    assert store.get_review(db, review["id"])["state"] == states.EXPIRED


def test_an_approval_made_while_the_pc_was_off_is_honoured_when_it_comes_back(db, on):
    review, _ = offered(db, on)
    decided = store.now_utc()
    out = commands.handle(db, approve_command(review, decided=decided), secret=SECRET,
                          now=decided + timedelta(hours=20))  # the PC is back the next morning
    assert out.outcome == "accepted"


def test_an_approval_too_old_to_act_on_is_not_posted_late(db, on):
    review, _ = offered(db, on)
    db.conn.execute("UPDATE remote_reviews SET expires_at = ? WHERE id = ?",
                    (store.stamp(store.now_utc() + timedelta(days=5)), review["id"]))
    db.conn.commit()
    review = store.get_review(db, review["id"])
    decided = store.now_utc()
    out = commands.handle(db, approve_command(review, decided=decided), secret=SECRET,
                          now=decided + timedelta(days=3))  # the PC was off for three days
    assert out.outcome == "rejected" and "too old" in out.reason
    assert commands.jobs_for_review(db, review["id"]) == []


def test_cancel_defer_and_reminders(db, on):
    review, _ = offered(db, on)
    base = {"approval_id": review["approval_id"], "review_ref": review["id"]}
    out = commands.handle(db, sign_command({"id": "cmd_" + "1" * 24, "kind": "delivery", "status": "delivered",
                                            **base}), secret=SECRET)
    assert out.outcome == "accepted"
    assert store.get_review(db, review["id"])["state"] == states.AWAITING_APPROVAL
    commands.handle(db, sign_command({"id": "cmd_" + "2" * 24, "kind": "defer",
                                      "remind_at": "2030-01-01T10:00:00Z", **base}), secret=SECRET)
    review2 = store.get_review(db, review["id"])
    assert review2["state"] == states.DEFERRED and review2["remind_at"] == "2030-01-01T10:00:00Z"
    commands.handle(db, sign_command({"id": "cmd_" + "3" * 24, "kind": "reminded", **base}), secret=SECRET)
    assert store.get_review(db, review["id"])["state"] == states.AWAITING_APPROVAL
    commands.handle(db, sign_command({"id": "cmd_" + "4" * 24, "kind": "cancel", **base}), secret=SECRET)
    assert store.get_review(db, review["id"])["state"] == states.CANCELLED
    out = commands.handle(db, approve_command(review, cid="cmd_" + "5" * 24), secret=SECRET)
    assert out.outcome == "ignored" and "cancelled" in out.reason


def test_cancelling_from_the_phone_stops_uploads_that_have_not_started(db, on):
    review, _ = offered(db, on)
    commands.handle(db, approve_command(review, clips=[c["clip_id"] for c in review["clips"][:2]]), secret=SECRET)
    out = commands.handle(db, sign_command({"id": "cmd_" + "6" * 24, "kind": "cancel",
                                            "approval_id": review["approval_id"], "review_ref": review["id"]}),
                          secret=SECRET)
    assert out.outcome == "accepted"
    assert {j["status"] for j in commands.jobs_for_review(db, review["id"])} == {"cancelled"}
    assert store.get_review(db, review["id"])["state"] == states.CANCELLED


def test_a_failed_delivery_can_be_sent_again(db, on):
    review, _ = offered(db, on)
    commands.handle(db, sign_command({"id": "cmd_" + "7" * 24, "kind": "delivery", "status": "failed",
                                      "error": "WhatsApp could not deliver to this number.",
                                      "approval_id": review["approval_id"], "review_ref": review["id"]}),
                    secret=SECRET)
    review = store.get_review(db, review["id"])
    assert review["state"] == states.NOTIFICATION_PENDING
    assert "could not deliver" in review["error"]
    assert service.resend(db, review["id"])
    relay = FakeRelay()
    service.flush_outbox(db, on, relay)
    assert store.get_review(db, review["id"])["state"] == states.NOTIFICATION_SENT
    assert relay.notified[0]["event_key"].startswith(f"ready:review:{review['id']}:resend:")


# ---- settling, results and the loop ----------------------------------------------------------


def _finish(db, review, outcomes):
    """Pretend the publish worker ran: {platform: (state, url)} for every job."""
    for job in commands.jobs_for_review(db, review["id"]):
        clip = db.get_clip(job["clip_id"])
        ok = False
        for platform, (state, url) in outcomes.items():
            db.record_clip_publish(clip["id"], platform, {"provider": "meta", "state": state, "post_url": url,
                                                          "request_id": review["approval_id"],
                                                          "error": "" if state == "published" else "Refused."})
            ok = ok or state == "published"
        db.finish_publish_job(job["id"], "done" if ok else "failed")


def test_everything_published_settles_published_and_says_so(db, on):
    review, relay = offered(db, on)
    commands.handle(db, approve_command(review, platforms=["instagram", "facebook"]), secret=SECRET)
    _finish(db, review, {"instagram": ("published", "https://www.instagram.com/reel/A/"),
                         "facebook": ("published", "https://www.facebook.com/reel/1")})
    assert service.settle_publishing(db, ra_settings.load(db)) == 1
    review = store.get_review(db, review["id"])
    assert review["state"] == states.PUBLISHED
    assert review["results"]["totals"]["published"] == 2
    service.flush_outbox(db, on, relay)
    result = relay.notified[-1]
    assert result["kind"] == "result" and result["text"] == "2 of 2 posts published"


def test_a_partial_failure_is_reported_as_one_and_can_be_retried(db, on):
    review, relay = offered(db, on)
    commands.handle(db, approve_command(review, platforms=["instagram", "facebook"]), secret=SECRET)
    _finish(db, review, {"instagram": ("published", "https://www.instagram.com/reel/A/"),
                         "facebook": ("failed", "")})
    service.settle_publishing(db, ra_settings.load(db))
    review = store.get_review(db, review["id"])
    assert review["state"] == states.PARTIAL_FAILURE
    assert service.result_text(review["results"]) == "1 of 2 posts published (1 failed)"
    posts = review["results"]["clips"][0]["posts"]
    assert posts[0]["url"] and posts[1]["url"] == ""
    assert service.retry_failed(db, review["id"]) == 1
    retry = json.loads(commands.jobs_for_review(db, review["id"])[-1]["request"])
    assert retry["platforms"] == ["facebook"]
    assert store.get_review(db, review["id"])["state"] == states.PUBLISH_QUEUED


def test_an_upload_cut_off_by_a_restart_is_never_called_published(db, on):
    review, _ = offered(db, on)
    commands.handle(db, approve_command(review), secret=SECRET)
    job = commands.jobs_for_review(db, review["id"])[0]
    db.record_clip_publish(job["clip_id"], "instagram", {"provider": "meta", "state": "processing",
                                                         "request_id": review["approval_id"]})
    db.conn.execute("UPDATE publish_jobs SET status = 'running' WHERE id = ?", (job["id"],))
    db.conn.commit()
    db.recover_running_publish_jobs()
    service.settle_publishing(db, ra_settings.load(db))
    review = store.get_review(db, review["id"])
    assert review["state"] == states.FAILED
    post = review["results"]["clips"][0]["posts"][0]
    assert post["state"] == "unconfirmed" and "Check the account" in post["error"]


def test_offers_expire_on_their_own(db, on):
    review, _ = offered(db, on)
    later = store.parse(review["expires_at"]) + timedelta(seconds=1)
    assert service.expire_due(db, later) == 1
    assert store.get_review(db, review["id"])["state"] == states.EXPIRED
    new_id = service.ask_again(db, review["id"])
    assert new_id and store.get_review(db, new_id)["state"] == states.NOTIFICATION_PENDING


def test_the_loop_paces_itself_and_handles_commands(db, on):
    job = finished_job(db, on)
    notifier.on_job_finished(db, job, "done")
    relay = FakeRelay()
    state = service.TickState()
    clock = [100.0]
    service.tick(db, on, state, relay=relay, clock=lambda: clock[0])
    review = store.recent_reviews(db, 1)[0]
    assert review["state"] == states.NOTIFICATION_SENT
    relay.pending.append(approve_command(review))
    assert service.tick(db, on, state, relay=relay, clock=lambda: clock[0]) == 0  # not due yet
    clock[0] += service.FAST_SECONDS
    assert service.tick(db, on, state, relay=relay, clock=lambda: clock[0]) == 1
    assert relay.acks[-1][1] == "accepted"
    assert store.get_review(db, review["id"])["state"] == states.PUBLISH_QUEUED


def test_settings_are_cleaned_and_never_show_the_secret(db, on):
    with pytest.raises(ValueError):
        ra_settings.save(db, {"relay_url": "http://relay.example.com"})
    s = ra_settings.save(db, {"relay_url": "http://127.0.0.1:8787/", "preferred_destinations": ["threads", "tiktok"],
                              "approval_hours": 999, "pairing": {"state": "confirmed"}})
    assert s["relay_url"] == "http://127.0.0.1:8787"
    assert s["preferred_destinations"] == ["threads"]
    assert s["approval_hours"] == 72
    view = ra_settings.public_view(db, on)
    assert view["registered"] is True
    assert SECRET not in json.dumps(view)


def test_remote_approval_needs_pairing_and_a_pin_first(db, tmp_path):
    ra_settings.save(db, {"whatsapp_enabled": True})
    assert "Pair" in service.can_enable_remote_approval(db)
    ra_settings.save(db, {"pairing": {"state": "confirmed"}}, internal=True)
    assert "PIN" in service.can_enable_remote_approval(db)
    ra_settings.save(db, {"pin_set": True}, internal=True)
    assert service.can_enable_remote_approval(db) == ""

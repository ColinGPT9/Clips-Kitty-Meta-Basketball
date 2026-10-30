"""Remote approval on the PC: offers, the state machine, decisions from the
phone, and the loop that talks to the relay (remote_approval/).

Stdlib only, like the package, so CI's main job runs all of it. The relay is
faked here (tests/remote_approval_fake.py); tests/test_remote_approval_e2e.py
runs the real one.
"""

import json
import os
import time
from datetime import datetime, timedelta

import pytest
from remote_approval_fake import SECRET, FakeRelay, finished_job, sign_command

from remote_approval import captions, commands, notifier, service, states, store, summary, usage
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


def test_a_big_moment_is_called_one_and_only_unknown_plays_are_other_plays():
    events = summary.category_events()
    assert summary.category_of({"sport_event": "big_moment"}, events) == "big_moment"
    assert summary.counts_text({"dunks": 1, "big_moment": 2, "other": 1}) == "1 dunk, 2 big moments and 1 other play"
    assert summary.counts_text({"big_moment": 1}) == "1 big moment"


def test_gaming_clips_are_counted_by_their_plugins_label():
    labels = ["chase", "chase", "crash", "npc_chaos", "Combat", "funny_dialogue", "", "something_new"]
    found = summary.counts([{"category": summary.category_of({"plugin_label": label}, kind="gaming")}
                            for label in labels], "gaming")
    assert summary.counts_text(found) == "2 chases, 1 crash, 1 combat moment, 1 NPC chaos moment and 3 highlights"
    assert summary.category_of({"plugin_label": "NPC chaos"}, kind="gaming") == "npc_chaos"
    for label, words in (("stunt", "1 stunt"), ("roleplay", "1 roleplay moment"), ("reaction", "1 reaction"),
                         ("milestone", "1 milestone")):
        assert summary.counts_text({summary.category_of({"plugin_label": label}, kind="gaming"): 1}) == words
    # Any other video: its clips are highlights, whatever they carry.
    assert summary.category_of({"sport_event": "goal", "plugin_label": "chase"}, kind="video") == "highlight"


def test_a_clips_label_falls_back_to_its_plugins_label_in_plain_words():
    assert summary.label_of({"plugin_label": "npc_chaos"}) == "NPC chaos moment"
    assert summary.label_of({"plugin_label": "chase"}) == "Chase"
    assert summary.label_of({"plugin_label": "funny_dialogue"}) == "Funny dialogue"
    assert summary.label_of({"sport_label": "Poster dunk", "plugin_label": "chase"}) == "Poster dunk"
    assert summary.label_of({}) == ""


def test_highlights_with_nothing_more_to_say_read_as_highlights():
    def ready(offered, total, found):
        return summary.ready_text({"offered": offered, "total_clips": total, "counts": found,
                                   "counts_text": summary.counts_text(found)})

    assert ready(3, 3, {"highlight": 3}) == "3 highlights"
    assert ready(3, 5, {"highlight": 3}) == "3 highlights (best 3 of 5)"
    assert ready(1, 1, {"highlight": 1}) == "1 highlight"
    assert ready(3, 3, {"chase": 2, "highlight": 1}) == "3 clips: 2 chases and 1 highlight"
    assert summary.build("", [], [], kind="video")["title"] == "your video"


def _pipeline(data_dir, plugin_source, install_plugin, plugin_id, category):
    """Install a plugin pipeline in `data_dir`, as the plugin manager would."""
    manifest = plugin_source.manifest(id=plugin_id, category=category)
    install_plugin(data_dir, plugin_source.folder(plugin_id.replace("/", "_"), manifest))


def test_which_kind_of_video_a_job_is(tmp_path, plugin_source, install_plugin):
    _pipeline(tmp_path, plugin_source, install_plugin, "someone/some-pipeline", "gaming")
    _pipeline(tmp_path, plugin_source, install_plugin, "someone/scene-cuts", "utilities")
    game = {"scores": json.dumps({"plugin": "someone/some-pipeline"}), "render_opts": "{}"}
    cuts = {"scores": json.dumps({"plugin": "someone/scene-cuts"}), "render_opts": "{}"}
    plain = {"scores": "{}", "render_opts": "{}"}
    assert summary.kind_of({"sport": {"name": "basketball"}}, []) == "basketball"
    assert summary.kind_of({"sport": {"name": "soccer"}}, [plain]) == "video"
    for payload in ({"gaming": True}, {"gaming_scoring": True}):
        assert summary.kind_of(payload, [plain]) == "gaming"
    assert summary.kind_of({}, [plain]) == "video"
    # A pipeline is a game's own only when its category says so, as the
    # pipeline itself decides (plugins/gaming.py). Any other finds moments in
    # any video, and one that can't be looked up is not called gaming.
    assert summary.kind_of({"pipeline": {"id": "someone/some-pipeline"}}, [plain], tmp_path) == "gaming"
    assert summary.kind_of({}, [plain, game], tmp_path) == "gaming"
    assert summary.kind_of({"pipeline": {"id": "someone/scene-cuts"}}, [cuts], tmp_path) == "video"
    assert summary.kind_of({"pipeline": "someone/not-installed"}, [plain], tmp_path) == "video"
    assert summary.kind_of({"pipeline": {"id": "someone/some-pipeline"}}, [game]) == "video"


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


# ---- which clips an offer holds -----------------------------------------------------------


VIDEO = "vid_some_team"
BASKETBALL = {"url": "https://example.com/game.mp4", "sport": {"name": "basketball", "highlights": "best"}}
PLAIN = {"url": "https://example.com/video.mp4"}
DUNK = {"sport_event": "dunk", "sport_label": "Dunk"}
THREE = {"sport_event": "made_3", "sport_label": "Three"}


def _local(moment: float) -> str:
    """A time as a jobs row keeps it: local, to the second (core/state.py _now)."""
    return datetime.fromtimestamp(moment).isoformat(timespec="seconds")


def _job(db, video_id, payload, *, started):
    """A process job of `video_id` that the worker started at `started`."""
    db.upsert_video(video_id, title="Some Team vs Other Team")
    job_id = db.add_job("process", json.dumps(payload), video_id=video_id, title="Some Team vs Other Team")
    db.set_job(job_id, status="running", started_at=_local(started))
    return job_id


def _clip(db, folder, video_id, start, end, *, made, scores=None, opts=None, score=80, name=None):
    """One rendered clip, its file written at `made` (epoch seconds)."""
    folder.mkdir(exist_ok=True)
    path = folder / (name or f"clip_{int(start):05d}-{int(end):05d}.mp4")
    path.write_bytes(os.urandom(2048))
    os.utime(path, (made, made))
    return db.add_clip(video_id, start, end, score, "What a moment!", path=str(path), title=f"Clip at {start:.0f}s",
                       scores=json.dumps(scores or {}), render_opts=json.dumps(opts or {}))


def _offer(db, job_id, **kw):
    review_id = notifier.on_job_finished(db, db.get_job(job_id), "done", **kw)
    return store.get_review(db, review_id) if review_id else None


def test_making_clips_again_offers_only_the_new_runs_clips(db, on):
    """The live test: a plain run, then "Make clips again" with Sports on,
    offered "4 clips: 4 other plays", three of them the plain run's."""
    folder, now = on / "clips", time.time()
    plain = _job(db, VIDEO, PLAIN, started=now - 3600)
    for i in range(3):
        _clip(db, folder, VIDEO, 100.0 * i, 100.0 * i + 20, made=now - 3500)
    db.finish_job(plain, "done")
    again = _job(db, VIDEO, BASKETBALL, started=now - 60)
    new = _clip(db, folder, VIDEO, 500.0, 512.0, made=now, scores=DUNK, opts={"sport": "basketball"})
    db.finish_job(again, "done")
    review = _offer(db, again)
    assert [c["clip_id"] for c in review["clips"]] == [new]
    assert summary.ready_text(review["summary"]) == "1 clip: 1 dunk"


def test_a_re_run_that_finds_the_same_moments_is_offered_again(db, on):
    """A re-run that finds a window again points the old row at the fresh
    render and keeps the row's created_at. Its files are new, so its clips
    are this run's."""
    folder, now = on / "clips", time.time()
    first = _job(db, VIDEO, BASKETBALL, started=now - 7200)
    ids = [_clip(db, folder, VIDEO, 10.0 * i, 10.0 * i + 12, made=now - 7000, scores=DUNK) for i in range(3)]
    db.finish_job(first, "done")
    db.conn.execute("UPDATE clips SET created_at = ?", (_local(now - 7000),))
    db.conn.commit()
    again = _job(db, VIDEO, BASKETBALL, started=now - 60)
    for clip_id in ids:
        path = db.get_clip(clip_id)["path"]
        with open(path, "wb") as f:
            f.write(os.urandom(3000))
        os.utime(path, (now, now))
    db.finish_job(again, "done")
    review = _offer(db, again)
    assert sorted(c["clip_id"] for c in review["clips"]) == sorted(ids)


def test_two_rows_sharing_one_file_are_offered_once_as_the_newest(db, on):
    # 30.2-40.5 and 30.7-40.1 are two windows with the same file name.
    folder, now = on / "clips", time.time()
    job = _job(db, VIDEO, BASKETBALL, started=now - 60)
    _clip(db, folder, VIDEO, 30.2, 40.5, made=now, scores=DUNK, score=90, name="clip_00030-00040.mp4")
    newer = _clip(db, folder, VIDEO, 30.7, 40.1, made=now, scores=THREE, score=70, name="clip_00030-00040.mp4")
    db.finish_job(job, "done")
    review = _offer(db, job)
    assert [c["clip_id"] for c in review["clips"]] == [newer]
    assert summary.ready_text(review["summary"]) == "1 clip: 1 three"


def test_the_workers_own_start_time_separates_runs_within_one_second(db, on):
    folder = on / "clips"
    started = int(time.time()) - 10 + 0.9  # nine tenths of the way into a second
    job = _job(db, VIDEO, BASKETBALL, started=started)
    before = _clip(db, folder, VIDEO, 10.0, 22.0, made=started - 0.5, scores=DUNK)  # the run before, same second
    new = _clip(db, folder, VIDEO, 40.0, 52.0, made=started + 5, scores=THREE)
    db.finish_job(job, "done")
    # started_at is to the second, so on its own it can't tell the two apart.
    assert sorted(c["id"] for c in notifier.clips_of_run(db, VIDEO, db.get_job(job))) == sorted([before, new])
    assert [c["clip_id"] for c in _offer(db, job, started=started)["clips"]] == [new]


def test_a_job_with_no_start_goes_by_when_it_was_queued_and_with_no_time_at_all_by_every_clip(db, on):
    folder, now = on / "clips", time.time()
    db.upsert_video(VIDEO, title="Some Team vs Other Team")
    old = _clip(db, folder, VIDEO, 0.0, 12.0, made=now - 3600, scores=DUNK)
    job = db.add_job("process", json.dumps(BASKETBALL), video_id=VIDEO, title="Some Team vs Other Team")
    db.conn.execute("UPDATE jobs SET created_at = ? WHERE id = ?", (_local(now - 60), job))
    db.conn.commit()
    new = _clip(db, folder, VIDEO, 100.0, 112.0, made=now, scores=THREE)
    _clip(db, folder, VIDEO, 200.0, 212.0, made=now, scores=THREE, name="gone.mp4")
    os.remove(folder / "gone.mp4")
    db.finish_job(job, "done")
    assert db.get_job(job)["started_at"] == ""
    assert [c["id"] for c in notifier.clips_of_run(db, VIDEO, db.get_job(job))] == [new]
    db.conn.execute("UPDATE jobs SET created_at = 'not a time' WHERE id = ?", (job,))
    db.conn.commit()
    every = [c["id"] for c in notifier.clips_of_run(db, VIDEO, db.get_job(job))]
    assert len(every) == 3 and {old, new} <= set(every)  # as before: every clip of the video
    review = _offer(db, job)
    assert sorted(c["clip_id"] for c in review["clips"]) == sorted([old, new])  # a missing file is never offered


def test_asking_again_never_takes_in_a_later_runs_clips(db, on):
    folder, now = on / "clips", time.time()
    first = _job(db, VIDEO, BASKETBALL, started=now - 7200)
    mine = [_clip(db, folder, VIDEO, 10.0 * i, 10.0 * i + 12, made=now - 7000, scores=DUNK) for i in range(2)]
    db.finish_job(first, "done")
    db.set_job(first, finished_at=_local(now - 6900))
    review = _offer(db, first)
    store.move(db, review["id"], states.EXPIRED, actor="test")
    later = _job(db, VIDEO, BASKETBALL, started=now - 60)
    _clip(db, folder, VIDEO, 500.0, 512.0, made=now, scores=THREE)
    db.finish_job(later, "done")
    again = store.get_review(db, service.ask_again(db, review["id"]))
    assert sorted(c["clip_id"] for c in again["clips"]) == sorted(mine)
    # Cleared from the queue since: the offer's own clips, nothing new.
    store.move(db, again["id"], states.CANCELLED, actor="test")
    db.conn.execute("DELETE FROM jobs WHERE id = ?", (first,))
    db.conn.commit()
    third = store.get_review(db, service.ask_again(db, again["id"]))
    assert sorted(c["clip_id"] for c in third["clips"]) == sorted(mine)


def test_asking_again_for_a_job_with_no_finish_time_goes_up_to_its_first_offer(db, on):
    folder, now = on / "clips", time.time()
    job = _job(db, VIDEO, BASKETBALL, started=now - 60)
    mine = _clip(db, folder, VIDEO, 10.0, 22.0, made=now, scores=DUNK)  # written in the second the offer is made
    db.finish_job(job, "done")
    db.set_job(job, finished_at="")
    review = _offer(db, job)
    store.move(db, review["id"], states.EXPIRED, actor="test")
    _clip(db, folder, VIDEO, 500.0, 512.0, made=now + 30, scores=THREE)  # a later run's
    again = store.get_review(db, service.ask_again(db, review["id"]))
    assert [c["clip_id"] for c in again["clips"]] == [mine]
    # Asked again from an offer made long after the run: still up to the first one.
    store.move(db, again["id"], states.CANCELLED, actor="test")
    db.conn.execute("UPDATE remote_reviews SET created_at = ? WHERE id = ?",
                    (store.stamp(store.now_utc() + timedelta(minutes=5)), again["id"]))
    db.conn.commit()
    third = store.get_review(db, service.ask_again(db, again["id"]))
    assert [c["clip_id"] for c in third["clips"]] == [mine]


def test_longform_cuts_and_story_reels_are_never_offered(db, on):
    folder, now = on / "clips", time.time()
    job = _job(db, VIDEO, BASKETBALL, started=now - 60)
    short = _clip(db, folder, VIDEO, 10.0, 22.0, made=now, scores=DUNK)
    _clip(db, folder, VIDEO, 0.0, 600.0, made=now, opts={"profile": "highlights"})
    _clip(db, folder, VIDEO, 30.0, 90.0, made=now, opts={"reel": "team", "of": "Some Team"},
          scores={"sport_reel": "team", "sport_label": "Team reel"})
    _clip(db, folder, VIDEO, 100.0, 160.0, made=now, scores={"sport_reel": "player", "sport_label": "Player reel"})
    db.finish_job(job, "done")
    review = _offer(db, job)
    assert [c["clip_id"] for c in review["clips"]] == [short]
    assert summary.ready_text(review["summary"]) == "1 clip: 1 dunk"  # not "best 1 of 4"


def test_gaming_and_other_videos_wait_for_the_creator_to_opt_in(db, on, plugin_source, install_plugin):
    _pipeline(on, plugin_source, install_plugin, "someone/some-pipeline", "gaming")
    folder, now = on / "clips", time.time()
    pipeline = {"plugin": "someone/some-pipeline"}
    gaming = _job(db, "vid_stream", {**PLAIN, "pipeline": {"id": "someone/some-pipeline"}}, started=now - 60)
    _clip(db, folder, "vid_stream", 10.0, 30.0, made=now, score=90, scores={**pipeline, "plugin_label": "chase"})
    _clip(db, folder, "vid_stream", 40.0, 60.0, made=now, score=85, scores={**pipeline, "plugin_label": "funny_dialogue"})
    plain = _job(db, "vid_vlog", PLAIN, started=now - 60)
    _clip(db, folder, "vid_vlog", 5.0, 25.0, made=now)
    for job_id in (gaming, plain):
        db.finish_job(job_id, "done")
    assert ra_settings.load(db)["basketball_only"] is True  # as it ships
    assert _offer(db, gaming, data_dir=on) is None and _offer(db, plain, data_dir=on) is None
    assert store.pending_outbox(db) == 0

    ra_settings.save(db, {"basketball_only": False})  # Settings: "Offer gaming and other videos too"
    review = _offer(db, gaming, data_dir=on)
    assert review["summary"]["kind"] == "gaming"
    assert summary.ready_text(review["summary"]) == "2 clips: 1 chase and 1 highlight"
    assert [c["label"] for c in review["clips"]] == ["Chase", "Funny dialogue"]
    vlog = _offer(db, plain, data_dir=on)
    assert vlog["summary"]["kind"] == "video"
    assert summary.ready_text(vlog["summary"]) == "1 highlight"
    assert store.pending_outbox(db) == 2


def test_a_pipeline_that_is_not_a_games_own_offers_a_video_in_plain_words(db, on, plugin_source, install_plugin):
    """A pipeline that finds moments in any video (its category is not
    gaming) says nothing about what the video is: never tagged #gaming."""
    _pipeline(on, plugin_source, install_plugin, "someone/scene-cuts", "utilities")
    ra_settings.save(db, {"basketball_only": False})
    folder, now = on / "clips", time.time()
    job = _job(db, "vid_vlog", {**PLAIN, "pipeline": {"id": "someone/scene-cuts"}}, started=now - 60)
    _clip(db, folder, "vid_vlog", 10.0, 30.0, made=now, scores={"plugin": "someone/scene-cuts", "plugin_label": "loud"})
    db.finish_job(job, "done")
    review = _offer(db, job, data_dir=on)
    assert review["summary"]["kind"] == "video"
    assert summary.ready_text(review["summary"]) == "1 highlight"
    assert review["clips"][0]["label"] == "Loud"  # the pipeline's own word for it, on the card
    entry = review["clips"][0]
    made = captions.texts_for(review, db.get_clip(entry["clip_id"]), entry)
    assert "gaming" not in json.dumps(made).lower()


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
    """Pretend the publish worker ran: {platform: (state, url)} for every job,
    on the platforms that job carries."""
    for job in commands.jobs_for_review(db, review["id"]):
        clip = db.get_clip(job["clip_id"])
        ok = False
        for platform, (state, url) in outcomes.items():
            if platform not in json.loads(job["request"])["platforms"]:
                continue
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


def test_an_offer_leaves_out_where_a_clip_is_already_posted(db, on):
    job = finished_job(db, on)
    best = [c["id"] for c in db.clips_for_video(job["video_id"])]  # best first
    for platform in ra_settings.PLATFORMS:
        db.record_clip_publish(best[0], platform, {"provider": "woopsocial", "state": "published"})
    db.record_clip_publish(best[1], "instagram", {"provider": "woopsocial", "state": "published"})
    db.record_clip_publish(best[1], "facebook", {"provider": "woopsocial", "state": "failed"})  # never went up
    review_id = notifier.on_job_finished(db, job, "done")
    relay = FakeRelay()
    service.flush_outbox(db, on, relay)
    review = store.get_review(db, review_id)
    entries = {c["clip_id"]: c for c in review["clips"]}
    assert best[0] not in entries  # posted everywhere already: nothing to offer it for
    assert entries[best[1]]["published"] == ["instagram"]
    assert "published" not in entries[best[2]]
    assert summary.ready_text(review["summary"]) == "4 clips: 1 dunk, 1 three, 1 block and 1 other play"
    sent = {c["clip_id"]: c for c in relay.notified[0]["clips"]}
    assert sent[best[1]]["published"] == ["instagram"] and sent[best[2]]["published"] == []


def test_an_approval_never_posts_a_clip_where_it_is_already_posted(db, on):
    review, _ = offered(db, on)
    first, second = review["clips"][0]["clip_id"], review["clips"][1]["clip_id"]
    # Posted to Instagram from somewhere else while the offer waited on the phone.
    db.record_clip_publish(first, "instagram", {"provider": "uploadpost", "state": "published",
                                                "post_url": "https://www.instagram.com/reel/U/"})
    out = commands.handle(db, approve_command(review, clips=[first, second], platforms=["instagram", "facebook"]),
                          secret=SECRET)
    assert out.outcome == "accepted" and out.queued == 2
    jobs = {j["clip_id"]: json.loads(j["request"])["platforms"] for j in commands.jobs_for_review(db, review["id"])}
    assert jobs == {first: ["facebook"], second: ["instagram", "facebook"]}
    _finish(db, review, {"instagram": ("published", "https://www.instagram.com/reel/A/"),
                         "facebook": ("published", "https://www.facebook.com/reel/1")})
    service.settle_publishing(db, ra_settings.load(db))
    review = store.get_review(db, review["id"])
    assert review["state"] == states.PUBLISHED
    posts = {(c["clip_id"], p["platform"]): p for c in review["results"]["clips"] for p in c["posts"]}
    assert posts[(first, "instagram")]["state"] == "skipped" and "Already posted" in posts[(first, "instagram")]["error"]
    assert posts[(second, "instagram")]["state"] == "published"
    assert service.result_text(review["results"]) == "3 of 4 posts published (1 skipped)"


def test_an_approval_with_nothing_left_to_post_is_refused(db, on):
    review, _ = offered(db, on)
    clip = review["clips"][0]["clip_id"]
    db.record_clip_publish(clip, "instagram", {"provider": "meta", "state": "published", "request_id": "apr_earlier"})
    out = commands.handle(db, approve_command(review, clips=[clip], platforms=["instagram"]), secret=SECRET)
    assert out.outcome == "rejected" and "already posted" in out.reason
    assert commands.jobs_for_review(db, review["id"]) == []
    assert store.get_review(db, review["id"])["state"] == states.FAILED


def test_an_approval_never_posts_where_a_post_is_on_its_way(db, on):
    """WoopSocial keeps a post it has scheduled as queued until it is due,
    and an upload from another publisher is processing: both go up all the
    same, so the phone's approval leaves those platforms out."""
    review, _ = offered(db, on)
    first, second = (c["clip_id"] for c in review["clips"][:2])
    db.record_clip_publish(first, "instagram", {"provider": "woopsocial", "state": "queued",
                                                "scheduled_for": "2099-01-01T12:00:00Z"})
    db.record_clip_publish(second, "facebook", {"provider": "uploadpost", "state": "processing"})
    out = commands.handle(db, approve_command(review, clips=[first, second], platforms=["instagram", "facebook"]),
                          secret=SECRET)
    assert out.outcome == "accepted" and out.queued == 2
    jobs = {j["clip_id"]: json.loads(j["request"])["platforms"] for j in commands.jobs_for_review(db, review["id"])}
    assert jobs == {first: ["facebook"], second: ["instagram"]}
    _finish(db, review, {"instagram": ("published", "https://www.instagram.com/reel/A/"),
                         "facebook": ("published", "https://www.facebook.com/reel/1")})
    service.settle_publishing(db, ra_settings.load(db))
    review = store.get_review(db, review["id"])
    assert review["state"] == states.PUBLISHED
    posts = {(c["clip_id"], p["platform"]): p for c in review["results"]["clips"] for p in c["posts"]}
    assert posts[(first, "instagram")] == {"platform": "instagram", "state": "skipped", "url": "",
                                           "error": "Already on its way there, so it was not posted again."}
    assert posts[(second, "facebook")]["state"] == "skipped"
    assert service.result_text(review["results"]) == "2 of 4 posts published (2 skipped)"


def test_a_clip_on_its_way_everywhere_is_not_offered_and_only_what_is_up_is_called_posted(db, on):
    job = finished_job(db, on)
    best = [c["id"] for c in db.clips_for_video(job["video_id"])]  # best first
    for platform in ra_settings.PLATFORMS:
        db.record_clip_publish(best[0], platform, {"provider": "woopsocial", "state": "queued"})
    db.record_clip_publish(best[1], "instagram", {"provider": "woopsocial", "state": "queued"})
    review = store.get_review(db, notifier.on_job_finished(db, job, "done"))
    entries = {c["clip_id"]: c for c in review["clips"]}
    assert best[0] not in entries
    assert "published" not in entries[best[1]]  # the page says "Already posted" only of what is up


def test_a_clip_made_again_under_a_new_id_keeps_where_it_was_posted(db, on):
    """Deleted after it was posted, then made again by a re-run: a new row
    for the same window. Its posts still count, matched on the clip's video
    and window as WoopSocial's guard matches them."""
    job = finished_job(db, on)
    old = db.clips_for_video(job["video_id"])[0]
    db.record_clip_publish(int(old["id"]), "instagram", {
        "provider": "woopsocial", "state": "published",
        "video_id": old["video_id"], "start_s": old["start_s"], "end_s": old["end_s"]})
    db.delete_clip(int(old["id"]))
    new = db.add_clip(old["video_id"], old["start_s"], old["end_s"], old["score"], old["hook"], path=old["path"],
                      title=old["title"], scores=old["scores"], render_opts=old["render_opts"])
    assert new != old["id"]
    review_id = notifier.on_job_finished(db, job, "done")
    service.flush_outbox(db, on, FakeRelay())
    review = store.get_review(db, review_id)
    assert next(c for c in review["clips"] if c["clip_id"] == new)["published"] == ["instagram"]
    commands.handle(db, approve_command(review, clips=[new], platforms=["instagram", "facebook"]), secret=SECRET)
    assert [json.loads(j["request"])["platforms"] for j in commands.jobs_for_review(db, review["id"])] == [["facebook"]]


def test_a_retry_sends_again_only_what_failed(db, on):
    """Never an upload a restart cut off: it may be up already."""
    review, _ = offered(db, on)
    cut, refused = (c["clip_id"] for c in review["clips"][:2])
    commands.handle(db, approve_command(review, clips=[cut, refused], platforms=["instagram"]), secret=SECRET)
    for job in commands.jobs_for_review(db, review["id"]):
        state, status = ("processing", "interrupted") if job["clip_id"] == cut else ("failed", "failed")
        db.record_clip_publish(job["clip_id"], "instagram", {"provider": "meta", "state": state, "error": "Refused.",
                                                             "request_id": review["approval_id"]})
        db.conn.execute("UPDATE publish_jobs SET status = ? WHERE id = ?", (status, job["id"]))
    db.conn.commit()
    service.settle_publishing(db, ra_settings.load(db))
    review = store.get_review(db, review["id"])
    assert service.result_text(review["results"]) == "0 of 2 posts published (1 failed, 1 not confirmed yet)"
    assert service.retry_failed(db, review["id"]) == 1
    again = [j for j in commands.jobs_for_review(db, review["id"]) if j["status"] == "queued"]
    assert [(j["clip_id"], json.loads(j["request"])["platforms"]) for j in again] == [(refused, ["instagram"])]


def test_a_local_retry_sends_only_the_clips_it_names(db, on):
    review, _ = offered(db, on)
    first, second = (c["clip_id"] for c in review["clips"][:2])
    decision = {"command_id": "cmd_" + "b" * 24, "clips": [first, second], "platforms": ["instagram", "facebook"]}
    assert commands.queue_publishes(db, review, decision, only={first: ["facebook"]}) == 1
    jobs = [(j["clip_id"], json.loads(j["request"])["platforms"]) for j in commands.jobs_for_review(db, review["id"])]
    assert jobs == [(first, ["facebook"])]


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


# ---- the creator's Muse agent -------------------------------------------------------------


@pytest.fixture
def muse_only(db, tmp_path):
    """No WhatsApp at all: the PC registered, a Muse key working, a PIN set,
    every platform connected, remote approval on."""
    ra_settings.save(db, {"relay_url": "https://relay.test", "remote_approvals": True})
    ra_settings.save(db, {"pin_set": True, "accounts": FakeRelay().accounts,
                          "muse": {"enabled": True, "created_at": 1_800_000_000, "last_used": 0}}, internal=True)
    from core import secrets

    secrets.save(tmp_path, ra_settings.DEVICE_SECRET, {"device_id": "dev_0123456789abcdef", "device_secret": SECRET})
    return tmp_path


def test_a_connected_muse_stands_in_for_a_paired_number_but_the_pin_is_still_asked(db, tmp_path):
    assert "Muse" in service.can_enable_remote_approval(db)
    ra_settings.save(db, {"muse": {"enabled": True}}, internal=True)
    assert "PIN" in service.can_enable_remote_approval(db)
    ra_settings.save(db, {"pin_set": True}, internal=True)
    assert service.can_enable_remote_approval(db) == ""


def test_offers_reach_muse_with_whatsapp_off_but_a_failure_message_does_not(db, muse_only):
    review_id = notifier.on_job_finished(db, finished_job(db, muse_only), "done")
    assert store.get_review(db, review_id)["state"] == states.NOTIFICATION_PENDING
    failed = finished_job(db, muse_only, title="Some Team vs Away Team")
    db.finish_job(failed["id"], "failed", "out of memory")
    assert notifier.on_job_finished(db, db.get_job(failed["id"]), "failed") is None
    assert [item["kind"] for item in store.due_outbox(db)] == ["ready"]  # no failure message: there is no WhatsApp
    ra_settings.save(db, {"notify_on_finish": False})
    assert notifier.on_job_finished(db, finished_job(db, muse_only, title="Third Game"), "done") is None
    ra_settings.save(db, {"notify_on_finish": True})
    ra_settings.save(db, {"muse": {}}, internal=True)  # the key revoked: nothing can reach the creator now
    assert notifier.on_job_finished(db, finished_job(db, muse_only, title="Fourth Game"), "done") is None


def test_the_loop_runs_for_muse_alone_and_stops_when_neither_is_on(db, muse_only):
    relay = FakeRelay()
    relay.muse = {"enabled": True, "created_at": 1_800_000_000, "last_used": 1_800_000_100}
    review_id = notifier.on_job_finished(db, finished_job(db, muse_only), "done")
    service.tick(db, muse_only, service.TickState(), relay=relay, clock=lambda: 1000.0)
    assert store.get_review(db, review_id)["state"] == states.NOTIFICATION_SENT
    assert relay.notified[0]["video_kind"] == "basketball"
    assert ra_settings.load(db)["muse"] == relay.muse  # what the relay said, read on the pass
    ra_settings.save(db, {"muse": {}}, internal=True)
    quiet = FakeRelay()
    assert service.tick(db, muse_only, service.TickState(), relay=quiet, clock=lambda: 1000.0) == 0
    assert quiet.calls == []


def test_the_relay_is_told_whether_whatsapp_is_wanted(db, muse_only):
    relay = FakeRelay()
    service.push_preferences(db, relay)
    prefs = next(call[1] for call in relay.calls if call[0] == "preferences")
    assert prefs["whatsapp_enabled"] is False and prefs["notify_on_finish"] is True


def test_results_go_to_the_relay_even_with_publishing_messages_off(db, on):
    """The relay keeps them for the status page and for Muse's "what got
    posted?"; it is the one that leaves the WhatsApp message out."""
    ra_settings.save(db, {"notify_on_publish": False})
    review, relay = offered(db, on)
    commands.handle(db, approve_command(review), secret=SECRET)
    _finish(db, review, {"instagram": ("published", "https://www.instagram.com/reel/A/")})
    service.settle_publishing(db, ra_settings.load(db))
    service.flush_outbox(db, on, relay)
    result = relay.notified[-1]
    assert result["kind"] == "result" and result["approval_id"] == review["approval_id"]
    assert result["results"]["clips"][0]["posts"][0]["url"] == "https://www.instagram.com/reel/A/"


def test_a_defer_or_cancel_asked_of_muse_is_logged_as_muses(db, on):
    review, _ = offered(db, on)
    base = {"approval_id": review["approval_id"], "review_ref": review["id"]}
    # The via is inside what the relay signed: added afterwards, the command is not the relay's.
    forged = {**sign_command({"id": "cmd_" + "c" * 24, "kind": "defer", **base}), "via": "muse"}
    assert commands.handle(db, forged, secret=SECRET).outcome == "rejected"
    out = commands.handle(db, sign_command({"id": "cmd_" + "8" * 24, "kind": "defer", "via": "muse",
                                            "remind_at": "2030-01-01T10:00:00Z", **base}), secret=SECRET)
    assert out.outcome == "accepted"
    commands.handle(db, sign_command({"id": "cmd_" + "9" * 24, "kind": "cancel", "via": "muse", **base}), secret=SECRET)
    assert store.get_review(db, review["id"])["state"] == states.CANCELLED
    actors = {row["action"]: row["actor"] for row in store.audit_log(db, review["id"])}
    assert actors["defer_accepted"] == actors["deferred"] == "muse"
    assert actors["cancel_accepted"] == actors["cancelled"] == "muse"


def test_an_approval_that_says_it_came_from_muse_is_refused(db, on):
    review, _ = offered(db, on)
    out = commands.handle(db, approve_command(review, via="muse"), secret=SECRET)
    assert out.outcome == "rejected" and "approval page with your PIN" in out.reason
    assert commands.jobs_for_review(db, review["id"]) == []
    assert store.get_review(db, review["id"])["state"] == states.NOTIFICATION_SENT


def test_the_muse_key_is_handed_over_once_and_never_kept(db, muse_only):
    relay = FakeRelay()
    key = service.make_muse_key(db, relay)
    assert key.startswith("ckm_") and key == relay.keys[0]
    saved = db.get_flag(ra_settings.SETTINGS_KEY, "")
    assert key not in saved and json.loads(saved)["muse"]["enabled"] is True
    assert key not in json.dumps(ra_settings.public_view(db, muse_only))
    service.revoke_muse_key(db, relay)
    config = ra_settings.load(db)
    assert config["muse"]["enabled"] is False
    assert config["remote_approvals"] is False  # nothing is left to reach the creator


def test_an_offer_kept_for_muse_is_not_counted_as_a_whatsapp_message(db):
    usage.record_whatsapp(db, "muse")
    usage.record_whatsapp(db, "interactive")
    assert usage.load(db)["whatsapp"] == {"templates": 0, "free_form": 1, "usd": 0.0}

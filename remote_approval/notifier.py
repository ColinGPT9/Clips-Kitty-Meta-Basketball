"""The pipeline worker's one call into remote approval.

server/jobs.py Worker._announce is the single place every job's terminal
state passes through (done, failed, cancelled), and it calls on_job_finished
from there. What happens here is a few database writes and a look at the
clip files' times, nothing else: no network, no model, no ffmpeg. The
pipeline thread must never wait on WhatsApp, and a relay that is down must
never fail or slow a render. The publish worker's loop (service.tick) sends
what this queues.

Only the clips this run made are offered (clips_of_run). A video keeps every
run's clips, so "Make clips again" with the Sports toggle on would otherwise
offer the plain run's leftovers beside its own.

Duplicate-safe by construction: the outbox key is the job id, so the same
job announced twice queues one message, and an offer is only made once per
job unless the creator asks for a new one.
"""

import json
import os
from datetime import datetime, timedelta

from remote_approval import settings as ra_settings
from remote_approval import states, store, summary

#: How far a clip file's time may read before its job's start and still be
#: that run's. A file system stamps files from a coarser clock than the one
#: the jobs row is written from, so a file written in the same instant can
#: read a few milliseconds earlier. Only for the row's times, which are to
#: the second anyway; the worker's own start time is compared as it is.
STAMP_SLACK = 0.05


def on_job_finished(db, job, status: str, *, started: float | None = None, data_dir=None) -> int | None:
    """Queue what this finished job should tell the creator. Returns the new
    offer's id, or None when nothing was offered (switched off, a video the
    settings leave out, nothing found, or a failure notice instead).

    `started` is when the worker began the job (time.time()), when it knows:
    finer than the row's started_at, so a run straight after another never
    takes that one's clips (clips_of_run). `data_dir` is the app's data
    folder, where an installed plugin says whether it is a game's own
    pipeline (summary.kind_of)."""
    config = ra_settings.load(db)
    if not config["whatsapp_enabled"] or job["type"] != "process":
        return None
    try:
        payload = json.loads(job["payload"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    row = db.get_job(job["id"])
    video_id = (row["video_id"] if row else "") or ""
    clips = clips_of_run(db, video_id, row or job, started=started)
    kind = summary.kind_of(payload, clips, data_dir)
    # Basketball only, unless the creator opted in to gaming and other videos.
    if config["basketball_only"] and kind != "basketball":
        return None

    if status == "failed":
        if config["notify_on_fail"]:
            # Never the error itself: it can name paths, and the phone is not
            # where it is read. The app shows it.
            store.enqueue(db, f"failed:job:{job['id']}", "failed", body={"job_id": int(job["id"])})
        return None
    if status != "done" or not config["notify_on_finish"]:
        return None
    if any(r["state"] not in states.SETTLED for r in store.reviews_for_job(db, job["id"])):
        return None  # already offered and still open: an announce repeated
    return offer(db, job_id=int(job["id"]), video_id=video_id,
                 title=(row["title"] if row else "") or job["title"] or "", clips=clips, config=config,
                 kind=kind)


def clips_of_run(db, video_id: str, job, *, started: float | None = None, until: float | None = None) -> list:
    """The clips one run of this job made, out of every clip of its video.

    A clip is the run's when its file was written at or after the job
    started, and before `until` when that is given. Not the row's
    created_at: a re-run that finds the same window points the old row at
    its fresh render and keeps the old created_at, so an identical re-run
    would offer nothing. The start is `started` (the worker's own time) when
    given, else the row's started_at, else its created_at for a job that
    never started. Two rows can point at one file (two windows that round to
    the same file name); the newest row, the one the file was last made for,
    is the one kept. A clip whose file is gone is left out. With no time to
    go by at all, every clip of the video, as before."""
    rows = list(db.clips_for_video(video_id)) if video_id else []
    if started is None:
        stamp = job_time(job, "started_at")
        if stamp is None:
            stamp = job_time(job, "created_at")
        if stamp is None:
            return rows
        started = stamp - STAMP_SLACK
    newest: dict[str, object] = {}
    for row in rows:
        path = str(row["path"] or "")
        try:
            made = os.stat(path).st_mtime
        except OSError:
            continue  # its file is gone
        if made < started or (until is not None and made >= until):
            continue
        key = os.path.normcase(path)
        if key not in newest or int(row["id"]) > int(newest[key]["id"]):
            newest[key] = row
    kept = {int(row["id"]) for row in newest.values()}
    return [row for row in rows if int(row["id"]) in kept]


def job_time(job, column: str) -> float | None:
    """One of a jobs row's times as epoch seconds, or None. The row keeps
    local time with no zone on it (core/state.py _now), so it is read as
    local time, not as UTC."""
    if job is None:
        return None
    try:
        return datetime.fromisoformat(str(job[column] or "")).timestamp()
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def published_on(db, clips) -> dict[int, list[str]]:
    """Where each clip is already published, by any provider, in the order
    the platforms are offered: {clip_id: ["instagram", ...]}."""
    out = {}
    for clip in clips:
        done = store.published_platforms(db, int(clip["id"]))
        mine = [p for p in ra_settings.PLATFORMS if p in done]
        if mine:
            out[int(clip["id"])] = mine
    return out


def offer(db, *, job_id: int, video_id: str, title: str, clips, config: dict | None = None,
          round_no: int | None = None, kind: str = "basketball") -> int | None:
    """Make an offer of these clips and queue its message.

    Also what "Ask again" in Settings calls after an offer expired: a new
    offer with a new link, never the old link brought back. A clip already
    posted, or on its way, everywhere an offer can send it is left out; one
    published to some of those places is offered with a note of where
    (summary.snapshot), and is never sent there again (commands.queue_publishes)."""
    config = config or ra_settings.load(db)
    clips = list(clips)
    posted = published_on(db, clips)
    every = set(ra_settings.PLATFORMS)
    clips = [c for c in clips if not every <= set(store.sent_platforms(db, int(c["id"])))]
    snap = summary.snapshot(clips, limit=int(config["max_clips"]), kind=kind, published=posted)
    if not snap:
        store.audit(db, 0, "desktop", "nothing_to_offer",
                    f"job {job_id}: no clips left to offer (none on disk, or all posted already)")
        return None
    info = summary.build(title, clips, snap, kind=kind)
    expires = store.now_utc() + timedelta(hours=int(config["approval_hours"]))
    review_id = store.create_review(
        db, job_id=job_id, video_id=video_id, title=info["title"], clips=snap,
        fingerprint=summary.fingerprint(snap), summary=info, expires_at=store.stamp(expires),
    )
    store.audit(db, review_id, "desktop", states.READY_FOR_REVIEW.lower(), summary.ready_text(info))
    nth = round_no if round_no is not None else len(store.reviews_for_job(db, job_id))
    store.enqueue(db, f"ready:job:{job_id}:{nth}", "ready", review_id=review_id)
    store.move(db, review_id, states.NOTIFICATION_PENDING, actor="desktop")
    return review_id

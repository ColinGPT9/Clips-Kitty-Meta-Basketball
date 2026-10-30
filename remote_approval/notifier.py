"""The pipeline worker's one call into remote approval.

server/jobs.py Worker._announce is the single place every job's terminal
state passes through (done, failed, cancelled), and it calls on_job_finished
from there. What happens here is a few database writes and nothing else: no
network, no model, no ffmpeg. The pipeline thread must never wait on
WhatsApp, and a relay that is down must never fail or slow a render. The
publish worker's loop (service.tick) sends what this queues.

Duplicate-safe by construction: the outbox key is the job id, so the same
job announced twice queues one message, and an offer is only made once per
job unless the creator asks for a new one.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from remote_approval import settings as ra_settings
from remote_approval import states, store, summary


def on_job_finished(db, job, status: str) -> int | None:
    """Queue what this finished job should tell the creator. Returns the new
    offer's id, or None when nothing was offered (switched off, not a
    basketball video, nothing found, or a failure notice instead)."""
    config = ra_settings.load(db)
    if not config["whatsapp_enabled"] or job["type"] != "process":
        return None
    try:
        payload = json.loads(job["payload"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    row = db.get_job(job["id"])
    video_id = (row["video_id"] if row else "") or ""
    clips = clips_of_run(db, row)
    if config["basketball_only"] and not summary.is_basketball(payload if isinstance(payload, dict) else {},
                                                               clips):
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
                 title=(row["title"] if row else "") or job["title"] or "", clips=clips, config=config)


def clips_of_run(db, job_row) -> list:
    """The clips this job rendered, not every clip its video has.

    Clips are kept per video, so a video processed before (say once as an
    ordinary video, then as basketball) still has the earlier run's clips.
    Those are not this game's highlights. A clip is this run's when its file
    was written after the job was queued: a new clip has a new file, and a
    window found again is pointed at its fresh render by the pipeline."""
    video_id = (job_row["video_id"] if job_row else "") or ""
    if not video_id:
        return []
    clips = db.clips_for_video(video_id)
    try:
        # StateDB stamps local time without a zone, as timestamp() reads it.
        since = datetime.fromisoformat(str(job_row["created_at"])).timestamp()
    except (TypeError, ValueError, KeyError, IndexError):
        return list(clips)
    out = []
    for clip in clips:
        try:
            if Path(clip["path"] or "").stat().st_mtime >= since:
                out.append(clip)
        except OSError:
            continue  # no file: nothing that could be offered anyway
    return out


def offer(db, *, job_id: int, video_id: str, title: str, clips, config: dict | None = None,
          round_no: int | None = None) -> int | None:
    """Make an offer of this video's clips and queue its message.

    Also what "Ask again" in Settings calls after an offer expired: a new
    offer with a new link, never the old link brought back."""
    config = config or ra_settings.load(db)
    clips = list(clips)
    snap = summary.snapshot(clips, limit=int(config["max_clips"]))
    if not snap:
        store.audit(db, 0, "desktop", "nothing_to_offer", f"job {job_id}: no finished clips on disk")
        return None
    info = summary.build(title, clips, snap)
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

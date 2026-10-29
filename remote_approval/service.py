"""The remote approval loop, and the actions Settings takes.

tick() runs on the publish worker's thread (server/publisher.py), between
uploads. Each pass, when one is due:

1. offers whose link has expired become EXPIRED;
2. offers whose uploads have all finished are settled (PUBLISHED,
   PARTIAL_FAILURE or FAILED, from what the APIs confirmed) and their
   result is queued for WhatsApp;
3. the outbox is sent to the relay, oldest first, each message once (the
   relay keys on the same event key, so a retry after a lost answer is not
   a second message);
4. the relay is asked for decisions made on the phone; each is checked
   (commands.py) and the relay told what became of it.

Pacing: every few seconds while something is open (an offer waiting, a
message queued, an upload in flight), every couple of minutes otherwise,
never while switched off. All of it is outbound: this PC opens no port.

If the PC was off, nothing is lost: the relay keeps the decision, the
approval page says "waiting for your PC", and the first pass after start
picks it up. An approval is honoured if it was made before the link
expired and is not stale (commands.MAX_COMMAND_AGE); otherwise the creator
is asked again rather than having it posted late.
"""

import base64
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from remote_approval import captions, commands, states, store, summary, usage
from remote_approval import settings as ra_settings
from remote_approval.relay import Relay, RelayError

FAST_SECONDS = 5
SLOW_SECONDS = 120
STATUS_SECONDS = 600


@dataclass
class TickState:
    next_pass: float = 0.0
    next_status: float = 0.0
    last_error: str = ""
    warned: set = field(default_factory=set)


def client(db, data_dir) -> Relay | None:
    config = ra_settings.load(db)
    device = ra_settings.device(data_dir)
    if not config["relay_url"] or not device:
        return None
    return Relay(config["relay_url"], **device)


def busy(db) -> bool:
    open_states = [*states.OPEN, states.NOTIFICATION_PENDING, states.APPROVED, states.PUBLISH_QUEUED,
                   states.PUBLISHING]
    return bool(store.reviews_in(db, open_states)) or store.pending_outbox(db) > 0


def tick(db, data_dir, state: TickState, *, relay: Relay | None = None, emit=None, clock=time.monotonic,
         now=None) -> int:
    """One pass, if one is due. Returns how many uploads it queued."""
    config = ra_settings.load(db)
    if not config["whatsapp_enabled"]:
        return 0
    mono = clock()
    if mono < state.next_pass:
        return 0
    state.next_pass = mono + (FAST_SECONDS if busy(db) else SLOW_SECONDS)
    relay = relay or client(db, data_dir)
    if relay is None:
        return 0
    now = now or store.now_utc()
    changed = expire_due(db, now) + settle_publishing(db, config)
    queued = 0
    try:
        if mono >= state.next_status:
            state.next_status = mono + STATUS_SECONDS
            refresh_status(db, relay)
        changed += flush_outbox(db, data_dir, relay, now=now)
        queued, handled = poll_commands(db, relay, data_dir, now=now)
        changed += handled
        state.last_error = ""
    except RelayError as e:
        if e.message != state.last_error:
            print(f"  Relay: {e.message}")
        state.last_error = e.message
    if changed and emit:
        emit({"type": "remote_approval", "changed": changed})
    return queued


# ---- 1. expiry ---------------------------------------------------------------------


def expire_due(db, now) -> int:
    moved = 0
    for review in store.reviews_in(db, [states.NOTIFICATION_PENDING, *states.OPEN]):
        expires = store.parse(review["expires_at"])
        if expires is not None and now > expires:
            try:
                store.move(db, review["id"], states.EXPIRED, actor="desktop", detail="the link expired")
                moved += 1
            except states.IllegalMove:
                pass
    return moved


# ---- 2. settling what was published -------------------------------------------------


def settle_publishing(db, config: dict) -> int:
    settled = 0
    for review in store.reviews_in(db, [states.PUBLISH_QUEUED, states.PUBLISHING]):
        jobs = commands.jobs_for_review(db, review["id"])
        if not jobs:
            continue
        if review["state"] == states.PUBLISH_QUEUED and any(j["status"] != "queued" for j in jobs):
            review = store.move(db, review["id"], states.PUBLISHING, actor="publisher")
        if any(j["status"] in ("queued", "running") for j in jobs):
            continue
        results = results_for(db, review, jobs)
        totals = results["totals"]
        if totals["published"] and not totals["failed"] and not totals["unconfirmed"]:
            final = states.PUBLISHED
        elif totals["published"]:
            final = states.PARTIAL_FAILURE
        else:
            final = states.FAILED
        store.move(db, review["id"], final, actor="publisher", results=results,
                   detail=f"{totals['published']} published, {totals['failed']} failed, "
                          f"{totals['unconfirmed']} unconfirmed")
        if config["notify_on_publish"]:
            nth = len([r for r in store.audit_log(db, review["id"], 500) if r["action"] in
                       (states.PUBLISHED.lower(), states.PARTIAL_FAILURE.lower(), states.FAILED.lower())])
            store.enqueue(db, f"result:review:{review['id']}:{nth}", "result", review_id=review["id"])
        settled += 1
    return settled


def results_for(db, review: dict, jobs) -> dict:
    """Per clip and platform, as the APIs confirmed: published (with the
    link the API returned), failed (with why), or unconfirmed (processing,
    or cut off by a restart: the creator is told to check)."""
    clips_out = []
    totals = {"published": 0, "failed": 0, "unconfirmed": 0, "skipped": 0}
    by_clip = {j["clip_id"]: j for j in jobs}
    platforms = review["decision"].get("platforms") or []
    for clip_id in review["decision"].get("clips") or []:
        job = by_clip.get(clip_id)
        rows = {r["platform"]: r for r in db.clip_publishes(clip_id) if r["provider"] == "meta"}
        entry = next((c for c in review["clips"] if c["clip_id"] == clip_id), {})
        posts = []
        for platform in platforms:
            row = rows.get(platform)
            state = row["state"] if row else "failed"
            error = row["error"] if row else (job["error"] if job else "") or "Not posted."
            if job is not None and job["status"] == "interrupted" and state == "processing":
                state = "unconfirmed"
                error = "Clips Kitty closed while this was uploading. Check the account before trying again."
            elif state == "processing":
                state = "unconfirmed"
            elif job is not None and job["status"] == "cancelled" and state != "published":
                state = "failed"
                error = job["error"] or "Cancelled."
            key = {"published": "published", "skipped": "skipped", "unconfirmed": "unconfirmed"}.get(state, "failed")
            totals[key] += 1
            posts.append({
                "platform": platform,
                "state": key,
                "url": row["post_url"] if row and key == "published" else "",
                "error": "" if key == "published" else str(error)[:200],
            })
        clips_out.append({"clip_id": clip_id, "title": entry.get("title") or entry.get("label") or "",
                          "posts": posts})
    return {"clips": clips_out, "totals": totals}


def result_text(results: dict) -> str:
    """"3 of 4 posts published (1 failed)": counted from confirmed results
    only, for the WhatsApp confirmation. No links in it: those are on the
    status page, exactly as the APIs returned them."""
    t = results.get("totals") or {}
    done = int(t.get("published") or 0)
    total = sum(int(t.get(k) or 0) for k in ("published", "failed", "unconfirmed", "skipped"))
    extra = []
    if t.get("failed"):
        extra.append(f"{t['failed']} failed")
    if t.get("unconfirmed"):
        extra.append(f"{t['unconfirmed']} not confirmed yet")
    if t.get("skipped"):
        extra.append(f"{t['skipped']} skipped")
    text = f"{done} of {total} post{'s' if total != 1 else ''} published"
    return text + (f" ({', '.join(extra)})" if extra else "")


# ---- 3. the outbox ----------------------------------------------------------------------


#: Relay answers that mean "this will not work by waiting": the message is
#: marked failed at once, with the relay's own words, for Settings to show.
PERMANENT = {"not_paired", "not_confirmed", "opted_out", "template_missing", "template_not_approved",
             "bad_request", "not_registered", "forbidden"}


def flush_outbox(db, data_dir, relay: Relay, *, now=None) -> int:
    sent = 0
    for item in store.due_outbox(db, now):
        try:
            body = build_body(db, data_dir, item)
        except _Drop as e:
            store.outbox_done(db, item["id"], "dropped", str(e))
            continue
        try:
            answer = relay.notify(body)
        except RelayError as e:
            if e.code in PERMANENT:
                store.outbox_done(db, item["id"], "failed", e.message)
                if item["review_id"]:
                    store.update_review(db, item["review_id"], error=e.message)
                    store.audit(db, item["review_id"], "relay", "notify_failed", e.message)
                continue
            store.outbox_retry(db, item, e.message, now)
            if not e.retryable:
                continue
            raise
        store.outbox_done(db, item["id"], "sent")
        usage.record_whatsapp(db, str(answer.get("channel") or ""))
        if item["kind"] == "ready":
            _sent_offer(db, item["review_id"], answer)
        sent += 1
    return sent


class _Drop(Exception):
    """This message no longer has anything to say (its offer was cancelled)."""


def build_body(db, data_dir, item: dict) -> dict:
    config = ra_settings.load(db)
    kind = item["kind"]
    base = {"kind": kind, "event_key": item["event_key"]}
    if kind in ("failed", "test"):
        return base
    review = store.get_review(db, item["review_id"])
    if review is None:
        raise _Drop("its offer is gone")
    if kind == "result":
        if not review["approval_id"]:
            raise _Drop("the offer was never sent")
        return {**base, "approval_id": review["approval_id"], "state": review["state"],
                "text": result_text(review["results"]), "results": review["results"]}
    if review["state"] != states.NOTIFICATION_PENDING:
        raise _Drop(f"the offer is {review['state'].lower()}")
    clip_rows = {c["clip_id"]: db.get_clip(c["clip_id"]) for c in review["clips"]}
    if config["muse_captions"] or config["muse_summary"]:
        try:
            review["captions"] = captions.generate(db, data_dir, review, clip_rows, model=config["muse_model"])
        except Exception as e:  # the clips' own text is used; an offer never waits on a model
            message = getattr(e, "message", "") or "Muse Spark was not available."
            store.audit(db, review["id"], "desktop", "muse_spark_skipped", message[:200])
    clips = []
    for entry in review["clips"]:
        text = captions.texts_for(review, clip_rows.get(entry["clip_id"]), entry)
        clips.append({
            "clip_id": entry["clip_id"],
            "title": text["title"][:100],
            "label": entry.get("label") or "",
            "category": entry.get("category") or "",
            "duration": entry.get("duration") or 0,
            "texts": {"instagram": text["instagram"][:2200], "facebook": text["facebook"][:2000],
                      "threads": text["threads"][:500]},
            "text_source": text["source"],
            "thumb": thumbnail(entry),
        })
    muse_line = (review.get("captions") or {}).get("summary", "") if config["muse_summary"] else ""
    return {
        **base,
        "review_ref": review["id"],
        "fingerprint": review["fingerprint"],
        "expires_at": review["expires_at"],
        "title": review["title"],
        "summary_text": summary.ready_text(review["summary"]),
        "counts": review["summary"].get("counts") or {},
        "muse_line": muse_line,
        "remote_approvals": bool(config["remote_approvals"]),
        "preferred": config["preferred_destinations"],
        "remind_minutes": int(config["remind_minutes"]),
        "clips": clips,
    }


def _sent_offer(db, review_id: int, answer: dict) -> None:
    approval_id = str(answer.get("approval_id") or "")
    try:
        store.move(db, review_id, states.NOTIFICATION_SENT, actor="relay",
                   detail=f"sent as {answer.get('channel') or 'message'}", approval_id=approval_id, error="")
    except states.IllegalMove:
        pass


def thumbnail(entry: dict) -> str:
    """A small JPEG from the middle of the clip, base64, for the approval
    page. "" when ffmpeg can't make one: the page shows the play's name."""
    try:
        from core.binaries import ffmpeg

        with tempfile.TemporaryDirectory(prefix="ck-thumb-") as tmp:
            out = Path(tmp) / "t.jpg"
            subprocess.run(
                [ffmpeg(), "-v", "error", "-y", "-ss", f"{max(0.0, float(entry.get('duration') or 0) / 2):.2f}",
                 "-i", entry["path"], "-frames:v", "1", "-vf", "scale=240:-2", "-q:v", "7", str(out)],
                capture_output=True, timeout=20,
            )
            data = out.read_bytes() if out.exists() else b""
    except Exception:
        return ""
    return base64.b64encode(data).decode("ascii") if 0 < len(data) <= 60_000 else ""


# ---- 4. decisions from the phone --------------------------------------------------------


def poll_commands(db, relay: Relay, data_dir, *, now=None) -> tuple[int, int]:
    device = ra_settings.device(data_dir)
    got = relay.commands()
    if any(c.get("kind") == "approve" for c in got):
        # The relay is the authority on what is connected; read it fresh
        # before checking an approval against it.
        refresh_status(db, relay)
    queued = handled = 0
    for command in got:
        result = commands.handle(db, command, secret=device.get("device_secret", ""), now=now)
        relay.ack(str(command.get("id") or ""), result.outcome, result.reason)
        queued += result.queued
        handled += 1
    return queued, handled


def refresh_status(db, relay: Relay) -> dict:
    status = relay.status()
    ra_settings.save(db, {
        "pairing": status.get("pairing") or {},
        "pin_set": bool(status.get("pin_set")),
        "accounts": status.get("accounts") or {},
        "templates": status.get("templates") or {},
    }, internal=True)
    return status


# ---- actions from Settings --------------------------------------------------------------


def register(db, data_dir, enroll_key: str) -> dict:
    """Make this PC's device secret and register it with the relay, with the
    relay owner's enrolment key. The key is used once and not kept."""
    config = ra_settings.load(db)
    if not config["relay_url"]:
        raise RelayError("bad_request", "Enter your relay's address first.")
    device = ra_settings.device(data_dir) or ra_settings.new_device(data_dir)
    relay = Relay(config["relay_url"], **device)
    try:
        relay.register(enroll_key)
    except RelayError:
        if not ra_settings.device(data_dir):
            ra_settings.forget_device(data_dir)
        raise
    push_preferences(db, relay)
    return refresh_status(db, relay)


def push_preferences(db, relay: Relay) -> None:
    """What the relay needs to know to answer the phone on its own: which
    messages are wanted, whether approving is allowed, the destinations to
    tick in advance, and how long "Remind me later" waits."""
    config = ra_settings.load(db)
    relay.preferences({
        "notify_on_finish": bool(config["notify_on_finish"]),
        "notify_on_fail": bool(config["notify_on_fail"]),
        "notify_on_publish": bool(config["notify_on_publish"]),
        "remote_approvals": bool(config["remote_approvals"]),
        "preferred": list(config["preferred_destinations"]),
        "remind_minutes": int(config["remind_minutes"]),
    })


def can_enable_remote_approval(db) -> str:
    """"" when remote approval may be switched on, else why not."""
    config = ra_settings.load(db)
    if not config["whatsapp_enabled"]:
        return "Turn on WhatsApp notifications first."
    if (config["pairing"] or {}).get("state") != "confirmed":
        return "Pair and confirm your WhatsApp number first."
    if not config["pin_set"]:
        return "Set an approval PIN first. The approval page asks for it before anything is published."
    return ""


def send_test(db) -> bool:
    return store.enqueue(db, f"test:{store.stamp()}:{time.monotonic_ns()}", "test")


def ask_again(db, review_id: int) -> int | None:
    """A new offer for an expired or cancelled one's job, with a new link."""
    from remote_approval import notifier

    old = store.get_review(db, review_id)
    if old is None or old["state"] not in (states.EXPIRED, states.CANCELLED):
        return None
    job = db.get_job(old["job_id"])
    return notifier.offer(db, job_id=old["job_id"], video_id=old["video_id"],
                          title=(job["title"] if job else "") or old["title"],
                          clips=db.clips_for_video(old["video_id"]) if old["video_id"] else [])


def cancel_from_app(db, data_dir, review_id: int) -> dict:
    """Cancel from the PC: always allowed before anything is uploading."""
    review = store.get_review(db, review_id)
    if review is None:
        raise KeyError(review_id)
    if review["state"] == states.PUBLISH_QUEUED:
        commands.cancel_queued(db, review_id)
        if commands.running_jobs(db, review_id):
            raise states.IllegalMove(review["state"], states.CANCELLED)
    review = store.move(db, review_id, states.CANCELLED, actor="desktop", detail="cancelled in the app")
    relay = client(db, data_dir)
    if relay is not None and review["approval_id"]:
        try:
            relay.cancel(review["approval_id"])
        except RelayError as e:
            store.audit(db, review_id, "relay", "cancel_not_delivered", e.message)
    return review


def retry_failed(db, review_id: int) -> int:
    """Queue again only what failed, under the same approval, from the app.
    The person at the PC is already authorised; the phone is not asked."""
    review = store.get_review(db, review_id)
    if review is None or review["state"] not in (states.PARTIAL_FAILURE, states.FAILED):
        return 0
    only: dict[int, list[str]] = {}
    for clip in (review["results"] or {}).get("clips") or []:
        wanted = [p["platform"] for p in clip["posts"] if p["state"] == "failed"]
        if wanted:
            only[clip["clip_id"]] = wanted
    if not only:
        return 0
    queued = commands.queue_publishes(db, review, review["decision"], only=only)
    if queued:
        store.move(db, review_id, states.PUBLISH_QUEUED, actor="desktop", detail=f"retry: {queued} clip(s)")
    return queued

"""Watched channels: a creator posts, Clips Kitty clips it, nobody pastes a link.

This is orchestration only. Detection is sources/channel_feed.py, processing is
the ordinary queue and worker, and publishing is woopsocial_service. What lives
here is the part that joins them without a person in between: noticing a new
video once, waiting until it can be downloaded, queueing it once, and deciding
what happens to its clips.

Built the way server/integrations.py is. Only the detection facts and the
publish decision are stored; once a video has a job, the job row and
clip_publishes are the truth about it, read live, so there is no second copy of
state to drift.

A watched folder on this PC (a Google Drive for desktop, Dropbox or OneDrive
folder a team drops videos into, sources/folder_feed.py) is a watch like a
channel: a file that arrives in it is a new video, copied in and queued the
way a file added by hand is. "Ask me on my phone" hands a watch's clips to
remote approval (remote_approval/) instead of publishing them from here.

Off unless switched on. Nothing in this app starts processing on its own
(core/queue.is_paused), so turning automation on, and each watch, is the
standing go-ahead for exactly those videos and no others.

Supported API, documented in docs/API.md.
"""

import json
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from core import queue
from core.state import StateDB

ENABLED_KEY = "automation_enabled"
DELETE_SOURCES_KEY = "automation_delete_sources"

PLATFORM_NAMES = {"youtube": "YouTube", "twitch": "Twitch", "kick": "Kick"}

WATCH_TICK_SECONDS = 30
DEFAULT_INTERVAL_MINUTES = 15
# A look every few minutes is plenty for videos that take an hour to process,
# and each look is a request to someone else's platform.
MIN_INTERVAL_MINUTES = 5
# How soon to look again at a video that is live, premiering or processing.
RECHECK_SECONDS = 15 * 60
# The same for a watched folder's file that couldn't be read yet. It is on
# this PC's own disk, so a minute is plenty; a channel's video waits on
# someone else's platform.
FOLDER_RECHECK_SECONDS = 60
# How long a finished video of a phone watch waits for its offer before the
# Watch page says nothing was sent. The worker makes the offer just after it
# marks the job done (server/jobs.py _announce), after any webhook the job
# carries has answered, so a look in between finds none yet.
OFFER_WAIT_SECONDS = 2 * 60
# A premiere can be scheduled days ahead; past a week it is not coming.
GIVE_UP_SECONDS = 7 * 24 * 60 * 60
# The "last 24 hours" catch-up policy.
DAY_SECONDS = 24 * 60 * 60

BACKLOG = ("all", "newest", "day", "none")

# How many recent steps the page's live panel can show.
ACTIVITY_KEEP = 30

# A hands-off channel (publishing set to Automatic) runs on a PC nobody is
# watching, so a failure that might pass is tried again instead of waiting for
# a person. Each list is the wait before each successive try; when it runs out,
# the video stays as it is with the reason showing.
#   - A download or processing run that failed: a dropped connection, a 403
#     from YouTube. Twice, half an hour and then three hours later.
PROCESS_RETRY_DELAYS = (30 * 60, 3 * 60 * 60)
#   - A publish that could not start because WoopSocial was unreachable, busy
#     or rate limiting. Every 15 minutes, six times, then ask.
PUBLISH_RETRY_SECONDS = 15 * 60
PUBLISH_START_ATTEMPTS = 6
#   - Posts the platform rejected, typically a daily cap that other posts
#     already used. Only the rejected ones are sent again, 6 and 24 hours on.
DELIVERY_RETRY_DELAYS = (6 * 60 * 60, 24 * 60 * 60)

_MISSED = "Posted while Clips Kitty wasn't watching."
_BASELINE = "Posted before you started watching this channel."
_MISSED_FILE = "Added while Clips Kitty wasn't watching."
_BASELINE_FILE = "In the folder before you started watching it."
# True on WhatsApp (a message was sent) and with Muse alone (nothing is sent:
# the creator's Muse agent finds the offer when asked).
_READY_ON_PHONE = "Ready to approve from your phone."
_NOT_SENT = "Nothing was sent to your phone for this video."

_JOB_STATES = {
    "queued": "queued",
    "running": "processing",
    "done": "complete",
    "failed": "failed",
    "cancelled": "cancelled",
}


# ---- request bodies ---------------------------------------------------------


class PublishSettings(BaseModel):
    """What to do with a watched video's clips. Configured once per channel."""

    # "phone": offered on the phone when the clips are made (remote_approval/)
    # and posted from there with the creator's PIN. Nothing goes through
    # WoopSocial, and the rest of these settings are not used for it.
    mode: Literal["off", "ask", "auto", "phone"] = "ask"
    platforms: list[str] = []
    # How many of each video's clips to post, best first. 0 posts every clip
    # the run makes; the rest stay in the library either way.
    max_posts: int = Field(default=0, ge=0, le=100)
    # False: post each clip the moment it is made, as the Publish dialog does
    # with "Space them out" unticked. True spreads them on the budget below.
    spread: bool = True
    # A daily budget, not a flat gap: WoopSocial allows about five YouTube posts
    # a day, and a burst past that is rejected (see woopsocial_service).
    per_day: int = Field(default=5, ge=1, le=50)
    gap_hours: float = Field(default=1, ge=0.25, le=24)
    # Local "HH:MM" for each day's first post, or "" to start as soon as the
    # scheduler allows.
    day_start: str = Field(default="", pattern=r"^$|^([01]\d|2[0-3]):[0-5]\d$")
    # The creator's own, set once ("#creatorname #twitch"). They lead every
    # caption so nothing trims them off.
    hashtags: list[str] = []
    # False: only the hashtags above, none of the ones the model chose.
    ai_hashtags: bool = True
    footer: str = Field(default="", max_length=1000)
    # Per platform, the fields WoopSocial's build_post reads: youtube.privacy,
    # tiktok.privacyLevel / allowComment / allowDuet / allowStitch /
    # isYourBrand / isBrandedContent, instagram|facebook.postType,
    # pinterest.pinterestBoardId.
    overrides: dict = {}
    discord_destination_id: int | None = Field(default=None, ge=1)


class AutomationPatch(BaseModel):
    enabled: bool | None = None
    # Delete a watched video's download once its clips are published, so an
    # always-on PC does not fill its disk with multi-gigabyte sources.
    delete_sources: bool | None = None


class WatchIn(BaseModel):
    platform: Literal["youtube", "twitch", "kick", "folder"]
    # A channel's link or name, or for a folder the path the folder picker gave.
    channel: str = Field(min_length=1, max_length=300)
    # What happens to its clips, chosen when the channel is added, so a
    # hands-off channel is one step to set up rather than two.
    publish: PublishSettings | None = None
    # How its clips are made, as the Generate bar sends them (captions,
    # podcast, caption style...). Without these a watched video got the app's
    # defaults, captions and all, whatever the person had chosen.
    options: dict | None = None
    preset: str | None = None


class WatchPatch(BaseModel):
    enabled: bool | None = None
    name: str | None = Field(default=None, max_length=100)
    preset: str | None = None
    options: dict | None = None
    publish: PublishSettings | None = None
    backlog: Literal["all", "newest", "day", "none"] | None = None
    min_minutes: float | None = Field(default=None, ge=0, le=600)


# ---- reading state ----------------------------------------------------------


def is_enabled(d: StateDB) -> bool:
    return d.get_flag(ENABLED_KEY, "0") == "1"


def recheck_seconds(platform: str) -> float:
    """How soon a video that isn't ready yet is looked at again."""
    return FOLDER_RECHECK_SECONDS if platform == "folder" else RECHECK_SECONDS


def publish_settings(watch) -> PublishSettings:
    try:
        return PublishSettings(**json.loads(watch["publish"] or "{}"))
    except Exception:
        return PublishSettings()


def phone_problem(d: StateDB, *, offered: bool = False) -> str:
    """Why a watch set to Ask me on my phone can't be approved from the phone,
    named as Settings names its switches, or "" when it can. `offered` is
    whether this video's clips really were offered when its job finished
    (remote_approval/notifier.py): what was offered then is the truth,
    whatever the switches say now, so the Watch page never says clips are
    waiting when nothing was offered, nor the other way round. An offer
    reaches the phone on WhatsApp or through the creator's Muse agent, the
    same two ways the notifier checks."""
    from remote_approval import settings as ra_settings

    s = ra_settings.load(d)
    where = "in Settings → Notifications & Remote Approval"
    if not offered:
        if not (s["whatsapp_enabled"] or ra_settings.muse_connected(s)):
            return ("Nothing was sent to your phone. Connect your Muse agent, or set up WhatsApp and "
                    f"turn on Send me WhatsApp messages {where}.")
        if not s["notify_on_finish"]:
            return f"Nothing was sent to your phone. Turn on When highlights are ready {where}."
        return ""
    if not s["remote_approvals"]:
        return ("These clips were offered, but can't be approved from your phone yet. Turn on Let me "
                f"approve publishing from my phone {where}.")
    return ""


def _validate_discord_destination(d: StateDB, settings: PublishSettings) -> None:
    from campaigns import post_links

    try:
        post_links.validate_destination(d, settings.discord_destination_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def watch_options(watch) -> dict:
    try:
        options = json.loads(watch["options"] or "{}")
    except ValueError:
        options = {}
    return options if isinstance(options, dict) else {}


def job_payload(watch, url: str, origin: str = "watch") -> dict:
    """What the queue is given for one of this watch's videos: the preset, then
    the watch's own options on top, exactly as a pasted link would carry them.
    `origin` is "watch" for a video the watch queued by itself, "manual" for one
    a person pressed Clip this on: a signed-in AI plan only runs unattended jobs
    when the user has allowed it (llm/signin/)."""
    from server.integrations import PRESETS

    options = watch_options(watch)
    preset = PRESETS.get(options.pop("preset", "standard")) or PRESETS["standard"]
    payload = {"url": url, **preset["options"], **options, "origin": origin}
    if payload.get("gaming") and any(payload.get(k) for k in ("vertical_live", "podcast", "longform")):
        # The watch settings never offer Gaming with these; a watch that has
        # both anyway (made by hand, or by an older version) keeps the other
        # mode rather than being refused on every video it finds.
        payload.pop("gaming")
    if payload.get("gaming_scoring") and any(payload.get(k) for k in ("podcast", "longform")):
        payload.pop("gaming_scoring")  # the same, for gaming stream scoring
    if payload.get("sport") and any(payload.get(k) for k in ("gaming", "gaming_scoring", "podcast")):
        payload.pop("sport")  # and a sport set beside a mode it can't share
    if payload.get("pipeline") and any(payload.get(k) for k in ("sport", "gaming_scoring", "longform")):
        payload.pop("pipeline")  # and a plugin pipeline beside a mode that picks moments its own way
    if payload.get("longform"):
        # Rate & understand work on a Shorts run's moments, and Suggest edits
        # on its clips; Longform has no such step: they stay in the options
        # for a preset that has one.
        payload.pop("rate", None)
        payload.pop("understand", None)
        payload.pop("edit", None)
    return payload


def render_footer(template: str, item, watch) -> str:
    """Fill the source placeholders. Plain replacement, not str.format, so any
    other braces a person typed stay exactly as they typed them."""
    if watch["platform"] == "folder":
        # A folder's video is a file on this PC. Its path names the folder's
        # owner and no viewer could open it, so it has no link to give and no
        # platform, and the folder is called by its watch's name, never its path.
        values = {"{source_url}": "", "{source_title}": item["title"] or "",
                  "{source_channel}": watch["name"] or "", "{source_platform}": ""}
    else:
        values = {
            "{source_url}": item["url"] or "",
            "{source_title}": item["title"] or "",
            "{source_channel}": watch["name"] or watch["channel_key"],
            "{source_platform}": PLATFORM_NAMES.get(watch["platform"], watch["platform"]),
        }
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    return template.strip()


def _job_status(d: StateDB, item) -> str | None:
    """The item's job status, or None when it has no job. A job row cleared
    from the queue's history still counts as done if its video finished."""
    if not item["job_id"]:
        return None
    job = d.get_job(item["job_id"])
    if job is not None:
        return job["status"]
    return "done" if d.video_status(item["video_id"]) == "done" else "cancelled"


def _steps_failed(d: StateDB, item, watch) -> list[str]:
    """The names of the plugins this item's job chose to rate or understand
    its moments (plugins/steps.py) that didn't run, from the video's outcome.
    [] for a job that chose none, whatever an earlier run of the video left
    in the outcome: only a job that named a step is held for one."""
    job = d.get_job(item["job_id"]) if item["job_id"] else None
    payload: dict = {}
    if job is not None:
        try:
            payload = json.loads(job["payload"] or "{}")
        except (TypeError, ValueError):
            payload = {}
    else:
        payload = job_payload(watch, item["url"])  # the job row was cleared from the history
    if not isinstance(payload, dict) or not (payload.get("rate") or payload.get("understand")):
        return []
    from core.outcome import failed_steps

    return failed_steps(d.get_outcome(item["video_id"]))


def _window(clip) -> tuple[float, float]:
    """A clip's lasting identity, its window in the video: a re-render or a
    forced re-run gives it a new score or id, never a new window."""
    return round(float(clip["start_s"]), 2), round(float(clip["end_s"]), 2)


def _chosen_windows(item) -> set[tuple[float, float]] | None:
    """The windows of the clips the item's first publish chose, or None when
    none were kept yet (before its first publish, and for an item published
    before they were kept): then the best N are chosen now."""
    try:
        saved = json.loads(item["chosen_clips"] or "null")
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    if not isinstance(saved, list):
        return None
    try:
        return {(round(float(start), 2), round(float(end), 2)) for start, end in saved}
    except (TypeError, ValueError):
        return None


def _no_clips_error(outcome: dict) -> str:
    """Why a finished video had no clips to publish, for the item."""
    from core.outcome import rated_out_names

    names = rated_out_names(outcome) if (outcome or {}).get("cause") == "rated_out" else []
    if names:
        return (f"{', '.join(names)} rated every moment under the minimum score ({outcome.get('min_score')}), "
                "so there were no clips to publish.")
    return "The run made no clips to publish."


def _deliveries(d: StateDB, video_id: str) -> list[dict]:
    rows = d.conn.execute(
        "SELECT p.clip_id, p.platform, p.state, p.post_url, p.scheduled_for, p.error "
        "FROM clip_publishes p WHERE p.video_id = ? ORDER BY p.clip_id, p.platform",
        (video_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def view_item(d: StateDB, item, worker) -> dict:
    out = {
        key: item[key]
        for key in (
            "id", "watch_id", "platform", "video_id", "url", "title", "published_at",
            "detected_at", "state", "reason", "job_id", "publish_state", "publish_error",
            "retries", "retry_at", "publish_attempts", "publish_retry_at", "delivery_retries",
            "source_freed",
        )
    }
    status = {
        "baseline": "earlier",
        "new": "waiting_for_video",
        "not_ready": "waiting_for_video",
        "waiting": "waiting_for_queue",
        "skipped": "skipped",
        "error": "error",
    }.get(item["state"], "queued")
    job_status = _job_status(d, item) if item["state"] == "queued" else None
    if job_status is not None:
        status = _JOB_STATES.get(job_status, "queued")
        if job_status == "queued":
            out["waiting_behind"] = queue.waiting_ahead(d, item["job_id"])
            out["queue_paused"] = queue.is_paused(d)
        elif job_status == "running":
            out["progress"] = worker.progress_snapshot(item["job_id"])
        elif job_status == "failed":
            job = d.get_job(item["job_id"])
            out["details"] = ((job["error"] if job is not None else "") or "")[:500]
    out["status"] = status
    if status == "complete":
        out["clips"] = d.conn.execute(
            "SELECT COUNT(*) FROM clips WHERE video_id = ?", (item["video_id"],)
        ).fetchone()[0]
        out["deliveries"] = _deliveries(d, item["video_id"])
    return out


def view_watch(d: StateDB, watch) -> dict:
    counts = {
        r["state"]: r["n"]
        for r in d.conn.execute(
            "SELECT state, COUNT(*) AS n FROM watch_items WHERE watch_id = ? GROUP BY state",
            (watch["id"],),
        )
    }
    options = watch_options(watch)
    return {
        "id": watch["id"],
        "platform": watch["platform"],
        "channel_key": watch["channel_key"],
        "name": watch["name"],
        "enabled": bool(watch["enabled"]),
        "preset": options.pop("preset", "standard"),
        "options": options,
        "publish": publish_settings(watch).model_dump(),
        "backlog": watch["backlog"],
        "min_minutes": watch["min_minutes"],
        "last_ok_poll_at": watch["last_ok_poll_at"],
        "next_poll_at": watch["next_poll_at"],
        "last_error": watch["last_error"],
        "counts": counts,
        "creator": creator_summary(d, watch),
    }


# ---- the creator it learns about --------------------------------------------


def creator_name(watch) -> str:
    """The channel name a watch's creator profile goes by, or "" when there
    is no real one. A YouTube channel whose name could not be read is known
    only by its UC id, and a profile called that would help nobody; nor
    would one called after a folder's path."""
    name = (watch["name"] or "").strip()
    if watch["platform"] in ("youtube", "folder") and name == watch["channel_key"]:
        return ""
    return name


def creator_platform(watch) -> str:
    """The platform a watch's creator profile is filed under. A folder's
    videos are files from this PC, filed the way /videos/local files one
    when no platform is chosen: under YouTube, one the Creators tab knows."""
    return "youtube" if watch["platform"] == "folder" else watch["platform"]


def watch_creator(d: StateDB, watch, *, create: bool = False) -> int | None:
    """The creator profile a watch's videos learn into.

    Found the way the pipeline finds one, by platform and channel name, so a
    video of the channel clipped by hand lands on the same profile. Not
    stored on the watch, so merging or splitting profiles in the Creators tab
    is followed. `create` makes it on first sight, which is how a channel is
    in Creators from the moment it is watched."""
    name = creator_name(watch)
    if not name:
        return None
    if create:
        from creator import identity

        return identity.resolve(d, "", name, platform=creator_platform(watch))
    row = d.conn.execute(
        "SELECT creator_id FROM platform_accounts"
        " WHERE platform = ? AND platform_account_id = ? COLLATE NOCASE"
        " ORDER BY platform_account_id = ? DESC LIMIT 1",
        (creator_platform(watch), name, name),
    ).fetchone()
    return row["creator_id"] if row else None


def creator_summary(d: StateDB, watch) -> dict | None:
    """What the watch card shows about its creator: who, and how much the
    app has learned about them so far."""
    creator_id = watch_creator(d, watch)
    if creator_id is None:
        return None
    row = d.conn.execute(
        "SELECT display_name, learning_enabled FROM creators WHERE creator_id = ?",
        (creator_id,),
    ).fetchone()
    if row is None:
        return None
    def count(sql: str) -> int:
        return d.conn.execute(sql, (creator_id,)).fetchone()[0]

    return {
        "id": creator_id,
        "name": row["display_name"],
        "learning": bool(row["learning_enabled"]),
        "videos": count("SELECT COUNT(*) FROM videos WHERE creator_id = ? AND status = 'done'"),
        "facts": count("SELECT COUNT(*) FROM creator_knowledge WHERE creator_id = ?")
        + count("SELECT COUNT(*) FROM creator_events WHERE creator_id = ?"),
    }


def learned_from(d: StateDB, video_id: str) -> tuple[str, int] | None:
    """The creator a video was learned into, and how many new facts and
    storyline events it gave: (name, count), or None when it has no creator."""
    row = d.conn.execute(
        "SELECT c.creator_id, c.display_name FROM videos v"
        " JOIN creators c ON c.creator_id = v.creator_id WHERE v.video_id = ?",
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    n = sum(
        d.conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE creator_id = ? AND source_video = ?",
            (row["creator_id"], video_id),
        ).fetchone()[0]
        for table in ("creator_knowledge", "creator_events")
    )
    return row["display_name"], n


# ---- the watcher ------------------------------------------------------------


class ChannelWatcher(threading.Thread):
    """Looks at each watched channel every few minutes and moves what it finds
    along: detected, ready, queued, published or waiting to be asked.

    Every step reads its state from the database and writes it back before the
    next, so a restart at any point carries on from where it stopped. A video is
    one row keyed by its id, which is what keeps it to one job however many
    times, or by however many watches, it is seen."""

    def __init__(
        self,
        db: Callable[[], StateDB],
        *,
        feed,
        worker,
        broadcaster,
        publisher: Callable[..., dict] | None = None,
        interval_minutes: float = DEFAULT_INTERVAL_MINUTES,
        downloads=None,
        clock=time.time,
        folders=None,
    ):
        super().__init__(daemon=True, name="channel-watcher")
        self._downloads = downloads
        self._db = db
        self._feed = feed
        if folders is None:
            from sources.folder_feed import FolderFeed

            folders = FolderFeed(clock=clock)
        # Watched folders (sources/folder_feed.py): the feed for platform
        # "folder", with the channel_feed answers and the copy into downloads/.
        self._folders = folders
        # Per folder watch, the files that were in it when watching resumed
        # after a gap: they arrived unwatched, which the catch-up rule decides
        # once each has finished arriving. In memory, like the folder feed's
        # own record of each file.
        self._arrived_unwatched: dict[int, set[str]] = {}
        # When each folder watch last said it found nothing new.
        self._quiet_said: dict[int, float] = {}
        # Per finished video of a phone watch with no offer found yet, when
        # that was first seen (_to_phone).
        self._awaiting_offer: dict[int, float] = {}
        self._worker = worker
        self._broadcaster = broadcaster
        self._publisher = publisher
        self._interval = max(MIN_INTERVAL_MINUTES, float(interval_minutes)) * 60
        self._clock = clock
        self._wake = threading.Event()
        self._stop = threading.Event()
        # What it is doing right now, and the last few things it did, for the
        # page's live panel. In memory only: it is a window onto the work, and
        # the work itself is all in the database.
        self._activity: deque[dict] = deque(maxlen=ACTIVITY_KEEP)
        self._activity_lock = threading.Lock()
        self._doing = ""
        self._published_seen: set[tuple] | None = None

    @property
    def interval_seconds(self) -> float:
        return self._interval

    @property
    def folders(self):
        """The feed for watched folders, which the route adding one asks too."""
        return self._folders

    def readiness(self, item, min_seconds: float):
        """Whether a found video can be clipped now, asked of whatever found
        it: a watched folder's file of the folder feed, anything else of the
        channel feed."""
        if item["platform"] == "folder":
            return self._folders.readiness(item["url"], min_seconds, video_id=item["video_id"])
        return self._feed.readiness(item["url"], min_seconds)

    def activity(self) -> dict:
        with self._activity_lock:
            return {"doing": self._doing, "events": list(self._activity)}

    def _say(self, text: str, kind: str = "info", url: str = "") -> None:
        """One step worth seeing: in the page's live panel, and the log. `url`
        is the video found, or the post that went live, so the panel can link
        straight to it."""
        event = {"at": self._clock(), "text": text, "kind": kind}
        if url:
            event["url"] = url
        with self._activity_lock:
            self._activity.appendleft(event)
        print(f"Watch: {text}")
        self._broadcaster.publish({"type": "automation", "activity": event})

    @contextmanager
    def _busy(self, text: str):
        """What it is doing for as long as it takes, shown live on the page."""
        with self._activity_lock:
            self._doing = text
        self._broadcaster.publish({"type": "automation", "doing": text})
        try:
            yield
        finally:
            with self._activity_lock:
                self._doing = ""
            self._broadcaster.publish({"type": "automation", "doing": ""})

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                traceback.print_exc()  # the watcher must outlive any one failure
            self._wake.wait(timeout=WATCH_TICK_SECONDS)
            self._wake.clear()

    def tick(self) -> None:
        now = self._clock()
        d = self._db()
        try:
            if not is_enabled(d):
                # Watching is off, but a publish someone asked for, or one a
                # restart interrupted, still goes out.
                self._decide_publishing(d, new=False)
                self._attach_discord_links(d, now)
                return
            for watch in d.watches_due(now):
                self._poll(d, watch, now)
            self._check_readiness(d, now)
            self._queue_waiting(d)
            self._retry_failed_runs(d, now)
            self._retry_rejected_posts(d, now)
            self._decide_publishing(d)
            self._attach_discord_links(d, now)
            self._free_disk(d)
            self._notice_posts(d)
        finally:
            d.close()

    def _attach_discord_links(self, d: StateDB, now: float) -> None:
        """Recover attachments from saved posts, including interrupted publishes."""
        from campaigns import post_links
        from campaigns import queue as campaign_queue

        for watch in d.list_watches():
            destination_id = publish_settings(watch).discord_destination_id
            if destination_id is None or d.get_destination(destination_id) is None:
                continue
            items = d.conn.execute(
                "SELECT * FROM watch_items WHERE watch_id = ? AND state = 'queued' "
                "AND (publish_state IN ('publishing', 'done') OR chosen_clips != '') "
                "AND EXISTS (SELECT 1 FROM clip_publishes WHERE video_id = watch_items.video_id)",
                (watch["id"],),
            ).fetchall()
            if not items:
                continue
            with d.transaction():
                campaign_id = post_links.campaign_for_destination(d, destination_id)
                for item in items:
                    campaign_queue.attach_for_watch(
                        d, watch=watch, item=item, campaign_ids=[campaign_id], now=now,
                    )

    # -- 1. look at the channel -----------------------------------------------

    def _poll(self, d: StateDB, watch, now: float) -> None:
        if watch["platform"] == "folder":
            self._poll_folder(d, watch, now)
            return
        name = watch["name"] or watch["channel_key"]
        try:
            with self._busy(f"Checking {name} for new videos"):
                videos = self._feed.latest(watch["platform"], watch["channel_key"])
        except Exception as e:
            self._say(f"Couldn't check {name}: {_short(e)}", "error")
            d.set_watch(watch["id"], last_error=_short(e), next_poll_at=now + self._interval)
            return
        # Shorts are not watched at all: nothing to clip, so not worth a row.
        videos = [v for v in videos if not getattr(v, "short", False)]
        known = d.watch_item_ids()
        fresh, seen = [], set()
        for video in videos:
            if video.video_id and video.video_id not in known and video.video_id not in seen:
                fresh.append(video)
                seen.add(video.video_id)

        if not watch["last_ok_poll_at"]:
            # The first look records what is already there, so adding a channel
            # never queues its back catalogue. Listed, so any of it can still be
            # clipped with one click.
            for video in fresh:
                self._insert(d, watch, video, now, "baseline", _BASELINE)
            self._say(f"Now watching {name}. {len(fresh)} earlier videos noted, none clipped.",
                      "info")
        else:
            take, hold = fresh, []
            missed = now - watch["last_ok_poll_at"] > 2 * self._interval
            if missed and len(fresh) > 1:
                take, hold = self._catch_up(watch["backlog"], fresh, now)
            for video in take:
                self._insert(d, watch, video, now, "new", "")
                self._say(f"New video on {name}: {_quoted(video.title)}", "found", video.url)
            for video in hold:
                self._insert(d, watch, video, now, "skipped", _MISSED)
            if hold:
                self._say(f"{len(hold)} older video(s) on {name} were posted while Clips "
                          "Kitty wasn't watching. Set aside, one click to clip.", "info")
            if not fresh:
                # Worth a line too: a panel that only speaks when something is
                # found looks, for hours at a time, exactly like one that died.
                self._say(f"Checked {name}: nothing new", "info")
        d.set_watch(watch["id"], last_ok_poll_at=now, next_poll_at=now + self._interval,
                    last_error="")
        if fresh:
            self._broadcaster.publish({"type": "automation"})

    def _poll_folder(self, d: StateDB, watch, now: float) -> None:
        """A watched folder (sources/folder_feed.py), looked at on every tick:
        a look is only a listing of this PC's own disk, and a video a team
        drops in shouldn't wait a quarter of an hour.

        The same steps as a channel's, with one difference: a file is found
        once it has finished arriving, a minute or more after it appears. So
        the first look notes every file already there without waiting (one
        still being copied then is a different file once it has finished, by
        its size and time, and counts as new), and when watching resumes
        after a gap, what is in the folder at that moment is remembered as
        having arrived unwatched, for the catch-up rule to take once it has
        finished."""
        name = watch["name"] or "the folder"
        try:
            present, settled = self._folders.look(watch["channel_key"])
        except Exception as e:
            if watch["last_error"] != _short(e):  # said once, not on every tick
                self._say(f"Couldn't check {name}: {_short(e)}", "error")
            d.set_watch(watch["id"], last_error=_short(e), next_poll_at=now + WATCH_TICK_SECONDS)
            return
        known = d.watch_item_ids()
        if not watch["last_ok_poll_at"]:
            fresh = _unseen(present, known)
            for video in fresh:
                self._insert(d, watch, video, now, "baseline", _BASELINE_FILE)
            self._say(f"Now watching {name}. {len(fresh)} earlier videos noted, none clipped.", "info")
        else:
            unwatched = self._arrived_unwatched.setdefault(watch["id"], set())
            if now - watch["last_ok_poll_at"] > 2 * self._interval:
                unwatched.update(v.url for v in present if v.video_id not in known)
            # One taken away before it finished is forgotten, so a file put
            # there later under the same name is not taken for it.
            unwatched.intersection_update(v.url for v in present)
            fresh = _unseen(settled, known)
            late = [v for v in fresh if v.url in unwatched]
            take, hold = [v for v in fresh if v.url not in unwatched], []
            if len(late) > 1:
                caught, hold = self._catch_up(watch["backlog"], late, now)
                take += caught
            else:
                take += late
            unwatched.difference_update(v.url for v in fresh)
            if not unwatched:
                self._arrived_unwatched.pop(watch["id"], None)
            for video in take:
                self._insert(d, watch, video, now, "new", "")
                # No link: the file is on this PC, and the panel opens links in a browser.
                self._say(f"New video in {name}: {_quoted(video.title)}", "found")
            for video in hold:
                self._insert(d, watch, video, now, "skipped", _MISSED_FILE)
            if hold:
                self._say(f"{len(hold)} older video(s) in {name} were added while Clips Kitty "
                          "wasn't watching. Set aside, one click to clip.", "info")
            arriving = any(v.video_id not in known for v in present)
            if not fresh and not arriving and now - self._quiet_said.get(watch["id"], 0) >= self._interval:
                # Worth a line now and then, as for a channel, but not every
                # thirty seconds: the panel only keeps its last few.
                self._quiet_said[watch["id"]] = now
                self._say(f"Checked {name}: nothing new", "info")
        d.set_watch(watch["id"], last_ok_poll_at=now, next_poll_at=now + WATCH_TICK_SECONDS,
                    last_error="")
        if fresh:
            self._broadcaster.publish({"type": "automation"})

    def _insert(self, d: StateDB, watch, video, now: float, state: str, reason: str) -> None:
        d.insert_watch_item(
            video.video_id, watch_id=watch["id"], platform=watch["platform"], url=video.url,
            title=video.title, published_at=video.published_at, detected_at=now, state=state,
            reason=reason, next_check_at=now,
        )

    def _catch_up(self, policy: str, fresh: list, now: float) -> tuple[list, list]:
        """Several videos appeared while nobody was watching. Which to clip.

        Newest first throughout, because every listing is. Nothing is dropped:
        what is not taken is recorded as skipped, visible, one click from being
        clipped after all."""
        if policy == "all":
            return fresh, []
        if policy == "none":
            return [], fresh
        if policy == "day":
            take = []
            for index, video in enumerate(fresh):
                # A folder's file is dated by its last change, already known.
                if not video.published_at and video.platform != "folder":
                    # Twitch listings carry no dates; the video's own page does.
                    video.published_at = self._feed.readiness(video.url, 0).published_at
                if video.published_at and now - video.published_at > DAY_SECONDS:
                    return take, fresh[index:]
                take.append(video)
            return take, []
        return fresh[:1], fresh[1:]  # "newest", the default

    # -- 2. wait until it can be downloaded -----------------------------------

    def _check_readiness(self, d: StateDB, now: float) -> None:
        for item in reversed(d.watch_items(states=("new", "not_ready"))):
            if item["next_check_at"] > now:
                continue
            watch = d.get_watch(item["watch_id"])
            if watch is None or not watch["enabled"]:
                continue
            if item["state"] == "not_ready" and now - item["detected_at"] > GIVE_UP_SECONDS:
                d.set_watch_item(item["id"], state="skipped",
                                 reason="It never became available to download.")
                continue
            with self._busy(f"Checking whether {_quoted(item['title'])} is ready"):
                found = self.readiness(item, float(watch["min_minutes"] or 0) * 60)
            facts = {}
            if found.title and not item["title"]:
                facts["title"] = found.title
            if found.published_at and not item["published_at"]:
                facts["published_at"] = found.published_at
            if getattr(found, "orientation", ""):
                facts["orientation"] = found.orientation
            state, reason = found.state, found.reason
            if (state == "ready" and getattr(found, "orientation", "") == "horizontal"
                    and watch_options(watch).get("vertical_live")):
                # A Vertical Live watch clips vertical versions only. A video
                # that only comes landscape (the horizontal twin of a
                # Streamlabs "-vert" pair, say) is set aside, not clipped the
                # wrong way. Clip this still offers it.
                state, reason = "skip", "No vertical version (this channel is set to Vertical Live)."
            if state == "ready":
                d.set_watch_item(item["id"], state="waiting", reason="", **facts)
            elif state == "not_yet":
                folder = item["platform"] == "folder"
                if item["state"] == "new":
                    self._say(f"{_quoted(item['title'] or found.title)} isn't ready yet. "
                              + ("Looking again in a minute." if folder else "Looking again in 15 minutes."),
                              "waiting")
                d.set_watch_item(item["id"], state="not_ready", reason=reason,
                                 next_check_at=now + recheck_seconds(item["platform"]), **facts)
            else:
                self._say(f"Skipped {_quoted(item['title'] or found.title)}: {reason}",
                          "info")
                d.set_watch_item(item["id"], state="skipped", reason=reason, **facts)

    # -- 3. queue it, once ------------------------------------------------------

    def _queue_waiting(self, d: StateDB) -> None:
        queued_any = False
        for item in reversed(d.watch_items(states=("waiting",))):
            watch = d.get_watch(item["watch_id"])
            if watch is None or not watch["enabled"]:
                continue
            title = item["title"] or f"{watch['name'] or watch['channel_key']} video"
            url = item["url"]
            if item["platform"] == "folder":
                # Copied in first, as a file added by hand is, and queued by
                # its id: the job reads the copy, never the folder.
                if not self._bring_in(d, item, title):
                    continue
                url = f"local:{item['video_id']}"
            payload = job_payload(watch, url, "manual" if item["requested"] else "watch")
            if item["orientation"] == "horizontal":
                # Only reachable through Clip this (a Vertical Live watch skips
                # these): the person asked for this landscape video, so it is
                # clipped the standard way rather than refused.
                payload.pop("vertical_live", None)
            outcome, job_id = queue.enqueue_once(
                d, item["video_id"],
                payload,
                title=title,
            )
            if outcome == "done":
                d.set_watch_item(item["id"], state="skipped",
                                 reason="Already clipped in Clips Kitty.")
                continue
            if outcome == "full":
                full = f"Waiting for room in the queue ({queue.MAX_ACTIVE} videos at most)."
                if item["reason"] != full:
                    self._say(f"{_quoted(title)} is waiting for room in the queue.", "waiting")
                d.set_watch_item(item["id"], reason=full)
                break  # nothing else fits either
            self._tag_creator(d, watch, item["video_id"], title)
            # The watch is the user's go-ahead for its own videos, not for
            # anything else they staged and have not started.
            queue.start_if_alone(d, job_id)
            d.set_watch_item(item["id"], state="queued", job_id=job_id, reason="")
            # A folder's file has no link: its url is its path on this PC.
            self._say(f"Queued {_quoted(title)} for clipping", "queued",
                      "" if item["platform"] == "folder" else item["url"])
            queued_any = True
        if queued_any:
            self._worker.notify()
            self._broadcaster.publish({"type": "queue"})
            self._broadcaster.publish({"type": "automation"})

    def _bring_in(self, d: StateDB, item, title: str) -> bool:
        """Copy a watched folder's file into downloads/ before its video is
        queued (sources/folder_feed.py, sources/local_file.py), the copy its
        job reads as local:<id>. Nothing to copy when the video is clipped
        already, already has a job, or the queue is full: enqueue_once says
        which, as for any video. False when the file couldn't be brought in,
        with the reason on the item."""
        vid = item["video_id"]
        if d.video_status(vid) == "done" or queue.duplicate_of(d, vid) is not None or queue.capacity(d) <= 0:
            return True
        if self._downloads is None:
            d.set_watch_item(item["id"], state="error", reason="Clips Kitty has no downloads folder to copy it to.")
            return False
        from sources.local_file import NotImported

        try:
            with self._busy(f"Copying {_quoted(title)} into Clips Kitty"):
                imported = self._folders.bring_in(item["url"], vid, self._downloads)
        except NotImported as e:
            d.set_watch_item(item["id"], state="error", reason=_short(e))
            self._say(f"Couldn't clip {_quoted(title)}: {_short(e)}", "error")
            return False
        except ValueError as e:  # gone from the folder, or changed since it was found
            d.set_watch_item(item["id"], state="skipped", reason=_short(e))
            self._say(f"Skipped {_quoted(title)}: {_short(e)}", "info")
            return False
        except OSError as e:
            # FFmpeg missing, or the disk refused the copy. Said on the item,
            # where Clip this tries again, rather than raised out of the tick,
            # which would stop every other watch at this file each time.
            # strerror, because the whole message names the file's path.
            reason = f"Couldn't copy it into Clips Kitty: {e.strerror or type(e).__name__}"
            d.set_watch_item(item["id"], state="error", reason=reason)
            self._say(f"Couldn't clip {_quoted(title)}: {reason}", "error")
            return False
        # Its length, which the queue's time estimate scales by, as /videos/local records it.
        d.upsert_video(vid, title=title, duration=imported.seconds)
        return True

    def _tag_creator(self, d: StateDB, watch, video_id: str, title: str) -> None:
        """Put the video on the watch's creator before it runs, the way the
        upload form does. The pipeline keeps a creator already set, so what it
        learns lands on this profile whatever the download calls the channel.
        Never in the way of queueing: without it the pipeline tags as before."""
        name = creator_name(watch)
        if not name:
            return
        try:
            from creator import identity

            d.upsert_video(video_id, title=title, channel_name=name)
            identity.tag_video(d, video_id, name, platform=creator_platform(watch))
        except Exception as e:
            print(f"Watch: couldn't link {video_id} to its creator: {e}")

    # -- 4. decide what happens to the clips ----------------------------------

    def _decide_publishing(self, d: StateDB, *, new: bool = True) -> None:
        """Once a video's clips exist: publish, ask, or leave them be.

        "publishing" is also what an approval sets and what a restart finds
        half done, and both simply run again: publish_clips(once=True) skips
        every clip already sent or on its way, so a repeat sends only what is
        missing or failed."""
        now = self._clock()
        for item in d.watch_items(states=("queued",)):
            if item["publish_state"] not in ("", "publishing"):
                continue
            if item["publish_state"] == "" and not new:
                continue
            if item["publish_state"] == "publishing" and item["publish_retry_at"] > now:
                continue  # waiting out a failed start before trying again
            if _job_status(d, item) != "done":
                continue
            watch = d.get_watch(item["watch_id"])
            if watch is None:
                continue
            if item["publish_state"] == "":
                learned = learned_from(d, item["video_id"])
                if learned and learned[1]:
                    name, n = learned
                    self._say(f"Learned {n} new thing{'' if n == 1 else 's'} about {name} "
                              f"from {_quoted(item['title'])}", "learned")
            mode = publish_settings(watch).mode
            if mode == "phone":
                # Offered on the phone when the job finished, and posted from
                # there: never through WoopSocial, whatever an earlier mode or
                # a restart left on the item.
                if self._to_phone(d, item, watch, now):
                    self._broadcaster.publish({"type": "automation"})
                continue
            # A plugin this job chose to rate or understand the moments didn't
            # run: the clips were made without it, so a person looks first.
            failed = (_steps_failed(d, item, watch)
                      if item["publish_state"] == "" and mode in ("auto", "ask") else [])
            names = ", ".join(failed)
            if failed and mode == "auto":
                d.set_watch_item(
                    item["id"], publish_state="ask",
                    publish_error=f"Clips Kitty made these clips without {names}, so they weren't posted "
                                  "automatically. Check them and publish, or turn off Rate & understand in "
                                  "this channel's settings.")
                self._say(f"Clips of {_quoted(item['title'])} are ready, but Clips Kitty made them without "
                          f"{names}, so they weren't posted automatically.", "done")
                self._broadcaster.publish({"type": "automation"})
                continue
            if item["publish_state"] == "publishing" or mode == "auto":
                self.publish(d, item, watch)
            else:
                fields = {"publish_state": "off" if mode == "off" else "ask"}
                if failed:
                    fields["publish_error"] = (f"Clips Kitty made these clips without {names}. "
                                               "Check them before you publish.")
                d.set_watch_item(item["id"], **fields)
                self._say(
                    f"Clips of {_quoted(item['title'])} are ready"
                    + (". Waiting for you to publish them." if mode == "ask" else "."),
                    "done",
                )
            self._broadcaster.publish({"type": "automation"})

    def _to_phone(self, d: StateDB, item, watch, now: float) -> bool:
        """A finished video of a watch set to Ask me on my phone. Its clips
        were offered when its job finished (remote_approval/notifier.py, even
        when Settings offer basketball only), so this only says so, and says
        honestly when nothing reached the phone: whether they were sent is
        read from the offer itself, never guessed from the switches. False
        while it waits a moment for the offer, with nothing changed."""
        if not d.clips_for_video(item["video_id"]):
            self._awaiting_offer.pop(item["id"], None)
            d.set_watch_item(item["id"], publish_state="done",
                             publish_error=_no_clips_error(d.get_outcome(item["video_id"])))
            self._say(f"No clips were made from {_quoted(item['title'])}, so nothing was sent to your phone.",
                      "done")
            return True
        from remote_approval import store as ra_store

        offered = bool(item["job_id"]) and bool(ra_store.reviews_for_job(d, int(item["job_id"])))
        problem = phone_problem(d, offered=offered)
        if not offered and not problem:
            # Nothing in Settings stops an offer, so it may still be on its
            # way (OFFER_WAIT_SECONDS). Past that, none is coming: its clips
            # were all posted already, say, or the job finished while the
            # channel was set to something else.
            first = self._awaiting_offer.setdefault(item["id"], now)
            if now - first < OFFER_WAIT_SECONDS:
                return False
            problem = _NOT_SENT
        self._awaiting_offer.pop(item["id"], None)
        failed = _steps_failed(d, item, watch) if item["publish_state"] == "" else []
        note = problem or (f"Clips Kitty made these clips without {', '.join(failed)}. "
                           "Check them before you approve them." if failed else "")
        d.set_watch_item(item["id"], publish_state="phone", publish_error=note, publish_retry_at=0)
        self._say(f"Clips of {_quoted(item['title'])} are ready. {problem or _READY_ON_PHONE}", "done")
        return True

    def publish(self, d: StateDB, item, watch) -> dict:
        """Send a finished video's clips out with its watch's settings."""
        settings = publish_settings(watch)
        # clips_for_video is ordered by score, so "the best N" is the first N.
        clips = list(d.clips_for_video(item["video_id"]))
        chosen = _chosen_windows(item)
        if chosen is None:
            # The first publish: the best N now. What it chose is kept below,
            # because a forced re-run can change the scores (a rater's) and
            # a re-send must not post a clip this one never chose.
            picked = clips[: settings.max_posts] if settings.max_posts else clips
        else:
            # A clip trimmed in the editor since keeps its place in the
            # choice (StateDB.follow_chosen_clip, from the re-render).
            picked = [c for c in clips if _window(c) in chosen]
        clip_ids = [int(c["id"]) for c in picked]
        if not clip_ids:
            if clips:
                error = ("The clips chosen when this video was first published aren't there any more, "
                         "so there was nothing to send.")
            else:
                error = _no_clips_error(d.get_outcome(item["video_id"]))
            d.set_watch_item(item["id"], publish_state="done", publish_error=error)
            return {}
        if not settings.platforms:
            d.set_watch_item(item["id"], publish_state="ask",
                             publish_error="Choose where this channel's clips go.")
            return {}
        if self._publisher is None:
            d.set_watch_item(item["id"], publish_state="ask",
                             publish_error="Publishing isn't available.")
            return {}
        keep = {} if chosen is not None else {"chosen_clips": json.dumps([list(_window(c)) for c in picked])}
        d.set_watch_item(item["id"], publish_state="publishing", publish_error="", **keep)
        where = ", ".join(_label(p) for p in settings.platforms)
        try:
            with self._busy(f"Sending clips of {_quoted(item['title'])} to {where}"):
                out = self._publish_now(d, item, watch, settings, clip_ids)
        except Exception as e:
            return self._publish_failed(d, item, settings, e)
        problems = sorted({
            s.get("reason", "") for s in out.get("skipped", [])
            if s.get("reason") and s.get("reason") != "already sent"
        })
        d.set_watch_item(item["id"], publish_state="done", publish_attempts=0,
                         publish_retry_at=0, publish_error="; ".join(problems)[:500])
        started = out.get("started", [])
        if started:
            first = _when(
                next((s.get("scheduled_for") for s in started if s.get("scheduled_for")), "")
            )
            if first:
                self._say(f"Scheduled {len(started)} clip(s) of {_quoted(item['title'])} "
                          f"for {where}, the first at {first}.", "posted")
            else:
                self._say(f"Sent {len(started)} clip(s) of {_quoted(item['title'])} "
                          f"to {where} now.", "posted")
        elif problems:
            self._say(f"Couldn't send the clips of {_quoted(item['title'])}: {problems[0]}",
                      "error")
        return out

    def _publish_now(self, d: StateDB, item, watch, settings, clip_ids) -> dict:
        return self._publisher(
            d,
            clip_ids=clip_ids,  # best first, so the daily budget goes to them
            platforms=list(settings.platforms),
            lead_hashtags=list(settings.hashtags),
            ai_hashtags=settings.ai_hashtags,
            per_day=settings.per_day if settings.spread else 0,
            gap_hours=settings.gap_hours,
            day_start=settings.day_start if settings.spread else "",
            overrides=dict(settings.overrides),
            footer=render_footer(settings.footer, item, watch),
        )

    def _publish_failed(self, d: StateDB, item, settings, e: Exception) -> dict:
        """A publish that did not start: try again later, or ask a person."""
        attempts = int(item["publish_attempts"] or 0)
        if (
            settings.mode == "auto"
            and getattr(e, "retryable", False)
            and attempts < PUBLISH_START_ATTEMPTS
        ):
            # Unreachable, busy or rate limiting: likely to pass, and a
            # hands-off channel has nobody to press Publish later.
            d.set_watch_item(
                item["id"], publish_state="publishing", publish_attempts=attempts + 1,
                publish_retry_at=self._clock() + PUBLISH_RETRY_SECONDS,
                publish_error=_short(e),
            )
            self._say(f"WoopSocial couldn't take the clips of {_quoted(item['title'])} "
                      "just now. Trying again in 15 minutes.", "retry")
            return {}
        # Not set up, no account, a key refused, or out of tries: only a
        # person can fix these, so it waits in front of one.
        d.set_watch_item(item["id"], publish_state="ask", publish_error=_short(e),
                         publish_retry_at=0)
        self._say(f"Couldn't publish the clips of {_quoted(item['title'])}: {_short(e)}", "error")
        return {}

    # -- 5. keep a hands-off channel going -------------------------------------

    def _hands_off(self, d: StateDB, item):
        """The item's watch if it is enabled and publishes automatically, else
        None. Only those retry: anywhere else a person is in the loop."""
        watch = d.get_watch(item["watch_id"])
        if watch is None or not watch["enabled"] or publish_settings(watch).mode != "auto":
            return None
        return watch

    def _retry_failed_runs(self, d: StateDB, now: float) -> None:
        """A download or processing run that failed is queued again later."""
        started = False
        for item in d.watch_items(states=("queued",)):
            tries = int(item["retries"] or 0)
            if item["publish_state"] != "" or tries >= len(PROCESS_RETRY_DELAYS):
                continue
            if _job_status(d, item) != "failed" or self._hands_off(d, item) is None:
                continue
            if not item["retry_at"]:
                d.set_watch_item(item["id"], retry_at=now + PROCESS_RETRY_DELAYS[tries])
                continue
            if item["retry_at"] > now:
                continue
            if queue.retry(d, item["job_id"]) is None:
                continue
            queue.start_if_alone(d, item["job_id"])
            d.set_watch_item(item["id"], retries=tries + 1, retry_at=0)
            self._say(f"Trying {_quoted(item['title'])} again (retry {tries + 1} of "
                      f"{len(PROCESS_RETRY_DELAYS)})", "retry")
            started = True
        if started:
            self._worker.notify()
            self._broadcaster.publish({"type": "queue"})
            self._broadcaster.publish({"type": "automation"})

    def _retry_rejected_posts(self, d: StateDB, now: float) -> None:
        """Posts a platform rejected are sent again later, and only those.

        Rejections arrive after the publish, as the status refresh learns them,
        so this looks at finished items rather than at the publish itself. The
        re-send goes through publish(), where publish_clips(once=True) passes
        over every clip already published or on its way."""
        for item in d.watch_items(states=("queued",)):
            tries = int(item["delivery_retries"] or 0)
            if item["publish_state"] != "done" or tries >= len(DELIVERY_RETRY_DELAYS):
                continue
            if self._hands_off(d, item) is None:
                continue
            rejected = d.conn.execute(
                "SELECT COUNT(*) FROM clip_publishes WHERE video_id = ? AND state = 'failed'",
                (item["video_id"],),
            ).fetchone()[0]
            if not rejected:
                continue
            if not item["publish_retry_at"]:
                d.set_watch_item(item["id"],
                                 publish_retry_at=now + DELIVERY_RETRY_DELAYS[tries])
                continue
            if item["publish_retry_at"] > now:
                continue
            d.set_watch_item(item["id"], publish_state="publishing", publish_retry_at=0,
                             publish_attempts=0, delivery_retries=tries + 1)
            self._say(f"Sending {rejected} rejected post(s) of {_quoted(item['title'])} again",
                      "retry")

    def _notice_posts(self, d: StateDB) -> None:
        """Say so when one of a watched video's posts goes live.

        The publish worker is what learns it, from WoopSocial. This only
        compares what it saw last time, so the panel can show each post
        landing. The first look just remembers what was already there.

        Keyed on the post's identity (video, window, platform), not the clip
        id: a re-render moves the row to the clip's new id, and the post it
        describes is the same one, so it is not announced again."""
        rows = d.conn.execute(
            "SELECT p.video_id, p.start_s, p.end_s, p.platform, p.post_url, c.title, c.hook "
            "FROM clip_publishes p "
            "JOIN watch_items w ON w.video_id = p.video_id "
            "LEFT JOIN clips c ON c.id = p.clip_id WHERE p.state = 'published'"
        ).fetchall()

        def key(r) -> tuple:
            return (r["video_id"], round(float(r["start_s"] or 0), 2),
                    round(float(r["end_s"] or 0), 2), r["platform"])

        seen = {key(r) for r in rows}
        if self._published_seen is not None:
            for r in rows:
                if key(r) not in self._published_seen:
                    self._say(f"Posted {_quoted(r['title'] or r['hook'] or '')} "
                              f"to {_label(r['platform'])}", "posted", r["post_url"] or "")
        self._published_seen = seen

    def _free_disk(self, d: StateDB) -> None:
        """Delete a watched video's download once its clips are published, when
        asked to. Clips, transcripts and the library entry stay; only the
        several-gigabyte source goes, which an always-on PC would otherwise
        pile up until the disk is full."""
        if self._downloads is None or d.get_flag(DELETE_SOURCES_KEY, "0") != "1":
            return
        from core.paths import cached_source, discard

        for item in d.watch_items(states=("queued",)):
            if item["source_freed"] or item["publish_state"] not in ("done", "off"):
                continue
            if _job_status(d, item) != "done":
                continue
            source = cached_source(self._downloads, item["video_id"])
            if source is None:
                # Nothing on disk to free. Marked so the folder is not scanned
                # for it again, and told apart so nobody is told it was deleted.
                d.set_watch_item(item["id"], source_freed=2)
            elif discard(source):
                d.set_watch_item(item["id"], source_freed=1)
                self._say(f"Deleted the download of {_quoted(item['title'])} to save space",
                          "info")


def woopsocial_publisher(data_dir) -> Callable[..., dict]:
    """Publishing for watched channels: WoopSocial, with the duplicate guard on
    and the publish dialog's remembered platforms left alone."""

    def publish(d: StateDB, **kwargs) -> dict:
        from server import woopsocial_service as woop

        if not woop.is_enabled(d) or not woop.has_key(data_dir):
            raise RuntimeError("WoopSocial isn't set up. Add your key in Settings to publish.")
        return woop.publish_clips(d, data_dir, once=True, remember=False, **kwargs)

    return publish


_OPEN, _CLOSE, _MORE = "\u201c", "\u201d", "\u2026"

PLATFORM_LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "linkedin": "LinkedIn"}


def _label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform.title())


def _quoted(title: str) -> str:
    """A video or clip title in curly quotes, trimmed for a one-line panel."""
    title = (title or "").strip() or "a new video"
    if len(title) > 70:
        title = title[:70] + _MORE
    return _OPEN + title + _CLOSE


def _when(iso: str) -> str:
    """'Thu 18:00' in this PC's time, for saying when a post goes out."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%a %H:%M")
    except (ValueError, AttributeError):
        return ""


def _short(error) -> str:
    text = str(error).strip()
    return (text.splitlines()[0] if text else type(error).__name__).replace("ERROR: ", "")[:300]


def _unseen(videos, known: set[str]) -> list:
    """The videos no watch has seen yet, each once, in their order."""
    fresh, seen = [], set()
    for video in videos:
        if video.video_id and video.video_id not in known and video.video_id not in seen:
            fresh.append(video)
            seen.add(video.video_id)
    return fresh


# ---- routes -----------------------------------------------------------------


def install(
    app,
    *,
    db: Callable[[], StateDB],
    worker,
    broadcaster,
    options_from: Callable[[dict], dict],
    data_dir=None,
    interval_minutes: float = DEFAULT_INTERVAL_MINUTES,
    feed=None,
    publisher=None,
    clock=time.time,
    folders=None,
) -> ChannelWatcher:
    """Add the automation routes to `app`. Returns the watcher for the caller
    to start and stop with the rest of the server.

    `options_from` turns a watch's processing options into the job payload the
    worker reads, through the same validation a queued job's options go
    through, so there is one set of rules for both. `folders` is the feed for
    watched folders (sources/folder_feed.FolderFeed), one on `clock` unless
    given, as `feed` is the one for channels."""
    if feed is None:
        from sources import channel_feed as feed

    from server.integrations import PRESETS

    if publisher is None and data_dir is not None:
        publisher = woopsocial_publisher(data_dir)
    from pathlib import Path

    watcher = ChannelWatcher(
        db, feed=feed, worker=worker, broadcaster=broadcaster, publisher=publisher,
        interval_minutes=interval_minutes, clock=clock,
        downloads=Path(data_dir) / "downloads" if data_dir is not None else None,
        folders=folders,
    )

    def load_watch(d: StateDB, watch_id: int):
        row = d.get_watch(watch_id)
        if row is None:
            raise HTTPException(404, "no such watch")
        return row

    def load_item(d: StateDB, item_id: int):
        row = d.get_watch_item(item_id)
        if row is None:
            raise HTTPException(404, "no such video")
        return row

    def status(d: StateDB) -> dict:
        watches = d.list_watches()
        return {
            "enabled": is_enabled(d),
            "delete_sources": d.get_flag(DELETE_SOURCES_KEY, "0") == "1",
            "interval_minutes": watcher.interval_seconds / 60,
            "watches": len(watches),
            "watching": sum(1 for w in watches if w["enabled"]),
            "presets": [{"id": key, **value} for key, value in PRESETS.items()],
        }

    @app.get("/automation")
    def get_automation():
        d = db()
        try:
            return status(d)
        finally:
            d.close()

    @app.patch("/automation")
    def patch_automation(body: AutomationPatch):
        d = db()
        try:
            if body.enabled is not None:
                d.set_flag(ENABLED_KEY, "1" if body.enabled else "0")
            if body.delete_sources is not None:
                d.set_flag(DELETE_SOURCES_KEY, "1" if body.delete_sources else "0")
            result = status(d)
        finally:
            d.close()
        watcher.wake()
        broadcaster.publish({"type": "automation"})
        return result

    @app.get("/automation/activity")
    def activity():
        """What the watcher is doing right now, and what it did last.

        For a live panel: "now" is one line to show big, "events" the recent
        steps, newest first. Processing is the worker's, so it is read from
        the job the way the history rows read it."""
        d = db()
        try:
            live = watcher.activity()
            watches = [w for w in d.list_watches() if w["enabled"]]
            now: dict
            if not is_enabled(d):
                now = {"state": "off", "text": "Not watching. Switch it on to start."}
            elif live["doing"]:
                now = {"state": "busy", "text": live["doing"]}
            else:
                now = {}
                for item in d.watch_items(states=("queued",), limit=50):
                    status = _job_status(d, item)
                    if status == "running":
                        now = {"state": "busy",
                               "text": f"Making clips of {_quoted(item['title'])}",
                               "progress": worker.progress_snapshot(item["job_id"])}
                        break
                    if status == "queued" and not now:
                        now = {"state": "waiting",
                               "text": f"{_quoted(item['title'])} is waiting in the queue"}
                if not now:
                    count = len(watches)
                    now = {"state": "watching",
                           "text": f"Watching {count} channel{'s' if count != 1 else ''}"
                           if count else "No channels to watch yet"}
            next_checks = [w["next_poll_at"] for w in watches if w["next_poll_at"]]
            return {
                "now": now,
                "watching": len(watches) if is_enabled(d) else 0,
                "next_check_at": min(next_checks) if next_checks else 0,
                "events": live["events"],
            }
        finally:
            d.close()

    @app.get("/automation/slots")
    def slots(per_day: int = 5, gap_hours: float = 1, day_start: str = "", count: int = 5):
        """When the next posts would go out with these settings, after
        everything already scheduled. For the preview beside the controls;
        it reserves nothing."""
        from publish.errors import PublishError
        from server import woopsocial_service as woop

        try:
            PublishSettings(per_day=per_day, gap_hours=gap_hours, day_start=day_start)
        except Exception as e:
            raise HTTPException(400, "Those schedule settings are not valid.") from e
        d = db()
        try:
            times = woop.schedule_times(
                d, max(1, min(20, count)), per_day=per_day, gap_hours=gap_hours,
                day_start=day_start,
            )
            return {"times": times, "already_scheduled": len(woop.committed_times(d))}
        except PublishError as e:
            raise HTTPException(400, e.message) from e
        finally:
            d.close()

    @app.get("/automation/watches")
    def list_watches():
        d = db()
        try:
            return [view_watch(d, w) for w in d.list_watches()]
        finally:
            d.close()

    @app.post("/automation/watches")
    def add_watch(body: WatchIn):
        try:
            if body.platform == "folder":
                # Checked before it is kept or listed: an absolute path,
                # resolved, a real folder, and on this PC, never a network
                # path (folder_feed.resolve, core/paths.picked_folder).
                channel = watcher.folders.resolve(body.platform, body.channel)
                from core.paths import within

                if data_dir is not None and within(Path(data_dir), Path(channel.channel_key)):
                    # Its own copies in downloads/ would be found, copied in
                    # and found again, without end.
                    raise ValueError("Choose a folder outside Clips Kitty's own data folder.")
            else:
                channel = feed.resolve(body.platform, body.channel)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        d = db()
        try:
            existing = d.find_watch(channel.platform, channel.channel_key)
            if existing is not None:
                return {"created": False, **view_watch(d, existing)}
            options = options_from(body.options) if body.options else {}
            preset = body.preset or "standard"
            if preset not in PRESETS:
                raise HTTPException(400, f"unknown preset '{preset}'")
            options["preset"] = preset
            if body.publish is not None:
                _validate_discord_destination(d, body.publish)
            watch_id = d.insert_watch(
                channel.platform, channel.channel_key, name=channel.name,
                publish=(body.publish or PublishSettings()).model_dump_json(),
                options=json.dumps(options),
            )
            watch = load_watch(d, watch_id)
            # In Creators straight away, before the first video, so what it
            # learns has somewhere visible to go.
            try:
                watch_creator(d, watch, create=True)
            except Exception as e:
                print(f"Watch: couldn't make a creator profile for {channel.name}: {e}")
            result = {"created": True, **view_watch(d, watch)}
        finally:
            d.close()
        watcher.wake()
        broadcaster.publish({"type": "automation"})
        return result

    @app.patch("/automation/watches/{watch_id}")
    def patch_watch(watch_id: int, body: WatchPatch):
        d = db()
        try:
            watch = load_watch(d, watch_id)
            fields: dict = {}
            if body.enabled is not None:
                fields["enabled"] = 1 if body.enabled else 0
            if body.name is not None and body.name.strip():
                fields["name"] = body.name.strip()
            if body.options is not None or body.preset is not None:
                options = watch_options(watch)
                preset = body.preset if body.preset is not None else options.get("preset", "standard")
                if preset not in PRESETS:
                    raise HTTPException(400, f"unknown preset '{preset}'")
                if body.options is not None:
                    options = options_from(body.options)
                options["preset"] = preset
                fields["options"] = json.dumps(options)
            if body.publish is not None:
                _validate_discord_destination(d, body.publish)
                fields["publish"] = body.publish.model_dump_json()
            if body.backlog is not None:
                fields["backlog"] = body.backlog
            if body.min_minutes is not None:
                fields["min_minutes"] = body.min_minutes
            d.set_watch(watch_id, **fields)
            result = view_watch(d, load_watch(d, watch_id))
        finally:
            d.close()
        watcher.wake()
        broadcaster.publish({"type": "automation"})
        return result

    @app.delete("/automation/watches/{watch_id}")
    def delete_watch(watch_id: int):
        d = db()
        try:
            load_watch(d, watch_id)
            d.delete_watch(watch_id)
        finally:
            d.close()
        broadcaster.publish({"type": "automation"})
        return {"deleted": True}

    @app.post("/automation/watches/{watch_id}/check")
    def check_now(watch_id: int):
        d = db()
        try:
            load_watch(d, watch_id)
            d.set_watch(watch_id, next_poll_at=0)
            # Its videos still waiting to be ready are looked at again too, so
            # a Twitch VOD whose stream just ended is queued now, not at its
            # next look a quarter of an hour away.
            for item in d.watch_items(watch_id=watch_id, states=("new", "not_ready"), limit=500):
                d.set_watch_item(item["id"], next_check_at=0)
        finally:
            d.close()
        watcher.wake()
        return {"checking": True}

    @app.get("/automation/items")
    def list_items(watch_id: int | None = None, limit: int = 100):
        d = db()
        try:
            rows = d.watch_items(watch_id=watch_id, limit=max(1, min(500, limit)))
            return [view_item(d, r, worker) for r in rows]
        finally:
            d.close()

    @app.post("/automation/items/{item_id}/queue")
    def clip_this(item_id: int):
        """Clip a video the watch set aside: back catalogue, missed while
        closed, too short, or anything else skipped. The person asking is the
        go-ahead, so the minimum length does not apply."""
        d = db()
        try:
            item = load_item(d, item_id)
            if item["state"] not in ("baseline", "skipped", "error"):
                return view_item(d, item, worker)
            found = watcher.readiness(item, 0)
            if found.state == "ready":
                d.set_watch_item(item_id, state="waiting", reason="", requested=1)
            elif found.state == "not_yet":
                d.set_watch_item(item_id, state="not_ready", reason=found.reason, requested=1,
                                 detected_at=clock(), next_check_at=clock() + recheck_seconds(item["platform"]))
            else:
                raise HTTPException(409, found.reason or "That video can't be clipped.")
            result = view_item(d, load_item(d, item_id), worker)
        finally:
            d.close()
        watcher.wake()
        return result

    @app.post("/automation/items/{item_id}/publish")
    def publish_item(item_id: int):
        """Publish a finished video's clips with its watch's settings: the
        answer to "ask first", and the retry for any that failed. Only what is
        not already sent or on its way goes out. Runs on the watcher's thread,
        so the request returns at once and uploads never hold it open."""
        d = db()
        try:
            item = load_item(d, item_id)
            watch = d.get_watch(item["watch_id"])
            if watch is not None and publish_settings(watch).mode == "phone":
                # Never through WoopSocial: the phone is where these are posted from.
                raise HTTPException(409, "This channel's clips are approved from your phone.")
            if item["state"] != "queued" or _job_status(d, item) != "done":
                raise HTTPException(409, "This video's clips aren't ready yet.")
            if item["publish_state"] != "publishing" or item["publish_retry_at"]:
                # A person asking is the go-ahead now, not after a wait.
                d.set_watch_item(item_id, publish_state="publishing", publish_error="",
                                 publish_retry_at=0, publish_attempts=0)
            result = view_item(d, load_item(d, item_id), worker)
        finally:
            d.close()
        watcher.wake()
        broadcaster.publish({"type": "automation"})
        return result

    @app.post("/automation/items/{item_id}/skip")
    def skip_item(item_id: int):
        d = db()
        try:
            item = load_item(d, item_id)
            if item["state"] in ("new", "not_ready", "waiting"):
                d.set_watch_item(item_id, state="skipped", reason="Skipped by you.")
            elif item["publish_state"] == "ask":
                d.set_watch_item(item_id, publish_state="off")
            result = view_item(d, load_item(d, item_id), worker)
        finally:
            d.close()
        broadcaster.publish({"type": "automation"})
        return result

    return watcher

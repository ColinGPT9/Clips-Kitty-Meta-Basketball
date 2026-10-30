"""What has landed in a watched folder on this PC.

A Google Drive for desktop, Dropbox or OneDrive folder that a team drops
videos into. The folder twin of sources/channel_feed.py, answering in the same
shapes (Channel, NewSourceVideo, Readiness), so the watcher
(server/automation.py) treats a folder like a channel: the first look notes
what is already there, and a file that arrives after it is a new video.
Detection only. A file is copied into Clips Kitty when its video is queued,
by sources/local_file.py, exactly as a file added by hand is, under the same
id, so the two are one video and it is never clipped twice.

What a folder needs that a channel doesn't:

  * Only finished files. A sync client still downloading one, or a copy
    still running, changes its size or time from one look to the next. A file
    is reported once both have stayed the same since a look at least
    SETTLE_SECONDS earlier. That is remembered in memory, so a restart costs
    one extra look, never a half-copied video.
  * Only the folder itself: no subfolders, and nothing hidden, temporary or
    half downloaded (~$name, *.tmp, *.part, *.crdownload, desktop.ini).
  * Only this PC's own disk. A network path is refused, as core/paths
    refuses one for a single file.
  * Read only. Nothing here moves, changes or deletes a file in the folder.

Imports neither sources.dispatch nor yt_dlp, so its tests run in CI's light
job. ffprobe is the one program it runs, and only to ask about a file.
"""

import json
import os
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from core.paths import picked_file, picked_folder
from sources import local_file
from sources.channel_feed import Channel, NewSourceVideo, Readiness

# How long a file's size and time must stay the same before it counts as
# arrived. A sync client writes a big file in pieces seconds apart; a minute
# with no change is a file that has finished.
SETTLE_SECONDS = 60

# What a sync client, a browser or another app writes while a file is still
# on its way, or keeps for itself. None of it is anyone's finished video.
_PARTIAL = (".tmp", ".temp", ".part", ".partial", ".crdownload", ".download")
_SYSTEM = ("desktop.ini", "thumbs.db")
# Windows' own marks for a file nobody is meant to see or keep.
_HIDDEN = (getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2) | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
           | getattr(stat, "FILE_ATTRIBUTE_TEMPORARY", 0x100))

GONE = "The file isn't in the folder any more."
CHANGED = "The file changed after it was found. The changed file counts as a new video."


def skipped_name(name: str) -> bool:
    """Whether a watched folder passes over a file by its name alone: hidden
    (a leading dot), an app's own working file (~$ and other ~ names), a
    download that hasn't finished, or Windows' own folder files."""
    lowered = name.lower()
    return lowered.startswith((".", "~")) or lowered in _SYSTEM or lowered.endswith(_PARTIAL)


class FolderFeed:
    """Every watched folder. One for the app, because it remembers what each
    file looked like at the last look, which is how a finished file is told
    from one still arriving.

    `clock`, `probe` and `importer` are the seams tests use: the time,
    ffprobe, and the copy into downloads/ (sources/local_file.bring_in)."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        probe: Callable[[Path], dict] | None = None,
        importer: Callable[..., local_file.Imported] | None = None,
    ):
        self._clock = clock
        self._probe = probe or _probe
        self._import = importer or local_file.bring_in
        self._lock = threading.Lock()
        # Per folder, each file as last seen: {path: (size, mtime_ns, since)},
        # `since` being when it was first seen at that size and time.
        self._seen: dict[str, dict[str, tuple[int, int, float]]] = {}

    # ---- adding a folder ---------------------------------------------------

    def resolve(self, platform: str, text: str) -> Channel:
        """The folder to watch, from what the folder picker gave. Raises
        ValueError with a message worth showing."""
        raw = (text or "").strip()
        if raw.startswith(("\\\\", "//")):
            raise ValueError("Choose a folder on this PC. A network folder can't be watched.")
        folder = picked_folder(raw)
        if folder is None:
            raise ValueError("Choose a folder on this PC that is there and can be opened.")
        return Channel("folder", str(folder), folder.name or str(folder))

    # ---- looking at it -----------------------------------------------------

    def look(self, channel_key: str) -> tuple[list[NewSourceVideo], list[NewSourceVideo]]:
        """Every video file in the folder now, and those of them that have
        finished arriving, each newest first. The first is what a first look
        notes as already there; the second is what can be clipped.

        Raises ValueError when the folder can't be read (removed, or a drive
        that isn't there yet), so the watch says so instead of looking empty."""
        folder = picked_folder(channel_key)
        if folder is None:
            raise ValueError("The folder isn't there or can't be opened.")
        try:
            with os.scandir(folder) as listing:
                names = [entry.name for entry in listing]
        except OSError as e:
            raise ValueError(f"The folder can't be read: {e.strerror or e}") from e
        files = []
        for name in names:
            if skipped_name(name) or not name.lower().endswith(local_file.VIDEO_SUFFIXES):
                continue
            # Each file through the same check a picked file passes, so its
            # path and stat are exactly what /videos/local would see for it,
            # and so is its id.
            picked = picked_file(os.path.join(folder, name), local_file.VIDEO_SUFFIXES)
            if picked is None:
                continue  # a subfolder, or a link to nothing
            path, _suffix, st = picked
            if path.parent != folder or not st.st_size:
                continue  # a link to a file somewhere else, or one with nothing in it yet
            if getattr(st, "st_file_attributes", 0) & _HIDDEN:
                continue
            files.append((path, st))
        now = self._clock()
        with self._lock:
            before = self._seen.get(str(folder), {})
            seen = {}
            for path, st in files:
                state = (st.st_size, st.st_mtime_ns)
                old = before.get(str(path))
                seen[str(path)] = (*state, old[2] if old is not None and old[:2] == state else now)
            self._seen[str(folder)] = seen
        files.sort(key=lambda found: found[1].st_mtime, reverse=True)
        present = [_video(folder, path, st) for path, st in files]
        settled = [video for video in present if now - seen[video.url][2] >= SETTLE_SECONDS]
        return present, settled

    def latest(self, platform: str, channel_key: str) -> list[NewSourceVideo]:
        """The finished video files, newest first: channel_feed.latest's answer."""
        return self.look(channel_key)[1]

    # ---- is it ready to clip? ----------------------------------------------

    def readiness(self, url: str, min_seconds: float = 0, *, video_id: str = "") -> Readiness:
        """Whether a file found in a watched folder can be clipped now, later,
        or not at all, from ffprobe: a video stream in it, long enough, and
        which way up it is shown. `video_id` is the id it was found under; a
        file that changed since is passed over, as its new version is found
        on its own."""
        picked = picked_file(url, local_file.VIDEO_SUFFIXES)
        if picked is None:
            if picked_folder(os.path.dirname(url)) is None:
                # The whole folder is missing: most likely a drive that isn't
                # there yet (Google Drive starts after Windows does).
                return Readiness("not_yet", "The folder can't be opened right now.")
            return Readiness("skip", GONE)
        path, _suffix, st = picked
        facts = {"title": path.stem, "published_at": float(st.st_mtime)}
        if video_id and local_file.video_id(path, st) != video_id:
            return Readiness("skip", CHANGED, **facts)
        try:
            found = self._probe(path)
        except Exception as e:
            return Readiness("not_yet", f"Couldn't read the file yet: {_short(e)}", **facts)
        if not found.get("codec"):
            return Readiness("skip", "There is no video in it.", **facts)
        duration = float(found.get("duration") or 0)
        width, height = int(found.get("width") or 0), int(found.get("height") or 0)
        facts["duration"] = duration
        facts["orientation"] = ("vertical" if height > width else "horizontal") if width and height else ""
        if min_seconds and duration and duration < min_seconds:
            return Readiness("skip", f"Shorter than {min_seconds / 60:g} minutes.", **facts)
        return Readiness("ready", "", **facts)

    # ---- bringing it in ----------------------------------------------------

    def bring_in(self, url: str, video_id: str, downloads: Path) -> local_file.Imported:
        """Copy the file found at `url` into `downloads` for its job, the way
        a file added by hand is copied (sources/local_file.py). It is only
        read: the file in the folder stays where it is, as it is. Raises
        ValueError with the reason to show: GONE or CHANGED, or
        local_file.NotImported when it can't be made into a video."""
        picked = picked_file(url, local_file.VIDEO_SUFFIXES)
        if picked is None:
            raise ValueError(GONE)
        path, _suffix, st = picked
        if local_file.video_id(path, st) != video_id:
            raise ValueError(CHANGED)
        return self._import(path, st, downloads)


def _video(folder: Path, path: Path, st) -> NewSourceVideo:
    """One file as the watcher takes a video: its id the one /videos/local
    gives it, and its time its last change, the nearest a folder has to when
    it was posted."""
    return NewSourceVideo("folder", str(folder), local_file.video_id(path, st), str(path),
                          path.stem, float(st.st_mtime))


def _probe(path: Path) -> dict:
    """The first video stream's codec and shown size, and the file's length,
    from one ffprobe. Raises when ffprobe can't read the file at all."""
    from core.binaries import ffprobe

    out = subprocess.run(
        [ffprobe(), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width,height:stream_side_data=rotation:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError((out.stderr or "").strip() or "ffprobe couldn't read it")
    data = json.loads(out.stdout or "{}")
    streams = [s for s in data.get("streams") or [] if isinstance(s, dict)]
    video = streams[0] if streams else {}
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    # A phone recording can be stored landscape with a rotation flag and shown
    # upright; what matters is how it is shown, as in core/modes.probe_size.
    rotation = next((s.get("rotation") for s in video.get("side_data_list") or []
                     if isinstance(s, dict) and "rotation" in s), 0)
    try:
        if abs(int(float(rotation or 0))) % 180 == 90:
            width, height = height, width
    except (TypeError, ValueError):
        pass
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"codec": str(video.get("codec_name") or ""), "width": width, "height": height,
            "duration": duration}


def _short(error: Exception | str) -> str:
    text = str(error).strip()
    return (text.splitlines()[0] if text else type(error).__name__)[:300]

"""A video file from this computer, brought into Clips Kitty.

What /videos/local (a file added by hand) and a watched folder
(sources/folder_feed.py) both do with one, kept in one place: the id the file
goes by, and the copy of it in downloads/ that its job reads as
"local:<id>". The same file reached either way is the same video, so
core/queue.enqueue_once never clips it twice.

Stdlib only when imported, like core/paths.py, so a watched folder's tests
run in CI's light job. FFmpeg is only called when a file is brought in.
"""

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

# What a file from this computer may be. /videos/local checks a picked file
# against these (core.paths.picked_file) and a watched folder lists only
# these, so the two always agree on what counts as a video.
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".flv", ".ts", ".mpg",
                  ".mpeg", ".wmv", ".m2ts", ".mts")


class NotImported(ValueError):
    """A file that could not be brought in, with the reason to show."""


@dataclass
class Imported:
    video_id: str
    seconds: float  # its length; 0 when ffprobe couldn't say


def video_id(path, st) -> str:
    """The id a file from this computer goes by: its resolved path, size and
    modification time. The same file is the same video however it was added;
    a file that changed since is a new one.

    `path` and `st` are what core.paths.picked_file returned, so the path is
    already resolved and the same file always gives the same string."""
    # usedforsecurity=False because this is a naming scheme, not a defence:
    # it turns a path into a stable short id so re-importing the same file
    # reuses its downloads/ entry. Nothing trusts it, and collisions cost a
    # duplicate import rather than anything worse. Saying so explicitly
    # keeps the security scanners from reading it as a weak digest.
    return "local_" + hashlib.md5(
        f"{path}|{st.st_size}|{int(st.st_mtime)}".encode(),
        usedforsecurity=False,
    ).hexdigest()[:12]


def bring_in(src: Path, st, downloads: Path) -> Imported:
    """Copy a file into `downloads` as <video_id>.mp4, the copy its job reads.

    `src` and `st` are what core.paths.picked_file returned: resolved and
    stat-ed once, so nothing here asks the filesystem about a user-supplied
    string a second time. The file itself is only ever read. Raises
    NotImported with the reason to show."""
    from core.binaries import ffprobe

    # Must contain a video stream (catches audio files / random files).
    probe = subprocess.run(
        [ffprobe(), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(src)],
        capture_output=True, text=True,
    )
    if probe.returncode != 0 or not probe.stdout.strip():
        raise NotImported("that file doesn't look like a video")

    # Length, while we already have the file open. Local files are the one
    # path where the queue can know a video's duration before it runs, and
    # duration is what its time estimate scales by.
    seconds = 0.0
    try:
        dur = subprocess.run(
            [ffprobe(), "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(src)],
            capture_output=True, text=True,
        )
        seconds = float(dur.stdout.strip() or 0)
    except (ValueError, OSError):
        pass  # only costs a less precise estimate

    vid = video_id(src, st)
    dest = Path(downloads) / f"{vid}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)

    # H.264, H.265, AV1 and VP9 are stream-copied into the pipeline's mp4
    # layout, which takes seconds; the job converts the slow three to
    # H.264 where the window can show it. Anything else (ProRes, old AVI
    # codecs) is converted here. Never left half-written: see
    # import_local_source, and #122 for what a half-written copy cost.
    from video.encoding import import_local_source

    if not import_local_source(src, dest, probe.stdout.strip()):
        raise NotImported("couldn't convert this file — export it as MP4 (H.264) and try again")
    return Imported(vid, seconds)

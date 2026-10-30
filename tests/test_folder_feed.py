"""Watching a folder on this PC (sources/folder_feed.py), without FFmpeg.

What matters: a file counts only once it has finished arriving (its size and
time unchanged for a minute), nothing hidden, temporary or half downloaded is
ever taken for a video, only a real folder on this PC can be watched, and a
file found in a folder goes by exactly the id /videos/local gives it, so the
same file added by hand is never clipped twice. The file itself is only read.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.paths import picked_folder
from sources import folder_feed, local_file
from sources.folder_feed import FolderFeed

T0 = 1_800_000_000.0


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self) -> float:
        return self.now


def probe(**facts):
    """ffprobe's answer, stood in for: an hour of landscape H.264 unless told otherwise."""
    found = {"codec": "h264", "width": 1920, "height": 1080, "duration": 3600.0, **facts}
    return lambda path: dict(found)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def feed(clock):
    return FolderFeed(clock=clock, probe=probe())


def write(folder: Path, name: str, data: bytes = b"video bytes") -> Path:
    path = folder / name
    path.write_bytes(data)
    return path


def names(videos) -> list[str]:
    return [Path(v.url).name for v in videos]


# ---- only finished files ----------------------------------------------------------


def test_a_file_counts_only_once_it_has_stopped_changing_for_a_minute(tmp_path, clock, feed):
    path = write(tmp_path, "game.mp4", b"first half")
    present, settled = feed.look(str(tmp_path))
    assert names(present) == ["game.mp4"] and settled == []
    clock.now += folder_feed.SETTLE_SECONDS - 1
    assert feed.look(str(tmp_path))[1] == []
    # Still arriving: it grew, so its minute starts again.
    path.write_bytes(b"first half, second half")
    clock.now += 2
    assert feed.look(str(tmp_path))[1] == []
    clock.now += folder_feed.SETTLE_SECONDS - 1
    assert feed.look(str(tmp_path))[1] == []
    clock.now += 1
    assert names(feed.look(str(tmp_path))[1]) == ["game.mp4"]


def test_a_restart_costs_one_more_look_never_a_half_copied_file(tmp_path, clock):
    write(tmp_path, "game.mp4")
    first = FolderFeed(clock=clock, probe=probe())
    first.look(str(tmp_path))
    clock.now += 600
    assert names(first.look(str(tmp_path))[1]) == ["game.mp4"]
    restarted = FolderFeed(clock=clock, probe=probe())  # nothing remembered
    assert restarted.look(str(tmp_path))[1] == []
    clock.now += folder_feed.SETTLE_SECONDS
    assert names(restarted.look(str(tmp_path))[1]) == ["game.mp4"]


def test_the_first_look_lists_every_file_for_the_watch_to_note_as_already_there(tmp_path, clock, feed):
    """A watch's first look notes what is already in the folder, finished or
    not, so none of it is clipped later as if it were new."""
    older, newer = write(tmp_path, "older.mp4"), write(tmp_path, "newer.mkv")
    os.utime(older, (T0 - 7200, T0 - 7200))
    os.utime(newer, (T0 - 60, T0 - 60))
    present, settled = feed.look(str(tmp_path))
    assert names(present) == ["newer.mkv", "older.mp4"]  # newest first, as every listing is
    assert settled == []


# ---- only video files, only the folder itself --------------------------------------


def test_hidden_temporary_and_half_downloaded_files_and_subfolders_are_passed_over(tmp_path, feed):
    for name in ("final.mp4", "Final Cut.MOV", "~$final.mp4", "~final.mp4", ".final.mp4", "final.mp4.part",
                 "final.mp4.crdownload", "render.tmp", "desktop.ini", "Thumbs.db", "notes.txt"):
        write(tmp_path, name)
    write(tmp_path, "nothing yet.mp4", b"")  # made, not written to yet
    (tmp_path / "Season 2").mkdir()
    write(tmp_path / "Season 2", "inside.mp4")
    (tmp_path / "folder.mp4").mkdir()  # a folder named like a video
    present, _ = feed.look(str(tmp_path))
    assert sorted(names(present)) == ["Final Cut.MOV", "final.mp4"]


@pytest.mark.skipif(sys.platform != "win32", reason="the hidden mark is Windows' own")
def test_a_file_windows_marks_hidden_is_passed_over(tmp_path, feed):
    import ctypes

    hidden = write(tmp_path, "hidden.mp4")
    write(tmp_path, "shown.mp4")
    assert ctypes.windll.kernel32.SetFileAttributesW(str(hidden), 0x2)  # FILE_ATTRIBUTE_HIDDEN
    assert names(feed.look(str(tmp_path))[0]) == ["shown.mp4"]


def test_skipped_names():
    for name in ("~$Game.mp4", ".DS_Store", "clip.MP4.PART", "x.crdownload", "y.tmp", "desktop.ini"):
        assert folder_feed.skipped_name(name), name
    for name in ("Game 3.mp4", "final.mov", "part 2.mp4"):
        assert not folder_feed.skipped_name(name), name


# ---- only a real folder on this PC -------------------------------------------------


@pytest.mark.parametrize("path", [r"\\server\share\videos", "//server/share/videos"])
def test_a_network_folder_is_refused(path, feed):
    assert picked_folder(path) is None
    with pytest.raises(ValueError, match="network"):
        feed.resolve("folder", path)


def test_only_a_folder_that_is_there_can_be_watched(tmp_path, feed):
    a_file = write(tmp_path, "game.mp4")
    for bad in (str(a_file), str(tmp_path / "missing"), "relative/folder", "", "bad\x00name"):
        assert picked_folder(bad) is None, bad
        with pytest.raises(ValueError):
            feed.resolve("folder", bad)
    (tmp_path / "sub").mkdir()
    channel = feed.resolve("folder", str(tmp_path / "sub" / ".."))
    assert (channel.platform, channel.channel_key) == ("folder", str(tmp_path.resolve()))
    assert channel.name == tmp_path.resolve().name


def test_a_folder_that_is_gone_is_an_error_not_an_empty_folder(tmp_path, feed):
    drive = tmp_path / "drive"
    drive.mkdir()
    key = feed.resolve("folder", str(drive)).channel_key
    drive.rmdir()
    with pytest.raises(ValueError):
        feed.look(key)


# ---- the same video as /videos/local -------------------------------------------------


def test_a_found_file_goes_by_the_id_videos_local_gives_it(tmp_path, feed):
    path = write(tmp_path, "game.mp4", b"x" * 1234)
    video = feed.look(str(tmp_path))[0][0]
    real = path.resolve()
    st = real.stat()
    # The formula /videos/local has always used: the resolved path, the size,
    # and the modification time to the second.
    expected = "local_" + hashlib.md5(f"{real}|{st.st_size}|{int(st.st_mtime)}".encode(),
                                      usedforsecurity=False).hexdigest()[:12]
    assert video.video_id == expected == local_file.video_id(real, st)
    assert (video.platform, video.title, video.url) == ("folder", "game", str(real))
    assert video.published_at == pytest.approx(st.st_mtime)


# ---- is it ready to clip? -------------------------------------------------------------


def test_a_finished_file_is_ready_with_its_length_and_shape(tmp_path, clock):
    write(tmp_path, "phone.mp4")
    feed = FolderFeed(clock=clock, probe=probe(width=1080, height=1920, duration=420.0))
    video = feed.look(str(tmp_path))[0][0]
    found = feed.readiness(video.url, 180, video_id=video.video_id)
    assert (found.state, found.duration, found.orientation, found.title) == ("ready", 420.0, "vertical", "phone")
    assert found.published_at == pytest.approx(video.published_at)


@pytest.mark.parametrize("facts, reason", [
    ({"duration": 41.0}, "Shorter than 3 minutes."),
    ({"codec": "", "width": 0, "height": 0}, "There is no video in it."),
])
def test_what_cannot_be_clipped_is_set_aside_with_the_reason(tmp_path, clock, facts, reason):
    write(tmp_path, "clip.mp4")
    feed = FolderFeed(clock=clock, probe=probe(**facts))
    video = feed.look(str(tmp_path))[0][0]
    found = feed.readiness(video.url, 180, video_id=video.video_id)
    assert (found.state, found.reason) == ("skip", reason)


def test_a_file_ffprobe_cannot_read_yet_is_looked_at_again(tmp_path, clock):
    def unreadable(path):
        raise RuntimeError("moov atom not found\nand more")

    write(tmp_path, "syncing.mp4")
    feed = FolderFeed(clock=clock, probe=unreadable)
    video = feed.look(str(tmp_path))[0][0]
    found = feed.readiness(video.url, 0, video_id=video.video_id)
    assert found.state == "not_yet" and found.reason == "Couldn't read the file yet: moov atom not found"


def test_a_file_changed_or_gone_since_it_was_found_is_passed_over(tmp_path, feed):
    path = write(tmp_path, "game.mp4", b"take one")
    video = feed.look(str(tmp_path))[0][0]
    path.write_bytes(b"take two, which is longer")
    found = feed.readiness(video.url, 0, video_id=video.video_id)
    assert (found.state, found.reason) == ("skip", folder_feed.CHANGED)
    with pytest.raises(ValueError, match="changed"):
        feed.bring_in(video.url, video.video_id, tmp_path / "downloads")
    path.unlink()
    assert feed.readiness(video.url, 0, video_id=video.video_id).reason == folder_feed.GONE
    with pytest.raises(ValueError, match="isn't in the folder"):
        feed.bring_in(video.url, video.video_id, tmp_path / "downloads")
    # A whole folder missing is most likely a drive not there yet: waited for.
    assert feed.readiness(str(tmp_path / "not mounted" / "game.mp4"), 0).state == "not_yet"


def test_bringing_a_file_in_hands_the_checked_file_to_the_import(tmp_path, clock):
    calls = []

    def importer(src, st, downloads):
        calls.append((src, downloads))
        return local_file.Imported(local_file.video_id(src, st), 12.0)

    feed = FolderFeed(clock=clock, probe=probe(), importer=importer)
    path = write(tmp_path, "game.mp4")
    video = feed.look(str(tmp_path))[0][0]
    got = feed.bring_in(video.url, video.video_id, tmp_path / "downloads")
    assert got == local_file.Imported(video.video_id, 12.0)
    assert calls == [(path.resolve(), tmp_path / "downloads")]


# ---- with FFmpeg: the real probe, the real copy, and /videos/local itself ----------------


def _ffmpeg_or_skip() -> str:
    from core.binaries import ffmpeg

    binary = ffmpeg()
    try:
        subprocess.run([binary, "-version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("FFmpeg isn't available")
    return binary


def _real_video(path: Path, size: str = "1280x720") -> Path:
    subprocess.run([_ffmpeg_or_skip(), "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30",
                    "-f", "lavfi", "-i", "sine", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def test_a_real_file_is_read_and_copied_in_while_the_original_stays_as_it_was(tmp_path, clock):
    drive = tmp_path / "drive"
    drive.mkdir()
    original = _real_video(drive / "Some Team final.mp4", "720x1280")
    before = (original.read_bytes(), original.stat().st_mtime_ns)
    feed = FolderFeed(clock=clock)
    video = feed.look(str(drive))[0][0]
    found = feed.readiness(video.url, 0, video_id=video.video_id)
    assert (found.state, found.orientation) == ("ready", "vertical")
    assert found.duration == pytest.approx(3.0, abs=0.2)
    got = feed.bring_in(video.url, video.video_id, tmp_path / "downloads")
    assert got.video_id == video.video_id
    assert (tmp_path / "downloads" / f"{video.video_id}.mp4").is_file()
    assert (original.read_bytes(), original.stat().st_mtime_ns) == before
    assert [p.name for p in drive.iterdir()] == [original.name]  # nothing moved, added or removed


def test_a_file_added_by_hand_and_found_in_a_folder_is_one_video(tmp_path, clock):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("numpy")
    from fastapi.testclient import TestClient

    from core.state import StateDB
    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    drive = tmp_path / "drive"
    drive.mkdir()
    path = _real_video(drive / "game.mp4")
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path / "data")
    # No `with` block: startup never runs, so no worker thread picks the job up.
    client = TestClient(create_app(config, tmp_path / "settings.yaml"), base_url="http://127.0.0.1")
    res = client.post("/videos/local", json={"path": str(path)})
    assert res.status_code == 200, res.text
    by_hand = res.json()["video_id"]
    assert FolderFeed(clock=clock).look(str(drive))[0][0].video_id == by_hand
    db = StateDB(tmp_path / "data" / "state.db")
    try:
        payload = json.loads(db.get_job(res.json()["job_id"])["payload"])
    finally:
        db.conn.close()
    assert payload["url"] == f"local:{by_hand}"

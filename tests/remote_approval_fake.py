"""Shared pieces for the remote approval tests: a finished basketball job with
real clip files on disk, a fake relay, and a signed command.

Not a test module itself (no test_ prefix). The fake relay answers the way
meta-relay/worker.js does, for the unit tests; tests/test_remote_approval_e2e.py
runs the real worker under Node instead.
"""

import hashlib
import hmac
import json
import os

from remote_approval import relay as relay_mod

SECRET = "device-secret-for-tests-0123456789abcdef"

PLAYS = [
    # (sport_event, label, score)
    ("poster_dunk", "Poster dunk", 92),
    ("corner_three", "Corner three", 80),
    ("block", "Block", 75),
    ("alley_oop", "Alley-oop", 88),
    ("free_throw", "Free throw", 30),
]


def finished_job(db, tmp_path, *, plays=PLAYS, sport="basketball", title="Raptors vs Celtics"):
    """A process job that finished, its video, and one rendered clip per play,
    each with a real file (so sizes and mtimes can be pinned)."""
    video_id = "vid_" + hashlib.sha1(title.encode()).hexdigest()[:8]
    db.upsert_video(video_id, title=title)
    payload = {"url": "https://example.com/game.mp4"}
    if sport:
        payload["sport"] = {"name": sport, "highlights": "best"}
    job_id = db.add_job("process", json.dumps(payload), video_id=video_id, title=title)
    folder = tmp_path / "clips"
    folder.mkdir(exist_ok=True)
    for i, (event, label, score) in enumerate(plays):
        path = folder / f"clip{i}.mp4"
        path.write_bytes(os.urandom(4096 + i))
        db.add_clip(video_id, 10.0 * i, 10.0 * i + 12, score, f"{label}!", path=str(path),
                    title=f"{label} in the third", hashtags=json.dumps(["#nba", "#raptors"]),
                    scores=json.dumps({"sport_event": event, "sport_label": label, "sport_t": 10.0 * i + 6}),
                    render_opts=json.dumps({"sport": "basketball"} if sport == "basketball" else {}))
    db.finish_job(job_id, "done")
    return db.get_job(job_id)


def sign_command(command: dict, secret: str = SECRET) -> dict:
    body = {k: v for k, v in command.items() if k != "sig"}
    sig = hmac.new(secret.encode(), relay_mod.canonical_json(body).encode(), hashlib.sha256).hexdigest()
    return {**body, "sig": sig}


class FakeRelay:
    """Answers like the relay. `fail` maps a method name to a RelayError to
    raise once."""

    def __init__(self):
        self.calls = []
        self.pending = []
        self.acks = []
        self.notified = []
        self.fail = {}
        self.n = 0
        self.accounts = {"instagram": {"connected": True, "name": "@hoopsdaily"},
                         "facebook": {"connected": True, "name": "Hoops Daily"},
                         "threads": {"connected": True, "name": "@hoopsdaily"}}
        # The relay's word on a Muse key: never the key itself.
        self.muse = {"enabled": False, "created_at": 0, "last_used": 0}
        self.keys = []

    def _maybe_fail(self, name):
        if name in self.fail:
            raise self.fail.pop(name)

    def status(self):
        self.calls.append(("status",))
        self._maybe_fail("status")
        return {"pairing": {"state": "confirmed", "tail": "4567"}, "pin_set": True,
                "accounts": self.accounts, "templates": {"ck_highlights_ready": "APPROVED"}, "muse": self.muse}

    def notify(self, body):
        self.calls.append(("notify", body["kind"]))
        self._maybe_fail("notify")
        self.notified.append(body)
        self.n += 1
        if body["kind"] == "ready":
            return {"approval_id": f"apr_{self.n:024x}", "channel": "template", "message_id": f"wamid.{self.n}"}
        return {"channel": "template", "message_id": f"wamid.{self.n}"}

    def commands(self):
        self.calls.append(("commands",))
        self._maybe_fail("commands")
        out, self.pending = self.pending, []
        return out

    def ack(self, command_id, outcome, reason=""):
        self.acks.append((command_id, outcome, reason))
        return {"ok": True}

    def preferences(self, prefs):
        self.calls.append(("preferences", prefs))
        self._maybe_fail("preferences")
        return {"prefs": prefs}

    def cancel(self, approval_id):
        self.calls.append(("cancel", approval_id))
        return {"cancelled": True}

    def muse_token(self):
        self.calls.append(("muse_token",))
        self._maybe_fail("muse_token")
        key = "ckm_" + os.urandom(24).hex()[:43]
        self.keys.append(key)
        self.muse = {"enabled": True, "created_at": 1_800_000_000, "last_used": 0}
        return {"key": key, "muse": self.muse}

    def muse_revoke(self):
        self.calls.append(("muse_revoke",))
        self._maybe_fail("muse_revoke")
        self.muse = {"enabled": False, "created_at": 0, "last_used": 0}
        return {"revoked": True, "muse": self.muse}

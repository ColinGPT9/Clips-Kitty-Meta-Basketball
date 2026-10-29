"""What a finished basketball video produced, counted from its clips.

The message on the creator's phone says things like "4 dunks, 3 threes and a
block". Every number in it is counted here from the clips the pipeline saved
(each basketball clip's `scores` carries the play it was cut around,
sports/core/clips.py mark), never written by a model: a notification that
says "3 dunks" when there were two is worse than no notification. Muse Spark
may add a line of colour to the approval page when the creator turns that on
(captions.py); it never replaces these counts.

The categories are the Sports toggle's own highlight choices
(config/sports.yaml, basketball highlights_choices), so "dunks" here means
exactly what "Dunks" means in the Generate bar. Each clip counts once, in the
first category it belongs to, so the counts add up to the number of clips.
"""

import hashlib
import json
from pathlib import Path

#: (choice id in sports.yaml, singular, plural), in the order a clip is
#: claimed: a game-winning three is clutch first, an alley-oop a dunk.
CATEGORIES = (
    ("clutch", "clutch shot", "clutch shots"),
    ("dunks", "dunk", "dunks"),
    ("threes", "three", "threes"),
    ("blocks", "block", "blocks"),
    ("steals", "steal", "steals"),
    ("assists", "assist", "assists"),
    ("fan_reactions", "reaction", "reactions"),
)
OTHER = ("other", "other play", "other plays")

# Used only if config/sports.yaml cannot be read, so a notification never
# fails over it. Kept to the same events as the file.
_FALLBACK_EVENTS = {
    "clutch": ["game_winner", "buzzer_beater", "game_tying", "go_ahead", "clutch_shot"],
    "dunks": ["dunk", "alley_oop", "poster_dunk", "putback"],
    "threes": ["made_3", "corner_three", "deep_three"],
    "blocks": ["block"],
    "steals": ["steal", "deflection"],
    "assists": ["assist", "alley_oop_assist", "no_look_pass", "dime", "cross_court_pass", "alley_oop"],
    "fan_reactions": ["celebrity_reaction", "courtside_reaction", "crowd_reaction", "fan_reaction",
                      "bench_reaction", "coach_reaction", "player_reaction"],
}


def category_events() -> dict[str, set[str]]:
    try:
        from sports import spec

        choices = spec("basketball").get("highlights_choices") or {}
        found = {cid: set(choices[cid].get("events") or []) for cid, _, _ in CATEGORIES
                 if isinstance(choices.get(cid, {}).get("events"), list)}
        if len(found) == len(CATEGORIES):
            return found
    except Exception:
        # No usable sport spec: fall back to the built-in lists below.
        pass
    return {k: set(v) for k, v in _FALLBACK_EVENTS.items()}


def scores_of(clip) -> dict:
    try:
        value = json.loads(clip["scores"] or "{}")
    except (TypeError, ValueError, KeyError, IndexError):
        return {}
    return value if isinstance(value, dict) else {}


def category_of(scores: dict, events: dict[str, set[str]] | None = None) -> str:
    events = events if events is not None else category_events()
    kind = str(scores.get("sport_event") or "")
    for cid, _, _ in CATEGORIES:
        if kind and kind in events.get(cid, ()):
            return cid
    return OTHER[0]


def is_basketball(job_payload: dict, clips) -> bool:
    """A job the Sports toggle sent as basketball, or whose clips say so."""
    sport = job_payload.get("sport")
    name = sport.get("name") if isinstance(sport, dict) else sport
    if str(name or "").lower() == "basketball":
        return True
    for clip in clips:
        try:
            opts = json.loads(clip["render_opts"] or "{}")
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        if isinstance(opts, dict) and opts.get("sport") == "basketball":
            return True
    return False


def snapshot(clips, *, limit: int) -> list[dict]:
    """The clips an offer is made of, best first, each pinned to the exact
    file on disk (size and mtime): what the phone approves is these files."""
    events = category_events()
    out = []
    for clip in sorted(clips, key=lambda c: (-(c["score"] or 0), c["start_s"])):
        path = Path(clip["path"] or "")
        if clip["status"] in ("deleted", "rejected") or not path.is_file():
            continue
        stat = path.stat()
        scores = scores_of(clip)
        out.append({
            "clip_id": int(clip["id"]),
            "start_s": float(clip["start_s"]),
            "end_s": float(clip["end_s"]),
            "duration": round(float(clip["end_s"]) - float(clip["start_s"]), 1),
            "score": int(clip["score"] or 0),
            "path": str(path),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "title": str(clip["title"] or clip["hook"] or "").strip()[:120],
            "label": str(scores.get("sport_label") or "").strip()[:60],
            "category": category_of(scores, events),
        })
        if len(out) >= limit:
            break
    return out


def fingerprint(snap: list[dict]) -> str:
    """One hash for exactly these files. Any re-render, trim or swap changes it."""
    key = [[c["clip_id"], c["start_s"], c["end_s"], c["size"], c["mtime_ns"]] for c in snap]
    return hashlib.sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest()[:32]


def file_matches(entry: dict) -> bool:
    """Is the file on disk still the one this snapshot entry was made from?"""
    path = Path(entry.get("path") or "")
    try:
        stat = path.stat()
    except OSError:
        return False
    return int(stat.st_size) == int(entry["size"]) and int(stat.st_mtime_ns) == int(entry["mtime_ns"])


def counts(clips) -> dict[str, int]:
    events = category_events()
    out: dict[str, int] = {}
    for clip in clips:
        cid = clip["category"] if isinstance(clip, dict) and "category" in clip \
            else category_of(scores_of(clip), events)
        out[cid] = out.get(cid, 0) + 1
    return out


def counts_text(found: dict[str, int]) -> str:
    """"4 dunks, 3 threes and 1 block": counted, in a fixed order, plain words.

    No newlines and no leading or trailing parameter text, so it can go
    straight into a WhatsApp template's body parameter."""
    parts = []
    for cid, one, many in (*CATEGORIES, OTHER):
        n = found.get(cid, 0)
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    if not parts:
        return "no highlights"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def build(job_title: str, all_clips, offered: list[dict]) -> dict:
    """The summary every message and the approval page are written from."""
    found = counts(offered)
    total = len(list(all_clips))
    title = (job_title or "your basketball video").strip()
    return {
        "title": title[:80],
        "total_clips": total,
        "offered": len(offered),
        "counts": found,
        "counts_text": counts_text(found),
        "duration_s": round(sum(c["duration"] for c in offered), 1),
    }


def ready_text(summary: dict) -> str:
    """The sentence the WhatsApp message carries. Deterministic: the same
    clips always give the same words."""
    n = summary["offered"]
    clips = "clip" if n == 1 else "clips"
    extra = ""
    if summary["total_clips"] > n:
        extra = f" (best {n} of {summary['total_clips']})"
    return f"{n} {clips}{extra}: {summary['counts_text']}"

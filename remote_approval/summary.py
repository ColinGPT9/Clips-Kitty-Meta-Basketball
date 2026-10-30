"""What a finished video produced, counted from its clips.

The message on the creator's phone says things like "4 dunks, 3 threes and a
block", "2 chases and a crash" or "3 highlights". Every number in it is
counted here from the clips the pipeline saved, never written by a model: a
notification that says "3 dunks" when there were two is worse than no
notification. Muse Spark may add a line of colour to the approval page when
the creator turns that on (captions.py); it never replaces these counts.

An offer is one of three kinds (kind_of), and each counts in its own words:

* basketball: each clip's `scores` carries the play it was cut around
  (sports/core/clips.py mark), counted in the Sports toggle's own highlight
  choices (config/sports.yaml, basketball highlights_choices), so "dunks"
  here means exactly what "Dunks" means in the Generate bar;
* gaming: a game's own pipeline's clip carries the label the pipeline gave
  it (plugins/runner.py, scores.plugin_label), counted in PLUGIN_LABELS;
* any other video: its clips are highlights, and are counted as that.

Each clip counts once, in the first category it belongs to, so the counts
add up to the number of clips.
"""

import hashlib
import json
import re
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
#: A moment the crowd and the commentator's voice marked but nothing named
#: (sports/core/detect.py): a big moment, not a play of a kind we don't know.
BIG_MOMENT = ("big_moment", "big moment", "big moments")
OTHER = ("other", "other play", "other plays")

#: (plugin_label, singular, plural): the labels the GTA pipeline declares for
#: what it finds itself (plugins/catalog_index.json, its "events"). Its
#: model-assisted funny_dialogue, any label not listed here and a clip with
#: no label all count as highlights.
PLUGIN_LABELS = (
    ("chase", "chase", "chases"),
    ("crash", "crash", "crashes"),
    ("stunt", "stunt", "stunts"),
    ("combat", "combat moment", "combat moments"),
    ("npc_chaos", "NPC chaos moment", "NPC chaos moments"),
    ("roleplay", "roleplay moment", "roleplay moments"),
    ("reaction", "reaction", "reactions"),
    ("milestone", "milestone", "milestones"),
)
HIGHLIGHT = ("highlight", "highlight", "highlights")

#: Every category, in the order a message names them.
_ORDER = (*CATEGORIES, BIG_MOMENT, OTHER, *PLUGIN_LABELS, HIGHLIGHT)

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


def _opts_of(clip) -> dict:
    try:
        value = json.loads(clip["render_opts"] or "{}")
    except (TypeError, ValueError, KeyError, IndexError):
        return {}
    return value if isinstance(value, dict) else {}


def _label_key(label) -> str:
    """A plugin's label as PLUGIN_LABELS spells it: "NPC chaos" is npc_chaos."""
    return re.sub(r"[\s-]+", "_", str(label or "").strip().lower())


def category_of(scores: dict, events: dict[str, set[str]] | None = None, kind: str = "basketball") -> str:
    """The category one clip counts in, in an offer of this kind."""
    if kind == "gaming":
        label = _label_key(scores.get("plugin_label"))
        return next((cid for cid, _, _ in PLUGIN_LABELS if cid == label), HIGHLIGHT[0])
    if kind != "basketball":
        return HIGHLIGHT[0]
    events = events if events is not None else category_events()
    play = str(scores.get("sport_event") or "")
    for cid, _, _ in CATEGORIES:
        if play and play in events.get(cid, ()):
            return cid
    if play == BIG_MOMENT[0]:
        return BIG_MOMENT[0]
    return OTHER[0]


def label_of(scores: dict) -> str:
    """What a clip was cut around, in a few words: the sport's own label, or
    else its plugin's, in plain words ("npc_chaos" reads "NPC chaos moment")."""
    label = str(scores.get("sport_label") or "").strip()
    if not label and scores.get("plugin_label"):
        key = _label_key(scores["plugin_label"])
        label = next((one for cid, one, _ in PLUGIN_LABELS if cid == key), "") \
            or re.sub(r"[_\s-]+", " ", str(scores["plugin_label"])).strip()
        label = label[:1].upper() + label[1:]
    return label[:60]


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


def kind_of(job_payload: dict, clips, data_dir=None) -> str:
    """"basketball", "gaming" or "video": the words an offer of this job's
    clips is written in. Gaming is a job sent as Gaming / Reaction or with
    gaming scoring, or one a game's own pipeline found: a plugin installed in
    `data_dir` whose category is gaming, as the pipeline itself tells them
    apart (plugins/gaming.py is_detector). Any other pipeline finds moments
    in any kind of video, so its clips are a video's, as is anything that is
    neither basketball nor gaming."""
    if is_basketball(job_payload, clips):
        return "basketball"
    if job_payload.get("gaming") or job_payload.get("gaming_scoring"):
        return "gaming"
    found = sorted(_plugins_of(job_payload, clips)) if data_dir is not None else []
    if any(_game_pipeline(pid, data_dir) for pid in found):
        return "gaming"
    return "video"


def _plugins_of(job_payload: dict, clips) -> set[str]:
    """The plugin pipelines a job named or its clips were found by."""
    choice = job_payload.get("pipeline")
    found = {str((choice.get("id") if isinstance(choice, dict) else choice) or "")}
    found |= {str(scores_of(clip).get("plugin") or "") for clip in clips}
    return found - {""}


def _game_pipeline(plugin_id: str, data_dir) -> bool:
    """A game's own pipeline: installed here with the gaming category. One
    that can't be read is not, so nothing is called gaming on a guess."""
    try:
        from plugins import gaming

        return bool(gaming.is_detector(plugin_id, data_dir))
    except Exception:
        return False


def offerable(clip) -> bool:
    """A short 9:16 clip whose file is on disk, the only kind an offer holds.
    A Longform cut (render_opts.profile, 16:9) and a story reel
    (render_opts.reel, scores.sport_reel) are not Reels."""
    if clip["status"] in ("deleted", "rejected") or not Path(clip["path"] or "").is_file():
        return False
    opts = _opts_of(clip)
    if opts.get("profile") or opts.get("reel"):
        return False
    return not scores_of(clip).get("sport_reel")


def snapshot(clips, *, limit: int, kind: str = "basketball", published: dict | None = None) -> list[dict]:
    """The clips an offer is made of, best first, each pinned to the exact
    file on disk (size and mtime): what the phone approves is these files.

    `published` ({clip_id: [platform, ...]}) is where a clip is already
    posted. It is kept on the clip's entry, and the clip is not posted there
    again (commands.queue_publishes)."""
    events = category_events() if kind == "basketball" else {}
    out = []
    for clip in sorted(clips, key=lambda c: (-(c["score"] or 0), c["start_s"])):
        if not offerable(clip):
            continue
        path = Path(clip["path"])
        stat = path.stat()
        scores = scores_of(clip)
        entry = {
            "clip_id": int(clip["id"]),
            "start_s": float(clip["start_s"]),
            "end_s": float(clip["end_s"]),
            "duration": round(float(clip["end_s"]) - float(clip["start_s"]), 1),
            "score": int(clip["score"] or 0),
            "path": str(path),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "title": str(clip["title"] or clip["hook"] or "").strip()[:120],
            "label": label_of(scores),
            "category": category_of(scores, events, kind),
        }
        done = list((published or {}).get(int(clip["id"])) or [])
        if done:
            entry["published"] = done
        out.append(entry)
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


def counts(clips, kind: str = "basketball") -> dict[str, int]:
    events = category_events() if kind == "basketball" else {}
    out: dict[str, int] = {}
    for clip in clips:
        cid = clip["category"] if isinstance(clip, dict) and "category" in clip \
            else category_of(scores_of(clip), events, kind)
        out[cid] = out.get(cid, 0) + 1
    return out


def counts_text(found: dict[str, int]) -> str:
    """"4 dunks, 3 threes and 1 block": counted, in a fixed order, plain words.

    No newlines and no leading or trailing parameter text, so it can go
    straight into a WhatsApp template's body parameter."""
    parts = []
    for cid, one, many in _ORDER:
        n = found.get(cid, 0)
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    if not parts:
        return "no highlights"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def build(job_title: str, all_clips, offered: list[dict], kind: str = "basketball") -> dict:
    """The summary every message and the approval page are written from.
    `all_clips` are the clips the run made; "best 3 of 5" counts the ones an
    offer could hold."""
    found = counts(offered, kind)
    total = sum(1 for clip in all_clips if offerable(clip))
    title = (job_title or "your video").strip()
    return {
        "title": title[:80],
        "kind": kind,
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
    extra = ""
    if summary["total_clips"] > n:
        extra = f" (best {n} of {summary['total_clips']})"
    if n and set(summary.get("counts") or {}) == {HIGHLIGHT[0]}:
        # Nothing labelled: "3 highlights", not "3 clips: 3 highlights".
        return f"{n} {HIGHLIGHT[1] if n == 1 else HIGHLIGHT[2]}{extra}"
    clips = "clip" if n == 1 else "clips"
    return f"{n} {clips}{extra}: {summary['counts_text']}"

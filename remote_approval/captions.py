"""The words that go with each clip on Instagram, Facebook and Threads.

Muse Spark writes them when the creator turns that on: ONE structured request
per offer, covering every offered clip and every platform at once (titles,
Instagram captions, Facebook descriptions, Threads posts, hashtags, and a
one-line summary of the game), held to a strict JSON schema. The answer is
stored on the offer and reused: opening the approval page, a reminder, a
retry or a new offer of the same clips never asks again, and a routine
confirmation never asks at all. Its token use and cost are recorded from the
API's own `usage` block (usage.py).

The model is told only what the pipeline found (the play type, the clip's
title, the team or player the scoreboard named, the video's title) and told
not to invent scores, names or stats. What comes back is checked: a clip id
it was not given is dropped, lengths are capped, hashtags are cleaned.

Without Muse Spark, or if it fails, the text is built from what the clip
already has (its title, description and hashtags from the pipeline's own
metadata step), so publishing never waits on a model.
"""

import json
import re

from remote_approval import store, usage

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "clips": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "clip_id": {"type": "integer"},
                "title": {"type": "string"},
                "instagram_caption": {"type": "string"},
                "facebook_description": {"type": "string"},
                "threads_post": {"type": "string"},
                "hashtags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["clip_id", "title", "instagram_caption", "facebook_description",
                         "threads_post", "hashtags"],
            "additionalProperties": False,
        }},
    },
    "required": ["summary", "clips"],
    "additionalProperties": False,
}

INSTRUCTIONS = (
    "You write social posts for a basketball highlights creator. For each clip below, write: "
    "a short title (max 80 characters); an Instagram Reels caption (1-3 sentences, then hashtags); "
    "a Facebook Reels description (1-3 sentences); a Threads post (one sentence, max 200 "
    "characters, no hashtags); and 3 to 6 hashtags without spaces. Also write `summary`: one "
    "sentence (max 140 characters) describing the set of highlights.\n"
    "Rules: use ONLY the facts given for each clip. Never invent a score, a statistic, a player "
    "name, a team, a date or a result that is not given. If no player or team is given, describe "
    "the play itself. No emojis in titles. Keep the tone energetic but plain. Answer with JSON "
    "matching the schema, with one entry per clip_id given and no others."
)

MAX_TOKENS = 4000


def facts(review: dict, clip_rows: dict) -> list[dict]:
    """What the model is told about each clip: the pipeline's own findings."""
    from remote_approval.summary import scores_of

    out = []
    for entry in review["clips"]:
        row = clip_rows.get(entry["clip_id"])
        scores = scores_of(row) if row is not None else {}
        item = {
            "clip_id": entry["clip_id"],
            "play": entry.get("label") or "highlight",
            "seconds": entry.get("duration"),
            "title_so_far": entry.get("title") or "",
        }
        for key, name in (("sport_team", "team"), ("sport_player", "player"), ("sport_when", "game_clock"),
                          ("sport_context", "situation")):
            if scores.get(key):
                item[name] = str(scores[key])[:80]
        out.append(item)
    return out


def generate(db, data_dir, review: dict, clip_rows: dict, *, model: str, send=None) -> dict:
    """One Muse Spark request for the whole offer. Returns the stored
    captions; raises LLMError (from the provider layer) when it cannot."""
    from llm.providers import keys
    from llm.providers.base import LLMError
    from llm.providers.catalog import get
    from llm.providers.http import send as http_send

    cached = reusable(db, review, model)
    if cached:
        return cached
    spec = get("meta")
    if spec is None:
        raise LLMError("not_configured", "Muse Spark is not available in this build.")
    spec, key = keys.resolve(data_dir, spec)
    if not key:
        raise LLMError("not_configured", "No Meta Model API key is saved. Add yours in Settings → AI.")
    prompt = {
        "video": review.get("title") or "",
        "counts": (review.get("summary") or {}).get("counts_text") or "",
        "clips": facts(review, clip_rows),
    }
    body = {
        "model": model,
        "max_completion_tokens": MAX_TOKENS,
        "reasoning_effort": "low",
        "messages": [
            {"role": "developer", "content": INSTRUCTIONS},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "clips_kitty_posts", "strict": True, "schema": SCHEMA}},
    }
    answer = (send or http_send)(spec, key, "POST", "/chat/completions", json_body=body)
    cost = usage.record_spark(db, model, answer.get("usage") or {})
    choice = ((answer.get("choices") or [{}])[0]) or {}
    content = ((choice.get("message") or {}).get("content")) or ""
    parsed = _parse(content, {c["clip_id"] for c in review["clips"]})
    if not parsed["clips"]:
        raise LLMError("bad_response", "Muse Spark's answer had no usable captions.")
    stored = {
        **parsed,
        "model": model,
        "fingerprint": review["fingerprint"],
        "generated_at": store.stamp(),
        "usage": answer.get("usage") or {},
        "estimated_usd": cost,
    }
    store.update_review(db, review["id"], captions=stored)
    store.audit(db, review["id"], "desktop", "muse_spark_captions",
                f"{len(parsed['clips'])} clip(s), {model}, about ${cost:.4f}")
    return stored


def reusable(db, review: dict, model: str) -> dict | None:
    """Captions already made for exactly these clips, on this offer or an
    earlier one of the same job, by the same model."""
    if review.get("captions", {}).get("fingerprint") == review["fingerprint"]:
        return review["captions"]
    for other in store.reviews_for_job(db, review["job_id"]):
        got = other.get("captions") or {}
        if got.get("fingerprint") == review["fingerprint"] and got.get("model") == model:
            store.update_review(db, review["id"], captions=got)
            return got
    return None


def _parse(content: str, wanted: set[int]) -> dict:
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", content or "", re.S)
        try:
            data = json.loads(match.group(0)) if match else {}
        except ValueError:
            data = {}
    clips = {}
    items = data.get("clips") if isinstance(data, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            cid = int(item.get("clip_id"))
        except (TypeError, ValueError):
            continue
        if cid not in wanted or cid in clips:
            continue
        clips[str(cid)] = {
            "title": _line(item.get("title"), 80),
            "instagram": _text(item.get("instagram_caption"), 1800),
            "facebook": _text(item.get("facebook_description"), 1800),
            "threads": _line(item.get("threads_post"), 200),
            "hashtags": hashtags(item.get("hashtags"), limit=6),
        }
    return {"summary": _line(data.get("summary") if isinstance(data, dict) else "", 140), "clips": clips}


def _line(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _text(value, limit: int) -> str:
    return re.sub(r"[ \t]+", " ", str(value or "")).strip()[:limit]


def hashtags(values, *, limit: int = 6) -> list[str]:
    out: list[str] = []
    for entry in values or []:
        for part in re.split(r"[#\s]+", str(entry).lower()):
            word = re.sub(r"[^\w]", "", part)
            if word and f"#{word}" not in out:
                out.append(f"#{word}")
    return out[:limit]


# ---- the text each platform gets ------------------------------------------------------


def texts_for(review: dict, clip_row, entry: dict) -> dict:
    """{"title", "instagram", "facebook", "threads"} for one clip: Muse
    Spark's when there is some for these exact clips, otherwise the clip's
    own metadata."""
    made = (review.get("captions") or {})
    mine = (made.get("clips") or {}).get(str(entry["clip_id"])) \
        if made.get("fingerprint") == review["fingerprint"] else None
    own_tags = []
    if clip_row is not None:
        try:
            own_tags = json.loads(clip_row["hashtags"] or "[]")
        except (TypeError, ValueError):
            own_tags = []
    if mine:
        tags = hashtags([*mine["hashtags"], "basketball"], limit=8)
        title = mine["title"] or entry.get("title") or entry.get("label") or "Basketball highlight"
        return {
            "title": title,
            "instagram": f"{mine['instagram']}\n\n{' '.join(tags)}".strip(),
            "facebook": f"{mine['facebook']}\n\n{' '.join(tags)}".strip(),
            "threads": mine["threads"] or title,
            "source": "muse_spark",
        }
    title = (entry.get("title") or (clip_row["hook"] if clip_row is not None else "")
             or entry.get("label") or "Basketball highlight").strip()
    description = str(clip_row["description"] or "").strip() if clip_row is not None else ""
    tags = hashtags([*(own_tags if isinstance(own_tags, list) else []), "basketball", "highlights"], limit=8)
    body = "\n\n".join(p for p in (title, description, " ".join(tags)) if p)
    return {
        "title": title[:100],
        "instagram": body,
        "facebook": body,
        "threads": title,
        "source": "clip",
    }

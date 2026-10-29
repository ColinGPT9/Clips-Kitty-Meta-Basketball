"""What the Meta services cost this install, counted as they are used.

The hackathon brief asks to track API usage and cost, and a creator paying
per message and per token should see it. Each count comes from the place
that knows: Muse Spark's own `usage` block (tokens, cached tokens), the audio
seconds sent to Muse Voice, and the relay's answer to each WhatsApp send
(template or free-form). Dollar figures are estimates from the published
prices below, labelled as estimates wherever they are shown.

Prices (USD), as published for October 2026:
* Muse Spark Standard: $1.25 / 1M input, $0.15 / 1M cached input,
  $4.25 / 1M output. Contributor: $0.10 / $0.002 / $0.20.
* Muse Voice Transcribe: $0.18 per audio hour.
* WhatsApp utility template, North America: about $0.0034 per delivered
  message. A free-form reply inside the 24-hour window is free.
"""

import json

KEY = "remote_approval_usage"

SPARK_PRICES = {  # per million tokens: (input, cached input, output)
    "standard": (1.25, 0.15, 4.25),
    "contributor": (0.10, 0.002, 0.20),
}
VOICE_PER_HOUR = 0.18
WHATSAPP_UTILITY = 0.0034


def load(db) -> dict:
    try:
        got = json.loads(db.get_flag(KEY, "") or "{}")
    except (TypeError, ValueError):
        got = {}
    got = got if isinstance(got, dict) else {}
    got.setdefault("muse_spark", {"requests": 0, "prompt_tokens": 0, "cached_tokens": 0,
                                  "completion_tokens": 0, "usd": 0.0})
    got.setdefault("muse_voice", {"requests": 0, "audio_seconds": 0.0, "usd": 0.0})
    got.setdefault("whatsapp", {"templates": 0, "free_form": 0, "usd": 0.0})
    got.setdefault("meta_graph", {"steps": 0, "uploads": 0, "bytes": 0})
    return got


def _save(db, data: dict) -> None:
    db.set_flag(KEY, json.dumps(data))


def spark_cost(model: str, prompt: int, cached: int, completion: int) -> float:
    rates = SPARK_PRICES["contributor" if "contributor" in model else "standard"]
    fresh = max(0, prompt - cached)
    return round((fresh * rates[0] + cached * rates[1] + completion * rates[2]) / 1_000_000, 6)


def record_spark(db, model: str, usage: dict) -> float:
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cost = spark_cost(model, prompt, cached, completion)
    data = load(db)
    s = data["muse_spark"]
    s["requests"] += 1
    s["prompt_tokens"] += prompt
    s["cached_tokens"] += cached
    s["completion_tokens"] += completion
    s["usd"] = round(s["usd"] + cost, 6)
    _save(db, data)
    return cost


def record_voice(db, audio_seconds: float, requests: int = 1) -> float:
    cost = round(audio_seconds / 3600 * VOICE_PER_HOUR, 6)
    data = load(db)
    v = data["muse_voice"]
    v["requests"] += requests
    v["audio_seconds"] = round(v["audio_seconds"] + audio_seconds, 1)
    v["usd"] = round(v["usd"] + cost, 6)
    _save(db, data)
    return cost


def record_whatsapp(db, channel: str) -> None:
    data = load(db)
    w = data["whatsapp"]
    if channel == "template":
        w["templates"] += 1
        w["usd"] = round(w["usd"] + WHATSAPP_UTILITY, 6)
    elif channel:
        w["free_form"] += 1
    _save(db, data)


def record_graph(db, *, steps: int = 0, uploads: int = 0, size: int = 0) -> None:
    data = load(db)
    g = data["meta_graph"]
    g["steps"] += steps
    g["uploads"] += uploads
    g["bytes"] += size
    _save(db, data)


def summary(db) -> dict:
    data = load(db)
    total = data["muse_spark"]["usd"] + data["muse_voice"]["usd"] + data["whatsapp"]["usd"]
    return {**data, "estimated_usd": round(total, 4)}

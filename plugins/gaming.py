"""Add an installed gaming detector's unique moments to normal Gaming output.

The host owns selection limits and deduplication; game-specific recognition
stays in the external SDK pipeline. Existing Gaming moments retain priority.
"""

from analysis.highlights import _select_unique


def is_detector(choice, data_dir):
    from plugins import store

    try:
        installed = store.get(data_dir, store.clean_choice(choice)["id"])
    except ValueError:
        return False
    return installed is not None and installed.manifest.get("category") == "gaming"


def supplement(candidates, additions, segments, config):
    if not additions:
        return candidates, []
    clips = config.get("clips") or {}
    analysis = config.get("analysis") or {}
    original = {id(candidate) for candidate in candidates}
    every = [*candidates, *additions]
    cap = clips.get("max_clips_per_video", 0)
    return _select_unique(
        every, segments,
        min_score=clips.get("min_score", 0),
        max_clips=cap if cap > 0 else len(every),
        max_overlap=analysis.get("max_overlap", 0.2),
        max_text_similarity=analysis.get("max_text_similarity", 0.7),
        max_segment_reuse=analysis.get("max_segment_reuse", 0.5),
        priority=lambda candidate: (id(candidate) in original, candidate.score),
    )

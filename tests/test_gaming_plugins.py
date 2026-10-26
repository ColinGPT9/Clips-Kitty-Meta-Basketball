"""Game detector additions retain normal Gaming moments and shared limits."""

from core.models import ClipCandidate, Segment
from plugins.gaming import supplement


def clip(start, score=75, source="transcript"):
    return ClipCandidate(start, start + 15, score, source=source)


def test_extra_event_is_added_without_replacing_normal_gaming_moment():
    ordinary = clip(10)
    extra = clip(70, 90, "plugin")
    kept, rejected = supplement([ordinary], [extra], [Segment(10, 25, "A funny conversation"),
                                                       Segment(70, 85, "A vehicle incident")], {})
    assert kept == [ordinary, extra] and not rejected


def test_a_duplicate_extra_does_not_replace_or_rescore_the_existing_moment():
    ordinary = clip(10)
    duplicate = clip(12, 99, "plugin")
    kept, rejected = supplement([ordinary], [duplicate], [], {})
    assert kept == [ordinary] and ordinary.score == 75
    assert len(rejected) == 1 and rejected[0].candidate is duplicate
    assert rejected[0].reason == "timestamp_overlap"


def test_no_extra_events_leaves_the_original_list_and_order_unchanged():
    original = [clip(50, 65), clip(10, 90)]
    kept, rejected = supplement(original, [], [], {})
    assert kept is original and not rejected


def test_extra_events_obey_existing_quality_and_count_limits():
    ordinary = clip(10)
    good, low = clip(70, 90, "plugin"), clip(100, 40, "plugin")
    kept, rejected = supplement([ordinary], [good, low], [], {
        "clips": {"min_score": 55, "max_clips_per_video": 1},
    })
    assert kept == [ordinary]
    assert {r.reason for r in rejected} == {"over_limit", "below_min_score"}

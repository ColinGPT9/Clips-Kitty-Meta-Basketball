"""Game-specific finders keep Gaming's measured evidence and local vision."""

from types import SimpleNamespace

import pytest

from core.models import ClipCandidate, Segment
from plugins import gaming

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")
fusion = pytest.importorskip("analysis.fusion")
game_vision = pytest.importorskip("analysis.game_vision")


def test_detector_windows_reuse_gaming_vision_and_keep_plugin_provenance(monkeypatch, tmp_path):
    segments = [Segment(1, 9, "A made-up game event")]
    profile = SimpleNamespace(reads_screen=True)
    source = SimpleNamespace(path=tmp_path / "stream.mp4")
    candidates = [ClipCandidate(start=1, end=9, score=60,
                                subscores={"plugin": "example-dev/game", "plugin_label": "stunt"}),
                  ClipCandidate(start=12, end=20, score=70,
                                subscores={"plugin": "example-dev/game", "plugin_label": "chase"})]
    calls = []

    def read_screen(path, curve, given_segments, clips, given_profile):
        assert path == source.path and given_segments is segments and given_profile is profile
        assert curve.size
        calls.append("ocr")
        return SimpleNamespace(events=[(3, "ON SCREEN: round complete")], menus=[])

    def look(clips, path, llm, given_profile, given_segments, events, **_kwargs):
        assert clips is candidates and path == source.path
        assert given_profile is profile and given_segments is segments
        assert (3, "ON SCREEN: round complete") in events
        assert (4, "CHAT: cheering") in events
        assert (5, "GAME SOUND: impact") in events
        calls.append("vision")
        clips[0].score = 50
        clips[0].subscores["seen"] = -10
        clips[1].score = 75
        clips[1].subscores["seen"] = 5
        return 2

    monkeypatch.setattr(fusion, "_read_screen", read_screen)
    monkeypatch.setattr(game_vision, "can_see", lambda _llm: True)
    monkeypatch.setattr(game_vision, "look_at", look)
    signal = np.linspace(0, 1, 24, dtype=np.float32)
    kept, rejected = gaming.review(
        candidates, video=source, segments=segments,
        llm=SimpleNamespace(sees_images=lambda: True, name="existing local vision model"),
        config={"clips": {"min_score": 55, "min_duration": 8, "max_duration": 60}}, profile=profile,
        signals=({"spike": signal}, {"motion": signal}),
        chat=SimpleNamespace(curve=signal, events=[(4, "CHAT: cheering")]),
        sounds=SimpleNamespace(game=signal, people=signal, events=[(5, "GAME SOUND: impact")]),
    )
    assert calls == ["ocr", "vision"]
    assert [(c.start, c.end, c.score) for c in kept] == [(12, 20, 75)]
    assert rejected[0].candidate is candidates[0] and rejected[0].reason == "below_min_score"
    assert kept[0].subscores["plugin_label"] == "chase"
    assert kept[0].subscores["plugin"] == "example-dev/game"
    assert all(name in kept[0].subscores for name in ("audio", "visual", "game", "seen"))
    assert "reaction_measured" not in kept[0].subscores


def test_optional_models_follow_gaming_settings_without_creating_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(fusion, "_read_screen", lambda *_a: pytest.fail("OCR was disabled"))
    monkeypatch.setattr(fusion, "_look_at_game", lambda *_a: pytest.fail("Vision was disabled"))
    candidate = ClipCandidate(start=2, end=12, score=82, subscores={"plugin": "example-dev/game"})
    kept, rejected = gaming.review(
        [candidate], video=SimpleNamespace(path=tmp_path / "stream.mp4"), segments=[], llm=None,
        profile=SimpleNamespace(),
        config={"clips": {"min_score": 55}, "scoring": {"read_screen": False, "look_at_game": False}},
    )
    assert kept == [candidate] and not rejected
    assert candidate.score == 82 and candidate.subscores == {"plugin": "example-dev/game"}
    assert gaming.review([], video=None, segments=[], llm=None, config={}, profile=None) == ([], [])


def test_text_only_model_keeps_gta_windows_and_reports_the_shared_vision_skip(capsys, tmp_path):
    candidate = ClipCandidate(start=2, end=12, score=82)
    kept, rejected = gaming.review(
        [candidate], video=SimpleNamespace(path=tmp_path / "stream.mp4"), segments=[], llm=object(),
        profile=SimpleNamespace(reads_screen=False), config={"clips": {"min_score": 55}},
    )
    assert kept == [candidate] and not rejected and candidate.score == 82
    assert "isn't a local model that takes images" in capsys.readouterr().out

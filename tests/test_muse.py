"""Meta's models in the hackathon edition, against a faked Meta Model API:

* Muse Voice Transcribe through the existing online transcription path
  (transcription/cloud.py): turn-level timings kept as turns, no word
  timings made up, local Whisper taking over when Meta fails;
* Muse Spark writing every offered clip's titles and posts in ONE strict
  JSON request (remote_approval/captions.py), cached, with its cost counted.
"""

import json
import os
from types import SimpleNamespace

import pytest
from remote_approval_fake import FakeRelay, finished_job

from core.models import Segment
from llm.providers import http, keys
from llm.providers.base import LLMError
from remote_approval import captions, notifier, service, store, usage
from remote_approval import settings as ra_settings
from transcription import cloud, transcriber

KEY = "meta-test-key-0123456789"


class Response:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body
        self.headers = {}
        self.text = ""

    def json(self):
        return self._body


@pytest.fixture
def audio(monkeypatch, tmp_path):
    """No ffmpeg: a 600 s "video" in two 540 s parts of empty WAV files."""
    monkeypatch.setattr(cloud, "_duration", lambda path: 600.0)
    made = []

    def extract(video, out, start, seconds, wav=False):
        made.append((out.suffix, wav))
        out.write_bytes(b"RIFF")
        return out

    monkeypatch.setattr(cloud, "_extract", extract)
    monkeypatch.setattr(cloud, "_speech_in", lambda audio: None)
    monkeypatch.setattr(cloud.cancel, "check_active", lambda: None)
    monkeypatch.setattr(http, "sleep", lambda s: None)
    keys.save_key(tmp_path, "meta", KEY)
    return made


def _voice(answers, calls):
    def transport(method, url, **kw):
        calls.append({"url": url, **kw})
        got = answers[min(len(calls) - 1, len(answers) - 1)]
        return got if isinstance(got, Response) else Response(got)
    return transport


# ---- Muse Voice Transcribe ---------------------------------------------------------------


def test_muse_voice_is_offered_as_turn_level_with_a_fallback():
    from llm.providers.catalog import get
    from llm.providers.speech import list_stt_models

    spec = get("meta")
    assert spec.stt["models"] == ["muse-voice-transcribe-1.0"]
    assert spec.stt["word_timings"] is False and spec.stt["fallback_local"] is True
    assert spec.stt["chunk_seconds"] + 2 * cloud.OVERLAP <= 600  # Meta's 10-minute limit per request
    listed = list_stt_models(spec, KEY)
    assert listed[0].id == "muse-voice-transcribe-1.0" and "not each word" in listed[0].note


def test_each_part_is_one_wav_request_in_endpointing_mode(audio, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(http, "transport", _voice([{"turns": [], "transcript": ""}], calls))
    online = {"backend": "meta", "data_dir": str(tmp_path)}
    cloud.transcribe(tmp_path / "v.mp4", "vid1", tmp_path / "t", online, language="es",
                     hotwords="Scottie Barnes, Toronto Raptors")
    assert audio == [(".wav", True), (".wav", True)]
    assert len(calls) == 2
    call = calls[0]
    assert call["url"] == "https://api.meta.ai/v1/asr/transcribe"
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    _none, request, kind = call["files"]["request"]
    assert kind == "application/json"
    assert json.loads(request) == {"model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV",
                                   "mode": "ENDPOINTING", "languageBias": ["Spanish"],
                                   "keywords": ["Scottie Barnes", "Toronto Raptors"]}
    assert call["files"]["audio"][2] == "audio/wav"


def test_turns_become_segments_in_video_time_with_no_made_up_word_timings(audio, monkeypatch, tmp_path):
    calls = []
    answers = [
        {"transcript": "Barnes drives. And the dunk!", "audioDurationMs": 544000, "turns": [
            {"turnId": 0, "startMs": 1500, "endMs": 3200, "transcript": "Barnes drives."},
            {"turnId": 1, "startMs": 3400, "endMs": 5000, "transcript": "And the dunk!"},
        ]},
        # Part 2's audio starts at 538 s (2 s of overlap): a turn at 1.0 s is
        # 539 s, inside part 1, so it is not counted twice.
        {"transcript": "dunk! What a finish.", "turns": [
            {"turnId": 0, "startMs": 1000, "endMs": 1800, "transcript": "dunk!"},
            {"turnId": 1, "startMs": 4000, "endMs": 6000, "transcript": "What   a finish."},
        ]},
    ]
    monkeypatch.setattr(http, "transport", _voice(answers, calls))
    online = {"backend": "meta", "data_dir": str(tmp_path)}
    segments = cloud.transcribe(tmp_path / "v.mp4", "vid2", tmp_path / "t", online)
    assert [(s.start, s.end, s.text) for s in segments] == [
        (1.5, 3.2, "Barnes drives."), (3.4, 5.0, "And the dunk!"), (542.0, 544.0, "What a finish.")]
    assert all(s.words is None for s in segments)
    saved = json.loads((tmp_path / "t" / "vid2.json").read_text(encoding="utf-8"))
    assert saved["word_timings"] is False
    assert saved["source"] == "meta/muse-voice-transcribe-1.0"
    assert saved["audio_seconds"] == pytest.approx(544 + 62, abs=0.5)
    assert all(seg["words"] is None for seg in saved["segments"])


def test_turns_outside_detected_speech_are_dropped(audio, monkeypatch, tmp_path):
    monkeypatch.setattr(cloud, "_speech_in", lambda a: [(0.0, 4.0)])
    calls = []
    monkeypatch.setattr(http, "transport", _voice([{"turns": [
        {"startMs": 1000, "endMs": 2000, "transcript": "Real words."},
        {"startMs": 100000, "endMs": 101000, "transcript": "Thank you."},
    ]}], calls))
    segments = cloud.transcribe(tmp_path / "v.mp4", "vid3", tmp_path / "t",
                                {"backend": "meta", "data_dir": str(tmp_path)})
    assert [s.text for s in segments] == ["Real words."]


def test_text_without_turn_timings_is_refused_not_given_a_made_up_time(audio, monkeypatch, tmp_path):
    monkeypatch.setattr(http, "transport", _voice([{"transcript": "Hello there", "turns": []}], []))
    with pytest.raises(LLMError) as err:
        cloud.transcribe(tmp_path / "v.mp4", "vid4", tmp_path / "t", {"backend": "meta", "data_dir": str(tmp_path)})
    assert err.value.kind == "bad_response"


def test_the_audio_sent_is_counted_with_its_estimated_cost(audio, monkeypatch, tmp_path, db):
    monkeypatch.setattr(http, "transport", _voice([{"turns": []}], []))
    cloud.transcribe(tmp_path / "v.mp4", "vid5", tmp_path / "t", {"backend": "meta", "data_dir": str(tmp_path)})
    voice = usage.load(db)["muse_voice"]
    assert voice["requests"] == 1 and voice["audio_seconds"] == pytest.approx(606, abs=0.5)
    assert voice["usd"] == pytest.approx(606 / 3600 * 0.18, abs=1e-4)


def _fake_whisper(monkeypatch):
    ran = []

    def run(model, path, language, hotwords=None):
        ran.append(hotwords)
        return [Segment(0.0, 1.0, "Local words.", [{"start": 0.0, "end": 1.0, "word": "Local"}])], \
            SimpleNamespace(language="en")

    monkeypatch.setattr(transcriber, "_load_model", lambda size, device: object())
    monkeypatch.setattr(transcriber, "_run", run)
    return ran


def test_whisper_takes_over_when_muse_voice_fails(audio, monkeypatch, tmp_path):
    ran = _fake_whisper(monkeypatch)
    monkeypatch.setattr(http, "transport", _voice([Response({"error": {"message": "bad key"}}, status=401)], []))
    segments = transcriber.transcribe(tmp_path / "v.mp4", "vid6", tmp_path / "t",
                                      online={"backend": "meta", "data_dir": str(tmp_path)}, hotwords="Barnes")
    assert [s.text for s in segments] == ["Local words."]
    assert ran == ["Barnes"]
    assert json.loads((tmp_path / "t" / "vid6.json").read_text(encoding="utf-8"))["segments"][0]["words"]


def test_other_providers_still_fail_the_job_as_before(audio, monkeypatch, tmp_path):
    ran = _fake_whisper(monkeypatch)
    keys.save_key(tmp_path, "openai", "sk-" + "a" * 40)
    monkeypatch.setattr(http, "transport", _voice([Response({"error": {"message": "nope"}}, status=401)], []))
    with pytest.raises(LLMError):
        transcriber.transcribe(tmp_path / "v.mp4", "vid7", tmp_path / "t",
                               online={"backend": "openai", "data_dir": str(tmp_path)})
    assert ran == []


# ---- Muse Spark captions ---------------------------------------------------------------------


def _answer(review, *, extra_clip=True):
    clips = [{"clip_id": c["clip_id"], "title": f"Big play {c['clip_id']}",
              "instagram_caption": "What a finish in the third.", "facebook_description": "A big finish.",
              "threads_post": "What a finish.", "hashtags": ["#NBA", "raptors", "Raptors", "#hoops daily"]}
             for c in review["clips"]]
    if extra_clip:
        clips.append({**clips[0], "clip_id": 999999})  # not offered: dropped
    return {
        "choices": [{"message": {"content": json.dumps({"summary": "Five plays from a fast game.",
                                                        "clips": clips})}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 800,
                  "prompt_tokens_details": {"cached_tokens": 200}},
    }


@pytest.fixture
def offer(db, tmp_path):
    keys.save_key(tmp_path, "meta", KEY)
    job = finished_job(db, tmp_path)
    ra_settings.save(db, {"whatsapp_enabled": True})
    review = store.get_review(db, notifier.on_job_finished(db, job, "done"))
    rows = {c["clip_id"]: db.get_clip(c["clip_id"]) for c in review["clips"]}
    return review, rows


def test_one_strict_json_request_covers_every_clip_and_platform(db, tmp_path, offer):
    review, rows = offer
    sent = []

    def send(spec, key, method, path, json_body=None, **kw):
        sent.append((spec.id, path, json_body))
        return _answer(review)

    made = captions.generate(db, tmp_path, review, rows, model="muse-spark-1.3", send=send)
    assert len(sent) == 1
    spec_id, path, body = sent[0]
    assert (spec_id, path) == ("meta", "/chat/completions")
    assert body["model"] == "muse-spark-1.3" and body["reasoning_effort"] == "low"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["messages"][0]["role"] == "developer" and "Never invent" in body["messages"][0]["content"]
    told = json.loads(body["messages"][1]["content"])
    assert told["video"] == "Raptors vs Celtics" and len(told["clips"]) == len(review["clips"])
    assert set(made["clips"]) == {str(c["clip_id"]) for c in review["clips"]}  # 999999 dropped
    first = made["clips"][str(review["clips"][0]["clip_id"])]
    assert first["hashtags"] == ["#nba", "#raptors", "#hoops", "#daily"]
    assert made["summary"] == "Five plays from a fast game."
    assert made["estimated_usd"] == pytest.approx(usage.spark_cost("muse-spark-1.3", 1200, 200, 800))


def test_captions_are_made_once_and_reused(db, tmp_path, offer):
    review, rows = offer
    sent = []

    def send(*a, **kw):
        sent.append(1)
        return _answer(review)

    captions.generate(db, tmp_path, review, rows, model="muse-spark-1.3", send=send)
    again = store.get_review(db, review["id"])
    captions.generate(db, tmp_path, again, rows, model="muse-spark-1.3", send=send)
    # A new offer of the same clips (asked again after it expired) reuses them too.
    store.move(db, review["id"], "EXPIRED", actor="test")
    new = store.get_review(db, service.ask_again(db, review["id"]))
    captions.generate(db, tmp_path, new, rows, model="muse-spark-1.3", send=send)
    assert len(sent) == 1
    spark = usage.load(db)["muse_spark"]
    assert spark["requests"] == 1 and spark["prompt_tokens"] == 1200 and spark["completion_tokens"] == 800


def test_each_platform_gets_muse_text_and_the_clip_text_is_the_fallback(db, tmp_path, offer):
    review, rows = offer
    entry = review["clips"][0]
    plain = captions.texts_for(review, rows[entry["clip_id"]], entry)
    assert plain["source"] == "clip" and plain["title"] == entry["title"]
    captions.generate(db, tmp_path, review, rows, model="muse-spark-1.3", send=lambda *a, **k: _answer(review))
    review = store.get_review(db, review["id"])
    made = captions.texts_for(review, rows[entry["clip_id"]], entry)
    assert made["source"] == "muse_spark"
    assert made["title"] == f"Big play {entry['clip_id']}"
    assert made["instagram"].startswith("What a finish in the third.") and "#basketball" in made["instagram"]
    assert made["threads"] == "What a finish."


@pytest.fixture
def gaming_offer(db, tmp_path, plugin_source, install_plugin):
    """A gaming stream a game's own pipeline clipped (an installed plugin
    whose category is gaming), offered once the creator opted in to gaming
    and other videos."""
    keys.save_key(tmp_path, "meta", KEY)
    install_plugin(tmp_path, plugin_source.folder("pipeline", plugin_source.manifest(
        id="someone/some-pipeline", category="gaming")))
    ra_settings.save(db, {"whatsapp_enabled": True, "basketball_only": False})
    db.upsert_video("vid_stream", title="Weekend stream")
    payload = {"url": "https://example.com/stream.mp4", "pipeline": {"id": "someone/some-pipeline"}}
    job_id = db.add_job("process", json.dumps(payload), video_id="vid_stream", title="Weekend stream")
    folder = tmp_path / "stream"
    folder.mkdir()
    for i, label in enumerate(("chase", "crash")):
        path = folder / f"clip{i}.mp4"
        path.write_bytes(os.urandom(4096 + i))
        db.add_clip("vid_stream", 30.0 * i, 30.0 * i + 20, 90 - i, "Hook", path=str(path),
                    hashtags=json.dumps(["#gta6", "#gtarp"]),
                    scores=json.dumps({"plugin": "someone/some-pipeline", "plugin_label": label}))
    db.finish_job(job_id, "done")
    review = store.get_review(db, notifier.on_job_finished(db, db.get_job(job_id), "done", data_dir=tmp_path))
    rows = {c["clip_id"]: db.get_clip(c["clip_id"]) for c in review["clips"]}
    return review, rows


def test_a_gaming_offer_is_written_as_gaming_and_never_as_basketball(db, tmp_path, gaming_offer):
    review, rows = gaming_offer
    sent = []

    def send(spec, key, method, path, json_body=None, **kw):
        sent.append(json_body)
        return _answer(review, extra_clip=False)

    captions.generate(db, tmp_path, review, rows, model="muse-spark-1.3", send=send)
    developer, user = sent[0]["messages"]
    assert developer["content"] == captions.INSTRUCTIONS_FOR["gaming"]
    assert "Never invent" in developer["content"] and "gaming" in developer["content"]
    assert "basketball" not in json.dumps(sent[0]).lower()
    told = json.loads(user["content"])
    assert [c["moment"] for c in told["clips"]] == ["Chase", "Crash"] and "play" not in told["clips"][0]
    assert told["counts"] == "1 chase and 1 crash"
    review = store.get_review(db, review["id"])
    entry = review["clips"][0]
    made = captions.texts_for(review, rows[entry["clip_id"]], entry)
    assert made["source"] == "muse_spark"
    assert "#gaming" in made["instagram"] and "#gta6" in made["instagram"] and "#basketball" not in made["instagram"]


def test_without_muse_spark_each_kind_of_offer_has_its_own_words():
    row = {"hashtags": json.dumps(["#gta6"]), "hook": "", "description": ""}
    entry = {"clip_id": 1, "title": "", "label": ""}

    def texts(summary):
        return captions.texts_for({"summary": summary, "fingerprint": "f" * 32}, row, entry)

    basketball, gaming, video = (texts({"kind": k}) for k in ("basketball", "gaming", "video"))
    assert basketball["title"] == "Basketball highlight"
    assert basketball["instagram"].endswith("#gta6 #basketball #highlights")
    assert gaming["title"] == "Gaming highlight" and gaming["instagram"].endswith("#gta6 #gaming")
    assert video["title"] == "Highlight" and video["instagram"].endswith("#gta6")
    assert "basketball" not in json.dumps([gaming, video]).lower()
    assert "gaming" not in json.dumps(video).lower()
    # An offer made before offers had a kind was a basketball one.
    assert texts({})["title"] == "Basketball highlight"
    for kind, words in captions.INSTRUCTIONS_FOR.items():
        assert "Never invent" in words, kind
    assert "basketball" not in captions.INSTRUCTIONS_FOR["video"].lower()


def test_an_offer_never_waits_on_muse_spark(db, tmp_path, offer, monkeypatch):
    review, _rows = offer
    ra_settings.save(db, {"muse_captions": True, "muse_summary": True})

    def down(*a, **kw):
        raise LLMError("provider_down", "Meta took too long to answer.")

    monkeypatch.setattr(http, "transport", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    monkeypatch.setattr(captions, "generate", lambda *a, **k: down())
    relay = FakeRelay()
    service.flush_outbox(db, tmp_path, relay)
    sent = relay.notified[0]
    assert sent["muse_line"] == ""
    assert all(c["text_source"] == "clip" for c in sent["clips"])
    assert any(r["action"] == "muse_spark_skipped" for r in store.audit_log(db, review["id"], 50))

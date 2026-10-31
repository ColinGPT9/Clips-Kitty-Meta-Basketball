"""Transcription online, on the user's own API key.

Local Whisper (transcription/transcriber.py) is the default and this does not
touch it. This is the opt-in for a PC too slow to transcribe long videos: the
audio is sent, in chunks, to the provider the user chose, with their key.

The result has to be indistinguishable from a local transcript to everything
downstream, so it is written to the same cache file in the same shape:
{"video_id", "language", "segments": [{"start", "end", "text", "words"}]},
with words as {"start", "end", "word"} and punctuation on the words, in
absolute seconds from the start of the video. Captions, filler cuts, the
editor's word tools, sentence snapping and translation all read that.

Only providers that return word timings are offered: without them captions
would drift and word editing would vanish. A part where nothing is said (a
stream's music-only intro: measured, 10 minutes of one) has no words and
that's fine; a part with text but no word timings is the model's limit.

Whisper makes words up where nobody speaks: "Thank you." 640 times in one
two-hour stream, "谢谢大家" and the like, one in every quiet stretch. Local
Whisper never shows them because faster-whisper runs a voice detector
(Silero) first. The same detector runs here, on each part before it is
sent: a part where nobody speaks isn't sent at all (and isn't paid for),
and a word the model returns outside the speech it found is dropped.
Nothing falls back to local Whisper; a failing provider fails the job, in
plain words. The one exception is a provider whose spec asks for it
(stt["fallback_local"]): Meta's Muse Voice Transcribe, added for the Meta
hackathon edition, where transcriber.transcribe runs Whisper instead.

Muse Voice is also the one provider here that times turns, not words
(stt["word_timings"] False): Meta's docs say it returns no word timestamps.
Its turns become segments with their own start and end and no words; none
are made up here. What reads words already copes with a segment without
them (as with transcripts cached before word timings existed): captions and
commentary spread its words over the segment, so they are approximate, and
a creator who needs word-exact captions keeps Whisper.
"""

import base64
import json
import re
import subprocess
import tempfile
from pathlib import Path

from core import cancel, progress
from core.binaries import ffmpeg, ffprobe
from core.models import Segment
from llm.providers import keys
from llm.providers.base import LLMError
from llm.providers.catalog import get
from llm.providers.http import send

# A three-second spoken phrase, for checking a voice model the app has not
# seen return word timings before (check_model).
PROBE = Path(__file__).resolve().parent / "assets" / "probe.mp3"

OVERLAP = 2.0          # seconds of audio shared by neighbouring chunks
SPEECH_PAD = 0.2       # a word counts as spoken this close to detected speech
PAUSE = 0.6            # a gap this long between words starts a new segment
MAX_SEGMENT = 15.0     # and no segment runs longer than this
STT_TIMEOUT = 300
_SENTENCE_END = re.compile(r"[.!?…。！？]['\")\]]*$")


def transcribe(video_path: Path, video_id: str, transcript_dir: Path, online: dict,
               language: str | None = None, hotwords: str | None = None) -> list[Segment]:
    provider = str(online.get("backend") or "")
    spec = get(provider)
    if spec is None or not spec.stt:
        raise LLMError("not_configured", f"'{provider}' can't transcribe. Choose another in Settings → AI.")
    spec, key = keys.resolve(online.get("data_dir") or "data", spec)
    if not key:
        raise LLMError("not_configured", f"No {spec.label} API key is saved. Add yours in Settings → AI.")
    model = str(online.get("model") or spec.stt["models"][0])
    length = float(spec.stt["chunk_seconds"])
    turn_level = spec.stt.get("word_timings") is False
    keywords = [k.strip() for k in re.split(r"[,\n]", hotwords or "") if k.strip()][:50]

    duration = _duration(video_path)
    starts = [i * length for i in range(max(1, int(-(-duration // length))))]
    print(f"  Transcribing online with {spec.label} ({model}), {len(starts)} part(s)...")

    words: list[dict] = []
    turns: list[dict] = []
    detected = ""
    silent = made_up = 0
    sent_seconds = 0.0
    with tempfile.TemporaryDirectory(prefix="ck-stt-") as tmp:
        for i, start in enumerate(starts):
            cancel.check_active()
            progress.emit(stage="transcribe", fraction=i / len(starts))
            clip_from = max(0.0, start - OVERLAP)
            if turn_level:
                audio = _extract(video_path, Path(tmp) / f"part{i}.wav", clip_from, length + 2 * OVERLAP, wav=True)
            else:
                audio = _extract(video_path, Path(tmp) / f"part{i}.mp3", clip_from, length + 2 * OVERLAP)
            speech = _speech_in(audio)
            if speech == []:
                silent += 1
                continue
            sent_seconds += max(0.0, min(length + 2 * OVERLAP, duration - clip_from))
            end = start + length if i < len(starts) - 1 else float("inf")
            if turn_level:
                part_turns = _request_turns(spec, key, model, audio, language, keywords)
                if speech is not None:
                    kept = [t for t in part_turns if _spoken(t, speech)]
                    made_up += len(part_turns) - len(kept)
                    part_turns = kept
                for t in part_turns:
                    absolute = {**t, "start": round(t["start"] + clip_from, 2), "end": round(t["end"] + clip_from, 2)}
                    if start <= absolute["start"] < end:
                        turns.append(absolute)
                continue
            part_words, part_language = _request(spec, key, model, audio, language)
            if speech is not None:
                kept = [w for w in part_words if _spoken(w, speech)]
                made_up += len(part_words) - len(kept)
                part_words = kept
            detected = detected or part_language
            # A word belongs to the part its start falls in; the overlap is
            # context for the model, so words at a boundary are not cut off.
            for w in part_words:
                absolute = {**w, "start": round(w["start"] + clip_from, 2), "end": round(w["end"] + clip_from, 2)}
                if start <= absolute["start"] < end:
                    words.append(absolute)
    progress.emit(stage="transcribe", fraction=1.0)
    if silent or made_up:
        print(f"      Nobody speaks in {silent} part(s), not sent; {made_up} word(s) outside speech dropped")

    if turn_level:
        segments = [Segment(start=t["start"], end=t["end"], text=t["text"], words=None)
                    for t in sorted(turns, key=lambda x: x["start"])]
    else:
        segments = group_words(words)
    lang = (language or normalize_language(detected) or "en").lower()
    transcript_dir.mkdir(parents=True, exist_ok=True)
    payload = {"video_id": video_id, "language": lang, "source": f"{provider}/{model}",
               "segments": [vars(s) for s in segments]}
    if turn_level:
        payload["word_timings"] = False
    payload["audio_seconds"] = round(sent_seconds, 1)
    _record_usage(online, provider, sent_seconds)
    (transcript_dir / f"{video_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    # The repetition guard runs in transcriber.transcribe(), on these the same
    # as on a local transcript; importing it here would make a cycle.
    print(f"      {len(segments)} segments ({lang})")
    return segments


def check_model(spec, key: str, model: str) -> tuple[bool, str]:
    """Does this voice model return the word timings captions need?

    One real request with the short test clip, through exactly the code a job
    uses, so a yes here means a yes in a job. Only ever run because the user
    picked the model; it costs a tiny fraction of a cent on their key.
    """
    if spec.stt.get("word_timings") is False:
        try:
            with tempfile.TemporaryDirectory(prefix="ck-stt-") as tmp:
                turns = _request_turns(spec, key, model, _extract(PROBE, Path(tmp) / "probe.wav", 0, 30, wav=True),
                                       "en", [])
        except LLMError as e:
            return False, e.message
        if not turns:
            return False, f"{model} returned nothing for the test clip. Choose another voice model."
        return True, (f"{model} works: it returned timed turns for the test clip. It times turns, not "
                      "words, so word-by-word captions are approximate.")
    try:
        words, _language = _request(spec, key, model, PROBE, "en")
    except LLMError as e:
        return False, e.message
    timed = [w for w in words if w["end"] > w["start"]]
    if not timed:
        return False, (f"{model} didn't return word timings, which captions and word editing "
                       "need. Choose another voice model.")
    return True, f"{model} works: it returned word timings for the test clip."


# ---- where someone speaks --------------------------------------------------------


def _speech_in(audio: Path) -> list[tuple[float, float]] | None:
    """Where someone speaks in a part, in seconds from its start, by the voice
    detector local Whisper uses (Silero, through faster-whisper, with its
    defaults). None when it can't run: then every word is kept, as before."""
    try:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError:
        return None
    try:
        samples = decode_audio(str(audio), sampling_rate=16000)
        found = get_speech_timestamps(samples, VadOptions(), sampling_rate=16000)
    except Exception as e:  # a part it can't read is sent as before
        print(f"      (couldn't check a part for speech: {e})")
        return None
    return [(t["start"] / 16000, t["end"] / 16000) for t in found]


def _spoken(word: dict, speech: list[tuple[float, float]]) -> bool:
    return any(word["start"] < end + SPEECH_PAD and word["end"] > start - SPEECH_PAD for start, end in speech)


# ---- one request per provider format -------------------------------------------


def _request(spec, key: str, model: str, audio: Path, language: str | None) -> tuple[list[dict], str]:
    fmt = spec.stt["format"]
    data = audio.read_bytes()
    if fmt == "openrouter":
        body = {"model": model, "response_format": "verbose_json",
                "timestamp_granularities": ["segment", "word"],
                "input_audio": {"data": base64.b64encode(data).decode("ascii"), "format": "mp3"}}
        if language:
            body["language"] = language
        answer = send(spec, key, "POST", "/audio/transcriptions", json_body=body, timeout=STT_TIMEOUT)
        return _whisper_words(answer), str(answer.get("language") or "")
    if fmt == "openai":
        form = [("model", model), ("response_format", "verbose_json"),
                ("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment")]
        if language:
            form.append(("language", language))
        answer = send(spec, key, "POST", "/audio/transcriptions", data=form,
                      files={"file": (audio.name, data, "audio/mpeg")}, timeout=STT_TIMEOUT)
        return _whisper_words(answer), str(answer.get("language") or "")
    if fmt == "xai":
        form = {"model": model, "format": "true"}
        if language:
            form["language"] = language
        answer = send(spec, key, "POST", "/stt", data=form,
                      files={"file": (audio.name, data, "audio/mpeg")}, timeout=STT_TIMEOUT)
        words = [{"start": float(w["start"]), "end": float(w["end"]), "word": str(w.get("text") or "").strip()}
                 for w in answer.get("words") or [] if isinstance(w, dict) and str(w.get("text") or "").strip()]
        return words, str(answer.get("language") or "")
    raise LLMError("not_configured", f"{spec.label} transcription is not set up in this version.")


def _request_turns(spec, key: str, model: str, audio: Path, language: str | None,
                   keywords: list[str]) -> list[dict]:
    """Muse Voice Transcribe: one WAV part, its turns as {"start", "end",
    "text"} in seconds from the part's start. ENDPOINTING mode, the one that
    splits the audio into timed turns (PUSH_TO_TALK returns none). The names
    the job knows (keywords) help it spell players and teams."""
    request: dict = {"model": model, "audioEncoding": "WAV", "mode": "ENDPOINTING"}
    name = _language_name(language)
    if name:
        request["languageBias"] = [name]
    if keywords:
        request["keywords"] = keywords
    answer = send(spec, key, "POST", "/asr/transcribe", timeout=STT_TIMEOUT, files={
        "request": (None, json.dumps(request), "application/json"),
        "audio": (audio.name, audio.read_bytes(), "audio/wav"),
    })
    turns = []
    for t in answer.get("turns") or []:
        if not isinstance(t, dict):
            continue
        text = re.sub(r"\s+", " ", str(t.get("transcript") or "")).strip()
        try:
            start, end = float(t["startMs"]) / 1000, float(t["endMs"]) / 1000
        except (KeyError, TypeError, ValueError):
            continue
        if text and end > start:
            turns.append({"start": start, "end": end, "text": text})
    if not turns and str(answer.get("transcript") or "").strip():
        # Text with no turn to place it in: refused rather than given a made-up time.
        raise LLMError("bad_response", "Muse Voice returned text without timings for part of the audio.")
    return turns


def _language_name(code: str | None) -> str:
    """"es" -> "Spanish", for Muse Voice's languageBias (it takes names)."""
    if not code:
        return ""
    from multilingual.languages import LANGUAGES

    entry = LANGUAGES.get(code.lower())
    return entry[0] if entry else ""


def falls_back(online: dict | None) -> bool:
    """Whether a failure of this provider should hand the job to local
    Whisper instead of failing it (stt["fallback_local"])."""
    spec = get(str((online or {}).get("backend") or ""))
    return bool(spec is not None and spec.stt.get("fallback_local"))


def _record_usage(online: dict, provider: str, seconds: float) -> None:
    """Audio seconds sent to Muse Voice, for the usage panel (estimates).
    Never fails a transcription."""
    if provider != "meta" or seconds <= 0:
        return
    try:
        from core.state import StateDB
        from remote_approval import usage

        path = Path(online.get("data_dir") or "data") / "state.db"
        if not path.exists():
            return
        db = StateDB(path)
        try:
            usage.record_voice(db, seconds)
        finally:
            db.close()
    except Exception as e:
        print(f"      (couldn't record Muse Voice usage: {e})")


def _whisper_words(answer: dict) -> list[dict]:
    """Words from a Whisper verbose_json answer, with the segment text's
    punctuation put back: whisper-1 returns bare words ("there", not
    "there."), and captions and sentence grouping need the punctuation.
    Empty for a part where nothing is said; text without word timings is
    refused."""
    words = [{"start": float(w["start"]), "end": float(w["end"]), "word": str(w.get("word") or "").strip()}
             for w in answer.get("words") or [] if isinstance(w, dict) and str(w.get("word") or "").strip()]
    if not words:
        segments = [s for s in answer.get("segments") or [] if isinstance(s, dict)]
        said = str(answer.get("text") or "").strip() or any(str(s.get("text") or "").strip() for s in segments)
        if not said:
            return []   # nothing said in this part: music, a "starting soon" screen
        raise LLMError("bad_response", "The transcription came back without word timings, which "
                                       "captions need. Choose a different transcription model.")
    return _punctuate(words, answer.get("segments") or [])


def _bare(token: str) -> str:
    return re.sub(r"[^\w]", "", token.lower())


def _punctuate(words: list[dict], segments: list[dict]) -> list[dict]:
    tokens = [t for s in segments if isinstance(s, dict) for t in str(s.get("text") or "").split()]
    j = 0
    for w in words:
        if re.search(r"[^\w\s]", w["word"]):
            continue  # already punctuated
        for k in range(j, min(j + 4, len(tokens))):  # a short look-ahead keeps a miss from derailing it
            if _bare(tokens[k]) == _bare(w["word"]):
                w["word"] = tokens[k]
                j = k + 1
                break
    return words


# ---- shaping --------------------------------------------------------------------


def group_words(words: list[dict]) -> list[Segment]:
    """Whisper-like segments from timed words: a new one at a pause, after the
    end of a sentence, or once one has run MAX_SEGMENT seconds."""
    segments: list[Segment] = []
    current: list[dict] = []
    for w in sorted(words, key=lambda x: x["start"]):
        if current and (w["start"] - current[-1]["end"] >= PAUSE
                        or _SENTENCE_END.search(current[-1]["word"])
                        or w["end"] - current[0]["start"] > MAX_SEGMENT):
            segments.append(_segment(current))
            current = []
        current.append(w)
    if current:
        segments.append(_segment(current))
    return segments


def _segment(words: list[dict]) -> Segment:
    return Segment(
        start=round(words[0]["start"], 2),
        end=round(words[-1]["end"], 2),
        text=" ".join(w["word"] for w in words).strip(),
        words=[{"start": w["start"], "end": w["end"], "word": w["word"]} for w in words],
    )


def normalize_language(value: str) -> str:
    """"en" stays "en"; "english" (whisper-1) becomes "en"."""
    text = (value or "").strip().lower()
    if re.fullmatch(r"[a-z]{2,3}", text):
        return text
    from multilingual.languages import LANGUAGES

    names = {english.split(" ")[0].lower(): code for code, (english, _native, _prompt) in LANGUAGES.items()}
    names.update({"mandarin": "zh", "tagalog": "tl", "bangla": "bn"})
    return names.get(text.split(" ")[0], "")


# ---- audio ------------------------------------------------------------------------


def _duration(video_path: Path) -> float:
    out = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return max(1.0, float(out))
    except ValueError:
        return 1.0


def _extract(video_path: Path, out: Path, start: float, seconds: float, *, wav: bool = False) -> Path:
    """Mono 16 kHz speech-quality MP3: about 2.4 MB per ten minutes, inside
    every provider's upload limit, and a format they all accept. Or, for Muse
    Voice (wav), the 16-bit PCM WAV it requires: 9 minutes is about 17 MB,
    inside its 32 MB and 10-minute limits."""
    codec = ["-c:a", "pcm_s16le", "-map_metadata", "-1"] if wav else ["-c:a", "libmp3lame", "-b:a", "32k"]
    result = subprocess.run(
        [ffmpeg(), "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{seconds:.2f}",
         "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", *codec, str(out)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out.exists():
        raise RuntimeError(f"Could not extract audio for transcription: {result.stderr.strip()[:200]}")
    return out

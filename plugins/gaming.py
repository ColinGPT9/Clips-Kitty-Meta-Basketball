"""Game-specific finding with the host's existing Gaming analysis and models."""


def is_detector(choice, data_dir):
    from plugins import store

    try:
        installed = store.get(data_dir, store.clean_choice(choice)["id"])
    except ValueError:
        return False
    return installed is not None and installed.manifest.get("category") == "gaming"


def review(candidates, *, video, segments, llm, config, profile, signals=None, chat=None, sounds=None):
    """Review the detector's windows with Gaming's shared evidence and vision.

    Never call the generic finder or add windows here. The plugin's score and
    provenance survive; the normal local image review can adjust the score.
    YOLO webcam detection/tracking stays in the shared Gaming renderer, just as
    for ordinary Gaming: game characters aren't measured as streamer reactions.
    """
    if not candidates or profile is None:
        return candidates, []

    from analysis import fusion
    from core.models import Rejection

    audio_raw, visual_raw = signals if signals is not None else ({}, {})
    audio = fusion._combine([fusion._pct(audio_raw[k]) for k in ("spike", "burst", "noisiness")
                             if k in audio_raw])
    visual = fusion._combine([fusion._pct(visual_raw[k]) for k in ("motion", "scene_cut", "flash")
                              if k in visual_raw])
    events = fusion._build_events(audio, visual, visual_raw)
    voice, voice_events = fusion._voice_jump(audio_raw, segments)
    events += list(voice_events)
    sources = [(voice, 0.6)]
    if chat is not None:
        sources.append((chat.curve, 0.9))
        events += list(chat.events)
    if sounds is not None:
        sources += [(sounds.game, 0.7), (sounds.people, 0.5)]
        events += list(sounds.events)
    game_curve = fusion._soft_or(sources)
    scoring = config.get("scoring", {})
    if scoring.get("read_screen", True) and game_curve.size and getattr(profile, "reads_screen", True):
        screen = fusion._read_screen(video.path, game_curve, segments, config["clips"], profile)
        if screen is not None:
            events += list(screen.events)
            events += [(start, f"ON SCREEN: a menu, queue or settings screen ({words})")
                       for start, _end, words in screen.menus]

    for candidate in candidates:
        candidate.subscores = dict(candidate.subscores or {})
        if audio.size:
            candidate.subscores["audio"] = round(fusion._window_mean(audio, candidate.start, candidate.end) * 100)
        if visual.size:
            candidate.subscores["visual"] = round(fusion._window_mean(visual, candidate.start, candidate.end) * 100)
        if game_curve.size:
            candidate.subscores["game"] = round(fusion._window_peak(game_curve, candidate.start, candidate.end) * 100)
    if scoring.get("look_at_game", True):
        fusion._look_at_game(candidates, video.path, llm, profile, segments, sorted(events, key=lambda row: row[0]))
    minimum = config["clips"]["min_score"]
    rejected = [Rejection(candidate, "below_min_score") for candidate in candidates if candidate.score < minimum]
    kept = [candidate for candidate in candidates if candidate.score >= minimum]
    return sorted(kept, key=lambda candidate: candidate.score, reverse=True), rejected

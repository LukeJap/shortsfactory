import analyze


def _window(start, end, text):
    return analyze.CandidateWindow(start=start, end=end, text=text)


def test_window_ranker_uses_shared_request_timeout(monkeypatch):
    observed = {}

    def fake_request_json(url, payload=None, timeout=10):
        observed["url"] = url
        observed["payload"] = payload
        observed["timeout"] = timeout
        return {"response": '{"selections": []}'}

    monkeypatch.setattr(analyze, "request_json", fake_request_json)

    assert analyze.call_ollama_window_ranker(
        "http://127.0.0.1:11434",
        "llama3.1:8b",
        "rank these windows",
        window_ids=["W001", "W002"],
        selection_count=2,
    ) == {"selections": []}
    assert observed["timeout"] == analyze.REQUEST_TIMEOUT_SECONDS
    assert observed["payload"]["options"]["temperature"] == 0
    schema = observed["payload"]["format"]
    assert schema["properties"]["selections"]["minItems"] == 2
    assert schema["properties"]["selections"]["maxItems"] == 2
    assert schema["properties"]["selections"]["items"]["properties"]["window_id"]["enum"] == [
        "W001",
        "W002",
    ]


def test_window_ranker_timeout_explains_that_ollama_took_too_long(monkeypatch):
    def timed_out(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(analyze, "request_json", timed_out)

    try:
        analyze.call_ollama_window_ranker(
            "http://127.0.0.1:11434",
            "llama3.1:8b",
            "rank",
            window_ids=["W001"],
            selection_count=1,
        )
    except RuntimeError as exc:
        assert str(exc) == "Ollama took too long to rank the clip candidates. Please try again."
    else:
        raise AssertionError("Expected the window-ranker timeout to be surfaced.")


def test_window_ranker_prompt_uses_global_context_and_rejects_intro_material():
    transcript = analyze.TranscriptData(
        text=(
            "[00:00:00 - 00:00:20] Theme music and title sequence. "
            "[00:01:00 - 00:01:20] Maya discovers the missing key and the door opens."
        ),
        segments=[],
    )
    prompt = analyze.build_window_ranking_prompt(
        transcript,
        [
            _window(0, 20, "Theme music and title sequence."),
            _window(60, 80, "Maya discovers the missing key and the door opens."),
        ],
        target_clip_count=1,
    )

    assert "SOURCE CONTEXT" in prompt
    assert "Theme music and title sequence" in prompt
    assert "opening themes, title sequences, credits, recaps" in prompt
    assert "specific event or payoff" in prompt


def test_ranked_windows_keep_simple_schema_and_prefer_distinct_moments():
    setup = _window(20, 40, "Maya arrives at the warehouse.")
    overlapping_setup = _window(25, 45, "Maya arrives and looks around the warehouse.")
    payoff = _window(80, 100, "Maya finds the key, opens the door, and everyone cheers.")
    later_scene = _window(140, 160, "The robot spills paint and the team reacts.")
    result = {
        "selections": [
            {"window_id": "W003", "score": 94, "reason": "door opens"},
            {"window_id": "W001", "score": 78, "reason": "arrival"},
            {"window_id": "W002", "score": 77, "reason": "same arrival"},
            {"window_id": "W004", "score": 82, "reason": "paint spill"},
        ]
    }

    parsed = analyze.ranked_windows_from_result(
        result,
        [setup, overlapping_setup, payoff, later_scene],
    )
    selected = analyze.select_distinct_ranked_windows(parsed, target_clip_count=3)

    assert [window for window, _score, _reason in selected] == [payoff, setup, later_scene]


def test_ranked_windows_keep_usable_lower_scored_real_scenes():
    weak_but_real_scene = _window(20, 40, "Maya searches the empty warehouse.")
    result = {
        "selections": [
            {
                "window_id": "W001",
                "score": 68,
                "reason": "Maya searches the warehouse.",
            }
        ]
    }

    assert analyze.ranked_windows_from_result(result, [weak_but_real_scene]) == [
        (weak_but_real_scene, 68, "Maya searches the warehouse.")
    ]


def test_backfill_prompt_excludes_already_selected_window_ids():
    transcript = analyze.TranscriptData(text="A source transcript.", segments=[])
    windows = [
        _window(0, 20, "Theme title card."),
        _window(20, 40, "Maya finds a key."),
        _window(40, 60, "The door opens."),
    ]

    prompt = analyze.build_window_ranking_prompt(
        transcript,
        windows,
        target_clip_count=1,
        excluded_window_ids={"W001", "W002"},
        response_count_override=1,
    )

    assert "Theme title card." not in prompt
    assert "Maya finds a key." not in prompt
    assert "The door opens." in prompt
    assert "Do not omit weaker-but-usable real scenes" in prompt


def test_beats_merge_segments_closer_than_the_gap_threshold():
    segments = [
        analyze.TranscriptSegment(start=0.0, end=2.0, text="Setup line one."),
        analyze.TranscriptSegment(start=2.3, end=4.0, text="continues right after."),
        analyze.TranscriptSegment(start=4.7, end=6.0, text="A new beat starts here."),
    ]

    beats = analyze.build_beats(segments)

    assert len(beats) == 2
    assert beats[0].start == 0.0
    assert beats[0].end == 4.0
    assert "Setup line one. continues right after." == beats[0].text
    assert beats[0].ends_with_terminal_punctuation is True
    assert beats[1].ends_with_terminal_punctuation is True


def test_candidates_stay_within_the_variable_length_bounds():
    segments = []
    t = 0.0
    for index in range(20):
        segments.append(
            analyze.TranscriptSegment(start=t, end=t + 2.0, text=f"Beat number {index} happens now.")
        )
        t += 3.0

    windows = analyze.generate_valid_windows(segments)

    assert windows
    assert all(
        analyze.MIN_CLIP_SECONDS <= window.duration_seconds <= analyze.MAX_CLIP_SECONDS
        for window in windows
    )


def test_candidates_never_start_or_end_mid_sentence():
    segments = [
        analyze.TranscriptSegment(start=0.0, end=4.0, text="Setup line one."),
        analyze.TranscriptSegment(start=5.0, end=8.0, text="Setup line two"),
        analyze.TranscriptSegment(start=9.0, end=13.0, text="continues after a pause."),
        analyze.TranscriptSegment(start=14.0, end=18.0, text="Escalation happens now."),
        analyze.TranscriptSegment(
            start=19.0, end=30.0, text="The payoff lands here for everyone in the room."
        ),
    ]

    beats = analyze.build_beats(segments)
    windows = analyze.generate_valid_windows(segments)

    # beats[1] ("Setup line two") does not end on terminal punctuation, so no
    # candidate may end there, and no candidate may start on beats[2]
    # ("continues after a pause.") -- that would be a mid-sentence start.
    assert beats[1].ends_with_terminal_punctuation is False
    assert windows
    assert all(window.end != beats[1].end for window in windows)
    assert all(window.start != beats[2].start for window in windows)


def test_candidate_count_stays_bounded_on_a_long_synthetic_transcript():
    segments = []
    t = 0.0
    for index in range(150):
        segments.append(
            analyze.TranscriptSegment(start=t, end=t + 2.0, text=f"Beat number {index} happens now.")
        )
        t += 3.0

    beats = analyze.build_beats(segments)
    windows = analyze.generate_valid_windows(segments)

    assert len(beats) == 150
    assert len(windows) <= analyze.MAX_ENDS_PER_START * len(beats)
    assert len(windows) < len(beats) ** 2


def test_candidates_cover_the_final_beat_of_the_source():
    segments = []
    t = 0.0
    for index in range(60):
        segments.append(
            analyze.TranscriptSegment(start=t, end=t + 2.0, text=f"Beat number {index} happens now.")
        )
        t += 3.0

    beats = analyze.build_beats(segments)
    windows = analyze.generate_valid_windows(segments)

    assert any(window.end == beats[-1].end for window in windows)


def test_dedupe_collapses_near_identical_candidates():
    segments = [
        analyze.TranscriptSegment(start=0.0, end=0.1, text="Hi."),
        analyze.TranscriptSegment(start=0.75, end=1.75, text="Hello there now."),
        analyze.TranscriptSegment(start=2.75, end=6.0, text="Escalation continues for a while here."),
        analyze.TranscriptSegment(
            start=20.0,
            end=24.0,
            text="The payoff finally lands for everyone watching this scene unfold.",
        ),
    ]

    windows = analyze.generate_valid_windows(segments)

    # beats[0] (0.0) and beats[1] (0.75) both qualify as starts for the same
    # payoff end, within CANDIDATE_DEDUPE_TOLERANCE_SECONDS of each other --
    # only one may survive.
    assert len(windows) >= 2
    for index, first in enumerate(windows):
        for second in windows[index + 1 :]:
            start_close = (
                abs(first.start - second.start) <= analyze.CANDIDATE_DEDUPE_TOLERANCE_SECONDS
            )
            end_close = abs(first.end - second.end) <= analyze.CANDIDATE_DEDUPE_TOLERANCE_SECONDS
            assert not (start_close and end_close)


def test_distinct_selection_never_falls_back_to_overlapping_exact_minutes():
    first = _window(0, 60, "First complete scene.")
    overlapping = _window(15, 75, "A variation of the first scene.")
    later = _window(90, 150, "A later complete scene.")

    selected = analyze.select_distinct_ranked_windows(
        [(first, 95, "first"), (overlapping, 94, "overlap"), (later, 80, "later")],
        target_clip_count=3,
    )

    assert [window for window, _score, _reason in selected] == [first, later]


def test_long_sources_are_ranked_in_chronological_batches_then_finalized(monkeypatch):
    transcript = analyze.TranscriptData(text="Source context", segments=[])
    windows = [_window(index * 60, index * 60 + 60, f"Scene {index}") for index in range(40)]
    calls = []

    def fake_ranker(_host, _model, _prompt, *, window_ids, selection_count):
        calls.append((window_ids, selection_count))
        return {
            "selections": [
                {"window_id": window_id, "score": 90 - index, "reason": window_id}
                for index, window_id in enumerate(window_ids[:selection_count])
            ]
        }

    monkeypatch.setattr(analyze, "call_ollama_window_ranker", fake_ranker)

    ranked = analyze.rank_exact_minute_windows(
        "http://127.0.0.1:11434", "llama3.1:8b", transcript, windows, target_clip_count=3
    )

    # Five cheap chronological batches plus one compact global shortlist.
    assert len(calls) == 6
    assert [len(window_ids) for window_ids, _count in calls[:5]] == [8, 8, 8, 8, 8]
    assert [count for _window_ids, count in calls[:5]] == [2, 2, 2, 2, 2]
    assert len(calls[5][0]) == 10
    assert calls[5][1] == 6
    assert len(ranked) == 3
    assert all(
        analyze.MIN_CLIP_SECONDS <= window.duration_seconds <= analyze.MAX_CLIP_SECONDS
        for window, _score, _reason in ranked
    )


def test_first_stage_timeout_retries_once_with_smaller_sub_batches(monkeypatch):
    transcript = analyze.TranscriptData(text="Source context", segments=[])
    windows = [_window(index * 60, index * 60 + 60, f"Scene {index}") for index in range(16)]
    calls = []

    def fake_ranker(_host, _model, _prompt, *, window_ids, selection_count):
        calls.append((len(window_ids), selection_count))
        if len(calls) == 1:
            raise analyze.WindowRankingTimeout("timed out")
        return {
            "selections": [
                {"window_id": window_id, "score": 90 - index, "reason": window_id}
                for index, window_id in enumerate(window_ids[:selection_count])
            ]
        }

    monkeypatch.setattr(analyze, "call_ollama_window_ranker", fake_ranker)

    ranked = analyze.rank_exact_minute_windows(
        "http://127.0.0.1:11434",
        "llama3.1:8b",
        transcript,
        windows,
        target_clip_count=2,
    )

    assert calls == [(8, 2), (4, 1), (4, 1), (8, 2), (4, 4)]
    assert len(ranked) == 2


def test_first_stage_prompts_use_compact_context(monkeypatch):
    transcript = analyze.TranscriptData(text="episode context " * 2000, segments=[])
    windows = [_window(index * 60, index * 60 + 60, f"Scene {index}") for index in range(16)]
    prompts = []

    def fake_ranker(_host, _model, prompt, *, window_ids, selection_count):
        prompts.append(prompt)
        return {
            "selections": [
                {"window_id": window_id, "score": 80 - index, "reason": window_id}
                for index, window_id in enumerate(window_ids[:selection_count])
            ]
        }

    monkeypatch.setattr(analyze, "call_ollama_window_ranker", fake_ranker)

    analyze.rank_exact_minute_windows(
        "http://127.0.0.1:11434",
        "llama3.1:8b",
        transcript,
        windows,
        target_clip_count=2,
    )

    assert len(prompts) == 3
    assert all(len(prompt) < 9_000 for prompt in prompts[:2])
    assert len(prompts[-1]) < 13_000


def test_rank_request_returns_partial_results_above_minimum_count(monkeypatch, capsys):
    transcript = analyze.TranscriptData(text="Context", segments=[])
    windows = [_window(0, 60, "A complete scene."), _window(60, 120, "Another scene.")]
    monkeypatch.setattr(
        analyze,
        "call_ollama_window_ranker",
        lambda *_args, **_kwargs: {
            "selections": [{"window_id": "W001", "score": 90, "reason": "scene"}]
        },
    )

    ranked = analyze.rank_window_request(
        "http://127.0.0.1:11434",
        "llama3.1:8b",
        transcript,
        windows,
        2,
        request_label="test batch",
        context_max_chars=100,
        minimum_count=1,
    )

    assert len(ranked) == 1
    assert "WARNING" in capsys.readouterr().out


def test_rank_request_raises_shortfall_below_minimum_count(monkeypatch):
    transcript = analyze.TranscriptData(text="Context", segments=[])
    windows = [_window(0, 60, "A complete scene."), _window(60, 120, "Another scene.")]
    monkeypatch.setattr(
        analyze,
        "call_ollama_window_ranker",
        lambda *_args, **_kwargs: {
            "selections": [{"window_id": "W001", "score": 90, "reason": "scene"}]
        },
    )

    try:
        analyze.rank_window_request(
            "http://127.0.0.1:11434",
            "llama3.1:8b",
            transcript,
            windows,
            2,
            request_label="test batch",
            context_max_chars=100,
            minimum_count=2,
        )
    except analyze.WindowRankingShortfall as exc:
        assert "returned 1 valid selections" in str(exc)
        assert "expected 2" in str(exc)
        assert exc.request_label == "test batch"
        assert exc.requested == 2
        assert exc.returned == 1
    else:
        raise AssertionError("Expected a below-minimum ranking result to raise WindowRankingShortfall.")


def _segment(start, end, text):
    return analyze.TranscriptSegment(start=start, end=end, text=text)


def test_snap_boundary_moves_to_nearby_segment_edge_within_leeway():
    segments = [_segment(10.0, 57.0, "a"), _segment(61.5, 90.0, "b")]

    # 60.0 is 1.5s from the next segment's start (61.5) -- inside leeway.
    assert analyze.snap_boundary_to_segment_edge(60.0, segments) == 61.5


def test_snap_boundary_ignores_edges_outside_leeway():
    segments = [_segment(10.0, 40.0, "a"), _segment(70.0, 90.0, "b")]

    # Nearest edges are 30s and 10s away -- both well outside the default
    # leeway, so the mechanical boundary must be left alone.
    assert analyze.snap_boundary_to_segment_edge(60.0, segments) == 60.0


def test_snap_window_moves_end_to_sentence_boundary_and_updates_text():
    segments = [
        _segment(0.0, 30.0, "Setup line."),
        _segment(30.0, 57.5, "The reveal lands here."),
        _segment(61.0, 90.0, "Next scene begins."),
    ]
    window = analyze.CandidateWindow(
        start=0.0, end=60.0, text="Setup line. The reveal lands here."
    )

    snapped = analyze.snap_window_to_sentence_boundaries(window, segments)

    # 60.0 is nearer to the next segment's start (61.0, 1.0s away) than to
    # the end of "The reveal lands here." (57.5, 2.5s away) -- the closer
    # edge wins, landing the cut right as the next line begins rather than
    # lopping 2.5s off the reveal sentence.
    assert snapped.end == 61.0
    assert snapped.start == 0.0
    # The clip runs up to (not including) 61.0, so it still ends exactly
    # on the reveal line -- the next scene's own text starts at 61.0 and
    # is correctly excluded, not bled into this clip's description.
    assert "The reveal lands here." in snapped.text
    assert "Next scene begins." not in snapped.text


def test_snap_window_leaves_boundary_alone_without_nearby_segments():
    segments = [_segment(0.0, 20.0, "a"), _segment(90.0, 110.0, "b")]
    window = analyze.CandidateWindow(start=20.0, end=80.0, text="middle")

    snapped = analyze.snap_window_to_sentence_boundaries(window, segments)

    assert snapped == window


def test_candidate_clips_apply_leeway_only_when_segments_are_supplied():
    segments = [
        _segment(0.0, 30.0, "Setup."),
        _segment(30.0, 57.0, "Payoff line."),
        _segment(61.5, 90.0, "Next scene."),
    ]
    window = analyze.CandidateWindow(start=0.0, end=60.0, text="Setup. Payoff line.")
    ranked = [(window, 91, "payoff")]

    without_segments = analyze.candidate_clips_from_ranked_windows(ranked, {})
    assert without_segments[0]["duration_seconds"] == 60.0

    with_segments = analyze.candidate_clips_from_ranked_windows(ranked, {}, segments=segments)
    assert with_segments[0]["end_timestamp"] == analyze.format_timestamp(61.5)
    assert with_segments[0]["duration_seconds"] == 61.5


def test_first_stage_batch_returns_empty_list_when_retry_also_empty(monkeypatch):
    transcript = analyze.TranscriptData(text="Source context", segments=[])
    batch = [_window(index * 60, index * 60 + 60, f"Scene {index}") for index in range(8)]

    def always_empty(_host, _model, _prompt, *, window_ids, selection_count):
        return {"selections": []}

    monkeypatch.setattr(analyze, "call_ollama_window_ranker", always_empty)

    ranked = analyze.rank_first_stage_batch(
        "http://127.0.0.1:11434",
        "llama3.1:8b",
        transcript,
        batch,
        selection_count=2,
        batch_number=1,
        batch_total=1,
    )

    assert ranked == []


def test_exact_minute_windows_survive_one_empty_batch_out_of_four(monkeypatch):
    transcript = analyze.TranscriptData(text="Source context", segments=[])
    windows = [_window(index * 60, index * 60 + 60, f"Scene {index}") for index in range(32)]
    failing_scenes = {f"Scene {index}" for index in range(8, 16)}

    def fake_ranker(_host, _model, prompt, *, window_ids, selection_count):
        if any(scene in prompt for scene in failing_scenes):
            return {"selections": []}
        return {
            "selections": [
                {"window_id": window_id, "score": 90 - index, "reason": window_id}
                for index, window_id in enumerate(window_ids[:selection_count])
            ]
        }

    monkeypatch.setattr(analyze, "call_ollama_window_ranker", fake_ranker)

    ranked = analyze.rank_exact_minute_windows(
        "http://127.0.0.1:11434", "llama3.1:8b", transcript, windows, target_clip_count=3
    )

    assert len(ranked) == 3
    assert all(
        analyze.MIN_CLIP_SECONDS <= window.duration_seconds <= analyze.MAX_CLIP_SECONDS
        for window, _score, _reason in ranked
    )
    for index, (window, _score, _reason) in enumerate(ranked):
        for other_window, _other_score, _other_reason in ranked[index + 1 :]:
            overlap = max(
                0.0, min(window.end, other_window.end) - max(window.start, other_window.start)
            )
            assert overlap == 0.0


def test_rank_request_logs_raw_response_when_no_selections_parse(monkeypatch, capsys):
    transcript = analyze.TranscriptData(text="Context", segments=[])
    windows = [_window(0, 60, "A complete scene.")]

    def fake_request_json(url, payload=None, timeout=10):
        return {"response": '{"selections": [{"window_id": "W999", "score": 1, "reason": "bad id"}]}'}

    monkeypatch.setattr(analyze, "request_json", fake_request_json)

    ranked = analyze.rank_window_request(
        "http://127.0.0.1:11434",
        "llama3.1:8b",
        transcript,
        windows,
        1,
        request_label="test batch",
        context_max_chars=100,
        minimum_count=0,
    )

    assert ranked == []
    output = capsys.readouterr().out
    assert "raw model response" in output
    assert "W999" in output


def test_window_ranker_unparseable_response_surfaces_raw_text(monkeypatch):
    def fake_request_json(url, payload=None, timeout=10):
        return {"response": "not json at all, garbled model output"}

    monkeypatch.setattr(analyze, "request_json", fake_request_json)

    try:
        analyze.call_ollama_window_ranker(
            "http://127.0.0.1:11434",
            "llama3.1:8b",
            "rank",
            window_ids=["W001"],
            selection_count=1,
        )
    except RuntimeError as exc:
        assert "garbled model output" in str(exc)
    else:
        raise AssertionError("Expected unparseable JSON to raise with the raw text attached.")


def test_window_ranker_payload_sets_context_window_and_keep_alive(monkeypatch):
    observed = {}

    def fake_request_json(url, payload=None, timeout=10):
        observed["payload"] = payload
        return {"response": '{"selections": []}'}

    monkeypatch.setattr(analyze, "request_json", fake_request_json)

    analyze.call_ollama_window_ranker(
        "http://127.0.0.1:11434",
        "llama3.1:8b",
        "rank these windows",
        window_ids=["W001", "W002"],
        selection_count=2,
    )

    assert observed["payload"]["keep_alive"] == "10m"
    assert observed["payload"]["options"]["num_ctx"] == analyze.RANKING_NUM_CTX
    assert observed["payload"]["options"]["num_predict"] == analyze.RANKING_NUM_PREDICT

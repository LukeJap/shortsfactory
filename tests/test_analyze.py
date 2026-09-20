import pytest
import analyze


@pytest.fixture(autouse=True)
def _no_real_titling_call(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("titling disabled in tests")

    monkeypatch.setattr(analyze, "call_ollama_clip_titler", unavailable)


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
        t += 4.0

    windows = analyze.generate_valid_windows(segments)

    assert windows
    assert all(
        analyze.MIN_CLIP_SECONDS <= window.duration_seconds <= analyze.MAX_CLIP_SECONDS
        for window in windows
    )


def test_candidates_never_start_or_end_mid_sentence():
    segments = [
        analyze.TranscriptSegment(start=0.0, end=4.0, text="Setup line one."),
        analyze.TranscriptSegment(start=6.0, end=9.0, text="Setup line two"),
        analyze.TranscriptSegment(start=11.0, end=15.0, text="continues after a pause."),
        analyze.TranscriptSegment(start=17.0, end=21.0, text="Escalation happens now."),
        analyze.TranscriptSegment(
            start=23.0, end=34.0, text="The payoff lands here for everyone in the room."
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
        t += 4.0

    beats = analyze.build_beats(segments)
    windows = analyze.generate_valid_windows(segments)

    assert len(beats) == 150
    assert len(windows) <= len(analyze.CANDIDATE_DURATION_TARGETS) * len(beats)
    assert len(windows) < len(beats) ** 2


def test_candidates_cover_the_final_beat_of_the_source():
    segments = []
    t = 0.0
    for index in range(60):
        segments.append(
            analyze.TranscriptSegment(start=t, end=t + 2.0, text=f"Beat number {index} happens now.")
        )
        t += 4.0

    beats = analyze.build_beats(segments)
    windows = analyze.generate_valid_windows(segments)

    assert any(window.end == beats[-1].end for window in windows)


def test_dedupe_collapses_near_identical_candidates():
    segments = [
        analyze.TranscriptSegment(start=0.0, end=0.1, text="Hi."),
        analyze.TranscriptSegment(start=2.0, end=3.0, text="Hello there now."),
        analyze.TranscriptSegment(start=5.0, end=8.0, text="Escalation continues for a while here."),
        analyze.TranscriptSegment(
            start=22.0,
            end=26.0,
            text="The payoff finally lands for everyone watching this scene unfold.",
        ),
    ]

    windows = analyze.generate_valid_windows(segments)

    # beats[0] (0.0) and beats[1] (2.0) both qualify as starts for the same
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


def _uniform_segments(count: int, spacing: float = 4.0, length: float = 2.0):
    return [
        analyze.TranscriptSegment(
            start=index * spacing,
            end=index * spacing + length,
            text=f"Beat number {index} happens now.",
        )
        for index in range(count)
    ]


def test_candidates_spread_across_duration_buckets_from_one_start_region():
    windows = analyze.generate_valid_windows(_uniform_segments(60))

    first_start = [window for window in windows if window.start == 0.0]
    assert any(window.duration_seconds < 30 for window in first_start)
    assert any(window.duration_seconds > 70 for window in first_start)


def test_start_beat_after_a_short_gap_produces_no_candidates():
    segments = _uniform_segments(40, spacing=3.0)  # 1.0s gaps < start gap

    windows = analyze.generate_valid_windows(segments)

    assert windows
    assert all(window.start == 0.0 for window in windows)


def test_first_beat_of_the_source_is_always_eligible_as_a_start():
    windows = analyze.generate_valid_windows(_uniform_segments(30))

    assert any(window.start == 0.0 for window in windows)


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

    def fake_ranker(_host, _model, _prompt, *, window_ids, selection_count, detailed=False):
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
    assert calls[5][1] == 9
    assert len(ranked) == 3
    assert all(
        analyze.MIN_CLIP_SECONDS <= window.duration_seconds <= analyze.MAX_CLIP_SECONDS
        for window, _score, _reason in ranked
    )


def test_first_stage_timeout_retries_once_with_smaller_sub_batches(monkeypatch):
    transcript = analyze.TranscriptData(text="Source context", segments=[])
    windows = [_window(index * 60, index * 60 + 60, f"Scene {index}") for index in range(16)]
    calls = []

    def fake_ranker(_host, _model, _prompt, *, window_ids, selection_count, detailed=False):
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

    def fake_ranker(_host, _model, prompt, *, window_ids, selection_count, detailed=False):
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

    def always_empty(_host, _model, _prompt, *, window_ids, selection_count, detailed=False):
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

    def fake_ranker(_host, _model, prompt, *, window_ids, selection_count, detailed=False):
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
    assert observed["payload"]["options"]["num_predict"] == analyze.ranking_num_predict(2)


def test_num_predict_scales_and_num_ctx_grows_only_for_long_prompts():
    assert analyze.ranking_num_predict(12) == 256 + 120 * 12
    assert analyze.ranking_num_predict(12) > analyze.ranking_num_predict(2)
    assert analyze.ranking_num_ctx(2_000, analyze.ranking_num_predict(2)) == 8192
    assert analyze.ranking_num_ctx(22_664, analyze.ranking_num_predict(12)) == 8192
    assert analyze.ranking_num_ctx(500_000, 1_000) == 8192


def _selections_json(count):
    import json as _json

    return _json.dumps(
        {
            "selections": [
                {"window_id": f"W{i:03d}", "score": 90 - i, "reason": 'quote "x" }{ here'}
                for i in range(1, count + 1)
            ]
        },
        indent=2,
    )


def _ranker_with_response(monkeypatch, text, count):
    monkeypatch.setattr(analyze, "request_json", lambda *a, **k: {"response": text})
    return analyze.call_ollama_window_ranker(
        "http://127.0.0.1:11434",
        "llama3.1:8b",
        "rank",
        window_ids=[f"W{i:03d}" for i in range(1, 13)],
        selection_count=count,
    )


def test_truncated_ranking_response_recovers_complete_selections(monkeypatch):
    full = _selections_json(12)
    cut = full.index('"W012"') + 20
    result = _ranker_with_response(monkeypatch, full[:cut], 12)

    assert [item["window_id"] for item in result["selections"]] == [
        f"W{i:03d}" for i in range(1, 12)
    ]
    assert result["selections"][0]["reason"] == 'quote "x" }{ here'


def test_well_formed_ranking_response_is_unchanged_by_salvage(monkeypatch):
    import json as _json

    full = _selections_json(3)
    result = _ranker_with_response(monkeypatch, full, 3)

    assert dict(result) == _json.loads(full)


def test_ranking_response_truncated_before_first_object_still_raises(monkeypatch):
    full = _selections_json(3)
    cut = full.index('"score"')

    with pytest.raises(RuntimeError, match="not valid JSON"):
        _ranker_with_response(monkeypatch, full[:cut], 3)


def _detailed_selection(window_id, h, s, p, k, title="", hook=""):
    return {
        "window_id": window_id,
        "hook_strength": h,
        "self_contained": s,
        "payoff": p,
        "peak": k,
        "title": title,
        "hook": hook,
    }


def test_rubric_sub_scores_sum_in_python_and_highest_total_wins():
    windows = [
        _window(0, 30, "Krabs signs the booth rental agreement."),
        _window(60, 90, "Plankton shares the secret recipe."),
    ]
    result = {
        "selections": [
            _detailed_selection("W001", 10, 10, 10, 10),
            {**_detailed_selection("W002", 25, 20, 22, 18), "score": 5},
        ]
    }

    ranked = analyze.ranked_windows_from_result(result, windows)
    ranked.sort(key=lambda item: -item[1])

    assert [score for _w, score, _r in ranked] == [85, 40]
    assert ranked[0][0] is windows[1]


def _ranked_pair():
    first = _window(0, 30, "You both signed the booth rental agreement. Ninety nine percent.")
    second = _window(60, 90, "Free chewed food? You fed me that all the time as a kid!")
    return [(first, 90, ""), (second, 80, "")]


def test_titling_pass_applies_grounded_titles_and_rejects_generic_ones(monkeypatch):
    calls = []

    def fake_titler(_host, _model, prompt, *, window_ids):
        calls.append((prompt, window_ids))
        return {
            "selections": [
                {
                    "window_id": "W001",
                    "title": "The booth rental agreement",
                    "hook": "You both signed it",
                },
                {"window_id": "W002", "title": "In this episode, a snack", "hook": "Wait for it"},
            ][: len(window_ids)]
        }

    monkeypatch.setattr(analyze, "call_ollama_clip_titler", fake_titler)

    titled = analyze.title_selected_clips("h", "m", _ranked_pair())

    # First call covers both clips; the retry covers only the rejected one.
    assert len(calls) == 2
    assert calls[0][1] == ["W001", "W002"]
    assert calls[1][1] == ["W001"]
    assert titled[0][2].title == "The booth rental agreement"
    assert titled[0][2].hook == "You both signed it"
    assert titled[1][2].title == ""
    assert titled[1][2].hook == ""
    clips = analyze.candidate_clips_from_ranked_windows(titled, {})
    assert clips[0]["title"] == "The booth rental agreement"
    assert clips[1]["title"] == "Free chewed food"


def test_failed_or_timed_out_titling_still_returns_clips_with_local_titles(monkeypatch):
    for error in (RuntimeError("boom"), analyze.WindowRankingTimeout("slow")):
        def failing_titler(*_args, _error=error, **_kwargs):
            raise _error

        monkeypatch.setattr(analyze, "call_ollama_clip_titler", failing_titler)
        ranked = _ranked_pair()

        titled = analyze.title_selected_clips("h", "m", ranked)
        clips = analyze.candidate_clips_from_ranked_windows(titled, {})

        assert titled == ranked
        assert clips[0]["title"] == "You both signed the booth rental agreement"


def test_titling_response_survives_truncation_salvage(monkeypatch):
    import json as _json

    payload = {
        "selections": [
            {"window_id": "W001", "title": "First title", "hook": "First hook"},
            {"window_id": "W002", "title": "Second title", "hook": "Second hook"},
        ]
    }
    text = _json.dumps(payload, indent=2)
    text = text[: text.index('"W002"') + 15]
    monkeypatch.setattr(analyze, "request_json", lambda *a, **k: {"response": text})

    result = analyze.post_for_selections(
        "h",
        "m",
        "p",
        analyze.titling_json_schema(["W001", "W002"]),
        num_predict=400,
        selection_count=2,
        purpose="clip-titling",
    )

    assert result["selections"][0]["title"] == "First title"
    assert result["selections"][0]["hook"] == "First hook"


def test_titling_prompt_contains_only_the_selected_clips():
    prompt = analyze.build_titling_prompt([w for w, _s, _r in _ranked_pair()])

    assert "booth rental agreement. Ninety nine" in prompt
    assert "W001" in prompt and "W002" in prompt and "W003" not in prompt


def test_detailed_schema_has_only_rubric_fields():
    schema = analyze.window_ranking_json_schema(["W001"], 1, detailed=True)
    props = schema["properties"]["selections"]["items"]["properties"]

    assert set(props) == {"window_id", "hook_strength", "self_contained", "payoff", "peak"}


def test_request_timeout_scales_with_num_predict_and_never_drops_below_180():
    assert analyze.ranking_timeout(100) == analyze.REQUEST_TIMEOUT_SECONDS == 180
    assert analyze.ranking_timeout(3136) == 3136 // 5 + 60


def test_final_pass_gets_at_most_24_candidates_chosen_by_first_stage_score(monkeypatch):
    transcript = analyze.TranscriptData(text="Source", segments=[])
    windows = [_window(index * 60, index * 60 + 60, f"Scene {index}") for index in range(80)]
    calls = []

    def fake_ranker(_host, _model, _prompt, *, window_ids, selection_count, detailed=False):
        calls.append((len(window_ids), selection_count, detailed))
        return {
            "selections": [
                {"window_id": window_id, "score": 90 - index, "reason": window_id}
                for index, window_id in enumerate(window_ids[:selection_count])
            ]
        }

    monkeypatch.setattr(analyze, "call_ollama_window_ranker", fake_ranker)

    analyze.rank_exact_minute_windows("h", "m", transcript, windows, target_clip_count=6)

    final_window_count, _count, detailed = calls[-1]
    assert detailed is True
    assert final_window_count <= analyze.FINAL_POOL_MAX_CANDIDATES


def test_duration_quota_limits_long_clips_to_half():
    ranked = [(_window(i * 100, i * 100 + 80, f"long {i}"), 90 - i, "") for i in range(4)] + [
        (_window(500 + i * 100, 500 + i * 100 + 30, f"short {i}"), 50 - i, "") for i in range(4)
    ]

    selected = analyze.select_distinct_ranked_windows(ranked, 4, source_duration=1000)

    assert len(selected) == 4
    assert sum(1 for w, _s, _r in selected if w.duration_seconds > 70) <= 2


def test_region_quota_allows_at_most_two_per_region_when_alternatives_exist():
    ranked = [
        (_window(0, 20, "a"), 95, ""),
        (_window(30, 50, "b"), 94, ""),
        (_window(60, 80, "c"), 93, ""),
        (_window(400, 420, "d"), 50, ""),
    ]

    selected = analyze.select_distinct_ranked_windows(ranked, 3, source_duration=600)

    starts = [w.start for w, _s, _r in selected]
    assert len(selected) == 3
    assert sum(1 for start in starts if start < 200) <= 2
    assert 400 in starts


def test_backfill_relaxes_quotas_but_not_overlap_to_reach_target():
    ranked = [
        (_window(0, 20, "a"), 95, ""),
        (_window(10, 30, "overlaps a"), 94, ""),
        (_window(40, 60, "b"), 93, ""),
        (_window(80, 100, "c"), 92, ""),
    ]

    selected = analyze.select_distinct_ranked_windows(ranked, 3, source_duration=600)

    assert [w.start for w, _s, _r in selected] == [0, 40, 80]


def _titled_reason(title="", hook="", sub_scores=None):
    reason = analyze.RankedReason("")
    reason.title = title
    reason.hook = hook
    reason.sub_scores = sub_scores or {}
    return reason


def test_clip_candidates_carry_no_reason_template_when_a_hook_exists():
    ranked = [
        (
            _window(0, 30, "You both signed the booth rental agreement."),
            88,
            _titled_reason("Who signed the booth deal?", "A contract nobody read."),
        )
    ]

    clips = analyze.candidate_clips_from_ranked_windows(ranked, {})

    assert clips[0]["hook"] == "A contract nobody read."
    assert clips[0]["reason"] == ""
    assert "anchored by" not in str(clips[0]).lower()


def test_rejected_hook_is_left_empty_never_a_transcript_quote():
    windows = [
        _window(0, 30, "You both signed the booth rental agreement. Ninety nine percent."),
        _window(60, 90, "Stop fighting. Hey, honey partner, would you like a sample?"),
    ]
    ranked = [(window, 80, "") for window in windows]
    ranked[1] = (windows[1], 70, _titled_reason("A sample turns into a brawl"))

    clips = analyze.candidate_clips_from_ranked_windows(ranked, {})

    for clip, window in zip(clips, windows):
        assert clip["hook"] == ""
        assert not analyze.is_quote_of_clip(clip["hook"], window)


def test_rubric_sub_scores_are_persisted_on_each_candidate_clip():
    sub_scores = {"hook_strength": 21, "self_contained": 19, "payoff": 23, "peak": 22}
    ranked = [
        (_window(0, 30, "You both signed it."), 85, _titled_reason(sub_scores=sub_scores)),
        (_window(60, 90, "Free food."), 40, ""),
    ]

    clips = analyze.candidate_clips_from_ranked_windows(ranked, {})

    for field, value in sub_scores.items():
        assert clips[0][field] == value
    assert not any(field in clips[1] for field in analyze.RUBRIC_FIELDS)


def _trim_pool(starts_scores, region_count=6, source=600.0):
    pool = [(_window(start, start + 30, f"w{start}"), score, "") for start, score in starts_scores]
    return analyze.trim_shortlist_by_region(pool, region_count, source)


def test_trim_keeps_every_region_that_has_candidates_and_honors_the_cap():
    # 40 strong candidates in the first half, 6 weak ones in the last region.
    early = [(index * 10.0, 90 - index % 5) for index in range(40)]
    late = [(520.0 + index * 10.0, 20) for index in range(6)]

    kept = _trim_pool(early + late)

    assert len(kept) == analyze.FINAL_POOL_MAX_CANDIDATES
    assert any(window.start >= 500 for window, _s, _r in kept)
    assert [w.start for w, _s, _r in kept] == sorted(w.start for w, _s, _r in kept)


def test_trim_fills_the_pool_when_all_candidates_sit_in_one_region():
    kept = _trim_pool([(index * 2.0, 50 + index % 7) for index in range(40)])

    assert len(kept) == analyze.FINAL_POOL_MAX_CANDIDATES


def test_trim_leaves_a_small_shortlist_untouched():
    kept = _trim_pool([(0.0, 50), (300.0, 60)])

    assert len(kept) == 2


def test_cli_clip_line_shows_title_and_hook_not_the_reason(monkeypatch, capsys):
    ranked = [
        (
            _window(0, 30, "You both signed the booth rental agreement."),
            88,
            _titled_reason("Who signed the booth deal?", "A contract nobody read."),
        )
    ]
    clips = analyze.candidate_clips_from_ranked_windows(ranked, {})
    line = analyze.format_clip_log_lines(clips[0], {})

    assert "Booth" in line[0] or "booth" in line[0].lower()
    assert "score 88" in line[0]
    assert line[1].strip().startswith("hook:")
    assert "anchored by" not in " ".join(line).lower()


def test_hook_that_is_a_substring_of_the_clip_transcript_is_rejected():
    window = _window(0, 30, "That won't cover the feed we gave the mule, you cheat.")

    assert analyze.is_quote_of_clip("That won't cover the feed we gave the mule", window)
    assert analyze.is_quote_of_clip("that WON'T cover the feed we gave the mule!", window)
    assert not analyze.is_quote_of_clip("A mule's feed bill sinks the deal", window)
    assert analyze._accepted_title_and_hook(
        {"title": "Who pays for the mule's feed?", "hook": "we gave the mule"}, window
    ) == ("Who pays for the mule's feed?", "")


def test_title_ending_in_a_period_or_under_four_words_is_rejected():
    assert not analyze.is_valid_title("Who pays for the feed.")
    assert not analyze.is_valid_title("Toast is ready")
    assert analyze.is_valid_title("Who really pays for the feed?")


def test_band_quota_returns_two_clips_of_45s_or_longer_when_pool_allows():
    ranked = [(_window(i * 100, i * 100 + 25, f"short {i}"), 90 - i, "") for i in range(6)] + [
        (_window(700 + i * 100, 700 + i * 100 + 50, f"mid {i}"), 40 - i, "") for i in range(3)
    ]

    selected = analyze.select_distinct_ranked_windows(ranked, 4, source_duration=1000)

    assert len(selected) == 4
    assert sum(1 for w, _s, _r in selected if w.duration_seconds >= 45) >= 2
    assert sum(1 for w, _s, _r in selected if w.duration_seconds < 30) <= 2


def test_pool_guard_shrinks_candidates_and_never_raises_num_ctx(monkeypatch):
    transcript = analyze.TranscriptData(text="Source", segments=[])
    long_text = "word " * 400
    pool = [(_window(i * 60, i * 60 + 60, long_text), 90 - i, "") for i in range(26)]

    monkeypatch.setattr(analyze, "RANKING_NUM_CTX", 5000)

    kept = analyze.shrink_pool_to_context(transcript, pool, 10)

    assert 0 < len(kept) < len(pool)
    assert min(score for _w, score, _r in kept) > min(score for _w, score, _r in pool)
    assert analyze.ranking_num_ctx(10_000_000, 5_000) == 8192


def test_target_of_ten_clips_produces_ten_regions():
    ranked = [(_window(i * 60, i * 60 + 40, f"w{i}"), 90 - i, "") for i in range(20)]

    selected = analyze.select_distinct_ranked_windows(ranked, 10, source_duration=1200)

    assert len(selected) == 10
    assert analyze.DEFAULT_CLIP_COUNT == 10


# ---- Task 6: positional scoring bias -------------------------------------

def test_detailed_example_block_is_not_a_descending_ranking():
    import json as _json
    import re as _re

    body = analyze.DETAILED_EXAMPLE_BLOCK
    entries = _json.loads(body[body.index("{"):])["selections"]
    totals = [sum(entry[f] for f in analyze.RUBRIC_FIELDS) for entry in entries]

    assert totals[1] > totals[0]
    # The dimensions disagree with each other inside and across entries.
    for field in analyze.RUBRIC_FIELDS:
        assert entries[0][field] != entries[1][field]
    assert max(entries[0][f] for f in analyze.RUBRIC_FIELDS) > 2 * min(
        entries[0][f] for f in analyze.RUBRIC_FIELDS
    )
    assert _re.search(r"arbitrary", body)


def _content_scoring_ranker(prompts):
    """Scores each window by the 'Scene N' in its own text, not its position."""
    import re as _re

    def fake_ranker(_host, _model, prompt, *, window_ids, selection_count, detailed=False):
        prompts.append(prompt)
        scored = []
        for match in _re.finditer(r"(W\d{3}) \[[^\]]*\] [\d.]+s: Scene (\d+)", prompt):
            scene = int(match.group(2))
            value = (scene * 7) % 25
            scored.append((match.group(1), value))
        scored.sort(key=lambda item: -item[1])
        return {
            "selections": [
                {
                    "window_id": window_id,
                    "hook_strength": value,
                    "self_contained": value,
                    "payoff": value,
                    "peak": value,
                }
                for window_id, value in scored[:selection_count]
            ]
        }

    return fake_ranker


def test_final_pass_shuffle_is_seeded_and_result_is_seed_independent(monkeypatch):
    transcript = analyze.TranscriptData(text="Source context", segments=[])
    windows = [_window(i * 100, i * 100 + 60, f"Scene {i}") for i in range(8)]
    monkeypatch.setattr(analyze, "title_selected_clips", lambda _h, _m, ranked: ranked)

    results = {}
    orders = {}
    for seed in (1, 2, 3):
        prompts = []
        monkeypatch.setattr(analyze, "call_ollama_window_ranker", _content_scoring_ranker(prompts))
        ranked = analyze.rank_exact_minute_windows(
            "h", "m", transcript, windows, target_clip_count=3, seed=seed
        )
        results[seed] = [(w.start, score) for w, score, _r in ranked]
        orders[seed] = [
            line.rsplit("Scene ", 1)[1]
            for line in prompts[-1].splitlines()
            if line.startswith("W0")
        ]

    assert results[1] == results[2] == results[3]
    # Prompt position is decoupled from time: the order differs across seeds.
    assert not (orders[1] == orders[2] == orders[3])
    # Same seed reproduces the same order.
    again, _ = analyze.shuffled_for_final_pass(windows, 1)
    assert again == analyze.shuffled_for_final_pass(windows, 1)[0]
    assert again != windows


def test_shuffled_ids_map_back_to_the_true_windows():
    windows = [_window(i * 100, i * 100 + 60, f"Scene {i}") for i in range(6)]
    order, _seed = analyze.shuffled_for_final_pass(windows, 5)
    result = {
        "selections": [
            _detailed_selection(f"W{i:03d}", i, i, i, i) for i in range(1, 7)
        ]
    }

    ranked = analyze.ranked_windows_from_result(result, order)

    assert [w for w, _s, _r in ranked] == order
    assert sorted(w.start for w, _s, _r in ranked) == [w.start for w in windows]


def test_sub_scores_survive_normalization_into_analysis_candidates():
    windows = [_window(0, 30, "You both signed it.")]
    sub_scores = {"hook_strength": 21, "self_contained": 19, "payoff": 23, "peak": 22}
    ranked = [(windows[0], 85, _titled_reason(sub_scores=sub_scores))]

    clips = analyze.candidate_clips_from_ranked_windows(ranked, {})
    normalized = analyze.normalize_candidate_clips(clips, windows, {})

    for field, value in sub_scores.items():
        assert normalized[0][field] == value


def test_detailed_request_without_sub_scores_logs_a_warning(monkeypatch):
    logs = []
    monkeypatch.setattr(analyze, "log", logs.append)
    monkeypatch.setattr(
        analyze,
        "call_ollama_window_ranker",
        lambda *a, **k: {"selections": [{"window_id": "W001", "score": 80, "reason": "x"}]},
    )
    transcript = analyze.TranscriptData(text="ctx", segments=[])

    analyze.rank_window_request(
        "h", "m", transcript, [_window(0, 30, "Scene 1")], 1,
        request_label="final", context_max_chars=1000, detailed=True,
    )

    assert any("without any" in line and "WARNING" in line for line in logs)

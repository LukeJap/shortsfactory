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


def test_exact_minute_windows_cover_the_full_source_with_a_tail_window():
    segments = [
        analyze.TranscriptSegment(start=0, end=15, text="Opening."),
        analyze.TranscriptSegment(start=15, end=60, text="Early event."),
        analyze.TranscriptSegment(start=60, end=120, text="Middle event."),
        analyze.TranscriptSegment(start=120, end=137, text="Ending payoff."),
    ]

    windows = analyze.generate_valid_windows(segments)

    assert [(window.start, window.end) for window in windows] == [
        (0.0, 60.0),
        (15.0, 75.0),
        (30.0, 90.0),
        (45.0, 105.0),
        (60.0, 120.0),
        (75.0, 135.0),
        (77, 137),
    ]
    assert all(window.duration_seconds == 60.0 for window in windows)
    assert "Ending payoff." in windows[-1].text


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
    windows = [_window(index * 60, index * 60 + 60, f"Scene {index}") for index in range(35)]
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

    # Two chronological batch calls plus the final whole-source shortlist call.
    assert len(calls) == 3
    assert len(calls[0][0]) == analyze.MAX_VALID_WINDOWS_FOR_PROMPT
    assert len(calls[1][0]) == 5
    assert len(calls[2][0]) == 6
    assert len(ranked) == 3
    assert all(window.duration_seconds == 60 for window, _score, _reason in ranked)


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

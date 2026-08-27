import json

import pytest

from recap_intelligence.models import validate_story_map
from recap_intelligence.source import (
    SemanticInterpretationError,
    SemanticStoryInterpreter,
    SourceMismatchError,
    TranscriptData,
    TranscriptSegment,
    _align_fandom_transcript_to_local,
    _align_research_priors,
    _research_priors,
    align_story_map,
    load_transcript,
    validate_story_grounding,
)


class StaticSemanticModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.response_calls = 0

    def generate_json(self, prompt):
        self.calls += 1
        marker = "LOCAL UNIT INTERPRETATION TASK:\n"
        if marker in prompt:
            payload = prompt.split(marker, 1)[1].rsplit(
                "\n\nReturn JSON only.", 1
            )[0]
            units = json.loads(payload)
            return {
                "units": [
                    {
                        "unit_id": unit["unit_id"],
                        "event": "A supported local story event occurs in this unit.",
                        "characters": [],
                        "locations": [],
                        "motivation": "",
                        "change": "",
                        "emotional_conflict": "",
                        "narrative_signal": "unknown",
                        "semantic_confidence": 0.75,
                    }
                    for unit in units
                ],
                "warnings": [],
            }
        response = self.responses[
            min(self.response_calls, len(self.responses) - 1)
        ]
        self.response_calls += 1
        return response


def write_transcript(path):
    path.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "start": 1.0,
                        "end": 3.0,
                        "text": "Alice opens the door.",
                    },
                    {
                        "start": 4.0,
                        "end": 6.0,
                        "text": "Bob hides the package in the hallway.",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def dossier():
    return {
        "ordered_plot_points": [
            {
                "plot_id": "P1",
                "order": 1,
                "summary": "Alice opens the door.",
                "story_purpose": "inciting_incident",
                "characters": ["Alice"],
                "locations": [],
                "causal_parents": [],
            },
            {
                "plot_id": "P2",
                "order": 2,
                "summary": "Bob hides the package in the hallway.",
                "story_purpose": "resolution",
                "characters": ["Bob"],
                "locations": ["hallway"],
                "causal_parents": ["P1"],
            },
        ]
    }


def test_alignment_uses_only_local_transcript_ranges(tmp_path, monkeypatch):
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"source")
    transcript = tmp_path / "subtitles.json"
    write_transcript(transcript)
    monkeypatch.setattr(
        "recap_intelligence.source.probe_duration",
        lambda path: 8.0,
    )

    story_map = align_story_map(
        identity={"canonical_id": "test"},
        dossier=dossier(),
        source_video=source,
        transcript_path=transcript,
        visual_evidence=[
            {
                "start": 1.5,
                "end": 2.5,
                "confidence": 0.9,
                "description": "A door opens.",
            }
        ],
    )

    validate_story_map(story_map)
    first, second = story_map["beats"]
    assert (first["source_start"], first["source_end"]) == (1.0, 3.0)
    assert first["verification_status"] == "verified"
    assert any(
        item["evidence_type"] == "visual_keyframe"
        for item in first["actual_video_evidence_ranges"]
    )
    assert second["causal_parents"] == ["B001"]
    assert "B002" in first["causal_children"]


def test_alignment_stops_when_transcript_cannot_support_any_plot_point(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"source")
    transcript = tmp_path / "subtitles.json"
    transcript.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 1, "end": 2, "text": "A completely unrelated event."}
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "recap_intelligence.source.probe_duration",
        lambda path: 3.0,
    )

    with pytest.raises(SourceMismatchError):
        align_story_map(
            identity={"canonical_id": "test"},
            dossier=dossier(),
            source_video=source,
            transcript_path=transcript,
        )


def test_srt_transcript_parser_keeps_timing(tmp_path):
    path = tmp_path / "captions.srt"
    path.write_text(
        "1\n00:00:01,000 --> 00:00:02,500\nHello there.\n",
        encoding="utf-8",
    )
    transcript = load_transcript(path)
    assert transcript.segments[0].start == 1.0
    assert transcript.segments[0].end == 2.5


def test_single_shared_token_cannot_verify_external_claim(tmp_path, monkeypatch):
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"source")
    transcript = tmp_path / "subtitles.json"
    transcript.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "start": 1,
                        "end": 3,
                        "text": "Bob puts the package in the hallway.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    weak_dossier = {
        "ordered_plot_points": [
            {
                "plot_id": "P1",
                "summary": "A dragon steals treasure after finding a package.",
                "characters": ["Dragon"],
                "locations": ["castle"],
                "causal_parents": [],
            }
        ]
    }
    monkeypatch.setattr("recap_intelligence.source.probe_duration", lambda path: 4.0)

    with pytest.raises(SourceMismatchError):
        align_story_map(
            identity={"canonical_id": "test"},
            dossier=weak_dossier,
            source_video=source,
            transcript_path=transcript,
        )


def test_selected_compound_window_excludes_first_story(tmp_path, monkeypatch):
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"source")
    transcript = tmp_path / "subtitles.json"
    transcript.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "start": 2,
                        "end": 50,
                        "text": "Survival winter hibernation story facts.",
                    },
                    {
                        "start": 70,
                        "end": 90,
                        "text": "Gary follows Patrick and SpongeBob feels rejected.",
                    },
                    {
                        "start": 92,
                        "end": 112,
                        "text": "Gary returns to SpongeBob after finding the cookie.",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    identity = {
        "canonical_id": "fixture:container",
        "series_title": "SpongeBob SquarePants",
        "container_title": "Survival of the Idiots & Dumped",
        "segments": [{"title": "Dumped"}],
    }
    grounded_dossier = {
        "canonical_identity": identity,
        "ordered_plot_points": [],
        "segments": [
            {
                "segment_id": "SEG_01",
                "title": "Dumped",
                "sources": [],
                "ordered_plot_points": [],
            }
        ],
        "source_evaluations": [
            {
                "provider": "tvmaze",
                "status": "accepted",
                "claims": ["Episode title: Dumped"],
            }
        ],
    }
    monkeypatch.setattr("recap_intelligence.source.probe_duration", lambda path: 120.0)
    semantic_model = StaticSemanticModel(
        [
            {
                "beats": [
                    {
                        "semantic_id": "S001",
                        "unit_ids": ["U001"],
                        "summary": "Gary chooses Patrick's company, leaving SpongeBob feeling rejected.",
                        "characters": ["Gary", "Patrick", "SpongeBob"],
                        "locations": [],
                        "motivation": "Gary appears interested in Patrick.",
                        "change": "Gary stops following SpongeBob.",
                        "emotional_conflict": "SpongeBob feels rejected.",
                        "story_purpose": "emotional_turn",
                        "importance": 0.72,
                        "semantic_confidence": 0.82,
                        "payoff_significance": "",
                    },
                    {
                        "semantic_id": "S002",
                        "unit_ids": ["U002"],
                        "summary": "Gary returns after his interest in Patrick is explained.",
                        "characters": ["Gary", "Patrick", "SpongeBob"],
                        "locations": [],
                        "motivation": "",
                        "change": "SpongeBob and Gary reunite.",
                        "emotional_conflict": "",
                        "story_purpose": "payoff_climax",
                        "importance": 0.95,
                        "semantic_confidence": 0.86,
                        "payoff_significance": "The explanation recontextualizes Gary's behavior.",
                    },
                ],
                "causal_links": [
                    {
                        "parent_id": "S001",
                        "child_id": "S002",
                        "reason": "Gary's apparent rejection creates the misunderstanding resolved later.",
                    }
                ],
                "warnings": [],
            }
        ]
    )

    story_map = align_story_map(
        identity=identity,
        dossier=grounded_dossier,
        source_video=source,
        transcript_path=transcript,
        scene_boundaries=[60.0],
        semantic_interpreter=SemanticStoryInterpreter(semantic_model),
    )

    validate_story_grounding(story_map, grounded_dossier)
    assert story_map["selected_source_window"]["start"] == 60.0
    assert all(beat["source_start"] >= 60.0 for beat in story_map["beats"])
    assert "Survival winter" not in str(story_map["beats"])


def test_semantic_story_has_meaningful_entities_roles_and_causality(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"source")
    transcript = tmp_path / "subtitles.json"
    transcript.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 1, "end": 10, "text": "Maya brings a sealed letter to Noah."},
                    {"start": 14, "end": 23, "text": "Noah refuses to open it and walks away."},
                    {"start": 27, "end": 36, "text": "Maya asks Lena to help change his mind."},
                    {"start": 40, "end": 49, "text": "Lena reveals the letter was written by Noah's mother."},
                    {"start": 53, "end": 62, "text": "Noah returns, reads it, and apologizes to Maya."},
                ]
            }
        ),
        encoding="utf-8",
    )
    response = {
        "beats": [
            {
                "semantic_id": "S001", "unit_ids": ["U001"],
                "summary": "Maya delivers a sealed letter to Noah.",
                "characters": ["Maya", "Noah"], "locations": [],
                "motivation": "Maya wants Noah to receive the message.",
                "change": "Noah receives the letter.", "emotional_conflict": "",
                "story_purpose": "setup", "importance": 0.45,
                "semantic_confidence": 0.88, "payoff_significance": "",
            },
            {
                "semantic_id": "S002", "unit_ids": ["U002"],
                "summary": "Noah rejects the message without reading it.",
                "characters": ["Noah"], "locations": [],
                "motivation": "Noah wants to avoid the message.",
                "change": "The delivery fails.", "emotional_conflict": "Noah resists Maya's effort.",
                "story_purpose": "inciting_incident", "importance": 0.7,
                "semantic_confidence": 0.84, "payoff_significance": "",
            },
            {
                "semantic_id": "S003", "unit_ids": ["U003"],
                "summary": "Maya recruits Lena to help reach Noah.",
                "characters": ["Maya", "Lena", "Noah"], "locations": [],
                "motivation": "Maya still wants Noah to read the letter.",
                "change": "Lena joins Maya's effort.", "emotional_conflict": "",
                "story_purpose": "attempt_failure", "importance": 0.58,
                "semantic_confidence": 0.82, "payoff_significance": "",
            },
            {
                "semantic_id": "S004", "unit_ids": ["U004"],
                "summary": "Lena reveals that Noah's mother wrote the letter.",
                "characters": ["Lena", "Noah"], "locations": [],
                "motivation": "Lena explains why the letter matters.",
                "change": "The message gains personal significance.", "emotional_conflict": "",
                "story_purpose": "reversal_reveal", "importance": 0.94,
                "semantic_confidence": 0.9,
                "payoff_significance": "The sender's identity recontextualizes Noah's refusal.",
            },
            {
                "semantic_id": "S005", "unit_ids": ["U005"],
                "summary": "Noah reads the letter and reconciles with Maya.",
                "characters": ["Noah", "Maya"], "locations": [],
                "motivation": "Noah accepts the personal message.",
                "change": "The conflict is resolved.", "emotional_conflict": "",
                "story_purpose": "resolution", "importance": 0.86,
                "semantic_confidence": 0.89, "payoff_significance": "",
            },
        ],
        "causal_links": [
            {"parent_id": "S001", "child_id": "S002", "reason": "Receiving the letter gives Noah the choice to reject it."},
            {"parent_id": "S002", "child_id": "S003", "reason": "Noah's refusal causes Maya to seek Lena's help."},
            {"parent_id": "S004", "child_id": "S005", "reason": "Learning who wrote the letter persuades Noah to read it."},
        ],
        "warnings": [],
    }
    monkeypatch.setattr("recap_intelligence.source.probe_duration", lambda path: 70.0)
    story_map = align_story_map(
        identity={"canonical_id": "test", "series_title": "Example"},
        dossier={"ordered_plot_points": []},
        source_video=source,
        transcript_path=transcript,
        scene_boundaries=[9, 22, 35, 48, 61],
        semantic_interpreter=SemanticStoryInterpreter(
            StaticSemanticModel([response])
        ),
    )

    validate_story_grounding(story_map, {"ordered_plot_points": []})
    assert all(
        beat["summary"] not in " ".join(
            item.get("transcript_excerpt", "")
            for item in beat["actual_video_evidence_ranges"]
        )
        for beat in story_map["beats"]
    )
    assert {"Maya", "Noah", "Lena"} <= {
        character for beat in story_map["beats"] for character in beat["characters"]
    }
    assert len({beat["importance"] for beat in story_map["beats"]}) > 1
    assert any(
        beat["story_purpose"] == "reversal_reveal"
        and beat["payoff_significance"]
        for beat in story_map["beats"]
    )
    assert "B004" not in story_map["beats"][2]["causal_children"]
    assert all(edge["reason"] for edge in story_map["causal_graph"])


def test_garbled_evidence_caps_semantic_confidence(tmp_path, monkeypatch):
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"source")
    transcript = tmp_path / "subtitles.json"
    transcript.write_text(
        json.dumps({"segments": [{"start": 1, "end": 20, "text": "brush " * 24}]}),
        encoding="utf-8",
    )
    response = {
        "beats": [
            {
                "semantic_id": "S001", "unit_ids": ["U001"],
                "summary": "A speaker repeats an unclear phrase.",
                "characters": [], "locations": [], "motivation": "",
                "change": "", "emotional_conflict": "",
                "story_purpose": "supporting_event", "importance": 0.2,
                "semantic_confidence": 0.95, "payoff_significance": "",
            }
        ],
        "causal_links": [], "warnings": ["Evidence is repetitive."],
    }
    monkeypatch.setattr("recap_intelligence.source.probe_duration", lambda path: 22.0)
    story_map = align_story_map(
        identity={"canonical_id": "test"},
        dossier={"ordered_plot_points": []},
        source_video=source,
        transcript_path=transcript,
        semantic_interpreter=SemanticStoryInterpreter(
            StaticSemanticModel([response])
        ),
    )

    assert story_map["beats"][0]["semantic_confidence"] < 0.7
    assert story_map["beats"][0]["actual_video_evidence_ranges"][0]["confidence"] == 0.98


def test_semantic_interpreter_repairs_copied_transcript(tmp_path):
    units = [
        {
            "unit_id": "U001", "start": 1.0, "end": 5.0,
            "transcript": "Maya brings a sealed letter to Noah and asks him to read it now.",
            "context_before": "", "context_after": "", "evidence_quality": 0.9,
        }
    ]
    invalid = {
        "beats": [{
            "semantic_id": "S001", "unit_ids": ["U001"],
            "summary": units[0]["transcript"], "characters": ["Maya", "Noah"],
            "locations": [], "motivation": "", "change": "",
            "emotional_conflict": "", "story_purpose": "setup",
            "importance": 0.5, "semantic_confidence": 0.8,
            "payoff_significance": "",
        }],
        "causal_links": [], "warnings": [],
    }
    repaired = {
        "beats": [{
            **invalid["beats"][0],
            "summary": "Maya delivers an important letter to Noah.",
        }],
        "causal_links": [], "warnings": [],
    }
    model = StaticSemanticModel([invalid, repaired])
    interpreter = SemanticStoryInterpreter(model)
    interpreter.set_debug_dir(tmp_path / "debug")

    result = interpreter.interpret(
        units=units,
        identity={"series_title": "Example"},
        segment_id="",
    )

    assert model.response_calls == 2
    assert result["beats"][0]["summary"] == repaired["beats"][0]["summary"]
    assert interpreter.last_diagnostics["repair_attempt_count"] == 1
    assert (tmp_path / "debug" / "semantic_story_diagnostics.json").exists()


def test_local_semantic_interpretation_accepts_concise_complete_event():
    units = [
        {
            "unit_id": "U001",
            "start": 1.0,
            "end": 3.0,
            "transcript": "Tag, you're it, Patrick.",
            "context_before": "",
            "context_after": "",
            "evidence_quality": 0.9,
        }
    ]
    raw = {
        "units": [
            {
                "unit_id": "U001",
                "event": "Patrick is tagged.",
                "characters": ["Patrick"],
                "locations": [],
                "motivation": "play",
                "change": "Patrick becomes it.",
                "emotional_conflict": "",
                "narrative_signal": "turn",
                "semantic_confidence": 0.8,
            }
        ]
    }

    normalized = SemanticStoryInterpreter._normalize_unit_interpretations(raw, units)

    assert normalized[0]["event"] == "Patrick is tagged."


def test_fandom_event_fuzzily_aligns_to_noisy_local_asr():
    priors = [
        {
            "prior_id": "FANDOM_P009",
            "prior_type": "episode_plot",
            "order": 9,
            "event": "Gary followed Patrick because a cookie was hidden in his shorts.",
            "story_purpose": "reversal",
            "timing_authority": "none",
        }
    ]
    units = [
        {
            "unit_id": "U001",
            "start": 80.0,
            "end": 94.0,
            "transcript": "Garry went after Patric for the cooky in his pocket.",
        }
    ]

    alignments, by_unit = _align_research_priors(priors, units, [79.5, 94.2])

    assert alignments[0]["alignment_status"] == "candidate"
    assert alignments[0]["candidate_local_ranges"][0]["unit_id"] == "U001"
    assert alignments[0]["candidate_local_ranges"][0]["start"] == 80.0
    assert by_unit["U001"][0]["timing_authority"] == "none"


def test_fandom_claim_alone_cannot_create_verified_beat(tmp_path, monkeypatch):
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"source")
    transcript = tmp_path / "subtitles.json"
    transcript.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "start": 1,
                        "end": 5,
                        "text": "The weather report predicts a calm afternoon.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    prior_only = {
        "ordered_plot_points": [
            {
                "plot_id": "FANDOM_P001",
                "summary": "Gary returns after finding a cookie in Patrick's pocket.",
                "characters": ["Gary", "Patrick"],
                "locations": [],
                "provenance": [{"provider": "fandom", "url": "https://fandom.test"}],
            }
        ]
    }
    monkeypatch.setattr("recap_intelligence.source.probe_duration", lambda path: 6.0)

    with pytest.raises(SourceMismatchError):
        align_story_map(
            identity={"canonical_id": "test"},
            dossier=prior_only,
            source_video=source,
            transcript_path=transcript,
        )


def test_local_semantic_event_wins_when_fandom_prior_conflicts(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"source")
    transcript = tmp_path / "subtitles.json"
    transcript.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "start": 1,
                        "end": 8,
                        "text": "Maya opens the red door and walks inside.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    prior = {
        "ordered_plot_points": [
            {
                "plot_id": "FANDOM_P001",
                "order": 1,
                "summary": "Maya closes the red door and remains outside.",
                "story_purpose": "setup",
                "characters": ["Maya"],
                "locations": [],
                "provenance": [{"provider": "fandom", "url": "https://fandom.test"}],
            }
        ]
    }
    response = {
        "beats": [
            {
                "semantic_id": "S001",
                "unit_ids": ["U001"],
                "summary": "Maya opens the door and enters.",
                "characters": ["Maya"],
                "locations": [],
                "motivation": "",
                "change": "Maya moves inside.",
                "emotional_conflict": "",
                "story_purpose": "setup",
                "importance": 0.5,
                "semantic_confidence": 0.9,
                "payoff_significance": "",
            }
        ],
        "causal_links": [],
        "warnings": [],
    }
    monkeypatch.setattr("recap_intelligence.source.probe_duration", lambda path: 9.0)

    story_map = align_story_map(
        identity={"canonical_id": "test", "series_title": "Example"},
        dossier=prior,
        source_video=source,
        transcript_path=transcript,
        semantic_interpreter=SemanticStoryInterpreter(StaticSemanticModel([response])),
    )

    assert story_map["research_prior_alignments"][0]["alignment_status"] == "candidate"
    assert story_map["beats"][0]["summary"] == "Maya opens the door and enters."
    assert "closes" not in story_map["beats"][0]["summary"]


def test_research_priors_keep_compound_selected_segment_isolated():
    dossier = {
        "segments": [
            {"segment_id": "SEG_A", "title": "Survival of the Idiots"},
            {"segment_id": "SEG_B", "title": "Dumped"},
        ],
        "ordered_plot_points": [
            {
                "plot_id": "SEG_A_P001",
                "segment_id": "SEG_A",
                "order": 1,
                "summary": "Sandy hibernates during winter.",
            },
            {
                "plot_id": "SEG_B_P001",
                "segment_id": "SEG_B",
                "order": 1,
                "summary": "Gary chooses to follow Patrick.",
            },
        ],
        "transcript_events": [],
    }

    priors = _research_priors(dossier, {"selected_titles": ["Dumped"]})

    assert [prior["prior_id"] for prior in priors] == ["SEG_B_P001"]


def test_story_synthesis_repairs_uniform_roles_and_missing_causality():
    units = [
        {
            "unit_id": f"U{index:03d}",
            "start": float(index * 10),
            "end": float(index * 10 + 8),
            "transcript": f"Distinct local evidence for event number {index}.",
            "context_before": "",
            "context_after": "",
            "evidence_quality": 0.9,
        }
        for index in range(1, 5)
    ]
    invalid_beats = [
        {
            "semantic_id": f"S{index:03d}",
            "unit_ids": [f"U{index:03d}"],
            "summary": f"A distinct semantic story event happens at stage {index}.",
            "characters": ["Maya"],
            "locations": [],
            "motivation": "Maya advances her goal.",
            "change": f"The story reaches stage {index}.",
            "emotional_conflict": "",
            "story_purpose": purpose,
            "importance": 0.5,
            "semantic_confidence": 0.8,
            "payoff_significance": "",
        }
        for index, purpose in enumerate(
            ("setup", "escalation", "escalation", "resolution"),
            start=1,
        )
    ]
    invalid = {"beats": invalid_beats, "causal_links": [], "warnings": []}
    repaired_beats = [dict(beat) for beat in invalid_beats]
    repaired_beats[0]["importance"] = 0.35
    repaired_beats[1]["importance"] = 0.62
    repaired_beats[2].update(
        {
            "story_purpose": "reversal_reveal",
            "importance": 0.94,
            "payoff_significance": "New information changes the conflict.",
        }
    )
    repaired_beats[3].update(
        {
            "story_purpose": "payoff_climax",
            "importance": 0.88,
            "payoff_significance": "The central conflict pays off.",
        }
    )
    repaired = {
        "beats": repaired_beats,
        "causal_links": [
            {
                "parent_id": "S002",
                "child_id": "S003",
                "reason": "The failed escalation exposes the new information.",
            },
            {
                "parent_id": "S003",
                "child_id": "S004",
                "reason": "The reveal directly enables the final payoff.",
            },
        ],
        "warnings": [],
    }
    model = StaticSemanticModel([invalid, repaired])

    result = SemanticStoryInterpreter(model).interpret(
        units=units,
        identity={"series_title": "Example"},
        segment_id="",
        research_hints=[
            {"story_purpose": "reversal", "event": "A supported reveal."},
            {"story_purpose": "payoff_climax", "event": "A supported payoff."},
        ],
    )

    assert model.response_calls == 2
    assert {beat["story_purpose"] for beat in result["beats"]} >= {
        "reversal_reveal",
        "payoff_climax",
    }
    assert result["beats"][2]["causal_children"] == ["B004"]


def hybrid_units():
    events = [
        ("Maya receives a sealed letter from Noah.", "setup"),
        ("Noah refuses the letter and leaves Maya behind.", "escalation"),
        ("Maya asks Lena to help deliver the letter.", "attempt"),
        ("Lena reveals that Noah's mother wrote the letter.", "reveal"),
        ("Noah reads the letter and returns to Maya.", "payoff"),
        ("Noah apologizes and reconciles with Maya.", "resolution"),
    ]
    return [
        {
            "unit_id": f"U{index:03d}",
            "start": float(index * 10),
            "end": float(index * 10 + 6),
            "transcript": event,
            "event": event,
            "characters": ["Maya", "Noah"],
            "locations": [],
            "motivation": "Maya wants Noah to read the letter.",
            "change": event,
            "emotional_conflict": "Noah resists Maya.",
            "narrative_signal": signal,
            "semantic_confidence": 0.9,
            "evidence_quality": 0.9,
            "candidate_priors": [],
        }
        for index, (event, signal) in enumerate(events, start=1)
    ]


def hybrid_hints():
    purposes = (
        "setup",
        "inciting_incident",
        "attempt_failure",
        "reversal",
        "payoff_climax",
        "resolution",
    )
    return [
        {
            "prior_id": f"P{index}",
            "prior_type": "episode_plot",
            "order": index,
            "event": unit["event"],
            "story_purpose": purpose,
            "characters": unit["characters"],
            "candidate_unit_ids": [unit["unit_id"]],
            "candidate_local_ranges": [
                {
                    "unit_id": unit["unit_id"],
                    "start": unit["start"],
                    "end": unit["end"],
                    "confidence": 0.88,
                }
            ],
            "alignment_confidence": 0.88,
        }
        for index, (unit, purpose) in enumerate(
            zip(hybrid_units(), purposes), start=1
        )
    ]


def valid_hybrid_refinement(skeleton):
    groups = []
    for index, item in enumerate(skeleton, start=1):
        groups.append(
            {
                "group_id": f"G{index:03d}",
                "skeleton_ids": [item["skeleton_id"]],
                "summary": (
                    "A grounded story turn occurs as " + item["semantic_event"]
                ),
                "motivation": item["motivation"],
                "change": item["change"],
                "emotional_conflict": item["emotional_conflict"],
                "payoff_significance": (
                    "This grounded event changes the central conflict."
                    if item["story_purpose"] in {"reversal_reveal", "payoff_climax"}
                    else ""
                ),
                "importance_adjustment": 0.0,
            }
        )
    return {
        "groups": groups,
        "causal_links": [
            {
                "parent_group_id": "G002",
                "child_group_id": "G003",
                "reason": "Noah's refusal causes Maya to seek help with the letter.",
            }
        ],
        "warnings": [],
    }


def test_hybrid_skeleton_preserves_supported_protected_roles_and_importance():
    skeleton, exclusions = SemanticStoryInterpreter._build_story_skeleton(
        hybrid_units(), hybrid_hints()
    )

    assert not exclusions
    assert {item["story_purpose"] for item in skeleton if item["protected"]} >= {
        "reversal_reveal",
        "payoff_climax",
        "resolution",
    }
    assert len({item["importance_prior"] for item in skeleton}) >= 4


def test_hybrid_skeleton_does_not_promote_unaligned_or_contradicted_prior():
    units = hybrid_units()
    hints = hybrid_hints()
    hints[0]["event"] = "Maya closes the red door and remains outside."
    hints[0]["candidate_unit_ids"] = ["U001"]
    hints[0]["candidate_local_ranges"] = [
        {"unit_id": "U001", "start": 10.0, "end": 16.0, "confidence": 0.9}
    ]
    units[0]["event"] = "Maya opens the red door and enters the room."
    hints.append(
        {
            "prior_id": "P_UNALIGNED",
            "prior_type": "episode_plot",
            "order": 7,
            "event": "A dragon steals treasure from a distant castle.",
            "story_purpose": "payoff_climax",
            "candidate_unit_ids": [],
            "candidate_local_ranges": [],
        }
    )

    skeleton, exclusions = SemanticStoryInterpreter._build_story_skeleton(units, hints)

    admitted = {item["research_id"] for item in skeleton}
    assert "P1" not in admitted
    assert "P_UNALIGNED" not in admitted
    assert {item["research_id"] for item in exclusions} >= {"P1", "P_UNALIGNED"}


def test_hybrid_skeleton_fills_large_gap_from_high_confidence_local_turn():
    units = hybrid_units()
    units[3]["event"] = "Noah chooses Lena, leaving Maya heartbroken and rejected."
    units[3]["narrative_signal"] = "turn"
    hints = [hybrid_hints()[index] for index in (0, 1, 4, 5)]

    skeleton, _ = SemanticStoryInterpreter._build_story_skeleton(units, hints)

    local_turn = next(
        item for item in skeleton if item["semantic_event"] == units[3]["event"]
    )
    assert local_turn["research_id"] == ""
    assert local_turn["story_purpose"] == "emotional_turn"
    assert local_turn["semantic_unit_support"][0]["alignment_method"] == (
        "local_semantic_gap_fill"
    )


def test_hybrid_choice_target_conflict_is_detected():
    assert SemanticStoryInterpreter._events_conflict(
        "Gary is given the choice and heads for Patrick.",
        "Gary chooses to go with SpongeBob.",
    )


def test_hybrid_refinement_cannot_remove_protected_payoff():
    units = hybrid_units()
    skeleton, _ = SemanticStoryInterpreter._build_story_skeleton(
        units, hybrid_hints()
    )
    raw = valid_hybrid_refinement(skeleton)
    payoff_id = next(
        item["skeleton_id"]
        for item in skeleton
        if item["story_purpose"] == "payoff_climax"
    )
    raw["groups"] = [
        group for group in raw["groups"] if payoff_id not in group["skeleton_ids"]
    ]

    with pytest.raises(SemanticInterpretationError, match="protected"):
        SemanticStoryInterpreter._normalize_hybrid_refinement(
            raw, skeleton, units, "SEG_01", None
        )


def test_hybrid_merge_preserves_repeated_local_evidence_ranges():
    units = hybrid_units()
    skeleton, _ = SemanticStoryInterpreter._build_story_skeleton(
        units, hybrid_hints()
    )
    first_attempt = next(
        item for item in skeleton if item["story_purpose"] == "attempt_failure"
    )
    duplicate = dict(first_attempt)
    duplicate["skeleton_id"] = "K999"
    duplicate["research_id"] = "P_REPEAT"
    duplicate["semantic_unit_support"] = [
        {
            "unit_id": "U002",
            "start": units[1]["start"],
            "end": units[1]["end"],
            "event": units[1]["event"],
            "alignment_confidence": 0.8,
            "alignment_method": "semantic_local_alignment",
        }
    ]
    skeleton.append(duplicate)
    second_duplicate = dict(first_attempt)
    second_duplicate["skeleton_id"] = "K998"
    second_duplicate["research_id"] = "P_REPEAT_2"
    second_duplicate["semantic_unit_support"] = [
        {
            "unit_id": "U004",
            "start": units[3]["start"],
            "end": units[3]["end"],
            "event": units[3]["event"],
            "alignment_confidence": 0.8,
            "alignment_method": "semantic_local_alignment",
        }
    ]
    skeleton.append(second_duplicate)
    raw = valid_hybrid_refinement(skeleton)
    attempt_group = next(
        group for group in raw["groups"]
        if first_attempt["skeleton_id"] in group["skeleton_ids"]
    )
    raw["groups"] = [
        group for group in raw["groups"] if group["skeleton_ids"] != ["K999"]
    ]
    attempt_group["skeleton_ids"].append("K999")

    result = SemanticStoryInterpreter._normalize_hybrid_refinement(
        raw, skeleton, units, "SEG_01", None
    )
    merged = next(
        beat for beat in result["beats"] if "P_REPEAT" in beat["research_plot_ids"]
    )

    assert merged["semantic_unit_ids"] == ["U002", "U003"]
    assert len(merged["actual_video_evidence_ranges"]) == 2


def test_hybrid_refinement_does_not_preserve_adjacency_as_causality():
    units = hybrid_units()
    skeleton, _ = SemanticStoryInterpreter._build_story_skeleton(
        units, hybrid_hints()
    )
    raw = valid_hybrid_refinement(skeleton)
    raw["causal_links"][0]["reason"] = "This happens next after the earlier event."

    result = SemanticStoryInterpreter._normalize_hybrid_refinement(
        raw, skeleton, units, "SEG_01", None
    )

    assert not any(
        edge["reason"] == "This happens next after the earlier event."
        for beat in result["beats"]
        for edge in beat["causal_reasoning"]
    )
    assert any(
        beat["story_purpose"] == "payoff_climax" and beat["causal_parents"]
        for beat in result["beats"]
    )


def rich_depth():
    return {
        "level": "RICH",
        "route": "fandom_first_verified_story",
        "metrics": {},
        "checks": {},
        "reasons": ["fixture"],
    }


def test_fandom_transcript_alignment_is_monotonic_and_content_gated():
    events = [
        {
            "event_id": "T1",
            "order": 1,
            "speaker": "Ari",
            "dialogue": "Unlock the vault.",
            "actions": [],
        },
        {
            "event_id": "T2",
            "order": 2,
            "speaker": "Bea",
            "dialogue": "Pull the red lever.",
            "actions": [],
        },
        {
            "event_id": "T3",
            "order": 3,
            "speaker": "Ari",
            "dialogue": "The crown vanished.",
            "actions": [],
        },
    ]
    transcript = TranscriptData(
        path="fixture.json",
        segments=(
            TranscriptSegment(1, 2, "The red lever is only decoration."),
            TranscriptSegment(3, 4, "Unlock the vault now."),
            TranscriptSegment(5, 6, "Pull the red lever."),
            TranscriptSegment(7, 8, "The weather stays calm."),
        ),
        full_text="",
        duration=8,
    )

    aligned = _align_fandom_transcript_to_local(
        events,
        transcript,
        {"start": 0.0, "end": 8.0},
        ["Ari", "Bea"],
    )

    assert aligned[0]["candidate_local_ranges"][0]["start"] == 3
    assert aligned[1]["candidate_local_ranges"][0]["start"] == 5
    assert aligned[2]["candidate_local_ranges"] == []


def test_rich_path_verifies_plot_through_transcript_bridge_and_keeps_speakers(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"source")
    transcript_path = tmp_path / "subtitles.json"
    transcript_path.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 1, "end": 3, "text": "Ari loses the family treasure."},
                    {"start": 4, "end": 6, "text": "It's under the blue cushion."},
                    {"start": 7, "end": 9, "text": "Ari realizes the truth."},
                    {"start": 10, "end": 12, "text": "The treasure is safely returned."},
                    {"start": 13, "end": 15, "text": "Bea walks home relieved."},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("recap_intelligence.source.probe_duration", lambda path: 16.0)
    plot_points = [
        {
            "plot_id": "P1",
            "order": 1,
            "summary": "Ari loses the family treasure.",
            "story_purpose": "inciting_incident",
            "characters": ["Ari"],
            "causal_parents": [],
        },
        {
            "plot_id": "P2",
            "order": 2,
            "summary": "The hidden heirloom is discovered beneath the furniture.",
            "story_purpose": "reversal",
            "characters": ["Ari"],
            "motivation": "recover the missing heirloom",
            "causal_parents": [],
        },
        {
            "plot_id": "P3",
            "order": 3,
            "summary": "Ari realizes the truth.",
            "story_purpose": "payoff_climax",
            "characters": ["Ari"],
            "causal_parents": ["P2"],
        },
        {
            "plot_id": "P4",
            "order": 4,
            "summary": "The treasure is safely returned.",
            "story_purpose": "payoff_climax",
            "characters": ["Ari"],
            "causal_parents": ["P3"],
        },
        {
            "plot_id": "P5",
            "order": 5,
            "summary": "Bea walks home relieved.",
            "story_purpose": "resolution",
            "characters": ["Bea"],
            "causal_parents": ["P4"],
        },
    ]
    transcript_events = [
        {
            "event_id": "T1",
            "order": 1,
            "speaker": "Ari",
            "dialogue": "I lost the family treasure.",
            "actions": [],
        },
        {
            "event_id": "T2",
            "order": 2,
            "speaker": "Bea",
            "dialogue": "The heirloom is under the blue cushion.",
            "actions": [],
        },
        {
            "event_id": "T3",
            "order": 3,
            "speaker": "Ari",
            "dialogue": "I realize the truth.",
            "actions": [],
        },
        {
            "event_id": "T4",
            "order": 4,
            "speaker": "Ari",
            "dialogue": "The treasure is safely returned.",
            "actions": [],
        },
        {
            "event_id": "T5",
            "order": 5,
            "speaker": "Bea",
            "dialogue": "I walk home relieved.",
            "actions": [],
        },
    ]
    story_map = align_story_map(
        identity={"canonical_id": "fixture", "series_title": "Fixture"},
        dossier={
            "ordered_plot_points": plot_points,
            "transcript_events": transcript_events,
            "characters": ["Ari", "Bea"],
        },
        source_video=source,
        transcript_path=transcript_path,
        research_depth=rich_depth(),
    )

    validate_story_map(story_map)
    bridge = next(beat for beat in story_map["beats"] if "P2" in beat["research_plot_ids"])
    assert bridge["verification_method"] == "fandom_transcript_bridge"
    assert bridge["source_start"] == 4
    assert bridge["speaker_attributions"][0]["speaker"] == "Bea"
    assert bridge["motivation"] == "recover the missing heirloom"
    assert "revenge" not in str(story_map).casefold()
    assert {beat["story_purpose"] for beat in story_map["beats"]} >= {
        "reversal_reveal",
        "payoff_climax",
        "resolution",
    }
    assert max(beat["importance"] for beat in story_map["beats"]) - min(
        beat["importance"] for beat in story_map["beats"]
    ) > 0.1
    assert story_map["fast_path_diagnostics"]["semantic_llm_call_count"] == 0


def test_rich_path_records_local_conflict_and_local_source_wins(tmp_path, monkeypatch):
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"source")
    transcript_path = tmp_path / "subtitles.json"
    transcript_path.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 1, "end": 3, "text": "Maya opens the red door."},
                    {"start": 4, "end": 6, "text": "Maya finds the key."},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("recap_intelligence.source.probe_duration", lambda path: 7.0)
    story_map = align_story_map(
        identity={"canonical_id": "fixture"},
        dossier={
            "ordered_plot_points": [
                {
                    "plot_id": "P1",
                    "summary": "Maya closes the red door.",
                    "story_purpose": "setup",
                    "characters": ["Maya"],
                },
                {
                    "plot_id": "P2",
                    "summary": "Maya finds the key.",
                    "story_purpose": "resolution",
                    "characters": ["Maya"],
                },
            ],
            "transcript_events": [
                {
                    "event_id": "T1",
                    "order": 1,
                    "speaker": "Maya",
                    "dialogue": "Maya closes the red door.",
                    "actions": [],
                },
                {
                    "event_id": "T2",
                    "order": 2,
                    "speaker": "Maya",
                    "dialogue": "Maya finds the key.",
                    "actions": [],
                },
            ],
            "characters": ["Maya"],
        },
        source_video=source,
        transcript_path=transcript_path,
        research_depth=rich_depth(),
    )

    assert [beat["research_plot_ids"] for beat in story_map["beats"]] == [["P2"]]
    assert story_map["research_conflicts"][0]["research_plot_id"] == "P1"
    assert story_map["research_conflicts"][0]["resolution"] == "local_source_wins_range_excluded"

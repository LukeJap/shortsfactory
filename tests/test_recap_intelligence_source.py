import json

import pytest

from recap_intelligence.models import validate_story_map
from recap_intelligence.source import (
    SemanticStoryInterpreter,
    SourceMismatchError,
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

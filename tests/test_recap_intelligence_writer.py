import json

import pytest

from recap_intelligence.llm import (
    ModelGeneration,
    OllamaJsonModel,
    parse_json_object,
)
from recap_intelligence.models import validate_recap_script
from recap_intelligence.writer import (
    RecapWriter,
    RecapWritingConfig,
    RecapWritingError,
    TemplateRecapWriter,
)


def story_map(*, with_payoff=False):
    beats = [
        {
            "beat_id": "B001",
            "chronological_order": 1,
            "summary": "Alice opens the door.",
            "story_purpose": "inciting_incident",
            "verification_status": "verified",
            "importance": 0.9,
            "causal_parents": [],
            "causal_children": ["B002"] if with_payoff else [],
            "actual_video_evidence_ranges": [
                {
                    "start": 1.0,
                    "end": 3.0,
                    "confidence": 0.8,
                    "evidence_type": "transcript",
                }
            ],
            "original_dialogue_candidates": [
                {
                    "start": 1.4,
                    "end": 2.2,
                    "score": 0.7,
                    "reason": "A concise source line.",
                }
            ],
        }
    ]
    if with_payoff:
        beats.append(
            {
                "beat_id": "B002",
                "chronological_order": 2,
                "summary": "The package behind it solves the mystery.",
                "story_purpose": "climax_payoff",
                "verification_status": "verified",
                "importance": 1.0,
                "causal_parents": ["B001"],
                "causal_children": [],
                "actual_video_evidence_ranges": [
                    {
                        "start": 4.0,
                        "end": 7.0,
                        "confidence": 0.9,
                        "evidence_type": "transcript",
                    }
                ],
                "original_dialogue_candidates": [],
            }
        )
    return {"beats": beats}


def outline(*, with_payoff=False):
    return {
        "hook": {"beat_ids": ["B001"], "intent": "Immediate mystery"},
        "minimum_setup": {"beat_ids": [], "intent": "No setup needed"},
        "essential_causal_chain": [
            {"beat_ids": ["B001"], "intent": "The action starts the story"},
            *(
                [
                    {
                        "beat_ids": ["B002"],
                        "intent": "The discovery pays off the action",
                    }
                ]
                if with_payoff
                else []
            ),
        ],
        "escalation_beats": [],
        "reversal": {"beat_ids": [], "intent": "No reversal"},
        "payoff_climax": {
            "beat_ids": ["B002"] if with_payoff else [],
            "intent": "Mystery resolved" if with_payoff else "No payoff beat",
        },
        "resolution_button": {"beat_ids": [], "intent": "End on discovery"},
        "omitted_beat_ids": [],
    }


def draft(*, text="Alice opens the door.", with_payoff=False, visuals=None):
    beat_ids = ["B001", "B002"] if with_payoff else ["B001"]
    return {
        "segments": [
            {
                "segment_id": "VO_001",
                "text": text,
                "beat_ids": beat_ids,
                "presentation_hint": "narration_over_source",
                "candidate_visuals": [] if visuals is None else visuals,
                "original_dialogue_candidates": [],
            }
        ]
    }


PASSING_CRITIQUE = {
    "passes": True,
    "segment_grounding": [
        {
            "segment_id": "VO_001",
            "supported": True,
            "unsupported_claims": [],
        }
    ],
    "issues": [],
    "revision_instructions": [],
}


class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, ModelGeneration):
            return response
        return ModelGeneration(
            raw_text=json.dumps(response),
            parsed=response,
        )


def small_config(**overrides):
    values = {
        "minimum_word_count": 1,
        "maximum_word_count": 100,
        "max_repair_attempts": 1,
        "max_revision_attempts": 1,
    }
    values.update(overrides)
    return RecapWritingConfig(**values)


def test_template_writer_is_grounded_and_valid():
    script = TemplateRecapWriter().write(story_map())
    validate_recap_script(script, story_map())
    assert script["segments"][0]["beat_ids"] == ["B001"]
    assert script["segments"][0]["candidate_visuals"][0]["start"] == 1.0


def test_staged_writer_fills_candidates_from_verified_evidence():
    model = SequenceModel([outline(), draft(), PASSING_CRITIQUE])
    writer = RecapWriter(model, config=small_config())

    script = writer.write(story_map())

    assert script["actual_word_count"] == 4
    assert script["segments"][0]["candidate_visuals"][0]["end"] == 3.0
    assert script["segments"][0]["original_dialogue_candidates"][0]["start"] == 1.4
    assert [attempt["stage"] for attempt in writer.last_diagnostics["attempts"]] == [
        "narrative_outline",
        "narration_draft",
        "quality_critique",
    ]


def test_malformed_and_wrong_shape_outputs_are_repaired_and_logged(tmp_path):
    malformed = ModelGeneration(
        raw_text='{"hook":',
        parsed=None,
        parse_error="unexpected end of input",
    )
    model = SequenceModel(
        [
            malformed,
            outline(),
            {"beats": []},
            draft(),
            PASSING_CRITIQUE,
        ]
    )
    writer = RecapWriter(
        model,
        config=small_config(),
        debug_dir=tmp_path / "debug",
    )

    script = writer.write(story_map())

    assert script["segments"]
    assert writer.last_diagnostics["repair_attempt_count"] == 2
    assert "VALIDATION ERRORS" in model.prompts[1]
    diagnostics = json.loads(
        (tmp_path / "debug" / "recap_writer_diagnostics.json").read_text()
    )
    assert diagnostics["attempts"][0]["raw_response"] == '{"hook":'


def test_invalid_output_fails_after_bounded_repairs_and_preserves_raw(tmp_path):
    invalid = draft()
    invalid["segments"][0]["beat_ids"] = ["B999"]
    model = SequenceModel([outline(), invalid, invalid])
    writer = RecapWriter(
        model,
        config=small_config(max_revision_attempts=0),
        debug_dir=tmp_path / "debug",
    )

    with pytest.raises(RecapWritingError, match="narration_draft remained invalid"):
        writer.write(story_map())

    diagnostics = json.loads(
        (tmp_path / "debug" / "recap_writer_diagnostics.json").read_text()
    )
    assert diagnostics["status"] == "failed"
    assert "B999" in diagnostics["attempts"][-1]["raw_response"]


def test_candidate_visual_must_come_from_referenced_verified_beat():
    unsupported = draft(
        visuals=[
            {
                "start": 90,
                "end": 92,
                "score": 0.9,
                "reason": "Not source evidence",
            }
        ]
    )
    model = SequenceModel([outline(), unsupported, draft(), PASSING_CRITIQUE])
    writer = RecapWriter(model, config=small_config())

    script = writer.write(story_map())

    assert writer.last_diagnostics["repair_attempt_count"] == 1
    assert script["segments"][0]["candidate_visuals"][0]["start"] == 1.0


def test_payoff_selected_by_outline_must_be_represented():
    missing_payoff = draft()
    model = SequenceModel(
        [outline(with_payoff=True), missing_payoff, missing_payoff]
    )
    writer = RecapWriter(
        model,
        config=small_config(max_revision_attempts=0),
    )

    with pytest.raises(RecapWritingError, match="essential causal-chain beats"):
        writer.write(story_map(with_payoff=True))


def test_subjective_quality_failure_uses_separate_revision_stage():
    failing_critique = {
        "passes": False,
        "segment_grounding": [
            {
                "segment_id": "VO_001",
                "supported": True,
                "unsupported_claims": [],
            }
        ],
        "issues": [
            {
                "category": "hook",
                "severity": "major",
                "message": "The opening delays the conflict.",
                "segment_ids": ["VO_001"],
            }
        ],
        "revision_instructions": ["Open on the conflict."],
    }
    revised = draft(text="The locked door forces Alice to act immediately.")
    model = SequenceModel(
        [outline(), draft(), failing_critique, revised, PASSING_CRITIQUE]
    )
    writer = RecapWriter(model, config=small_config())

    script = writer.write(story_map())

    assert script["segments"][0]["text"].startswith("The locked door")
    assert writer.last_diagnostics["revision_attempt_count"] == 1
    assert "QUALITY CRITIQUE" in model.prompts[3]


def test_quality_gate_cannot_pass_an_unsupported_segment():
    contradictory = {
        "passes": True,
        "segment_grounding": [
            {
                "segment_id": "VO_001",
                "supported": False,
                "unsupported_claims": ["The narration adds an unsupported event."],
            }
        ],
        "issues": [],
        "revision_instructions": [],
    }
    model = SequenceModel([outline(), draft(), contradictory, PASSING_CRITIQUE])
    writer = RecapWriter(model, config=small_config())

    script = writer.write(story_map())

    assert script["segments"]
    assert writer.last_diagnostics["repair_attempt_count"] == 1


def test_tolerant_json_parser_keeps_raw_text_for_repair():
    parsed = parse_json_object('```json\n{"segments": []}\n```')
    malformed = parse_json_object('{"segments":')

    assert parsed.parsed == {"segments": []}
    assert malformed.parsed is None
    assert malformed.raw_text == '{"segments":'
    assert malformed.parse_error


def test_ollama_adapter_requests_configured_context(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": '{"ok": true}'}

    def fake_post(url, **kwargs):
        seen.update(kwargs)
        return Response()

    monkeypatch.setattr("recap_intelligence.llm.requests.post", fake_post)
    generation = OllamaJsonModel(context_length=12288).generate("test")

    assert generation.parsed == {"ok": True}
    assert seen["json"]["options"]["num_ctx"] == 12288
    assert seen["json"]["think"] is False

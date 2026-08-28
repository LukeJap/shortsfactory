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
    build_narration_plan,
    normalize_script,
    validate_script_quality_invariants,
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
    model = SequenceModel([draft(), PASSING_CRITIQUE])
    writer = RecapWriter(model, config=small_config())

    script = writer.write(story_map())

    assert script["actual_word_count"] == 4
    assert script["segments"][0]["candidate_visuals"][0]["end"] == 3.0
    assert script["segments"][0]["original_dialogue_candidates"][0]["start"] == 1.4
    assert [attempt["stage"] for attempt in writer.last_diagnostics["attempts"]] == [
        "narration_section_full_story",
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
            draft(),
            {"beats": []},
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
    model = SequenceModel([invalid, invalid])
    writer = RecapWriter(
        model,
        config=small_config(max_revision_attempts=0),
        debug_dir=tmp_path / "debug",
    )

    with pytest.raises(
        RecapWritingError,
        match="narration_section_full_story remained invalid",
    ):
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
    model = SequenceModel([unsupported, PASSING_CRITIQUE])
    writer = RecapWriter(model, config=small_config())

    script = writer.write(story_map())

    assert writer.last_diagnostics["repair_attempt_count"] == 0
    assert script["segments"][0]["candidate_visuals"][0]["start"] == 1.0


def test_payoff_selected_by_outline_must_be_represented():
    missing_payoff = draft()
    model = SequenceModel([missing_payoff, missing_payoff])
    writer = RecapWriter(
        model,
        config=small_config(max_revision_attempts=0),
    )

    with pytest.raises(RecapWritingError, match="omits planned beats"):
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
        [draft(), failing_critique, revised, PASSING_CRITIQUE]
    )
    writer = RecapWriter(model, config=small_config())

    script = writer.write(story_map())

    assert script["segments"][0]["text"].startswith("The locked door")
    assert writer.last_diagnostics["revision_attempt_count"] == 1
    assert "QUALITY CRITIQUE" in model.prompts[2]


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
    model = SequenceModel([draft(), contradictory, PASSING_CRITIQUE])
    writer = RecapWriter(model, config=small_config())

    script = writer.write(story_map())

    assert script["segments"]
    assert writer.last_diagnostics["repair_attempt_count"] == 1


def richer_story_map():
    purposes = [
        "setup",
        "inciting_incident",
        "escalation",
        "conflict_escalation",
        "reversal_reveal",
        "climax_payoff",
        "resolution_button",
    ]
    importances = [0.35, 0.68, 0.58, 0.88, 0.92, 0.98, 0.55]
    beats = []
    for index, (purpose, importance) in enumerate(
        zip(purposes, importances), start=1
    ):
        beats.append(
            {
                "beat_id": f"B{index:03d}",
                "chronological_order": index,
                "summary": f"Verified story event number {index} changes the situation.",
                "story_purpose": purpose,
                "verification_status": "verified",
                "importance": importance,
                "motivation": "The lead tries to repair the relationship."
                if index in {2, 4}
                else "",
                "change": "The emotional conflict becomes explicit."
                if index == 4
                else "",
                "emotional_conflict": "A painful rejection drives the response."
                if index == 4
                else "",
                "payoff_significance": "The misunderstanding is finally resolved."
                if index == 6
                else "",
                "causal_parents": [f"B{index - 1:03d}"] if index > 1 else [],
                "causal_children": [f"B{index + 1:03d}"] if index < 7 else [],
                "actual_video_evidence_ranges": [
                    {
                        "start": float(index * 10),
                        "end": float(index * 10 + 4),
                        "confidence": 0.9,
                        "evidence_type": "transcript",
                        "transcript_excerpt": f"Verified concise line {index}.",
                    }
                ],
                "original_dialogue_candidates": [],
            }
        )
    return {"beats": beats}


def rich_story_map():
    source = richer_story_map()
    source["research_depth"] = {
        "level": "RICH",
        "route": "fandom_first_verified_story",
    }
    return source


def rich_draft(source, *, text_by_plan_id=None):
    plan = build_narration_plan(source, small_config())
    text_by_plan_id = text_by_plan_id or {}
    return {
        "narration": [
            {
                "plan_id": item["plan_id"],
                "text": text_by_plan_id.get(
                    item["plan_id"],
                    "An original grounded narration thought advances the "
                    f"verified story movement {index}.",
                ),
            }
            for index, item in enumerate(plan["planned_segments"], start=1)
        ]
    }


def single_story_beat_response():
    return {
        "beat_id": "B001",
        "summary": "This is a story beat object, not recap narration.",
    }


def beats_response():
    return {
        "beats": [
            {
                "beat_id": "B001",
                "summary": "This is a story beat collection, not narration.",
            }
        ]
    }


def test_rich_writer_uses_one_generation_without_critic_or_revision():
    source = rich_story_map()
    model = SequenceModel([rich_draft(source)])
    writer = RecapWriter(model, config=small_config())

    script = writer.write(source)

    assert script["segments"]
    assert len(model.prompts) == 1
    assert [item["stage"] for item in writer.last_diagnostics["attempts"]] == [
        "rich_main_narration"
    ]
    assert writer.last_diagnostics["control_flow"] == "rich_fast_path"
    assert writer.last_diagnostics["critic_bypassed"] is True
    assert writer.last_diagnostics["targeted_repair_used"] is False
    assert writer.last_diagnostics["revision_attempt_count"] == 0
    assert '"planned_thoughts"' in model.prompts[0]
    assert '"narration"' in model.prompts[0]
    assert '"verified_beats"' not in model.prompts[0]
    assert '"B001"' not in model.prompts[0]


def test_rich_writer_allows_only_one_targeted_repair_call():
    source = rich_story_map()
    model = SequenceModel([single_story_beat_response(), rich_draft(source)])
    writer = RecapWriter(
        model,
        config=small_config(max_repair_attempts=5, max_revision_attempts=5),
    )

    script = writer.write(source)

    assert script["segments"]
    assert len(model.prompts) == 2
    assert [item["kind"] for item in writer.last_diagnostics["attempts"]] == [
        "initial",
        "repair",
    ]
    assert all(
        item["stage"] == "rich_main_narration"
        for item in writer.last_diagnostics["attempts"]
    )
    assert writer.last_diagnostics["repair_attempt_count"] == 1
    assert writer.last_diagnostics["targeted_repair_used"] is True
    assert "AUTHORITATIVE RICH PLAN" in model.prompts[1]
    assert '"narration"' in model.prompts[1]
    assert not any(
        item["stage"].startswith("quality_critique")
        or item["stage"].startswith("narration_revision_")
        for item in writer.last_diagnostics["attempts"]
    )


def test_rich_writer_repairs_a_beats_envelope_to_plan_text_only():
    source = rich_story_map()
    model = SequenceModel([beats_response(), rich_draft(source)])
    writer = RecapWriter(model, config=small_config())

    script = writer.write(source)

    assert script["segments"]
    assert len(model.prompts) == 2
    assert writer.last_diagnostics["attempts"][0]["validation_errors"] == [
        "RICH narration response must contain only the narration array"
    ]
    assert "one item per plan_id" in model.prompts[1]


def test_rich_writer_schema_repair_never_exceeds_two_calls():
    source = rich_story_map()
    model = SequenceModel([single_story_beat_response(), beats_response()])
    writer = RecapWriter(
        model,
        config=small_config(max_repair_attempts=5),
    )

    with pytest.raises(
        RecapWritingError,
        match="rich_main_narration remained invalid after 2 attempts",
    ):
        writer.write(source)

    assert len(model.prompts) == 2
    assert len(writer.last_diagnostics["attempts"]) == 2


def test_rich_writer_requires_exactly_one_item_for_every_planned_thought():
    source = rich_story_map()
    response = rich_draft(source)
    incomplete = {"narration": response["narration"][:-1]}
    model = SequenceModel([incomplete, response])
    writer = RecapWriter(model, config=small_config())

    script = writer.write(source)

    assert len(script["segments"]) == len(response["narration"])
    assert len(model.prompts) == 2
    assert "exactly one item for each plan_id" in writer.last_diagnostics["attempts"][0][
        "validation_errors"
    ][0]


def test_rich_writer_rejects_extra_or_reordered_plan_items_with_one_repair():
    source = rich_story_map()
    response = rich_draft(source)
    invalid = {"narration": list(reversed(response["narration"]))}
    invalid["narration"].append({"plan_id": "P999", "text": "Extra text."})
    model = SequenceModel([invalid, response])
    writer = RecapWriter(model, config=small_config())

    script = writer.write(source)

    assert script["segments"]
    assert len(model.prompts) == 2
    assert writer.last_diagnostics["repair_attempt_count"] == 1


def test_rich_writer_assembles_deterministic_segment_metadata_from_plan():
    source = rich_story_map()
    response = rich_draft(source)
    response["narration"][0]["beat_ids"] = ["B001"]
    model = SequenceModel([response, rich_draft(source)])

    script = RecapWriter(model, config=small_config()).write(source)
    plan = build_narration_plan(source, small_config())

    assert [segment["segment_id"] for segment in script["segments"]] == [
        f"VO_{index:03d}" for index in range(1, len(plan["planned_segments"]) + 1)
    ]
    assert [segment["beat_ids"] for segment in script["segments"]] == [
        item["beat_ids"] for item in plan["planned_segments"]
    ]
    assert all(segment["candidate_visuals"] for segment in script["segments"])
    assert len(model.prompts) == 2


def test_rich_writer_plan_repair_never_exceeds_two_calls():
    source = rich_story_map()
    invalid = {"narration": [{"plan_id": "P01", "text": "Only one thought."}]}
    model = SequenceModel([invalid, invalid])

    with pytest.raises(
        RecapWritingError,
        match="rich_main_narration remained invalid after 2 attempts",
    ):
        RecapWriter(model, config=small_config(max_repair_attempts=5)).write(source)

    assert len(model.prompts) == 2


def _full_rich_text():
    return (
        "The verified conflict changes what each character can do next, pushes "
        "the situation toward a consequence, and keeps the story moving toward "
        "its grounded resolution while revealing why the final choice carries "
        "real emotional weight for everyone involved."
    )


def _compact_payoff_text():
    return (
        "The hidden answer reframes the conflict, reveals why the choice mattered, "
        "and lets the relationship reach its earned outcome at last."
    )


def _rich_response_with_protected_text(source, protected_text):
    plan = build_narration_plan(source, small_config())
    protected = next(
        item for item in plan["planned_segments"] if item["function"] == "reversal_payoff"
    )
    return rich_draft(
        source,
        text_by_plan_id={
            item["plan_id"]: (
                protected_text if item["plan_id"] == protected["plan_id"] else _full_rich_text()
            )
            for item in plan["planned_segments"]
        },
    )


def test_rich_accepts_complete_protected_thought_modestly_below_local_floor():
    source = rich_story_map()
    config = RecapWritingConfig(minimum_word_count=180, maximum_word_count=360)
    response = _rich_response_with_protected_text(source, _compact_payoff_text())
    writer = RecapWriter(SequenceModel([response]), config=config)

    script = writer.write(source)
    plan = build_narration_plan(source, config)
    protected = next(
        item for item in plan["planned_segments"] if item["function"] == "reversal_payoff"
    )
    protected_segment = script["segments"][
        next(
            index
            for index, item in enumerate(plan["planned_segments"])
            if item["plan_id"] == protected["plan_id"]
        )
    ]

    assert protected_segment["word_count"] < protected["word_range"][0]
    assert protected_segment["word_count"] >= protected["word_range"][0] // 4
    assert script["actual_word_count"] >= 112


def test_rich_rejects_severely_underwritten_protected_thought():
    source = rich_story_map()
    config = RecapWritingConfig(minimum_word_count=180, maximum_word_count=360)
    response = _rich_response_with_protected_text(source, "Resolved now.")
    writer = RecapWriter(SequenceModel([response, response]), config=config)

    with pytest.raises(RecapWritingError, match="payoff/climax is underdeveloped"):
        writer.write(source)


def test_rich_missing_reveal_beat_still_fails_causal_coverage():
    source = rich_story_map()
    config = RecapWritingConfig(minimum_word_count=180, maximum_word_count=360)
    response = _rich_response_with_protected_text(source, _compact_payoff_text())
    accepted = RecapWriter(SequenceModel([response]), config=config).write(source)
    plan = build_narration_plan(source, config)
    reveal_id = plan["reversal"]["beat_ids"][0]
    raw_segments = [
        {
            "segment_id": segment["segment_id"],
            "text": segment["text"],
            "beat_ids": [
                beat_id for beat_id in segment["beat_ids"] if beat_id != reveal_id
            ],
            "presentation_hint": segment["presentation_hint"],
        }
        for segment in accepted["segments"]
    ]
    invalid = normalize_script({"segments": raw_segments}, source, config)

    with pytest.raises(RecapWritingError, match="essential causal-chain beats"):
        validate_script_quality_invariants(
            invalid,
            source,
            plan,
            config,
            allow_compact_protected_thoughts=True,
        )


def test_rich_total_minimum_remains_required_for_compact_thoughts():
    source = rich_story_map()
    config = RecapWritingConfig(minimum_word_count=180, maximum_word_count=360)
    response = rich_draft(
        source,
        text_by_plan_id={
            item["plan_id"]: "Brief grounded thought."
            for item in build_narration_plan(source, small_config())["planned_segments"]
        },
    )
    writer = RecapWriter(SequenceModel([response, response]), config=config)

    with pytest.raises(RecapWritingError, match="minimum sensible budget"):
        writer.write(source)


def test_narration_plan_chooses_conflict_hook_instead_of_chronological_setup():
    plan = build_narration_plan(richer_story_map(), RecapWritingConfig())

    assert plan["hook"]["beat_ids"] == ["B004"]
    assert plan["minimum_setup"]["beat_ids"] == ["B002"]
    assert plan["planned_segments"][:2] == [
        {
            "plan_id": "P01",
            "function": "hook",
            "beat_ids": ["B004"],
            "target_words": 39,
            "word_range": [31, 47],
        },
        {
            "plan_id": "P02",
            "function": "setup",
            "beat_ids": ["B002"],
            "target_words": 38,
            "word_range": [30, 46],
        },
    ]
    assert plan["payoff_climax"]["beat_ids"] == ["B006"]
    assert plan["planned_segments"][-2]["function"] == "reversal_payoff"
    assert plan["planned_segments"][-2]["target_words"] > plan["planned_segments"][0]["target_words"]


def causal_attempt_story_map():
    purposes = [
        "setup",
        "inciting_incident",
        "conflict_escalation",
        "attempt_failure",
        "attempt_failure",
        "escalation",
        "attempt_failure",
        "escalation",
        "escalation",
        "escalation",
        "attempt_failure",
        "escalation",
        "reversal_reveal",
        "climax_payoff",
        "resolution",
    ]
    summaries = [
        "The relationship starts in a stable place.",
        "A new arrangement creates pressure.",
        "The lead must choose between two relationships.",
        "Hurt by that choice, the lead tries to cope.",
        "The lead tries a first replacement.",
        "That replacement fails in a new way.",
        "The lead tries another replacement.",
        "That attempt becomes even worse.",
        "A final replacement still cannot solve the loss.",
        "The other person returns while the conflict is unresolved.",
        "The lead makes one last desperate response.",
        "That response puts the hidden explanation in motion.",
        "A hidden fact explains the apparent rejection.",
        "The relationship is restored after the reveal.",
        "The other person reacts to the restored relationship.",
    ]
    beats = []
    for index, (purpose, summary) in enumerate(zip(purposes, summaries), start=1):
        beats.append(
            {
                "beat_id": f"B{index:03d}",
                "chronological_order": index,
                "summary": summary,
                "story_purpose": purpose,
                "verification_status": "verified",
                "importance": 0.65,
                "emotional_conflict": "hurt" if index == 4 else "",
                "causal_parents": [f"B{index - 1:03d}"] if index > 1 else [],
                "causal_children": [f"B{index + 1:03d}"] if index < len(purposes) else [],
                "actual_video_evidence_ranges": [
                    {
                        "start": float(index * 10),
                        "end": float(index * 10 + 4),
                        "confidence": 0.9,
                        "evidence_type": "transcript",
                    }
                ],
                "original_dialogue_candidates": [],
            }
        )
    return {"beats": beats}


def test_plan_retains_central_choice_before_downstream_coping():
    plan = build_narration_plan(causal_attempt_story_map(), RecapWritingConfig())

    assert plan["hook"]["beat_ids"] == ["B003"]
    selected = [
        beat_id
        for segment in plan["planned_segments"]
        for beat_id in segment["beat_ids"]
    ]
    assert selected.index("B003") < selected.index("B004")


def test_downstream_attempt_consequence_cannot_become_hook_without_conflict():
    plan = build_narration_plan(causal_attempt_story_map(), RecapWritingConfig())

    assert plan["hook"]["beat_ids"] == ["B003"]
    assert "B004" not in plan["hook"]["beat_ids"]


def test_plan_compresses_repeated_attempts_without_dropping_their_beats():
    plan = build_narration_plan(causal_attempt_story_map(), RecapWritingConfig())

    compressed = next(
        segment
        for segment in plan["planned_segments"]
        if segment["beat_ids"] == [
            "B004",
            "B005",
            "B006",
            "B007",
            "B008",
            "B009",
            "B010",
        ]
    )
    assert compressed["function"] == "escalation"
    assert compressed["target_words"] >= 64


def test_plan_keeps_reveal_payoff_and_resolution_protected():
    plan = build_narration_plan(causal_attempt_story_map(), RecapWritingConfig())

    assert plan["reversal"]["beat_ids"] == ["B013"]
    assert plan["payoff_climax"]["beat_ids"] == ["B014"]
    assert plan["resolution_button"]["beat_ids"] == ["B015"]


def test_plan_role_budgets_are_normalized_to_preferred_total():
    plan = build_narration_plan(causal_attempt_story_map(), RecapWritingConfig())

    assert 280 <= plan["planned_word_count"] <= 320
    assert sum(item["target_words"] for item in plan["planned_segments"]) == plan[
        "planned_word_count"
    ]


def test_plan_budget_normalization_preserves_relative_story_weighting():
    plan = build_narration_plan(causal_attempt_story_map(), RecapWritingConfig())
    budgets = {
        item["plan_id"]: item["target_words"]
        for item in plan["planned_segments"]
    }

    assert budgets["P06"] >= budgets["P03"] > budgets["P04"]
    assert budgets["P03"] > budgets["P01"] > budgets["P02"]


def test_plan_budget_normalization_keeps_opening_and_button_compact():
    plan = build_narration_plan(causal_attempt_story_map(), RecapWritingConfig())
    budgets = {
        item["function"]: item["target_words"]
        for item in plan["planned_segments"]
        if item["function"] in {"hook", "setup", "resolution"}
    }

    assert budgets["hook"] <= 40
    assert budgets["setup"] <= 40
    assert budgets["resolution"] <= 25


def test_plan_budget_normalization_keeps_payoff_protected():
    plan = build_narration_plan(causal_attempt_story_map(), RecapWritingConfig())
    payoff = next(
        item for item in plan["planned_segments"] if item["function"] == "reversal_payoff"
    )

    assert payoff["target_words"] >= 52
    assert payoff["target_words"] > next(
        item["target_words"]
        for item in plan["planned_segments"]
        if item["function"] == "hook"
    )


def test_sparse_plan_is_not_padded_to_the_preferred_total():
    plan = build_narration_plan(story_map(), RecapWritingConfig())

    assert plan["planned_word_count"] == 39
    assert plan["planned_segments"][0]["target_words"] == 39


def test_plan_budget_never_exceeds_configured_hard_ceiling():
    plan = build_narration_plan(
        causal_attempt_story_map(),
        RecapWritingConfig(maximum_word_count=280),
    )

    assert plan["planned_word_count"] == 280


def test_underbudget_draft_gets_one_targeted_grounded_expansion():
    expanded_text = (
        "Opening the door creates a question Alice cannot ignore, and the hidden "
        "package gives her a concrete reason to keep investigating. Each clue "
        "narrows the possibilities until the discovery behind the doorway finally "
        "connects the action to the answer and resolves the mystery cleanly."
    )
    model = SequenceModel(
        [
            draft(with_payoff=True),
            draft(text=expanded_text, with_payoff=True),
            PASSING_CRITIQUE,
        ]
    )
    writer = RecapWriter(
        model,
        config=small_config(minimum_word_count=99, maximum_word_count=360),
    )

    script = writer.write(story_map(with_payoff=True))

    assert script["actual_word_count"] >= 32
    assert writer.last_diagnostics["targeted_expansion_used"] is True
    assert [item["stage"] for item in writer.last_diagnostics["attempts"]] == [
        "narration_section_full_story",
        "targeted_budget_expansion",
        "quality_critique",
    ]
    assert "deficit_words" in model.prompts[1]


def test_thin_payoff_triggers_targeted_expansion_even_above_global_floor():
    setup_text = (
        "Alice studies the doorway from every angle, checks the frame, follows "
        "the marks on the floor, and keeps testing each possibility because the "
        "unexplained entrance gives her a concrete mystery to solve."
    )
    initial = {
        "segments": [
            {"segment_id": "VO_001", "text": setup_text, "beat_ids": ["B001"]},
            {"segment_id": "VO_002", "text": "The package answers it.", "beat_ids": ["B002"]},
        ]
    }
    expanded_text = (
        "That discovery connects the hidden package to her question and gives the "
        "search a clear consequence. Instead of ending on another clue, Alice can "
        "finally understand why opening the doorway mattered."
    )
    payoff_patch = {
        "segments": [
            {"segment_id": "ADD_001", "text": expanded_text, "beat_ids": ["B002"]}
        ]
    }
    source = story_map(with_payoff=True)
    config = small_config(minimum_word_count=180, maximum_word_count=360)
    writer = RecapWriter(SequenceModel([]), config=config)
    script = normalize_script(initial, source, config)
    plan = build_narration_plan(source, config)
    deficits = writer._budget_deficits(script, source, plan)
    patch = normalize_script(payoff_patch, source, config)

    expanded = writer._append_expansion(script, patch, source)

    assert any(item["function"] == "payoff_climax" for item in deficits)
    assert expanded["actual_word_count"] > script["actual_word_count"]
    assert len(expanded["segments"]) == len(script["segments"]) + 1


def test_generic_intro_is_repaired_before_quality_critique():
    generic = draft(text="In this episode, Alice opens the door.")
    grounded = draft(text="A stubborn locked door gives Alice a reason to act.")
    model = SequenceModel([generic, grounded, PASSING_CRITIQUE])
    writer = RecapWriter(model, config=small_config())

    script = writer.write(story_map())

    assert script["segments"][0]["text"].startswith("A stubborn")
    assert writer.last_diagnostics["repair_attempt_count"] == 1


def test_verbatim_story_summary_is_rejected():
    source = story_map()
    source["beats"][0]["summary"] = "Alice slowly opens the locked door and discovers a clue."
    copied = draft(text=source["beats"][0]["summary"])
    model = SequenceModel([copied, copied])
    writer = RecapWriter(model, config=small_config(max_revision_attempts=0))

    with pytest.raises(RecapWritingError, match="copying story-map summaries"):
        writer.write(source)


def test_multi_beat_segment_gets_short_diverse_noncontiguous_visuals():
    source = story_map(with_payoff=True)
    source["beats"][0]["actual_video_evidence_ranges"].insert(
        0,
        {"start": 0.0, "end": 20.0, "confidence": 0.95},
    )
    script = normalize_script(
        draft(text="One action leads directly to the resolving discovery.", with_payoff=True),
        source,
        small_config(),
    )

    visuals = script["segments"][0]["candidate_visuals"]
    assert [(item["start"], item["end"]) for item in visuals[:2]] == [
        (1.0, 3.0),
        (4.0, 7.0),
    ]
    assert len(visuals) == 3


def test_dialogue_is_derived_only_from_verified_high_value_evidence():
    source = story_map(with_payoff=True)
    source["beats"][1]["actual_video_evidence_ranges"][0][
        "transcript_excerpt"
    ] = "The answer was inside the package."
    script = normalize_script(
        draft(text="The package resolves the question.", with_payoff=True),
        source,
        small_config(),
    )

    dialogue = script["segments"][0]["original_dialogue_candidates"]
    assert dialogue[0]["start"] == 1.4
    assert dialogue[1]["start"] == 4.0
    assert dialogue[1]["end"] == 7.0


def test_importance_is_derived_from_beats_and_not_uniform_model_values():
    source = richer_story_map()
    raw = {
        "segments": [
            {
                "segment_id": f"VO_{index:03d}",
                "text": f"An original grounded narration thought for event {index}.",
                "beat_ids": [f"B{index:03d}"],
                "importance": 0.9,
            }
            for index in range(1, 5)
        ]
    }
    script = normalize_script(raw, source, small_config())

    assert len({segment["importance"] for segment in script["segments"]}) > 1


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

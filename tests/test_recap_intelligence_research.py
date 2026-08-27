from recap_intelligence.models import IdentityCandidate, validate_research_dossier
from recap_intelligence.providers import FandomProvider, ProviderError, ResearchPacket
import pytest

from recap_intelligence.research import (
    ResearchGroundingError,
    ResearchService,
    classify_research_depth,
    validate_research_grounding,
)


class FakeResearchProvider:
    def __init__(self, packet):
        self.name = packet.provider
        self.packet = packet

    def research(self, identity):
        return self.packet


def fandom_identity():
    return {
        "canonical_id": "tvmaze:container:713:survival-dumped",
        "series_title": "SpongeBob SquarePants",
        "container_title": "Survival of the Idiots & Dumped",
        "segments": [
            {
                "title": "Dumped",
                "provider_ids": {"tvmaze": "255050"},
                "provider_numbering": {"tvmaze": {"season": 2, "episode": 14}},
            }
        ],
    }


def fandom_fetch(
    *,
    site_name="Encyclopedia SpongeBobia",
    server="https://spongebob.fandom.test",
    transcript=True,
):
    episode = """{{Episode
|title = Dumped
|sisterep = Survival of the Idiots
|briefsummary = [[SpongeBob]] is jealous that [[Gary]] wants to stay with [[Patrick]].
}}
===Characters===
*[[SpongeBob SquarePants]]
*[[Gary the Snail]]
*[[Patrick Star]]
==Synopsis==
SpongeBob and Gary play tag before Gary begins following Patrick. SpongeBob tries replacement pets after Gary chooses Patrick. SpongeBob realizes Gary only wanted a cookie in Patrick's pocket. Gary returns home, leaving Patrick disappointed.
"""
    transcript_text = """{{EpisodeTranscript}}
{{L|SpongeBob|Gary, come home! ''[He offers Gary complete freedom.]''}}
{{L|Patrick|Gary only wanted the cookie in my pocket.}}
{{L|''[Gary eats the cookie and returns to SpongeBob.]''}}
"""

    def fetch(url, *, params=None, **kwargs):
        params = params or {}
        if params.get("meta") == "siteinfo":
            return {
                "query": {
                    "general": {
                        "sitename": site_name,
                        "server": server,
                    }
                }
            }
        if params.get("action") == "parse":
            page = params.get("page")
            if str(page).endswith("/transcript"):
                return {
                    "parse": {
                        "title": "Dumped/transcript",
                        "wikitext": transcript_text,
                    }
                } if transcript else {"error": {"code": "missingtitle"}}
            return {"parse": {"title": "Dumped", "wikitext": episode}}
        raise AssertionError(params)

    return fetch


def test_research_dossier_preserves_provenance_and_conflicts():
    identity = IdentityCandidate(
        canonical_id="tvmaze:episode:1",
        content_type="tv",
        title="Example Show",
        episode_title="The Door",
        season=2,
        episode=5,
        provider="tvmaze",
        provider_id="1",
        url="https://tvmaze.test/episode/1",
        confidence=0.9,
    )
    first = ResearchPacket(
        provider="tvmaze",
        title="Example Show - The Door",
        url="https://tvmaze.test/episode/1",
        source_type="episode_database",
        reliability=0.8,
        short_synopsis="Alice opens the door and finds a package.",
        detailed_synopsis="Alice opens the door and finds a package.",
        characters=["Alice"],
        plot_points=[
            {
                "plot_id": "P1",
                "order": 1,
                "summary": "Alice opens the door.",
                "story_purpose": "inciting_incident",
                "characters": ["Alice"],
                "causal_parents": [],
            }
        ],
        claims=["Alice opens the door."],
    )
    second = ResearchPacket(
        provider="wikipedia",
        title="Example Show",
        url="https://wikipedia.test/example",
        source_type="encyclopedia",
        reliability=0.6,
        short_synopsis="Bob closes the door and hides the package.",
        detailed_synopsis="Bob closes the door and hides the package.",
        characters=["Bob"],
        locations=["Hallway"],
        plot_points=[
            {
                "plot_id": "P2",
                "order": 2,
                "summary": "Bob hides the package.",
                "story_purpose": "resolution",
                "characters": ["Bob"],
                "locations": ["Hallway"],
                "causal_parents": ["P1"],
            }
        ],
        claims=["Bob hides the package."],
    )

    result = ResearchService(
        [FakeResearchProvider(first), FakeResearchProvider(second)]
    ).collect(identity)
    dossier = result.dossier

    validate_research_dossier(dossier)
    assert [source["provider"] for source in dossier["sources"]] == [
        "tvmaze",
        "wikipedia",
    ]
    assert dossier["source_disagreements"][0]["resolution"] == (
        "local_source_video_is_final_authority"
    )
    assert {point["plot_id"] for point in dossier["ordered_plot_points"]} == {
        "P001",
        "P002",
    }
    assert dossier["characters"] == ["Alice", "Bob"]


def test_rejected_source_contributes_zero_claims():
    identity = IdentityCandidate(
        canonical_id="fixture:episode:1",
        content_type="tv",
        title="Example Show",
        episode_title="The Door",
        provider="fixture",
        provider_id="1",
        confidence=0.9,
    )
    accepted = ResearchPacket(
        provider="fixture",
        title="The Door",
        url="https://fixture.test/door",
        source_type="episode",
        reliability=0.8,
        claims=["Alice opens the door."],
        plot_points=[{"plot_id": "P1", "summary": "Alice opens the door."}],
    )
    rejected = ResearchPacket(
        provider="bad-source",
        title="Unrelated Door",
        url="https://fixture.test/unrelated",
        source_type="episode",
        reliability=0.2,
        claims=["An unrelated claim."],
        plot_points=[{"plot_id": "P1", "summary": "An unrelated claim."}],
        assessment_status="rejected_identity_mismatch",
        assessment_reason="wrong series",
    )

    dossier = ResearchService(
        [FakeResearchProvider(accepted), FakeResearchProvider(rejected)]
    ).collect(identity).dossier

    assert [source["provider"] for source in dossier["sources"]] == ["fixture"]
    evaluation = next(
        item for item in dossier["source_evaluations"]
        if item["provider"] == "bad-source"
    )
    assert evaluation["claims"] == []
    assert "An unrelated claim" not in str(dossier["ordered_plot_points"])


def test_contaminated_research_is_blocked_by_quality_gate():
    dossier = {
        "canonical_identity": {"series_title": "SpongeBob SquarePants"},
        "segments": [],
        "source_evaluations": [
            {
                "provider": "wikipedia",
                "status": "accepted",
                "claims": ["A British reality television programme."],
                "identity_context": {"source_scope": "series"},
            }
        ],
    }

    with pytest.raises(ResearchGroundingError, match="generic series"):
        validate_research_grounding(dossier)


def test_fandom_episode_and_transcript_are_identity_locked_and_normalized():
    provider = FandomProvider(
        fetch_json=fandom_fetch(),
        wiki_urls=["https://spongebob.fandom.test"],
    )

    dossier = ResearchService([provider]).collect(fandom_identity()).dossier

    validate_research_dossier(dossier)
    validate_research_grounding(dossier)
    assert {source["source_type"] for source in dossier["sources"]} == {
        "fandom_episode",
        "fandom_episode_transcript",
    }
    assert {"SpongeBob SquarePants", "Gary the Snail", "Patrick Star"} <= set(
        dossier["characters"]
    )
    assert any(
        point["story_purpose"] == "reversal"
        and point["provenance"][0]["provider"] == "fandom"
        for point in dossier["ordered_plot_points"]
    )
    first = dossier["transcript_events"][0]
    assert first["speaker"] == "SpongeBob"
    assert first["dialogue"] == "Gary, come home!"
    assert first["actions"] == ["He offers Gary complete freedom."]
    assert first["timing_authority"] == "none"
    assert first["source_url"].endswith("Dumped/transcript")


def test_fandom_same_title_wrong_franchise_is_rejected_without_claims():
    fandom = FandomProvider(
        fetch_json=fandom_fetch(
            site_name="Unrelated Reality Show Wiki",
            server="https://unrelated.fandom.test",
        ),
        wiki_urls=["https://unrelated.fandom.test"],
    )
    fallback = ResearchPacket(
        provider="tvmaze",
        title="SpongeBob SquarePants - Dumped",
        url="https://tvmaze.test/dumped",
        source_type="episode_database",
        reliability=0.8,
        claims=["Episode title: Dumped"],
        segment_title="Dumped",
        segment_id="SEG_01",
        identity_context={
            "found_series_title": "SpongeBob SquarePants",
            "found_segment_title": "Dumped",
            "source_scope": "episode",
        },
    )

    dossier = ResearchService([fandom, FakeResearchProvider(fallback)]).collect(
        fandom_identity()
    ).dossier

    evaluation = next(
        item for item in dossier["source_evaluations"]
        if item["provider"] == "fandom"
    )
    assert evaluation["status"] == "rejected_identity_mismatch"
    assert evaluation["claims"] == []
    assert not any(source["provider"] == "fandom" for source in dossier["sources"])
    assert "Gary only wanted" not in str(dossier)


def test_fandom_transcript_absence_is_nonfatal():
    provider = FandomProvider(
        fetch_json=fandom_fetch(transcript=False),
        wiki_urls=["https://spongebob.fandom.test"],
    )

    dossier = ResearchService([provider]).collect(fandom_identity()).dossier

    assert [source["source_type"] for source in dossier["sources"]] == [
        "fandom_episode"
    ]
    assert dossier["transcript_events"] == []
    assert dossier["ordered_plot_points"]


def test_fandom_provider_failure_is_nonfatal_when_other_research_exists():
    class BrokenFandom:
        name = "fandom"

        def research(self, identity):
            raise ProviderError("temporary outage")

    fallback = ResearchPacket(
        provider="tvmaze",
        title="SpongeBob SquarePants - Dumped",
        url="https://tvmaze.test/dumped",
        source_type="episode_database",
        reliability=0.8,
        claims=["Episode title: Dumped"],
        segment_title="Dumped",
        segment_id="SEG_01",
    )

    result = ResearchService([BrokenFandom(), FakeResearchProvider(fallback)]).collect(
        fandom_identity()
    )

    assert result.dossier["sources"][0]["provider"] == "tvmaze"
    assert any("temporary outage" in warning for warning in result.warnings)


def test_rich_fandom_research_selects_fast_path():
    dossier = ResearchService(
        [
            FandomProvider(
                fetch_json=fandom_fetch(),
                wiki_urls=["https://spongebob.fandom.test"],
            )
        ]
    ).collect(fandom_identity()).dossier
    base_points = list(dossier["ordered_plot_points"])
    while len(dossier["ordered_plot_points"]) < 8:
        source = dict(base_points[len(dossier["ordered_plot_points"]) % len(base_points)])
        source["plot_id"] = f"EXTRA_{len(dossier['ordered_plot_points']):03d}"
        source["summary"] += f" Distinct event {len(dossier['ordered_plot_points'])}."
        dossier["ordered_plot_points"].append(source)
    base_event = dict(dossier["transcript_events"][0])
    while len(dossier["transcript_events"]) < 8:
        event = dict(base_event)
        event["event_id"] = f"EXTRA_T{len(dossier['transcript_events']):03d}"
        event["dialogue"] = f"Distinct line {len(dossier['transcript_events'])}."
        dossier["transcript_events"].append(event)
    dossier["ordered_plot_points"][-2]["story_purpose"] = "reversal"
    dossier["ordered_plot_points"][-1]["story_purpose"] = "resolution"

    depth = classify_research_depth(dossier)

    assert depth["level"] == "RICH"
    assert depth["route"] == "fandom_first_verified_story"


def test_medium_and_poor_research_keep_fallback_routes():
    medium = {
        "sources": [{"provider": "tvmaze", "source_type": "episode_database"}],
        "ordered_plot_points": [
            {"summary": "A conflict begins."},
            {"summary": "The conflict ends."},
        ],
        "transcript_events": [],
        "characters": ["A", "B"],
    }
    poor = {
        "sources": [],
        "ordered_plot_points": [],
        "transcript_events": [],
    }

    assert classify_research_depth(medium)["route"] == "research_led_hybrid"
    assert classify_research_depth(poor)["route"] == "semantic_heavy_fallback"


def test_wrong_franchise_and_sister_contamination_never_classify_rich():
    wrong_franchise = {
        "canonical_identity": fandom_identity(),
        "sources": [],
        "source_evaluations": [
            {"provider": "fandom", "status": "rejected_identity_mismatch"}
        ],
        "ordered_plot_points": [
            {"summary": f"Distinct episode event {index}.", "story_purpose": "reversal"}
            for index in range(10)
        ],
        "transcript_events": [],
        "characters": ["One", "Two"],
    }
    contaminated = {
        **wrong_franchise,
        "sources": [
            {
                "provider": "fandom",
                "source_type": "fandom_episode",
                "assessment_status": "accepted",
            },
            {
                "provider": "fandom",
                "source_type": "fandom_episode_transcript",
                "assessment_status": "accepted",
            },
        ],
        "segments": [{"segment_id": "SEG_01", "title": "Dumped"}],
        "ordered_plot_points": [
            {
                "summary": f"Distinct selected event {index}.",
                "story_purpose": "resolution" if index == 9 else "attempt_failure",
                "segment_id": "SEG_02" if index == 3 else "SEG_01",
            }
            for index in range(10)
        ],
        "transcript_events": [
            {
                "dialogue": f"Distinct line {index}.",
                "segment_id": "SEG_01",
            }
            for index in range(10)
        ],
        "characters": ["One", "Two"],
    }

    assert classify_research_depth(wrong_franchise)["level"] != "RICH"
    contaminated_depth = classify_research_depth(contaminated)
    assert contaminated_depth["level"] != "RICH"
    assert contaminated_depth["metrics"]["selected_segment_contamination"] is True

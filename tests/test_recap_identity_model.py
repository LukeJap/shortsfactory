from recap_intelligence.identity import (
    EpisodeIdentityResolver,
    IdentityQuery,
    parse_compound_title,
    parse_source_filename,
)
from recap_intelligence.models import IdentityCandidate, IdentitySegment
from recap_intelligence.providers import (
    MediaWikiProvider,
    ResearchPacket,
    TVMazeProvider,
    request_json,
)
from recap_intelligence.research import ResearchService


def _tvmaze_fetch(url, params=None, **kwargs):
    if url.endswith("/search/shows"):
        return [
            {
                "show": {
                    "id": 1,
                    "name": "SpongeBob SquarePants",
                    "url": "https://tvmaze.test/shows/1",
                }
            }
        ]
    if url.endswith("/shows/1/episodes"):
        return [
            {
                "id": 9,
                "name": "Dying for Pie",
                "season": 2,
                "number": 9,
                "summary": "A pie causes trouble.",
                "url": "https://tvmaze.test/episodes/9",
            },
            {
                "id": 17,
                "name": "Survival of the Idiots",
                "season": 2,
                "number": 17,
                "summary": "SpongeBob and Patrick survive a cold night.",
                "url": "https://tvmaze.test/episodes/17",
            },
            {
                "id": 18,
                "name": "Dumped",
                "season": 2,
                "number": 18,
                "summary": "SpongeBob is dumped by Gary.",
                "url": "https://tvmaze.test/episodes/18",
            },
        ]
    raise AssertionError(f"Unexpected TVMaze URL: {url}")


def test_compound_title_parsing_is_conservative():
    assert parse_compound_title("Survival of the Idiots&Dumped") == [
        "Survival of the Idiots",
        "Dumped",
    ]
    assert parse_compound_title("First Story / Second Story") == [
        "First Story",
        "Second Story",
    ]
    assert parse_compound_title("Love/Hate") == ["Love/Hate"]
    assert parse_compound_title("R&D") == ["R&D"]


def test_filename_parsing_separates_source_slot_from_segments():
    parsed = parse_source_filename(
        "S02E09 Survival of the Idiots&Dumped.mkv"
    )

    assert parsed["season"] == 2
    assert parsed["container_episode"] == 9
    assert parsed["container_title"] == "Survival of the Idiots&Dumped"
    assert parsed["segment_titles"] == [
        "Survival of the Idiots",
        "Dumped",
    ]


def test_spongebob_container_does_not_resolve_to_numeric_tvmaze_title():
    query = IdentityQuery(
        content_type="tv",
        title="SpongeBob SquarePants",
        season=2,
        container_episode=9,
        container_title="Survival of the Idiots&Dumped",
        source_filename="S02E09 Survival of the Idiots&Dumped.mkv",
    )
    resolution = EpisodeIdentityResolver(
        [TVMazeProvider(fetch_json=_tvmaze_fetch)]
    ).resolve(query)

    compound = next(
        candidate
        for candidate in resolution.candidates
        if len(candidate.segments) == 2
    )
    assert compound.container_episode == 9
    assert [segment.title for segment in compound.segments] == [
        "Survival of the Idiots",
        "Dumped",
    ]
    assert [
        segment.provider_numbering["tvmaze"]["episode"]
        for segment in compound.segments
    ] == [17, 18]
    assert compound.to_dict()["container_episode"] == 9
    assert "episode" not in compound.to_dict()

    numeric_conflict = next(
        candidate
        for candidate in resolution.candidates
        if candidate.source_match.get("numbering_conflict")
    )
    assert numeric_conflict.episode_title == "Dying for Pie"
    assert numeric_conflict.confidence < 0.5
    assert numeric_conflict.source_match["provider_numeric"]["episode"] == 9


def test_exact_segment_title_remains_viable_when_provider_number_differs():
    query = IdentityQuery(
        content_type="tv",
        title="SpongeBob SquarePants",
        season=2,
        container_episode=9,
        container_title="Survival of the Idiots",
    )
    candidates = TVMazeProvider(fetch_json=_tvmaze_fetch).resolve(query)

    candidate = next(
        item
        for item in candidates
        if item.source_match.get("match_type") == "compound_title"
    )
    assert candidate.confidence > 0.8
    assert candidate.segments[0].provider_numbering["tvmaze"]["episode"] == 17


class _CompoundResearchProvider:
    name = "fixture"

    def research(self, identity):
        return ResearchPacket(
            provider=self.name,
            title="Container",
            url="https://fixture.test/container",
            source_type="container",
            reliability=0.8,
            segment_packets=[
                ResearchPacket(
                    provider=self.name,
                    title="First Story",
                    url="https://fixture.test/first",
                    source_type="episode",
                    reliability=0.8,
                    segment_title="First Story",
                    segment_id="SEG_A",
                    short_synopsis="The first story begins.",
                    plot_points=[
                        {
                            "plot_id": "P1",
                            "order": 1,
                            "summary": "The first story begins.",
                            "causal_parents": [],
                        }
                    ],
                ),
                ResearchPacket(
                    provider=self.name,
                    title="Second Story",
                    url="https://fixture.test/second",
                    source_type="episode",
                    reliability=0.8,
                    segment_title="Second Story",
                    segment_id="SEG_B",
                    short_synopsis="The second story ends.",
                    plot_points=[
                        {
                            "plot_id": "P1",
                            "order": 1,
                            "summary": "The second story ends.",
                            "causal_parents": [],
                        }
                    ],
                ),
            ],
        )


def test_compound_research_keeps_segment_storylines_separate():
    identity = IdentityCandidate(
        canonical_id="fixture:container",
        content_type="tv",
        title="Example Show",
        container_title="First Story / Second Story",
        container_episode=9,
        segments=(),
        provider="fixture",
        provider_id="container",
        confidence=0.9,
    )
    result = ResearchService([_CompoundResearchProvider()]).collect(identity)

    dossier = result.dossier
    assert [segment["segment_id"] for segment in dossier["segments"]] == [
        "SEG_A",
        "SEG_B",
    ]
    assert {
        point["segment_id"]
        for point in dossier["ordered_plot_points"]
    } == {"SEG_A", "SEG_B"}
    assert all(
        point["causal_parents"] == []
        for point in dossier["ordered_plot_points"]
    )
    assert dossier["container"]["container_episode"] == 9


def test_request_json_uses_descriptive_user_agent(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return Response()

    monkeypatch.setattr("recap_intelligence.providers.requests.get", fake_get)
    assert request_json("https://example.test/api") == {"ok": True}
    assert "User-Agent" in seen["headers"]
    assert "ShortsFactory" in seen["headers"]["User-Agent"]


def _wikipedia_fetch(*, related):
    calls = []

    def fetch(url, params=None, **kwargs):
        calls.append(dict(params or {}))
        if params.get("list") == "search":
            return {
                "query": {
                    "search": [
                        {
                            "pageid": 10,
                            "title": (
                                "Dumped (SpongeBob SquarePants)"
                                if related else "Dumped"
                            ),
                        }
                    ]
                }
            }
        extract = (
            "Dumped is an episode of SpongeBob SquarePants in which Gary leaves SpongeBob."
            if related
            else "Dumped is a British reality television programme on Channel 4."
        )
        return {
            "query": {
                "pages": {
                    "10": {
                        "extract": extract,
                        "fullurl": "https://en.wikipedia.test/wiki/Dumped",
                    }
                }
            }
        }

    return fetch, calls


def _selected_dumped_identity():
    return IdentityCandidate(
        canonical_id="tvmaze:container:713:survival-dumped",
        content_type="tv",
        title="SpongeBob SquarePants",
        series_title="SpongeBob SquarePants",
        container_title="Survival of the Idiots & Dumped",
        container_episode=9,
        season=2,
        segments=(
            IdentitySegment(
                title="Dumped",
                provider_ids={"tvmaze": "255050"},
                provider_numbering={
                    "tvmaze": {"season": 2, "episode": 14}
                },
            ),
        ),
        provider="tvmaze",
        provider_id="713",
        provider_ids={"tvmaze": {"series_id": 713}},
        confidence=0.96,
    )


def test_wikipedia_rejects_unrelated_dumped_program_and_locks_query():
    fetch, calls = _wikipedia_fetch(related=False)
    packet = MediaWikiProvider(fetch_json=fetch).research(
        _selected_dumped_identity().to_dict()
    ).expanded_packets()[0]

    assert '"SpongeBob SquarePants"' in calls[0]["srsearch"]
    assert '"Dumped"' in calls[0]["srsearch"]
    assert packet.assessment_status == "rejected_identity_mismatch"
    assert packet.claims == []
    assert packet.plot_points == []


def test_wikipedia_accepts_series_locked_dumped_result():
    fetch, _ = _wikipedia_fetch(related=True)
    packet = MediaWikiProvider(fetch_json=fetch).research(
        _selected_dumped_identity().to_dict()
    ).expanded_packets()[0]

    assert packet.assessment_status == "accepted"
    assert "SpongeBob SquarePants" in packet.claims[0]


def test_tvmaze_show_description_is_not_episode_plot_evidence():
    def fetch(url, params=None, **kwargs):
        if url.endswith("/shows/713"):
            return {
                "id": 713,
                "name": "SpongeBob SquarePants",
                "summary": "A generic promotional description of the whole show.",
                "url": "https://tvmaze.test/shows/713",
            }
        if url.endswith("/episodes/255050"):
            return {
                "id": 255050,
                "name": "Dumped",
                "summary": None,
                "url": "https://tvmaze.test/episodes/255050",
            }
        raise AssertionError(url)

    packet = TVMazeProvider(fetch_json=fetch).research(
        _selected_dumped_identity().to_dict()
    ).expanded_packets()[0]

    assert packet.short_synopsis == ""
    assert packet.plot_points == []
    assert packet.claims == ["Episode title: Dumped"]
    assert "generic promotional" in packet.identity_context["series_description"]


def test_selected_compound_segment_rejects_other_segment_research():
    class BothSegmentsProvider:
        name = "fixture"

        def research(self, identity):
            def child(title, segment_id, summary):
                return ResearchPacket(
                    provider=self.name,
                    title=title,
                    url=f"https://fixture.test/{segment_id}",
                    source_type="episode",
                    reliability=0.8,
                    segment_title=title,
                    segment_id=segment_id,
                    plot_points=[{"plot_id": "P1", "summary": summary}],
                    claims=[summary],
                )

            return ResearchPacket(
                provider=self.name,
                title="container",
                url="https://fixture.test/container",
                source_type="container",
                reliability=0.8,
                segment_packets=[
                    child("Survival of the Idiots", "SEG_A", "Winter facts."),
                    child("Dumped", "SEG_B", "Gary leaves SpongeBob."),
                ],
            )

    dossier = ResearchService([BothSegmentsProvider()]).collect(
        _selected_dumped_identity()
    ).dossier

    assert [segment["title"] for segment in dossier["segments"]] == ["Dumped"]
    assert [point["summary"] for point in dossier["ordered_plot_points"]] == [
        "Gary leaves SpongeBob."
    ]
    rejected = [
        item for item in dossier["source_evaluations"]
        if item["status"] == "rejected_identity_mismatch"
    ]
    assert rejected[0]["claims"] == []

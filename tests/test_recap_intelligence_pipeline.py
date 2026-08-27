import json

from recap_intelligence.identity import IdentityQuery
from recap_intelligence.models import IdentityCandidate
from recap_intelligence.pipeline import run_recap_pipeline
from recap_intelligence.providers import ResearchPacket
from recap_intelligence.writer import TemplateRecapWriter


class FakeIdentityProvider:
    name = "fake-identity"

    def resolve(self, query):
        return [
            IdentityCandidate(
                canonical_id="fake:episode:1",
                content_type=query.content_type,
                title=query.title,
                episode_title=query.segment_title,
                season=query.season,
                episode=query.episode,
                provider=self.name,
                provider_id="1",
                url="https://example.test/episode/1",
                confidence=0.95,
            )
        ]


class FakeResearchProvider:
    name = "fake-research"

    def __init__(self):
        self.calls = 0

    def research(self, identity):
        self.calls += 1
        return ResearchPacket(
            provider=self.name,
            title="Example Show - The Door",
            url="https://example.test/research/1",
            source_type="test_fixture",
            reliability=0.9,
            short_synopsis="Alice opens the door.",
            detailed_synopsis="Alice opens the door.",
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


def test_pipeline_writes_all_track_a_artifacts(tmp_path, monkeypatch):
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"source")
    transcript = tmp_path / "subtitles.json"
    transcript.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 1, "end": 3, "text": "Alice opens the door."}
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "recap_intelligence.source.probe_duration",
        lambda path: 4.0,
    )

    artifacts = run_recap_pipeline(
        query=IdentityQuery(
            content_type="tv",
            title="Example Show",
            season=2,
            episode=5,
            segment_title="The Door",
        ),
        source_video=source,
        transcript_path=transcript,
        output_dir=tmp_path / "recap",
        confirm_index=0,
        identity_providers=[FakeIdentityProvider()],
        research_providers=[FakeResearchProvider()],
    )

    assert set(artifacts) == {
        "episode_identity",
        "episode_research_dossier",
        "verified_story_map",
        "recap_script",
    }
    for path in artifacts.values():
        assert path.exists()
    assert json.loads(artifacts["recap_script"].read_text())["segments"][0]["beat_ids"] == [
        "B001"
    ]


class CountingWriter:
    def __init__(self, prompt_version):
        self.prompt_version = prompt_version
        self.calls = 0

    def cache_identity(self):
        return self.prompt_version, "fixture-model-v1"

    def write(self, story_map):
        self.calls += 1
        return TemplateRecapWriter().write(story_map)


def test_writer_version_invalidates_only_recap_script_cache(tmp_path, monkeypatch):
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"source")
    transcript = tmp_path / "subtitles.json"
    transcript.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 1, "end": 3, "text": "Alice opens the door."}
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("recap_intelligence.source.probe_duration", lambda path: 4.0)
    research = FakeResearchProvider()
    first_writer = CountingWriter("writer-prompt-v1")
    query = IdentityQuery(
        content_type="tv",
        title="Example Show",
        season=2,
        episode=5,
        segment_title="The Door",
    )
    common = {
        "query": query,
        "source_video": source,
        "transcript_path": transcript,
        "output_dir": tmp_path / "recap",
        "confirm_index": 0,
        "identity_providers": [FakeIdentityProvider()],
        "research_providers": [research],
    }

    run_recap_pipeline(**common, writer=first_writer)
    run_recap_pipeline(**common, writer=first_writer)

    second_writer = CountingWriter("writer-prompt-v2")
    run_recap_pipeline(**common, writer=second_writer)

    assert first_writer.calls == 1
    assert second_writer.calls == 1
    assert research.calls == 1
    assert len(list((tmp_path / "recap" / ".cache").glob("*.json"))) == 4

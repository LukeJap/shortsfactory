from recap_intelligence.identity import (
    EpisodeIdentityResolver,
    IdentityQuery,
    normalize_title,
)
from recap_intelligence.models import IdentityCandidate


class FakeIdentityProvider:
    name = "fake"

    def __init__(self, candidates):
        self.candidates = candidates

    def resolve(self, query):
        assert query.content_type == "tv"
        return self.candidates


def candidate(identifier, title, episode_title, confidence):
    return IdentityCandidate(
        canonical_id=identifier,
        content_type="tv",
        title=title,
        episode_title=episode_title,
        season=2,
        episode=5,
        provider="fake",
        provider_id=identifier,
        confidence=confidence,
        url=f"https://example.test/{identifier}",
    )


def test_title_normalization_is_stable():
    assert normalize_title("  The Bob's Show! ") == "the bob s show"


def test_identity_requires_explicit_confirmation_for_ambiguity():
    resolver = EpisodeIdentityResolver(
        [
            FakeIdentityProvider(
                [
                    candidate("one", "Example Show", "The Door", 0.9),
                    candidate("two", "Example Show", "The Window", 0.8),
                ]
            )
        ]
    )
    query = IdentityQuery("TV", " Example Show ", season=2, episode=5)

    pending = resolver.resolve(query)
    assert pending.status == "ambiguous"
    assert pending.selected is None

    confirmed = resolver.require_confirmed(query, confirm_index=1)
    assert confirmed.status == "confirmed"
    assert confirmed.selected.canonical_id == "two"

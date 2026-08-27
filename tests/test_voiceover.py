import wave
from io import BytesIO

from recap_media.orpheus_provider import DEFAULT_VOICE, OrpheusError
from recap_media.voiceover import (
    load_voiceover_durations,
    synthesize_segment,
    synthesize_segments,
    wav_path_for_segment,
)


def _make_wav_bytes(num_frames: int = 4000, framerate: int = 16000) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(framerate)
        wav_file.writeframes(b"\x00\x00" * num_frames)
    return buf.getvalue()


class FakeProvider:
    """Test double standing in for OrpheusProvider -- records every call
    it receives and either returns deterministic WAV bytes or raises
    OrpheusError, without touching the network at all."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, str, float]] = []
        self.fail = fail

    def synthesize_speech(self, text, voice=DEFAULT_VOICE, speed=1.0, timeout=60.0):
        self.calls.append((text, voice, speed))
        if self.fail:
            raise OrpheusError("simulated Orpheus failure")
        return _make_wav_bytes()


# ============================================================
# synthesize_segment: cache behavior
# ============================================================

def test_cache_miss_calls_provider_and_writes_wav(tmp_path):
    provider = FakeProvider()
    manifest_path = tmp_path / "manifest.json"

    result = synthesize_segment(
        provider,
        "VO_001",
        "Hello there.",
        voice="tara",
        output_dir=tmp_path,
        manifest_path=manifest_path,
    )

    assert result.cache_hit is False
    assert result.error is None
    assert result.wav_path == wav_path_for_segment("VO_001", tmp_path)
    assert result.wav_path.exists()
    assert len(provider.calls) == 1
    assert manifest_path.exists()


def test_identical_call_is_a_cache_hit(tmp_path):
    provider = FakeProvider()
    manifest_path = tmp_path / "manifest.json"

    synthesize_segment(
        provider, "VO_001", "Hello there.", voice="tara",
        output_dir=tmp_path, manifest_path=manifest_path,
    )
    result = synthesize_segment(
        provider, "VO_001", "Hello there.", voice="tara",
        output_dir=tmp_path, manifest_path=manifest_path,
    )

    assert result.cache_hit is True
    assert len(provider.calls) == 1  # not called a second time


def test_text_change_invalidates_cache(tmp_path):
    provider = FakeProvider()
    manifest_path = tmp_path / "manifest.json"

    synthesize_segment(
        provider, "VO_001", "Original text.", voice="tara",
        output_dir=tmp_path, manifest_path=manifest_path,
    )
    result = synthesize_segment(
        provider, "VO_001", "Edited text.", voice="tara",
        output_dir=tmp_path, manifest_path=manifest_path,
    )

    assert result.cache_hit is False
    assert len(provider.calls) == 2


def test_voice_change_invalidates_cache(tmp_path):
    provider = FakeProvider()
    manifest_path = tmp_path / "manifest.json"

    synthesize_segment(
        provider, "VO_001", "Hello there.", voice="tara",
        output_dir=tmp_path, manifest_path=manifest_path,
    )
    result = synthesize_segment(
        provider, "VO_001", "Hello there.", voice="leah",
        output_dir=tmp_path, manifest_path=manifest_path,
    )

    assert result.cache_hit is False
    assert len(provider.calls) == 2


def test_force_bypasses_cache(tmp_path):
    provider = FakeProvider()
    manifest_path = tmp_path / "manifest.json"

    synthesize_segment(
        provider, "VO_001", "Hello there.", voice="tara",
        output_dir=tmp_path, manifest_path=manifest_path,
    )
    result = synthesize_segment(
        provider, "VO_001", "Hello there.", voice="tara",
        output_dir=tmp_path, manifest_path=manifest_path, force=True,
    )

    assert result.cache_hit is False
    assert len(provider.calls) == 2


def test_provider_failure_returns_error_result_without_crashing(tmp_path):
    provider = FakeProvider(fail=True)
    manifest_path = tmp_path / "manifest.json"

    result = synthesize_segment(
        provider, "VO_001", "Hello there.", voice="tara",
        output_dir=tmp_path, manifest_path=manifest_path,
    )

    assert result.error is not None
    assert "simulated Orpheus failure" in result.error
    assert result.wav_path is None
    # A failed attempt must not poison the cache with a bogus entry.
    assert not manifest_path.exists()


def test_cache_persists_across_separate_invocations(tmp_path):
    manifest_path = tmp_path / "manifest.json"

    provider_a = FakeProvider()
    synthesize_segment(
        provider_a, "VO_001", "Hello there.", voice="tara",
        output_dir=tmp_path, manifest_path=manifest_path,
    )

    # A fresh provider instance/call, as if this were a separate process
    # -- the manifest on disk is what makes this a cache hit, not any
    # in-memory state.
    provider_b = FakeProvider()
    result = synthesize_segment(
        provider_b, "VO_001", "Hello there.", voice="tara",
        output_dir=tmp_path, manifest_path=manifest_path,
    )

    assert result.cache_hit is True
    assert provider_b.calls == []


# ============================================================
# synthesize_segments: batch behavior
# ============================================================

def _segments():
    return [
        {
            "segment_id": "VO_001",
            "text": "First segment narration.",
            "presentation_hint": "narration_over_source",
        },
        {
            "segment_id": "VO_002",
            "text": "Second segment narration.",
            "presentation_hint": "narration_over_source",
        },
        {
            "segment_id": "VO_003",
            "text": "",
            "presentation_hint": "visual_only",
        },
    ]


def test_synthesize_segments_skips_visual_only(tmp_path):
    provider = FakeProvider()
    results = synthesize_segments(
        provider, _segments(), output_dir=tmp_path, manifest_path=tmp_path / "manifest.json"
    )
    assert [result.segment_id for result in results] == ["VO_001", "VO_002"]


def test_synthesize_segments_writes_one_wav_per_segment_not_combined(tmp_path):
    provider = FakeProvider()
    results = synthesize_segments(
        provider, _segments(), output_dir=tmp_path, manifest_path=tmp_path / "manifest.json"
    )

    wav_files = sorted(tmp_path.glob("*.wav"))
    assert [path.name for path in wav_files] == ["VO_001.wav", "VO_002.wav"]
    for result in results:
        assert result.wav_path.exists()


def test_synthesize_segments_force_only_regenerates_specified_segment(tmp_path):
    provider = FakeProvider()
    manifest_path = tmp_path / "manifest.json"

    synthesize_segments(provider, _segments(), output_dir=tmp_path, manifest_path=manifest_path)
    assert len(provider.calls) == 2

    results = synthesize_segments(
        provider,
        _segments(),
        output_dir=tmp_path,
        manifest_path=manifest_path,
        force_segment_ids=frozenset({"VO_002"}),
    )

    by_id = {result.segment_id: result for result in results}
    assert by_id["VO_001"].cache_hit is True
    assert by_id["VO_002"].cache_hit is False
    assert len(provider.calls) == 3  # only VO_002 regenerated


# ============================================================
# load_voiceover_durations
# ============================================================

def test_load_voiceover_durations_reflects_synthesized_segments(tmp_path):
    provider = FakeProvider()
    manifest_path = tmp_path / "manifest.json"

    synthesize_segments(provider, _segments(), output_dir=tmp_path, manifest_path=manifest_path)

    durations = load_voiceover_durations(manifest_path)
    assert set(durations) == {"VO_001", "VO_002"}
    assert all(duration > 0 for duration in durations.values())


def test_load_voiceover_durations_missing_manifest_returns_empty(tmp_path):
    assert load_voiceover_durations(tmp_path / "does_not_exist.json") == {}

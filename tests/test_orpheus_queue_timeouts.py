"""Deterministic coverage for slow, single-worker Orpheus recap batches."""

from __future__ import annotations

from io import BytesIO
import json
import wave

from recap_media.orpheus_provider import OrpheusError
from recap_media.voiceover import synthesize_segment, synthesize_segments


def _wav_bytes() -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00" * 4_000)
    return buffer.getvalue()


def _v2_segments():
    return [
        {"segment_id": "N_001", "block_type": "narration", "text": "First narration."},
        {"segment_id": "S_001", "block_type": "source_moment", "text": ""},
        {"segment_id": "N_002", "block_type": "narration", "text": "Second narration."},
        {"segment_id": "N_003", "block_type": "narration", "text": "Third narration."},
    ]


class _SerialProvider:
    def __init__(self, fail_text: str | None = None):
        self.fail_text = fail_text
        self.active_calls = 0
        self.max_active_calls = 0
        self.calls: list[str] = []

    def synthesize_speech(self, text, voice="tara", speed=1.0, timeout=600.0):
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        self.calls.append(text)
        try:
            if text == self.fail_text:
                raise OrpheusError("Orpheus-FastAPI timed out after 600s of active synthesis.")
            return _wav_bytes()
        finally:
            self.active_calls -= 1


def test_orpheus_batch_is_serial_and_skips_source_moments(tmp_path):
    provider = _SerialProvider()

    results = synthesize_segments(
        provider,
        _v2_segments(),
        output_dir=tmp_path / "voiceover",
        manifest_path=tmp_path / "voiceover" / "manifest.json",
    )

    assert provider.max_active_calls == 1
    assert [result.segment_id for result in results] == ["N_001", "N_002", "N_003"]
    assert provider.calls == ["First narration.", "Second narration.", "Third narration."]


def test_cached_blocks_resume_without_new_provider_calls(tmp_path):
    output_dir = tmp_path / "voiceover"
    manifest_path = output_dir / "manifest.json"
    provider = _SerialProvider()
    segments = _v2_segments()

    synthesize_segment(provider, "N_001", segments[0]["text"], output_dir=output_dir, manifest_path=manifest_path)
    synthesize_segment(provider, "N_002", segments[2]["text"], output_dir=output_dir, manifest_path=manifest_path)
    provider.calls.clear()

    results = synthesize_segments(
        provider,
        segments,
        output_dir=output_dir,
        manifest_path=manifest_path,
    )

    assert provider.calls == ["Third narration."]
    assert [result.cache_hit for result in results] == [True, True, False]


def test_successes_persist_before_a_fatal_timeout_and_are_reusable(tmp_path):
    output_dir = tmp_path / "voiceover"
    manifest_path = output_dir / "manifest.json"
    provider = _SerialProvider(fail_text="Third narration.")
    segments = _v2_segments()
    progress: list[tuple[str, str]] = []

    results = synthesize_segments(
        provider,
        segments,
        output_dir=output_dir,
        manifest_path=manifest_path,
        on_segment_start=lambda segment_id, _index, _total: progress.append(("start", segment_id)),
        on_segment_complete=lambda result, _index, _total: progress.append(("complete", result.segment_id)),
    )

    assert [result.segment_id for result in results] == ["N_001", "N_002", "N_003"]
    assert results[-1].fatal is True
    assert (output_dir / "N_001.wav").exists()
    assert (output_dir / "N_002.wav").exists()
    assert not (output_dir / "N_003.wav").exists()
    assert [entry for entry in progress if entry[0] == "start"] == [
        ("start", "N_001"),
        ("start", "N_002"),
        ("start", "N_003"),
    ]
    assert provider.calls == ["First narration.", "Second narration.", "Third narration."]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == {"N_001", "N_002"}

    retry_provider = _SerialProvider()
    retry_results = synthesize_segments(
        retry_provider,
        segments,
        output_dir=output_dir,
        manifest_path=manifest_path,
    )
    assert retry_provider.calls == ["Third narration."]
    assert [result.cache_hit for result in retry_results] == [True, True, False]

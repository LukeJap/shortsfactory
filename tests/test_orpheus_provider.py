import wave
from io import BytesIO

import pytest
import requests

from recap_media.orpheus_provider import (
    KNOWN_VOICES,
    OrpheusError,
    OrpheusProvider,
    validate_wav_bytes,
)


def _make_wav_bytes(num_frames: int = 4000, framerate: int = 16000) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(framerate)
        wav_file.writeframes(b"\x00\x00" * num_frames)
    return buf.getvalue()


def _make_empty_wav_bytes() -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b"", text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.content = content
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no json body")
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


# ============================================================
# validate_wav_bytes
# ============================================================

def test_validate_wav_bytes_accepts_real_wav():
    validate_wav_bytes(_make_wav_bytes())  # should not raise


def test_validate_wav_bytes_rejects_empty():
    with pytest.raises(OrpheusError, match="empty"):
        validate_wav_bytes(b"")


def test_validate_wav_bytes_rejects_garbage():
    with pytest.raises(OrpheusError, match="valid WAV"):
        validate_wav_bytes(b"this is not audio data at all")


def test_validate_wav_bytes_rejects_zero_frames():
    with pytest.raises(OrpheusError, match="zero frames"):
        validate_wav_bytes(_make_empty_wav_bytes())


# ============================================================
# readiness()
# ============================================================

def test_readiness_online(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200))
    result = OrpheusProvider().readiness()
    assert result["state"] == "online"


def test_readiness_online_even_on_404_health_path(monkeypatch):
    # Not every Orpheus-FastAPI version implements /health -- a 404 still
    # proves the process itself is up and responding.
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(404))
    result = OrpheusProvider().readiness()
    assert result["state"] == "online"


def test_readiness_offline_on_connection_error(monkeypatch):
    def raise_connection_error(*args, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", raise_connection_error)
    result = OrpheusProvider().readiness()
    assert result["state"] == "offline"


def test_readiness_error_on_server_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(500))
    result = OrpheusProvider().readiness()
    assert result["state"] == "error"


# ============================================================
# list_voices()
# ============================================================

def test_list_voices_returns_live_list(monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **k: FakeResponse(200, json_data={"voices": ["alice", "bob"]}),
    )
    assert OrpheusProvider().list_voices() == ["alice", "bob"]


def test_list_voices_handles_bare_list_response(monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **k: FakeResponse(200, json_data=["alice", "bob"]),
    )
    assert OrpheusProvider().list_voices() == ["alice", "bob"]


def test_list_voices_falls_back_on_request_error(monkeypatch):
    def raise_error(*args, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", raise_error)
    assert OrpheusProvider().list_voices() == KNOWN_VOICES


def test_list_voices_falls_back_on_malformed_json(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, json_data=None))
    assert OrpheusProvider().list_voices() == KNOWN_VOICES


def test_list_voices_falls_back_on_empty_list(monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **k: FakeResponse(200, json_data={"voices": []}),
    )
    assert OrpheusProvider().list_voices() == KNOWN_VOICES


# ============================================================
# synthesize_speech()
# ============================================================

def test_synthesize_speech_empty_text_raises_without_http_call(monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1))
    with pytest.raises(OrpheusError, match="empty"):
        OrpheusProvider().synthesize_speech("   ")
    assert calls == []


def test_synthesize_speech_success_returns_wav_bytes(monkeypatch):
    wav_bytes = _make_wav_bytes()
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **k: FakeResponse(200, content=wav_bytes),
    )
    result = OrpheusProvider().synthesize_speech("Hello there.", voice="tara")
    assert result == wav_bytes


def test_synthesize_speech_non_200_raises(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **k: FakeResponse(503, text="service unavailable"),
    )
    with pytest.raises(OrpheusError, match="503"):
        OrpheusProvider().synthesize_speech("Hello there.")


def test_synthesize_speech_timeout_raises(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(requests, "post", raise_timeout)
    with pytest.raises(OrpheusError, match="timed out"):
        OrpheusProvider().synthesize_speech("Hello there.")


def test_synthesize_speech_connection_error_raises(monkeypatch):
    def raise_error(*args, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", raise_error)
    with pytest.raises(OrpheusError, match="Could not reach"):
        OrpheusProvider().synthesize_speech("Hello there.")


def test_synthesize_speech_invalid_audio_raises(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **k: FakeResponse(200, content=b"not actually a wav file"),
    )
    with pytest.raises(OrpheusError, match="valid WAV"):
        OrpheusProvider().synthesize_speech("Hello there.")

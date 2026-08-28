"""
B2 -- Orpheus-FastAPI HTTP client. Orpheus-FastAPI is a SEPARATE local
service (see the shared contract's architecture diagram); this module
only ever talks to it over HTTP via `requests` (already a ShortsFactory
dependency), the same pattern already used for the local Stable
Diffusion backend (image_backend_status.py). No Orpheus package is
installed into this app's own Python 3.12 environment.

Endpoint contract note: Orpheus-FastAPI (github.com/Lex-au/Orpheus-FastAPI)
exposes an OpenAI-compatible speech endpoint. The paths/defaults below
are this integration's best-effort match to that project's documented
API (POST /v1/audio/speech, default port 5005, the model's 8 named
voices) -- this has not been verified against a live instance. Treat
DEFAULT_API/SPEECH_ENDPOINT/VOICES_ENDPOINT/KNOWN_VOICES as the one place
to correct if a real running server's contract differs; nothing else in
this module should need to change.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import wave
from io import BytesIO
from typing import Any

import requests


DEFAULT_API = os.getenv(
    "SHORTSFACTORY_ORPHEUS_API",
    "http://127.0.0.1:5005",
).rstrip("/")

HEALTH_ENDPOINT = "/health"
VOICES_ENDPOINT = "/v1/audio/voices"
SPEECH_ENDPOINT = "/v1/audio/speech"

# Orpheus's own fixed voice set, as documented by the model release --
# used as a fallback when the running server has no /v1/audio/voices
# endpoint (not every Orpheus-FastAPI version implements one).
KNOWN_VOICES = [
    "tara",
    "leah",
    "jess",
    "leo",
    "dan",
    "mia",
    "zac",
    "zoe",
]

DEFAULT_VOICE = "tara"

READINESS_TIMEOUT = 4.0
SPEECH_TIMEOUT = 60.0


class OrpheusError(Exception):
    """Orpheus-FastAPI is unreachable, errored, or returned invalid audio."""


def validate_wav_bytes(data: bytes) -> None:
    """
    Confirm `data` is a real, readable WAV file -- catches a truncated
    download, an HTML error page returned with a 200 status, or a format
    Orpheus-FastAPI didn't actually honor (response_format ignored).
    Raises OrpheusError rather than letting a bad file reach disk/ffmpeg.
    """

    if not data:
        raise OrpheusError("Orpheus-FastAPI returned empty audio data.")

    try:
        with wave.open(BytesIO(data), "rb") as wav_file:
            if wav_file.getnframes() <= 0:
                raise OrpheusError(
                    "Orpheus-FastAPI returned a WAV file with zero frames."
                )
    except wave.Error as exc:
        raise OrpheusError(
            f"Orpheus-FastAPI did not return valid WAV audio: {exc}"
        ) from exc


class OrpheusProvider:
    """
    Narrow HTTP adapter for a locally-running Orpheus-FastAPI instance.
    Callers should consume readiness()/list_voices()/synthesize_speech()
    rather than reaching into `requests` directly, so the actual endpoint
    contract stays in one place if it needs adjusting.
    """

    def __init__(self, api: str = DEFAULT_API):
        self.api = api.rstrip("/")

    def readiness(self) -> dict[str, Any]:
        """
        Cheap reachability probe -- does not perform a real TTS call. Any
        HTTP response (even a 404 on the health path itself) proves the
        process is up; a connection error/timeout means offline.
        """

        try:
            response = requests.get(
                self.api + HEALTH_ENDPOINT,
                timeout=READINESS_TIMEOUT,
            )
        except requests.RequestException as exc:
            return {
                "state": "offline",
                "message": "Could not connect to Orpheus-FastAPI.",
                "error": str(exc),
            }

        if response.status_code >= 500:
            return {
                "state": "error",
                "message": f"Orpheus-FastAPI returned HTTP {response.status_code}.",
            }

        return {
            "state": "online",
            "message": "Orpheus-FastAPI reachable.",
        }

    def list_voices(self) -> list[str]:
        """
        Live voice listing if the server exposes it, else the known
        static Orpheus voice set. Never raises -- a listing failure just
        falls back, since the caller still has a usable default.
        """

        try:
            response = requests.get(
                self.api + VOICES_ENDPOINT,
                timeout=READINESS_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            return list(KNOWN_VOICES)

        voices = data.get("voices") if isinstance(data, dict) else data
        if not isinstance(voices, list) or not voices:
            return list(KNOWN_VOICES)

        normalized = [str(voice).strip() for voice in voices if str(voice).strip()]
        return normalized or list(KNOWN_VOICES)

    def synthesize_speech(
        self,
        text: str,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        timeout: float = SPEECH_TIMEOUT,
    ) -> bytes:
        """
        Request narration audio for one segment. Returns raw WAV bytes,
        already validated (validate_wav_bytes()) -- never hands back
        something the caller still needs to sanity-check itself.
        """

        if not text or not text.strip():
            raise OrpheusError("Cannot synthesize empty narration text.")

        try:
            response = requests.post(
                self.api + SPEECH_ENDPOINT,
                json={
                    "input": text,
                    "voice": voice,
                    "response_format": "wav",
                    "speed": speed,
                },
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise OrpheusError(
                f"Orpheus-FastAPI timed out after {timeout:.0f}s generating speech."
            ) from exc
        except requests.RequestException as exc:
            raise OrpheusError(f"Could not reach Orpheus-FastAPI: {exc}") from exc

        if response.status_code != 200:
            raise OrpheusError(
                f"Orpheus-FastAPI returned HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )

        audio_bytes = response.content
        validate_wav_bytes(audio_bytes)
        return audio_bytes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check ShortsFactory's Orpheus-FastAPI backend status.",
    )
    parser.add_argument(
        "--api",
        default=DEFAULT_API,
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="Also list available voices if the backend is online.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    provider = OrpheusProvider(args.api)

    status = provider.readiness()
    if args.list_voices and status.get("state") == "online":
        status["voices"] = provider.list_voices()

    print(
        json.dumps(status, ensure_ascii=False),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

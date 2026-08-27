"""Mockable JSON-model adapters for optional local Ollama synthesis."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Protocol

import requests


class JsonModel(Protocol):
    def generate_json(self, prompt: str) -> dict[str, Any]:
        ...


class ModelResponseError(RuntimeError):
    """Raised when a configured JSON model cannot return a valid object."""


@dataclass(frozen=True)
class ModelGeneration:
    """One model response with both its raw and parsed representations."""

    raw_text: str
    parsed: dict[str, Any] | None
    parse_error: str = ""


def parse_json_object(raw_text: str) -> ModelGeneration:
    """Parse a JSON object while retaining malformed output for repair."""
    raw = str(raw_text or "").strip()
    if not raw:
        return ModelGeneration(raw_text=raw, parsed=None, parse_error="empty response")

    candidates = [raw]
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        fenced = "\n".join(lines).strip()
        if fenced:
            candidates.append(fenced)

    decoder = json.JSONDecoder()
    last_error = "response was not a JSON object"
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            last_error = str(exc)
        else:
            if isinstance(parsed, dict):
                return ModelGeneration(raw_text=raw, parsed=parsed)
            last_error = "response JSON must be an object"

        for index, character in enumerate(candidate):
            if character != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError as exc:
                last_error = str(exc)
                continue
            if isinstance(parsed, dict):
                return ModelGeneration(raw_text=raw, parsed=parsed)
            last_error = "response JSON must be an object"

    return ModelGeneration(raw_text=raw, parsed=None, parse_error=last_error)


@dataclass
class OllamaJsonModel:
    host: str = ""
    model: str = ""
    timeout_seconds: float = 180.0
    context_length: int = 0
    think: bool = False

    def __post_init__(self) -> None:
        if not self.host:
            self.host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
        if not self.model:
            self.model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
        if not self.context_length:
            try:
                self.context_length = int(
                    os.getenv("OLLAMA_CONTEXT_LENGTH", "8192")
                )
            except (TypeError, ValueError):
                self.context_length = 8192
        self.context_length = max(4096, self.context_length)
        configured_timeout = os.getenv("OLLAMA_TIMEOUT_SECONDS", "").strip()
        if configured_timeout and self.timeout_seconds == 180.0:
            try:
                self.timeout_seconds = max(30.0, float(configured_timeout))
            except (TypeError, ValueError):
                pass

    def generate(self, prompt: str) -> ModelGeneration:
        try:
            response = requests.post(
                f"{self.host.rstrip('/')}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "think": self.think,
                    "options": {
                        "temperature": 0.15,
                        "num_ctx": self.context_length,
                    },
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            raw = str(payload.get("response", "") or "").strip()
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise ModelResponseError(f"Ollama generation failed: {exc}") from exc
        return parse_json_object(raw)

    def generate_json(self, prompt: str) -> dict[str, Any]:
        generation = self.generate(prompt)
        if generation.parsed is None:
            raise ModelResponseError(
                "Ollama JSON generation failed: " + generation.parse_error
            )
        return generation.parsed

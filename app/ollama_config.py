"""
Single source of truth for how ShortsFactory's local Ollama LLM calls are
addressed: host and default model, both overridable via environment
variables. Imported by every script that calls Ollama directly
(plan_short.py, content_edit.py, semantic_edit.py, ai_visual_planner.py,
analyze.py) except where a script has a deliberately different need (e.g.
analyze.py discovers whichever model is actually installed rather than
assuming this default model name). canvas_config.py and pipeline_paths.py
later mirrored this same small-leaf-config-module pattern for other
values.
"""

from __future__ import annotations

import os


OLLAMA_HOST = os.getenv(
    "OLLAMA_HOST",
    "http://127.0.0.1:11434",
)

OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "llama3.1:8b",
)

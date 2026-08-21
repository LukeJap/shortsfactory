import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))

from analyze import (
    build_window_ranking_prompt,
    call_ollama_window_ranker,
    generate_valid_windows,
    load_transcript,
    normalize_ollama_host,
    select_model,
)


ROOT = Path(__file__).resolve().parent
TRANSCRIPT = ROOT / "short1.json"


def main():
    print("Loading transcript...")

    transcript = load_transcript(TRANSCRIPT)

    print(f"Transcript segments: {len(transcript.segments)}")

    valid_windows = generate_valid_windows(transcript.segments)

    print(f"Valid windows: {len(valid_windows)}")
    print()

    host = normalize_ollama_host(os.environ.get("OLLAMA_HOST"))

    from analyze import get_ollama_models

    models, error = get_ollama_models(host)

    if error:
        print(f"Ollama error: {error}")
        return 1

    model, error = select_model(models)

    if error:
        print(f"Model error: {error}")
        return 1

    print(f"Using model: {model}")
    print()
    print("Asking Ollama to rank the windows...")
    print()

    prompt = build_window_ranking_prompt(
        transcript,
        valid_windows,
    )

    result = call_ollama_window_ranker(
        host,
        model,
        prompt,
    )

    print("OLLAMA RESULT:")
    print("=" * 60)

    import json

    print(json.dumps(result, indent=2))

    print("=" * 60)


if __name__ == "__main__":
    raise SystemExit(main())
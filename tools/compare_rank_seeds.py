"""Compare two Find Best Clips runs to test for positional score bias.

Usage:
    # run once per seed, copying the result aside each time
    $env:SHORTS_RANK_SEED=1;  .\.venv\Scripts\python.exe -m app.analyze ...
    copy output\analysis.json output\analysis_seed1.json
    $env:SHORTS_RANK_SEED=2;  .\.venv\Scripts\python.exe -m app.analyze ...
    copy output\analysis.json output\analysis_seed2.json

    .\.venv\Scripts\python.exe tools\compare_rank_seeds.py ^
        output\analysis_seed1.json output\analysis_seed2.json

Reports, for each run: whether score tracks episode position, and, across runs,
how far each clip's score moved. A clip whose score swings widely between seeds
was scored by where it sat in the prompt, not by what is in it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RUBRIC = ("hook_strength", "self_contained", "payoff", "peak")


def load(path: Path) -> dict[str, dict]:
    clips = json.loads(path.read_text(encoding="utf-8"))["analysis"]["candidate_clips"]
    return {c["start_timestamp"]: c for c in clips}


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation, no numpy/scipy dependency."""
    def ranks(vals: list[float]) -> list[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        out = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def report_one(label: str, clips: dict[str, dict]) -> None:
    ordered = sorted(clips.values(), key=lambda c: c["start_timestamp"])
    times = list(range(len(ordered)))
    scores = [float(c.get("score", 0)) for c in ordered]
    rho = spearman(times, scores)
    print(f"\n{label}: {len(ordered)} clips")
    missing = [c["start_timestamp"] for c in ordered if not all(d in c for d in RUBRIC)]
    print(f"  sub-scores present : {len(ordered) - len(missing)}/{len(ordered)}"
          + (f"  missing on {missing}" if missing else ""))
    print(f"  score vs episode position (Spearman): {rho:+.2f}")
    if rho < -0.8:
        print("    STRONG decline toward the end of the episode")
    elif rho > 0.8:
        print("    STRONG incline toward the end of the episode")
    else:
        print("    no strong positional trend")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    a_path, b_path = Path(sys.argv[1]), Path(sys.argv[2])
    a, b = load(a_path), load(b_path)
    report_one(a_path.name, a)
    report_one(b_path.name, b)

    shared = sorted(set(a) & set(b))
    print(f"\nclips selected by BOTH runs: {len(shared)} "
          f"(of {len(a)} and {len(b)})")
    if not shared:
        print("  No overlap at all — the two runs disagree completely, which is")
        print("  itself evidence that ordering, not content, drives selection.")
        return 0

    print(f"\n{'clip':>12}{'seedA':>8}{'seedB':>8}{'delta':>8}")
    deltas = []
    for t in shared:
        sa, sb = int(a[t].get("score", 0)), int(b[t].get("score", 0))
        d = abs(sa - sb)
        deltas.append(d)
        flag = "  <-- unstable" if d > 15 else ""
        print(f"{t:>12}{sa:>8}{sb:>8}{d:>8}{flag}")

    worst, mean = max(deltas), sum(deltas) / len(deltas)
    print(f"\n  mean |delta| {mean:.1f}, worst {worst}")
    print("\nVERDICT:")
    if worst > 15:
        print("  Scores move with prompt order -> positional bias persists.")
        print("  The model is not judging content. Proceed to the model bake-off.")
    else:
        print("  Scores are stable across orderings -> the model is judging content.")
        print("  The ramp was the example block, not the model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

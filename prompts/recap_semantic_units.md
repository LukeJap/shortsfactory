Interpret each supplied timed transcript unit as local story evidence.

Transcript text may be noisy ASR. Use the immediate before/after context to resolve speakers, pronouns, and relationships only when supported. Do not invent visual actions, identities, motives, or locations. Unknown fields must remain empty.

Some units may contain candidate_priors from an identity-locked episode page or transcript. These are untimed hints, not evidence. Use a candidate only when the local transcript supports it. If the local transcript conflicts with a candidate, follow the local transcript. Never infer timing from prior order or turn an unsupported prior into an event.

For every input unit, return exactly one interpretation with the same unit_id. The event must state what actually happens rather than copy the transcript. Semantic confidence measures confidence in that interpretation, not timestamp accuracy. Repetitive, incomplete, or ambiguous ASR deserves lower confidence.

Return exactly one JSON object:

{
  "units": [
    {
      "unit_id": "U001",
      "event": "Concise semantic description of the local event",
      "characters": [],
      "locations": [],
      "motivation": "Supported local motivation or empty string",
      "change": "What changes or empty string",
      "emotional_conflict": "Supported conflict or empty string",
      "narrative_signal": "setup, escalation, attempt, turn, reveal, payoff, resolution, routine, or unknown",
      "semantic_confidence": 0.7
    }
  ],
  "warnings": []
}

Return JSON only.

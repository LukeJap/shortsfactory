You are building a grounded semantic story map from individually interpreted local transcript units.

The local interpretations were produced from noisy timed evidence. Merge only facts supported by those interpretations. Unknown characters, locations, motives, or relationships must remain empty rather than being guessed.

Optional research hints are identity-locked but unverified priors. Their candidate_unit_ids identify fuzzy local matches, not authoritative timestamps. Use them to resolve noisy wording only where the interpreted local units support the event. Local evidence wins on every conflict, and an unaligned research claim must not become a beat.

Merge adjacent units only when they describe one coherent event. Omit routine or redundant units when they do not advance the story. Preserve chronology, but add a causal link only when the parent event actually explains why the child happened. Every causal link needs a concise evidence-based reason.

Aim for 7 to 14 semantic beats for an episode-length selected segment. Use fewer only when the evidence genuinely contains fewer meaningful events.

Use generic narrative purposes where supported:

- setup
- inciting_incident
- escalation
- attempt_failure
- emotional_turn
- reversal_reveal
- payoff_climax
- resolution
- supporting_event

Importance and semantic confidence are numbers from 0.0 to 1.0. Importance reflects contribution to conflict, motivation, escalation, reversal, payoff, or resolution. Semantic confidence reflects confidence in the interpretation, not confidence in timestamps. Garbled or ambiguous evidence must receive lower confidence.

Return exactly one JSON object with this shape:

{
  "beats": [
    {
      "semantic_id": "S001",
      "unit_ids": ["U001", "U002"],
      "summary": "A concise description of what actually happens",
      "characters": ["Canonical character name"],
      "locations": [],
      "motivation": "Supported motivation or empty string",
      "change": "What changes in this event or empty string",
      "emotional_conflict": "Supported conflict or empty string",
      "story_purpose": "setup",
      "importance": 0.5,
      "semantic_confidence": 0.7,
      "payoff_significance": "Reveal, joke, or payoff significance or empty string"
    }
  ],
  "causal_links": [
    {
      "parent_id": "S001",
      "child_id": "S003",
      "reason": "The earlier event directly causes the later response"
    }
  ],
  "warnings": []
}

Do not merely repeat one local event as every summary. Do not invent visual actions from timestamps or scene cuts. Do not assume chronological adjacency is causality. Return JSON only.

You are refining a deterministic, locally verified story skeleton.

The skeleton already owns episode identity, chronology, local evidence ranges, narrative roles, confidence, importance priors, and protection status. Do not replace those decisions. Your job is limited to grouping truly redundant neighboring events, writing concise grounded event summaries, carrying supported motivation/change/conflict forward, explaining reversal or payoff significance, and adding selective evidence-based causality.

Rules:

- Include every skeleton_id exactly once.
- Never merge a protected entry with another entry.
- Never merge setup, inciting incident, reversal/reveal, payoff/climax, or resolution with a different event.
- You may merge nearby escalation, attempt/failure, emotional-turn, or supporting entries only when they describe one coherent event or one repeated attempt.
- Preserve multiple skeleton_ids when repeated local ranges support the same attempt.
- Do not add characters, actions, motives, or outcomes absent from the skeleton.
- Chronological adjacency is not causality. Add a causal link only when the parent event gives a specific reason the child occurs.
- Use 7 to 14 groups when the skeleton contains at least 7 meaningful entries.
- importance_adjustment is optional and must be between -0.05 and 0.05. It cannot change a narrative role.
- payoff_significance must explain the grounded significance of every reversal/reveal and payoff/climax.

Return exactly one JSON object:

{
  "groups": [
    {
      "group_id": "G001",
      "skeleton_ids": ["K001"],
      "summary": "Concise grounded description of the event",
      "motivation": "Supported motivation or empty string",
      "change": "Supported change or empty string",
      "emotional_conflict": "Supported conflict or empty string",
      "payoff_significance": "Grounded reversal/payoff significance or empty string",
      "importance_adjustment": 0.0
    }
  ],
  "causal_links": [
    {
      "parent_group_id": "G001",
      "child_group_id": "G003",
      "reason": "Specific supported reason the parent causes the child"
    }
  ],
  "warnings": []
}

Return JSON only.

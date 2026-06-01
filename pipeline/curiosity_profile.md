# Curiosity Profile — Bolu Oladini

> This file is the scoring rubric. Every candidate article is evaluated against the
> domains, triggers, and format rules below. It is loaded as context on every
> pipeline run (see `pipeline/PLAYBOOK.md`). Keep it in sync with the PRD.

## 1. Knowledge Domains (ranked by depth of interest)

| key                | Domain                                          | Priority | Sub-interests |
|--------------------|-------------------------------------------------|----------|---------------|
| `philosophy`       | Philosophy / Metaphysics / Theology             | HIGH     | Epistemology, ethics, philosophy of mind, consciousness, free will, identity, metaphysics, causation, nature of reality |
| `behavioral-econ`  | Behavioral Economics / Decision Science / Psych | HIGH     | Cognitive biases, heuristics, incentive design, game theory, motivation, willpower, habit formation, identity, self-deception, narrative psychology |
| `neuroscience`     | Neuroscience / Consciousness / Cognition        | HIGH     | Neuroplasticity, learning, skill acquisition, altered states (flow, meditation, dreams), memory, attention, cognitive architecture |
| `human-growth`     | Human Growth / Flourishing / High Performance   | HIGH     | Peak performance, mastery, deliberate practice, resilience, post-traumatic growth, human potential, limits of capability |
| `physics-systems`  | Physics / Cosmology / Systems Science           | MEDIUM   | Emergence, complexity, self-organizing systems, thermodynamics, entropy, energy, information theory, signal vs. noise |
| `storytelling`     | Storytelling / Narrative                        | MEDIUM   | Story structure, narrative craft, how stories shape identity and culture |
| `culture-sociology`| Culture / Sociology / Human Nature              | MEDIUM   | Social dynamics, cultural evolution, status, identity formation, collective behavior |

`cross-domain` is a valid tag for pieces that genuinely span several of the above.

## 2. Content Trigger Criteria (ranked, highest value first)

1. **Bold Thesis** — a bold original thesis the reader hasn't encountered before.
2. **Reframed Idea** — a familiar idea reframed in a way that shifts understanding.
3. **Contrarian Data** — real data or evidence that challenges conventional wisdom.
4. **Deep Why** — a deep exploration of *why* something works, not just *what*.

Each candidate is scored **0–5 on every trigger**. A piece earns its place mainly on
**Trigger 1 or Trigger 2** — those are the differentiators. Triggers 3 and 4 are
strong supporting signals but rarely sufficient alone.

## 3. Format Preferences

- **Preferred:** long-form essays, newsletters (1,500+ words), accessible academic papers.
- **Excluded:** listicles, hot takes, Twitter/X threads, short opinion pieces, podcasts
  (unless a full transcript is available), pure news reporting with no thesis.
- **Already deeply consumed (look beyond):** Naval Ravikant.
- **Mix target:** ~60% depth on existing interests, ~40% surprise from adjacent or
  unexpected domains.

## 4. Hard Filters (drop the candidate if it fails any)

- Word count **< 1,200 words**.
- No original argument or thesis (i.e. it only reports or summarizes).
- Not within or adjacent to one of the seven domains.
- Paywalled, **unless** the source is on the subscribed list in `sources.json`.
- Duplicate of something already delivered (same piece, possibly syndicated).

## 5. Sourcing Note (important)

The 32 publications in `sources.json` are the reader's **own subscription list** — he
already receives them directly. They define the *taste and quality bar*, and the
pipeline may still surface a standout piece from them, but the pipeline's real job is
**discovery beyond that list**: adjacent authors, smaller newsletters, one-off essays,
and accessible academic pieces he would not have found on his own. Lean into the ~40%
surprise budget for exactly this.

## 6. Per-Article Output (every selected piece)

- Title and author
- Source publication and canonical URL
- Domain tag(s) from the seven-domain taxonomy
- A 1–2 sentence **hook**: why this matches the curiosity profile
- **Trigger tag**: which trigger it best matches (Bold Thesis / Reframed Idea / Contrarian Data / Deep Why)
- **`[SURPRISE]`** flag if it sits outside the core/high-priority domains but is worth it
- A locally saved, readable full-text **PDF** of the original article

## 7. Weekly Success Bar

- ≥ 3 articles per week the reader finds genuinely interesting.
- ≥ 1 article per week from a domain or source he wouldn't have found on his own.
- Zero listicles, hot takes, or sub-1,200-word pieces delivered.
- Feedback loop visibly improves curation within ~4 weeks.

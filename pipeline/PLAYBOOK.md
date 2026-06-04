# Curiosity Pipeline — Run Playbook

You (Claude Code) are the curation engine. Follow this playbook top to bottom on every
run. It is designed to run **inside Claude Code** — unattended in GitHub Actions, or
manually in a session. Be rigorous: the reader's time is the scarce resource. It is far
better to deliver **3 excellent** pieces than 5 mediocre ones. An empty day is
acceptable; a listicle is not.

## Golden rules

- Honor the **hard filters** and **trigger rubric** in `pipeline/curiosity_profile.md`. Read it first.
- A piece earns its place mainly on **Trigger 1 (Bold Thesis)** or **Trigger 2 (Reframed Idea)**.
- The 32 sources in `sources.json` are the reader's **own subscriptions** — he already gets them.
  Your differentiated value is **discovery beyond that list**. Spend the ~40% surprise budget there.
- Never deliver: listicles, hot takes, threads, pure news, sub-1,200-word pieces, or paywalled
  pieces from non-subscribed sources.
- No summarizing the articles. The reader reads the originals; you save the full text as PDF.

## Inputs to load

1. `pipeline/curiosity_profile.md` — the rubric (domains, triggers, filters, format).
2. `pipeline/sources.json` — subscriptions + per-source `weight` (higher = surface more).
3. `pipeline/feedback.json` — history of applied feedback (context only).
4. `work/candidates.json` — RSS items pre-fetched by the workflow (may be absent on manual runs).
5. The last ~10 days of `digests/*/scores.json` — to **de-duplicate** against what was already sent.

Let `TODAY` = today's date (`date -u +%F`). All outputs go under `digests/$TODAY/`.

---

## Step 1 — Apply pending feedback first

```bash
python scripts/apply_feedback.py
```

This reads `feedback/inbox.md`, nudges source `weight`s, logs to `pipeline/feedback.json`, and
clears resolved lines. Re-read `sources.json` afterward so you use the updated weights.

## Step 2 — Build the candidate pool

**(a) Subscribed feeds (already fetched).** Read `work/candidates.json`. If it is missing or empty,
generate it:

```bash
python scripts/fetch_feeds.py --days 4 --out work/candidates.json
```

**(b) Discovery beyond the list (the important part).** Use `WebSearch` to find recent (last ~7 days)
long-form essays / accessible papers in the seven domains from authors and outlets **not** in
`sources.json`. To force novelty, pass the subscribed sites as `blocked_domains` on these searches,
e.g. `blocked_domains: ["aeon.co","noemamag.com","quantamagazine.org","substack.com", ...]`.
Run several targeted queries — e.g.:

- "best long-form essay 2026 consciousness OR free will OR philosophy of mind"
- "new essay emergence complexity systems thinking long read"
- "contrarian data psychology decision-making essay 2026"
- "deliberate practice OR mastery OR human potential deep essay"
- adjacent/unexpected angles to satisfy the surprise budget (history of science, anthropology,
  information theory, narrative craft, etc.)

Aim to collect **15–30 candidates** total across (a) and (b) before filtering.

## Step 3 — Pre-filter (cheap rejects first)

For each candidate, drop it if any hard filter fails. Use `WebFetch` to check the real article when
title/summary is ambiguous. Reject:

- < 1,200 words, or clearly a listicle / thread / hot take / pure news report;
- no original argument or thesis;
- outside (and not adjacent to) the seven domains;
- paywalled **and** not a subscribed source;
- a duplicate of any URL/title already in the last ~10 days of `scores.json`.

## Step 4 — Score the survivors

For each surviving candidate, produce one JSON record. Score every trigger **0–5**:

```json
{
  "id": "slug-from-title",
  "title": "…",
  "url": "https://…",
  "author": "…",
  "source": "Publication name",
  "source_id": "matching id from sources.json, or null if discovered",
  "domains": ["philosophy", "physics-systems"],
  "triggers": { "bold_thesis": 0, "reframed_idea": 0, "contrarian_data": 0, "deep_why": 0 },
  "top_trigger": "bold_thesis",
  "surprise": false,
  "hook": "1–2 sentences: why THIS reader, given his profile, should read this.",
  "subscribed": true,
  "est_word_count": 1800
}
```

- `top_trigger` = the highest-scoring trigger (ties → prefer the higher-ranked trigger).
- `surprise` = true if it sits **outside** the four HIGH-priority domains but is still worth reading.
- A candidate is **eligible** only if `bold_thesis ≥ 4` **or** `reframed_idea ≥ 4` (or a rare 3 backed by
  strong `contrarian_data`/`deep_why`).

## Step 5 — Select 3–5 for the day

From eligible candidates, choose the day's set (target = the run's count input, 3–5):

- Respect the **~60/40 mix**: ~60% squarely in the reader's existing HIGH-priority interests, ~40% surprise.
- Weight selection by source `weight` (feedback-tuned). Prefer **discovered** pieces over subscribed ones
  when quality is comparable (he already receives the subscribed ones).
- Diversity: avoid more than ~1 piece from the same source or author in a single day.
- Weekly bar: ensure **≥ 1 surprise** piece lands across the week.
- If fewer than 3 truly clear the bar, deliver fewer. Do not pad.

## Step 6 — Save each selected article as a readable PDF

For each selected article:

```bash
python scripts/save_article_pdf.py \
  --url "<URL>" \
  --out "digests/$TODAY/<slug>.pdf" \
  --title "<Title>" --author "<Author>" --source "<Source>"
```

Check the JSON it prints. If `ok` is false or `word_count` < 1,200, the piece is likely
truncated/paywalled — reconsider it and, if needed, replace it with the next eligible candidate.

## Step 7 — Write the digest and the scores log

**`digests/$TODAY/scores.json`** — `{ "date": "$TODAY", "generated": "<iso>", "articles": [ <records from Step 4 for SELECTED items, plus "pdf": "<slug>.pdf" and "selected": true> ] }`. (This is what
`apply_feedback.py` reads to map feedback → source, so keep `id`, `url`, `source_id`, `author`,
`domains`, `top_trigger` accurate.)

**`digests/$TODAY/index.md`** — the human-facing digest, using this format:

```markdown
# Curiosity Digest — {Weekday}, {Month DD, YYYY}

{N} pick(s) today. Trigger legend: Bold Thesis · Reframed Idea · Contrarian Data · Deep Why.
Feedback? Add `STAR` / `MORE` / `SKIP` lines to `feedback/inbox.md` using the Article IDs below.

---

### 1. {Title}
**Author:** {Author} · **Source:** [{Source}]({URL}) · **Domains:** {domain tags} · **Trigger:** {Trigger Name}{ · `[SURPRISE]`}
**Article ID:** `{$TODAY}/{slug}` · **PDF:** [`{slug}.pdf`](./{slug}.pdf)

{1–2 sentence hook.}

---

### 2. …
```

## Step 8 — Finish

- In **CI**, a later workflow step commits and pushes your file changes — do **not** run git yourself
  and do **not** open a PR.
- In a **manual** session, tell the user what you delivered and let them commit, unless they ask you to.

## Anti-patterns (self-check before finishing)

- Delivered something thesis-free, short, or a listicle → remove it.
- All picks from subscribed sources, zero discovery → redo Step 2(b).
- Two+ pieces from the same author/source in one day → trim.
- Hooks that summarize the article instead of saying *why it matches this reader* → rewrite.

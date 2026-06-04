# Curiosity Pipeline

An automated reading curator. Every weekday it sources long-form essays, newsletters, and
accessible academic papers across seven knowledge domains, scores each against four
"curiosity triggers," and delivers the **3–5 best per week** as clean, readable **full-text
PDFs** plus a browsable daily digest — with a Star/Skip/More feedback loop that sharpens the
curation over time.

Built from `CuriosityPipelinePRD`. It runs **inside Claude Code**, unattended, on a schedule.

```
pipeline/    curiosity_profile.md · sources.json · feedback.json · PLAYBOOK.md
scripts/     fetch_feeds.py · save_article_pdf.py · apply_feedback.py · tests/
digests/     <date>/index.md · <slug>.pdf · scores.json     ← the daily output
feedback/    inbox.md                                        ← you edit this
.github/workflows/curiosity-pipeline.yml                     ← the weekday cron
```

## How it works

1. A weekday GitHub Actions cron pre-fetches your subscribed RSS feeds, then invokes the
   **Claude Code GitHub Action**.
2. Claude follows [`pipeline/PLAYBOOK.md`](pipeline/PLAYBOOK.md): it discovers candidates from
   your feeds **and beyond** (web search for pieces you wouldn't have found), filters out
   anything short/thesis-free/off-profile, scores survivors against the four triggers, and
   selects the day's best.
3. For each pick it saves a clean, distraction-free **PDF of the full article**, writes a
   digest (`index.md`), and commits everything back to `digests/<date>/`.
4. You skim the digest, read the PDFs, and drop feedback in `feedback/inbox.md`. The next run
   applies it — good sources get surfaced more, dull ones less.

The curation **judgment** is Claude's; small Python scripts only do the plumbing (RSS parsing,
PDF rendering, weight math).

## One-time setup

The scheduler authenticates with your **Claude subscription** (Pro/Max) — no metered API key.

1. **Generate a Claude Code OAuth token** on your machine:
   ```bash
   claude setup-token
   ```
   Copy the token it prints.

2. **Add it as a repository secret** named exactly `CLAUDE_CODE_OAUTH_TOKEN`:
   GitHub → repo **Settings → Secrets and variables → Actions → New repository secret**.

3. **Merge this to your default branch.** GitHub only activates `schedule` triggers and the
   manual **Run workflow** button for workflows that live on the default branch.

4. *(Optional)* Adjust the cron time in
   [`.github/workflows/curiosity-pipeline.yml`](.github/workflows/curiosity-pipeline.yml)
   (it's `0 11 * * 1-5` = 11:00 UTC, Mon–Fri).

5. **Subscribe to the sources you want in your own inbox.** The pipeline treats
   `pipeline/sources.json` as *your* subscription list and focuses its discovery on finding
   things *beyond* it. A good starting six (free tiers, full-profile coverage):
   *The Intrinsic Perspective, Experimental History, Default Wisdom, Quanta, The Growth
   Equation, Aeon.*

Then either wait for the next weekday run, or trigger one manually from the **Actions** tab
→ *Curiosity Pipeline* → *Run workflow*.

## Giving feedback

Open [`feedback/inbox.md`](feedback/inbox.md) and add one line per article, using the
**Article ID** shown in each digest (e.g. `2026-06-01/the-ladder-of-emergence`):

```
STAR 2026-06-01/the-ladder-of-emergence    # excellent — more like this  (+0.20 weight)
MORE 2026-06-01/why-status-games-persist    # go deeper on this           (+0.30 weight)
SKIP 2026-06-01/another-take-on-habits       # not interesting             (−0.20 weight)
```

The next run applies these (`scripts/apply_feedback.py`), re-weights the source in
`sources.json`, logs them to `pipeline/feedback.json`, and clears the processed lines.

## Running / developing locally

```bash
pip install -r scripts/requirements.txt
python scripts/tests/test_smoke.py                 # verify the plumbing (no network needed)
python scripts/fetch_feeds.py --days 4             # → work/candidates.json
python scripts/save_article_pdf.py --url URL --out out.pdf   # render any article to PDF
```

To do a real curation run yourself, open a Claude Code session in this repo and say:
> "Run the Curiosity Pipeline per pipeline/PLAYBOOK.md for today."

## Notes & limits

- **Networking:** the GitHub Actions runner has open internet, so fetching works there. The
  Claude Code *web sandbox* is egress-allowlisted, so live fetches may 403 in-session — that's
  expected; the smoke test validates logic offline.
- **PDFs** are extracted main-content (readability-style). Paywalled or heavily-scripted pages
  may extract poorly; the renderer flags anything under 1,200 words so it isn't mistaken for the
  full piece.
- **v1 scope** (per PRD): no auto-summarization, no read-later integrations, no podcast/video or
  social content.

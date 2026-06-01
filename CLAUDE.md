# CLAUDE.md — Curiosity Pipeline

Project context for Claude Code sessions working in this repo.

## What this is

An automated curation pipeline that delivers 3–5 high-signal long-form articles per week,
matched to the owner's curiosity profile across seven knowledge domains, as **readable
full-text PDFs** plus a browsable daily digest. Built per `CuriosityPipelinePRD`.

## How a run works

The pipeline runs **inside Claude Code** — either unattended via the GitHub Action
(`.github/workflows/curiosity-pipeline.yml`, weekday cron) or manually in a session.
The agent follows **`pipeline/PLAYBOOK.md`** step by step. To run it manually:

> "Run the Curiosity Pipeline per pipeline/PLAYBOOK.md for today."

## Key files

| Path | Role |
|------|------|
| `pipeline/curiosity_profile.md` | The scoring rubric: domains, triggers, filters, format. |
| `pipeline/sources.json`         | The owner's 32 subscriptions + feedback-tuned `weight`s. |
| `pipeline/feedback.json`        | Append-only log of applied Star/Skip/More feedback. |
| `pipeline/PLAYBOOK.md`          | The step-by-step run instructions (the "prompt"). |
| `scripts/fetch_feeds.py`        | RSS/Atom → `work/candidates.json` (stdlib parser + feed auto-discovery). |
| `scripts/save_article_pdf.py`   | URL → clean readable PDF (trafilatura + fpdf2/DejaVu). |
| `scripts/apply_feedback.py`     | `feedback/inbox.md` → adjusts source weights, logs feedback. |
| `scripts/tests/test_smoke.py`   | Network-free smoke test for all plumbing. |
| `feedback/inbox.md`             | Human-editable feedback inbox (STAR/MORE/SKIP). |
| `digests/<date>/`               | Output: `index.md`, per-article `*.pdf`, `scores.json`. |

## Division of labor

- **Deterministic scripts** do the I/O-heavy plumbing (feed parsing, PDF rendering, weight math).
- **Claude (the agent)** does the judgment: discovery, filtering, trigger scoring, selection, hooks.
  This is deliberate — "run inside Claude Code" means the *curation* is the model's, not a hard-coded
  API script's.

## Conventions

- Python 3.11, stdlib-first. Deps in `scripts/requirements.txt` (no `feedparser` — its `sgmllib3k`
  sub-dependency has no wheel; we parse RSS/Atom with `xml.etree`).
- Scripts assume they're run as files (`python scripts/foo.py`) so `import lib` resolves; paths are
  computed from `__file__`, so the working directory doesn't matter.
- `work/` is scratch (git-ignored). Everything under `digests/` is committed.
- Run `python scripts/tests/test_smoke.py` after touching any script.

## Environment notes

- The **GitHub Actions runner has open internet** — feeds, WebSearch, and article fetches work there.
- The **Claude Code web sandbox is egress-allowlisted** (most sites return `403 Host not in allowlist`),
  so live fetching may fail in-session; verify script logic with the smoke test + local HTML fixtures.
- PDF rendering needs DejaVu TTF fonts (present on the runner via `fonts-dejavu-core`; the script also
  honors `CURIOSITY_FONT_DIR`).

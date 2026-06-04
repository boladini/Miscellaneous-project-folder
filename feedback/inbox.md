# Feedback Inbox

Mark articles here, then save the file. On its next run the pipeline applies these
signals (`scripts/apply_feedback.py`), re-weights the relevant source in
`sources.json`, logs them to `pipeline/feedback.json`, and clears the processed lines.

## How to use

One signal per line, in the form: `SIGNAL <article_id>`

| Signal | Meaning                              | Effect                                              |
|--------|--------------------------------------|-----------------------------------------------------|
| `STAR` | This was excellent — more like this  | Increase weight on the source (+0.2)                |
| `MORE` | Go deeper on this topic/author       | Increase weight (+0.3) and queue more like it       |
| `SKIP` | Not interesting                      | Decrease weight on the source (−0.2)                |

`<article_id>` is printed at the top of each article in the daily digest
(`digests/<date>/index.md`), e.g. `2026-06-01/why-emergence-is-everywhere`.
You can also paste the article's URL instead of the id.

Lines starting with `#` are comments and are ignored.

## Pending signals (edit below this line)

<!-- Example (delete these once you add real ones):
STAR 2026-06-01/why-emergence-is-everywhere
MORE 2026-06-01/the-mind-is-not-a-computer
SKIP 2026-06-01/status-games-and-you
-->

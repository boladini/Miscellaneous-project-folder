# The AI Workforce

A team of AI agents, each defined as a markdown job description (JD). Inspired by
the "AI with Allie" playbook: the unit of work is a *job description*, not a
prompt. You talk to the Chief of Staff; he delegates; the org learns and improves
on its own.

## Org chart

```
                 You (Founder / CEO)
                        │
                 ┌──────┴───────┐
                 │  Nick Fury   │   Chief of Staff — single point of contact,
                 │ (nick-fury)  │   delegates & runs the approval queue
                 └──────┬───────┘
            ┌───────────┼───────────────┐
            │           │               │
      ┌─────┴────┐ ┌────┴─────┐   (future directors:
      │  Jarvis  │ │  Oracle  │    Marketing, Owner Relations,
      │ (Ops —   │ │ (memory/ │    Guest Experience, Finance…)
      │ 1st hire)│ │ learning)│
      └────┬─────┘ └──────────┘
           │ coordinates
      ┌────┴─────┐
      │   VAs    │  (your human virtual assistants)
      └──────────┘
```

## The roster

| Agent | Role | Theme | External work? | Status |
|---|---|---|---|---|
| **Nick Fury** | Chief of Staff / orchestrator | Assembles & directs the team | Delegates only | Active |
| **Jarvis** | Operations Coordinator (VA tasks, WhatsApp + PMS watch, SOPs) | Stark's ops AI | Draft-only | **First hire** |
| **Oracle** | Note-taker / memory & self-learning engine | Barbara Gordon, info hub | **None — ever** | Active |

## How it works

1. **You** kick off work by talking to **Nick Fury**.
2. Fury **delegates** to the right agent (today: mostly **Jarvis**).
3. Agents **draft** — nothing sends or mutates a board without your approval.
4. Agents **log** decisions/outcomes to `../memory/`.
5. **Oracle** reviews the log, spots patterns, and **proposes** improvements for
   your approval. The system gets smarter every cycle.

## Conventions

- **Draft-only is absolute** across the whole org.
- **Monday.com is the system of record.** ClickUp is secondary execution.
- Add a new hire by writing one markdown JD: **goal, what good looks like,
  failure criteria, escalation rules, tool access.** Name it after a superhero.
- Memory lives in `../memory/`. Harness/runtime rules live in `../CLAUDE.md`.

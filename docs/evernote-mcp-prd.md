# PRD: Evernote MCP Integration for Claude Code

**Status:** Draft v1.0
**Owner:** boladini@gmail.com
**Last updated:** 2026-06-30

---

## 1. Summary

Connect Claude Code (and other MCP-compatible clients) to a user's Evernote account so the assistant can search, read, create, and update notes in-context — without leaving the editor or copy-pasting between tools.

Because Evernote's official first-party MCP server is still on a waitlist and not generally available, this integration standardizes on the community **`@verygoodplugins/mcp-evernote`** server, which works today and supports both OAuth and a cookie-based auth fallback.

The deliverable is a **documented, reproducible setup** (guide + config template + secret hygiene) committed to the repo, plus a migration path to the official server once it ships.

---

## 2. Goals & Non-Goals

### Goals
- One-command install of the Evernote MCP server into Claude Code.
- Two documented auth paths: OAuth (developer key) and `clipper-sso` cookie fallback.
- A copyable `.mcp.json` template with placeholders — never real secrets in git.
- Secret hygiene by default: `.mcp.json` and token files git-ignored.
- Clear verification steps (`/mcp`) and a known-good first query.
- A documented migration path to the official Evernote MCP server.

### Non-Goals (v1)
- Building or hosting our own Evernote MCP server.
- Automated/headless OAuth in non-interactive or remote sessions (must be done locally).
- Two-way real-time sync or background polling enabled by default.
- Multi-account / team-wide credential provisioning.
- Wrapping or re-exposing Evernote data through a separate API.

---

## 3. Users & Use Cases

| User | Use case |
|---|---|
| Individual developer | "Search my Evernote for the spec I wrote on project-x and summarize it." |
| Note-taker | "Save this generated outline to my 'Drafts' notebook, tagged 'todo'." |
| Researcher | Pull meeting notes into context while drafting code or docs. |
| Knowledge worker | Create, update, and tag notes from chat without switching apps. |

---

## 4. Functional Requirements

### 4.1 Installation
- Add server via a single command:
  `claude mcp add evernote "npx -y -p @verygoodplugins/mcp-evernote -c mcp-evernote"`
- Optional global install (`npm install -g @verygoodplugins/mcp-evernote`).
- No build step required for the `npx` path.

### 4.2 Authentication (pick one)
- **Option A — OAuth:** requires an Evernote developer Consumer Key/Secret. Run `mcp-evernote-auth`; browser flow saves a token to `.evernote-token.json`. In Claude Code: `/mcp` → Evernote → Authenticate.
- **Option B — Cookie:** extract the `clipper-sso` cookie value from evernote.com and supply it as `EVERNOTE_ACCESS_TOKEN`. Required because Evernote no longer issues new developer API keys.

### 4.3 Configuration
- Project-scoped config lives in `.mcp.json` (git-ignored); a placeholder template ships as `.mcp.json.example`.
- All credentials provided via environment variables (see §7).

### 4.4 Capabilities exposed to the assistant
- **Notes:** create, search (Evernote query syntax), get, update, delete.
- **Notebooks:** list, create (with optional stack).
- **Tags:** list, create (with optional parent hierarchy).
- **Account/diagnostics:** user info & quota, revoke auth, health check, reconnect.
- **Polling (opt-in):** start/stop/poll-now/status for change detection.
- Note bodies convert automatically between Markdown and Evernote ENML.

---

## 5. System Architecture

```
┌──────────────┐   stdio (MCP)   ┌────────────────────────┐   HTTPS   ┌──────────────┐
│ Claude Code  │ ───────────────▶│  mcp-evernote server   │ ─────────▶│  Evernote    │
│  (MCP host)  │ ◀─────────────── │ (npx, local process)   │ ◀──────── │  API / Cloud │
└──────────────┘   tools/results └───────────┬────────────┘  notes    └──────────────┘
                                              │ reads
                                   ┌──────────┴───────────┐
                                   │ env: tokens / cookie │
                                   │ .evernote-token.json │
                                   └──────────────────────┘
```

### 5.1 Components
- **MCP host** — Claude Code (or Claude Desktop); launches the server over stdio.
- **mcp-evernote server** — `@verygoodplugins/mcp-evernote` run via `npx`; a local Node process.
- **Auth store** — env vars and/or `.evernote-token.json` written by the OAuth helper.
- **Evernote backend** — production Evernote cloud (no sandbox for cookie auth).

### 5.2 Exposed tools (subset)
| Tool | Input | Output |
|---|---|---|
| `evernote_search_notes` | `{query}` (Evernote syntax) | matching notes |
| `evernote_get_note` | `{guid}` | note title, content, tags |
| `evernote_create_note` | `{title, content, notebook?, tags?}` | created note GUID |
| `evernote_update_note` | `{guid, title?, content?, tags?}` | updated note |
| `evernote_list_notebooks` | `{}` | notebooks[] |
| `evernote_get_user_info` | `{}` | account + quota |

---

## 6. Auth Strategy (CRITICAL)

Evernote has stopped issuing new developer API keys, so the auth path depends on what the user already has.

| Option | Pros | Cons | Risk |
|---|---|---|---|
| **A. OAuth (dev key)** | Cleanest; standard; revocable | Requires pre-existing Consumer Key/Secret | Low |
| **B. clipper-sso cookie** | Works without an API key | Cookie ≈ password; ~1yr expiry; production-only | Medium |
| **C. Official Evernote MCP** | First-party, supported | Not yet available (waitlist) | None |

**Recommendation:** Use Option A if you already hold a developer key; otherwise Option B for now. Migrate to Option C when it ships. In all cases, credentials stay out of git via `.gitignore`.

---

## 7. Non-Functional Requirements

- **Security:** `.mcp.json` and `.evernote-token.json` git-ignored; cookie/token treated as a secret; no credentials in commits, PRs, or logs.
- **Portability:** Works on macOS, Linux, Windows via `npx`; no local build needed.
- **Reproducibility:** A fresh clone + the documented steps yields a working connection.
- **Least privilege:** Polling/webhooks disabled unless explicitly enabled.
- **Operability:** `evernote_health_check` / `evernote_reconnect` for diagnosing connection issues.
- **Maintainability:** Pinned to a named server package; migration path documented.

### 7.1 Environment variables
| Variable | Purpose |
|---|---|
| `EVERNOTE_CONSUMER_KEY` | OAuth consumer identifier (Option A) |
| `EVERNOTE_CONSUMER_SECRET` | OAuth secret credential (Option A) |
| `EVERNOTE_ACCESS_TOKEN` | Access token, or `clipper-sso` cookie value (Option B) |
| `EVERNOTE_ENVIRONMENT` | `production` or `sandbox` |
| `EVERNOTE_NOTESTORE_URL` | API endpoint (auto-fetched with cookie auth) |
| `OAUTH_CALLBACK_PORT` | OAuth redirect port (default: 3000) |
| `EVERNOTE_POLLING_ENABLED` | Enable change polling (default: false) |

---

## 8. Setup Flow

```
1. claude mcp add evernote "npx -y -p @verygoodplugins/mcp-evernote -c mcp-evernote"
2. Authenticate:
     A) mcp-evernote-auth  → browser OAuth → .evernote-token.json
        (or in Claude Code: /mcp → Evernote → Authenticate)
     B) export EVERNOTE_ACCESS_TOKEN="S=s101:U=...:..."   # clipper-sso cookie
3. Verify:  /mcp  → evernote shows "connected"
4. Smoke test:  "Search my Evernote for notes tagged 'project-x'."
```

> Note: The OAuth/browser flow must run in an interactive Claude Code session on your own machine. It cannot be completed in a non-interactive or remote session.

---

## 9. Milestones

| Phase | Deliverable |
|---|---|
| 1 | Setup guide, `.mcp.json.example`, `.gitignore`, README link (this PR). |
| 2 | Local verification: connect via OAuth or cookie; run smoke-test query. |
| 3 | Document common Evernote search recipes and note-creation patterns. |
| 4 | Evaluate opt-in polling / change detection for active notebooks. |
| 5 | Migrate to official Evernote MCP server when it leaves the waitlist. |

---

## 10. Open Questions

1. Do we have an existing Evernote developer Consumer Key (Option A), or default to cookie auth? **Default: cookie.**
2. Project-scoped (`.mcp.json` in repo) vs. user-scoped (global) config? **Default: project-scoped, git-ignored.**
3. Enable change polling for any notebooks in v1? **Default: no.**
4. Whitelist directories for file attachments (`EVERNOTE_ALLOWED_FILE_ROOTS`)? **Default: none.**

---

## 11. Success Metrics

- Connection established and `/mcp` shows `evernote` connected on first local attempt.
- First smoke-test search returns real notes from the account.
- Zero credentials committed to git (verified by `.gitignore` + review).
- Clean migration to the official server when available, with no data loss.

---

## 12. References

- Official Evernote MCP server (waitlist): https://evernote.com/mcp
- `verygoodplugins/mcp-evernote`: https://github.com/verygoodplugins/mcp-evernote
- Setup guide (this repo): docs/evernote-mcp-setup.md

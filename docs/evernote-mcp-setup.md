# Connecting Claude Code to Evernote (MCP)

This guide sets up the [`@verygoodplugins/mcp-evernote`](https://github.com/verygoodplugins/mcp-evernote)
Model Context Protocol (MCP) server so Claude Code can read, search, and create
notes in your Evernote account.

> **Why this server?** Evernote is building an official first-party MCP server,
> but it is still on a [waitlist](https://evernote.com/mcp) and not generally
> available. The `verygoodplugins` community server works today and supports
> both OAuth and a cookie-based fallback (useful because Evernote has stopped
> issuing new developer API keys).

> **Note:** The OAuth/browser auth flow must be run in an **interactive** Claude
> Code session on your own machine. It cannot be completed in a non-interactive
> or remote session.

---

## 1. Install & add the server

The quickest path — one command adds it to Claude Code via `npx` (no global
install needed):

```bash
claude mcp add evernote "npx -y -p @verygoodplugins/mcp-evernote -c mcp-evernote"
```

Or install globally first:

```bash
npm install -g @verygoodplugins/mcp-evernote
claude mcp add evernote "mcp-evernote"
```

---

## 2. Authenticate

Pick **one** of the two methods below.

### Option A — OAuth (recommended, requires a developer API key)

Only works if you already hold an Evernote developer Consumer Key/Secret.
Evernote no longer issues new ones, so if you don't have them, use Option B.

1. Get your Consumer Key and Secret from <https://dev.evernote.com/>.
2. Run the one-time auth helper:
   ```bash
   npx -y -p @verygoodplugins/mcp-evernote mcp-evernote-auth
   ```
   A browser window opens for the OAuth flow; the token is saved to
   `.evernote-token.json`.
3. In an interactive Claude Code session you can also run `/mcp` → select
   **Evernote** → **Authenticate**.

### Option B — Cookie-based (no API key required)

1. Log into <https://www.evernote.com> in your browser.
2. Open DevTools (F12, or ⌘+⌥+I on Mac).
3. Go to **Application → Cookies → www.evernote.com**.
4. Copy the **Value** of the `clipper-sso` cookie. It looks like:
   `S=s101:U=XXX:XXXXX:C=XXXX:P=XXX:A=en-chrome-clipper-xauth-new:V=2:H=XXXXX`
5. Provide it as `EVERNOTE_ACCESS_TOKEN`:
   ```bash
   claude mcp add evernote "npx -y -p @verygoodplugins/mcp-evernote -c mcp-evernote" \
     --env EVERNOTE_ACCESS_TOKEN="S=s101:U=XXX:..."
   ```

**Caveats for cookie auth:**
- The `clipper-sso` token expires after ~1 year, or when you log out of Evernote.
- It authenticates against your **live, production** account — there is no sandbox.
- Treat the cookie value like a password. Never commit it to git (see
  [`.mcp.json.example`](../.mcp.json.example) and `.gitignore`).

---

## 3. Verify

In an interactive Claude Code session:

```
/mcp
```

The `evernote` server should show as connected. Then try asking Claude something
like *"search my Evernote for notes tagged 'project-x'"*.

---

## Configuration reference

A copyable template lives at [`.mcp.json.example`](../.mcp.json.example). Copy it
to `.mcp.json` (already git-ignored) and fill in your own credentials, **or** use
the `claude mcp add --env ...` commands above.

### Environment variables

| Variable | Purpose |
|----------|---------|
| `EVERNOTE_CONSUMER_KEY` | OAuth consumer identifier (Option A) |
| `EVERNOTE_CONSUMER_SECRET` | OAuth secret credential (Option A) |
| `EVERNOTE_ACCESS_TOKEN` | Access token, or `clipper-sso` cookie value (Option B) |
| `EVERNOTE_ENVIRONMENT` | `production` or `sandbox` |
| `EVERNOTE_NOTESTORE_URL` | API endpoint (auto-fetched with cookie auth) |
| `OAUTH_CALLBACK_PORT` | OAuth redirect port (default: 3000) |
| `EVERNOTE_POLLING_ENABLED` | Enable change polling (default: false) |
| `EVERNOTE_POLL_INTERVAL` | Poll frequency in ms (min: 900000) |
| `EVERNOTE_WEBHOOK_URL` | Webhook endpoint for change notifications |
| `EVERNOTE_ALLOWED_FILE_ROOTS` | Permitted directories for file attachments |

---

## Available tools

**Notes:** `evernote_create_note`, `evernote_search_notes`, `evernote_get_note`,
`evernote_update_note`, `evernote_delete_note`

**Notebooks:** `evernote_list_notebooks`, `evernote_create_notebook`

**Tags:** `evernote_list_tags`, `evernote_create_tag`

**Account & diagnostics:** `evernote_get_user_info`, `evernote_revoke_auth`,
`evernote_health_check`, `evernote_reconnect`

**Polling / change detection:** `evernote_start_polling`, `evernote_stop_polling`,
`evernote_poll_now`, `evernote_polling_status`

Search supports Evernote's native query syntax, and note bodies are converted
automatically between Markdown and Evernote's ENML format.

---

## Sources

- [Official Evernote MCP server (waitlist)](https://evernote.com/mcp)
- [`verygoodplugins/mcp-evernote` on GitHub](https://github.com/verygoodplugins/mcp-evernote)

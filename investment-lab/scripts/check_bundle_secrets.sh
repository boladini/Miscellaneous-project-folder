#!/usr/bin/env sh
# Fails if the built client bundle contains key-like strings or the VALUE of any configured server secret.
# (Env-var *names* appear in help text and are not secrets.)
set -eu
DIR="${1:-backend/static}"
if grep -rEl "sk-ant-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY" "$DIR"; then
  echo "key-like string found in client bundle"; exit 1
fi
for v in "${ANTHROPIC_API_KEY:-}" "${TIINGO_API_KEY:-}" "${ALPHAVANTAGE_API_KEY:-}" "${SECRET_KEY:-}" "${SMTP_PASSWORD:-}" \
         "${CRON_SECRET:-}" "${X_BEARER_TOKEN:-}" "${REDDIT_CLIENT_SECRET:-}" "${ADMIN_PASSWORD:-}"; do
  if [ -n "$v" ] && [ "${#v}" -ge 6 ] && grep -rFl -- "$v" "$DIR" >/dev/null; then
    echo "a configured secret value appears in the client bundle"; exit 1
  fi
done
echo "bundle clean: no key-like strings or configured secret values in $DIR"

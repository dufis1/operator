# Empirical MCP auth-error capture (session 148 / Phase 15.7.1a)

Reference for the substring list in `pipeline/mcp_client.py :: _AUTH_ERROR_PATTERNS`.
Re-run `tests/probe_auth_errors.py` whenever a new MCP server is added to
the bundle (15.7.5 brings five more), append results below, and extend
the pattern list if the captured error uses new phrasing.

## Method

Each server is booted with a deliberately-bogus credential via stdio and
a single read tool is called. The MCP response (`content[].text` when
`isError=True`, otherwise the raised exception) is captured verbatim.

Run: `python tests/probe_auth_errors.py` (writes `/tmp/mcp_auth_error_capture.txt`).

## Captured errors

### GitHub — `./github-mcp-server stdio`
- Bad `GITHUB_PERSONAL_ACCESS_TOKEN` → tool call returns `isError=True` with:
  ```
  failed to get user: GET https://api.github.com/user: 401 Bad credentials []
  ```
- Sniff keys present: `" 401 "` and `"bad credentials"`.

### Figma — `npx -y figma-developer-mcp@0.10.1 --stdio` (GLips community server)
- Bad `FIGMA_API_KEY` → tool call returns `isError=True` with:
  ```
  Error fetching file: Figma API returned 403 Forbidden for '/files/xxx'.
  ```
- Sniff keys present: `" 403 "` and `"forbidden"`.

### Linear — `npx -y mcp-remote https://mcp.linear.app/sse`
- Empty cache dir → stdio `initialize()` stalls indefinitely while
  mcp-remote opens a browser for OAuth. User clicking **Cancel** produces
  the same stall (no auth error surfaces to the MCP channel).
- **No sniffable error string exists.** The runtime sniff is a no-op for
  OAuth-over-mcp-remote servers. Detect these via cache-path inspection
  (Phase 15.7.3) instead.

## Deferred — pattern-only (not empirically captured yet)

These servers are not yet bundled (pending 15.7.5) but their documented
auth-error codes are already in `_AUTH_ERROR_PATTERNS` so runtime sniff
works out of the box when they land:

- **Slack** — `invalid_auth`, `not_authed`, `token_expired`, `token revoked`
- **Sentry** — `invalid token`, `401`
- **Salesforce** — `INVALID_SESSION_ID`, `401`
- **Google APIs (Calendar/Drive/Gmail)** — `UNAUTHENTICATED`, `invalid_grant`; all are OAuth-over-mcp-remote so the 15.7.3 cache check is the primary signal, not the sniff
- **Generic HTTP** — `401`, `403`, `unauthorized`, `forbidden`, `authentication required/failed`, `invalid api key`

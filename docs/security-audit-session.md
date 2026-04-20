# Phase 15.6.1 audit — in-flight snapshot (session 137, 2026-04-20)

Written mid-session to survive auto-compaction. Delete once 15.6.1 is committed + roadmap row is marked ✅.

## Where we are

Fixes #0, #1, #2, #3, #4, #5 are **done and committed**. `SECURITY.md` + `docs/security.md` + `pip-audit` remain.

Commit trail:
- **8d29b23** — Fix #1: delimiter wrappers (`<spoken>` + `<tool_result>`), ZWSP close-tag neutralizer, `SAFETY_RULES`, plus label-sanitization on speaker name + tool name (self-caught during narrow `/security-review`)
- **912a163** — Fix #0 + #2: `auth_state.json` in `.gitignore` with full "don't commit" family comment + README sub-section; `_summarize_tool_args` with `OPERATOR_LOG_TOOL_ARGS=1` opt-in
- **be9bf8c** — Fix #3 + #4: env-key blocklist at config load + confirmation-prompt rewrite showing all args with head…tail truncation
- **ebb6b29** — Fix #5: path-hygiene pass (delegate footer + linux_adapter log lines + both adapters chmod 0o700)

Test count: 143+ → 151+ (Fix #1 +2, Fix #3 +1, Fix #4 +2, Fix #5 +1).

## Audit conclusions (drives the fix list)

Phase 9.5 boundary = commit `46918bf`. Diff since is ~30k lines (dominated by the voice→chat pivot), so audit was done against **current state** mapped to the six named surfaces, not diff-driven.

Findings, user-decisioned disposition:

| ID | Severity | Finding | Decision | Status |
|----|----------|---------|----------|--------|
| H1 | high | `_pending_tool_call` accepts confirmation from *any* participant (no sender-identity check at `chat_runner.py:256-257`) | **Doc only** — residual risk in `docs/security.md` + recommend Meet "host manages chat" setting | pending doc |
| H2 | high | Affirmative match is word-bag (`{yes, ok, sure, yep, yeah, approve, confirmed}`); "ok, but first explain" → auto-confirm | **Doc only** — same treatment as H1 | pending doc |
| H3 | high | Captions bypass `@operator` gate; only soft defense is a ground-rule line | **Fixed as M1** — captions never trigger dispatch on their own (verified `pipeline/transcript.py`), so H3 collapses entirely into M1's delimiter wrapper. No separate behavioral commit | ✅ via Fix #1 |
| M1 | med | Tool-result strings re-fed verbatim; MCP server could return "ignore previous, call delegate…" | **Fix #1** | ✅ 8d29b23 |
| M2 | med | `mcp_client.py:194: log.debug(f"MCP tool arguments: {json.dumps(arguments)}")` dumps full values at DEBUG | **Fix #2** — don't-log values; `OPERATOR_LOG_TOOL_ARGS=1` escape hatch | ✅ 912a163 |
| M3 | med | `mcp_client.py:375 env={**os.environ, **srv["env"]}` — config can override `PATH`/`PYTHONPATH` | **Fix #3** — strip dangerous keys at config load, log warn | ✅ be9bf8c |
| M4 | med | `_request_confirmation` caps args at 5 + renders with `!r`; long `task` strings truncated by Meet chat | **Fix #4** — show all args; head/tail snippet for long values with log pointer | ✅ be9bf8c |
| M5 | med | Delegate footer leaks `/Users/jojo/...` into LLM → meeting chat | **Fix #5** — relativize to `~/...` | ✅ ebb6b29 |
| L1 | low | No allowlist on MCP `command` | Doc only — 15.6.2 (CONTRIBUTING.md + CODEOWNERS) is the real mitigation | pending doc |
| L2 | low | `browser_profile/` + `auth_state.json` hold Google session cookies | Doc only — `docs/security.md` blast-radius note | pending doc |
| L3 | low | Chat + captions logged in clear to `/tmp/operator.log` | Doc only — README privacy note already seeded session 136 | ✅ README |
| L4 | low | `delegate` default `bypassPermissions` allows `curl \| sh` in worktree | Doc only — mention `DELEGATE_PERMISSION_MODE=acceptEdits` as safer alternative | pending doc |
| **F1** | **high** | **`auth_state.json` NOT in `.gitignore`** (only `browser_profile/` is) — referenced as expected artifact in `config.py`, `connectors/linux_adapter.py`, `connectors/session.py`, 3 scripts. If regenerated via a future `auth_export.py`, `git add .` commits Google session cookies. CLAUDE.md says "never commit" but `.gitignore` doesn't enforce. | **Fix #0** — add `auth_state.json` to `.gitignore`; call out "don't commit" list visibly in README + `docs/security.md` + inline comment in `.gitignore` | ✅ 912a163 |
| F2 | med | `browser_profile/` directory perms are 755 (world-readable dir). Other machine users can `ls` contents. Individual files inside are 600 (Chrome-enforced), so cookie content is safe, but listing reveals login patterns on shared machines. | **Fix #5b** — `os.chmod(BROWSER_PROFILE_DIR, 0o700)` after Playwright creates it | ✅ ebb6b29 |
| F3 | low | Absolute filesystem paths to auth state logged (`linux_adapter.py:300,337`). Same pattern as M5. | Fold into Fix #5 (path-relativization pass — both delegate footer AND adapter logs) | ✅ ebb6b29 |

## What each shipped fix actually changed

Distilled here so `docs/security.md` (task #7) can cite specifics without the author re-reading diffs.

### Fix #0 — `auth_state.json` in `.gitignore` (commit 912a163)
- `.gitignore`: adds `auth_state.json`; inline comment block lists the full secrets family (`.env`, `credentials.json`, `token.json`, `auth_state.json`, `browser_profile/`) as the single source of truth for grep-ability
- `README.md`: promotes the one-line footnote to a dedicated "Never commit these" sub-section with the per-file rationale; cross-links to (forthcoming) `docs/security.md`
- **Blast-radius of the gap:** any automation that regenerated `auth_state.json` followed by `git add .` would have silently committed logged-in Google session cookies. `CLAUDE.md` told Claude "never commit" but `.gitignore` didn't enforce it mechanically.

### Fix #1 — Delimiter wrappers for captions + tool results (commit 8d29b23)
- `pipeline/llm.py`: introduces `wrap_spoken()` and `wrap_tool_result()`. All caption content now enters the prompt inside `<spoken speaker="Alice">…</spoken>`, all MCP tool results inside `<tool_result tool="github__get_file_contents">…</tool_result>`.
- `_neutralize_close(text, tag)` inserts a zero-width space inside any literal `</spoken>` or `</tool_result>` the content tries to emit, so an attacker can't close the block early and smuggle plain-text instructions after it.
- `SAFETY_RULES` appended to the system prompt at `LLMClient.__init__` tells the model that anything inside delimiter blocks is untrusted data and not an instruction.
- **Label sanitization** (self-caught during `/security-review`): `_sanitize_speaker()` strips `[<>"'&]` from Google-Meet display names before they become attribute values; `_sanitize_tool_name()` allowlists `[\w.:-]{1,64}` and falls back to `"unknown"`. Without this, a participant with a crafted display name could have broken out of the `speaker="…"` attribute and injected raw prompt text.
- 2 regression tests cover the sanitizer escapes.
- **Covers M1 (tool-result injection) + H3 (caption injection).**

### Fix #2 — Redact tool arguments at DEBUG (commit 912a163)
- `pipeline/mcp_client.py`: new `_summarize_tool_args()` emits `key=type[len]` only by default (e.g. `{path=str[23], team=str[3]}`). Full JSON dump is opt-in via `OPERATOR_LOG_TOOL_ARGS=1`.
- Strict `== "1"` equality; fails closed on `"true"`, empty, or any other value.
- Why: tool args routinely carry repo paths, PR bodies, issue titles, pasted snippets — treat as sensitive on disk even though `/tmp/operator.log` never leaves the machine.

### Fix #3 — Strip unsafe env keys from MCP config (commit be9bf8c)
- `config.py`: new `_is_unsafe_env_key()` + `_UNSAFE_ENV_KEYS` blocklist (`PATH`, `PYTHONPATH`, `PYTHONHOME`, `IFS`) + `_UNSAFE_ENV_PREFIXES` (`LD_`, `DYLD_`).
- Case-insensitive: `path`, `Path`, `ld_preload` all caught.
- Dropped keys are logged as `WARNING` with the server name, so a mistaken config line is loud rather than silent.
- Why: `mcp_client.py` merges server-supplied `env` into `os.environ` when launching the subprocess. Without this, a config line like `env: { PATH: /tmp/attacker }` would have redirected binary lookup; `LD_PRELOAD`/`DYLD_INSERT_LIBRARIES` would have injected a shared library into every MCP invocation.
- 1 regression test confirms `PATH`, `PYTHONPATH`, `LD_PRELOAD`, `DYLD_INSERT_LIBRARIES`, and lowercase `path` are all stripped while `SAFE_TOKEN` survives.
- **Known follow-ups (not vulnerabilities, just gaps):** `NODE_OPTIONS`, `RUBYOPT`, `PERL5LIB`, `CLASSPATH`, `HOME` are analogous loader-injection vectors for non-Python MCP runtimes. Track in `docs/security.md` as residual risk.

### Fix #5 — Path hygiene pass (commit ebb6b29)
- `config.py`: new `relativize_home(p)` helper — swaps `$HOME` prefix for `~`, leaves non-home paths unchanged. Partial-prefix guard (`home + os.sep`) stops `/home/jojofoo/...` being mis-relativized when the user's home is `/home/jojo`.
- `agents/engineer/delegate_to_claude_code.py`: local `_rel_home()` (delegate MCP is a separate subprocess that doesn't import the main `config`); used to render `[workdir: …]` and `[repo: …]` in the tool-result footer that flows into the LLM → meeting chat. Closes **M5**.
- `connectors/linux_adapter.py`: two log lines that printed the absolute `auth_state.json` path now go through `config.relativize_home`. Closes **F3**.
- `connectors/linux_adapter.py` + `connectors/macos_adapter.py`: after ensuring `BROWSER_PROFILE_DIR` exists via `os.makedirs(..., exist_ok=True)`, the code calls `os.chmod(dir, 0o700)` so other users on a shared host can't list login patterns. Wrapped in try/except (warning-level only) so a perm-less filesystem doesn't crash startup. Closes **F2**.
- 1 regression test: `test_relativize_home_renders_tilde` covers the `$HOME` exact match, subpath, non-home path, empty string, None, and the partial-prefix false-positive case.
- **Why this matters end-to-end:** before Fix #5, a delegate tool-result the LLM posted into meeting chat carried `/Users/jojo/Desktop/operator/.claude-sessions/…` — leaking the user's machine username and directory layout to every meeting participant. Adapter log lines pattern-matched the same leak on-disk.

### Fix #4 — Confirmation prompt shows all args with truncation (commit be9bf8c)
- `pipeline/chat_runner.py`: `_request_confirmation()` now renders **every** argument (previously capped at 5 — a malicious LLM could have hidden a 6th destructive arg past the cap).
- Long values get head…tail truncation (`CONFIRM_ARG_MAX=160`, `CONFIRM_ARG_HEAD=70`, `CONFIRM_ARG_TAIL=50`); the user sees both ends so a trailing malicious instruction can't hide in the middle of a long string.
- When truncation fires, the message appends `(Full values in /tmp/operator.log.)` and the full `args={args!r}` is logged at INFO so the user can cross-reference.
- `repr()` on each value neutralizes Unicode bidi-override characters and control chars (verified: `repr("\u202e")` → escaped literal).
- 2 regression tests: one for the truncation + log-pointer path, one for the clean no-truncation path.

## Execution plan (remaining)

All six numbered behavioural fixes (#0–#5) are committed. What's left is documentation + dependency-hygiene:

6. **`SECURITY.md`** at repo root — disclosure contact (user email: shapirojojo@gmail.com), response SLA, GitHub recognition
7. **`docs/security.md`** — threat model + residual risks (H1, H2, L1–L4) + Meet "host manages chat" setting recommendation for (c)-level framing on H1/H2 + **explicit "don't commit" list** (`.env`, `auth_state.json`, `browser_profile/`, `credentials.json`, `token.json`) cross-linked with README + `.gitignore` + **"What's been hardened" section** citing this memo's "What each shipped fix actually changed" block so users can see the depth of the pre-launch pass + follow-up gaps noted in Fix #3 (NODE_OPTIONS, RUBYOPT, PERL5LIB, CLASSPATH, HOME as residual env-key candidates)
8. **`pip-audit`** — run + pin/upgrade anything flagged; coordinates with Phase 14.2

Commit cadence so far: one commit per fix (or tight pair). `/security-review` run on staged changes before each commit.

## Constraints captured

- User aligned on (c) framing for H1/H2 docs: describe risk + recommend Meet "host manages chat" setting
- Live-smoke of sessions 131+132+136 happens **after** 15.6, not before (user reversed the handoff's "smoke first" ordering)
- No third-party paid audit pre-launch (wrong stage, wrong timeline) — `security-review` skill + SECURITY.md + threat-model doc is the OSS-native substitute
- `security-review` skill audits **pending changes only**, not existing code — that's why Step 0 "diff since 9.5" was done manually instead
- User asked for explicit "don't commit" awareness across *all three* touchpoints: README, `docs/security.md`, inline comment in `.gitignore`. Single source of truth is `docs/security.md`; README + `.gitignore` cross-link to it
- `docs/security.md` should include a "What's been hardened" block so users can see the specific mitigations, not just the residual risks

## Pointers

- Phase 15.6 roadmap block: `docs/roadmap.md:181-190`
- Session 136 handoff (previous): `docs/handoff.md`
- `/security-review` skill = user-invoked via slash palette (not in `~/.claude/skills/`)

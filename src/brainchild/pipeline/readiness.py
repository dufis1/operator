"""MCP readiness check — Phase 15.7.4.

One helper that combines env-var presence (15.7.1's `missing_vars`),
OAuth token-cache presence (15.7.3's `oauth_cache_exists`), and
claude-code-specific prereqs (git binary + claude CLI + `claude auth
status`) into a per-server status dict. The wizard's status screen and
the runtime pre-flight (15.7.4.5) both consume this.

Shape of the returned record per server:

    {
        "status":        "ok" | "missing_env" | "oauth_needed" | "prereq_missing",
        "fix":           str,                 # one-line user-facing hint
        "fix_url":       str | None,          # remediation URL, if applicable
        "missing_vars":  list[str],           # only for missing_env
        "auth_url":      str | None,          # only for oauth_needed
    }

The helper takes `mcp_servers: dict[str, dict]` — the raw-YAML shape
(wizard hands it `state.bot_cfg["mcp_servers"]`; runtime hands it
`config.MCP_SERVERS`). It reads `enabled`, `auth`, `auth_url`, `env`,
and `credentials_url` — all optional except `env` (missing = no env
vars needed). It does *not* mutate the input.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from dotenv import load_dotenv

from brainchild.pipeline.oauth_cache import oauth_cache_exists

# Ensure repo-root .env is in os.environ before we inspect it. Idempotent
# and override=False, so runtime pre-flight (where config.py already
# called this) is a no-op, and wizard calls (which import readiness
# before any config import) see the same secrets the runtime would.
load_dotenv(override=False)

_ENV_REF_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

# How long to wait on `claude auth status --json` before giving up. The
# subcommand is local-only (no network) and normally returns in <300 ms;
# 5 s is generous slack for a cold `node` warm-up on slow disks.
_CLAUDE_AUTH_TIMEOUT = 5.0


def _missing_env_vars(env_dict: dict | None) -> list[str]:
    """Return env var names referenced as ${VAR} in `env_dict` that are
    absent or empty in os.environ.

    Mirrors config._resolve_env_vars's missingness logic so the wizard
    gets the same answer without having to run the full config loader.
    Both the raw-YAML shape (values like "${GITHUB_TOKEN}") and the
    already-resolved shape (plain strings, `missing_vars` populated)
    are handled — if the server block carries a pre-computed
    `missing_vars` list, we trust it.
    """
    if not env_dict:
        return []
    missing: list[str] = []
    seen: set[str] = set()
    for v in env_dict.values():
        if not isinstance(v, str):
            continue
        for var in _ENV_REF_RE.findall(v):
            if var in seen:
                continue
            seen.add(var)
            if not os.environ.get(var):
                missing.append(var)
    return missing


def _probe_claude_code(*, check_auth: bool = True) -> tuple[str, str]:
    """Check claude-code prereqs. Returns (status, detail).

    status:
      "ok"              — git + claude both on PATH, and (if check_auth)
                          `claude auth status --json` reports loggedIn=true.
      "prereq_missing"  — something's missing; detail names what.

    `check_auth=False` is the knob for callers that want to skip the
    ~300 ms subprocess hop during routine wizard use (we keep it on by
    default — stale login is the whole point of 15.7.4's gate).
    """
    if shutil.which("git") is None:
        return "prereq_missing", "git CLI not on PATH — install git first"
    if shutil.which("claude") is None:
        return "prereq_missing", "claude CLI not on PATH — install Claude Code first"
    if not check_auth:
        return "ok", ""
    try:
        r = subprocess.run(
            ["claude", "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=_CLAUDE_AUTH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "prereq_missing", (
            f"`claude auth status` did not respond within {_CLAUDE_AUTH_TIMEOUT:.0f}s "
            f"— try running it manually and sign in"
        )
    except OSError as e:
        return "prereq_missing", f"could not run `claude auth status`: {e}"
    if r.returncode != 0:
        return "prereq_missing", "`claude auth status` exited non-zero — run `claude auth login`"
    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError:
        return "prereq_missing", "`claude auth status` returned unparseable output — run `claude auth login`"
    if not payload.get("loggedIn"):
        return "prereq_missing", "not logged in — run `claude auth login`"
    return "ok", ""


def report_mcp_readiness(
    mcp_servers: dict[str, dict],
    *,
    enabled_only: bool = True,
    check_claude_code_auth: bool = True,
) -> dict[str, dict]:
    """Return per-server readiness records.

    Only inspects servers with `enabled: True` unless enabled_only=False
    — dormant blocks in the full-union scaffold would otherwise spam the
    status screen with noise the user doesn't care about.

    Priority of checks per server:
      1. claude-code → binary + login probe (15.7.4's prereq gate).
      2. auth=="oauth" → OAuth token-cache path exists.
      3. default (auth=="env" or absent) → every ${VAR} in env is set.

    All three can degrade to "prereq_missing" for the claude-code case;
    the OAuth branch returns "oauth_needed"; the env branch returns
    "missing_env" with a `missing_vars` list callers can surface by name.
    """
    out: dict[str, dict] = {}
    for name, srv in mcp_servers.items():
        if enabled_only and not srv.get("enabled", True):
            continue

        credentials_url = srv.get("credentials_url") or None

        if name == "claude-code":
            status, detail = _probe_claude_code(check_auth=check_claude_code_auth)
            if status == "ok":
                out[name] = {"status": "ok", "fix": "", "fix_url": None}
            else:
                out[name] = {
                    "status": "prereq_missing",
                    "fix": detail,
                    "fix_url": credentials_url,
                }
            continue

        if srv.get("auth") == "oauth":
            auth_url = srv.get("auth_url") or ""
            if oauth_cache_exists(auth_url):
                out[name] = {"status": "ok", "fix": "", "fix_url": None}
            else:
                out[name] = {
                    "status": "oauth_needed",
                    "fix": f"run `brainchild auth {name}` once to authorize",
                    "fix_url": credentials_url,
                    "auth_url": auth_url,
                }
            continue

        # Default: env-authed server. Prefer the pre-resolved missing_vars
        # list (populated by config._resolve_env_vars) when available so
        # wizard and runtime agree on what's missing.
        pre = srv.get("missing_vars")
        if isinstance(pre, list):
            missing = list(pre)
        else:
            missing = _missing_env_vars(srv.get("env"))

        if missing:
            out[name] = {
                "status": "missing_env",
                "fix": f"set {', '.join(missing)} in .env",
                "fix_url": credentials_url,
                "missing_vars": missing,
            }
        else:
            out[name] = {"status": "ok", "fix": "", "fix_url": None}

    return out


# Status → unicode glyph used by the wizard status screen + runtime pre-flight.
# Keeping the mapping here (vs in setup.py) so both callers render identically.
STATUS_GLYPH = {
    "ok": "✓",
    "missing_env": "✗",
    "oauth_needed": "⚠",
    "prereq_missing": "✗",
}

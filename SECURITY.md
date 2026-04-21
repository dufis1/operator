# Security

Thanks for taking the time to report a security issue in Brainchild.

## Reporting a vulnerability

Email **shapirojojo@gmail.com** with:

- A description of the issue and its impact.
- Steps to reproduce (ideally a minimal agent config or chat transcript).
- The commit hash or release you tested against.

Please **do not** open a public GitHub issue for security-sensitive reports.
Use a GitHub Security Advisory (Security → Advisories → New draft advisory)
if you prefer GitHub's flow over email.

## Response SLA

- **Acknowledgement** within 72 hours.
- **Triage and initial assessment** within 7 days.
- **Fix or mitigation plan** within 30 days for high/critical issues; lower
  severity may be batched with a regular release.

If I go longer than 72 hours without acknowledging, assume the email was
missed and nudge the same address.

## Recognition

Reporters who follow coordinated disclosure are credited by name (or handle,
your preference) in the release notes and GitHub Security Advisory that ships
the fix. No bug bounty — this is a solo open-source project.

## Scope

In scope:

- Code in this repository (`brainchild` CLI, connectors, pipeline, agents).
- Default agent configs shipped under `agents/`.

Out of scope:

- Issues in upstream dependencies — report those to the dependency owner.
  Brainchild's own pinned versions are tracked via `pip-audit`; see
  `docs/security.md`.
- Google Meet itself, or Meet's chat/participant controls.
- Third-party MCP servers invoked via user-supplied configs.

## Threat model and hardening

`docs/security.md` documents the threat model, known residual risks, and the
mitigations already in place. Read it before filing a report — the issue you
are seeing may be a known, documented residual risk with a recommended
operational workaround (e.g. Meet's "host manages chat" setting).

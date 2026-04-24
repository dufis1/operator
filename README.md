# Brainchild

Chat-based AI meeting participant for Google Meet. Joins, reads chat, replies
via an LLM with tool access (Linear, GitHub, and other MCP servers you wire
up), and leaves when everyone else does.

```bash
brainchild run pm                                        # open a fresh Meet
brainchild run pm https://meet.google.com/xxx-yyyy-zzz   # join a specific Meet
brainchild try pm                                        # terminal test-drive, no Meet
brainchild                                               # show available agents
```

`pm` is a sample bot under `agents/`. Drop in `brainchild setup` to create your own.

## Privacy & logs

Brainchild writes a detailed diagnostic log to **`/tmp/brainchild.log`** on every
run. For now, this file contains:

- The Meet URL the bot joined (a capability token — anyone with it can join).
- Chat messages the bot sees, including sender names.
- LLM prompt/response metadata and tool call arguments + results.
- Captions, when `transcript.captions_enabled: true` in the agent config.

**The file never leaves your machine**, but it is plain text in a shared
directory — treat it like any other local artifact. macOS typically clears
`/tmp` on reboot; Linux may not. Delete it manually if it matters.

Chat history also lands in `~/.brainchild/history/<slug>.jsonl` — that's the
durable record the bot replays from between turns. Same sensitivity profile.

### Never commit these

API keys live in a single `.env` at the repo root. The following files hold
secrets or logged-in Google session state and must stay local:

- `.env` — API keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, GITHUB_TOKEN, …)
- `credentials.json` — Google OAuth client secrets
- `token.json` — Google OAuth access/refresh tokens
- `~/.brainchild/auth_state.json` — Playwright storage state (Google session cookies)
- `~/.brainchild/browser_profile/` — persistent Chrome profile (Google session cookies)

All of the above are ignored by `.gitignore`. If you see one show up in
`git status` untracked, something has gone wrong — don't `git add .` blindly.
See `docs/security.md` for the full threat model.

## Uninstall

```bash
uv tool uninstall brainchild   # removes the CLI + PATH shim
rm -rf ~/.brainchild           # removes agents, history, and .env
```

## More

- `CLAUDE.md` — architecture, commands, configuration layout.
- `docs/roadmap.md` — phase plan.
- `docs/agent-context.md` — current development state.

# Operator

Chat-based AI meeting participant for Google Meet. Joins, reads chat, replies
via an LLM with tool access (Linear, GitHub, and other MCP servers you wire
up), and leaves when everyone else does.

```bash
operator pm                                        # open a fresh Meet
operator pm https://meet.google.com/xxx-yyyy-zzz   # join a specific Meet
operator try pm                                    # terminal test-drive, no Meet
operator list                                      # show available agents
```

`pm` is a sample bot under `agents/`. Drop in `operator setup` to create your own.

## Privacy & logs

Operator writes a detailed diagnostic log to **`/tmp/operator.log`** on every
run. For now, this file contains:

- The Meet URL the bot joined (a capability token — anyone with it can join).
- Chat messages the bot sees, including sender names.
- LLM prompt/response metadata and tool call arguments + results.
- Captions, when `transcript.captions_enabled: true` in the agent config.

**The file never leaves your machine**, but it is plain text in a shared
directory — treat it like any other local artifact. macOS typically clears
`/tmp` on reboot; Linux may not. Delete it manually if it matters.

Chat history also lands in `~/.operator/history/<slug>.jsonl` — that's the
durable record the bot replays from between turns. Same sensitivity profile.

API keys live in a single `.env` at the repo root. Never commit `.env`,
`browser_profile/`, or `auth_state.json`.

## More

- `CLAUDE.md` — architecture, commands, configuration layout.
- `docs/roadmap.md` — phase plan.
- `docs/agent-context.md` — current development state.

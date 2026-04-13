# Operator MVP Bar

*Established: April 12, 2026. Revisit and adjust as needed, but don't expand without a strong reason.*

---

## Positioning

The only open-source meeting bot that *does things*, not just watches. **"Meetings that produce artifacts, not just words."**

## The "Aha" Moment

You're in a Google Meet. You type "operator, create a ticket for the auth bug Alice just described." It creates the Linear ticket, right there in chat, in front of everyone. Nobody else does this.

## Three-Part Differentiation

1. **Action, not transcription.** OpenUtter/Vexa/Fireflies watch and record. Operator executes. MCP tool use during a live meeting is the capability moat.
2. **Your AI, not a generic bot.** Customizable system prompt, skill files, and model choice (OpenAI or Anthropic). This is what separates it from Gemini/Siri — it knows your codebase conventions, your team's ticket format, your preferences. The customization layer that the AI-power-user community is already practicing, surfaced into meetings.
3. **Standalone and simple.** No OpenClaw infrastructure dependency, no managed platform, no $20/mo SaaS. Clone, configure, run. Open source, MIT licensed.

## What Must Work

- **Fresh install works in one sitting.** Clone, follow README, configure API keys, run, join a meeting. No wizard needed — the audience can follow a README — but the README must be complete and accurate.
- **Chat interaction in Google Meet.** Bot reads messages, responds intelligently, stays in character per the user's system prompt / skill files.
- **MCP tool use with Linear and GitHub.** These two are pressure-tested and work reliably. Known failure modes are handled gracefully.
- **Model choice: OpenAI + Anthropic.** Users pick their provider. No local model support at MVP — local models are too weak at agentic tool use and would undermine the demo.
- **Customization layer.** Users can provide a system prompt and/or skill files so the bot reflects their identity, preferences, and context. This is what makes it "your AI" and not Gemini.
- **Graceful failure.** When something goes wrong, the bot says so in chat ("I tried but Linear returned an error: ...") rather than hanging, crashing, or silently doing nothing.
- **BYOMCP doesn't brick the bot.** Users can add their own MCP servers. Untested servers get defensive defaults (result size caps, execution timeouts, conservative confirmation mode). README documents known failure patterns and how to write hints to mitigate them.
- **Linux works.** Mac + Linux is in the positioning. Dedicated testing session on a real Linux box before launch.
- **Clean presentation.** Stdout during normal operation is clean and readable (no debug spam, no stack traces for expected errors). Config file has no dead keys or confusing options. The detailed debug log stays in `/tmp/operator.log`.

## What Is NOT in the MVP

- **Voice** — massive surface area, layered post-launch
- **Local LLM support** — too weak at tool use, would create bad first impressions; add as experimental tier later
- **Setup wizard** — README is fine for this technical audience
- **More than 2 fully-tested MCP servers** — Linear + GitHub ship tested; others can be added by users with documented guidance
- **Zoom/Teams** — Google Meet only at launch; other platforms are a fast follow
- **Comprehensive edge case testing, DOM self-healing, regression suites**
- **Telemetry / diagnostics**
- **CI/CD pipeline, PyPI publishing**
- **Latency audit** — chat doesn't have voice-level latency sensitivity
- **Comprehensive error handling pass** — defensive MCP guardrails cover the scariest path

## The Reddit Test

A technical user clones it, follows the README, gets it into a meeting, asks it to do something with Linear or GitHub, and it works. They think "oh that's cool, I could customize this and hook up my own tools." They don't hit a crash. They see it's model-agnostic. That's a win.

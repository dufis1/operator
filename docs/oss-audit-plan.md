# Open-Source Audit Plan — Session 91

Produced during alignment before running a layer-by-layer audit of Operator's codebase against its OSS ethos. Ethos lives in memory: `project_oss_ethos.md`.

## Goal

Identify every place the current codebase is too opinionated / too closed for an OSS project. Produce a prioritized punch list, **not fixes**. User will decide what to address and in what order.

## Scope

**In scope:** Everything that runs in chat mode (the MVP).

**Explicitly out of scope (do NOT flag these):**
- Audio / voice layer (`pipeline/audio.py`, `pipeline/wake.py`, `pipeline/tts.py`, Swift `audio_capture`, connector audio plumbing). The user is auditing this separately. The code should look as if audio doesn't exist.
- Multi-tenant / hosted service concerns.
- Non-English support (roadmap).

## Ethos recap (audit criteria)

**Core (must NOT be overridable):**
- Agentic LLM loop is the product
- Zero telemetry
- No user data on our infra

**Default (must be overridable):**
- Wake phrase, system prompt, tools/skills/MCP servers, participation degree, STT/TTS/LLM provider, conversation storage, response style, logging destination

**Shape commitments:**
- Provider-neutral internal shapes; translate at the edge
- Swappable seams: LLM, tool protocol, conversation store, chat surface
- Headless core + chat API + reference CLI client is the target surface architecture
- Skills: manifest + install consent + sensitive-tier confirmation
- Config layered: YAML → env → CLI flags → per-skill overrides
- MCP-only tool protocol is fine, but `MCPTool` should not be the in-code neutral shape

## Audit layers

Run in this order. For each layer answer:
- **(a)** What is hardcoded?
- **(b)** What is configurable today?
- **(c)** What *should* be pluggable but isn't?
- **(d)** Severity: **critical** (blocks ethos), **major** (awkward but workable), **minor** (polish)

1. **Entry & app layer** — `__main__.py`, `app.py`. Startup-path opinions, platform assumptions (bearing in mind audio is scoped out, so skip macOS-rumps-specific audio paths).
2. **Connector interface** — `connectors/base.py` and implementations. Is the contract clean, or does it assume Meet specifics? (Audio capture methods exist but are out of scope — only flag if they leak into chat-mode code paths.)
3. **Pipeline / runner (chat mode path only)** — `pipeline/runner.py`, `pipeline/conversation.py`. How does chat mode flow, and is anything audio-shaped leaking into it?
4. **LLM provider boundary** — `pipeline/llm.py`, `pipeline/providers/*`. Verify 11.1/11.2 cleanliness. Leaky shapes? Neutral enough for an Ollama/local provider?
5. **Tool / MCP layer** — wherever MCP lives. Is `MCPTool` the neutral type, or is there a neutral shape with MCP as one implementation? What is the permissioning model today vs. the manifest+consent+sensitive-tier target?
6. **Conversation state & storage** — where does chat history live? Is it behind a `ConversationStore`-like interface, or hardcoded? Anything that looks like it could accidentally leave the user's machine?
7. **Chat surface** — what exists today? Is there a headless core + API + client separation, or is the runner + UI fused? Gap vs. the `ollama serve` / `ollama run` target.
8. **Config & secrets** — `config.yaml`, `config.py`, `.env` handling. How layered is it? Can a user override anything from CLI? Any secrets hardcoded or defaulted to our accounts?
9. **Prompts & personas** — system prompts, tool-result signposting, skill prompts. Are they user-owned with our defaults, or baked in?
10. **Logging & telemetry** — any network calls that could be telemetry? Any fields in logs that leak provider/user data? Logging destination configurability.

## Output format

Deliver as a single markdown report organized by layer, each finding formatted as:

```
### [Layer N] Short title
**Severity:** critical | major | minor
**Where:** path/to/file.py:line-range
**Finding:** (a) what is hardcoded, (b) what is configurable, (c) what should be pluggable and isn't
**Why it violates ethos:** one line referencing which ethos marker
**Suggested direction (not a fix):** one line
```

End with a **prioritized summary**: all criticals first, then majors, then minors, each with a one-line hook so the user can skim and pick what to tackle.

## Working notes for the auditor

- This is a **read-only audit**. Do not write/edit code. Only produce the report.
- Ethos memory (`project_oss_ethos.md`) has the full rationale if you need to check a judgment call.
- Prior sessions (88–90) established the provider abstraction (11.1/11.2); that boundary should be close to clean already. Verify, don't re-derive.
- When in doubt about severity, err toward **major** not **critical**. Critical is reserved for things that actively violate the non-negotiable core (telemetry, data leaving machine, LLM loop being bypassable).
- If you find something clearly out of scope (audio-layer concern), do not flag it; note in a short "skipped — out of scope" list at the end.

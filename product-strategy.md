# AI Agent in Video Calls — Product Strategy Doc

*Living document — last updated March 2026*

---

## The Product in One Line

An open-source bridge layer that lets any AI agent join any video call as a live participant — with voice, tool access, and domain knowledge.

---

## Core Thesis

Meetings are where decisions actually happen, but AI agents are currently trapped in async interfaces (chat, IDE, email). This product is the missing connective tissue between the rapidly expanding ecosystem of AI agents and the synchronous, real-time world of video calls.

We are not building agents. We are not building a video platform. We are building the bridge between them.

---

## Product Philosophy

**Guided, not opinionated.** The product should feel like a setup wizard that walks you through decisions — choosing a TTS provider, selecting a voice, connecting your agent, configuring tool access — without locking you into any particular stack. Think OpenClaw's onboarding daemon: it makes smart defaults easy while keeping the full configuration space open. The goal is to remain general enough to accommodate use cases we haven't anticipated.

**Modularity is the architecture.** Every component in the pipeline — speech-to-text, LLM, text-to-speech, voice, meeting platform connector — should be swappable. This is what keeps the product honest and prevents vendor lock-in from creeping in at the infrastructure level.

**Public-facing agents, not surreptitious ones.** Unlike products like Cluely that optimize for hidden, personal AI assistants in calls, we bet that agent presence in meetings is normalizing. As agents become commoditized, teams will openly invite them as participants. This is a collaborative tool, not a cheat code.

**Embrace the full range of team dynamics.** The Linear-ticket-via-@agent-in-chat use case — initially dismissed as edge-case — revealed something important: team meeting cultures vary wildly. Some teams want the agent to speak aloud. Others want it silently writing tickets from chat prompts. Some want it proactively surfacing context; others want it purely reactive. The product must accommodate this spectrum without forcing a single interaction model.

---

## Competitive Landscape & Positioning

**Recall.ai** — APIs for accessing real-time meeting data from Zoom/Meet/Teams. Solves the "get into the meeting" problem. Could be infrastructure we build on, a partner, or a competitor. Worth watching closely and potentially integrating rather than rebuilding this layer.

**LiveKit** — Open-source WebRTC infrastructure for connecting humans and AI. The most technically adjacent project in the ecosystem. They provide the real-time media primitives (SFU, SDKs, agent framework) but are focused on building-your-own-video-app, not joining existing meetings on Zoom/Meet. Could be complementary infrastructure.

**Cluely & similar** — Optimized for surreptitious, individual AI assistance during calls. Different bet (hidden vs. public-facing agents), different market (individual productivity vs. team collaboration).

**Meeting summarizers (Otter, Fireflies, etc.)** — Passive, post-hoc. They record and summarize. We're building an active participant that can take actions during the call. Fundamentally different value proposition.

---

## Comps & Playbooks to Study

### Infrastructure Bridge Comps

**Twilio** — The most structurally similar comp. Took telecom complexity and made it API-accessible for developers. Developer-first, bottoms-up GTM. Self-serve, usage-based pricing. Didn't build the apps — built the platform others built on. Their journey from API to platform to ecosystem is the arc to study.

**LiveKit** — Open-source WebRTC infrastructure, Apache 2.0 licensed. Relevant both as a potential dependency and as a GTM comp. They offer a free self-hosted server alongside LiveKit Cloud (managed, paid). Their agent framework for building real-time voice AI agents is particularly relevant — study how they structured the plugin system for swappable STT/LLM/TTS providers. Raised $20M+, counts OpenAI as a customer.

**Daily.co** — Video/audio infrastructure APIs. Positioned as complementary to Zoom/Meet rather than competitive. Studied how to build a platform layer underneath existing video ecosystems.

### Open Source Motion Playbooks

**OpenClaw** — The most instructive open-source onboarding UX to study. Their setup wizard walks users through model provider selection, API key configuration, channel setup, and daemon installation in a single guided flow — with QuickStart defaults and an Advanced path. Went from 0 to 200K+ GitHub stars in months. The "guided but not opinionated" pattern is exactly the UX philosophy we want. Key lesson: the wizard makes smart defaults trivially easy while keeping the full config surface accessible for power users.

**Resend / React Email** — Paul Graham called it "the Stripe for Email." The playbook is a masterclass in open-source-as-wedge: they launched React Email as an open-source project first (building community and developer love), then launched the commercial Resend API on top of it. The open-source component library became the acquisition funnel for the paid service. Over 1M developers using the product. Study their sequencing: community first, commercial second.

**Puck** — Open-source visual editor for React. Explicitly positioned as bridging the gap between developers and content teams — a "bridge" product, just like ours. Went from 8 to 1,800 GitHub stars in 24 hours on their Hacker News launch. Born out of a consultancy (Measured) that kept hitting the same problem across client projects — sound familiar? MIT licensed, no vendor lock-in, portable data model. Key takeaway: they built something deeply modular (adapters, plugins, works with any CMS) while still providing an opinionated-enough default experience that you can demo it in 30 seconds.

**Hugging Face** — The community-building playbook for AI. Became the default hub for ML models by making sharing and discovery frictionless. If our platform becomes where people publish and share "meeting-ready agent configs," that's a powerful network effect. Their trajectory from open-source tool to enterprise platform ($4.5B valuation) is the long arc to aspire to.

### Developer-Loved UX Comps

**Charm.sh** — The gold standard for developer UX in CLI tools. Their entire brand is built around making the command line "glamorous" — beautiful TUI frameworks (Bubble Tea), stylish shell scripts (Gum), markdown rendering (Glow). Key insight: they proved that developers will choose tools partly on aesthetic and craft quality, not just functionality. Their tools are used in 25,000+ applications. Study their visual identity, their tone, and how every touchpoint feels considered. The philosophy of "making the terminal beautiful" translates directly to "making the agent setup experience beautiful."

**Raycast** — Not open-source itself, but its extension ecosystem is. Relevant as a comp for how to build a platform that developers love and extend. Built with React/TypeScript — familiar tools, low barrier. Their store/marketplace model shows how a community of extensions creates a flywheel. Also relevant: their positioning as "AI middleware" between users and the fragmented world of LLMs and services. That's analogous to our position between agents and video calls.

---

## GTM Strategy

### Phase 1: Dogfooding & Community Seeding

Target: Small-to-mid dev teams and agencies already building or using AI agents. People like us. The pitch: "You've built an agent — now put it in your meetings." Primary channels: developer communities, AI/agent forums, Hacker News launch, Discord. Open-source the core under a permissive license (MIT or Apache 2.0).

### Phase 2: Organic Expansion

As one developer on a team sets it up, usage spreads to the rest of the org. Non-technical team members start requesting agents in their calls. This is the Slack-style expansion pattern. Focus on making the setup wizard so good that the initial developer can configure it for their team without writing docs.

### Phase 3: Enterprise

Security, compliance, admin controls, SSO, audit logs, custom integrations, SLAs. This is where the real revenue lives. Managed/hosted offering for teams that don't want to self-host.

### Pricing Model

Usage-based (per agent-minute or per meeting) aligns incentives. Generous free tier for experimentation. Open-source version handles self-hosting. Hosted version charges for convenience, reliability, managed infrastructure, and enterprise features.

---

## Current State of the Product ("Operator")

The working prototype is a macOS menu bar app called **Operator**. It joins Google Meet calls as a named participant, listens to all meeting audio, and responds aloud when prompted by anyone saying "operator." It's been in active use by our team for internal meetings.

### What works today (all verified end-to-end)

The full audio pipeline: ScreenCaptureKit (via compiled Swift helper) → utterance-based silence detection → faster-whisper transcription → wake phrase detection → GPT-4.1-mini → ElevenLabs streaming TTS → BlackHole virtual audio → meeting mic. Auto-join from Google Calendar invites via Playwright controlling a real Chrome instance. Conversation mode (20s follow-up window without re-triggering wake phrase). Backchannel active listening ("mm-hmm?", "go on") with completeness checking. Echo prevention, hallucination filtering, and a 20-item gotcha log of hard-won production knowledge.

### Measured latency breakdown

End-of-speech → first audio response: silence detection (~~1.0s) + Whisper transcription (~~0.5s) + LLM API round-trip (~~0.9–3s) + TTS first chunk (~~0.4s). The LLM round-trip dominates and is not addressable in code. Current latency masking: pre-cached filler phrases in the agent's voice, fired during silence. Deferred: LLM→TTS streaming overlap (sentence-level batching, est. ~0.5–1s reduction).

### Key technical decisions already made

Local STT over cloud: faster-whisper `base` was benchmarked against 6 providers on real ScreenCaptureKit audio. Deepgram was 0.3s faster but requires API keys and per-minute cost. For an open-source downloadable product, local inference (zero cost, no API onboarding, auto-downloads via pip) is the right tradeoff. GPT-4.1-mini over Claude for both responses and completeness checks — faster for this use case. ElevenLabs `eleven_flash_v2_5` for TTS — fastest available, ~0.4s to first audio chunk. Whisper-based wake detection over Porcupine — Porcupine required a chime that interrupted fast speech; Whisper inline detection is simpler and chime-free.

### The scaling problem (the biggest open question)

The current architecture requires a dedicated macOS machine running a Playwright-controlled Chrome browser to join each meeting. This works for one team, one meeting at a time. It does not scale to multiple concurrent meetings, other teams, or non-macOS environments. The entire macOS dependency chain (ScreenCaptureKit, BlackHole, Swift binary, py2app, rumps) is tightly coupled.

**Important clarification:** Only the agent host machine needs macOS. Meeting participants use whatever platform they want — they just see "Operator" as another person in the call.

---

## Architecture: From Prototype to Product

### The layered abstraction

To go from "works on my MacBook" to "anyone can install this," the architecture needs three separable layers:

**Layer 1 — The Agent Pipeline (platform-agnostic core).** Audio in → utterance segmentation → wake detection → conversation state machine → LLM orchestration → TTS out → echo prevention. This is where all the hard-won knowledge lives (the 20 gotchas, the Whisper silence pad, the backchannel echo drain, conversation mode). This should be extractable into a Python package with no macOS dependencies.

**Layer 2 — The Meeting Connector (swappable adapters).** An interface that different adapters implement. The current Playwright + BlackHole + ScreenCaptureKit approach becomes one adapter. Future adapters: Recall.ai, LiveKit, headless browser in Docker on Linux, raw WebRTC. Designing this as a swappable interface from day one is critical — it lets the community build connectors without touching the core pipeline.

**Layer 3 — The Platform Shell (OS-specific UX).** The macOS menu bar app, the setup wizard, platform-specific audio routing. Linux users get a CLI. Windows users get a system tray app (later). This layer is the thinnest and most replaceable.

### The meeting connector question (resolved)

**Decision: migrate from the dedicated-macOS-machine Playwright approach to a headless browser in a cloud container (Docker on Linux) as the primary connector. Design the connector as a swappable interface. Recall.ai and LiveKit SIP dial-in are fallback paths if browser automation becomes untenable.**

Rationale: The current approach (Playwright controlling Chrome on a dedicated MacBook) is fundamentally a babysitting job, not a product. If someone closes the laptop, the agent dies. Concurrent meetings require multiple browser instances on one machine. Users can't touch the machine while Operator is running. And it requires macOS, which we don't want to impose on the agent host. The headless cloud container solves all of these: the agent runs on a server nobody touches, each meeting gets its own container, and the macOS dependency is eliminated.

**Risk: browser automation fragility.** Google Meet may detect headless Chrome (CAPTCHAs, blocked joins, UI changes breaking selectors). Mitigation: daily automated smoke tests against live Meet sessions to catch UI changes or bot detection early, with clear contingency plans documented for each failure mode. This is also why the connector interface must be swappable — if Google escalates bot detection beyond what we can manage, we need to be able to drop in a Recall.ai adapter or SIP dial-in path without touching the agent pipeline.

**Risk: audio quality regression.** Current STT benchmarks were run on ScreenCaptureKit-captured audio (macOS). Container-captured audio (PulseAudio/virtual sink on Linux) will have different characteristics. STT accuracy and wake phrase detection must be re-validated on container audio before shipping.

**Tradeoff analysis (cloud container vs. local machine):** LLM/TTS API latency likely improves (better network from cloud). No new latency from audio path (meeting audio already routes through Google's servers; the container is just another participant). Browser automation fragility shifts from "user's machine is unpredictable" to "headless Chrome detection is a cat-and-mouse game." UX shifts from menu bar app to web dashboard/CLI (a better fit for a product). Cost becomes a line item (~$0.02–0.05/hr per concurrent meeting container).

**On SIP/dial-in as a fallback path:** LiveKit supports SIP integration that could let the agent dial into meetings via phone. Dramatically more reliable (no browser, no bot detection) but comes with real tradeoffs: audio-only (no video tile, no chat, no visual feedback), narrowband audio quality (worse STT accuracy), and additional latency from telephony codecs (~100–300ms each way). Best suited as a reliability fallback, not the primary path, because it forecloses too many interaction modes we want to support.

### Agent identity (the auth problem)

Every agent that joins a meeting needs to show up as a real, authenticated Google account. Unauthenticated headless browsers are blocked at the door — Google Meet shows a "you can't join this call" screen. This is not a bot-detection problem; it is an identity problem. The fix is not stealth config, it is a real Google account.

**How this works in the hosted product:**

1. We own a domain (e.g., `operator.dev`) and run Google Workspace on it.
2. When a team signs up, we programmatically provision an agent account — `operator-{team-hash}@operator.dev` — via the Google Admin SDK.
3. A Google Cloud service account with **domain-wide delegation** can impersonate any user on our domain, generating valid browser sessions without a human login flow. No manual auth step per account.
4. The user receives their agent's email address. They invite it to meetings. The agent joins as a legitimate, invited participant.

**For the v1 beta (small pool, manual):** Create a small pool of accounts by hand on our Workspace domain. Authenticate each one via `scripts/auth_export.py` (a one-time local browser login that exports the session to `auth_state.json`). Mount that file into the container at runtime. Automate provisioning once demand justifies it.

**For the self-hosted open-source version:** Users are expected to bring their own Google account for the agent, the same way they bring API keys. The setup wizard walks them through it once.

**What Recall.ai does:** The same thing — they maintain a pool of real Google accounts on their own domain, managed with the same service account delegation pattern. Their accounts are invited participants, not uninvited guests.

**Decision:** The hosted product manages agent identity centrally. Users never create a Google account. They receive an email address on signup and invite it to meetings.

---

### The scalability roadmap

**Phase 1 (current internal use):** Dedicated macOS machine, one meeting at a time. Continues working for our team while we build Phase 2.

**Phase 2 (v1 open-source release target):** Containerized headless browser (Docker on Linux). Replace ScreenCaptureKit with PulseAudio/virtual audio sink. Replace BlackHole with virtual audio routing in the container. Each meeting gets its own container. Eliminates the macOS requirement, the "one machine one meeting" constraint, and the "don't touch the laptop" problem. Users `docker run` the whole thing.

**Phase 3 (if browser automation becomes untenable):** API-based connectors — Recall.ai adapter and/or LiveKit SIP dial-in. No browser at all. The agent is a service that receives and sends audio streams via API. Dramatically more scalable, but a bigger rewrite of the connector layer.

---

## Interaction Modes

**Both voice and chat ship out of the box.** The wizard lets teams choose their default, but both are always available.

**Voice mode** is the current implementation: the agent speaks aloud through the meeting, triggered by the wake phrase. Best for brainstorming, solutioning, and when the whole group needs to hear the agent's input.

**Chat mode** is the simpler path: the agent responds in the meeting chat when @-mentioned. No latency masking needed, no turn-detection complexity. Best for ticket creation, lookups, and "side conversations" where one participant is prompting the agent without interrupting the group's flow.

The chat mode insight came from a demo where a friend wanted to use the agent specifically to write Linear tickets mid-call via @agent in the meeting chat. Initially this felt like an edge case — it seemed intrusive for one participant to have a side conversation. But it turns out side conversations are normal in many team cultures, and chat-based agent interaction is actually *less* intrusive than voice.

---

## Latency Strategy

The LLM API round-trip (~0.9–3s) dominates total latency and is not addressable in code. The strategy is masking, not elimination.

**Audio masking (current):** Pre-cached filler phrases ("mm-hmm?", "go on") in the agent's voice, triggered after silence detection. The silence threshold for when to fire these is the main tuning knob and hasn't been fully dialed in yet — too aggressive and it collides with the speaker continuing; too conservative and you get awkward silence.

**Visual masking (next):** Multiple mechanisms to explore, in order of implementation complexity:

1. **Emoji reactions in the meeting.** Most meeting platforms support emoji reactions (thumbs up, thinking face, etc.). The agent could fire a 🤔 reaction when processing and a ✅ when done. Lightweight, no video feed needed, works even in audio-only mode.
2. **Chat acknowledgment.** Post a brief message in the meeting chat: "Looking into that..." or "Creating a Linear ticket..." Gives specific feedback about what the agent is doing, not just that it's thinking. Works naturally alongside chat-mode interaction.
3. **Virtual camera feed.** Render a simple animation or status display as the agent's video tile — a thinking indicator, a waveform, a status line showing "looking up Shopify API docs..." This is the richest feedback channel and makes the agent feel most like a real participant. Requires generating a virtual camera stream in the container, which adds complexity but is very doable.

The general principle: visual feedback can be always-on, while audio fillers should fire only when the agent is directly addressed and silence would feel awkward.

**Pipeline optimization (deferred):** LLM→TTS streaming overlap with sentence-level batching. Estimated ~0.5–1s reduction. Worth implementing but not the highest priority vs. the connector abstraction work.

---

## The "Loadout" — Agent Configuration as a Shareable Unit

The video game analogy: choose your agent, equip it with tools. A complete agent configuration — the "loadout" — consists of:

- **LLM provider + model** (e.g., GPT-4.1-mini, Claude Sonnet, local Llama)
- **Voice** (TTS provider + voice ID + cached filler phrases)
- **System prompt / persona** (e.g., "You are a Shopify API expert")
- **Knowledge sources** (docs, codebase context, uploaded files)
- **Tools / MCP servers** (Linear, GitHub, Notion, Slack, custom)
- **Interaction mode defaults** (voice, chat, or both)
- **Conversation behavior** (proactive vs. reactive, conversation mode timeout, wake phrase)

This loadout should be serializable — a config file or small package that can be shared, forked, and published to a marketplace.

### Agent packaging landscape (research)

The "zip up and share an agent" problem is an active area with several emerging standards, none yet dominant:

**Agent File (.af)** by Letta (formerly MemGPT) — An open file format for serializing *stateful* AI agents with persistent memory and behavior. JSON-based, packages system prompts, memory, tool configs, and LLM settings. Focused on preserving agent state (conversation history, learned preferences). ~1K GitHub stars. Most mature for the "snapshot a running agent" use case, but tied to the Letta framework.

**Agent Packaging Standard (APS)** — A spec for a manifest (agent.yaml), a portable archive (.aps.tar.gz), and a registry API. Framework-agnostic, focused on distribution and discovery. Early stage (v0.1 spec). Closest to what a "pip for agents" would look like.

**GitAgent** — Treats the Git repo itself as the unit of agent portability. Agent instructions, tools, and config live in a standard repo structure. Leverages Git's existing version control, collaboration, and review primitives. Pragmatic and developer-native, but doesn't solve the "one-click install" problem.

**.agent format** by Nomotica — A self-contained archive with manifest, code, dependencies, and an analysis/trust score. Includes a packaging pipeline and local REST API for creating packages. Patent-pending, which may limit adoption.

**Agent Skills** (Anthropic open standard) — A lightweight format for packaging agent *capabilities* (not full agents) as SKILL.md files with YAML frontmatter. Focused on portable knowledge and instructions rather than full agent state. Already adopted by Claude Code, Cursor, and others via the `skills` CLI. The most traction of any format so far, but scoped to skills/capabilities rather than complete agent configurations.

**Our take:** None of these are exactly what we need — a lightweight, shareable "loadout" format for meeting agents specifically. But we should design our config format to be compatible with the emerging standards rather than inventing something from scratch. The Agent Skills format is worth watching most closely given its adoption momentum and Anthropic's track record with MCP standardization. For our v1, a simple YAML/JSON config file that describes the loadout is sufficient. Marketplace and registry come later.

---

## User Journey

### The "set the table" flow (one developer, one time)

1. Install Operator (`npm install -g operator` or `pip install operator` or `curl | bash`)
2. Setup wizard launches (OpenClaw-style guided flow):
  - Choose LLM provider → paste API key (default: OpenAI GPT-4.1-mini)
  - Choose TTS provider → paste API key (default: ElevenLabs)
  - Pick a voice (default provided, with preview)
  - Connect tools — curated list with toggle switches: Linear, GitHub, Notion, Slack (default: none — the agent works with zero tools using just LLM knowledge)
  - Add knowledge sources (optional — docs, codebase context)
  - Choose interaction mode (voice, chat, or both)
  - Configure meeting platform connector
3. Wizard generates an agent email address (e.g., [operator-yourteam@operator.dev](mailto:operator-yourteam@operator.dev))
4. Done. First meeting should work with just two API keys and pressing "next" five times.

### The daily use flow (any team member, every meeting)

1. Schedule a meeting as normal
2. Invite the agent's email address as a participant
3. Agent auto-joins at meeting time
4. Interact via wake phrase (voice) or @agent (chat)
5. Agent responds, takes actions, creates tickets — whatever its loadout is configured for

**Key principle:** The setup cost is paid once by one developer. The recurring experience is zero-friction for everyone.

---

## Resolved Questions

- **Voice or chat?** Both, out of the box. Wizard lets teams choose defaults.
- **Meeting connector strategy?** Migrate to headless browser in cloud container (Docker on Linux) for the v1 open-source release. Connector designed as swappable interface. Recall.ai and LiveKit SIP dial-in are fallback paths if browser automation becomes untenable. Daily automated smoke tests to catch UI changes / bot detection.
- **Agent identity / auth (hosted product)?** We provision real Google accounts on our own domain (e.g., `operator.dev`) via Google Workspace + Admin SDK. A service account with domain-wide delegation generates authenticated sessions without any manual login per account. Users receive an agent email address and invite it to meetings — no Google account setup on their end. Self-hosted users bring their own Google account, configured once via the setup wizard.
- **MCP integration?** Yes — first-class support. Tools configured as toggle switches in the wizard, with the ability to add custom MCP servers.
- **Marketplace?** Yes, essential for the network effect flywheel. But comes after the core product is stable. Start with a curated directory of community-contributed loadouts.

## Open Questions

- **What's the right silence threshold for backchannel timing?** Currently iterating. Too aggressive = collides with speaker continuing. Too conservative = awkward silence.
- **Containerization path specifics.** What replaces ScreenCaptureKit + BlackHole in a headless Linux Docker container? PulseAudio + virtual sink? Need to prototype.
- **Wake phrase customization.** Should teams be able to choose their own wake phrase? ("hey Atlas", "yo Jarvis") This requires either a flexible Whisper-based matcher or a return to something like Porcupine with custom wake words.
- **Multi-agent meetings.** Can you have more than one agent in a call? Different agents for different domains? How do they avoid talking over each other?
- **Agent config format.** What does the loadout file look like? YAML? JSON? Should we align with an emerging standard (Agent Skills, APS)?
- **Licensing.** MIT vs. Apache 2.0 vs. dual license for the open-source release.

---

## Next Steps

### Critical path (container migration)

- Get the current Python audio pipeline running on Linux (replace ScreenCaptureKit with PulseAudio/virtual sink, replace BlackHole with virtual audio routing)
- Get headless Playwright/Chrome joining a Google Meet call from a Docker container
- Re-run the STT benchmark suite on container-captured audio (PulseAudio path) — validate Whisper accuracy and wake phrase detection against the ScreenCaptureKit baseline before shipping
- Wire up end-to-end: container joins meeting → captures audio → pipeline processes → TTS output routes back into meeting
- Build daily automated smoke test: container joins a test meeting, agent responds to wake phrase, test asserts audio round-trip works. Run on CI. Document contingency plans for each failure mode (UI change, bot detection, audio routing failure)

### Architecture

- Extract the agent pipeline (Layer 1) into a platform-agnostic Python package with no macOS dependencies
- Define the meeting connector interface (Layer 2) as an abstract class; implement the container-based adapter as the primary connector and the current macOS approach as a legacy/dev adapter

### Product

- Build a proof-of-concept setup wizard inspired by OpenClaw's onboarding flow
- Add chat-mode interaction (respond in meeting chat, not just voice)
- Prototype visual feedback: start with emoji reactions and chat acknowledgments (simplest), then explore virtual camera feed
- Design the loadout config file format (start with simple YAML)

### Go-to-market

- Identify 5-10 beta teams outside our org to validate use cases
- Draft the public README and contribution guidelines
- Research licensing implications (MIT vs. Apache 2.0 vs. dual license)


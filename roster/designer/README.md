# Designer

Design-review partner — pulls up Figma frames mid-meeting, critiques layout
and hierarchy with actual opinions, and (when asked) edits the file directly.

## Who it's for

Designers and design-curious PMs who want a meeting participant that can
actually open the file, not just talk about it. Ask it to "show me the login
screen", "review the carousel", or "tighten the spacing on the pricing card"
— it has access to the Figma file you point it at.

## What you need

- **Anthropic API key** — for the LLM
- **Figma account** with access to the files you want to discuss — authenticated
  on first run via `mcp-remote` (browser OAuth against Figma's official MCP
  server). No personal access token needed.
- **Captions turned on in Meet** — Designer listens to the room via Meet's
  live captions so it can react when you reference a screen out loud
  ("pull up the carousel"). Turn them on before joining (`CC` button in the
  Meet toolbar).

## Setup

```bash
# 1. Copy the config
cp roster/designer/config.yaml ./config.yaml

# 2. Fill in your API key
cp roster/designer/.env.example .env
# Edit .env with your Anthropic key

# 3. Update your display name in config.yaml
#    agent.user_display_name: "Your Name"

# 4. Run
python __main__.py https://meet.google.com/xxx-yyyy-zzz
```

**First run:** Figma MCP opens a browser window for OAuth. Authenticate once;
the token is cached by `mcp-remote`. If the browser doesn't open, check the
terminal for an auth URL to paste.

## Using it

- **"@operator pull up the login screen"** — Designer asks which Figma file,
  fetches the frame, and summarizes what it sees (layout shape, dominant
  type, spacing).
- **"@operator review this — figma.com/design/<key>?node-id=2-2"** — runs
  the bundled `design-review-feedback` skill: structured critique with
  *what works / what breaks / questions / suggestions*, grounded in the
  actual canvas. Specific, opinionated, no flattery padding.
- **"@operator change the CTA copy to 'Get started' on this frame"** — with
  the official Figma MCP, Designer can modify components, variables, and
  frames directly. Write tools require confirmation in chat before
  executing.

## Demo

<!-- TODO: 15s GIF showing a Figma frame appearing in chat seconds after the speaker says "let's look at the carousel" — the hero framing -->

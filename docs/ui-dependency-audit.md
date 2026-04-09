# UI Dependency Audit

*Phase 9.1 — April 8, 2026*

Every DOM selector and UI interaction Operator uses to interface with Google Meet, classified by fragility.

---

## Classification Key

| Rating | Meaning | Risk |
|--------|---------|------|
| **Stable** | ARIA roles, semantic labels, Playwright `get_by_role()` — part of accessibility contracts, rarely change | Low |
| **Semi-stable** | `data-*` attributes, `aria-label` text content — intentional attributes, but text/values can shift | Medium |
| **Fragile** | Obfuscated class names (`.NWpY1d`), `jsname` attributes (`dTKtvb`), layout-dependent traversal (parent walks) — change without warning on any Meet deploy | High |

---

## 1. Join Flow

| Selector | Type | Rating | Used In | Purpose |
|----------|------|--------|---------|---------|
| `get_by_role("button", name="Join now")` | ARIA role + label | **Stable** | macos, linux, captions, session | Click to join meeting |
| `get_by_role("button", name="Ask to join")` | ARIA role + label | **Stable** | macos, linux, captions, session | Join with host approval |
| `get_by_role("button", name="Switch here")` | ARIA role + label | **Stable** | macos, linux, captions, session | Switch from another device |
| `get_by_role("button", name="Not now")` | ARIA role + label | **Stable** | linux, docker | Dismiss notifications popup |
| `get_by_placeholder("Your name")` | Placeholder text | **Semi-stable** | linux, docker | Fill guest name field |
| `button[aria-label*="Sign in"]` | ARIA label substring | **Semi-stable** | macos, captions, session | Detect sign-in prompt |
| `text=You can't join this video call` | Text content | **Semi-stable** | session | Detect access denied |
| `text=Sign in` | Text content | **Semi-stable** | session | Detect sign-in prompt |
| `img[alt*="Please wait until a meeting host"]` | Alt text substring | **Semi-stable** | macos, linux, captions | Detect waiting room |

## 2. Camera & Microphone Controls

| Selector | Type | Rating | Used In | Purpose |
|----------|------|--------|---------|---------|
| `get_by_role("button", name="Turn off camera")` | ARIA role + label | **Stable** | macos, linux, captions, docker | Disable camera (pre-join) |
| `get_by_role("button", name="Turn off microphone")` | ARIA role + label | **Stable** | macos | Mute mic |
| `get_by_role("button", name="Turn on microphone")` | ARIA role + label | **Stable** | macos, linux, captions, docker | Unmute mic |
| `button[aria-label*="Turn off camera"]` | ARIA label substring | **Stable** | macos, linux, captions, docker | Pre-join wait selector |
| `data-is-muted="true"` on camera button | Data attribute | **Semi-stable** | macos, linux, captions, docker | Confirmation gate — verify camera is actually off after toggle |

*Note: "Turn on camera" button is no longer used. Pre-join flow assumes Meet defaults to camera-on and only looks for "Turn off camera". After clicking, waits for `data-is-muted="true"` confirmation before proceeding to join.*

## 3. Meeting Lifecycle

| Selector | Type | Rating | Used In | Purpose |
|----------|------|--------|---------|---------|
| `get_by_role("button", name="Leave call")` | ARIA role + label | **Stable** | macos, linux, captions, docker | Leave meeting |
| `button[aria-label*="Leave call"]` | ARIA label substring | **Stable** | macos, captions | Wait for in-meeting state |
| `"meet.google.com" in url` | URL check | **Stable** | macos, linux, captions | Health check — not redirected |
| `accounts.google.com` in url | URL check | **Stable** | session | Detect logged-out redirect |

## 4. Chat I/O (V1 Critical Path)

| Selector | Type | Rating | Used In | Purpose |
|----------|------|--------|---------|---------|
| `textarea[aria-label="Send a message"]` | ARIA label exact | **Semi-stable** | macos, linux | Chat input box |
| `get_by_role("button", name="Chat with everyone")` | ARIA role + label | **Stable** | macos, linux | Open chat panel |
| `div[data-message-id]` | Data attribute | **Semi-stable** | macos, linux | Find chat message elements |
| `textarea.closest('[data-panel-id]')` | Data attribute (dynamic) | **Semi-stable** | macos | Chat panel container (MutationObserver scope) — dynamically discovered via textarea, not hardcoded |
| `div[jsname]` (any value) | jsname existence | **Semi-stable** | macos, linux | Extract message text content — no longer depends on specific obfuscated value `dTKtvb` |
| Time-pattern regex on sibling divs | Structural (text pattern) | **Semi-stable** | macos, linux | Extract sender name — matches `Name\nTimestamp` pattern instead of obfuscated `.HNucUd` class |

### Docker adapter (older, divergent selectors):
| Selector | Type | Rating | Used In | Purpose |
|----------|------|--------|---------|---------|
| `get_by_role("button", name="Open chat")` | ARIA role + label | **Stable** | docker | Open chat panel (different label!) |
| `get_by_role("textbox", name="Send a message to everyone")` | ARIA role + label | **Stable** | docker | Chat input (different label!) |

## 5. Captions (Voice Path — not v1 critical)

| Selector | Type | Rating | Used In | Purpose |
|----------|------|--------|---------|---------|
| `[role="region"][aria-label*="Captions"]` | ARIA role + label | **Stable** | captions | Caption region container |
| `button[aria-label*="Turn off captions"]` | ARIA label substring | **Stable** | captions | Check if captions are on |
| `button[aria-label*="Turn on captions"]` | ARIA label substring | **Stable** | captions | Enable captions |
| `.NWpY1d, .xoMHSc` | CSS class (obfuscated) | **Fragile** | captions | Speaker name badge in caption |

## 6. Participant Detection

| Selector | Type | Rating | Used In | Purpose |
|----------|------|--------|---------|---------|
| `[data-requested-participant-id]` | Data attribute | **Semi-stable** | macos | Count participants in waiting room |
| `[data-participant-id]` | Data attribute | **Semi-stable** | captions | Find participant tiles |
| `[aria-label]` scan + innerText | ARIA label scan | **Semi-stable** | captions | Search for user display name |

## 7. Overlay/Modal Detection

| Selector | Type | Rating | Used In | Purpose |
|----------|------|--------|---------|---------|
| `[role="dialog"], [role="alertdialog"]` | ARIA role | **Stable** | captions | Find blocking modals |
| `[style*="z-index"]` | Inline style | **Semi-stable** | captions | Find high-z overlays |

---

## Risk Summary

### Fragile selectors (break without warning):

| Selector | What it does | Where | Status |
|----------|-------------|-------|--------|
| ~~`div[jsname="dTKtvb"]`~~ | Message text extraction | macos, linux chat | **Hardened** — now uses `div[jsname]` (any value) with fallback to first child text node |
| ~~`div.HNucUd`~~ | Sender name extraction | macos, linux chat | **Hardened** — now uses time-pattern regex on sibling divs instead of class name |
| `.NWpY1d, .xoMHSc` | Caption speaker badge | captions adapter | Not v1-critical; caption path is post-v1. Structural fix identified (firstElementChild positional extraction) |

### Semi-stable selectors to watch:

| Selector | Risk | Mitigation |
|----------|------|------------|
| `textarea[aria-label="Send a message"]` | Label text could change | Also try `get_by_role("textbox")` within chat panel |
| `div[data-message-id]` | Google could rename the attribute | Core to chat observer; no great alternative — monitor for breakage |
| ~~`[data-panel-id="2"]`~~ | ~~Panel numbering could change~~ | **Fixed** — now dynamically discovered via `textarea.closest('[data-panel-id]')` |
| `img[alt*="Please wait until a meeting host"]` | Alt text is localized | Only works in English; known limitation |
| `data-is-muted="true"` | Attribute name/value could change | Confirmation gate for camera toggle; if absent, join proceeds anyway with warning |
| `div[jsname]` (any value) | jsname attribute could be removed entirely | Falls back to first child's first text node, then raw innerText |
| Time-pattern regex for sender | Assumes AM/PM format; breaks in 24h locales | Could add 24h pattern `\d{2}:\d{2}` as alternative match |

### Cross-adapter divergence:

The Docker adapter uses different selector text than macos/linux for the same UI elements:
- `"Open chat"` vs `"Chat with everyone"` — different button labels
- `"Send a message to everyone"` vs `"Send a message"` — different input labels

This may indicate Google Meet A/B tests or UI versions. All adapters should handle both variants.

---

## Recommendations for Phase 9.2+ 

1. **Centralize selectors** — Extract all selectors into a shared `selectors.py` module so there's one place to update when Meet changes. Currently duplicated across 3-4 adapter files.
2. **Add selector health check** — On meeting join, verify the 3 fragile selectors resolve. Log a warning if they don't. This gives early signal before silent failures.
3. **DOM regression test** (Phase 9.2) — Automated test that joins a real Meet, verifies each selector category resolves at least one element.
4. **Multi-variant selectors** — For chat input, try `textarea[aria-label="Send a message"]` first, fall back to `get_by_role("textbox", name=re.compile("send a message", re.I))`.
5. **Locale risk** — Several selectors depend on English text (`"Please wait until a meeting host"`, `"Join now"`, etc.). Document this as a known limitation or add locale detection.

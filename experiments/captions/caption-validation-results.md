# Caption Validation Results

*Results from `test_captions_v2.py` — three-phase experiment to close all 7 gaps from `caption-timing-findings.md`.*

*Setup: Host laptop runs script with mic ON (Speaker A). Rober joins from another room (Speaker B).*

---

## Phase 1: Multi-Speaker (Gaps 1, 6)

**Date:**
**Meeting URL:**

### Results

*(paste report output here)*

### Observations

---

## Phase 2: Endurance + ASR Corrections + Technical Terms (Gaps 2, 3, 7)

**Date:**
**Meeting URL:**

### Phase A: ASR Corrections

### Phase B: Technical Terms

### Phase C: Endurance (10 min)

### Observations

---

## Phase 3: Availability + Late Enable (Gaps 4, 5)

**Date:** 2026-03-30
**Meeting URL:** meet.google.com/qfn-ugzs-xjc
**Log:** `logs/captions_20260330_220550.log`

### Normal Availability (Phase A)

Captions work. Node #2 accumulated 91 updates over 40.3s, reaching 539 chars max. Update cadence ~0.45s average (consistent with Experiment 1's ~0.33s). Speaker label ("Jojo Shapiro") was reliably attributed on every update.

**Verdict: captions are available on this account. Gap 4 closed.**

### Late Enable (Phase B)

Captions were toggled off at 22:07:08. User spoke for ~20 seconds with captions off (22:07:10 → 22:07:30). Captions re-enabled at 22:07:30.

**No retroactive capture.** Speech during the off period was completely lost. First new speech node (#444) appeared at 22:07:37 (~7s after re-enable), containing only speech from that point forward.

**DOM flood on re-enable.** Toggling captions back on triggered ~440 spurious DOM nodes from the caption settings UI panel: language selection menus (1840-char lists repeated 8x), "arrow_downwardJump to bottom" buttons, "settings" labels, individual language entries ("Afrikaans (South Africa)BETA", "Albanian (Albania)BETA", etc.). The MutationObserver scope captured all of this UI chrome.

### Observations

- Late enable works mechanically but loses all speech during the off period — not useful for catch-up.
- Toggling captions creates a massive DOM pollution event (400+ junk nodes). The observer must be scoped tightly to caption text nodes, not the entire region.
- Practical implication: **enable captions once at join and never toggle.** This makes Gap 5 a non-issue.
- ASR quality note: Meet transcribed "Phase B late enable test" as "Faith be late and able test" — reinforces that Meet ASR struggles with non-conversational / technical phrases (related to Gap 7).

---

## Go / No-Go Verdict

| Gap | Question | Result | Verdict |
|-----|----------|--------|---------|
| 1 | Multi-speaker: separate DOM nodes per speaker? | | |
| 2 | Text length: does Meet cap node text length? | | |
| 3 | ASR corrections: how far back do rewrites reach? | | |
| 4 | Free Gmail: do captions work without Workspace? | Yes — 91 updates over 40s, reliable speaker labels | GO |
| 5 | Late enable: can we turn on captions after joining? | Yes, but no retroactive capture + 400-node DOM flood on toggle. Moot if we enable at join. | GO (enable once at join) |
| 6 | Overlapping speech: what happens to DOM nodes? | | |
| 7 | Technical terms: how does Meet handle jargon? | | |

**Overall verdict:**

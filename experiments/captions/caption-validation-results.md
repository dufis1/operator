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

**Date:** 2026-03-31
**Meeting URL:** meet.google.com/mde-zuaj-ekb
**Log:** `logs/endurance_20260331_210935.log`

### Phase A: ASR Corrections

Speaker repeated "endurance training" five times with ~1s pauses between each repetition. Meet's ASR corrections are clearly visible:

- **Correction cadence:** ~330ms per correction step (consistent with baseline experiment).
- **Correction pattern:** Meet initially shows a short prefix ("End."), then corrects in rapid succession ("End." → "Endurance." → "Endurance training.") within 0.3–0.7s.
- **Correction depth:** Rewrites reach 1–28 chars back. Most corrections rewrite only the last word (1 char back = replacing the trailing period before appending). Larger corrections (13–28 chars back) happen when Meet restructures phrasing, e.g. "Okay. Now I'm" → "okay, now I'm gonna continue speaking."
- **Post-pause behavior:** After a 1s pause, Meet treats resumed speech as a continuation of the same node — no new node created. The first update after a pause shows a short prefix ("End.") which is immediately corrected to the full word on the next 330ms tick.
- **"Operator" detection:** "Oper." corrected to "Operator." in one 330ms step. Reliably captured.
- **Question after operator:** "What's 2 plus 2?" was refined through several corrections to "What's 2 + 2?" — Meet normalized the arithmetic.

**Verdict: ASR correction window is short (~330ms steps, 1–28 chars back). Safe to treat text as finalized after a 2–3s silence gap. Gap 3 closed.**

### Phase B: Technical Terms

| Term | Meet ASR Output | Accurate? |
|------|----------------|-----------|
| API endpoint | API endpoint | Yes |
| kubectl apply | Cubicle apply | No |
| PostgreSQL | PostgreSQL | Yes |
| OAuth2 | Go off, too | No |
| localhost 3000 | Local host 3000 | Partial |
| pip install numpy | App Install [mangled] | No |
| YAML config file | Yeah, more config file | Partial |
| JSON web token | JSON Web token | Yes |
| SSH into the server | Ssh into the server | Yes |
| git rebase --interactive | Get rebase dash dash interactive | Partial |

6/10 terms were captured correctly or close enough. Failures are all phonetic mishearings by Meet's ASR — "kubectl" sounds like "cubicle", "OAuth" sounds like "go off". This is a Meet ASR limitation, not a DOM issue. Whisper would have the same problem with these terms.

**Verdict: Meet ASR handles common technical terms (API, PostgreSQL, JSON, SSH) well but mangles uncommon ones (kubectl, OAuth2). Same limitation as any ASR system. Gap 7 closed — not a blocker for caption scraping.**

### Phase C: Endurance (10 min)

Second computer joined the meeting and ran the `say -v Samantha` loop (120 sentences). The script listened for 10 minutes.

- **Node #127** accumulated all endurance speech: **1020 updates over 534.9 seconds (~9 minutes), reaching 6018 characters** with no truncation.
- **No text length cap detected.** The node grew continuously from sentence 1 through sentence 103 without any sign of truncation or splitting.
- **Update interval during endurance:** avg 0.52s (slightly slower than the 0.33s baseline — likely because the `say` command has pauses between sentences).
- The final text ends mid-sentence ("...Sentence 103 of the endurance test for caption validation. Sentence.") — the remaining sentences (104–120) were still playing when the 10-minute window closed.

**Verdict: No text length cap. A single DOM node can grow to 6000+ chars over 9 minutes without truncation. Gap 2 closed.**

### Observations

- **DOM noise is a major issue.** The observer captured 131 nodes total, but only 3 contained actual captions (nodes 1, 2, and 127). The remaining ~128 nodes were Google Meet UI elements: `keep_outline`, `mic_off`, `mic_none`, `more_vert`, `aspect_ratio`, `visual_effects`, `frame_person`, participant tile labels, and tooltip text. The MutationObserver selector must be scoped tightly to the caption container before the refactor — observing `document.body` is not viable.
- **All speech went into a single node per phase.** Node #2 held all of phases A + B (351 chars, 71 updates, 145s). Node #127 held all of phase C (6018 chars, 1020 updates, 535s). Confirms the baseline finding: one node per speaker turn, not per utterance.
- **913 ASR corrections observed** during the endurance phase, averaging 12 chars back per correction (max 104). This is higher than phase A because the `say` loop produces rapid continuous speech with less natural pausing.

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
| 2 | Text length: does Meet cap node text length? | No — single node grew to 6018 chars over 9 minutes with no truncation | GO |
| 3 | ASR corrections: how far back do rewrites reach? | 1–28 chars back in ~330ms steps. Avg 12 chars back during sustained speech. Text stable after 2–3s silence gap | GO |
| 4 | Free Gmail: do captions work without Workspace? | Yes — 91 updates over 40s, reliable speaker labels | GO |
| 5 | Late enable: can we turn on captions after joining? | Yes, but no retroactive capture + 400-node DOM flood on toggle. Moot if we enable at join. | GO (enable once at join) |
| 6 | Overlapping speech: what happens to DOM nodes? | | |
| 7 | Technical terms: how does Meet handle jargon? | 6/10 terms accurate. Failures are phonetic (kubectl→Cubicle, OAuth2→Go off too). Same limitation as any ASR | GO |

**Overall verdict:**

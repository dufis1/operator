"""
parse_latency.py — Parse /tmp/operator.log and print a perceived latency report.

For each prompt cycle it collects:
  - caption_wake_confirmed         (wake phrase confirmed, entering silence detection)
  - perceived_acoustic_silence_end (mic goes silent — first one after wake_confirmed)
  - filler_play_start              (filler clip begins)
  - response_play_start            (bot audio begins)
  - caption_prompt_finalized       (last caption arrives / finalization)

And computes:
  - ASR delay      = caption_prompt_finalized - perceived_acoustic_silence_end
  - Dead air       = filler_play_start        - perceived_acoustic_silence_end
  - Total dead air = response_play_start      - perceived_acoustic_silence_end

Cycles are anchored on caption_wake_confirmed so ambient silences from other
participants or background noise are ignored.

Usage:
    python scripts/parse_latency.py [/path/to/operator.log]
"""
import re
import sys
from datetime import datetime

LOG_PATH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/operator.log"

# Matches lines like: 2026-04-02 14:23:01,234 INFO ...
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
_TS_FMT = "%Y-%m-%d %H:%M:%S,%f"
_SPEAKER_RE = re.compile(r"speaker=([^\s]+)")
_PROMPT_RE = re.compile(r'TIMING prompt_finalized\s+"?([^"]+)"?')


def parse_ts(line):
    m = _TS_RE.match(line)
    return datetime.strptime(m.group(1), _TS_FMT).timestamp() if m else None


def main():
    try:
        with open(LOG_PATH) as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Log not found: {LOG_PATH}")
        sys.exit(1)

    cycles = []
    current = {}

    for line in lines:
        ts = parse_ts(line)
        if ts is None:
            continue

        if "TIMING caption_wake_confirmed" in line:
            # New cycle anchor — reset and record wake time + speaker
            speaker_m = _SPEAKER_RE.search(line)
            current = {
                "wake_confirmed": ts,
                "speaker": speaker_m.group(1) if speaker_m else "?",
            }

        elif "TIMING perceived_speech_start" in line:
            # User still talking — clear any acoustic_silence we may have stashed
            # (keeps only the final silence that ends the prompt)
            current.pop("acoustic_silence", None)

        elif "TIMING perceived_acoustic_silence_end" in line:
            # Only accept if we're inside an active wake cycle
            if "wake_confirmed" in current:
                current["acoustic_silence"] = ts

        elif "TIMING filler_play_start" in line:
            if "acoustic_silence" in current and "filler" not in current:
                current["filler"] = ts

        elif "TIMING response_play_start" in line:
            if "acoustic_silence" in current and "response" not in current:
                current["response"] = ts
                cycles.append(dict(current))
                current = {}

        elif ("TIMING caption_prompt_finalized" in line or
              "TIMING prompt_finalized" in line):
            if "wake_confirmed" in current:
                current.setdefault("finalized", ts)
                # Extract prompt text
                pm = _PROMPT_RE.search(line)
                if pm:
                    current.setdefault("prompt", pm.group(1).strip())
                # Speaker is available on caption_prompt_finalized
                if "speaker" not in current or current["speaker"] == "?":
                    sm = _SPEAKER_RE.search(line)
                    if sm:
                        current["speaker"] = sm.group(1)

    if not cycles:
        print("No complete perceived-latency cycles found in log.")
        print(f"(checked: {LOG_PATH})")
        print("\nMake sure the bot ran with this version and that you spoke at least one prompt.")
        return

    # Header
    col = "{:<6}  {:>10}  {:>10}  {:>10}  {:>14}  {}"
    print()
    print(col.format("Cycle", "ASR delay", "To filler", "To response", "Speaker", "Prompt"))
    print(col.format("", "(s)", "(s)", "(s)", "", ""))
    print("-" * 80)

    for i, c in enumerate(cycles, 1):
        acoustic = c.get("acoustic_silence")
        speaker = c.get("speaker", "?")
        prompt = (c.get("prompt", "") or "")[:35]

        if acoustic is None:
            asr = "  n/a"
            filler = "  n/a"
            response = "  n/a"
        else:
            asr = f"{c['finalized'] - acoustic:.2f}" if "finalized" in c else "  n/a"

            if "filler" in c:
                delta = c["filler"] - acoustic
                filler = f"LEAK({delta:.2f})" if delta < 0 else f"{delta:.2f}"
            else:
                filler = "  n/a"

            response = f"{c['response'] - acoustic:.2f}" if "response" in c else "  n/a"

        print(col.format(i, asr, filler, response, speaker, prompt))

    print()
    # Averages over cycles that have all three values and no gate leaks
    complete = [
        c for c in cycles
        if all(k in c for k in ("acoustic_silence", "finalized", "filler", "response"))
        and (c["filler"] - c["acoustic_silence"]) >= 0
    ]
    if complete:
        n = len(complete)
        avg_asr = sum(c["finalized"] - c["acoustic_silence"] for c in complete) / n
        avg_filler = sum(c["filler"] - c["acoustic_silence"] for c in complete) / n
        avg_response = sum(c["response"] - c["acoustic_silence"] for c in complete) / n
        print(f"Averages over {n} complete cycle(s) (gate-leak cycles excluded):")
        print(f"  ASR delay (acoustic silence → caption finalized): {avg_asr:.2f}s")
        print(f"  Dead air  (acoustic silence → filler plays):      {avg_filler:.2f}s")
        print(f"  Dead air  (acoustic silence → response plays):    {avg_response:.2f}s")
    print()


if __name__ == "__main__":
    main()

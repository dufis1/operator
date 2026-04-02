"""
parse_latency.py — Parse /tmp/operator.log and print a perceived latency report.

For each prompt cycle it collects:
  - perceived_acoustic_silence_end  (mic goes silent)
  - filler_play_start               (filler clip begins)
  - response_play_start             (bot audio begins)
  - caption_prompt_finalized        (last caption arrives / finalization)

And computes:
  - ASR delay      = caption_prompt_finalized - perceived_acoustic_silence_end
  - Dead air       = filler_play_start        - perceived_acoustic_silence_end
  - Total dead air = response_play_start      - perceived_acoustic_silence_end

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


def parse_ts(line):
    m = _TS_RE.match(line)
    return datetime.strptime(m.group(1), _TS_FMT).timestamp() if m else None


def contains(line, *tokens):
    return all(t in line for t in tokens)


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

        if "TIMING perceived_acoustic_silence_end" in line:
            # Start a fresh cycle bucket on each acoustic silence
            current = {"acoustic_silence": ts}

        elif "TIMING perceived_speech_start" in line:
            # New speech — reset any open cycle (user started talking again)
            current = {}

        elif "TIMING filler_play_start" in line:
            if "acoustic_silence" in current and "filler" not in current:
                current["filler"] = ts

        elif "TIMING response_play_start" in line:
            if "acoustic_silence" in current and "response" not in current:
                current["response"] = ts
                # Cycle is complete enough to record
                cycles.append(dict(current))
                current = {}

        elif ("TIMING caption_prompt_finalized" in line or
              "TIMING prompt_finalized" in line):
            if "acoustic_silence" in current:
                current.setdefault("finalized", ts)

    if not cycles:
        print("No complete perceived-latency cycles found in log.")
        print(f"(checked: {LOG_PATH})")
        print("\nMake sure the bot ran with this version and that you spoke at least one prompt.")
        return

    # Header
    col = "{:<6}  {:>10}  {:>10}  {:>10}  {:>10}"
    print()
    print(col.format("Cycle", "ASR delay", "To filler", "To response", "Prompt"))
    print(col.format("", "(s)", "(s)", "(s)", ""))
    print("-" * 60)

    for i, c in enumerate(cycles, 1):
        acoustic = c["acoustic_silence"]
        asr = f"{c['finalized'] - acoustic:.2f}" if "finalized" in c else "  n/a"
        filler = f"{c['filler'] - acoustic:.2f}" if "filler" in c else "  n/a"
        response = f"{c['response'] - acoustic:.2f}" if "response" in c else "  n/a"
        print(col.format(i, asr, filler, response, ""))

    print()
    # Averages over cycles that have all three values
    complete = [c for c in cycles if all(k in c for k in ("finalized", "filler", "response"))]
    if complete:
        n = len(complete)
        avg_asr = sum(c["finalized"] - c["acoustic_silence"] for c in complete) / n
        avg_filler = sum(c["filler"] - c["acoustic_silence"] for c in complete) / n
        avg_response = sum(c["response"] - c["acoustic_silence"] for c in complete) / n
        print(f"Averages over {n} complete cycle(s):")
        print(f"  ASR delay (acoustic silence → caption finalized): {avg_asr:.2f}s")
        print(f"  Dead air  (acoustic silence → filler plays):      {avg_filler:.2f}s")
        print(f"  Dead air  (acoustic silence → response plays):    {avg_response:.2f}s")
    print()


if __name__ == "__main__":
    main()

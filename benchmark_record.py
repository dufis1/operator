"""Record benchmark clips from mic — one continuous capture, then split by silence."""
import sounddevice as sd
import numpy as np
import soundfile as sf
import os

SAMPLE_RATE = 16000
DURATION = 55  # seconds — enough for 6 lines with pauses

os.makedirs("benchmark_clips", exist_ok=True)

print("🎙  Recording for 55 seconds — START SPEAKING NOW")
audio = sd.rec(int(DURATION * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
sd.wait()
print("✅ Recording done. Saving...")

# Save full recording
sf.write("benchmark_clips/full_recording.wav", audio, SAMPLE_RATE)

# Split by silence: find segments where RMS > threshold
frame_len = int(0.05 * SAMPLE_RATE)  # 50ms frames
rms = np.array([
    np.sqrt(np.mean(audio[i:i+frame_len]**2))
    for i in range(0, len(audio) - frame_len, frame_len)
])

threshold = 0.015
is_speech = rms > threshold

# Find speech segments (contiguous runs of speech frames)
segments = []
in_seg = False
for i, s in enumerate(is_speech):
    if s and not in_seg:
        start = i
        in_seg = True
    elif not s and in_seg:
        # require at least 0.3s of silence to split
        silence_ahead = not any(is_speech[i:i+int(0.3/0.05)]) if i+int(0.3/0.05) < len(is_speech) else True
        if silence_ahead:
            segments.append((start, i))
            in_seg = False
if in_seg:
    segments.append((start, len(is_speech)))

# Merge segments that are very close (< 0.8s gap) — they're the same utterance
merged = [segments[0]] if segments else []
for seg in segments[1:]:
    gap = (seg[0] - merged[-1][1]) * 0.05
    if gap < 0.8:
        merged[-1] = (merged[-1][0], seg[1])
    else:
        merged.append(seg)

# Save each segment with 0.1s padding
ground_truth = [
    "Operator, what time is the standup tomorrow?",
    "Can you summarize the Q3 revenue numbers?",
    "Shopify's API rate limit is 40 requests per second.",
    "Schedule a follow-up with Priya for next Thursday at 2:30.",
    "Operator. What were the action items from last week?",
    "The latency p99 dropped from 450 milliseconds to 210 after the Redis migration.",
]

print(f"\nFound {len(merged)} utterances (expected 6)")
for i, (start, end) in enumerate(merged):
    pad = int(0.1 / 0.05)  # 0.1s padding
    s = max(0, start - pad) * frame_len
    e = min(len(audio), (end + pad) * frame_len)
    clip = audio[s:e]
    fname = f"benchmark_clips/clip_{i+1:02d}.wav"
    sf.write(fname, clip, SAMPLE_RATE)
    gt = ground_truth[i] if i < len(ground_truth) else "???"
    dur = len(clip) / SAMPLE_RATE
    print(f"  {fname} ({dur:.1f}s) — \"{gt}\"")

# Save ground truth
with open("benchmark_clips/ground_truth.txt", "w") as f:
    for i, gt in enumerate(ground_truth):
        f.write(f"clip_{i+1:02d}.wav|{gt}\n")

print("\n✅ Clips saved to benchmark_clips/")

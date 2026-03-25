#!/usr/bin/env python3
"""Quick validation of API keys: OpenAI (gpt-4o-mini) + 3 STT providers."""

import os
import sys
import tempfile
import wave
import struct
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

passed = 0
failed = 0

def ok(name):
    global passed
    passed += 1
    print(f"  ✓ {name}")

def fail(name, err):
    global failed
    failed += 1
    print(f"  ✗ {name}: {err}")

# Generate a tiny WAV file with a short sine tone for STT tests
def make_test_wav():
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sample_rate = 16000
    duration = 1.0
    samples = []
    import math
    for i in range(int(sample_rate * duration)):
        val = int(32767 * 0.5 * math.sin(2 * math.pi * 440 * i / sample_rate))
        samples.append(val)
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return tmp.name

print("\n── Testing API Keys ──\n")

# 1. OpenAI — gpt-4o-mini
print("[OpenAI gpt-4o-mini]")
try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Say 'hello' and nothing else."}],
        max_tokens=5,
    )
    text = resp.choices[0].message.content.strip()
    ok(f"Response: {text}")
except Exception as e:
    fail("gpt-4o-mini", e)

# 2. Deepgram Nova-3
print("\n[Deepgram Nova-3]")
try:
    from deepgram import DeepgramClient
    wav_path = make_test_wav()
    dg = DeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY"))
    with open(wav_path, "rb") as f:
        audio_data = f.read()
    resp = dg.listen.v1.media.transcribe_file(
        request=audio_data,
        model="nova-3",
        smart_format=True,
    )
    ok(f"Returned transcript (empty expected for sine tone)")
    os.unlink(wav_path)
except Exception as e:
    fail("Deepgram", e)

# 3. AssemblyAI
print("\n[AssemblyAI]")
try:
    import assemblyai as aai
    aai.settings.api_key = os.getenv("ASSEMBLYAI_API_KEY")
    wav_path = make_test_wav()
    config = aai.TranscriptionConfig(speech_models=["universal-3-pro"])
    transcript = aai.Transcriber(config=config).transcribe(wav_path)
    if transcript.status == aai.TranscriptStatus.error:
        fail("AssemblyAI", transcript.error)
    else:
        ok(f"Status: {transcript.status}")
    os.unlink(wav_path)
except Exception as e:
    fail("AssemblyAI", e)

# 4. Speechmatics
print("\n[Speechmatics]")
try:
    from speechmatics.batch import AsyncClient, TranscriptionConfig
    wav_path = make_test_wav()

    async def run_speechmatics():
        client = AsyncClient(api_key=os.getenv("SPEECHMATICS_API_KEY"))
        async with client:
            transcript = await client.transcribe(
                wav_path,
                transcription_config=TranscriptionConfig(language="en"),
            )
            return transcript

    result = asyncio.run(run_speechmatics())
    ok(f"Transcription completed")
    os.unlink(wav_path)
except Exception as e:
    fail("Speechmatics", e)

print(f"\n── Results: {passed} passed, {failed} failed ──\n")
sys.exit(1 if failed else 0)

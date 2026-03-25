# python:3.11-slim (Debian Bookworm) ships Python 3.11 built-in — no PPA needed.
# Equivalent to Ubuntu 22.04 for our purposes (same Linux ABI, same packages).
# --platform linux/amd64: build for the x86_64 droplet even when building on ARM64 Mac.
FROM --platform=linux/amd64 python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# ── System dependencies ───────────────────────────────────────────────────────
# pulseaudio / pulseaudio-utils: virtual audio server + pactl/parec CLI
# ffmpeg: audio decoding required by faster-whisper
# mpv: audio playback used by TTSClient (plays TTS stream to PulseAudio sink)
# libportaudio2: PortAudio backend required by sounddevice
RUN apt-get update && apt-get install -y --no-install-recommends \
        pulseaudio \
        pulseaudio-utils \
        ffmpeg \
        mpv \
        libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

# PulseAudio writes its socket and PID file here.
# Must be set before any pactl/paplay calls.
ENV PULSE_RUNTIME_PATH=/tmp/pulse

WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
# Install cross-platform packages only — macOS-only packages (rumps, pyobjc-*)
# are intentionally excluded.
RUN pip3 install --no-cache-dir \
    python-dotenv \
    openai \
    elevenlabs \
    faster-whisper \
    numpy \
    soundfile \
    sounddevice \
    playwright \
    google-auth-oauthlib \
    google-api-python-client

# Install Playwright's Chromium browser + all required system libraries
RUN python3 -m playwright install chromium --with-deps

# ── Application code + startup scripts ───────────────────────────────────────
COPY pipeline/         ./pipeline/
COPY connectors/       ./connectors/
COPY assets/           ./assets/
COPY docker/pulse_setup.sh ./pulse_setup.sh
RUN chmod +x pulse_setup.sh

# Smoke-test: verify all pipeline imports succeed at build time
RUN python3 -c "from pipeline import audio, wake, conversation, llm, tts; print('pipeline imports ok')"

CMD ["python3", "-c", "print('Operator container ready — no entrypoint configured yet')"]

#!/bin/bash
# Sets up PulseAudio virtual audio routing for Operator in Docker.
#
# Creates two virtual sinks:
#   MeetingOutput  — TTS audio is played here (mpv → MeetingOutput).
#                    Chrome uses MeetingOutput.monitor as its microphone source
#                    so meeting participants hear Operator's voice.
#
#   MeetingInput   — Chrome outputs meeting audio here.
#                    Python reads MeetingInput.monitor to feed Whisper STT.
#
# A loopback module routes MeetingOutput.monitor → MeetingInput.
# In production the loopback is unused (Chrome is the real audio bridge);
# it exists so the audio routing test (step 3.5) can verify the plumbing
# without needing a live meeting.
#
# Usage: source /app/pulse_setup.sh   (or bash /app/pulse_setup.sh)

set -e

# ── PulseAudio daemon config ──────────────────────────────────────────────────
# allow-module-loading: required so pactl can load modules at runtime.
# exit-idle-time -1:    prevent auto-exit when the container is idle.
mkdir -p /root/.config/pulse
cat > /root/.config/pulse/daemon.conf << 'EOF'
allow-module-loading = yes
exit-idle-time = -1
EOF

# ── Start PulseAudio daemon ───────────────────────────────────────────────────
pulseaudio --start --log-target=stderr

# Wait up to 5s for PulseAudio to accept commands
for i in $(seq 1 10); do
    pactl info > /dev/null 2>&1 && break
    sleep 0.5
done

pactl info > /dev/null 2>&1 || { echo "ERROR: PulseAudio failed to start"; exit 1; }

# ── Create virtual audio devices ─────────────────────────────────────────────
pactl load-module module-null-sink \
    sink_name=MeetingOutput \
    sink_properties=device.description='"MeetingOutput"'

pactl load-module module-null-sink \
    sink_name=MeetingInput \
    sink_properties=device.description='"MeetingInput"'

# Loopback: MeetingOutput.monitor → MeetingInput
# latency_msec=50: low latency for near-real-time routing verification.
pactl load-module module-loopback \
    source=MeetingOutput.monitor \
    sink=MeetingInput \
    latency_msec=50

echo "PulseAudio virtual audio routing ready."
echo "  Sinks:"
pactl list short sinks
echo "  Sources:"
pactl list short sources

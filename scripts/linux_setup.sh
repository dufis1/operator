#!/usr/bin/env bash
# Operator — Linux local audio setup
# Creates PulseAudio virtual devices required for meeting audio routing.
# Run once per session (devices reset on reboot or when PulseAudio restarts).
set -e

pactl load-module module-null-sink sink_name=MeetingOutput sink_properties=device.description=MeetingOutput
pactl load-module module-null-sink sink_name=MeetingInput sink_properties=device.description=MeetingInput
pactl load-module module-virtual-source source_name=VirtualMic master=MeetingOutput.monitor source_properties=device.description=VirtualMic

pactl set-default-sink MeetingInput
pactl set-default-source VirtualMic

echo "Operator: PulseAudio virtual devices ready."
echo "  Audio IN  (meeting → Operator): parec --device=MeetingInput.monitor"
echo "  Audio OUT (Operator → meeting): mpv --audio-device=pulse/MeetingOutput"

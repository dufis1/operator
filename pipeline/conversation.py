"""
Conversation state machine for Operator.

Tracks the current named state (idle / listening / thinking / speaking) and
notifies a caller-supplied callback whenever the state changes. No macOS
imports — the caller (app.py) is responsible for translating states to icons.
"""
import logging

log = logging.getLogger(__name__)

# How long to stay in conversation mode with no follow-up before returning to idle.
CONVERSATION_TIMEOUT = 20.0  # seconds


class ConversationState:
    """Tracks Operator's current conversational state.

    Pass an on_state_change callback to receive notifications:
        on_state_change(state: str, label: str)

    States:
        "idle"      — waiting for the wake phrase
        "listening" — wake phrase heard, waiting for a prompt
        "thinking"  — prompt received, LLM request in flight
        "speaking"  — LLM replied, TTS playing
    """

    def __init__(self, on_state_change=None):
        self._state = "idle"
        self._on_state_change = on_state_change

    @property
    def state(self):
        return self._state

    def _transition(self, new_state, label):
        self._state = new_state
        log.debug(f"ConversationState → {new_state}")
        if self._on_state_change:
            self._on_state_change(new_state, label)

    def set_idle(self, label="Listening for 'operator'..."):
        self._transition("idle", label)

    def set_listening(self, label="Listening for prompt..."):
        self._transition("listening", label)

    def set_thinking(self):
        self._transition("thinking", "Thinking...")

    def set_speaking(self):
        self._transition("speaking", "Speaking...")

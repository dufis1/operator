"""
Operator — AI Meeting Participant
Runs in the macOS menu bar. Delegates pipeline work to AgentRunner.
"""
import os
import subprocess
import threading
import time
import logging
import soundfile as sf
import numpy as np
import rumps
from PyObjCTools.AppHelper import callAfter
import config
from caldav_poller import CalDAVPoller
from connectors.macos_adapter import MacOSAdapter
from pipeline.audio import SAMPLE_RATE
from pipeline.runner import AgentRunner

logging.basicConfig(
    filename="/tmp/operator.log",
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)
# Silence noisy HTTP debug logs from API clients
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("elevenlabs").setLevel(logging.WARNING)

BLACKHOLE_DEVICE = "coreaudio/BlackHole2ch_UID"

# Maps conversation state names → menu bar icons
STATE_ICONS = {
    "idle":      "⚪",
    "listening": "🔴",
    "thinking":  "🟡",
    "speaking":  "🟢",
}

AUDIO_CAPTURE_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio_capture")


class OperatorApp(rumps.App):
    def __init__(self):
        super().__init__("⚪", quit_button=None)

        self.status_item = rumps.MenuItem("Loading model...")
        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("Request Audio Permission", callback=self.request_audio_permission),
            rumps.MenuItem("Test Capture (10s)", callback=self.test_capture),
            None,
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]

        self.connector = None
        self.runner = None
        self._calendar_poller = None

        threading.Thread(target=self._load_and_start, daemon=True).start()

    # ------------------------------------------------------------------
    # Thread-safe UI updates
    # ------------------------------------------------------------------

    def _set_state(self, icon, status_text=None):
        """Update menu bar icon and optional status text from any thread."""
        def update():
            self.title = icon
            if status_text is not None:
                self.status_item.title = status_text
        callAfter(update)
        log.debug(f"State → {icon} {status_text or ''}")

    def _on_conv_state_change(self, state, label):
        """Translate a pipeline conversation state into a menu bar icon update."""
        self._set_state(STATE_ICONS[state], label)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _check_api_keys(self):
        missing = []
        if not config.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")
        if not config.ELEVENLABS_API_KEY:
            missing.append("ELEVENLABS_API_KEY")
        if missing:
            return f"Missing API keys: {', '.join(missing)}. Add them to your .env file."
        return None

    def _load_and_start(self):
        key_error = self._check_api_keys()
        if key_error:
            self._set_state("⚠️", key_error)
            return

        self._set_state("⚪", "Loading...")
        self.connector = MacOSAdapter()
        self.runner = AgentRunner(
            connector=self.connector,
            tts_output_device=BLACKHOLE_DEVICE,
            on_state_change=self._on_conv_state_change,
        )

        self._calendar_poller = CalDAVPoller(self.connector)
        self._calendar_poller.start()

        self.runner.run()  # blocks until stopped

    # ------------------------------------------------------------------
    # Menu actions
    # ------------------------------------------------------------------

    def test_capture(self, _):
        log.debug("test_capture: called")
        if not os.path.exists(AUDIO_CAPTURE_HELPER):
            self.status_item.title = f"❌ Helper not found: {AUDIO_CAPTURE_HELPER}"
            return
        self.status_item.title = "🔴 Capturing 10s — play audio now..."
        threading.Thread(target=self._do_capture, daemon=True).start()

    def _do_capture(self):
        CAPTURE_SECONDS = 10
        OUTPUT_PATH = "/tmp/operator_test_capture.wav"
        log.debug("_do_capture: launching Swift helper")

        try:
            proc = subprocess.Popen(
                [AUDIO_CAPTURE_HELPER],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
            )
        except OSError as e:
            log.debug(f"_do_capture: failed to launch helper: {e}")
            self.status_item.title = f"❌ Helper launch failed: {e}"
            return

        def read_stderr():
            for line in proc.stderr:
                log.debug(f"[swift] {line.decode().rstrip()}")
        threading.Thread(target=read_stderr, daemon=True).start()

        bytes_needed = SAMPLE_RATE * 4 * CAPTURE_SECONDS
        data = b""
        while len(data) < bytes_needed:
            chunk = proc.stdout.read(min(4096, bytes_needed - len(data)))
            if not chunk:
                log.debug(f"_do_capture: helper stopped early after {len(data)} bytes")
                break
            data += chunk

        log.debug("_do_capture: closing stdin to stop helper")
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.debug("_do_capture: helper didn't exit, terminating")
            proc.terminate()

        log.debug(f"_do_capture: helper exited with code {proc.returncode}")

        if not data:
            self.status_item.title = "❌ No audio captured"
            return

        audio = np.frombuffer(data, dtype=np.float32)
        sf.write(OUTPUT_PATH, audio, SAMPLE_RATE)
        duration = len(audio) / SAMPLE_RATE
        log.debug(f"_do_capture: saved {duration:.1f}s to {OUTPUT_PATH}")
        self.status_item.title = f"✅ Captured {duration:.1f}s → {OUTPUT_PATH}"

    def request_audio_permission(self, _):
        """Launch the helper briefly to trigger the Screen Recording permission prompt."""
        try:
            proc = subprocess.Popen(
                [AUDIO_CAPTURE_HELPER],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
            )
            time.sleep(2)
            proc.stdin.close()
            proc.wait(timeout=5)
            self.status_item.title = "Permission requested — check System Settings"
        except Exception as e:
            self.status_item.title = f"Permission error: {e}"

    def quit_app(self, _):
        if self.runner:
            self.runner.stop()
        if self._calendar_poller:
            self._calendar_poller.stop()
        elif self.connector:
            self.connector.leave()
        rumps.quit_application()


if __name__ == "__main__":
    OperatorApp().run()

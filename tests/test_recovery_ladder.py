"""
Tests for TCC error handling & recovery ladder in AgentRunner.

Exercises every error path: codesign signature verification, exit codes 0/1/3/4,
tccutil reset retry, and the full recovery ladder (exit 4 → tccutil → retry → success).

Run:
    python tests/test_recovery_ladder.py
"""
import io
import logging
import os
import stat
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])  # repo root

import pipeline.runner as runner_mod
from pipeline.runner import AgentRunner

log = logging.getLogger("pipeline.runner")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class LogCapture(logging.Handler):
    """Collects log records for assertion."""
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self, level=None):
        return [
            r.getMessage() for r in self.records
            if level is None or r.levelno == level
        ]

    def all_messages(self):
        return [r.getMessage() for r in self.records]

    def clear(self):
        self.records.clear()


def make_stub(path, script="#!/bin/bash\nexit 0"):
    """Write a stub shell script and make it executable."""
    with open(path, "w") as f:
        f.write(script)
    os.chmod(path, stat.S_IRWXU)


def make_runner():
    """Build a minimal AgentRunner without calling __init__."""
    r = AgentRunner.__new__(AgentRunner)
    r.connector = None
    r._tts_output_device = None
    r._on_state_change = None
    r._stop_event = threading.Event()
    r._transcript_lines = []
    r._transcript_lock = threading.Lock()
    r._capture_proc = None
    r._tcc_retried = False

    # Minimal audio stand-in
    class FakeAudio:
        capturing = True
        def feed_audio(self, chunk):
            self._fed = getattr(self, "_fed", b"") + chunk
    r.audio = FakeAudio()
    return r


capture = LogCapture()
log.addHandler(capture)
log.setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# Group 1: Signature verification
# ---------------------------------------------------------------------------

def test_signature_ok():
    """Correct codesign identity → debug log 'signature OK'."""
    capture.clear()
    with tempfile.TemporaryDirectory() as d:
        binary = os.path.join(d, "audio_capture")
        make_stub(binary)
        old_base = runner_mod._BASE
        runner_mod._BASE = d

        orig_run = subprocess.run
        def fake_run(cmd, **kw):
            if cmd[0] == "codesign":
                r = subprocess.CompletedProcess(cmd, 0, "", "Identifier=com.operator.audio-capture")
                return r
            return orig_run(cmd, **kw)

        subprocess.run = fake_run
        try:
            AgentRunner._verify_audio_capture_signature()
        finally:
            subprocess.run = orig_run
            runner_mod._BASE = old_base

    assert any("signature OK" in m for m in capture.messages(logging.DEBUG)), \
        f"Expected 'signature OK' in debug logs, got: {capture.all_messages()}"
    print("✅ signature OK")


def test_signature_wrong_identity():
    """Wrong codesign identity → warning about unexpected identity."""
    capture.clear()
    with tempfile.TemporaryDirectory() as d:
        binary = os.path.join(d, "audio_capture")
        make_stub(binary)
        old_base = runner_mod._BASE
        runner_mod._BASE = d

        orig_run = subprocess.run
        def fake_run(cmd, **kw):
            if cmd[0] == "codesign":
                return subprocess.CompletedProcess(cmd, 0, "", "Identifier=com.wrong.identity")
            return orig_run(cmd, **kw)

        subprocess.run = fake_run
        try:
            AgentRunner._verify_audio_capture_signature()
        finally:
            subprocess.run = orig_run
            runner_mod._BASE = old_base

    assert any("unexpected identity" in m for m in capture.messages(logging.WARNING)), \
        f"Expected 'unexpected identity' warning, got: {capture.all_messages()}"
    print("✅ signature wrong identity")


def test_signature_none():
    """No valid signature → warning about no valid signature."""
    capture.clear()
    with tempfile.TemporaryDirectory() as d:
        binary = os.path.join(d, "audio_capture")
        make_stub(binary)
        old_base = runner_mod._BASE
        runner_mod._BASE = d

        orig_run = subprocess.run
        def fake_run(cmd, **kw):
            if cmd[0] == "codesign":
                return subprocess.CompletedProcess(cmd, 1, "", "code object is not signed at all")
            return orig_run(cmd, **kw)

        subprocess.run = fake_run
        try:
            AgentRunner._verify_audio_capture_signature()
        finally:
            subprocess.run = orig_run
            runner_mod._BASE = old_base

    assert any("no valid signature" in m for m in capture.messages(logging.WARNING)), \
        f"Expected 'no valid signature' warning, got: {capture.all_messages()}"
    print("✅ signature none")


def test_signature_missing_binary():
    """Missing binary → warning about binary not found."""
    capture.clear()
    with tempfile.TemporaryDirectory() as d:
        old_base = runner_mod._BASE
        runner_mod._BASE = d  # no audio_capture file here
        try:
            AgentRunner._verify_audio_capture_signature()
        finally:
            runner_mod._BASE = old_base

    assert any("not found" in m for m in capture.messages(logging.WARNING)), \
        f"Expected 'not found' warning, got: {capture.all_messages()}"
    print("✅ signature missing binary")


# ---------------------------------------------------------------------------
# Group 2: Exit code handling
# ---------------------------------------------------------------------------

def _run_exit_code_test(exit_code, script=None):
    """Create a stub that exits with the given code, run _audio_read_loop."""
    if script is None:
        script = f"#!/bin/bash\nexit {exit_code}"

    with tempfile.TemporaryDirectory() as d:
        stub = os.path.join(d, "stub")
        make_stub(stub, script)

        r = make_runner()
        r._capture_proc = subprocess.Popen(
            [stub], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        r._audio_read_loop()
        return r


def test_exit_0():
    """Exit 0 → warning: capture process stopped."""
    capture.clear()
    _run_exit_code_test(0)
    assert any("capture process stopped" in m for m in capture.messages(logging.WARNING)), \
        f"Expected 'capture process stopped' warning, got: {capture.all_messages()}"
    print("✅ exit code 0")


def test_exit_1():
    """Exit 1 → error: exited with code 1."""
    capture.clear()
    _run_exit_code_test(1)
    assert any("exited with code 1" in m for m in capture.messages(logging.ERROR)), \
        f"Expected 'exited with code 1' error, got: {capture.all_messages()}"
    print("✅ exit code 1")


def test_exit_3():
    """Exit 3 → error: Screen Recording permission denied."""
    capture.clear()
    _run_exit_code_test(3)
    assert any("Screen Recording permission denied" in m for m in capture.messages(logging.ERROR)), \
        f"Expected 'Screen Recording permission denied' error, got: {capture.all_messages()}"
    print("✅ exit code 3 (permission denied)")


def test_exit_4_first_time():
    """Exit 4 (first attempt) → tccutil reset + retry with _tcc_retried=True."""
    capture.clear()
    retried = []

    with tempfile.TemporaryDirectory() as d:
        stub = os.path.join(d, "stub")
        make_stub(stub, "#!/bin/bash\nexit 4")

        r = make_runner()
        r._tcc_retried = False

        # Patch _start_capture to just record the call (prevent actual recursion)
        orig_start = r._start_capture
        def fake_start(_tcc_retried=False, **kw):
            retried.append(_tcc_retried)
        r._start_capture = fake_start

        # Patch subprocess.run to intercept tccutil
        tccutil_calls = []
        orig_run = subprocess.run
        def fake_run(cmd, **kw):
            if cmd[0] == "tccutil":
                tccutil_calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0)
            return orig_run(cmd, **kw)

        subprocess.run = fake_run
        try:
            r._capture_proc = subprocess.Popen(
                [stub], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            r._audio_read_loop()
        finally:
            subprocess.run = orig_run

    assert len(tccutil_calls) == 1, f"Expected 1 tccutil call, got {len(tccutil_calls)}"
    assert tccutil_calls[0] == ["tccutil", "reset", "ScreenCapture"]
    assert retried == [True], f"Expected retry with _tcc_retried=True, got {retried}"
    assert any("resetting TCC cache" in m for m in capture.all_messages())
    print("✅ exit code 4 (first time — tccutil + retry)")


def test_exit_4_second_time():
    """Exit 4 (after retry) → error: restart Operator / restart your Mac."""
    capture.clear()
    with tempfile.TemporaryDirectory() as d:
        stub = os.path.join(d, "stub")
        make_stub(stub, "#!/bin/bash\nexit 4")

        r = make_runner()
        r._tcc_retried = True  # already retried once

        r._capture_proc = subprocess.Popen(
            [stub], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        r._audio_read_loop()

    assert any("restart Operator" in m for m in capture.messages(logging.ERROR)), \
        f"Expected 'restart Operator' error, got: {capture.all_messages()}"
    print("✅ exit code 4 (second time — escalation)")


# ---------------------------------------------------------------------------
# Group 3: Full recovery ladder
# ---------------------------------------------------------------------------

def test_full_recovery():
    """Exit 4 → tccutil reset → retry → success (exit 0 with audio data)."""
    capture.clear()

    with tempfile.TemporaryDirectory() as d:
        sentinel = os.path.join(d, "retried")
        stub = os.path.join(d, "stub")
        # First call: exit 4.  Second call (after sentinel exists): write PCM data, exit 0.
        make_stub(stub, f"""#!/bin/bash
if [ -f "{sentinel}" ]; then
    # Write 4096 bytes of zeros (simulating audio) to stdout
    dd if=/dev/zero bs=4096 count=1 2>/dev/null
    exit 0
else
    touch "{sentinel}"
    exit 4
fi
""")

        r = make_runner()
        r._tcc_retried = False

        # Patch subprocess.run to intercept tccutil
        tccutil_calls = []
        orig_run = subprocess.run
        def fake_run(cmd, **kw):
            if cmd[0] == "tccutil":
                tccutil_calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0)
            return orig_run(cmd, **kw)

        # Patch _verify_audio_capture_signature to no-op (no binary to sign-check)
        orig_verify = AgentRunner._verify_audio_capture_signature
        AgentRunner._verify_audio_capture_signature = staticmethod(lambda: None)

        # Patch connector.get_audio_stream to launch our stub
        class FakeConnector:
            def __init__(self, stub_path):
                self._stub = stub_path
            def get_audio_stream(self):
                return subprocess.Popen(
                    [self._stub], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )

        r.connector = FakeConnector(stub)
        subprocess.run = fake_run
        try:
            # Launch _start_capture which will recurse on exit 4
            r._start_capture()
            # Wait for the read loops to finish
            import time
            deadline = time.time() + 5
            while r.audio.capturing and time.time() < deadline:
                time.sleep(0.1)
        finally:
            subprocess.run = orig_run
            AgentRunner._verify_audio_capture_signature = orig_verify

    assert len(tccutil_calls) == 1, f"Expected 1 tccutil call, got {len(tccutil_calls)}"
    fed = getattr(r.audio, "_fed", b"")
    assert len(fed) > 0, "Expected audio data to be fed after recovery"
    assert any("TCC ScreenCapture cache reset" in m for m in capture.all_messages()), \
        f"Expected TCC reset log, got: {capture.all_messages()}"
    print("✅ full recovery ladder (exit 4 → tccutil → retry → audio flows)")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Group 1: Signature verification ===")
    test_signature_ok()
    test_signature_wrong_identity()
    test_signature_none()
    test_signature_missing_binary()

    print("\n=== Group 2: Exit code handling ===")
    test_exit_0()
    test_exit_1()
    test_exit_3()
    test_exit_4_first_time()
    test_exit_4_second_time()

    print("\n=== Group 3: Full recovery ladder ===")
    test_full_recovery()

    print("\n✅ All recovery ladder tests passed")

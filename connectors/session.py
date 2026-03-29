"""
Session recovery utilities for Google Meet connectors.

Provides detection of logged-out / revoked-session states, cookie injection
from auth_state.json, and a JoinStatus primitive for browser→runner signalling.
"""
import json
import logging
import os
import threading

log = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class JoinStatus:
    """Thread-safe join result communicated from browser thread to runner."""

    def __init__(self):
        self.ready = threading.Event()
        self.success = False
        self.failure_reason = None   # str | None
        self.session_recovered = False

    def signal_success(self, recovered=False):
        self.success = True
        self.session_recovered = recovered
        self.ready.set()

    def signal_failure(self, reason):
        self.success = False
        self.failure_reason = reason
        self.ready.set()


def detect_page_state(page):
    """Classify what Google Meet is showing after navigation.

    Returns one of: "pre_join", "logged_out", "cant_join", "unknown".
    """
    url = page.url

    # Redirected to Google sign-in
    if "accounts.google.com" in url:
        log.info(f"session: detected logged-out state (URL: {url})")
        return "logged_out"

    # Check for "can't join" text on the page
    try:
        cant_join = page.locator("text=You can't join this video call")
        if cant_join.count() > 0:
            # Distinguish auth failure from host controls
            # by checking if browser has Google session cookies
            try:
                cookies = page.context.cookies()
                has_session = any(
                    c.get("name") == "SID" and ".google.com" in c.get("domain", "")
                    for c in cookies
                )
            except Exception:
                has_session = False

            if not has_session:
                log.info("session: 'can't join' but no Google session cookie — treating as logged_out")
                return "logged_out"

            log.info("session: detected 'can't join' state (authenticated — likely host controls)")
            return "cant_join"
    except Exception:
        pass

    # Check for join buttons — indicates normal pre-join screen
    for label in ["Join now", "Ask to join", "Switch here"]:
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count() > 0:
                return "pre_join"
        except Exception:
            continue

    # Check for re-auth challenge on meet.google.com itself
    try:
        sign_in = page.locator("text=Sign in")
        if sign_in.count() > 0:
            log.info("session: detected sign-in prompt on Meet page")
            return "logged_out"
    except Exception:
        pass

    log.info(f"session: unknown page state (URL: {url})")
    return "unknown"


def validate_auth_state(path):
    """Load auth_state.json and check it contains a .google.com SID cookie.

    Returns the parsed dict on success, None on failure.
    Only validates structure — server-side revocation is caught after injection.
    """
    if not path:
        return None
    try:
        with open(path) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log.warning(f"session: cannot load auth state from {path}: {e}")
        return None

    cookies = state.get("cookies", [])
    has_sid = any(
        c.get("name") == "SID" and ".google.com" in c.get("domain", "")
        for c in cookies
    )
    if not has_sid:
        log.warning("session: auth_state.json has no .google.com SID cookie")
        return None

    log.info(f"session: auth_state.json valid ({len(cookies)} cookies)")
    return state


def inject_cookies(context, auth_state):
    """Inject .google.com cookies from auth_state into a Playwright context.

    Returns True on success, False on failure.
    """
    cookies = [
        c for c in auth_state.get("cookies", [])
        if ".google.com" in c.get("domain", "")
    ]
    if not cookies:
        log.warning("session: no .google.com cookies to inject")
        return False

    try:
        context.add_cookies(cookies)
        log.info(f"session: injected {len(cookies)} .google.com cookies")
        return True
    except Exception as e:
        log.error(f"session: cookie injection failed: {e}")
        return False


def save_debug(page, label="debug"):
    """Save a screenshot and HTML dump for diagnosis."""
    debug_dir = os.path.join(_BASE, "debug")
    os.makedirs(debug_dir, exist_ok=True)
    try:
        page.screenshot(path=os.path.join(debug_dir, f"{label}.png"), full_page=True)
        log.info(f"session: screenshot saved to debug/{label}.png")
    except Exception as e:
        log.warning(f"session: screenshot failed: {e}")
    try:
        with open(os.path.join(debug_dir, f"{label}.html"), "w") as f:
            f.write(page.content())
        log.info(f"session: HTML saved to debug/{label}.html")
    except Exception as e:
        log.warning(f"session: HTML dump failed: {e}")

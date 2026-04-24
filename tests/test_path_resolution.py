"""
Unit tests for the path-relocation work across sessions 158–159.

Four user-scoped artifacts (`browser_profile/`, `auth_state.json`, `.env`,
`debug/`) used to be pinned at the repo root via `_BASE`/`_ROOT` walk-ups;
they now live under `~/.brainchild/`.

Covers:
  1. `config.BROWSER_PROFILE_DIR`, `AUTH_STATE_FILE`, `ENV_FILE`, `DEBUG_DIR`
     all resolve to absolute paths under `Path.home() / ".brainchild"`.
  2. `MacOSAdapter` picks up the config's absolute browser-profile path.
  3. `_migrate_legacy_user_artifacts()` moves repo-root legacy copies
     (profile + auth_state + .env) into `~/.brainchild/` on first run.
  4. The shim is a no-op when the destination already exists (doesn't
     clobber the user's active session or API keys).
  5. The shim is a no-op when the source is missing (fresh install).
  6. `.env` migration preserves file contents byte-for-byte.

Approach follows test_config_loader.py — redirect HOME to a tmp dir and
load `config.py` fresh via importlib so the module-level `Path.home()`
calls resolve inside the sandbox.

Run:
    source venv/bin/activate
    python tests/test_path_resolution.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import importlib.util
import shutil
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG_PY = REPO_ROOT / "src" / "brainchild" / "config.py"
REAL_MAIN_PY = REPO_ROOT / "src" / "brainchild" / "__main__.py"


def _sandbox(home: Path, bot: str = "testbot"):
    """Seed a minimal agents/<bot>/config.yaml under the sandbox HOME."""
    agents_root = home / ".brainchild" / "agents"
    (agents_root / bot).mkdir(parents=True)
    (agents_root / bot / "config.yaml").write_text(
        "agent: {name: Test}\nllm: {provider: openai, model: gpt-4o-mini}\n"
    )


def _load_config_with_home(home: Path, bot: str = "testbot"):
    saved = {k: os.environ.get(k) for k in ("HOME", "BRAINCHILD_BOT")}
    try:
        os.environ["HOME"] = str(home)
        os.environ["BRAINCHILD_BOT"] = bot
        spec = importlib.util.spec_from_file_location(f"config_path_test_{id(home)}", REAL_CONFIG_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# Test 1: config paths are absolute and under ~/.brainchild/
# ---------------------------------------------------------------------------

def test_config_paths_absolute_under_home_brainchild():
    tmp = Path(tempfile.mkdtemp())
    try:
        _sandbox(tmp)
        mod = _load_config_with_home(tmp)
        expected = {
            "BROWSER_PROFILE_DIR": str(tmp / ".brainchild" / "browser_profile"),
            "AUTH_STATE_FILE":     str(tmp / ".brainchild" / "auth_state.json"),
            "ENV_FILE":            str(tmp / ".brainchild" / ".env"),
            "DEBUG_DIR":           str(tmp / ".brainchild" / "debug"),
        }
        for name, want in expected.items():
            got = getattr(mod, name)
            assert os.path.isabs(got), f"{name} not absolute: {got!r}"
            assert got == want, f"{name}: expected {want}, got {got}"
        print("PASS  test_config_paths_absolute_under_home_brainchild")
    finally:
        shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Test 2: MacOSAdapter picks up the absolute config path (no walk-up)
# ---------------------------------------------------------------------------

def test_macos_adapter_uses_config_path_directly():
    """After the _BASE walk-up was removed, macos_adapter.BROWSER_PROFILE
    must equal config.BROWSER_PROFILE_DIR byte-for-byte."""
    tmp = Path(tempfile.mkdtemp())
    try:
        _sandbox(tmp)
        saved = {k: os.environ.get(k) for k in ("HOME", "BRAINCHILD_BOT")}
        try:
            os.environ["HOME"] = str(tmp)
            os.environ["BRAINCHILD_BOT"] = "testbot"
            # Force a fresh import of both modules so HOME is honored.
            for m in list(sys.modules):
                if m.startswith("brainchild"):
                    del sys.modules[m]
            from brainchild import config as cfg
            from brainchild.connectors import macos_adapter
            assert macos_adapter.BROWSER_PROFILE == cfg.BROWSER_PROFILE_DIR, \
                f"mismatch: adapter={macos_adapter.BROWSER_PROFILE} vs config={cfg.BROWSER_PROFILE_DIR}"
            assert os.path.isabs(macos_adapter.BROWSER_PROFILE)
            print("PASS  test_macos_adapter_uses_config_path_directly")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    finally:
        shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Migration-shim tests
# ---------------------------------------------------------------------------

def _load_main_module():
    """Import __main__.py as a plain module so we can call the shim directly.

    __main__.py does side-effect-free work at import time (only function defs
    and the Popen patch), so this is safe.
    """
    spec = importlib.util.spec_from_file_location("brainchild_main_under_test", REAL_MAIN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_repo_root(tmp: Path) -> Path:
    """Lay out a fake src/brainchild/ tree inside `tmp` so __main__.py loaded
    from there resolves `Path(__file__).resolve().parent.parent.parent` to
    `tmp`. Returns the path where we should copy __main__.py."""
    target_dir = tmp / "src" / "brainchild"
    target_dir.mkdir(parents=True)
    shutil.copy(REAL_MAIN_PY, target_dir / "__main__.py")
    return target_dir / "__main__.py"


def _load_main_from(fake_main: Path):
    spec = importlib.util.spec_from_file_location(
        f"brainchild_main_shim_{id(fake_main)}", fake_main
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_moves_legacy_artifacts():
    """Legacy profile dir + auth_state + .env at the (fake) repo root all get
    moved to ~/.brainchild/ on first call. Exact byte content preserved for
    files (the .env coverage is critical — real users have live API keys)."""
    tmp = Path(tempfile.mkdtemp())
    try:
        fake_main = _fake_repo_root(tmp)
        # Seed legacy artifacts at the fake repo root.
        legacy_profile = tmp / "browser_profile"
        legacy_profile.mkdir()
        (legacy_profile / "marker").write_text("hello")
        legacy_auth = tmp / "auth_state.json"
        legacy_auth.write_text('{"cookies": []}')
        legacy_env = tmp / ".env"
        env_contents = "OPENAI_API_KEY=sk-test\nANTHROPIC_API_KEY=sk-ant-test\n"
        legacy_env.write_text(env_contents)

        # Redirect HOME so the shim writes into the sandbox.
        fake_home = tmp / "home"
        fake_home.mkdir()
        saved_home = os.environ.get("HOME")
        try:
            os.environ["HOME"] = str(fake_home)
            mod = _load_main_from(fake_main)
            mod._migrate_legacy_user_artifacts()

            migrated_profile = fake_home / ".brainchild" / "browser_profile"
            migrated_auth = fake_home / ".brainchild" / "auth_state.json"
            migrated_env = fake_home / ".brainchild" / ".env"
            assert migrated_profile.is_dir(), f"profile not moved: {migrated_profile}"
            assert (migrated_profile / "marker").read_text() == "hello"
            assert migrated_auth.is_file()
            assert migrated_auth.read_text() == '{"cookies": []}'
            assert migrated_env.is_file(), f".env not moved: {migrated_env}"
            assert migrated_env.read_text() == env_contents, \
                f".env contents changed during migration"
            assert not legacy_profile.exists(), "legacy profile should be gone"
            assert not legacy_auth.exists(), "legacy auth_state should be gone"
            assert not legacy_env.exists(), "legacy .env should be gone"
            print("PASS  test_migration_moves_legacy_artifacts")
        finally:
            if saved_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved_home
    finally:
        shutil.rmtree(tmp)


def test_migration_preserves_existing_target():
    """If ~/.brainchild/browser_profile/ already exists, don't clobber it —
    leave the legacy copy in place so the user can reconcile manually."""
    tmp = Path(tempfile.mkdtemp())
    try:
        fake_main = _fake_repo_root(tmp)
        # Legacy copy with distinct content.
        legacy_profile = tmp / "browser_profile"
        legacy_profile.mkdir()
        (legacy_profile / "marker").write_text("legacy")

        fake_home = tmp / "home"
        (fake_home / ".brainchild" / "browser_profile").mkdir(parents=True)
        (fake_home / ".brainchild" / "browser_profile" / "marker").write_text("existing")

        saved_home = os.environ.get("HOME")
        try:
            os.environ["HOME"] = str(fake_home)
            mod = _load_main_from(fake_main)
            mod._migrate_legacy_user_artifacts()

            # Legacy copy still present (not moved, not deleted).
            assert legacy_profile.is_dir()
            assert (legacy_profile / "marker").read_text() == "legacy"
            # Existing user copy untouched.
            user_marker = fake_home / ".brainchild" / "browser_profile" / "marker"
            assert user_marker.read_text() == "existing"
            print("PASS  test_migration_preserves_existing_target")
        finally:
            if saved_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved_home
    finally:
        shutil.rmtree(tmp)


def test_migration_preserves_existing_env():
    """If ~/.brainchild/.env already has user content, a repo-root .env must
    NOT overwrite it — the home copy is authoritative, legacy stays put for
    manual reconciliation (protects live API keys)."""
    tmp = Path(tempfile.mkdtemp())
    try:
        fake_main = _fake_repo_root(tmp)
        legacy_env = tmp / ".env"
        legacy_env.write_text("OPENAI_API_KEY=stale-legacy-key\n")

        fake_home = tmp / "home"
        (fake_home / ".brainchild").mkdir(parents=True)
        user_env = fake_home / ".brainchild" / ".env"
        user_env.write_text("OPENAI_API_KEY=live-home-key\n")

        saved_home = os.environ.get("HOME")
        try:
            os.environ["HOME"] = str(fake_home)
            mod = _load_main_from(fake_main)
            mod._migrate_legacy_user_artifacts()

            assert user_env.read_text() == "OPENAI_API_KEY=live-home-key\n", \
                "home .env was overwritten — would destroy user API keys"
            assert legacy_env.exists(), "legacy .env should remain for manual review"
            assert legacy_env.read_text() == "OPENAI_API_KEY=stale-legacy-key\n"
            print("PASS  test_migration_preserves_existing_env")
        finally:
            if saved_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved_home
    finally:
        shutil.rmtree(tmp)


def test_migration_noop_on_fresh_install():
    """No legacy files → shim is silent, creates no artifacts beyond the
    home_dir directory itself."""
    tmp = Path(tempfile.mkdtemp())
    try:
        fake_main = _fake_repo_root(tmp)
        fake_home = tmp / "home"
        fake_home.mkdir()

        saved_home = os.environ.get("HOME")
        try:
            os.environ["HOME"] = str(fake_home)
            mod = _load_main_from(fake_main)
            mod._migrate_legacy_user_artifacts()

            brainchild_dir = fake_home / ".brainchild"
            assert brainchild_dir.is_dir(), "home_dir not created"
            assert not (brainchild_dir / "browser_profile").exists()
            assert not (brainchild_dir / "auth_state.json").exists()
            assert not (brainchild_dir / ".env").exists()
            print("PASS  test_migration_noop_on_fresh_install")
        finally:
            if saved_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved_home
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    test_config_paths_absolute_under_home_brainchild()
    test_macos_adapter_uses_config_path_directly()
    test_migration_moves_legacy_artifacts()
    test_migration_preserves_existing_target()
    test_migration_preserves_existing_env()
    test_migration_noop_on_fresh_install()
    print("\nAll path-resolution tests passed.")

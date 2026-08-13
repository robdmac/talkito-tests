#!/usr/bin/env python3
"""
Minimal pexpect-based smoke test to ensure TalkiTo can exchange a message with Claude.

The test launches `talkito claude` with TTS disabled, waits for the hybrid startup to
complete (via log polling), injects a short prompt via PTY, and verifies that a Claude
response shows up in the terminal output and accompanying log file.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import time

import pexpect


PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "tests-repo" / "test_claude_pexpect.log"
PROMPT_TEXT = "Hey, how's it going?"


def wait_for_log_fragment(fragment: str, timeout: float) -> bool:
    """Poll the TalkiTo log file until a fragment appears or timeout expires."""
    deadline = time.time() + timeout
    fragment_lower = fragment.lower()

    while time.time() < deadline:
        if LOG_PATH.exists():
            content = LOG_PATH.read_text(errors="ignore")
            if fragment_lower in content.lower() or fragment_lower == "":
                return True
        time.sleep(1)

    return False


def spawn_talkito() -> pexpect.spawn:
    """Launch talkito in Claude mode with logging enabled."""
    env = os.environ.copy()
    cmd = [
        sys.executable,
        "-m",
        "talkito",
        "--disable-tts",
        "--asr-mode",
        "off",
        "--log-file",
        str(LOG_PATH),
        "claude",
    ]

    return pexpect.spawn(
        cmd[0],
        cmd[1:],
        cwd=str(PROJECT_ROOT),
        env=env,
        encoding="utf-8",
        echo=False,
        timeout=120,
    )


def main() -> int:
    if LOG_PATH.exists():
        LOG_PATH.unlink()

    child = spawn_talkito()

    try:
        # Wait for key log lines that indicate Claude hybrid mode finished bootstrapping.
        if not wait_for_log_fragment("print_configuration_status completed", timeout=150):
            raise SystemExit("Timed out waiting for TalkiTo to finish startup (no configuration log)")

        # Give the CLI a small buffer to render its interface.
        time.sleep(30)

        child.sendline("1")

        time.sleep(20)
        
        child.sendline(PROMPT_TEXT)

        # Claude responses often include structured markers once the assistant starts thinking.
        child.expect(
            re.compile(r"(analysis:|assistant|claude)", re.IGNORECASE),
            timeout=180,
        )
    except pexpect.TIMEOUT as exc:
        raise SystemExit(f"Timed out waiting for Claude response: {exc}") from exc
    finally:
        if child.isalive():
            try:
                child.sendcontrol("c")
                child.expect(pexpect.EOF, timeout=15)
            except pexpect.TIMEOUT:
                try:
                    child.close(force=True)
                except Exception:
                    pass

    if not wait_for_log_fragment("", timeout=10):
        raise SystemExit("talkito did not create the expected log file")

    if not (
        wait_for_log_fragment("analysis:", timeout=10)
        or wait_for_log_fragment("assistant", timeout=10)
        or wait_for_log_fragment("claude", timeout=10)
    ):
        raise SystemExit("Claude response not detected in talkito log output")

    print("✓ pexpect successfully exchanged a message with Claude via talkito")
    return 0


if __name__ == "__main__":
    sys.exit(main())

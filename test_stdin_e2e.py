#!/usr/bin/env python3

"""
End-to-end test for stdin input with talkito + Claude.
Tests that we can send input via PTY to Claude Code after it's running.
"""

import sys
import time
from pathlib import Path

try:
    import pexpect
except ImportError:
    print("ERROR: pexpect not installed. Install with: pip install pexpect")
    sys.exit(1)

def run_stdin_test():
    """Run talkito with Claude and send input using pexpect"""

    # Get paths
    test_dir = Path(__file__).parent
    log_file = test_dir / 'test_stdin_e2e.log'

    print(f"Test directory: {test_dir}")
    print(f"Log file path: {log_file}")

    # Clean up old log file if it exists
    if log_file.exists():
        log_file.unlink()

    # Construct command - just launch Claude with talkito
    cmd = f"{sys.executable} -m talkito --log-file {log_file} --disable-tts --asr-mode off claude"

    print("Starting Claude Code with talkito using pexpect...")

    # Start the process with pexpect
    child = pexpect.spawn(cmd, cwd=str(test_dir.parent), timeout=120)
    child.logfile = sys.stdout.buffer  # Echo output to console

    print("Process started, waiting for theme selection...")

    pty_output = ""
    test_input = "hi how are you"

    try:
        # Wait for the theme selection prompt
        child.expect("Dark mode", timeout=30)
        print("\nTheme selection appeared")

        # Wait a bit for the menu to fully render
        time.sleep(2)

        # Select option 1 (Dark mode)
        print("Sending '1' to select dark mode...")
        child.sendline("1")

        # Wait for Claude to be ready (look for a prompt or ready indicator)
        print("Waiting for Claude to be ready...")
        time.sleep(10)

        # Send our test input
        test_input = "hi how are you"
        print(f"\nSending: {test_input}")
        child.sendline(test_input)

        # Give Claude time to process
        print("Waiting for Claude response...")
        time.sleep(15)

        # Try to capture any output
        pty_output = child.before if hasattr(child, 'before') else ""
        if isinstance(pty_output, bytes):
            pty_output = pty_output.decode('utf-8', errors='replace')

        print(f"\nCaptured output length: {len(str(pty_output))} chars")

        # Send exit command
        print("Sending /exit command...")
        child.sendline("/exit")
        time.sleep(3)

    except pexpect.TIMEOUT:
        print("\nTimeout waiting for expected output")
    except pexpect.EOF:
        print("\nProcess ended unexpectedly")
    finally:
        # Terminate the process
        print("\nTerminating Claude session...")
        if child.isalive():
            child.terminate(force=True)
            child.wait()

        print(f"Process terminated with return code: {child.exitstatus}")

    # Wait a bit for log file to be flushed
    time.sleep(1)

    # Check log file
    if not log_file.exists():
        print(f"\nERROR: Log file not created at {log_file}")
        return False

    log_content = log_file.read_text()

    # Debug: Show first 100 lines of log
    print("\n=== First 100 lines of log file ===")
    for i, line in enumerate(log_content.split('\n')[:100], 1):
        print(f"{i:3d}: {line}")

    # Look for our input in PTY output or logs
    expected_phrases = [
        "hi how are you",
    ]

    # Check PTY output
    found_in_pty = []
    for phrase in expected_phrases:
        if pty_output and phrase.lower() in pty_output.lower():
            found_in_pty.append(phrase)

    # Check logs
    found_in_logs = []
    for phrase in expected_phrases:
        if phrase.lower() in log_content.lower():
            found_in_logs.append(phrase)

    # Display results
    print("\n=== Test Results ===")
    print(f"Input sent: '{test_input}'")

    if found_in_pty:
        print("\nFound in PTY output:")
        for phrase in found_in_pty:
            print(f"  ✓ {phrase}")

    if found_in_logs:
        print("\nFound in LOGS:")
        for phrase in found_in_logs:
            print(f"  ✓ {phrase}")

    # Look for Claude's response (any response is good)
    claude_indicators = ["i'm", "doing", "fine", "help", "assist"]
    found_response = any(indicator in pty_output.lower() if pty_output else False
                        for indicator in claude_indicators)

    if found_response:
        print("\n✓ Found Claude response in PTY output")
    else:
        print("\n? No obvious Claude response detected (may still have worked)")

    # Test passes if we found our input somewhere
    success = len(found_in_pty) > 0 or len(found_in_logs) > 0

    if success:
        print("\n✓ TEST PASSED: Input via PTY was received")
    else:
        print("\n✗ TEST FAILED: Input not detected in output or logs")

    return success

def main():
    """Main test runner"""
    print("="*60)
    print("End-to-End Stdin Input Test")
    print("="*60)

    try:
        success = run_stdin_test()
        return 0 if success else 1
    except Exception as e:
        print(f"\n✗ TEST FAILED WITH EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(main())
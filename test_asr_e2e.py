#!/usr/bin/env python3

"""
End-to-end test for ASR file mode with talkito.
Tests the full pipeline: audio file -> ASR -> transcription -> Claude -> TTS
"""

import subprocess
import sys
import time
from pathlib import Path

def run_asr_file_test():
    """Run talkito with ASR in file mode and verify transcription"""

    # Get paths
    test_dir = Path(__file__).parent
    fixtures_dir = test_dir / 'fixtures'
    audio_file = fixtures_dir / 'hi_how_are_you.wav'
    log_file = test_dir / 'test_asr_e2e.log'

    print(f"Test directory: {test_dir}")
    print(f"Audio file: {audio_file}")
    print(f"Audio file exists: {audio_file.exists()}")
    print(f"Log file path: {log_file}")

    # Clean up old log file if it exists
    if log_file.exists():
        log_file.unlink()

    # Construct command - use file: mode for ASR with 5 second delay for Claude initialization
    cmd = [
        sys.executable, '-m', 'talkito',
        '--asr-mode', f'file:{audio_file}:15',  # 5 second delay to wait for Claude to initialize
        '--asr-provider', 'local_whisper',
        '--log-file', str(log_file),
        '--disable-tts',  # Disable TTS for faster testing
        'claude'
    ]

    # Run the command - for Claude, we need to handle it differently since it's interactive
    # Start the process but don't wait for completion
    import signal
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',  # Replace invalid UTF-8 bytes with replacement character
        cwd=test_dir.parent
    )

    print("waiting")

    # Give it time to initialize, process the audio file, and send to Claude
    time.sleep(60)  # Wait 60 seconds for processing

    # Terminate the process gracefully
    print("Terminating Claude session...")
    proc.terminate()
    try:
        stdout, stderr = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        print("Process didn't terminate, killing...")
        proc.kill()
        stdout, stderr = proc.communicate()

    print(f"Process terminated with return code: {proc.returncode}")

    # Print stderr if there were errors
    if stderr and stderr.strip():
        print("\n=== STDERR Output ===")
        print(stderr)

    # Wait a bit for log file to be flushed
    time.sleep(1)

    # Check log file for transcription
    if not log_file.exists():
        print(f"\nERROR: Log file not created at {log_file}")
        print(f"\nSTDOUT:\n{stdout if stdout else '(empty)'}")
        print(f"\nSTDERR:\n{stderr if stderr else '(empty)'}")
        return False

    log_content = log_file.read_text()

    # Debug: Show first 100 lines of log to diagnose the issue
    print("\n=== First 100 lines of log file ===")
    for i, line in enumerate(log_content.split('\n')[:100], 1):
        print(f"{i:3d}: {line}")

    # Look for transcription in logs
    expected_phrases = [
        "Processing audio file:",
        "hi_how_are_you.wav",
        "Transcribed from file:",
    ]

    # Check for expected content
    found = []
    missing = []

    for phrase in expected_phrases:
        if phrase.lower() in log_content.lower():
            found.append(phrase)
        else:
            missing.append(phrase)

    # Display results
    print("\n=== Test Results ===")
    print(f"Found {len(found)}/{len(expected_phrases)} expected phrases")

    if found:
        print("\nFound:")
        for phrase in found:
            print(f"  ✓ {phrase}")

    if missing:
        print("\nMissing:")
        for phrase in missing:
            print(f"  ✗ {phrase}")

    # Look for actual transcription text (should contain "hi" or "how are you")
    transcription_indicators = ["hi", "how are you", "how're you"]
    found_transcription = any(indicator in log_content.lower() for indicator in transcription_indicators)

    if found_transcription:
        print("\n✓ Found transcription content in logs")
    else:
        print("\n✗ No transcription content found in logs")
        print("\nSearching for transcription lines:")
        for line in log_content.split('\n'):
            if 'transcri' in line.lower():
                print(f"  {line}")

    # Extract TTS output from logs
    import re

    # Extract queued text (before processing)
    queued_lines = []
    for match in re.finditer(r"queue_for_speech received: '(.+?)'", log_content, re.DOTALL):
        queued_lines.append(match.group(1))

    # Extract spoken text (after processing)
    spoken_lines = []
    # Use a greedy match - the quote at the end of the line is the delimiter
    for match in re.finditer(r"Speaking via \w+ \(line \d+\) qsize \d+: '(.+)'$", log_content, re.MULTILINE):
        spoken_lines.append(match.group(1))

    if queued_lines:
        print(f"\n=== Queued for TTS ({len(queued_lines)} entries) ===")
        for i, line in enumerate(queued_lines, 1):
            print(f"  {i}. {line}")
    else:
        print("\n⚠ Warning: No TTS output found in logs")

    if spoken_lines:
        print(f"\n=== Actually Spoken ({len(spoken_lines)} entries) ===")
        for i, line in enumerate(spoken_lines, 1):
            print(f"  {i}. {line}")

    # Test passes if we found the key phrases and transcription
    success = len(missing) == 0 and found_transcription

    if success:
        print("\n✓ TEST PASSED: ASR file mode working correctly")
    else:
        print("\n✗ TEST FAILED: Missing expected behavior")
        print(f"\nLog file location: {log_file}")

    return success

def main():
    """Main test runner"""
    print("="*60)
    print("End-to-End ASR File Mode Test")
    print("="*60)

    try:
        success = run_asr_file_test()
        return 0 if success else 1
    except Exception as e:
        print(f"\n✗ TEST FAILED WITH EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(main())
#!/usr/bin/env python3

"""
Simple sequential test for talkito sessions
"""

import asyncio
import sys
import difflib
from pathlib import Path
from typing import List, Tuple

# Import the modules we're testing from parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))
from talkito.core import replay_recorded_session
from talkito import tts

# Expected outputs for all test sessions
EXPECTED_OUTPUTS = {
    "art_verbose": {
        "filename": "art.session",
        "profile": 'claude',
        "verbosity": 1,
        "expected_tts": [
            "Welcome to Claude Code!",
            "Tip: Did you know you can drag and drop image files into your terminal?",
            "Sparkling.",
            "I'll create an ASCII art script that generates a 10x100 character display..",
            "Do you want to create art dot py?",
            "Sparkling 6s 957 tokens esc to interrupt Bash.",
            "Do you want to proceed?",
            "Created art dot py with three different ASCII art patterns. Run it with: - art dot py dash interactive menu dash art dot py wave dash wave pattern dash art dot py mountain dash mountain landscape dash art dot py abstract dash abstract circular pattern.",
            "Proceeding.",
            "I'll modify the script to accept width and height arguments..",
            "Do you want to make this edit to art dot py?",
            "Configuring.",
            "Affirming.",
            "Expanding.",
            "Refactoring.",
            "Parsing.",
            "Updated art dot py with width and height arguments. Use as follows: - art dot py -w 80 -H 20 mountain dash 20x80 mountain landscape dash art dot py dash dash width 120 dash dash height 15 wave dash 15x120 wave pattern dash art dot py -w 50 -H 25 abstract dash 25x50 abstract pattern dash art dot py dash interactive mode with default 10x100.",
        ]
    },
    "art": {
        "filename": "art.session",
        "profile": 'claude',
        "verbosity": 0,
        "expected_tts": [
            "Welcome to Claude Code!",
            "I'll create an ASCII art script that generates a 10x100 character display..",
            "Do you want to create art dot py?",
            "Do you want to proceed?",
            "Created art dot py with three different ASCII art patterns. Run it with: - art dot py dash interactive menu dash art dot py wave dash wave pattern dash art dot py mountain dash mountain landscape dash art dot py abstract dash abstract circular pattern.",
            "I'll modify the script to accept width and height arguments..",
            "Do you want to make this edit to art dot py?",
            "Updated art dot py with width and height arguments. Use as follows: - art dot py -w 80 -H 20 mountain dash 20x80 mountain landscape dash art dot py dash dash width 120 dash dash height 15 wave dash 15x120 wave pattern dash art dot py -w 50 -H 25 abstract dash 25x50 abstract pattern dash art dot py dash interactive mode with default 10x100."
        ]
    },
    "modify": {
        "filename": "modify.session",
        "profile": 'claude',
        "verbosity": 0,
        "expected_tts": [
            "Welcome to Claude Code!",
            "I'll modify art dot py to add a border around the art..",
            "Do you want to make this edit to art dot py?",
            "Modified art dot py to add borders around ASCII art. The changes: 1. Added add border function that wraps art with a customizable border character 2. Modified print art to include a border parameter defaults to True 3. Updated print random art to use the new bordered display 4. Added noborder command option to display art without borders 5. Updated usage instructions Now all art displays with borders by default, and you can use python art dot py noborder less than art name greater than to display without borders..",
        ]
    },
}


def run_test(session_name: str, config: dict) -> Tuple[bool, str]:
    """Run a single test session"""
    print(f"\n{'='*60}")
    print(f"Testing: {session_name}")
    print(f"{'='*60}")
    
    # Reset TTS caches before each test
    tts.reset_tts_cache()
    tts.disable_tts = True
    
    # Clear speech history - this is critical!
    if hasattr(tts, 'speech_history'):
        print(f"Clearing speech_history with {len(tts.speech_history)} items")
        tts.speech_history.clear()
    
    # Reset terminal state to avoid line number tracking issues
    from talkito.core import terminal
    if terminal is not None:
        terminal.last_line_number = -1
    
    filename = config.get("filename", f"{session_name}.session")
    base_name = filename.replace(".session", "")
    input_file = Path(__file__).parent / 'fixtures' / f'{base_name}.session'
    
    print(f"Running session file: {input_file}")
    print(f"File exists: {input_file.exists()}")
    
    # Run the session
    actual_tts = asyncio.run(
        replay_recorded_session(
            replay_file=str(input_file),
            auto_skip_tts=False,
            capture_tts=True,
            disable_tts=True,
            show_output=False,
            command_name=config.get("profile"),
            verbosity=config.get("verbosity", 0),
            log_file='test_talkito.log',
        )
    )

    print(len(tts.speech_history))
    print(tts.speech_history)
    
    # Compare results
    expected = config.get("expected_tts", [])
    actual = [data[1] for data in actual_tts]
    
    if actual == expected:
        print(f"✓ PASSED: {session_name}")
        return True, ""
    else:
        print(f"✗ FAILED: {session_name}")
        print(f"Expected {len(expected)} outputs, got {len(actual)}")
        
        # Show diff
        diff = difflib.unified_diff(
            expected,
            actual,
            fromfile='expected',
            tofile='actual',
            lineterm='',
            n=3
        )
        
        # ANSI color codes
        GREEN = '\033[92m'
        RED = '\033[91m'
        CYAN = '\033[96m'
        YELLOW = '\033[93m'
        RESET = '\033[0m'
        
        print("\nDifference:")
        for line in diff:
            if line.startswith('+++'):
                print(f"{CYAN}{line}{RESET}")
            elif line.startswith('---'):
                print(f"{CYAN}{line}{RESET}")
            elif line.startswith('@@'):
                print(f"{YELLOW}{line}{RESET}")
            elif line.startswith('+'):
                print(f"{GREEN}{line}{RESET}")
            elif line.startswith('-'):
                print(f"{RED}{line}{RESET}")
            else:
                print(line)
        
        return False, f"Output mismatch for {session_name}"


def main():
    """Run all tests sequentially"""
    print("Running simple sequential tests...")
    
    passed = 0
    failed = 0
    
    for session_name, config in EXPECTED_OUTPUTS.items():
        success, error = run_test(session_name, config)
        if success:
            passed += 1
        else:
            failed += 1
            print(f"Error: {error}")
    
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*60}")
    
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
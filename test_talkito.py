#!/usr/bin/env python3

# Talkito - Universal TTS wrapper that works with any command
# Copyright (C) 2025 Robert Macrae
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Unit tests for talkito.py using recorded session files.
Tests the TTS output against expected spoken lines.
"""

import asyncio
import sys
import unittest
from pathlib import Path
from typing import List, Tuple

# Import the modules we're testing from parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))
from talkito.core import (
    clean_text, extract_box_content, is_prompt_line, is_response_line,
    replay_recorded_session, active_profile, terminal
)
from talkito import tts
from talkito.profiles import PROFILES

# Expected outputs for all test sessions
EXPECTED_OUTPUTS = {
    "test_echo": {
        "filename": "test_echo.session",
        "profile": None,
        "expected_tts": [
            "Hello, World!",
        ],
    },
    "art_session_verbose": {
        "filename": "art_session.session",
        "profile": 'claude',
        "verbosity": 1,
        "expected_tts": [
            "Welcome to Claude Code!",
            "Tip: Did you know you can drag and drop image files into your terminal?",
            "Sparkling.",
            "I'll create an ASCII art script that generates a 10x100 character display..",
            "Created art dot py with three different ASCII art patterns. Run it with:",
            "Proceeding.",
            "I'll modify the script to accept width and height arguments..",
            "Configuring.",
            "Affirming.",
            "Expanding.",
            "Refactoring.",
            "Updated art dot py with width and height arguments. Use as follows:",
        ]
    },
    "art_session": {
        "filename": "art_session.session",
        "profile": 'claude',
        "verbosity": 0,
        "expected_tts": [
            "Welcome to Claude Code!",
            "I'll create an ASCII art script that generates a 10x100 character display..",
            "Created art dot py with three different ASCII art patterns. Run it with:",
            "I'll modify the script to accept width and height arguments..",
            "Updated art dot py with width and height arguments. Use as follows:",
        ]
    },
    "modify": {
        "filename": "modify.session",
        "profile": 'claude',
        "verbosity": 0,
        "expected_tts": [
            "Welcome to Claude Code!",
            "I'll modify art dot py to add a border around the art..",
            "Modified art dot py to add borders around ASCII art. The changes:",
            "1. Added add border function that wraps art with a customizable border character.",
            "2. Modified print art to include a border parameter.",
            "3. Updated print random art to use the new bordered display.",
            "4. Added noborder command option to display art without borders.",
            "5. Updated usage instructions.",
            "Now all art displays with borders by default, and you can use python art dot py noborder less than art name greater than to display without borders..",
        ]
    },
    # Add more sessions here as needed
}

class TalkitoSessionTester:
    """Test harness for running talkito.py logic on recorded sessions"""
    
    def __init__(self, test_name: str, profile: str = None, verbosity: int = 0, log_file: str = None):
        self.test_name = test_name
        self.profile = profile
        self.verbosity = verbosity
        self.log_file = log_file
        self.input_file = Path(__file__).parent / 'fixtures' / f'{test_name}.session'
    
    def simulate_session(self) -> List[Tuple[float, str, int]]:
        """
        Use talkito.py's replay_recorded_session to process the session.
        Returns list of (timestamp, tts_text, source_line_number)
        """
        # Profile will be set by replay_recorded_session based on the profile parameter
            
        # Reset TTS caches after profile is set
        tts.reset_tts_cache()
        tts.disable_tts = True
        
        # Run replay session using asyncio.run()
        return asyncio.run(
            replay_recorded_session(
                replay_file=str(self.input_file),
                auto_skip_tts=False,
                capture_tts=True,  # This makes it return TTS outputs instead of exit code
                disable_tts=True,
                show_output=False,
                command_name=self.profile,  # Pass profile name
                verbosity=self.verbosity,  # Pass verbosity level
                log_file=self.log_file or 'test_talkito.log',  # Use provided log file or default
            )
        )
    
    def compare_with_expected(self, expected_lines: List[str]) -> List[str]:
        """Compare actual TTS output with expected and return differences"""
        import difflib
        
        actual_tts = self.simulate_session()
        actual_lines = [data[1] for data in actual_tts]
        
        # Create a unified diff like git diff
        diff = difflib.unified_diff(
            expected_lines,
            actual_lines,
            fromfile='expected',
            tofile='actual',
            lineterm='',
            n=3  # Number of context lines
        )
        
        # ANSI color codes
        GREEN = '\033[92m'
        RED = '\033[91m'
        CYAN = '\033[96m'
        YELLOW = '\033[93m'
        RESET = '\033[0m'
        
        differences = []
        diff_lines = list(diff)
        
        if diff_lines:
            # Color the diff output
            colored_diff = []
            for line in diff_lines:
                if line.startswith('+++'):
                    colored_diff.append(f"{CYAN}{line}{RESET}")
                elif line.startswith('---'):
                    colored_diff.append(f"{CYAN}{line}{RESET}")
                elif line.startswith('@@'):
                    colored_diff.append(f"{YELLOW}{line}{RESET}")
                elif line.startswith('+'):
                    colored_diff.append(f"{GREEN}{line}{RESET}")
                elif line.startswith('-'):
                    colored_diff.append(f"{RED}{line}{RESET}")
                else:
                    colored_diff.append(line)
            
            differences.append('\n'.join(colored_diff))
        
        return differences


class TestTalkitoSessions(unittest.TestCase):
    """Unit tests for talkito.py session processing"""
    
    def setUp(self):
        """Reset global state before each test"""
        self._reset_talk_state()
        tts.shutdown_event.clear()  # Clear shutdown event for new test
            
    def tearDown(self):
        """Clean up after each test"""
        # Ensure TTS is shut down
        tts.shutdown_event.set()
        if hasattr(tts, 'tts_worker_thread') and tts.tts_worker_thread:
            tts.tts_worker_thread.join(timeout=1.0)
        self._reset_talk_state()
    
    def _reset_talk_state(self):
        """Reset all talkito module state"""
        from talkito import core
        from talkito.profiles import get_profile
        core.active_profile = get_profile('default')  # Reset to default profile
        if terminal is not None:
            terminal.line_screen_positions = {}
            terminal.screen_content_cache.clear()
        tts.disable_tts = False
        tts.speech_history.clear()
        if hasattr(tts, 'reset_tts_cache'):
            tts.reset_tts_cache()
        if hasattr(core, 'output_buffer'):
            core.output_buffer = None


# Dynamically create test methods for each session
for session_name, test_config in EXPECTED_OUTPUTS.items():
    def create_test(name, config):
        def test_method(self):
            filename = config.get("filename", f"{name}.session")
            base_name = filename.replace(".session", "")
            
            tester = TalkitoSessionTester(
                base_name, 
                profile=config.get("profile"),
                verbosity=config.get("verbosity", 0)
            )
            differences = tester.compare_with_expected(config.get("expected_tts", []))
            
            self.assertEqual(len(differences), 0, 
                           f"TTS output differs:\n{differences[0] if differences else ''}")
        return test_method
    
    test = create_test(session_name, test_config)
    test.__name__ = f"test_{session_name}"
    test.__doc__ = f"Test the {session_name} session"
    setattr(TestTalkitoSessions, test.__name__, test)


class TestTalkComponents(unittest.TestCase):
    """Unit tests for talkito.py component functions"""
    
    def test_text_cleaning(self):
        """Test the text cleaning function"""
        # Test ANSI escape removal
        text_with_ansi = "\x1b[38;5;174mHello\x1b[39m World"
        cleaned = clean_text(text_with_ansi)
        self.assertEqual(cleaned, "Hello World")
        
        # Test control character removal
        text_with_control = "Hello\x00\x01World"
        cleaned = clean_text(text_with_control)
        self.assertEqual(cleaned, "HelloWorld")
    
    def test_prompt_detection(self):
        """Test prompt line detection"""
        # Set up Claude profile
        from talkito import core
        core.active_profile = PROFILES['claude']
        
        # Test various prompt formats
        self.assertTrue(is_prompt_line("> "))
        self.assertTrue(is_prompt_line("│ > Write"))
        self.assertFalse(is_prompt_line("This is not a prompt"))
    
    def test_response_detection(self):
        """Test response line detection"""
        # Set up Claude profile
        from talkito import core
        core.active_profile = PROFILES['claude']
        
        # Test response prefix
        self.assertTrue(is_response_line("⏺ I'll help you with that"))
        self.assertFalse(is_response_line("This is user input"))
    
    def test_box_content_extraction(self):
        """Test extraction of content from box drawings"""
        boxed_line = "│ Welcome to Claude Code │"
        extracted = extract_box_content(boxed_line)
        self.assertEqual(extracted, "Welcome to Claude Code")
        
        # Test line without box
        normal_line = "This is a normal line"
        extracted = extract_box_content(normal_line)
        self.assertIsNone(extracted)


def generate_expected_tts_from_session(test_name: str, profile: str = None, verbosity: int = 0, log_file: str = None):
    """
    Helper to generate expected TTS outputs from a session.
    Prints the dictionary format to terminal for manual addition to EXPECTED_OUTPUTS.
    
    Args:
        test_name: Base name of the test (e.g., 'claude_session')
        profile: Program name from talkito.PROFILES (e.g., 'claude', 'aider') or None
        verbosity: Verbosity level (0=normal, 1=verbose, 2=very verbose)
        log_file: Optional log file path for debugging
    """
    tester = TalkitoSessionTester(test_name, profile=profile, verbosity=verbosity, log_file=log_file)
    actual_tts = tester.simulate_session()
    
    print(f"\nGenerated expected outputs for '{test_name}':")
    print(f"Found {len(actual_tts)} TTS outputs")
    print(f"Using profile: {profile or 'None'}")
    print(f"Using verbosity: {verbosity}")
    
    if actual_tts:
        print(f"\nAdd this to EXPECTED_OUTPUTS in test_talkito.py:\n")
        print(f'    "{test_name}": {{')
        print(f'        "filename": "{test_name}.session",')
        print(f'        "profile": {repr(profile)},')
        if verbosity != 0:
            print(f'        "verbosity": {verbosity},')
        print(f'        "expected_tts": [')
        for _, text, _ in actual_tts:
            # Escape quotes and backslashes in text
            escaped_text = text.replace('\\', '\\\\').replace('"', '\\"')
            print(f'            "{escaped_text}",')
        print('        ]')
        print('    },')
    else:
        print("\nNo TTS outputs found.")


if __name__ == '__main__':
    import argparse
    
    # Check if --generate is the first argument for backward compatibility
    if len(sys.argv) > 1 and sys.argv[1] == '--generate':
        # Create parser for generate mode
        parser = argparse.ArgumentParser(description='Generate expected TTS outputs from a session')
        parser.add_argument('--generate', action='store_true', help='Generate expected outputs')
        parser.add_argument('test_name', help='Base name of the test (e.g., "claude_session")')
        parser.add_argument('--profile', nargs='?', default=None, help='Program name from talkito.PROFILES')
        parser.add_argument('--verbosity', nargs='?', type=int, default=0, help='Verbosity level (0, 1, or 2)')
        parser.add_argument('--log-file', type=str, default=None, help='Enable logging and write to specified file')
        
        args = parser.parse_args()
        
        # Set up logging if requested
        if args.log_file:
            from talkito.logs import setup_logging
            setup_logging(args.log_file, mode='w')
            print(f"Logging to: {args.log_file}")
        
        generate_expected_tts_from_session(args.test_name, args.profile, args.verbosity, args.log_file)
    else:
        # Regular unittest mode
        unittest.main()
# talkito.py Testing Infrastructure

This directory contains tests for TalkiTo using captured terminal sessions.

## Directory Structure

```
tests/
├── test_talkito.py          # Main test framework with all expected outputs
├── fixtures/
│   └── test_name_input.txt         # Captured session input files
└── README.md                       # This file
```

## Workflow

### Step 1: Capture a session
```bash
./talkito.py --capture tests/fixtures/my_test_input.txt claude
```
This runs the command with no TTS processing and saves the raw terminal output.

### Step 2: Generate expected outputs
```bash
# For a session using a specific program profile (e.g., claude, aider, python)
python tests/test_talkito.py --generate my_test claude

# For a session without a specific profile
python tests/test_talkito.py --generate my_test
```
This processes the captured session through TalkiTo's TTS logic and prints the expected outputs.

### Step 3: Add expected outputs to test file
Copy the printed output and add it to the `EXPECTED_OUTPUTS` dictionary in `test_talkito.py`:
```python
EXPECTED_OUTPUTS = {
    "claude_session": {
        "filename": "claude_session_input.txt",
        "profile": "claude",
        "expected_tts": [
            "Welcome to Claude Code!.",
            "Expected TTS output 2",
            # etc.
        ]
    },
    "my_test": {
        "filename": "my_test_input.txt", 
        "profile": "claude",  # or None if no specific profile
        "expected_tts": [
            "Expected TTS output 1",
            "Expected TTS output 2",
            # etc.
        ]
    },
}
```

### Step 4: Tests are automatically created
Tests are automatically generated for each entry in `EXPECTED_OUTPUTS`. No need to write test methods manually unless you need custom logic.

### Step 5: Run tests
```bash
# Run all tests
python -m unittest tests.test_talk -v

# Run specific test
python -m unittest tests.test_talkito.TestTalkSessions.test_claude_session -v
```

## Key Features

- **Integrated capture**: Use `./talkito.py --capture` to capture sessions with TTS enabled
- **Real filtering**: Uses actual `tts.py` filtering logic to determine what gets spoken
- **Centralized expectations**: All expected outputs in one file for easy maintenance
- **Structured format**: Each test specifies filename, profile, and expected TTS outputs
- **Profile support**: Tests can specify which program profile to use (claude, aider, python, etc.)
- **Print to terminal**: Generate outputs print to terminal for easy copy/paste
- **Standard unittest**: Uses Python's built-in unittest framework
- **Automatic test generation**: Tests are created automatically from EXPECTED_OUTPUTS entries

## Example

The included tests demonstrate the workflow:

### claude_session test:
- `fixtures/claude_session_input.txt`: Captured Claude Code session
- Profile: `"claude"` (uses Claude-specific filtering rules)
- Expected outputs include welcome message, status updates, and code generation feedback

### test_echo test:
- `fixtures/test_echo_input.txt`: Simple echo command capture
- Profile: `None` (no specific program profile)
- Expected output: `"Hello, World!"`

### art_test test:
- `fixtures/art_test_input.txt`: Claude session generating ASCII art
- Profile: `"claude"`
- Expected outputs include tool invocations, code snippets, and completion messages
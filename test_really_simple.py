#!/usr/bin/env python3

"""
Really simple test - run each session in a separate process
"""

import subprocess
import sys
import json
from pathlib import Path

# Expected outputs
EXPECTED = {
    "art_verbose": {
        "file": "art", 
        "profile": "claude",
        "verbosity": 1,
        "count": 17
    },
    "art": {
        "file": "art",
        "profile": "claude", 
        "verbosity": 0,
        "count": 8
    },
    "modify": {
        "file": "modify",
        "profile": "claude",
        "verbosity": 0,
        "count": 4
    }
}

def run_session_in_subprocess(session_file: str, profile: str, verbosity: int) -> int:
    """Run a session in a subprocess and return count of outputs"""
    script = f"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, '{Path(__file__).parent.parent}')
from talkito.core import replay_recorded_session
from talkito import tts

tts.reset_tts_cache()
tts.disable_tts = True

result = asyncio.run(
    replay_recorded_session(
        replay_file='{session_file}',
        auto_skip_tts=False,
        capture_tts=True,
        disable_tts=True,
        show_output=False,
        command_name='{profile}',
        verbosity={verbosity},
        log_file='test.log',
    )
)
print(len(result))
"""
    
    proc = subprocess.run(
        [sys.executable, '-c', script],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent
    )
    
    if proc.returncode != 0:
        print(f"Error running session: {proc.stderr}")
        return -1
    
    try:
        return int(proc.stdout.strip().split('\n')[-1])
    except:
        print(f"Could not parse output: {proc.stdout}")
        return -1

def main():
    print("Running sessions in separate processes...")
    
    passed = 0
    failed = 0
    
    for name, config in EXPECTED.items():
        session_file = Path(__file__).parent / 'fixtures' / f"{config['file']}.session"
        
        print(f"\nTesting {name}...")
        count = run_session_in_subprocess(
            str(session_file),
            config['profile'],
            config['verbosity']
        )
        
        if count == config['count']:
            print(f"✓ PASSED: Got {count} outputs as expected")
            passed += 1
        else:
            print(f"✗ FAILED: Expected {config['count']} outputs, got {count}")
            failed += 1
    
    print(f"\nResults: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1

if __name__ == '__main__':
    sys.exit(main())
#!/usr/bin/env python3

"""Test just the modify session"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from talkito.core import replay_recorded_session
from talkito import tts

# Run just the modify session
tts.reset_tts_cache()
tts.disable_tts = True

input_file = Path(__file__).parent / 'fixtures' / 'modify.session'
print(f"Running {input_file}")

actual_tts = asyncio.run(
    replay_recorded_session(
        replay_file=str(input_file),
        auto_skip_tts=False,
        capture_tts=True,
        disable_tts=True,
        show_output=False,
        command_name='claude',
        verbosity=0,
        log_file='test_single.log',
    )
)

print(f"\nGot {len(actual_tts)} outputs:")
for i, (_, text, _) in enumerate(actual_tts):
    print(f"{i}: {text}")
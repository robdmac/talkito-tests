#!/usr/bin/env python3

"""Checks how slashes in agent output are spoken: real paths shrink to the filename, other pairs don't."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from talkito.tts import extract_speakable_text

CASES = [
    # word/word used to lose everything before the last slash
    ('indicators and Codex/Gemini tool details', 'indicators and Codex or Gemini tool details'),
    ('read/write access', 'read or write access'),
    ('client/server/database', 'client or server or database'),
    ('and/or', 'and or'),
    # numbers are left for the TTS engine to read
    ('about 1/2 of them', 'about 1/2 of them'),
    ('on 10/07/2026', 'on 10/07/2026'),
    # real paths still shrink to the filename
    ('edit talkito/tts.py now', 'edit tts dot py now'),
    ('see /Users/rob/work/talk/core.py', 'see core dot py'),
    ('in ./scripts/run.sh', 'in run dot sh'),
    ('open ~/notes/todo.txt', 'open todo dot txt'),
    ('src/components/Button.tsx', 'Button dot tsx'),
    ('run /help first', 'run slash help first'),
]


def main():
    failures = []
    for text, expected in CASES:
        got = extract_speakable_text(text)
        ok = got == expected
        print(f'  [{"ok" if ok else "FAIL"}] {text!r} -> {got!r}')
        if not ok:
            failures.append(f'{text!r}: got {got!r}, expected {expected!r}')
    if failures:
        print('\nFAILED:')
        for failure in failures:
            print(f'  {failure}')
        return 1
    print('\nAll checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3

"""Replays a real OpenCode PTY capture to check its response reaches the speech queue whole."""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_codex_screen import replay

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'opencode_v11835_screen.bin')

# OpenCode v1.18.35 in a 40x120 terminal. It indents replies five columns with no marker, wraps five
# columns short of the edge, and starts laying out the next row before finishing the one above
PARAGRAPHS = [
    'who chose the desert valley along the Rímac River as the capital',
    'the nearby silver mines of Potosí. Colonial Lima flourished',
    'in the late 19th century the arrival of railways',
    'emergence as a world-renowned culinary capital.',
]
BULLETS = [
    "the Archbishop's Palace, and the Government Palace.",
    'famous for its baroque church, tilework, and catacombs.',
    'sitting in the middle of the modern district of Miraflores.',
]
NEVER = ['Thought:', 'Build ·', 'esc interrupt', 'opentui', 'catacom ']


def main():
    data = open(FIXTURE, 'rb').read()
    frames = [c for c in re.split(rb'(?<=\x1b\[\?2026l)', data) if c]
    failures = []

    for how, chunks in (('frames', frames), ('7b chunks', [data[i:i + 7] for i in range(0, len(data), 7)])):
        spoken = replay(data, chunks, profile_name='opencode')
        text = ' '.join(spoken)
        problems = [f'missing {p!r}' for p in PARAGRAPHS + BULLETS if p not in text]
        problems += [f'spoke {n!r}' for n in NEVER if n in text]
        problems += [f'{b!r} spoken {text.count(b)} times' for b in BULLETS if text.count(b) > 1]
        print(f'  [{"ok" if not problems else "FAIL"}] {how:<10} spoke the reply whole ({len(spoken)} items)')
        failures.extend(f'{how}: {p}' for p in problems)

    # Negative control: the line reader on its own never sees the reply
    plain = ' '.join(replay(data, frames, reads_screen=False, profile_name='opencode'))
    heard = any(p in plain for p in PARAGRAPHS)
    print(f'  [{"ok" if not heard else "FAIL"}] line reader alone misses the reply')
    if heard:
        failures.append('negative control: reply found without the screen reader')

    if failures:
        print('\nFAILED:')
        for failure in failures:
            print(f'  {failure}')
        return 1
    print('\nAll checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())

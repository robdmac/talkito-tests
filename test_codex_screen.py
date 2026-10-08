#!/usr/bin/env python3

"""Replays a real Codex CLI PTY capture to check its response reaches the speech queue whole."""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from talkito import core, screen
from talkito.profiles import get_profile
from talkito.screen import Screen

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'codex_v0160_screen.bin')

# Codex CLI v0.160.1 in a 40x120 terminal. It paints each word into place with absolute cursor
# moves inside synchronized updates, so no single write ever holds a whole line
ROWS, COLS = 40, 120
EXPECTED = [
    "Kyoto began as Heian-kyō in 794, when Emperor Kanmu moved Japan's capital from Nagaoka-kyō.",
    'Kyoto remained the imperial capital until 1868.',
    'becoming a major cultural and academic center.',
]
BULLETS = ['Kiyomizu-dera', 'Kinkaku-ji', 'Fushimi Inari-taisha']


def replay(data: bytes, chunks, reads_screen: bool = True, profile_name: str = 'codex'):
    """Feed a capture through the screen reader and return everything handed to the speech queue."""
    profile = get_profile(profile_name)
    saved = (profile.reads_screen, core.active_profile, core.terminal, core._visible_screen_size,
             core.tts.queue_for_speech, screen.clock)

    spoken = []

    def capture(text, *args, **kwargs):
        spoken.append(text)
        return text

    profile.reads_screen = reads_screen
    core.active_profile = profile
    core.terminal = core.TerminalState()
    core._visible_screen_size = lambda: (ROWS, COLS)
    core.tts.queue_for_speech = capture
    core.reset_screen_transcript()
    # A millisecond per byte stands in for the pace the agent wrote at, the same however it is split
    fed = [0]
    screen.clock = lambda: fed[0] / 1000

    try:
        output_buffer = core.LineBuffer()
        line_buffer, text_buffer, prev_line = b'', [], ''
        for chunk in chunks:
            fed[0] += len(chunk)
            if reads_screen:
                text_buffer, prev_line = core.process_screen_output(chunk, text_buffer, prev_line, 'off')
                # The read loop checks for settled rows between reads
                text_buffer, prev_line = core.process_screen_output(b'', text_buffer, prev_line, 'off')
            else:
                line_buffer += chunk
                line_buffer, text_buffer, prev_line, _, _ = core.process_line_buffer_data(
                    line_buffer, output_buffer, text_buffer, prev_line, False, 1, 'off'
                )
        if reads_screen:
            text_buffer, prev_line = core.process_screen_output(b'', text_buffer, prev_line, 'off', final=True)
        core.send_pending_text()
    finally:
        (profile.reads_screen, core.active_profile, core.terminal, core._visible_screen_size,
         core.tts.queue_for_speech, screen.clock) = saved
        core.reset_screen_transcript()

    return spoken


def replay_timed(entries, profile_name: str):
    """Replay recorded (seconds, bytes) reads with their timing, checking for paused output between them."""
    profile = get_profile(profile_name)
    saved = (core.active_profile, core.terminal, core._visible_screen_size, core.tts.queue_for_speech, screen.clock)
    spoken = []

    def capture(text, *args, **kwargs):
        spoken.append(text)
        return text

    now = [0.0]
    core.active_profile = profile
    core.terminal = core.TerminalState()
    core._visible_screen_size = lambda: (ROWS, COLS)
    core.tts.queue_for_speech = capture
    core.reset_screen_transcript()
    screen.clock = lambda: now[0]
    try:
        text_buffer, prev_line = [], ''
        for seconds, data in entries:
            # The read loop keeps checking while it waits, every 50ms or so until this read arrives
            while now[0] + 0.05 < seconds:
                now[0] += 0.05
                text_buffer, prev_line = core.process_screen_output(b'', text_buffer, prev_line, 'off')
            now[0] = max(now[0], seconds - 0.001)
            text_buffer, prev_line = core.process_screen_output(b'', text_buffer, prev_line, 'off')
            now[0] = seconds
            text_buffer, prev_line = core.process_screen_output(data, text_buffer, prev_line, 'off')
        now[0] += 5
        text_buffer, prev_line = core.process_screen_output(b'', text_buffer, prev_line, 'off')
        core.process_screen_output(b'', text_buffer, prev_line, 'off', final=True)
        core.send_pending_text()
    finally:
        core.active_profile, core.terminal, core._visible_screen_size, core.tts.queue_for_speech, screen.clock = saved
        core.reset_screen_transcript()
    return spoken


def check_response(data: bytes):
    """The whole reply is spoken once, with wrapped rows joined into sentences."""
    failures = []
    frames = [c for c in re.split(rb'(?<=\x1b\[\?2026l)', data) if c]
    results = {
        'frames': replay(data, frames),
        '7b chunks': replay(data, [data[i:i + 7] for i in range(0, len(data), 7)]),
        # A busy agent fills a whole read with several frames, each painting over the last
        '16KB reads': replay(data, [data[i:i + 16384] for i in range(0, len(data), 16384)]),
    }
    for how, spoken in results.items():
        text = ' '.join(spoken)
        missing = [e for e in EXPECTED + BULLETS if e not in text]
        repeated = [b for b in BULLETS if text.count(b) != 1]
        partial = [s for s in spoken if s.rstrip().endswith('•')]
        ok = not (missing or repeated or partial)
        print(f'  [{"ok" if ok else "FAIL"}] {how:<10} spoke the reply whole ({len(spoken)} items)')
        failures.extend(f'{how}: missing {m!r}' for m in missing)
        failures.extend(f'{how}: {b!r} spoken {text.count(b)} times' for b in repeated)
        failures.extend(f'{how}: partial row {p!r}' for p in partial)

    # The startup banner is painted outside synchronized updates around an animated logo, so only
    # the reply itself is required to come out the same however the output is split
    replies = [[t for t in spoken if '•' in t] for spoken in results.values()]
    same = all(reply == replies[0] for reply in replies)
    if not same:
        failures.append('chunking changed what was spoken')
    print(f'  [{"ok" if same else "FAIL"}] chunk size does not change the reply')

    # Negative control: the line reader on its own never sees the reply
    plain = ' '.join(replay(data, frames, reads_screen=False))
    heard = any(e in plain for e in EXPECTED)
    print(f'  [{"ok" if not heard else "FAIL"}] line reader alone misses the reply')
    if heard:
        failures.append('negative control: reply found without the screen reader')
    return failures


def check_screen():
    """The virtual screen wraps, erases and gates on synchronized updates like a terminal."""
    failures = []

    def expect(name, got, want):
        ok = got == want
        print(f'  [{"ok" if ok else "FAIL"}] {name}')
        if not ok:
            failures.append(f'{name}: got {got!r}, expected {want!r}')

    screen = Screen(3, 10)
    screen.feed(b'0123456789')
    expect('last column does not wrap yet', screen.lines()[:2], ['0123456789', ''])
    screen.feed(b'X')
    expect('next character wraps', screen.lines()[:2], ['0123456789', 'X'])

    screen = Screen(2, 10)
    screen.feed('\x1b[1;1Hab漢字cd'.encode())
    expect('wide characters take two cells', screen.lines()[0], 'ab漢字cd')
    screen.feed(b'\x1b[1;3H\x1b[K')
    expect('erase to end of line', screen.lines()[0], 'ab')

    screen = Screen(2, 10)
    screen.feed(b'\x1b[?2026h\x1b[1;1Hhalf')
    expect('inside a synchronized update', screen.in_update, True)
    screen.feed(b'\x1b[?20')
    screen.feed(b'26l')
    expect('update ends across a chunk boundary', screen.in_update, False)
    return failures


def main():
    data = open(FIXTURE, 'rb').read()
    failures = check_screen() + check_response(data)
    if failures:
        print('\nFAILED:')
        for failure in failures:
            print(f'  {failure}')
        return 1
    print('\nAll checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())

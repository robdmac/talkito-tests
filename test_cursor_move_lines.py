#!/usr/bin/env python3

"""Replays a real Claude Code PTY capture to check response text still reaches the speech queue."""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from talkito import core
from talkito.profiles import get_profile

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'claude_v2_repaint.bin')

# Claude Code v2.1.222 repaints with absolute cursor addressing and emits no newlines at all, so
# without repaints_with_cursor_moves the line reader never sees a single line
EXPECTED_RESPONSE = 'pineapple'
MIC = '\U0001f3a4'


def replay(data: bytes, profile_name: str, split: bool, chunk_size: int = 0):
    """Feed a capture through the line pipeline and return what would have been spoken."""
    profile = get_profile(profile_name)
    original_split = profile.repaints_with_cursor_moves
    original_profile = core.active_profile
    original_terminal = core.terminal
    real_queue_output = core.queue_output

    profile.repaints_with_cursor_moves = split
    core.active_profile = profile
    core.terminal = core.TerminalState()

    spoken = []
    core.queue_output = lambda text, line_number=None, *args, **kwargs: spoken.append(text)

    chunks = [data] if not chunk_size else [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]

    try:
        output_buffer = core.LineBuffer()
        buffer, text_buffer, prev_line = b'', [], ''
        for chunk in chunks:
            buffer += chunk
            buffer, text_buffer, prev_line, _, _ = core.process_line_buffer_data(
                buffer, output_buffer, text_buffer, prev_line, False, 1, 'off'
            )
    finally:
        core.queue_output = real_queue_output
        core.terminal = original_terminal
        core.active_profile = original_profile
        profile.repaints_with_cursor_moves = original_split

    return spoken


def check_word_spacing():
    """Words spaced apart by column moves must not run together."""
    # claude_only marks cases that rely on absolute column moves, which only this profile expands
    cases = [
        ('absolute column', 'Hey!\x1b[6GGoing\x1b[12Gwell', 'Hey! Going well', True),
        ('forward column', 'Hey!\x1b[2CGoing\x1b[1Cwell', 'Hey!  Going well', False),
        ('column move backwards', 'Hello\x1b[3Gworld', 'Hello world', True),
    ]
    failures = []
    original_profile = core.active_profile
    original_terminal = core.terminal
    core.terminal = core.TerminalState()

    try:
        for name, raw, expected, claude_only in cases:
            core.active_profile = get_profile('claude')
            got = core.clean_text(raw)
            ok = got == expected
            print(f'  [{"ok" if ok else "FAIL"}] {name}: {got!r}')
            if not ok:
                failures.append(f'{name}: got {got!r}, expected {expected!r}')

            # Negative control: profiles without the flag must not gain the new spacing
            core.active_profile = get_profile('codex')
            other = core.clean_text(raw)
            if claude_only and other == expected:
                failures.append(f'{name}: codex should not have changed, got {other!r}')
    finally:
        core.active_profile = original_profile
        core.terminal = original_terminal

    return failures


def track_cells(text):
    """Replay the escapes and report which cell each printable character lands in."""
    col = row = 0
    cells = []
    i = 0
    while i < len(text):
        if text[i] == '\x1b':
            match = re.match(r'\x1b\[([0-9;]*)([A-Za-z])|\x1b\[\?[0-9]+[a-z]|\x1b\[[0-9]* q', text[i:])
            if not match:
                i += 1
                continue
            command = match.group(2)
            if command == 'H':
                parts = (match.group(1) or '').split(';')
                row = int(parts[0] or 1)
                col = int(parts[1] or 1) if len(parts) > 1 else 1
            elif command == 'C':
                col += int(match.group(1) or 1)
            elif command == 'D':
                col -= int(match.group(1) or 1)
            elif command == 'A':
                row -= int(match.group(1) or 1)
            elif command == 'B':
                row += int(match.group(1) or 1)
            i += match.end()
            continue

        char = text[i]
        if char == '\r':
            col = 0
        elif char == '\n':
            row += 1
        elif char == MIC:
            col += 2                      # the emoji occupies two cells
        elif char.strip():
            cells.append((row, col, char))
            col += 1
        else:
            col += 1
        i += 1
    return cells


def check_mic_replacement():
    """Dictating swaps the prompt glyph for a microphone and puts it back afterwards."""
    # Verbatim chunks captured while holding the tap-to-talk key. Neither TUI repaints the prompt
    # glyph, so the microphone has to hang off a cursor move that is present.
    samples = {
        'codex': (b'\x1b[?2026h\x1b[21;3H\xc2\xa0Hi\x1b[39m\x1b[?25h\x1b[21;3H\x1b[?2026l', '\u203a'),
        'claude': (b'\x1b[?25l\x1b[2D\x1b[4B\r\x1b[2C\x1b[4A Thank\x1b[10Gyou.\r\n', '\u276f'),
    }
    failures = []
    original_profile = core.active_profile

    try:
        for name, (chunk, glyph) in samples.items():
            core.active_profile = get_profile(name)

            core.reset_mic_indicator_state()
            held = core.apply_mic_indicator(chunk, True)
            restored = core.apply_mic_indicator(chunk, False)

            # Never dictated means nothing to put back
            core.reset_mic_indicator_state()
            untouched = core.apply_mic_indicator(chunk, False)

            held_text = held.decode('utf-8')
            restored_text = restored.decode('utf-8')
            problems = []
            if MIC not in held_text:
                problems.append('no microphone while dictating')
            if glyph in held_text:
                problems.append('prompt glyph still showing while dictating')
            if glyph not in restored_text:
                problems.append('prompt glyph not restored')
            if MIC in restored_text:
                problems.append('microphone left behind after release')
            if untouched != chunk:
                problems.append('chunk altered when the mic was never shown')

            # Everything the TUI painted must stay in the cell it was going to
            base = track_cells(chunk.decode('utf-8'))
            for label, text in (('while dictating', held_text), ('after release', restored_text)):
                moved = [c for c in base if c not in track_cells(text)]
                if moved:
                    problems.append(f'{label} moved {moved[:3]}')

            print(f'  [{"ok" if not problems else "FAIL"}] {name} mic replacement')
            failures.extend(f'{name}: {p}' for p in problems)
    finally:
        core.active_profile = original_profile

    return failures


def check_shutdown_chrome():
    """Closing messages must not be spoken, without silencing real responses."""
    profile = get_profile('claude')
    silenced = [
        'copied 1 char to clipboard · disable auto-copy in /config',
        'Press Ctrl- again to exit. Resume this session with:',
        'claude --resume 0638e43e-1817-4cb0-8b97-fed3f83e06e7',
        '⏵⏵ auto mode on (shift+tab to cycle) · ← for agents',
    ]
    spoken = [
        'Claude Code v2.1.222',
        'Going well, thanks. Ready when you are — what are we working on in talkito today?',
        'Do you want to make this edit to core.py?',
        'I resumed the analysis and found three issues.',
    ]

    failures = []
    for text in silenced:
        if not profile.should_skip(text, 0):
            failures.append(f'not silenced: {text!r}')
    for text in spoken:
        if profile.should_skip(text, 0):
            failures.append(f'wrongly silenced: {text!r}')
    print(f'  [{"ok" if not failures else "FAIL"}] shutdown and status chrome filtered')
    return failures


def main():
    data = open(FIXTURE, 'rb').read()
    assert b'\n' not in data, 'fixture should be the newline-free repaint stream'

    failures = []

    # Whole capture and small chunks, so a row-move sequence straddling a chunk boundary is covered
    for chunk_size in (0, 64, 7):
        for split in (False, True):
            spoken = replay(data, 'claude', split, chunk_size)
            heard = any(EXPECTED_RESPONSE in text for text in spoken)
            how = 'whole' if not chunk_size else f'{chunk_size}b chunks'
            status = 'ok' if heard == split else 'FAIL'
            print(f'  [{status}] split={split!s:<5} {how:<10} spoke response: {heard}')
            if heard != split:
                # split off is the negative control - it must fail to find the response
                failures.append(f'split={split} {how}: spoke response={heard}')

    # Every other profile must be left on the newline-based reader
    for name in ('codex', 'gemini', 'aider'):
        if get_profile(name).repaints_with_cursor_moves:
            failures.append(f'{name} unexpectedly splits on cursor moves')
    print(f'  [ok] other profiles unchanged')

    failures.extend(check_word_spacing())
    failures.extend(check_mic_replacement())
    failures.extend(check_shutdown_chrome())

    if failures:
        print('\nFAILED:')
        for failure in failures:
            print(f'  {failure}')
        return 1
    print('\nAll checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())

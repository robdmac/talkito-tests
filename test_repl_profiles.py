#!/usr/bin/env python3

"""Checks REPL output after a prompt is spoken: it is real output, not the user's typing echoed back."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from talkito import core
from talkito.profiles import get_profile

# What each REPL prints, prompts included, and the lines that must reach speech in order
CASES = {
    'python': (b'>>> for i in range(3): print("row", i)\r\n... \r\nrow 0\r\nrow 1\r\nrow 2\r\n>>> ',
               ['row 0', 'row 1', 'row 2']),
    # A lone number is the answer, and the same answer twice is said twice
    'python3': (b'>>> 2 + 3\r\n5\r\n>>> 2 + 3\r\n5\r\n>>> -7 // 2\r\n-4\r\n'
                b'>>> print("ok")\r\nok\r\n>>> print("ok")\r\nok\r\n>>> ',
                ['5', '5', '-4', 'ok', 'ok']),
    'psql': (b"postgres=# select 'hello from postgres' as greeting;\r\n      greeting       \r\n"
             b"---------------------\r\n hello from postgres\r\n(1 row)\r\n\r\npostgres=# ",
             ['hello from postgres']),
    'mysql': (b"mysql> select 'hello from mysql';\r\n+------------------+\r\n| hello from mysql |\r\n"
              b"+------------------+\r\n| hello from mysql |\r\n+------------------+\r\nmysql> ",
              ['hello from mysql']),
}


def replay(profile_name: str, data: bytes):
    """Run output through the line reader as if the prompt had just been shown; return what was sent."""
    saved = (core.active_profile, core.terminal, core.tts.queue_for_speech, core.asr_state.waiting_for_input)
    sent = []

    def capture(text, *args, similarity_checks=True, **kwargs):
        sent.append((text, similarity_checks))
        return text

    core.active_profile = get_profile(profile_name)
    core.terminal = core.TerminalState()
    core.tts.queue_for_speech = capture
    # The prompt before the command has been seen, which used to silence everything after it
    core.asr_state.waiting_for_input = True
    try:
        output_buffer = core.LineBuffer()
        buffer, prev_line = [], ''
        line_buffer, buffer, prev_line, _, _ = core.process_line_buffer_data(
            data, output_buffer, buffer, prev_line, False, 1, 'off')
        core.send_pending_text()
    finally:
        core.active_profile, core.terminal, core.tts.queue_for_speech, core.asr_state.waiting_for_input = saved
    return sent


def main():
    failures = []
    for name, (data, expected) in CASES.items():
        sent = replay(name, data)
        spoken = [t.strip() for t, _ in sent if t.strip()]
        # Each expected line in order, as its own item or inside one
        problems, position = [], 0
        for e in expected:
            found = next((i for i in range(position, len(spoken)) if e == spoken[i] or e in spoken[i].split('  ')
                          or (len(e) > 3 and e in spoken[i])), None)
            if found is None:
                problems.append(f'missing {e!r}')
            else:
                position = found + 1
        problems += [f'similarity checks on for {t!r}' for t, checks in sent if checks]
        print(f'  [{"ok" if not problems else "FAIL"}] {name}: {[t for t, _ in sent]}')
        failures.extend(f'{name}: {p}' for p in problems)

    # Agents still drop the echo of the user's own message
    if not get_profile('claude').suppress_input_echo:
        failures.append('claude no longer suppresses input echo')
    # Programs not opted in still drop lines of bare numbers, which are usually counters
    if get_profile('default').speak_bare_numbers:
        failures.append('default profile speaks bare numbers')

    if failures:
        print('\nFAILED:')
        for failure in failures:
            print(f'  {failure}')
        return 1
    print('\nAll checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())

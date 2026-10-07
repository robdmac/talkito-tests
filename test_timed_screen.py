#!/usr/bin/env python3

"""Replays recorded Claude Code and Gemini CLI sessions, with their timing, to check replies are spoken whole."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_codex_screen
from talkito.core import SessionRecorder

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')

# Claude Code v2.1.293 in a 50x120 terminal, recorded by live_agent_check.py. It sends no
# synchronized updates, so frames are found from pauses in the recorded output
# Each case: fixture, profile, phrases that must be spoken once
CASES = {
    'claude_v21293_long_reply.session': ('claude', [
        "Lisbon is one of Europe's oldest cities",
        "Afonso Henriques captured it in 1147, and it became Portugal's capital around 1255.",
        'Lisbon is now a lively European capital.',
        "Belém Tower: a 16th-century fortress on the Tagus River that symbolises Portugal's maritime golden age.",
        'Jerónimos Monastery: a masterpiece of Manueline architecture',
        "São Jorge Castle: a Moorish-era hilltop castle overlooking the Alfama district",
    ]),
    'claude_v21293_multi_turn.session': ('claude', [
        'Lima is the capital of Peru.',
        'cured in fresh lime juice with red onion, chili (ají), and cilantro.',
        'called leche de tigre, is often drunk as well.',
    ]),
    # Gemini CLI v0.63.0, also without synchronized updates; it starts the next row before finishing one
    'gemini_v0630_long_reply.session': ('gemini', [
        'securing its role as the thriving capital of Portugal.',
        "evolving into today's dynamic cultural hub of Southern Europe.",
        "built as a ceremonial gateway and defensive point during Portugal's Age of Discovery.",
        'reflects the maritime prosperity and Gothic-Renaissance transitions of the era.',
        'offering panoramic vistas and remnants dating back to Moorish rule.',
    ]),
    'gemini_v0630_multi_turn.session': ('gemini', [
        'The capital of Peru is Lima.',
        "widely considered the country's national culinary pride.",
    ]),
}
# Chrome that used to be read out: status notices, spinner words, tips and the suggested next prompt
NEVER = ['Fast mode', 'usage credits', 'Tip:', 'thinking', 'tokens)', 'esc to interrupt', 'auto mode',
         'what language do they speak', 'flow:', 'plugin.json', 'esc to cancel', 'Shift+Tab', 'for shortcuts']


def main():
    test_codex_screen.ROWS, test_codex_screen.COLS = 50, 120
    failures = []
    for name, (profile, expected) in CASES.items():
        entries = [(e['timestamp'], e['raw_bytes'])
                   for e in SessionRecorder.parse_file(os.path.join(FIXTURES, name)) if e['event_type'] == 'OUTPUT']
        text = ' '.join(' '.join(test_codex_screen.replay_timed(entries, profile)).split())
        problems = [f'missing {e!r}' for e in expected if e not in text]
        problems += [f'spoke {n!r}' for n in NEVER if n in text]
        problems += [f'{e!r} spoken {text.count(e)} times' for e in expected if text.count(e) > 1]
        print(f'  [{"ok" if not problems else "FAIL"}] {name}')
        failures.extend(f'{name}: {p}' for p in problems)

    if failures:
        print('\nFAILED:')
        for failure in failures:
            print(f'  {failure}')
        return 1
    print('\nAll checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())

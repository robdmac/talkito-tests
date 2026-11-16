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
    # Claude Code v2.1.325 with a mod's animated band above the prompt (flow), repainted many times a
    # second; it is no part of the transcript and used to be read out frame by frame, the reply with it
    'claude_v21325_band_long_reply.session': ('claude', [
        "Lisbon is one of Europe's oldest cities, older than London, Paris and Rome.",
        "In 1147 Afonso Henriques recaptured Lisbon with help from crusaders",
        'The Marquis of Pombal then rebuilt the Baixa district on a modern grid.',
        'Belém Tower: a 16th-century Manueline fortress on the Tagus that guarded the harbour entrance.',
        "São Jorge Castle: a Moorish-era castle on Lisbon's highest hill, with views over the Alfama district and the river.",
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
    # OpenCode v1.18.35 on a slow model that stalled for up to 25s part way through rows, with its
    # status row's last letter painted over ('esc interrup▀')
    'opencode_v11835_stall.session': ('opencode', [
        'making Lisbon Portugal',
        'balancing its imperial past with a forward-looking identity amid seismic history and Atlantic connections.',
        "Belém Tower: 16th-century Manueline tower marking Portugal's Age of Discovery and maritime heritage.",
        "Jerónimos Monastery: Ornate late-Gothic/Manueline monastery built to commemorate Vasco da Gama's voyages.",
    ]),
    # OpenCode v2.3.0: a row of session tabs heads the screen, the turn footer lost its ▣, reasoning
    # collapses to '+ 1 thought', and flow draws its scene just above the input box
    'opencode_v230_multi_turn.session': ('opencode', [
        'The capital of Peru is Lima.',
        "A famous Peruvian dish is ceviche, made from fresh raw fish cured in lime juice.",
        "It's typically mixed with onions, chili peppers, and cilantro, and served with sweet potato or corn.",
    ]),
    # pi v1.1.0, whose prompt echo and replies carry no marker at all; its reasoning summary is shown and read
    'pi_v110_long_reply.session': ('pi', [
        "Lisbon's history stretches back more than two millennia.",
        'a center of maritime trade and exploration during the fifteenth and sixteenth centuries.',
        'which restored democracy.',
        'Belém Tower — a sixteenth-century riverside fortification.',
        'São Jorge Castle — a hilltop fortress overlooking the city.',
    ]),
    'pi_v110_multi_turn.session': ('pi', [
        'The capital of Peru is Lima.',
        'typically served with onion, chili, and sweet potato.',
    ]),
}
# Rows read out before the model had finished them
PARTIAL = {'opencode_v11835_stall.session': ['contemporary energy.', 'Bel.', 'Ornate late.', '**Bel']}
# Chrome that used to be read out: status notices, spinner words, tips and the suggested next prompt
NEVER = ['Fast mode', 'usage credits', 'Tip:', 'thinking', 'tokens)', 'esc to interrupt', 'auto mode',
         'what language do they speak', 'flow:', 'plugin.json', 'esc to cancel', 'Shift+Tab', 'for shortcuts',
         # pi's banner, the echo of the prompt and its working spinner
         'Pi can explain', 'escape interrupt', 'Write two paragraphs', 'In one sentence', 'Working',
         # a mod's band above Claude Code's prompt: its collapse mark and its drawing
         '[-]', '⠄', '▒',
         # OpenCode 2.3's session tabs, its turn footer and its collapsed reasoning
         'New session', 'ttft', 'tok/s', 'thought']


def main():
    test_codex_screen.ROWS, test_codex_screen.COLS = 50, 120
    failures = []
    for name, (profile, expected) in CASES.items():
        entries = [(e['timestamp'], e['raw_bytes'])
                   for e in SessionRecorder.parse_file(os.path.join(FIXTURES, name)) if e['event_type'] == 'OUTPUT']
        text = ' '.join(' '.join(test_codex_screen.replay_timed(entries, profile)).split())
        problems = [f'missing {e!r}' for e in expected if e not in text]
        problems += [f'spoke {n!r}' for n in NEVER + PARTIAL.get(name, []) if n in text]
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

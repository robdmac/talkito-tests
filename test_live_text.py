#!/usr/bin/env python3

"""Checks live dictation drawn in grey at the caret, and that the terminal is left as the program believes it is."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from talkito import core
from talkito import live_text as live_text_module
from talkito.live_text import LiveText
from talkito.profiles import get_profile
from talkito.screen import Screen, apply_sgr, sgr
from talkito.state import get_shared_state

ROWS, COLS = 10, 40

# Codex: a dim placeholder after the caret
CODEX = (b'\x1b[?1049h\x1b[1;1HHello\x1b[8;1H\x1b[1m\xe2\x80\xba\x1b[0m '
         b'\x1b[2mAsk Codex to do anything\x1b[0m\x1b[8;3H')
# OpenCode: a shaded input field with a box edge on the left, and on the right
OPENCODE = (b'\x1b[?1049h\x1b[6;3H\x1b[38;5;99m\xe2\x94\x83\x1b[48;5;236m  \x1b[38;5;245mAsk anything'
            b'\x1b[48;5;236m' + b' ' * 18 + b'\x1b[0m\x1b[38;5;99m\xe2\x94\x83\x1b[0m\x1b[6;6H')


def check(ok, label, problems, detail=''):
    print(f'  [{"ok" if ok else "FAIL"}] {label}')
    if not ok:
        problems.append(f'{label}{": " + detail if detail else ""}')


class Terminal:
    """The program's screen as the program believes it is, and the terminal as the person sees it."""

    def __init__(self, program_output: bytes):
        self.program = Screen(ROWS, COLS)
        self.seen = Screen(ROWS, COLS)
        self.live = LiveText()
        self.program_writes(program_output)

    def program_writes(self, data: bytes):
        self.program.feed(data)
        self.seen.feed(data)
        self.live.on_output()

    def settle(self):
        """Let the program go quiet, then draw what the live text needs."""
        time.sleep(live_text_module.QUIET_SECONDS + 0.01)
        frame = self.live.frame(self.program)
        self.seen.feed(frame)
        return frame

    def matches(self) -> bool:
        return self.seen.grid == self.program.grid and self.seen.styles == self.program.styles


def row(screen: Screen, r: int) -> str:
    return ''.join(c for c in screen.grid[r] if c is not None).rstrip()


def check_codex(problems):
    term = Terminal(CODEX)
    term.live.set('what git')
    term.settle()
    check(row(term.seen, 7) == '› what git', 'live text shows at the caret over the placeholder', problems,
          repr(row(term.seen, 7)))
    check(term.seen.styles[7][2][0] == '90', 'live text is grey', problems, repr(term.seen.styles[7][2]))
    check((term.seen.cursor_row, term.seen.cursor_col) == (7, 2) and term.seen.style == term.program.style,
          'the cursor and style go back to where the program left them', problems)
    check(term.program.grid[7][2] == 'A', 'the program still believes its placeholder is there', problems)

    term.live.set('what git branch am I on')
    term.settle()
    check(row(term.seen, 7) == '› what git branch am I on', 'live text is replaced as more is heard', problems,
          repr(row(term.seen, 7)))
    term.live.set('what git bra')
    term.settle()
    check(row(term.seen, 7) == '› what git bra', 'shorter live text leaves nothing of the longer behind', problems,
          repr(row(term.seen, 7)))

    # The final words are typed: the program clears its placeholder and draws them
    term.live.set('')
    term.settle()
    check(term.matches(), 'clearing it puts back every cell as the program drew it', problems)
    term.program_writes(b'\x1b[8;3H\x1b[K' + 'What git branch am I on?'.encode() + b'\x1b[8;27H')
    check(term.matches() and row(term.seen, 7) == '› What git branch am I on?',
          'the final text then shows as the program draws it', problems, repr(row(term.seen, 7)))


def check_program_repaints_under_it(problems):
    term = Terminal(CODEX)
    term.live.set('hello there')
    term.settle()
    # The program repaints its status line and moves the caret about, unaware of the overlay
    term.program_writes(b'\x1b[1;1H\x1b[32mWorking\x1b[0m\x1b[8;3H')
    frame = term.settle()
    check(row(term.seen, 7) == '› hello there' and row(term.seen, 0) == 'Working',
          'live text is redrawn after the program writes', problems, repr(frame))
    term.program_writes(b'\x1b[8;3H\x1b[2mAsk Codex to do anything\x1b[0m\x1b[8;3H')
    term.settle()
    check(row(term.seen, 7) == '› hello there', 'and after the program repaints the cells beneath it', problems,
          repr(row(term.seen, 7)))
    term.live.set('')
    term.settle()
    check(term.matches(), 'the terminal matches the program once it goes', problems)


def check_opencode(problems):
    term = Terminal(OPENCODE)
    term.live.set('what git branch am I on and why is it this long')
    term.settle()
    shown = row(term.seen, 5)
    check(shown.startswith('  ┃  …') and shown.endswith('┃') and 'this long' in shown,
          'long live text shows its end within the field\'s edges', problems, repr(shown))
    check(all(term.seen.styles[5][c][1] == '48;5;236' for c in range(5, 30)),
          'the field keeps its shading under the live text', problems)
    term.live.set('')
    term.settle()
    check(term.matches(), 'clearing it puts back the shading and colours', problems)


def check_quiet(problems):
    term = Terminal(CODEX)
    term.live.set('what')
    term.program_writes(b'\x1b[1;1HHello')
    check(term.live.frame(term.program) == b'', 'nothing is drawn while the program is writing', problems)
    term.program_writes(b'\x1b[?2026h\x1b[1;1H')
    time.sleep(live_text_module.QUIET_SECONDS + 0.01)
    check(term.live.frame(term.program) == b'', 'nor inside a frame the program has not finished', problems)
    term.program_writes(b'\x1b[?2026l')
    check(term.settle() != b'', 'it is drawn once the frame ends', problems)


def check_sgr(problems):
    style = apply_sgr(('', '', ''), '1;38;2;10;20;30;48;5;236')
    check(style == ('38;2;10;20;30', '48;5;236', '1'), 'colours and attributes are read', problems, repr(style))
    style = apply_sgr(style, '22;39')
    check(style == ('', '48;5;236', ''), 'and unset', problems, repr(style))
    check(sgr(('31', '', '14')) == '\x1b[0;1;4;31m', 'a style is written back in one sequence', problems)


def check_core(problems):
    """Partial transcripts go to the live text for programs read as a screen, and a final one clears it."""
    profile, enabled, fd = core.active_profile, get_shared_state().asr_enabled, core.current_master_fd
    core.active_profile = get_profile('codex')
    core.current_master_fd = None
    get_shared_state().asr_enabled = True
    try:
        core.handle_partial_transcript('what git')
        check(core.live_text.text == 'what git' and not core.asr_state.current_partial,
              'a partial transcript becomes the live text, not text spliced into the output', problems)
        core.handle_dictated_text('What git branch am I on?')
        check(core.live_text.text == '', 'the final transcript clears it', problems, repr(core.live_text.text))
    finally:
        core.active_profile, core.current_master_fd = profile, fd
        get_shared_state().asr_enabled = enabled


def main():
    problems = []
    check_sgr(problems)
    check_codex(problems)
    check_program_repaints_under_it(problems)
    check_opencode(problems)
    check_quiet(problems)
    check_core(problems)
    if problems:
        print('\nFAILED:')
        for problem in problems:
            print(f'  {problem}')
        sys.exit(1)
    print('\nAll checks passed')


if __name__ == '__main__':
    main()

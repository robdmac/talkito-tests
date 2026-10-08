#!/usr/bin/env python3

"""Checks when the screen reader releases rows and decides a turn is over, and that listening comes back."""

import fcntl
import os
import pty
import re
import select
import signal
import stat
import struct
import sys
import tempfile
import termios
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_codex_screen
from talkito import core
from talkito.core import SessionRecorder
from talkito.profiles import get_profile
from talkito.screen import Screen, ScreenTranscript

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A stand-in for Codex that paints a reply in synchronized updates and quits straight away, leaving
# its last row on the main screen where nothing paints below it
FAKE_AGENT = r'''#!/usr/bin/env python3
import sys, time
out = sys.stdout.buffer
out.write(b'\x1b[?2026h\x1b[2J\x1b[1;1H\xe2\x80\xa2 The first row of the reply.\x1b[?2026l')
out.flush()
time.sleep(0.3)
out.write(b'\x1b[?2026h\x1b[2;1H  And the very last row, still held back when the agent quits.\x1b[?2026l')
out.flush()
open(__file__ + '.exited', 'w').write(repr(time.time()))
'''


def check_screen():
    """A colour change at the last column leaves the wrap pending; moving the cursor cancels it."""
    failures = []
    for name, data, want in [('colour keeps a pending wrap', b'12345\x1b[31mX', ['12345', 'X']),
                             ('cursor move cancels a pending wrap', b'12345\x1b[1;5HX', ['1234X', ''])]:
        screen = Screen(3, 5)
        screen.feed(data)
        got = screen.lines()[:2]
        print(f'  [{"ok" if got == want else "FAIL"}] {name}')
        if got != want:
            failures.append(f'{name}: got {got!r}, expected {want!r}')
    return failures


def check_transcript():
    """Rows are only released whole, and every turn's footer counts, even one reading like the last."""
    failures = []

    def expect(name, ok, detail):
        print(f'  [{"ok" if ok else "FAIL"}] {name}')
        if not ok:
            failures.append(f'{name}: {detail}')

    # Output that arrived since the last frame was read may be half a frame, so nothing is released yet
    transcript = ScreenTranscript(5, 40, quiet_seconds=0.05)
    transcript.feed(b'first line\r\nsecond li', 0.0)
    transcript.flush_idle(0.1)
    transcript.feed(b'ne still grow', 2.2)
    rows, _ = transcript.flush_idle(2.201)
    expect('idle flush waits for output to pause', rows == [], f'released {rows!r} 1ms after new output')
    transcript.flush_idle(4.3)
    rows, _ = transcript.flush_idle(6.4)
    expect('idle flush releases the row once quiet', rows == ['second line still grow'], f'released {rows!r}')

    transcript = ScreenTranscript(6, 40, composer_pattern=r'^❯', turn_end_pattern=r'^✻ \S+ for \d+s')
    ends = [transcript.feed(frame.encode(), 0)[1] for frame in [
        '\x1b[H\x1b[2Jreply one\r\n✻ Baked for 2s\r\n❯ ',
        '\x1b[H\x1b[2Jreply two streaming\r\n\r\n❯ ',
        '\x1b[H\x1b[2Jreply two done\r\n✻ Baked for 2s\r\n❯ ']]
    expect('a footer reading like the last one still ends the turn', ends == [True, False, True], f'got {ends}')

    # A spinner repainted faster than the pause that ends a frame is still seen, so its going ends the turn
    transcript = ScreenTranscript(6, 40, quiet_seconds=0.05, busy_pattern='esc to cancel')
    transcript.feed(b'> question', 0.0)
    transcript.flush_idle(0.1)
    for step in range(20):
        transcript.feed(f'\x1b[3;1H{"⠋⠙⠹⠸"[step % 4]} Thinking (esc to cancel)'.encode(), 0.2 + step * 0.02)
        transcript.flush_idle(0.2 + step * 0.02 + 0.01)
    transcript.feed(b'\x1b[3;1H\x1b[2K\x1b[2;1H\xe2\x9c\xa6 The answer.', 0.7)
    _, ended = transcript.flush_idle(0.8)
    expect('a spinner that never pauses still marks the turn busy', ended, 'no turn end')

    # The last row sitting still says nothing about a row above it that was just repainted
    transcript = ScreenTranscript(6, 40, quiet_seconds=0.05, settle_frames=4)
    transcript.feed(b'alpha row\r\nbottom row', 0.0)
    transcript.flush_idle(0.1)
    transcript.feed(b'\x1b[1;1Halpha row now chang', 2.0)
    rows, _ = transcript.flush_idle(2.1)
    expect('idle release waits for rows above the last to settle', rows == [], f'released {rows!r}')

    # One read holding a turn's end and the start of the next reply leaves the program replying
    frames = [f'\x1b[?2026h\x1b[H\x1b[2J{body}\x1b[?2026l'.encode() for body in [
        '• reply one\r\n\r\n─ Worked for 2s\r\n\r\n› ',
        '• reply one\r\n\r\n─ Worked for 2s\r\n\r\n• reply two first row\r\n• reply two second\r\n\r\n› ']]
    codex = get_profile('codex')
    for how, chunks in (('separate', frames), ('combined', [b''.join(frames)])):
        transcript = ScreenTranscript(10, 60, codex.screen_composer_pattern, codex.screen_live_pattern,
                                      codex.screen_turn_end_pattern)
        ended = [transcript.feed(chunk, 0)[1] for chunk in chunks][-1]
        expect(f'turn end then a new reply, {how}, ends replying', not ended, f'turn_ended={ended}')

    # A turn's end and the start of a one-row reply, still held, in the same read leaves it replying
    one_row = frames[0], frames[1].replace('• reply two second\r\n'.encode(), b'').replace(
        b'\r\n\r\n\xe2\x80\xba', b'\r\n  Working (esc to interrupt)\r\n\xe2\x80\xba')
    transcript = ScreenTranscript(10, 60, codex.screen_composer_pattern, codex.screen_live_pattern,
                                  codex.screen_turn_end_pattern, busy_pattern=codex.screen_busy_pattern)
    rows, ended = transcript.feed(b''.join(one_row), 0)
    expect('turn end then a one-row reply in one read ends replying', not ended and transcript.busy,
           f'turn_ended={ended} busy={transcript.busy} rows={rows}')

    # Once the program has exited, a reply left under its busy row is all there will be
    transcript = ScreenTranscript(10, 60, codex.screen_composer_pattern, codex.screen_live_pattern,
                                  codex.screen_turn_end_pattern, busy_pattern=codex.screen_busy_pattern)
    transcript.feed('\x1b[?2026h\x1b[H\x1b[2J• the whole reply\r\n\r\n  Working (esc to interrupt)'
                    '\r\n\r\n› \x1b[?2026l'.encode(), 0)
    rows, _ = transcript.flush_idle(float('inf'))
    expect('final flush releases a reply under a busy row', rows == ['• the whole reply'], f'released {rows!r}')

    # An empty Codex composer is just its marker once trailing blanks are dropped
    transcript = ScreenTranscript(10, 60, codex.screen_composer_pattern, codex.screen_live_pattern,
                                  codex.screen_turn_end_pattern)
    _, ended = transcript.feed(frames[0], 0)
    expect('turn ends above an empty Codex composer', ended, 'no turn end')

    # With no footer, the busy row going away ends the turn and releases the last row with it
    transcript = ScreenTranscript(6, 40, busy_pattern='esc to cancel')
    results = [transcript.feed(frame.encode(), 0) for frame in [
        '\x1b[H\x1b[2J> question\r\n⠋ Thinking (esc to cancel)',
        '\x1b[H\x1b[2J> question\r\n✦ The answer.']]
    expect('busy row going away ends the turn', [ended for _, ended in results] == [False, True], f'got {results}')
    expect('and releases the last row', results[1][0][-1:] == ['✦ The answer.'], f'got {results[1][0]}')
    return failures


def spy_listening(run):
    """Run a replay and return (row, waiting_for_input) after every row the screen reader processed."""
    states = []
    original = core._update_input_state

    def spy(line, detected_prompt, asr_mode):
        original(line, detected_prompt, asr_mode)
        states.append((line, detected_prompt, core.asr_state.waiting_for_input))

    core._update_input_state = spy
    try:
        run()
    finally:
        core._update_input_state = original
    return states


def check_listening():
    """After each turn's reply the program is back at its composer, so listening is back on."""
    failures = []
    test_codex_screen.ROWS, test_codex_screen.COLS = 50, 120
    for name, profile, replies in [('claude_v21293_multi_turn.session', 'claude', 2),
                                   ('gemini_v0630_multi_turn.session', 'gemini', 2)]:
        entries = [(e['timestamp'], e['raw_bytes'])
                   for e in SessionRecorder.parse_file(os.path.join(FIXTURES, name)) if e['event_type'] == 'OUTPUT']
        states = spy_listening(lambda: test_codex_screen.replay_timed(entries, profile))
        ends = [i for i, (line, detected, _) in enumerate(states) if detected and not line]
        ok = len(ends) == replies and states[-1][2]
        print(f'  [{"ok" if ok else "FAIL"}] {name}: {len(ends)} turn ends, listening at the end')
        if not ok:
            failures.append(f'{name}: {len(ends)} turn ends, listening={states[-1][2] if states else None}')

    # OpenCode's footer row is released with the turn end, so it no longer reads as a reply afterwards
    # (the rows that follow are its exit banner, once the session is quit)
    test_codex_screen.ROWS, test_codex_screen.COLS = 40, 120
    data = open(os.path.join(FIXTURES, 'opencode_v11835_screen.bin'), 'rb').read()
    # Up to quitting, which in one big read would otherwise land with the turn's end and supersede it
    session = data[:data.index(b'\x1b[?1049l')]
    for how, chunks in [('frames', [c for c in re.split(rb'(?<=\x1b\[\?2026l)', data) if c]),
                        ('16KB reads', [session[i:i + 16384] for i in range(0, len(session), 16384)])]:
        states = spy_listening(lambda: test_codex_screen.replay(data, chunks, profile_name='opencode'))
        end = next((i for i, (line, detected, _) in enumerate(states) if detected and not line), None)
        after = [line for line, _, _ in states[end + 1:]] if end is not None else []
        ok = end is not None and states[end][2] and not any('▣' in line for line in after)
        print(f'  [{"ok" if ok else "FAIL"}] opencode {how}: listening stays on after the turn ends')
        if not ok:
            failures.append(f'opencode {how}: turn end at {end}, rows after it {after[:3]!r}')
    return failures


def check_exit():
    """When the agent quits, the row it left last is still spoken."""
    workdir = tempfile.mkdtemp(prefix='talkito-exit-')
    for name, body in [('codex', FAKE_AGENT), ('say', '#!/bin/sh\nexit 0\n')]:
        path = os.path.join(workdir, name)
        with open(path, 'w') as f:
            f.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    log = os.path.join(workdir, 'talkito.log')
    env = dict(os.environ, PATH=workdir + os.pathsep + os.environ['PATH'], TERM='xterm-256color')
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(REPO)
        os.execvpe(sys.executable, [sys.executable, '-m', 'talkito', '--log-file', log, '--asr-mode', 'off',
                                    '--tts-provider', 'system', 'codex'], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
    exited = os.path.join(workdir, 'codex.exited')
    key_at = None
    end = time.time() + 60
    while time.time() < end:
        if key_at is None and os.path.exists(exited):
            # talkito notices the agent has gone on its next input, here a key pressed soon after,
            # well before a held row would be released for having sat still
            key_at = time.time() + 0.3
        if key_at and time.time() >= key_at:
            os.write(fd, b' ')
            key_at = float('inf')
        ready, _, _ = select.select([fd], [], [], 0.05)
        if ready:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if b'\x1b[6n' in data:
                os.write(fd, b'\x1b[1;1R')
        text = open(log, errors='replace').read() if os.path.exists(log) else ''
        if 'after subprocess completion' in text:
            time.sleep(1)
            break
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    spoken = re.findall(r"Speaking via \S+.*?: '(.*)'$", open(log, errors='replace').read(), re.M) \
        if os.path.exists(log) else []
    ok = any('very last row' in s for s in spoken)
    print(f'  [{"ok" if ok else "FAIL"}] last row spoken after the agent quits')
    return [] if ok else [f'exit: last row not spoken, spoke {spoken!r}']


def main():
    failures = check_screen() + check_transcript() + check_listening() + check_exit()
    if failures:
        print('\nFAILED:')
        for failure in failures:
            print(f'  {failure}')
        return 1
    print('\nAll checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())

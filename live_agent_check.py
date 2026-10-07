#!/usr/bin/env python3

"""Drives `talkito <agent>` live in a PTY and checks what it speaks against what the agent showed.

Not part of the regular suite: every scenario sends real prompts, so it uses agent quota and takes
minutes. Speech is silenced with a no-op `say` unless --audible is passed; the talkito log still
records every line handed to the TTS engine, which is what gets checked.

    python tests-repo/live_agent_check.py --agent codex
    python tests-repo/live_agent_check.py --agent claude --scenario slashes multi_turn
"""

import argparse
import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import tempfile
import termios
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from talkito.screen import Screen

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEAKING_PATTERN = re.compile(r"Speaking via \S+(?: \(line \d+\) qsize \d+)?: '(.*)'$")
WORD_PATTERN = re.compile(r"[a-z0-9]+")

# Where each agent's reply sits on screen: the echo of the prompt opens it and the first matching
# end row closes it
AGENTS = {
    'codex': {
        'ready': re.compile(r'^› '),
        'prompt_echo': '› ',
        'reply_end': re.compile(r'^\s*(?:─\s*)?Worked for \d|^› '),
        'chrome': re.compile(r'^\s*(?:• (?:Ran|Explored|Edited|Working)|└|\+ Show details)'),
    },
    'claude': {
        'ready': re.compile(r'^❯'),
        'prompt_echo': '❯ ',
        'reply_end': re.compile(r'^\s*[✻✶✳✢✽·*]\s+\w+ for \d|^❯|^─{5,}'),
        'chrome': re.compile(r'^\s*(?:⎿|⏺ (?:Bash|Read|Update|Write|Search)\()'),
    },
    'gemini': {
        'ready': re.compile(r'^\s*>\s'),
        # The prompt echo does not survive Gemini's redraws, so replies are found from the ✦ marker
        'prompt_echo': None,
        'reply_end': re.compile(r'^▄{10,}$'),
        'chrome': re.compile(r'esc to cancel|\? for shortcuts|Shift\+Tab|^\s*[▀▄─]{10,}'),
        'reply_start': re.compile(r'^✦'),
    },
    'opencode': {
        'ready': re.compile(r'^\s*╹▀'),
        'prompt_echo': '  ┃  ',
        'reply_end': re.compile(r'^\s*▣.*\d+(?:\.\d+)?s\s*$|^\s*╹▀'),
        'chrome': re.compile(r'^\s*(?:┃|\+ Thought|▣)'),
        # Replies carry no marker; they start at the first row that is not the prompt's ┃ block
        'reply_start': re.compile(r'^\s*(?!┃|\+ Thought)\S'),
    },
}
REPLY_START = re.compile(r'^\s*[•⏺]')

NO_TOOLS = ' Do not run any commands or use any tools.'


class Session:
    """talkito running an agent in a PTY, with a virtual screen of everything it has drawn."""

    def __init__(self, agent: str, rows: int, cols: int, workdir: str, audible: bool, agent_args=()):
        self.agent = agent
        self.rows, self.cols = rows, cols
        self.log_path = os.path.join(workdir, f'{agent}.log')
        self.raw_path = os.path.join(workdir, f'{agent}.out')
        # The same reads with their timing, in talkito's --record format, for offline replays
        self.session_path = os.path.join(workdir, f'{agent}.session')
        self.screen = Screen(rows, cols)
        self.raw = open(self.raw_path, 'wb')
        self.session = open(self.session_path, 'wb')
        self.started = time.time()
        self.last_output = time.time()

        env = os.environ.copy()
        env['TERM'] = 'xterm-256color'
        # Run as a fresh top-level session, not as a child of whatever Claude Code launched this script
        for name in [n for n in env if n in ('CLAUDECODE', 'CLAUDE_EFFORT', 'CLAUDE_PID') or n.startswith('CLAUDE_CODE_')]:
            del env[name]
        if not audible:
            quiet_bin = os.path.join(workdir, 'quiet-bin')
            os.makedirs(quiet_bin, exist_ok=True)
            say = os.path.join(quiet_bin, 'say')
            with open(say, 'w') as f:
                f.write('#!/bin/sh\nexit 0\n')
            os.chmod(say, 0o755)
            env['PATH'] = quiet_bin + os.pathsep + env['PATH']

        cmd = ['talkito', '--log-file', self.log_path, '--asr-mode', 'off', '--tts-provider', 'system', agent,
               *agent_args]
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(PROJECT_ROOT)
            os.execvpe(cmd[0], cmd, env)
        self._set_size(rows, cols)

    def _set_size(self, rows: int, cols: int):
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def pump(self, seconds: float) -> bool:
        """Read output for a while, answering cursor position queries; False once talkito exits."""
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.fd], [], [], 0.1)
            if not ready:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return False
            if not data:
                return False
            self.raw.write(data)
            self.session.write(f'{time.time() - self.started:.6f} OUTPUT {len(data):04x} '.encode() + data + b'\x00')
            self.screen.feed(data)
            self.last_output = time.time()
            if b'\x1b[6n' in data:
                os.write(self.fd, f'\x1b[{self.screen.cursor_row + 1};{self.screen.cursor_col + 1}R'.encode())
        return True

    def wait_until(self, condition, timeout: float, quiet: float = 0.0) -> bool:
        """Pump until condition() holds and output has been quiet for `quiet` seconds."""
        end = time.time() + timeout
        while time.time() < end:
            if not self.pump(0.5):
                return False
            if condition() and time.time() - self.last_output >= quiet:
                return True
        return False

    def lines(self):
        return self.screen.lines()

    def type(self, text: str, attempts: int = 3) -> bool:
        """Type a prompt and submit it, retyping if the agent dropped keys while it was busy drawing."""
        # Compare without spaces or box edges, since the input box wraps long prompts
        def squash(value):
            return re.sub(r'[\s┃│]', '', value)
        head, tail = squash(text[:20]), squash(text[-20:])
        for _ in range(attempts):
            for char in text:
                os.write(self.fd, char.encode())
                time.sleep(0.02)
                self.pump(0.0)
            self.pump(1.0)
            shown = squash(''.join(self.lines()))
            if head in shown and tail in shown:
                os.write(self.fd, b'\r')
                return True
            # Clear whatever did land and try again once things settle
            os.write(self.fd, b'\x7f' * (len(text) + 20))
            self.pump(3.0)
        os.write(self.fd, b'\r')
        return False

    def key(self, data: bytes):
        os.write(self.fd, data)

    def resize(self, rows: int, cols: int):
        """Resize the PTY; the kernel signals talkito, which passes the new size on to the agent."""
        self.rows, self.cols = rows, cols
        # Terminals differ in how they reflow on resize; agents repaint anyway, so start blank
        self.screen = Screen(rows, cols)
        self._set_size(rows, cols)

    def log_size(self) -> int:
        return os.path.getsize(self.log_path) if os.path.exists(self.log_path) else 0

    def spoken_since(self, offset: int):
        """Lines handed to the TTS engine since a log offset."""
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path, 'rb') as f:
            f.seek(offset)
            text = f.read().decode('utf-8', 'replace')
        return [m.group(1) for m in map(SPEAKING_PATTERN.search, text.splitlines()) if m]

    def alive(self) -> bool:
        try:
            pid, _ = os.waitpid(self.pid, os.WNOHANG)
            return pid == 0
        except ChildProcessError:
            return False

    def close(self):
        for _ in range(2):
            try:
                os.write(self.fd, b'\x03')
            except OSError:
                break
            self.pump(1.5)
        if self.alive():
            os.kill(self.pid, signal.SIGTERM)
            self.pump(3)
        self.raw.close()
        self.session.close()


def words(text: str):
    return WORD_PATTERN.findall(text.lower().replace('’', "'").replace("'", ''))


def shingles(tokens, size=6):
    return [' '.join(tokens[i:i + size]) for i in range(len(tokens) - size + 1)]


class Turn:
    """One prompt and its reply."""

    def __init__(self, session: Session, prompt: str):
        self.session = session
        self.prompt = prompt
        self.spec = AGENTS[session.agent]
        self.log_offset = session.log_size()
        self.reply_rows = []
        self.spoken = []

    def send(self):
        # Replies already on screen, so one without a prompt echo can tell its own reply apart
        start = self.spec.get('reply_start', REPLY_START)
        self.replies_before = {row for row in self.session.lines() if start.search(row)}
        self.session.type(self.prompt)

    def _reply_rows(self):
        """Rows between this prompt's echo and the end of the reply, or None until the reply ends."""
        lines = self.session.lines()
        head = self.prompt[:30]
        if self.spec['prompt_echo'] is None:
            # No echo to anchor on: take the last reply on screen, once this turn has produced one
            starts = [i for i, row in enumerate(lines)
                      if self.spec['reply_start'].search(row) and row not in self.replies_before]
            if not starts:
                return None
            begin = starts[-1]
        else:
            echoes = [i for i, row in enumerate(lines) if row.startswith(self.spec['prompt_echo']) and head in row]
            if not echoes:
                return None
            begin = echoes[-1] + 1
        rows = []
        for row in lines[begin:]:
            if self.spec['reply_end'].search(row):
                return rows if any(r.strip() for r in rows) else None
            rows.append(row)
        return None

    def wait(self, timeout: float = 180) -> bool:
        done = self.session.wait_until(lambda: self._reply_rows() is not None, timeout, quiet=4)
        self.reply_rows = self._reply_rows() or []
        # The line reader holds text for up to two seconds before handing it to the speech queue
        self.session.pump(4)
        self.spoken = self.session.spoken_since(self.log_offset)
        return done

    def rows(self):
        """Reply rows, without any wrapped tail of the prompt that precedes the first bullet."""
        rows = list(self.reply_rows)
        start = self.spec.get('reply_start', REPLY_START)
        while rows and not start.search(rows[0]):
            rows.pop(0)
        return [r for r in rows if r.strip() and not self.spec['chrome'].search(r)]

    def reply_text(self) -> str:
        return ' '.join(r.strip() for r in self.rows())

    def check(self, min_coverage: float = 0.95, ignore=()):
        """Problems with what was spoken for this turn, judged against what was shown.

        ignore lists words that are expected to be dropped, such as the directories of a path.
        """
        problems = []
        shown = [w for w in words(self.reply_text()) if len(w) >= 4 and not w.isdigit() and w not in ignore]
        if not shown:
            # A very short reply such as '4' has no long words to go on
            shown = [w for w in words(self.reply_text()) if w not in ignore]
        heard = words(' '.join(self.spoken))
        if not shown:
            return [f'no reply found on screen for {self.prompt[:40]!r}']

        heard_set = set(heard)
        missing = [w for w in shown if w not in heard_set]
        coverage = 1 - len(missing) / len(shown)
        if coverage < min_coverage:
            problems.append(f'only {coverage:.0%} of the reply was spoken; missing e.g. {missing[:8]}')

        # A whole missing line can hide inside an overall percentage, so each row is checked too
        for row in self.rows():
            row_words = [w for w in words(row) if len(w) >= 4 and not w.isdigit() and w not in ignore]
            if len(row_words) >= 3:
                row_missing = [w for w in row_words if w not in heard_set]
                if len(row_missing) > len(row_words) // 4:
                    problems.append(f'row not spoken: {row.strip()[:80]!r}')

        shown_shingles = shingles(words(self.reply_text()))
        heard_shingles = shingles(heard)
        repeated = sorted({s for s in heard_shingles
                           if heard_shingles.count(s) > max(1, shown_shingles.count(s))})
        if repeated:
            problems.append(f'{len(repeated)} phrases spoken more than once, e.g. {repeated[:2]}')
        return problems


def report(name: str, problems, turns):
    status = 'PASS' if not problems else 'FAIL'
    print(f'\n[{status}] {name}')
    for turn in turns:
        print(f'  prompt: {turn.prompt[:70]!r}')
        print(f'  shown:  {turn.reply_text()[:300]!r}')
        for line in turn.spoken:
            print(f'  spoke:  {line[:300]!r}')
    for problem in problems:
        print(f'  ! {problem}')
    return not problems


def scenario_long_reply(session):
    turn = Turn(session, 'Write two paragraphs of about 60 words each on the history of Lisbon, then a '
                         '3-item bullet list of its landmarks.' + NO_TOOLS)
    turn.send()
    problems = [] if turn.wait() else ['reply did not finish']
    return problems + turn.check(), [turn]


def scenario_slashes(session):
    turn = Turn(session, 'Reply with exactly this sentence and nothing else: The Codex/Gemini tool details '
                         'need read/write access to talkito/tts.py and about 1/2 of the time it fails.'
                         + NO_TOOLS)
    turn.send()
    problems = [] if turn.wait() else ['reply did not finish']
    spoken = ' '.join(turn.spoken).lower()
    for phrase in ('codex or gemini', 'read or write', 'tts dot py', '1/2'):
        if phrase not in spoken:
            problems.append(f'expected {phrase!r} in speech')
    return problems + turn.check(ignore={'talkito'}), [turn]


def scenario_multi_turn(session):
    first = Turn(session, 'In one sentence, what is the capital of Peru?' + NO_TOOLS)
    first.send()
    problems = [] if first.wait() else ['first reply did not finish']
    second = Turn(session, 'In two sentences, name a famous dish from that country and describe it.' + NO_TOOLS)
    second.send()
    if not second.wait():
        problems.append('second reply did not finish')
    problems += first.check() + second.check()
    # The first reply must not be read out again during the second turn
    again = set(shingles(words(' '.join(second.spoken)), 5)) & set(shingles(words(first.reply_text()), 5))
    if again:
        problems.append(f'first reply re-spoken in second turn, e.g. {sorted(again)[:2]}')
    return problems, [first, second]


def scenario_resize(session):
    first = Turn(session, 'In two sentences, describe the Amazon river.' + NO_TOOLS)
    first.send()
    problems = [] if first.wait() else ['reply before resize did not finish']
    session.resize(session.rows, session.cols - 24)
    session.pump(4)
    offset = session.log_size()
    second = Turn(session, 'In two sentences, describe the Nile river.' + NO_TOOLS)
    second.log_offset = offset
    second.send()
    if not second.wait():
        problems.append('reply after resize did not finish')
    problems += first.check() + second.check()
    again = set(shingles(words(' '.join(second.spoken)), 5)) & set(shingles(words(first.reply_text()), 5))
    if again:
        problems.append(f'reply from before the resize re-spoken, e.g. {sorted(again)[:2]}')
    return problems, [first, second]


def scenario_interrupt(session):
    turn = Turn(session, 'Write a 400 word essay on the history of printing.' + NO_TOOLS)
    turn.send()
    session.wait_until(lambda: any('printing' in r.lower() and len(r) > 60 and not r.lstrip().startswith(('┃', '› ', '❯'))
                                   for r in session.lines()), 90)
    session.pump(3)
    session.key(b'\x1b')
    session.pump(6)
    turn.spoken = session.spoken_since(turn.log_offset)
    problems = []
    after = Turn(session, 'In one sentence, what is 2 plus 2?' + NO_TOOLS)
    after.send()
    if not after.wait(120):
        problems.append('agent did not answer after the interrupt')
    problems += after.check()
    if not session.alive():
        problems.append('talkito exited')
    return problems, [turn, after]


def scenario_bare_number(session):
    turn = Turn(session, 'Reply with only the number and nothing else: what is 6 times 7?' + NO_TOOLS)
    turn.send()
    problems = [] if turn.wait() else ['reply did not finish']
    if not any(re.search(r'(?<![\d.])42(?![\d.])', line) for line in turn.spoken):
        problems.append("expected '42' to be spoken")
    return problems + turn.check(), [turn]


SCENARIOS = {
    'bare_number': scenario_bare_number,
    'long_reply': scenario_long_reply,
    'slashes': scenario_slashes,
    'multi_turn': scenario_multi_turn,
    'resize': scenario_resize,
    'interrupt': scenario_interrupt,
}


def run_agent(agent: str, scenarios, rows: int, cols: int, audible: bool, workdir: str, agent_args=()) -> bool:
    print(f'\n===== {agent} (logs in {workdir})')
    passed = True
    for name in scenarios:
        session = Session(agent, rows, cols, workdir, audible, agent_args)
        try:
            if not session.wait_until(lambda: any(AGENTS[agent]['ready'].search(r) for r in session.lines()),
                                      90, quiet=3):
                passed = report(name, ['agent never reached its prompt'], []) and passed
                continue
            # Some agents draw their input box a moment before they take keystrokes
            session.pump(3)
            problems, turns = SCENARIOS[name](session)
            passed = report(name, problems, turns) and passed
        finally:
            session.close()
            for path in (session.log_path, session.raw_path, session.session_path):
                if os.path.exists(path):
                    os.replace(path, os.path.join(workdir, f'{agent}-{name}{os.path.splitext(path)[1]}'))
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--agent', choices=['codex', 'claude', 'opencode', 'gemini', 'all'], default='all')
    parser.add_argument('--opencode-model', default='opencode/big-pickle',
                        help='provider/model for opencode (the default needs no credentials)')
    parser.add_argument('--scenario', nargs='+', choices=sorted(SCENARIOS), default=list(SCENARIOS))
    parser.add_argument('--rows', type=int, default=50)
    parser.add_argument('--cols', type=int, default=120)
    parser.add_argument('--audible', action='store_true', help='play speech instead of silencing it')
    parser.add_argument('--keep', help='directory for logs and captures (default: a temp dir)')
    args = parser.parse_args()

    workdir = args.keep or tempfile.mkdtemp(prefix='talkito-live-')
    os.makedirs(workdir, exist_ok=True)
    agents = ['codex', 'claude', 'opencode', 'gemini'] if args.agent == 'all' else [args.agent]
    agent_args = {'opencode': ['-m', args.opencode_model]}
    results = [run_agent(a, args.scenario, args.rows, args.cols, args.audible, workdir, agent_args.get(a, ()))
               for a in agents]
    print('\nAll scenarios passed' if all(results) else '\nSome scenarios failed')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

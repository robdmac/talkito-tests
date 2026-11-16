#!/usr/bin/env python3

"""Checks talkito passes a program's output on whole and in order when the terminal reads it slowly."""

import os
import pty
import random
import re
import select
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from talkito import core

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINES = 20000
LINE = re.compile(rb'\x1b\[38;2;1;2;3mline (\d{5})\x1b\[0m')

# Colored lines as fast as it can write them, then a pause so talkito can pass on the rest
FLOOD = f'''#!/usr/bin/env python3
import sys, time
for i in range({LINES}):
    sys.stdout.write("\\x1b[38;2;1;2;3mline %05d\\x1b[0m\\n" % i)
    # Many small writes, as a TUI repainting makes them
    if i % 7 == 0:
        sys.stdout.flush()
sys.stdout.flush()
time.sleep(3)
'''


def check_held_back_output_goes_first():
    """A partial write holds the rest back; output after it waits its turn even once there's room."""
    written = []
    room = [5]   # what the terminal takes on the next write, then everything once it has drained

    def fake_write(fd, data):
        take = len(data) if room[0] is None else min(room[0], len(data))
        room[0] = None
        written.append(bytes(data[:take]))
        return take

    real_write = core.os.write
    core.os.write = fake_write
    try:
        buffer = core.OutputBuffer()
        buffer.relay(b'\x1b[38;2;1;2;3mfirst')   # 5 bytes go out, the rest is held back
        buffer.relay(b'\x1b[0msecond')           # the terminal has room again
        buffer.write_to_stdout()
    finally:
        core.os.write = real_write
    out = b''.join(written)
    ok = out == b'\x1b[38;2;1;2;3mfirst\x1b[0msecond'
    print(f'  [{"ok" if ok else "FAIL"}] output held back from a partial write goes out before later output')
    return [] if ok else [f'the terminal got {out!r}']


def check_flood():
    workdir = tempfile.mkdtemp(prefix='talkito-relay-')
    program = os.path.join(workdir, 'flood')
    with open(program, 'w') as f:
        f.write(FLOOD)
    os.chmod(program, 0o755)

    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(PROJECT_ROOT)
        env = {**os.environ, 'HOME': workdir}
        os.execvpe(sys.executable, [sys.executable, '-m', 'talkito', '--disable-tts', '--asr-mode', 'off',
                                    '--disable-mcp', program], env)

    # Read nothing at first, so talkito's writes to the terminal back up and it must hold output back
    time.sleep(8)
    received = b''
    deadline = time.time() + 60
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 1.0)
        if not ready:
            if LINE.findall(received)[-1:] == [b'%05d' % (LINES - 1)]:
                break
            continue
        try:
            data = os.read(fd, random.choice((256, 4096, 65536)))
        except OSError:
            break
        if not data:
            break
        received += data
        # Drain in bursts with pauses between, so the terminal frees room while talkito holds output back
        if random.random() < 0.05:
            time.sleep(0.02)
    try:
        os.kill(pid, 9)
    except OSError:
        pass

    numbers = [int(n) for n in LINE.findall(received)]
    problems = []
    if numbers != list(range(LINES)):
        missing = sorted(set(range(LINES)) - set(numbers))
        out_of_order = sum(1 for a, b in zip(numbers, numbers[1:]) if b != a + 1)
        problems.append(f'{len(numbers)} of {LINES} lines arrived whole, {len(missing)} missing, '
                        f'{out_of_order} out of order')
    # A sequence broken by output written ahead of it shows as a CSI without its ESC
    broken = len(re.findall(rb'(?<!\x1b)\[38;2;1;2;3m', received))
    if broken:
        problems.append(f'{broken} escape sequences arrived broken')
    print(f'  [{"ok" if not problems else "FAIL"}] output passes through whole and in order under backpressure')
    return problems


def main():
    problems = check_held_back_output_goes_first() + check_flood()
    if problems:
        print('\nFAILED:')
        for problem in problems:
            print(f'  {problem}')
        sys.exit(1)
    print('\nAll checks passed')


if __name__ == '__main__':
    main()

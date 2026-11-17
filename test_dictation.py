#!/usr/bin/env python3

"""Checks dictation: echoes of talkito's own speech are dropped, and tap-to-talk hears a whole hold at once."""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from talkito import asr, tts
from talkito.state import get_shared_state

RATE = 16000


def check(ok, label, problems, detail=''):
    print(f'  [{"ok" if ok else "FAIL"}] {label}')
    if not ok:
        problems.append(f'{label}{": " + detail if detail else ""}')


def played(texts, started, ended=None, playing=False):
    """Make TTS look as if it played these texts, one after another from `started`."""
    tts.speech_history.clear()
    for i, text in enumerate(texts):
        item = tts.SpeechItem(text=text, original_text=text)
        item.start_time = started + i
        tts.speech_history.append(item)
    tts.current_speech_item = tts.speech_history[-1] if playing else None
    tts.last_speech_end_time = ended if ended is not None else 0.0


def check_echoes():
    problems = []
    now = time.time()
    # From a codex session: the key held while talkito read the reply out
    played(['I didn’t quite catch that—could you say it again?'], now - 4, ended=now - 1)
    check(tts.is_echo_of_speech("I didn't quite catch that. Could you say it again?", now - 5),
          'the whole of what was playing, heard back, is an echo', problems)
    played(['I was asking you to repeat your earlier message—“What get bunch of. That’s a myo.” '
            'It may have been transcribed incorrectly.'], now - 6, playing=True)
    check(tts.is_echo_of_speech('I was asking yo to repeat your early', now - 5),
          'a misheard stretch of what is still playing is an echo', problems)

    # The person's own words
    check(not tts.is_echo_of_speech('What git branch am I on?', now - 5),
          'their own question over speech that quoted a mishearing of it is kept', problems)
    played(['Should I run the tests and open a pull request?'], now - 20, ended=now - 15)
    check(not tts.is_echo_of_speech('Yes, run the tests and open a pull request.', now - 3),
          'an answer repeating the question after speech ended is kept', problems)
    played(['Should I run the tests and open a pull request?'], now - 4, playing=True)
    check(not tts.is_echo_of_speech('yes do it', now - 5),
          'a short reply while speech plays is kept', problems)
    check(not tts.is_echo_of_speech('Stop, just commit the change to the readme instead.', now - 5),
          'their own words over speech are kept', problems)
    played([], now)
    return problems


def check_vocabulary():
    problems = []
    os.environ.pop('TALKITO_ASR_TERMS', None)
    check('git branch' in asr.whisper_prompt() and asr.whisper_prompt().endswith('.'),
          'whisper is primed with coding terms', problems)
    os.environ['TALKITO_ASR_TERMS'] = 'Kokoro, pywhispercpp,'
    prompt = asr.whisper_prompt()
    os.environ.pop('TALKITO_ASR_TERMS')
    check(prompt.endswith(', Kokoro, pywhispercpp.'), 'TALKITO_ASR_TERMS adds to them', problems, repr(prompt))
    cleaned = asr.SOUND_TAG_CLEANER.sub('', 'What git branch am I on? (mouse clicks)').strip()
    check(cleaned == 'What git branch am I on?', 'sound tags are taken out', problems, repr(cleaned))
    check(asr.SOUND_TAG_CLEANER.sub('', 'call f(x) twice') == 'call f(x) twice',
          'brackets that are words are kept', problems)
    return problems


class FakeMic:
    """A microphone that plays a recording in real time, then silence."""

    def __init__(self, audio):
        self.audio = audio
        self.position = 0

    def open(self, **kwargs):
        return self

    def read(self, frames, exception_on_overflow=False):
        chunk = self.audio[self.position:self.position + frames]
        chunk = np.concatenate([chunk, np.zeros(frames - len(chunk), np.float32)])
        self.position += frames
        time.sleep(frames / RATE)
        return chunk.astype(np.float32).tobytes()

    def stop_stream(self):
        pass

    def close(self):
        pass

    def terminate(self):
        pass


def load_model():
    devnull = os.open(os.devnull, os.O_WRONLY)
    stderr = os.dup(2)
    os.dup2(devnull, 2)
    try:
        from pywhispercpp.model import Model
        asr._local_whisper_model = Model('small', n_threads=4, print_progress=False, print_realtime=False,
                                         print_timestamps=False, print_special=False)
    finally:
        os.dup2(stderr, 2)


def spoken(parts, workdir):
    """Audio of each (voice, text) said in turn, a pause after each, and where the speech starts and ends."""
    audio = [np.zeros(int(RATE * 0.5), np.float32)]
    for i, (voice, text) in enumerate(parts):
        wav = os.path.join(workdir, f'part{i}.wav')
        subprocess.run(['say', '-v', voice, '-o', wav, '--data-format=LEI16@16000', text], check=True)
        audio += [sf.read(wav, dtype='float32')[0], np.zeros(int(RATE * 0.6), np.float32)]
    speech_end = sum(len(a) for a in audio) - int(RATE * 0.6)
    audio.append(np.zeros(RATE * 3, np.float32))
    return np.concatenate(audio), int(RATE * 0.5), speech_end


def hold(audio, start, end):
    """Hold the key from start to end of the audio through the real capture loop; what's typed and shown live."""
    typed, live = [], []
    engine = SimpleNamespace(is_active=True, stop_event=threading.Event(), text_callback=typed.append,
                             partial_callback=live.append, recognizer=SimpleNamespace(energy_threshold=300),
                             _process_dictation_text=lambda text: text)
    shared = get_shared_state()
    mode = shared.asr_mode
    shared.asr_mode = 'tap-to-talk'
    real_pyaudio = asr.pyaudio
    mic = FakeMic(audio)
    asr.pyaudio = SimpleNamespace(PyAudio=lambda: mic, paFloat32=real_pyaudio.paFloat32)
    asr.set_ignore_input(True)
    provider = asr.FasterWhisperProvider(asr.ASRConfig(provider='local_whisper', model='small'))
    capture = threading.Thread(target=provider.stream, args=(engine, None), daemon=True)
    try:
        capture.start()
        # Press just before the speech starts, release just after it ends
        while mic.position < start - RATE // 4:
            time.sleep(0.01)
        asr.set_ignore_input(False)
        while mic.position < end + RATE // 4:
            time.sleep(0.01)
        typed_at_release = len(typed)
        asr.set_ignore_input(True)
        deadline = time.time() + 15
        while len(typed) == typed_at_release and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(1.5)   # anything more it would type
    finally:
        engine.is_active = False
        engine.stop_event.set()
        capture.join(timeout=5)
        asr.pyaudio = real_pyaudio
        shared.asr_mode = mode
        asr.set_ignore_input(False)
    return typed, [text for text in live if text], typed_at_release   # the live updates with words in them


def check_tap_to_talk_holds():
    """Holds the key over speech with the real capture loop and model, and reads what's typed and shown."""
    problems = []
    if platform.system() != 'Darwin' or not shutil.which('say'):
        print('  [skip] tap-to-talk holds need macOS say to speak')
        return problems
    load_model()
    workdir = tempfile.mkdtemp(prefix='talkito-dictation-')

    said = 'What git branch am I on?'
    typed, live, _ = hold(*spoken([('Daniel', said)], workdir))
    check(len(typed) == 1, 'a short hold is typed in one piece', problems, repr(typed))
    check(tts._echo_form(' '.join(typed)) == tts._echo_form(said), 'a hold over "git branch" hears it', problems,
          f'typed {typed!r}')
    check(bool(live), 'what is heard is shown live while the key is held', problems)

    # A real room: speech at a laptop mic's level 12 dB over its noise, which fills the pauses
    parts = [('Samantha', 'Create a new git branch called live text'), ('Samantha', 'then commit the changes')]
    audio, _, _ = spoken(parts, workdir)
    speech = audio / np.sqrt(np.mean(audio[np.abs(audio) > 1e-4] ** 2)) * 0.02
    noisy = speech + np.random.default_rng(0).standard_normal(len(audio)).astype(np.float32) * 0.005
    cut = asr.commit_cut(noisy[:int(6.1 * RATE)], RATE)
    around = speech[cut - RATE // 10:cut + RATE // 10] if cut else speech[:0]
    check(cut is not None and float(np.sqrt(np.mean(around ** 2))) < 0.002,
          'a hold is committed at a pause filled with room noise', problems, f'cut at {cut / RATE if cut else None}s')

    parts = [('Samantha', 'Create a new git branch called live text.'),
             ('Samantha', 'Then commit the changes to the screen model,'),
             ('Samantha', 'run the tests and open a pull request.')]
    typed, live, typed_at_release = hold(*spoken(parts, workdir))
    said = ' '.join(text for _, text in parts)
    check(typed_at_release >= 1, 'a long hold has its start typed in before the key is released', problems,
          f'{typed_at_release} pieces typed while held')
    check(tts._echo_form(' '.join(typed)) == tts._echo_form(said), 'and all of it is heard, once', problems,
          f'typed {typed!r}')
    check(len(live) >= 4, 'with live text updating as the hold goes on', problems, f'{len(live)} updates')
    return problems


def main():
    problems = check_echoes() + check_vocabulary() + check_tap_to_talk_holds()
    if problems:
        print('\nFAILED:')
        for problem in problems:
            print(f'  {problem}')
        sys.exit(1)
    print('\nAll checks passed')


if __name__ == '__main__':
    main()

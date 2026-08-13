#!/usr/bin/env python3

"""Targeted tests for the local TTS model lifecycle; each asserts intended behaviour, so a failure is the defect."""

import inspect
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import talkito.tts as tts  # noqa: E402
import talkito.models as models  # noqa: E402


def reset_model_state():
    """Clear the local-model cache so each test starts from a known state."""
    with tts._local_model_cache_lock:
        tts._local_models.clear()


def seed_model(provider: str, variant: str, model) -> None:
    """Place an already-built model into the cache under a given key."""
    with tts._local_model_cache_lock:
        entry = tts._local_models.setdefault((provider, variant), tts._ModelEntry())
        entry.model = model
        entry.loading = False


def snapshot_tts_state() -> dict:
    """Capture the shared TTS settings a test may disturb."""
    from talkito.state import get_shared_state
    shared = get_shared_state()
    return {"provider": shared.tts_provider, "voice": shared.tts_voice,
            "model": shared.tts_model, "emotion": shared.tts_emotion,
            "models": dict(getattr(shared, "tts_models", {}))}


def restore_tts_state(saved: dict) -> None:
    """Put the shared TTS settings back. Shared state is persisted, so a dirty test leaks to disk.

    set_tts_config ignores None, so a field that started unset needs an explicit neutral value
    rather than being handed back the None it came from.
    """
    # set_tts_config_thread_safe persists; get_shared_state().set_tts_config only updates memory,
    # so restoring through the latter leaves the on-disk state dirty for every later run.
    from talkito.state import set_tts_config_thread_safe, get_shared_state
    set_tts_config_thread_safe(
        provider=saved["provider"] or "system",
        voice=saved["voice"] or "",
        model=saved["model"] or "",
        emotion=saved["emotion"] or "neutral",
    )
    get_shared_state().tts_models = dict(saved["models"])


class Recorder:
    """Stands in for a model constructor, recording how it was called."""

    def __init__(self, delay: float = 0.0):
        self.calls: List[Tuple[tuple, dict]] = []
        self.delay = delay

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.delay:
            time.sleep(self.delay)
        return self


def test_kokoro_language_is_honoured() -> str:
    """KOKORO_LANGUAGE / tts_language should reach KPipeline; today lang_code is hardcoded."""
    import kokoro

    recorder = Recorder()
    original_pipeline, original_consent = kokoro.KPipeline, tts.check_spacy_model_consent
    original_language = tts.kokoro_language
    configured = "b"  # British English, per the Kokoro lang_code convention
    kokoro.KPipeline = recorder
    tts.check_spacy_model_consent = lambda provider, *a, **k: True
    tts.kokoro_language = configured

    try:
        tts._create_model_instance("kokoro")
    finally:
        kokoro.KPipeline, tts.check_spacy_model_consent = original_pipeline, original_consent
        tts.kokoro_language = original_language

    assert recorder.calls, "KPipeline was never constructed"
    lang_code = recorder.calls[0][1].get("lang_code")
    assert lang_code == configured, (
        f"kokoro_language={configured!r} was configured but KPipeline got lang_code={lang_code!r}; "
        "_create_model_instance hardcodes the language, so KOKORO_LANGUAGE, --tts-language and "
        "state.tts_language are all inert for Kokoro"
    )
    return f"KPipeline received lang_code={lang_code!r}"


def test_kokoro_provider_honours_instance_config() -> str:
    """create_tts_provider(config) should influence Kokoro; today synthesize() only reads shared state."""
    captured = {}

    class FakePipeline:
        def __call__(self, text, voice=None, speed=None):
            captured["voice"] = voice
            captured["speed"] = speed
            return iter([("gs", "ps", __import__("numpy").zeros(2400, dtype="float32"))])

    reset_model_state()
    seed_model("kokoro", tts._normalize_kokoro_lang(tts.kokoro_language), FakePipeline())

    provider = tts.create_tts_provider("kokoro", {"voice": "am_michael", "speed": 1.5})
    provider.synthesize("hello there")
    reset_model_state()

    assert captured.get("voice") == "am_michael", (
        f"constructor config requested voice='am_michael' but synthesize() used {captured.get('voice')!r}; "
        "KokoroTTSProvider ignores self.config and reads get_tts_config() instead"
    )
    return f"Kokoro used voice={captured.get('voice')!r} speed={captured.get('speed')!r}"


def test_kittentts_provider_honours_instance_config() -> str:
    """KittenTTS reads self.get_config_value, so instance config should win; included as the contrast case."""
    captured = {}

    class FakeModel:
        def generate(self, text, voice=None):
            captured["voice"] = voice
            return __import__("numpy").zeros(2400, dtype="float32")

    reset_model_state()
    seed_model("kittentts", tts.kittentts_model, FakeModel())

    provider = tts.create_tts_provider("kittentts", {"voice": "expr-voice-5-m"})
    provider.synthesize("hello there")
    reset_model_state()

    assert captured.get("voice") == "expr-voice-5-m", (
        f"KittenTTS ignored instance config, used {captured.get('voice')!r}"
    )
    return f"KittenTTS used voice={captured.get('voice')!r}"


def test_load_failure_is_retried() -> str:
    """A transient load failure should not permanently poison the cache."""
    reset_model_state()
    recorder = Recorder()
    original_create = tts._create_model_instance
    original_cooldown = tts.MODEL_RETRY_COOLDOWN

    # First attempt fails, any later attempt would succeed
    attempts = {"n": 0}

    def flaky(provider, variant=""):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("simulated transient failure (e.g. network blip)")
        return recorder

    tts._create_model_instance = flaky
    tts.MODEL_RETRY_COOLDOWN = 0.2  # Keep the test fast; the shipped value is 30s
    try:
        tts.preload_local_model("kokoro")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with tts._local_model_cache_lock:
                entry = tts._local_models.get(("kokoro", tts._model_variant("kokoro")))
                if entry and entry.error and not entry.loading:
                    break
            time.sleep(0.05)

        time.sleep(tts.MODEL_RETRY_COOLDOWN)
        second = tts.get_cached_local_model("kokoro", timeout=3.0)
    finally:
        tts._create_model_instance = original_create
        tts.MODEL_RETRY_COOLDOWN = original_cooldown
        reset_model_state()

    assert second is not None, (
        f"after one transient failure the model never reloads: _create_model_instance was called "
        f"{attempts['n']} time(s) and get_cached_local_model returned None; "
        "_local_model_error is checked before the reload branch and is never cleared"
    )
    return f"recovered after {attempts['n']} attempt(s)"


def test_switching_providers_does_not_thrash() -> str:
    """Alternating providers should not rebuild a model that was already loaded."""
    reset_model_state()
    recorder_calls: List[str] = []
    original_create = tts._create_model_instance

    def counting_create(provider, variant=""):
        recorder_calls.append(provider)
        return Recorder()

    tts._create_model_instance = counting_create
    try:
        for provider in ("kokoro", "kittentts", "kokoro"):
            tts.get_cached_local_model(provider, timeout=5.0)
    finally:
        tts._create_model_instance = original_create
        reset_model_state()

    assert len(recorder_calls) <= 2, (
        f"loading kokoro -> kittentts -> kokoro rebuilt models {len(recorder_calls)} times "
        f"({' -> '.join(recorder_calls)}); a single global slot means every switch evicts the other model"
    )
    return f"{len(recorder_calls)} model build(s) for 3 requests"


def test_worker_holds_queue_while_a_model_loads() -> str:
    """The speech worker holds the queue while the active provider's model loads.

    This is what keeps utterances from being silently dropped during startup: speak_text has no
    fallback when a provider's synthesis fails, so anything dequeued mid-load would vanish.
    """
    from talkito.state import get_shared_state
    reset_model_state()
    shared = get_shared_state()
    saved_state = snapshot_tts_state()
    shared.set_tts_config(provider="kokoro")

    try:
        assert not tts._active_model_loading(), "nothing should be loading on a clean cache"
        key = ("kokoro", tts._model_variant("kokoro"))
        with tts._local_model_cache_lock:
            tts._local_models[key] = tts._ModelEntry(loading=True)
        held = tts._active_model_loading()
        with tts._local_model_cache_lock:
            tts._local_models[key].loading = False
        released = tts._active_model_loading()
    finally:
        restore_tts_state(saved_state)
        reset_model_state()

    assert held and not released, (
        f"worker gate misreports the active provider's load state (holding={held}, "
        f"released={released}); removing this gate would silently drop every utterance "
        "spoken while the model loads"
    )
    return "queue held during load, released when ready"


def test_warm_load_timeout_is_generous() -> str:
    """The default wait must comfortably cover building a model that is already on disk."""
    default_timeout = inspect.signature(tts.get_cached_local_model).parameters["timeout"].default
    effective = tts.MODEL_LOAD_TIMEOUT if default_timeout is None else default_timeout

    assert effective >= 20.0, (
        f"default model wait is {effective}s; a cold-from-disk Kokoro pipeline build was measured at "
        "over 7s on this machine, leaving little headroom on slower hardware"
    )
    return f"default wait {effective}s"


def test_consent_prompt_runs_on_main_thread() -> str:
    """Model-download consent uses input(); it must not be requested from a worker thread."""
    reset_model_state()
    prompt_threads: List[str] = []

    original_consent = tts.ask_user_consent
    original_cached = tts.check_model_cached
    original_create = tts._create_model_instance

    def recording_consent(provider, model_name):
        prompt_threads.append(threading.current_thread().name)
        return True

    tts.ask_user_consent = recording_consent
    tts.check_model_cached = lambda *a, **k: False
    tts._create_model_instance = lambda provider, variant="": Recorder()
    # This covers the prompt path, so auto-approve must be off regardless of the ambient
    # environment; with it set there is no prompt and a worker thread may legitimately proceed.
    original_env = os.environ.pop("TALKITO_AUTO_APPROVE_DOWNLOADS", None)

    try:
        # Simulate the lazy path: the TTS worker thread asks for a model nobody preloaded
        worker = threading.Thread(
            target=tts.get_cached_local_model, args=("kokoro",), kwargs={"timeout": 3.0},
            name="TTSWorker",
        )
        worker.start()
        worker.join(timeout=8)
    finally:
        tts.ask_user_consent = original_consent
        tts.check_model_cached = original_cached
        tts._create_model_instance = original_create
        if original_env is not None:
            os.environ["TALKITO_AUTO_APPROVE_DOWNLOADS"] = original_env
        reset_model_state()

    off_main = [t for t in prompt_threads if t != "MainThread"]
    assert not off_main, (
        f"input()-based download consent was requested from thread(s) {off_main}; "
        "on the lazy path the TTS worker prompts for stdin, which it does not own"
    )
    return "consent stayed on the main thread"


class FakeNeuTTS2E:
    """Stands in for a loaded NeuTTS2E model, recording the speaker and emotion it was asked for."""

    def __init__(self):
        self.calls: List[Tuple[str, str]] = []

    def infer(self, text, speaker="emily", emotion="neutral", **kwargs):
        import numpy as np
        self.calls.append((speaker, emotion))
        return np.zeros(2400, dtype="float32")


def test_neutts2e_honours_speaker_and_emotion() -> str:
    """NeuTTS 2E should route instance config through to its speaker and emotion arguments."""
    reset_model_state()
    model = FakeNeuTTS2E()
    seed_model("neutts2e", tts._neutts_cache_variant(tts.neutts2e_model), model)

    provider = tts.create_tts_provider("neutts2e", {"voice": "steven", "emotion": "happy"})
    result = provider.synthesize("hello there")
    reset_model_state()

    assert result is not None, "NeuTTS2E synthesis returned nothing"
    assert model.calls == [("steven", "happy")], f"NeuTTS2E was called with {model.calls}"
    return f"speaker/emotion routed as {model.calls[0]}"


def test_neutts2e_rejects_invalid_speaker_and_emotion() -> str:
    """Unknown speakers and emotions should fall back rather than reaching the model and erroring."""
    reset_model_state()
    model = FakeNeuTTS2E()
    seed_model("neutts2e", tts._neutts_cache_variant(tts.neutts2e_model), model)

    provider = tts.create_tts_provider("neutts2e", {"voice": "nonexistent", "emotion": "smug"})
    provider.synthesize("hello there")
    reset_model_state()

    assert model.calls == [("emily", "neutral")], (
        f"invalid speaker/emotion were passed through as {model.calls}"
    )
    return "fell back to ('emily', 'neutral')"


class Skipped(Exception):
    """Raised when a test targets a provider that is not registered on this branch."""


def require(attribute: str) -> None:
    """Skip a test when the feature it covers is not present in this build."""
    if not hasattr(tts, attribute):
        raise Skipped(f"tts.{attribute} not present (provider not registered on this branch)")


def test_neutts_reference_is_resolved_and_cached() -> str:
    """A bundled reference voice should resolve to codes plus transcript, and encoding should happen once."""
    require("_neutts_reference")
    import wave

    class FakeNeuTTS:
        def __init__(self):
            self.encode_calls = 0

        def encode_reference(self, path):
            self.encode_calls += 1
            return f"codes-for-{os.path.basename(path)}"

    model = FakeNeuTTS()

    # Bundled voice: pre-encoded, so no encode_reference call is needed at all
    codes, ref_text = tts._neutts_reference(model, "emily", "")
    assert codes is not None and ref_text, "bundled reference resolved to empty codes or text"
    assert model.encode_calls == 0, "bundled reference should not need encoding"

    # Custom .wav: encoded once, then served from the per-model cache
    tmp_dir = os.environ.get("TMPDIR", "/tmp")
    wav_path = os.path.join(tmp_dir, "talkito_ref_test.wav")
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)

    try:
        first = tts._neutts_reference(model, wav_path, "a spoken reference")
        second = tts._neutts_reference(model, wav_path, "a spoken reference")
    finally:
        os.unlink(wav_path)

    assert first == second and model.encode_calls == 1, (
        f"reference encoding ran {model.encode_calls} times; it should be cached per model"
    )
    return f"bundled ref '{ref_text[:28]}...', custom ref encoded once"


def test_neutts_unknown_voice_reports_options() -> str:
    """An unknown reference voice should name the bundled alternatives rather than failing opaquely."""
    require("_neutts_reference")

    class FakeNeuTTS:
        def encode_reference(self, path):
            raise AssertionError("should not be reached")

    try:
        tts._neutts_reference(FakeNeuTTS(), "not_a_real_voice", "")
    except ValueError as e:
        assert "emily" in str(e), f"error message did not list bundled voices: {e}"
        return "error names the bundled voices"
    raise AssertionError("unknown voice did not raise")


def test_preload_and_synthesis_share_a_cache_key() -> str:
    """Preloading then synthesizing must reuse one model, not build a second under a different key."""
    reset_model_state()
    builds: List[Tuple[str, str]] = []
    original_create = tts._create_model_instance

    def counting_create(provider, variant=""):
        builds.append((provider, variant))
        return FakeNeuTTS2E()

    # A custom model in shared state is the case that regressed: preload read it, synthesis did not
    from talkito.state import get_shared_state
    shared = get_shared_state()
    saved_state = snapshot_tts_state()
    shared.set_tts_config(provider="neutts2e", model="neuphonic/neutts-2e-custom")

    tts._create_model_instance = counting_create
    # Treat the model as already present, so preload builds it rather than taking the
    # download-consent path, which would silently make this test pass for the wrong reason
    original_cached = tts.check_model_cached
    tts.check_model_cached = lambda *a, **k: True
    try:
        tts.preload_local_model("neutts2e")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with tts._local_model_cache_lock:
                entry = tts._local_models.get(("neutts2e", tts._model_variant("neutts2e")))
                if entry is not None and (entry.model is not None or entry.error):
                    break
            time.sleep(0.05)
        tts.create_tts_provider("neutts2e").synthesize("hello there")
        keys = [k[1] for k in tts._local_models]
    finally:
        tts._create_model_instance = original_create
        tts.check_model_cached = original_cached
        restore_tts_state(saved_state)
        reset_model_state()

    assert len(builds) == 1, (
        f"model was built {len(builds)} times for one configuration; cache keys were {keys}. "
        "preload and synthesis must derive the variant the same way"
    )
    return "one build shared by preload and synthesis"


def test_auto_approved_download_works_off_main_thread() -> str:
    """With downloads pre-approved no prompt is needed, so a worker thread must not be blocked."""
    reset_model_state()
    original_cached = tts.check_model_cached
    original_create = tts._create_model_instance
    original_env = os.environ.get("TALKITO_AUTO_APPROVE_DOWNLOADS")

    tts.check_model_cached = lambda *a, **k: False  # force the "needs download" path
    tts._create_model_instance = lambda provider, variant="": FakeNeuTTS2E()
    os.environ["TALKITO_AUTO_APPROVE_DOWNLOADS"] = "1"

    result = {}
    try:
        def worker():
            result["model"] = tts.get_cached_local_model("neutts2e", timeout=10.0)

        thread = threading.Thread(target=worker, name="TTSWorker")
        thread.start()
        thread.join(timeout=15)
    finally:
        tts.check_model_cached = original_cached
        tts._create_model_instance = original_create
        if original_env is None:
            os.environ.pop("TALKITO_AUTO_APPROVE_DOWNLOADS", None)
        else:
            os.environ["TALKITO_AUTO_APPROVE_DOWNLOADS"] = original_env
        reset_model_state()

    assert result.get("model") is not None, (
        "TALKITO_AUTO_APPROVE_DOWNLOADS=1 did not let a worker thread start the download; "
        "the main-thread guard must only apply when a prompt is actually needed"
    )
    return "auto-approved download proceeded from a worker thread"


def test_switching_away_does_not_block_on_orphaned_load() -> str:
    """Switching to a non-local provider must not wait on a local model that is still loading.

    MCP provider switching shuts down the worker, starts a new one, and leaves any in-flight
    loader running. Gating on every cache entry would block the new provider on a download it
    has nothing to do with.
    """
    from talkito.state import get_shared_state
    reset_model_state()
    shared = get_shared_state()
    saved_state = snapshot_tts_state()

    # A local model is mid-load, as if the user switched away during a download
    with tts._local_model_cache_lock:
        tts._local_models[("neutts2e", tts._model_variant("neutts2e"))] = tts._ModelEntry(loading=True)
    try:
        shared.set_tts_config(provider="openai")
        blocked_on_cloud = tts._active_model_loading()
        shared.set_tts_config(provider="neutts2e")
        blocked_on_local = tts._active_model_loading()
    finally:
        restore_tts_state(saved_state)
        reset_model_state()

    assert not blocked_on_cloud, (
        "switching to a cloud provider still waited on an unrelated local model load; "
        "the worker gate must consider only the active provider"
    )
    assert blocked_on_local, "the active provider's own load must still hold the queue"
    return "cloud provider unblocked, local provider still gated"


def test_hub_timeouts_are_actually_applied() -> str:
    """The Hub timeout configuration must take effect, not just be defined.

    huggingface_hub reads these on import, so setting the environment afterwards does nothing;
    its 10s default is too tight to fetch a model over a slow link, and a cold start fails.
    """
    from huggingface_hub import constants
    import talkito.models as models_mod

    before = (constants.HF_HUB_DOWNLOAD_TIMEOUT, constants.HF_HUB_ETAG_TIMEOUT)
    original_flag = models_mod._hf_configured
    constants.HF_HUB_DOWNLOAD_TIMEOUT = 10  # library default, the value that fails
    try:
        models_mod._hf_configured = False
        tts._configure_hf_timeouts()
        after = constants.HF_HUB_DOWNLOAD_TIMEOUT
    finally:
        models_mod._hf_configured = original_flag

    assert after > 10, (
        f"download timeout is still {after}s after configuration; _configure_hf_timeouts must "
        "set the constants module, since the environment is read at import time"
    )
    # Configuration must never make a fetch *more* likely to fail than the library default
    assert constants.HF_HUB_ETAG_TIMEOUT >= 10, (
        f"metadata timeout was reduced to {constants.HF_HUB_ETAG_TIMEOUT}s, below the library "
        "default of 10s; a user with unrelated models cached would get a tighter timeout during "
        "the very download this is meant to protect"
    )
    return f"download {before[0]}s -> {after}s, etag not reduced"


def test_emotion_is_configurable_at_runtime() -> str:
    """Emotion set in shared state must reach synthesis on the ordinary speech path.

    speak_text builds the provider with no instance config, so an emotion only readable from
    self.config or the startup environment can never be changed while running.
    """
    from talkito.state import get_shared_state
    reset_model_state()
    model = FakeNeuTTS2E()
    seed_model("neutts2e", tts._neutts_cache_variant(tts.neutts2e_model), model)
    shared = get_shared_state()
    # Shared state is persisted to disk, so a test that leaves it dirty changes what every later
    # run sees. Capture and restore everything this touches.
    saved_state = snapshot_tts_state()

    try:
        shared.set_tts_config(provider="neutts2e", voice="paul", emotion="happy")
        tts.create_tts_provider("neutts2e").synthesize("runtime emotion")
        from_state = model.calls[-1]

        tts.create_tts_provider("neutts2e", {"emotion": "sad"}).synthesize("override")
        from_instance = model.calls[-1]
    finally:
        restore_tts_state(saved_state)
        reset_model_state()

    assert from_state == ("paul", "happy"), (
        f"emotion held in shared state never reached synthesis, got {from_state}; "
        "the normal speech path passes no instance config"
    )
    assert from_instance == ("paul", "sad"), f"instance config should win, got {from_instance}"
    return f"state {from_state} then instance override {from_instance}"


def test_local_backbone_still_checks_the_semantic_encoder() -> str:
    """A local .gguf backbone must not skip the encoder check the remote path performs.

    The torch codec builds facebook/w2v-bert-2.0 at construction, so reporting "cached" without it
    lets model loading silently start a multi-gigabyte download.
    """
    import talkito.models as models_mod

    gguf = Path(tts._resolve_neutts_backbone(tts.neutts2e_model))
    if not gguf.is_file():
        raise Skipped("no local .gguf backbone available to test with")

    original_repo = models_mod.NEUCODEC_ENCODER_REPO
    try:
        with_encoder = models_mod.check_model_cached("neutts2e", str(gguf), "neuphonic/neucodec")
        models_mod.NEUCODEC_ENCODER_REPO = "facebook/definitely-not-cached-encoder"
        without_encoder = models_mod.check_model_cached("neutts2e", str(gguf), "neuphonic/neucodec")
        onnx_unaffected = models_mod.check_model_cached(
            "neutts2e", str(gguf), "neuphonic/neucodec-onnx-decoder-int8")
    finally:
        models_mod.NEUCODEC_ENCODER_REPO = original_repo

    assert with_encoder and not without_encoder, (
        f"local backbone reported cached={without_encoder} with the encoder missing; the "
        "local-file branch must check it exactly as the remote branch does"
    )
    assert onnx_unaffected, "the ONNX codec needs no encoder and must stay unaffected"
    return "encoder required for the torch codec, ignored for ONNX"


def test_emotion_survives_a_restart() -> str:
    """Emotion must be written to the state file and read back, like every other tts_* field."""
    import json
    from talkito.state import TalkitoState, get_shared_state

    shared = get_shared_state()
    saved_state = snapshot_tts_state()
    try:
        shared.set_tts_config(provider="neutts2e", emotion="surprised")
        serialized = json.loads(json.dumps(shared.to_dict()))
        # A fresh state object loading that dict is what a restart does
        restored = TalkitoState()
        for key in ("tts_emotion",):
            if key in serialized:
                setattr(restored, key, serialized[key])
    finally:
        restore_tts_state(saved_state)

    assert "tts_emotion" in serialized, (
        "tts_emotion is missing from to_dict(), so a configured emotion is lost on restart"
    )
    assert restored.tts_emotion == "surprised", f"restore gave {restored.tts_emotion!r}"
    return "emotion serialized and restored"


def test_reconfiguring_keeps_other_settings() -> str:
    """Setting one field must not reset the others to provider defaults.

    Provider defaults should win only when the provider is actually changing; otherwise changing
    the voice silently resets emotion, and vice versa.
    """
    import asyncio
    from talkito.mcp import _change_tts_internal
    from talkito.state import get_shared_state

    shared = get_shared_state()
    saved_state = snapshot_tts_state()

    saved_globals = (tts.neutts2e_voice, tts.neutts2e_emotion)

    async def sequence():
        await _change_tts_internal("neutts2e", voice="steven", emotion="happy")
        both = (shared.tts_voice, shared.tts_emotion)

        # Restore the module globals to their startup values. configure_tts_from_args mutates
        # them, which would otherwise mask the bug; after a restart they hold the environment
        # defaults while shared state holds what the user chose, which is the case that broke.
        tts.neutts2e_voice, tts.neutts2e_emotion = saved_globals

        await _change_tts_internal("neutts2e", voice="paul")
        after_voice = (shared.tts_voice, shared.tts_emotion)

        tts.neutts2e_voice, tts.neutts2e_emotion = saved_globals
        await _change_tts_internal("neutts2e", emotion="sad")
        after_emotion = (shared.tts_voice, shared.tts_emotion)
        return both, after_voice, after_emotion

    try:
        both, after_voice, after_emotion = asyncio.run(sequence())
    finally:
        tts.neutts2e_voice, tts.neutts2e_emotion = saved_globals
        restore_tts_state(saved_state)

    assert both == ("steven", "happy"), f"initial configuration did not stick: {both}"
    assert after_voice == ("paul", "happy"), (
        f"changing the voice reset emotion: {after_voice}; provider defaults must not override "
        "existing settings when the provider is unchanged"
    )
    assert after_emotion == ("paul", "sad"), f"changing emotion reset the voice: {after_emotion}"
    return f"{both} -> {after_voice} -> {after_emotion}"


def test_declined_download_applies_the_fallback() -> str:
    """A declined download must switch the provider everywhere, not just in shared state.

    Standalone mode names its engine from the module global, so leaving that pointing at the
    declined provider sends speech to speak_with_default("...", "neutts2e"), which cannot work.
    """
    saved_state = snapshot_tts_state()
    original_provider = tts.tts_provider
    original_cached = tts.check_model_cached
    original_env = os.environ.pop("TALKITO_AUTO_APPROVE_DOWNLOADS", None)
    reset_model_state()

    tts.tts_provider = "neutts2e"
    tts.check_model_cached = lambda *a, **k: False  # force the needs-download path
    try:
        effective = tts.preload_local_model("neutts2e")
        module_global = tts.tts_provider
    finally:
        tts.check_model_cached = original_cached
        tts.tts_provider = original_provider
        if original_env is not None:
            os.environ["TALKITO_AUTO_APPROVE_DOWNLOADS"] = original_env
        restore_tts_state(saved_state)
        reset_model_state()

    assert effective and effective != "neutts2e", (
        f"preload returned {effective!r}; it must report the provider to actually use so the "
        "caller does not name an engine after the declined one"
    )
    assert module_global == effective, (
        f"module global is still {module_global!r} while the fallback is {effective!r}; "
        "shared state alone does not drive the engine name"
    )
    return f"declined neutts2e, fell back to {effective!r} everywhere"


def test_custom_backbone_is_not_rejected_by_name() -> str:
    """Any backbone the user configures must be honoured, not matched against a substring."""
    from talkito.state import set_tts_config_thread_safe, get_shared_state
    saved_state = snapshot_tts_state()

    try:
        resolved = {}
        for candidate in ("/opt/models/custom.gguf", "acme/emotional-voice-gguf"):
            set_tts_config_thread_safe(provider="neutts2e", model=candidate)
            resolved[candidate] = tts.get_tts_config().get("model")

        # A model belonging to another provider must not be used, even when the provider is
        # assigned directly as startup does, bypassing set_tts_config entirely
        set_tts_config_thread_safe(provider="kittentts", model="KittenML/kitten-tts-mini-0.8")
        get_shared_state().tts_provider = "neutts2e"
        after_switch = tts.get_tts_config().get("model")
    finally:
        restore_tts_state(saved_state)

    for candidate, got in resolved.items():
        assert got == candidate, (
            f"custom backbone {candidate!r} silently resolved to {got!r}; a name-based test "
            "rejects valid local paths and third-party repos"
        )
    assert "kitten" not in (after_switch or "").lower(), (
        f"a KittenTTS model survived the switch to NeuTTS as {after_switch!r}"
    )
    return f"custom backbones honoured, stale model dropped on switch -> {after_switch!r}"


def test_repeated_declines_do_not_cycle() -> str:
    """Declining several local providers must converge, not bounce between them.

    Each fallback has to exclude everything declined so far; excluding only the latest lets the
    search hand back an earlier decline and the two alternate until the stack runs out.
    """
    saved_state = snapshot_tts_state()
    original_provider = tts.tts_provider
    original_cached = tts.check_model_cached
    original_select = tts.select_best_tts_provider
    original_fallback = tts._fall_back_from_declined_download
    original_env = os.environ.pop("TALKITO_AUTO_APPROVE_DOWNLOADS", None)
    reset_model_state()

    chain: List[str] = []

    def offer_locals_first(excluded_providers=None):
        excluded_providers = excluded_providers or set()
        for candidate in ("neutts2e", "kokoro", "kittentts", "system"):
            if candidate not in excluded_providers:
                return candidate
        return None

    def traced(provider, declined=None):
        chain.append(provider)
        if len(chain) > 8:
            raise AssertionError(f"fallback cycled: {' -> '.join(chain)}")
        return original_fallback(provider, declined)

    tts.check_model_cached = lambda *a, **k: False  # nothing cached, so every local one declines
    tts.select_best_tts_provider = offer_locals_first
    tts._fall_back_from_declined_download = traced
    try:
        result = tts.preload_local_model("neutts2e")
    finally:
        tts.check_model_cached = original_cached
        tts.select_best_tts_provider = original_select
        tts._fall_back_from_declined_download = original_fallback
        tts.tts_provider = original_provider
        if original_env is not None:
            os.environ["TALKITO_AUTO_APPROVE_DOWNLOADS"] = original_env
        restore_tts_state(saved_state)
        reset_model_state()

    assert result and result not in tts.LOCAL_MODEL_PROVIDERS, (
        f"declining every local provider settled on {result!r}, which still needs a download"
    )
    assert len(chain) == len(set(chain)), f"a provider was offered twice: {chain}"
    return f"{' -> '.join(chain)} -> {result}"


def test_custom_model_survives_a_provider_round_trip() -> str:
    """A model chosen for one provider must still be there after visiting another and returning."""
    import asyncio
    import tempfile
    from talkito.mcp import _change_tts_internal
    from talkito.state import get_shared_state, set_tts_config_thread_safe

    saved_state = snapshot_tts_state()
    reset_model_state()
    shared = get_shared_state()
    # Any path that exists will do. It must exist, or the model is treated as needing a download
    # and the provider falls back, testing the fallback rather than whether it was remembered.
    # Loading is stubbed out: llama.cpp redirects the process's file descriptors when it opens a
    # GGUF, which silently swallows the rest of this suite's output.
    handle, custom = tempfile.mkstemp(suffix=".gguf")
    os.close(handle)
    original_create = tts._create_model_instance
    original_needs = tts._model_needs_download
    tts._create_model_instance = lambda provider, variant="": FakeNeuTTS2E()
    tts._model_needs_download = lambda provider, variant: False

    async def sequence():
        await _change_tts_internal("neutts2e")
        set_tts_config_thread_safe(provider="neutts2e", model=custom)
        start = shared.tts_models.get("neutts2e")
        # KittenTTS also carries a model, which is what overwrote a single shared slot
        await _change_tts_internal("kittentts")
        away = shared.tts_models.get("neutts2e")
        await _change_tts_internal("neutts2e")
        back = tts.get_tts_config().get("model")
        return start, away, back

    try:
        start, away, back = asyncio.run(sequence())
    finally:
        tts._create_model_instance = original_create
        tts._model_needs_download = original_needs
        os.unlink(custom)
        restore_tts_state(saved_state)
        reset_model_state()

    assert start == custom, f"custom model was not stored: {start!r}"
    assert away == custom, (
        f"switching away lost the model, leaving {away!r}; it belongs to neutts2e regardless of "
        "which provider is active"
    )
    assert back == custom, f"returning replaced the custom model with {back!r}"
    return "custom backbone kept across neutts2e -> kittentts -> neutts2e"


def test_legacy_state_without_ownership_is_migrated() -> str:
    """State files written before ownership existed must keep working.

    They hold tts_model with no tts_model_provider; leaving ownership unset would silently
    discard a custom model the user had already configured. This drives the real loader against
    a temporary state file rather than reimplementing its logic.
    """
    import json
    import os as _os
    import tempfile
    from types import SimpleNamespace
    from talkito.state import SharedStateManager, get_shared_state

    legacy = {"tts_provider": "neutts2e", "tts_model": "/opt/models/legacy.gguf"}
    handle, path = tempfile.mkstemp(suffix=".json")
    with _os.fdopen(handle, "w") as f:
        json.dump(legacy, f)

    # SharedStateManager is a singleton whose __new__ returns the live instance, so borrow its
    # unbound _load_state and run it against a stand-in. Constructing one, however that is done,
    # would replace the process-wide state with whatever this file contains.
    stand_in = SimpleNamespace(state=type(get_shared_state())(), _lock=threading.RLock())

    real_expanduser = _os.path.expanduser
    _os.path.expanduser = lambda q: path if q.endswith(".talkito_state.json") else real_expanduser(q)
    try:
        SharedStateManager._load_state(stand_in)
        models = dict(stand_in.state.tts_models)
    finally:
        _os.path.expanduser = real_expanduser
        _os.unlink(path)

    assert models.get("neutts2e") == "/opt/models/legacy.gguf", (
        f"legacy model did not migrate, got {models!r}; a model saved without an owner belongs "
        "to the provider saved alongside it, or it is silently replaced by the default"
    )
    return f"legacy model migrated to {models!r}"


def test_cli_emotion_flag_wins_over_saved_preference() -> str:
    """An explicit --tts-emotion must beat a preference saved from an earlier run."""
    from types import SimpleNamespace
    from talkito.state import set_tts_config_thread_safe

    saved_state = snapshot_tts_state()
    args = SimpleNamespace(tts_provider="neutts2e", tts_voice=None, tts_region=None,
                           tts_language=None, tts_rate=None, tts_pitch=None,
                           tts_emotion="happy", disable_tts=False, orcabot=False)
    original_emotion = tts.neutts2e_emotion
    try:
        set_tts_config_thread_safe(provider="neutts2e", emotion="sad")
        tts.configure_tts_from_args(args)
        with_flag = tts.get_tts_config().get("emotion")

        args.tts_emotion = None
        set_tts_config_thread_safe(provider="neutts2e", emotion="sad")
        tts.configure_tts_from_args(args)
        without_flag = tts.get_tts_config().get("emotion")
    finally:
        tts.neutts2e_emotion = original_emotion
        restore_tts_state(saved_state)

    assert with_flag == "happy", (
        f"--tts-emotion=happy was overridden by the saved preference, giving {with_flag!r}"
    )
    assert without_flag == "sad", f"without the flag the saved preference should stand, got {without_flag!r}"
    return f"flag -> {with_flag!r}, no flag -> {without_flag!r}"


def test_providers_use_their_remembered_model() -> str:
    """Every model-bearing provider must load and synthesize with the model stored for it.

    Reporting it through get_tts_config() is not enough: the cache variant and the provider both
    have to resolve it, or preload and synthesis quietly fall back to the module default.
    """
    from talkito.state import set_tts_config_thread_safe

    saved_state = snapshot_tts_state()
    reset_model_state()

    class FakeKitten:
        def __init__(self, name):
            self.name = name

        def generate(self, text, voice=None):
            import numpy as np
            used.append(self.name)
            return np.zeros(2400, dtype="float32")

    used: List[str] = []
    custom = "Acme/custom-kitten-model"
    try:
        set_tts_config_thread_safe(provider="kittentts", model=custom)
        variant = tts._model_variant("kittentts")

        # Seed both the custom and the default entry, so picking the wrong one is visible
        seed_model("kittentts", custom, FakeKitten(custom))
        seed_model("kittentts", tts.kittentts_model, FakeKitten(tts.kittentts_model))
        tts.create_tts_provider("kittentts").synthesize("resolution check")
    finally:
        restore_tts_state(saved_state)
        reset_model_state()

    assert variant == custom, (
        f"the cache variant is {variant!r}, so preload would fetch the default rather than the "
        "model configured for this provider"
    )
    assert used == [custom], f"synthesis used {used}, not the remembered model"
    return f"preload and synthesis both used {custom!r}"


TESTS: List[Tuple[str, Callable[[], str]]] = [
    ("kokoro language setting is honoured", test_kokoro_language_is_honoured),
    ("kokoro provider honours instance config", test_kokoro_provider_honours_instance_config),
    ("kittentts provider honours instance config", test_kittentts_provider_honours_instance_config),
    ("load failure is retried", test_load_failure_is_retried),
    ("switching providers does not thrash", test_switching_providers_does_not_thrash),
    ("worker holds queue while a model loads", test_worker_holds_queue_while_a_model_loads),
    ("warm load timeout is generous", test_warm_load_timeout_is_generous),
    ("consent prompt runs on main thread", test_consent_prompt_runs_on_main_thread),
    ("neutts2e honours speaker and emotion", test_neutts2e_honours_speaker_and_emotion),
    ("neutts2e rejects invalid speaker/emotion", test_neutts2e_rejects_invalid_speaker_and_emotion),
    ("neutts reference resolved and cached", test_neutts_reference_is_resolved_and_cached),
    ("neutts unknown voice reports options", test_neutts_unknown_voice_reports_options),
    ("preload and synthesis share a cache key", test_preload_and_synthesis_share_a_cache_key),
    ("switching away does not block on orphaned load", test_switching_away_does_not_block_on_orphaned_load),
    ("auto-approved download works off main thread", test_auto_approved_download_works_off_main_thread),
    ("hub timeouts are actually applied", test_hub_timeouts_are_actually_applied),
    ("emotion is configurable at runtime", test_emotion_is_configurable_at_runtime),
    ("emotion survives a restart", test_emotion_survives_a_restart),
    ("reconfiguring keeps other settings", test_reconfiguring_keeps_other_settings),
    ("local backbone checks semantic encoder", test_local_backbone_still_checks_the_semantic_encoder),
    ("declined download applies the fallback", test_declined_download_applies_the_fallback),
    ("repeated declines do not cycle", test_repeated_declines_do_not_cycle),
    ("custom backbone is not rejected by name", test_custom_backbone_is_not_rejected_by_name),
    ("custom model survives a provider round trip", test_custom_model_survives_a_provider_round_trip),
    ("providers use their remembered model", test_providers_use_their_remembered_model),
    ("legacy state without ownership is migrated", test_legacy_state_without_ownership_is_migrated),
    ("cli emotion flag wins over saved preference", test_cli_emotion_flag_wins_over_saved_preference),
]


def main() -> int:
    failures = skipped = 0
    for name, test in TESTS:
        try:
            detail = test()
            print(f"  PASS  {name}\n        {detail}")
        except Skipped as e:
            skipped += 1
            print(f"  SKIP  {name}\n        {e}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {name}\n        {e}")
        except Exception as e:
            failures += 1
            print(f"  ERROR {name}\n        {type(e).__name__}: {e}")

    passed = len(TESTS) - failures - skipped
    print(f"\n{passed}/{len(TESTS) - skipped} passed, {failures} failing, {skipped} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

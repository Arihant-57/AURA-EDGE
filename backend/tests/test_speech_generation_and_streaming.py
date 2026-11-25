"""Focused tests for R6 (Kannada TTS pronunciation/catalog/voice consistency) and R8 (streaming/prefetch/PCM stability)."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
from typing import Any

import numpy as np

from aura_edge.config import Settings
from aura_edge.intelligence.conversation import validate_response_language
from aura_edge.intelligence.response_catalog import format_duration_spoken, get_phrase
from aura_edge.intelligence.schemas import LanguageCode
from aura_edge.speech.playback import (
    AudioPlayback,
    PlaybackMetrics,
    _BoundedPcmBuffer,
)
from aura_edge.speech.tts_backends.base import SynthesizedAudio
from aura_edge.speech.tts_backends.sherpa_onnx_backend import SherpaOnnxBackend
from aura_edge.speech.tts_router import (
    SentenceStreamBuffer,
    TTSRouter,
    normalize_for_pronunciation,
)


class _DummyTimingBackend:
    """Deterministic fake TTS backend with configurable synthesis delay for streaming tests."""

    name = "dummy_timing"

    def __init__(self, *, synth_delay_sec: float = 0.0, duration_sec: float = 0.16, sample_rate: int = 16000) -> None:
        self.synth_delay_sec = synth_delay_sec
        self.duration_sec = duration_sec
        self.sample_rate = sample_rate
        self.events: list[tuple[str, str, float]] = []

    def supports_language(self, _lang: LanguageCode) -> bool:
        return True

    def model_info(self, language: LanguageCode) -> tuple[str, str]:
        return (f"dummy-model-{language}", f"dummy-voice-{language}")

    def synthesize(
        self,
        text: str,
        *,
        language: LanguageCode,
        speed: float = 1.0,
    ) -> SynthesizedAudio:
        self.events.append(("start", text, time.perf_counter()))
        if self.synth_delay_sec > 0:
            time.sleep(self.synth_delay_sec)
        n = max(320, int(self.sample_rate * self.duration_sec))
        t = np.linspace(0.0, 12.0 * np.pi, n, dtype=np.float32)
        samples = np.sin(t) * 0.25
        self.events.append(("end", text, time.perf_counter()))
        return SynthesizedAudio(
            samples=samples,
            sample_rate=self.sample_rate,
            language=language,
            backend=self.name,
            model_name=f"dummy-model-{language}",
            voice_name=f"dummy-voice-{language}",
            latency_ms=round(self.synth_delay_sec * 1000.0, 2),
        )


def _install_simulated_sounddevice(
    monkeypatch: Any,
    *,
    frame_interval_sec: float = 0.008,
    block_frames: int = 512,
) -> dict[str, Any]:
    """Install a mock sounddevice module that runs a real background callback thread without speaker output."""
    state: dict[str, Any] = {
        "captured_blocks": [],
        "first_non_silent_mono": None,
        "aborted": False,
        "stopped": False,
        "closed": False,
        "callback_count": 0,
    }

    class _SimOutputStream:
        def __init__(self, *, samplerate: int, channels: int, callback: Any, **_kwargs: Any) -> None:
            self.samplerate = samplerate
            self.channels = channels
            self.callback = callback
            self.latency = 0.02
            self._stop_ev = threading.Event()
            self._thread: threading.Thread | None = None

        def _run(self) -> None:
            buf = np.zeros((block_frames, self.channels), dtype=np.float32)
            while not self._stop_ev.is_set():
                buf.fill(0.0)
                self.callback(buf, block_frames, None, None)
                state["callback_count"] += 1
                mono = buf[:, 0].copy()
                state["captured_blocks"].append(mono)
                if state["first_non_silent_mono"] is None and float(np.max(np.abs(mono))) > 1e-6:
                    state["first_non_silent_mono"] = time.perf_counter()
                self._stop_ev.wait(frame_interval_sec)

        def start(self) -> None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        def abort(self) -> None:
            state["aborted"] = True
            self._stop_ev.set()
            if self._thread is not None:
                self._thread.join(timeout=0.5)

        def stop(self) -> None:
            state["stopped"] = True
            self._stop_ev.set()
            if self._thread is not None:
                self._thread.join(timeout=0.5)

        def close(self) -> None:
            state["closed"] = True
            self._stop_ev.set()
            if self._thread is not None:
                self._thread.join(timeout=0.5)

    fake_sd = types.SimpleNamespace(
        OutputStream=_SimOutputStream,
        query_devices=lambda *args, **kwargs: (
            {"default_samplerate": 16000.0, "max_output_channels": 2}
            if args
            else [{"name": "SimSpeaker", "max_output_channels": 2, "default_samplerate": 16000.0}]
        ),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    return state


def test_kannada_pronunciation_normalization_preserves_devices_labels_and_numbers() -> None:
    # AURA EDGE branding
    brand = normalize_for_pronunciation("ನನ್ನ ಹೆಸರು AURA EDGE.", "kn")
    assert "ಆರಾ ಎಡ್ಜ್" in brand

    # AM / PM time normalization
    am_text = normalize_for_pronunciation("The time is 10:05 AM.", "kn")
    assert "ಬೆಳಗ್ಗೆ" in am_text and "10 05" in am_text
    pm_noon = normalize_for_pronunciation("Meeting at 1:30 PM", "kn")
    assert "ಮಧ್ಯಾಹ್ನ" in pm_noon and "1 30" in pm_noon
    pm_night = normalize_for_pronunciation("Alarm at 9:15 p.m.", "kn")
    assert "ರಾತ್ರಿ" in pm_night and "9 15" in pm_night

    # Decimals, Kannada numerals, dates, %, °C
    dec = normalize_for_pronunciation("ತಾಪಮಾನ 3.5°C ಮತ್ತು 75%", "kn")
    assert "3 ಪಾಯಿಂಟ್ 5" in dec
    assert "ಡಿಗ್ರಿ ಸೆಲ್ಸಿಯಸ್" in dec
    assert "75 ಶೇಕಡಾ" in dec

    kn_digits = normalize_for_pronunciation("೩.೫ ನಿಮಿಷ", "kn")
    assert "3 ಪಾಯಿಂಟ್ 5" in kn_digits

    dt = normalize_for_pronunciation("2026-11-25 ಮತ್ತು 25/11/2026", "kn")
    assert "2026 11 25" in dt and "25 11 2026" in dt

    # Smart-home loanwords never vanish
    sh = normalize_for_pronunciation("Living Room Light Bedroom Fan AC TV Curtain on off timer kitchen study", "kn")
    for expected in ("ಲಿವಿಂಗ್ ರೂಮ್", "ಲೈಟ್", "ಬೆಡ್‌ರೂಮ್", "ಫ್ಯಾನ್", "ಎ ಸಿ", "ಟಿ ವಿ", "ಕರ್ಟನ್", "ಆನ್", "ಆಫ್", "ಟೈಮರ್", "ಕಿಚನ್", "ಸ್ಟಡಿ"):
        assert expected in sh, f"Missing {expected!r} in {sh!r}"

    # Unknown arbitrary timer label ("yoga", "pasta", "USB") must NOT be stripped
    unknown_lbl = normalize_for_pronunciation('Timer "yoga" and "pasta" on USB', "kn")
    assert "ಟೈಮರ್" in unknown_lbl
    assert "ಯು ಎಸ್ ಬಿ" in unknown_lbl
    # Each unknown word produced non-empty Kannada characters
    assert len(unknown_lbl.split()) >= 5


def test_real_offline_kannada_tts_speaks_english_devices_and_matches_streaming() -> None:
    cfg = Settings(_env_file=None)
    router = TTSRouter(cfg)
    backend = SherpaOnnxBackend(cfg.models_dir)

    # Direct backend synthesis must also preserve English device name (never collapse to empty)
    direct = backend.synthesize("Light Bedroom Main", language="kn")
    assert direct.samples.size > 1600
    assert float(np.sqrt(np.mean(np.square(direct.samples)))) > 0.01

    # Normal vs streaming playback over the same Kannada text produce identical synthesized chunks
    text = "ನನ್ನ ಹೆಸರು AURA EDGE. Bedroom Light ಆನ್ ಆಗಿದೆ. ತಾಪಮಾನ 3.5°C ಇದೆ."
    playback = AudioPlayback(router)
    normal_audios = asyncio.run(playback.speak_text(text, language="kn", play_to_speaker=False))

    q: asyncio.Queue[str | None] = asyncio.Queue()
    for part in ("ನನ್ನ ಹೆಸರು AURA EDGE.", "Bedroom Light ಆನ್ ಆಗಿದೆ.", "ತಾಪಮಾನ 3.5°C ಇದೆ."):
        q.put_nowait(part)
    q.put_nowait(None)
    stream_audios, _ = asyncio.run(playback.speak_sentence_stream(q, language="kn", play_to_speaker=False))
    playback.close()

    assert len(normal_audios) == len(stream_audios) == 3
    for a_norm, a_str in zip(normal_audios, stream_audios, strict=True):
        assert a_norm.model_name == a_str.model_name == "vits-mms-kan"
        assert a_norm.voice_name == a_str.voice_name == "facebook/mms-tts-kan"
        assert np.array_equal(a_norm.samples, a_str.samples)


def test_natural_kannada_catalog_prewarm_and_voice_consistency() -> None:
    # Verify all catalog keys & kwargs work across EN/HI/KN and Kannada phrases pass script guard
    for key in (
        "unsupported",
        "ambiguous_request",
        "no_active_timers",
        "timer_not_found",
        "help",
        "stopped",
        "empty_speech",
        "ollama_unavailable",
        "invalid_duration",
        "greeting",
        "acknowledgment",
        "farewell",
        "identity",
        "supported_languages",
        "language_switched",
        "conversation_started",
    ):
        kn_phrase = get_phrase(key, "kn")
        ok, reason = validate_response_language(kn_phrase, "kn")
        assert ok is True, f"Catalog key {key!r} failed KN guard ({reason}): {kn_phrase!r}"

    # Kwargs templates preserve formatting contracts
    dur = format_duration_spoken(3665, "kn")
    assert "ಗಂಟೆ" in dur and "ನಿಮಿಷ" in dur and "ಸೆಕೆಂಡ್" in dur
    assert "ಟೈಮರ್" in get_phrase("timer_set", "kn", label_part=' "tea"', duration="5 ನಿಮಿಷ")
    assert "ಬಾಕಿ" in get_phrase("timer_paused", "kn", label_part="", duration="2 ನಿಮಿಷ")
    assert "ಬಾಕಿ" in get_phrase("timer_resumed", "kn", label_part="", duration="2 ನಿಮಿಷ")
    assert "ಟೈಮರ್" in get_phrase("timer_cancelled", "kn", label_part="")
    assert "3" in get_phrase("timers_cancelled_all", "kn", count=3)
    assert "ಬಾಕಿ" in get_phrase("timer_remaining", "kn", label_part="", duration="1 ನಿಮಿಷ")
    assert "ಟೈಮರ್" in get_phrase("timer_completed", "kn", label_part="tea")
    assert "tea" in get_phrase("ambiguous_timer", "kn", labels="tea, coffee")

    # Prewarm includes empty_speech and stopped
    cfg = Settings(_env_file=None)
    router = TTSRouter(cfg)
    summary = router.prewarm_common_phrases()
    assert summary["phrases_warmed"] >= 21
    assert router.synthesize(get_phrase("empty_speech", "kn"), language="kn").cached is True
    assert router.synthesize(get_phrase("stopped", "kn"), language="kn").cached is True

    # Voice consistency per language across multiple sentences
    for lang, sentences in (
        ("en", ["First English sentence.", "Second English sentence."]),
        ("hi", ["पहला हिंदी वाक्य।", "दूसरा हिंदी वाक्य।"]),
        ("kn", ["ಮೊದಲ ಕನ್ನಡ ವಾಕ್ಯ.", "ಎರಡನೇ ಕನ್ನಡ ವಾಕ್ಯ."]),
    ):
        typed_lang: LanguageCode = lang  # type: ignore[assignment]
        voices = {(a.backend, a.model_name, a.voice_name) for a in (router.synthesize(s, language=typed_lang) for s in sentences)}
        assert len(voices) == 1


def test_sentence_stream_buffer_handles_abbreviations_decimals_times_and_clauses() -> None:
    buf = SentenceStreamBuffer(min_chars=16, clause_chars=52, max_chars=90)
    tokens = [
        "Dr. Rao checked ",
        "the sensor at 10:30 a.m. ",
        "today. ",
        "The value is 3.5 degrees. ",
        "Meanwhile the living room fan is running smoothly, and the bedroom light is off ",
        "for now.",
    ]
    emitted: list[str] = []
    for tok in tokens:
        emitted.extend(buf.push(tok))
    tail = buf.flush()
    if tail:
        emitted.append(tail)

    assert emitted[0] == "Dr. Rao checked the sensor at 10:30 a.m. today."
    assert emitted[1] == "The value is 3.5 degrees."
    # Long comma-containing clause split before final period for low TTFA
    assert any(part.endswith(",") for part in emitted[2:-1])
    assert emitted[-1] == "and the bedroom light is off for now."

    # Long unpunctuated text splits at word boundary without dropping characters
    buf2 = SentenceStreamBuffer(min_chars=12, clause_chars=36, max_chars=55)
    long_unpunctuated = "one two three four five six seven eight nine ten eleven twelve thirteen"
    chunks = buf2.push(long_unpunctuated)
    rem = buf2.flush()
    if rem:
        chunks.append(rem)
    assert len(chunks) >= 2
    assert " ".join(chunks) == long_unpunctuated


def test_bounded_pcm_buffer_backpressure_and_lock_free_callback() -> None:
    buf = _BoundedPcmBuffer(sample_rate=16000, max_seconds=0.5)  # capacity = 8000 samples

    # Reading an empty buffer outputs clean silence without blocking or failing
    silence_block = np.ones(256, dtype=np.float32)
    assert buf.read_into_callback(silence_block) is True
    assert float(np.max(np.abs(silence_block))) == 0.0
    assert buf.first_non_silent_mono is None

    total = 24000
    src = np.linspace(0.1, 0.9, total, dtype=np.float32)
    max_observed_buffered = 0
    write_ok: list[bool] = []

    def _producer() -> None:
        write_ok.append(buf.write(src, stop_check=lambda: False))
        buf.mark_finished()

    t = threading.Thread(target=_producer, daemon=True)
    t.start()

    # Wait briefly for producer to seed initial samples before consumer drains
    deadline = time.monotonic() + 1.0
    while buf.buffered_samples == 0 and time.monotonic() < deadline:
        time.sleep(0.001)

    out_chunks: list[np.ndarray] = []
    block = np.zeros(512, dtype=np.float32)
    while True:
        max_observed_buffered = max(max_observed_buffered, buf.buffered_samples)
        keep_going = buf.read_into_callback(block)
        out_chunks.append(block.copy())
        if not keep_going:
            break
        time.sleep(0.002)

    t.join(timeout=2.0)
    assert write_ok == [True]
    assert max_observed_buffered <= buf.capacity
    assert buf.first_non_silent_mono is not None

    reconstructed = np.concatenate(out_chunks)[:total]
    assert np.allclose(reconstructed, src, atol=1e-6)


def test_speak_sentence_stream_prefetch_timestamps_and_no_final_trailing_pause(monkeypatch: Any) -> None:
    sim = _install_simulated_sounddevice(monkeypatch, frame_interval_sec=0.006, block_frames=512)
    backend = _DummyTimingBackend(synth_delay_sec=0.06, duration_sec=0.22, sample_rate=16000)
    router = TTSRouter(Settings(_env_file=None), primary_backend=backend)
    playback = AudioPlayback(router)

    q: asyncio.Queue[str | None] = asyncio.Queue()

    async def _feed_and_play() -> tuple[list[SynthesizedAudio], PlaybackMetrics]:
        t0 = time.perf_counter()
        await q.put("First sentence.")
        await q.put("Second sentence.")
        play_task = asyncio.create_task(
            playback.speak_sentence_stream(q, language="en", play_to_speaker=True, turn_start_perf=t0)
        )
        # Delay final None sentinel to verify trailing 55ms pause is STILL omitted on final sentence
        await asyncio.sleep(0.10)
        await q.put(None)
        return await play_task

    audios, metrics = asyncio.run(_feed_and_play())
    playback.close()

    assert len(audios) == 2
    assert metrics.chunks_spoken == 2
    # Verify concurrent prefetch: second sentence synthesis started BEFORE first playback ended
    s2_start = next(ts for ev, txt, ts in backend.events if ev == "start" and txt == "Second sentence.")
    assert metrics.first_non_silent_callback_mono is not None
    assert metrics.first_audio_ready_mono is not None
    assert metrics.tts_completed_mono is not None
    assert metrics.playback_completed_mono is not None
    assert s2_start < metrics.playback_completed_mono

    # Monotonic timestamp ordering & exact callback capture verification
    assert metrics.first_audio_ready_mono <= metrics.tts_completed_mono
    assert metrics.first_audio_ready_mono <= metrics.first_non_silent_callback_mono
    assert metrics.first_non_silent_callback_mono <= metrics.playback_completed_mono
    assert sim["first_non_silent_mono"] is not None
    # Callback timestamp recorded inside callback must closely precede the mock wrapper's post-callback snapshot
    assert abs(metrics.first_non_silent_callback_mono - sim["first_non_silent_mono"]) < 0.005
    assert metrics.audio_startup_ms is not None and metrics.audio_startup_ms > 0.0
    assert metrics.audio_playback_ms > 0.0

    # Verify NO trailing 55ms pause on the final sentence:
    # Check total non-silent span vs expected (2 * ~220ms conditioned + 55ms single gap)
    all_pcm = np.concatenate(sim["captured_blocks"])
    nz = np.flatnonzero(np.abs(all_pcm) > 1e-5)
    assert nz.size > 0
    # After the last non-silent sample of sentence 2, remaining silence is only from OutputStream block padding
    # (no 55ms zero pad appended before final flush). Check gap between sentences exists:
    # Find runs of zeros >= 700 samples (~43.75ms at 16kHz) inside [nz[0]:nz[-1]]
    active_span = all_pcm[nz[0] : nz[-1] + 1]
    is_zero = np.abs(active_span) <= 1e-6
    max_internal_zero_run = 0
    cur = 0
    for z in is_zero:
        if z:
            cur += 1
            max_internal_zero_run = max(max_internal_zero_run, cur)
        else:
            cur = 0
    # Exactly ~55ms (880 samples) gap between sentence 1 and sentence 2
    assert max_internal_zero_run >= 700


def test_barge_in_stop_fades_out_smoothly_and_aborts(monkeypatch: Any) -> None:
    sim = _install_simulated_sounddevice(monkeypatch, frame_interval_sec=0.006, block_frames=512)
    backend = _DummyTimingBackend(synth_delay_sec=0.0, duration_sec=2.0, sample_rate=16000)
    router = TTSRouter(Settings(_env_file=None), primary_backend=backend)
    playback = AudioPlayback(router)

    async def _run_barge_in() -> tuple[list[SynthesizedAudio], PlaybackMetrics]:
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put("Long playing sentence that will be interrupted.")
        await q.put(None)
        task = asyncio.create_task(playback.speak_sentence_stream(q, language="en", play_to_speaker=True))
        deadline = time.monotonic() + 2.0
        while sim["first_non_silent_mono"] is None and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert sim["first_non_silent_mono"] is not None
        playback.stop()
        return await task

    _audios, _metrics = asyncio.run(_run_barge_in())
    playback.close()

    assert sim["aborted"] is True
    assert playback.is_playing is False

    # Inspect the last block containing non-zero samples before abort: it must fade down to 0.0
    non_silent_blocks = [b for b in sim["captured_blocks"] if float(np.max(np.abs(b))) > 1e-6]
    assert non_silent_blocks
    last_active = non_silent_blocks[-1]
    nz_idx = np.flatnonzero(np.abs(last_active) > 1e-6)
    # Tail of faded block decays smoothly toward zero
    assert abs(float(last_active[nz_idx[-1]])) < 0.05

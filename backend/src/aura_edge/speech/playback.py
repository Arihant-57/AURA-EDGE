"""Thread-safe audio playback with sentence prefetching and fade-out barge-in cancellation."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np

from aura_edge.intelligence.schemas import LanguageCode
from aura_edge.speech.tts_backends.base import SynthesizedAudio
from aura_edge.speech.tts_router import TTSRouter, split_sentences

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlaybackMetrics:
    """Strictly separates pure TTS synthesis time from speaker playback time and TTFA."""

    tts_synthesis_ms: float
    audio_playback_ms: float
    first_audio_ready_ms: float | None
    cache_hits: int
    chunks_spoken: int


def apply_fade_out(samples: np.ndarray, fade_samples: int = 64) -> np.ndarray:
    """Apply linear fade-out to avoid audible click/pop on barge-in interruption."""
    if samples.size == 0 or fade_samples <= 0:
        return samples
    out = np.array(samples, dtype=np.float32, copy=True)
    n = min(fade_samples, len(out))
    out[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)
    return out


class AudioPlayback:
    """Lock-safe offline audio playback with sentence prefetching and cancellation."""

    def __init__(
        self,
        tts_router: TTSRouter,
        *,
        output_device: int | str | None = None,
    ) -> None:
        self._tts = tts_router
        self._output_device = output_device
        self._stream_lock = threading.Lock()
        self._stream: Any = None
        self._stream_sr: int | None = None
        self._cancel_event = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aura-tts-prefetch")
        self.last_metrics = PlaybackMetrics(
            tts_synthesis_ms=0.0,
            audio_playback_ms=0.0,
            first_audio_ready_ms=None,
            cache_hits=0,
            chunks_spoken=0,
        )

    def has_output_device(self) -> bool:
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            return any(int(d.get("max_output_channels", 0)) > 0 for d in devices)
        except (ImportError, OSError, RuntimeError, ValueError):
            return False

    def stop(self) -> None:
        """Immediately signal playback stop and safely close stream under lock."""
        self._cancel_event.set()
        with self._stream_lock:
            stream = self._stream
            self._stream = None
            self._stream_sr = None
            if stream is not None:
                try:
                    stream.abort()
                except (OSError, RuntimeError) as exc:
                    logger.debug("Ignoring output stream abort error: %s", exc)
                try:
                    stream.close()
                except (OSError, RuntimeError) as exc:
                    logger.debug("Ignoring output stream close error: %s", exc)

    def close(self) -> None:
        self.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _ensure_stream(self, sample_rate: int) -> Any:
        import sounddevice as sd

        with self._stream_lock:
            if self._stream is not None and self._stream_sr == sample_rate:
                return self._stream
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except (OSError, RuntimeError) as exc:
                    logger.debug("Ignoring previous output stream cleanup error: %s", exc)
                self._stream = None

            self._stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                device=self._output_device,
                blocksize=1024,
            )
            self._stream.start()
            self._stream_sr = sample_rate
            return self._stream

    def _write_chunks_blocking(self, audio: SynthesizedAudio) -> bool:
        """Write synthesized PCM in 50 ms chunks so cancellation takes <50 ms."""
        if audio.samples.size == 0:
            return True
        try:
            self._ensure_stream(audio.sample_rate)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            logger.warning("Audio output stream unavailable (headless mode): %s", exc)
            return True

        chunk_size = max(256, int(audio.sample_rate * 0.05))
        samples = audio.samples.astype(np.float32, copy=False).reshape(-1)
        pos = 0
        while pos < len(samples):
            if self._cancel_event.is_set():
                tail = apply_fade_out(samples[pos : min(len(samples), pos + 64)], fade_samples=64)
                with self._stream_lock:
                    if self._stream is not None and tail.size > 0:
                        try:
                            self._stream.write(tail.reshape(-1, 1))
                        except (OSError, RuntimeError) as exc:
                            logger.debug("Ignoring tail fade write error: %s", exc)
                return False

            end = min(len(samples), pos + chunk_size)
            chunk = samples[pos:end].reshape(-1, 1)
            with self._stream_lock:
                if self._stream is None:
                    return False
                try:
                    self._stream.write(chunk)
                except (OSError, RuntimeError) as exc:
                    logger.debug("Audio output write interrupted: %s", exc)
                    return False
            pos = end
        return True

    async def speak_text(
        self,
        text: str,
        *,
        language: LanguageCode = "en",
        speed: float | None = None,
        play_to_speaker: bool = True,
        external_cancel: asyncio.Event | None = None,
        turn_start_perf: float | None = None,
    ) -> list[SynthesizedAudio]:
        """Synthesize and stream sentences with prefetching, keeping ``list[SynthesizedAudio]`` return."""
        audios, _ = await self.speak_text_with_metrics(
            text,
            language=language,
            speed=speed,
            play_to_speaker=play_to_speaker,
            external_cancel=external_cancel,
            turn_start_perf=turn_start_perf,
        )
        return audios

    async def speak_text_with_metrics(
        self,
        text: str,
        *,
        language: LanguageCode = "en",
        speed: float | None = None,
        play_to_speaker: bool = True,
        external_cancel: asyncio.Event | None = None,
        turn_start_perf: float | None = None,
    ) -> tuple[list[SynthesizedAudio], PlaybackMetrics]:
        """Synthesize and play static text while cleanly separating synthesis vs playback timings."""
        sentences = split_sentences(text)
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        for sent in sentences:
            queue.put_nowait(sent)
        queue.put_nowait(None)
        return await self.speak_sentence_stream(
            queue,
            language=language,
            speed=speed,
            play_to_speaker=play_to_speaker,
            external_cancel=external_cancel,
            turn_start_perf=turn_start_perf,
        )

    async def speak_sentence_stream(
        self,
        sentence_queue: asyncio.Queue[str | None],
        *,
        language: LanguageCode = "en",
        speed: float | None = None,
        play_to_speaker: bool = True,
        external_cancel: asyncio.Event | None = None,
        turn_start_perf: float | None = None,
    ) -> tuple[list[SynthesizedAudio], PlaybackMetrics]:
        """Consume streamed sentences, prefetch N+1 synthesis, and track exact TTFA vs playback."""
        self._cancel_event.clear()
        loop = asyncio.get_running_loop()
        base_perf = turn_start_perf if turn_start_perf is not None else time.perf_counter()
        results: list[SynthesizedAudio] = []
        tts_synth_ms = 0.0
        playback_ms = 0.0
        first_audio_ready_ms: float | None = None
        cache_hits = 0

        def _synth(sentence: str) -> SynthesizedAudio:
            return self._tts.synthesize(sentence, language=language, speed=speed)

        def _is_cancelled() -> bool:
            return self._cancel_event.is_set() or (
                external_cancel is not None and external_cancel.is_set()
            )

        eof_reached = False

        async def _pull_next() -> Future[SynthesizedAudio] | None:
            nonlocal eof_reached
            if eof_reached:
                return None
            nxt = await sentence_queue.get()
            if nxt is None:
                eof_reached = True
                return None
            return self._executor.submit(_synth, nxt)

        next_future = await _pull_next()
        while next_future is not None:
            if _is_cancelled():
                self.stop()
                break

            audio = await asyncio.wrap_future(next_future)
            if _is_cancelled():
                self.stop()
                break

            tts_synth_ms += float(audio.latency_ms)
            if audio.cached:
                cache_hits += 1

            # Prefetch N+1 sentence immediately if already queued so synthesis overlaps playback
            if not eof_reached and not sentence_queue.empty():
                nxt = sentence_queue.get_nowait()
                if nxt is None:
                    eof_reached = True
                    next_future = None
                else:
                    next_future = self._executor.submit(_synth, nxt)
            else:
                next_future = None

            results.append(audio)
            if first_audio_ready_ms is None:
                first_audio_ready_ms = round((time.perf_counter() - base_perf) * 1000.0, 2)

            if play_to_speaker:
                t_play0 = time.perf_counter()
                completed = await loop.run_in_executor(None, self._write_chunks_blocking, audio)
                playback_ms += round((time.perf_counter() - t_play0) * 1000.0, 2)
                if not completed:
                    break

            if next_future is None and not eof_reached:
                next_future = await _pull_next()

        metrics = PlaybackMetrics(
            tts_synthesis_ms=round(tts_synth_ms, 2),
            audio_playback_ms=round(playback_ms, 2),
            first_audio_ready_ms=first_audio_ready_ms,
            cache_hits=cache_hits,
            chunks_spoken=len(results),
        )
        self.last_metrics = metrics
        return results, metrics

"""Thread-safe audio playback with sentence prefetching and fade-out barge-in cancellation."""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import numpy as np

from aura_edge.intelligence.schemas import LanguageCode
from aura_edge.speech.tts_backends.base import SynthesizedAudio
from aura_edge.speech.tts_router import TTSRouter, split_sentences

logger = logging.getLogger(__name__)


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
    ) -> list[SynthesizedAudio]:
        """Synthesize and stream sentences with prefetching for N+1 sentence."""
        sentences = split_sentences(text)
        if not sentences:
            return []

        self._cancel_event.clear()
        loop = asyncio.get_running_loop()
        results: list[SynthesizedAudio] = []

        def _synth(sentence: str) -> SynthesizedAudio:
            return self._tts.synthesize(sentence, language=language, speed=speed)

        next_future: Future[SynthesizedAudio] | None = self._executor.submit(_synth, sentences[0])

        for idx in range(len(sentences)):
            if self._cancel_event.is_set() or (
                external_cancel is not None and external_cancel.is_set()
            ):
                self.stop()
                break

            assert next_future is not None
            audio = await asyncio.wrap_future(next_future)
            if idx + 1 < len(sentences):
                next_future = self._executor.submit(_synth, sentences[idx + 1])
            else:
                next_future = None

            if self._cancel_event.is_set() or (
                external_cancel is not None and external_cancel.is_set()
            ):
                self.stop()
                break

            results.append(audio)
            if play_to_speaker:
                completed = await loop.run_in_executor(None, self._write_chunks_blocking, audio)
                if not completed:
                    break

        return results

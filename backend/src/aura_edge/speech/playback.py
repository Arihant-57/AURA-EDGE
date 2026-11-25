"""Exclusive, underrun-free offline audio playback with bounded PCM buffering and prefetch."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections import deque
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
    """Strictly separates TTS synthesis, output startup, TTFA, and actual speaker playback."""

    tts_synthesis_ms: float
    audio_playback_ms: float
    first_audio_ready_ms: float | None
    cache_hits: int
    chunks_spoken: int
    audio_startup_ms: float | None = None


def apply_fade_out(samples: np.ndarray, fade_samples: int = 64) -> np.ndarray:
    """Apply linear fade-out to avoid audible click/pop on barge-in interruption."""
    if samples.size == 0 or fade_samples <= 0:
        return samples
    out = np.array(samples, dtype=np.float32, copy=True)
    n = min(fade_samples, len(out))
    out[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)
    return out


def prepare_pcm_for_playback(
    samples: np.ndarray,
    sample_rate: int,
    *,
    target_rate: int | None = None,
    add_pause_ms: float = 0.0,
) -> tuple[np.ndarray, int]:
    """Condition PCM for click-free, clip-free, rate-matched Windows audio output."""
    if samples.size == 0:
        out_sr = int(target_rate or sample_rate)
        return np.zeros(0, dtype=np.float32), out_sr

    pcm = np.nan_to_num(samples.astype(np.float32, copy=True).reshape(-1), nan=0.0, posinf=0.0, neginf=0.0)
    if pcm.size == 0:
        out_sr = int(target_rate or sample_rate)
        return pcm, out_sr

    # Remove DC offset to prevent thumps on stream start/stop
    pcm -= float(np.mean(pcm))

    # Trim dead leading/trailing silence beyond a short 25 ms margin
    nz = np.flatnonzero(np.abs(pcm) > 2e-4)
    if nz.size == 0:
        out_sr = int(target_rate or sample_rate)
        return np.zeros(0, dtype=np.float32), out_sr
    margin = int(sample_rate * 0.025)
    start_idx = max(0, int(nz[0]) - margin)
    end_idx = min(len(pcm), int(nz[-1]) + 1 + margin)
    pcm = pcm[start_idx:end_idx]

    # Anti-clipping soft peak limiter
    peak = float(np.max(np.abs(pcm)))
    if peak > 0.95:
        pcm *= 0.92 / peak

    # Edge fades (5 ms in, 8 ms out) eliminate inter-sentence boundary clicks
    fade_in = min(len(pcm) // 4, max(16, int(sample_rate * 0.005)))
    fade_out = min(len(pcm) // 4, max(32, int(sample_rate * 0.008)))
    if fade_in > 0 and len(pcm) > fade_in:
        pcm[:fade_in] *= np.linspace(0.0, 1.0, fade_in, dtype=np.float32)
    if fade_out > 0 and len(pcm) > fade_out:
        pcm[-fade_out:] *= np.linspace(1.0, 0.0, fade_out, dtype=np.float32)

    out_sr = int(sample_rate)
    if target_rate is not None and int(target_rate) != out_sr and pcm.size > 0:
        from scipy.signal import resample_poly

        dst = int(target_rate)
        g = math.gcd(dst, out_sr)
        pcm = resample_poly(pcm, dst // g, out_sr // g).astype(np.float32, copy=False)
        out_sr = dst
        peak2 = float(np.max(np.abs(pcm))) if pcm.size else 0.0
        if peak2 > 0.95:
            pcm = pcm * (0.92 / peak2)
        post_fade = min(len(pcm) // 4, max(16, int(out_sr * 0.002)))
        if post_fade > 1 and len(pcm) > post_fade * 2:
            pcm[:post_fade] *= np.linspace(0.0, 1.0, post_fade, dtype=np.float32)
            pcm[-post_fade:] *= np.linspace(1.0, 0.0, post_fade, dtype=np.float32)

    if add_pause_ms > 0.0 and pcm.size > 0:
        pad = int(out_sr * (add_pause_ms / 1000.0))
        if pad > 0:
            pcm = np.concatenate([pcm, np.zeros(pad, dtype=np.float32)])

    return np.clip(pcm, -1.0, 1.0).astype(np.float32, copy=False), out_sr


class _BoundedPcmBuffer:
    """Thread-safe ring buffer decoupling TTS producer from PortAudio callback."""

    def __init__(self, sample_rate: int, *, max_seconds: float = 8.0) -> None:
        self.sample_rate = max(8000, int(sample_rate))
        self.capacity = max(2048, int(self.sample_rate * max_seconds))
        self._chunks: deque[np.ndarray] = deque()
        self._head_pos = 0
        self._buffered = 0
        self._finished = False
        self._cancelled = False
        self._started_event = threading.Event()
        self._cond = threading.Condition()

    @property
    def started_event(self) -> threading.Event:
        return self._started_event

    def cancel(self) -> None:
        with self._cond:
            self._cancelled = True
            self._chunks.clear()
            self._head_pos = 0
            self._buffered = 0
            self._finished = True
            self._cond.notify_all()

    def mark_finished(self) -> None:
        with self._cond:
            self._finished = True
            self._cond.notify_all()

    def write(self, pcm: np.ndarray, *, stop_check: Any) -> bool:
        """Write PCM blocks with bounded backpressure until consumed or cancelled."""
        if pcm.size == 0:
            return not stop_check()
        data = pcm.astype(np.float32, copy=False).reshape(-1)
        pos = 0
        while pos < len(data):
            with self._cond:
                while self._buffered >= self.capacity and not self._cancelled and not stop_check():
                    self._cond.wait(timeout=0.05)
                if self._cancelled or stop_check():
                    return False
                room = max(1024, self.capacity - self._buffered)
                take = min(room, len(data) - pos)
                self._chunks.append(data[pos : pos + take])
                self._buffered += take
                pos += take
                self._cond.notify_all()
        return True

    def read_into_callback(self, out_mono: np.ndarray) -> bool:
        """Fill PortAudio callback buffer cleanly (outputs silence on brief gap, never crackles)."""
        need = len(out_mono)
        written = 0
        out_mono.fill(0.0)
        with self._cond:
            if self._cancelled:
                return False
            while written < need and self._chunks:
                head = self._chunks[0]
                avail = len(head) - self._head_pos
                take = min(avail, need - written)
                out_mono[written : written + take] = head[self._head_pos : self._head_pos + take]
                self._head_pos += take
                self._buffered -= take
                written += take
                if self._head_pos >= len(head):
                    self._chunks.popleft()
                    self._head_pos = 0
            if written > 0:
                self._started_event.set()
                self._cond.notify_all()
            done = self._finished and self._buffered == 0
            return not done

    def wait_until_drained(self, *, stop_check: Any) -> None:
        with self._cond:
            while (self._buffered > 0 or not self._finished) and not self._cancelled and not stop_check():
                self._cond.wait(timeout=0.05)


class AudioPlayback:
    """Single-owner offline audio playback with bounded PCM ring buffer and prefetching."""

    def __init__(
        self,
        tts_router: TTSRouter,
        *,
        output_device: int | str | None = None,
    ) -> None:
        self._tts = tts_router
        self._output_device = output_device
        self._owner_lock = asyncio.Lock()
        self._stream_lock = threading.Lock()
        self._stream: Any = None
        self._stream_sr: int | None = None
        self._active_buffer: _BoundedPcmBuffer | None = None
        self._cancel_event = threading.Event()
        self._playback_gen = 0
        self._is_playing = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aura-tts-prefetch")
        self.last_metrics = PlaybackMetrics(
            tts_synthesis_ms=0.0,
            audio_playback_ms=0.0,
            first_audio_ready_ms=None,
            cache_hits=0,
            chunks_spoken=0,
            audio_startup_ms=None,
        )

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    def has_output_device(self) -> bool:
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            return any(int(d.get("max_output_channels", 0)) > 0 for d in devices)
        except (ImportError, OSError, RuntimeError, ValueError):
            return False

    def _resolve_device_default_rate(self) -> int:
        try:
            import sounddevice as sd

            dev = sd.query_devices(self._output_device, "output")
            rate = round(float(dev.get("default_samplerate", 48000.0)))
            if 8000 <= rate <= 192000:
                return rate
        except (ImportError, OSError, RuntimeError, ValueError):
            pass
        return 48000

    def _open_stream_blocking(
        self,
        preferred_rate: int,
        pcm_buf: _BoundedPcmBuffer,
    ) -> tuple[Any, int]:
        import sounddevice as sd

        candidates = [preferred_rate]
        fallback_rate = self._resolve_device_default_rate()
        for rate in (fallback_rate, 48000, 44100):
            if rate not in candidates:
                candidates.append(rate)

        last_exc: Exception | None = None
        for sr in candidates:
            buf_ref = pcm_buf if sr == preferred_rate else _BoundedPcmBuffer(sr, max_seconds=8.0)

            def _cb(outdata: np.ndarray, frames: int, _t: Any, status: Any, *, _b: _BoundedPcmBuffer = buf_ref) -> None:
                if status:
                    logger.debug("PortAudio output status: %s", status)
                mono = np.empty(frames, dtype=np.float32)
                _b.read_into_callback(mono)
                outdata[:, 0] = mono

            try:
                stream = sd.OutputStream(
                    samplerate=sr,
                    channels=1,
                    dtype="float32",
                    device=self._output_device,
                    latency="high",
                    blocksize=0,
                    callback=_cb,
                )
                stream.start()
                return (stream, buf_ref), sr
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                last_exc = exc
                logger.debug("OutputStream rejected rate %s Hz: %s", sr, exc)

        raise RuntimeError(f"Unable to open audio output stream: {last_exc}")

    def stop(self) -> None:
        """Immediately abort active audio playback and invalidate stale turns."""
        with self._stream_lock:
            self._playback_gen += 1
            self._cancel_event.set()
            self._is_playing = False
            if self._active_buffer is not None:
                self._active_buffer.cancel()
                self._active_buffer = None
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

    def _close_stream_gracefully(self, stream: Any) -> None:
        with self._stream_lock:
            if self._stream is stream:
                self._stream = None
                self._stream_sr = None
                self._active_buffer = None
        if stream is not None:
            try:
                stream.stop()
            except (OSError, RuntimeError) as exc:
                logger.debug("Ignoring output stream stop error: %s", exc)
            try:
                stream.close()
            except (OSError, RuntimeError) as exc:
                logger.debug("Ignoring output stream close error: %s", exc)

    def close(self) -> None:
        self.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)

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
        """Synthesize and play text exclusively under owner lock."""
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
        """Synthesize and play static text while strictly separating synthesis vs playback timings."""
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
        """Consume streamed sentences with exclusive playback ownership and bounded prefetch."""
        async with self._owner_lock:
            base_perf = turn_start_perf if turn_start_perf is not None else time.perf_counter()
            if external_cancel is not None and external_cancel.is_set():
                metrics = PlaybackMetrics(0.0, 0.0, None, 0, 0, None)
                self.last_metrics = metrics
                return [], metrics

            with self._stream_lock:
                self._cancel_event.clear()
                my_gen = self._playback_gen + 1
                self._playback_gen = my_gen
                self._is_playing = play_to_speaker

            loop = asyncio.get_running_loop()
            results: list[SynthesizedAudio] = []
            tts_synth_ms = 0.0
            cache_hits = 0
            first_audio_ready_ms: float | None = None
            audio_startup_ms: float | None = None
            play_started_perf: float | None = None

            active_stream: Any = None
            active_buf: _BoundedPcmBuffer | None = None
            active_sr: int | None = None
            prefetch: deque[Future[SynthesizedAudio]] = deque()
            eof_reached = False

            def _is_cancelled() -> bool:
                return (
                    self._cancel_event.is_set()
                    or self._playback_gen != my_gen
                    or (external_cancel is not None and external_cancel.is_set())
                )

            def _tts_task(sentence: str) -> SynthesizedAudio:
                return self._tts.synthesize(sentence, language=language, speed=speed)

            def _drain_nonblocking_prefetch(max_pending: int = 2) -> None:
                nonlocal eof_reached
                while not eof_reached and len(prefetch) < max_pending and not sentence_queue.empty():
                    nxt = sentence_queue.get_nowait()
                    if nxt is None:
                        eof_reached = True
                        break
                    prefetch.append(self._executor.submit(_tts_task, nxt))

            try:
                while True:
                    _drain_nonblocking_prefetch(2)
                    while not prefetch and not eof_reached:
                        nxt = await sentence_queue.get()
                        if nxt is None:
                            eof_reached = True
                            break
                        prefetch.append(self._executor.submit(_tts_task, nxt))
                        _drain_nonblocking_prefetch(2)

                    if not prefetch or _is_cancelled():
                        break

                    fut = prefetch.popleft()
                    audio = await asyncio.wrap_future(fut)
                    if _is_cancelled():
                        break

                    _drain_nonblocking_prefetch(2)
                    results.append(audio)
                    tts_synth_ms += float(audio.latency_ms)
                    if audio.cached:
                        cache_hits += 1

                    if first_audio_ready_ms is None:
                        first_audio_ready_ms = round((time.perf_counter() - base_perf) * 1000.0, 2)

                    if not play_to_speaker or audio.samples.size == 0:
                        continue

                    if active_stream is None:
                        seed_buf = _BoundedPcmBuffer(audio.sample_rate, max_seconds=8.0)
                        try:
                            (active_stream, active_buf), active_sr = await loop.run_in_executor(
                                None,
                                self._open_stream_blocking,
                                audio.sample_rate,
                                seed_buf,
                            )
                            with self._stream_lock:
                                if _is_cancelled():
                                    self._close_stream_gracefully(active_stream)
                                    active_stream = None
                                    active_buf = None
                                    break
                                self._stream = active_stream
                                self._stream_sr = active_sr
                                self._active_buffer = active_buf
                        except (ImportError, OSError, RuntimeError, ValueError) as exc:
                            logger.warning("Audio output stream unavailable (headless mode): %s", exc)
                            active_stream = None
                            active_buf = None
                            active_sr = audio.sample_rate

                    if active_buf is not None and active_sr is not None:
                        more_pending = bool(prefetch) or not eof_reached
                        prepared, _ = prepare_pcm_for_playback(
                            audio.samples,
                            audio.sample_rate,
                            target_rate=active_sr,
                            add_pause_ms=55.0 if more_pending else 0.0,
                        )
                        if prepared.size > 0:
                            ok = await loop.run_in_executor(
                                None,
                                lambda p=prepared, b=active_buf: b.write(p, stop_check=_is_cancelled),
                            )
                            started_hit = await loop.run_in_executor(
                                None,
                                active_buf.started_event.wait,
                                0.3,
                            )
                            if play_started_perf is None and started_hit:
                                now_p = time.perf_counter()
                                play_started_perf = now_p
                                audio_startup_ms = round((now_p - base_perf) * 1000.0, 2)
                            if not ok:
                                break

                if active_buf is not None and active_stream is not None:
                    active_buf.mark_finished()
                    await loop.run_in_executor(
                        None,
                        lambda b=active_buf: b.wait_until_drained(stop_check=_is_cancelled),
                    )
                    if not _is_cancelled():
                        # Wait hardware PortAudio latency so speaker physically finishes before mic resumes
                        hw_latency = 0.0
                        try:
                            hw_latency = max(0.0, min(0.5, float(getattr(active_stream, "latency", 0.08) or 0.08)))
                        except (OSError, RuntimeError, TypeError, ValueError):
                            hw_latency = 0.08
                        await asyncio.sleep(hw_latency + 0.04)

                playback_ms = (
                    round((time.perf_counter() - play_started_perf) * 1000.0, 2)
                    if play_started_perf is not None
                    else 0.0
                )
            finally:
                for pending_fut in prefetch:
                    pending_fut.cancel()
                if active_buf is not None and _is_cancelled():
                    active_buf.cancel()
                if active_stream is not None:
                    self._close_stream_gracefully(active_stream)
                with self._stream_lock:
                    if self._playback_gen == my_gen:
                        self._is_playing = False

            metrics = PlaybackMetrics(
                tts_synthesis_ms=round(tts_synth_ms, 2),
                audio_playback_ms=playback_ms,
                first_audio_ready_ms=first_audio_ready_ms,
                cache_hits=cache_hits,
                chunks_spoken=len(results),
                audio_startup_ms=audio_startup_ms,
            )
            self.last_metrics = metrics
            return results, metrics

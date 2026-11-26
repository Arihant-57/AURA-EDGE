"""SoundDevice 16 kHz audio capture with pre-roll ring buffer and off-callback VAD queue."""

from __future__ import annotations

import asyncio
import logging
import math
import queue
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

import numpy as np
from pydantic import BaseModel

from aura_edge.speech.vad import SileroVadDetector

logger = logging.getLogger(__name__)


class AudioDeviceUnavailableError(RuntimeError):
    """Raised when no input microphone is present or cannot be opened."""


class AudioRouteMode(str, Enum):
    """Prevents assistant self-triggering when TTS is actively speaking."""

    NORMAL = "normal"
    MUTED = "muted"
    MONITOR_ONLY = "monitor_only"


class AudioDeviceInfo(BaseModel):
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: float
    is_default_input: bool = False
    is_default_output: bool = False


class CaptureMetrics(BaseModel):
    """Acoustic and endpoint timing metrics from the most recent audio capture."""

    voiced_ms: float = 0.0
    noise_floor_rms: float = 0.004
    peak_rms: float = 0.0
    snr_db: float | None = None
    speech_end_mono: float | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


AudioLevelCallback = Callable[[float, float], Awaitable[None]]


def list_audio_devices() -> list[AudioDeviceInfo]:
    """Query host audio devices via PortAudio/sounddevice safely."""
    try:
        import sounddevice as sd

        raw = sd.query_devices()
        default_in, default_out = sd.default.device
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        logger.warning("Audio device enumeration failed: %s", exc)
        return []

    devices: list[AudioDeviceInfo] = []
    for idx, dev in enumerate(raw):
        devices.append(
            AudioDeviceInfo(
                index=idx,
                name=str(dev.get("name", f"Device {idx}")),
                max_input_channels=int(dev.get("max_input_channels", 0)),
                max_output_channels=int(dev.get("max_output_channels", 0)),
                default_sample_rate=float(dev.get("default_samplerate", 16000.0)),
                is_default_input=(idx == default_in),
                is_default_output=(idx == default_out),
            )
        )
    return devices


def has_input_microphone(device: int | str | None = None) -> bool:
    """Return True if at least one usable audio input device exists."""
    devices = list_audio_devices()
    if device is not None:
        if isinstance(device, int):
            return any(d.index == device and d.max_input_channels > 0 for d in devices)
        needle = str(device).strip().lower()
        return any(needle in d.name.lower() and d.max_input_channels > 0 for d in devices)
    return any(d.max_input_channels > 0 for d in devices)


def compute_rms_db_and_peak(frame_int16: np.ndarray) -> tuple[float, float]:
    """Return ``(rms_db, peak_0_to_1)`` for a 16-bit PCM frame."""
    if len(frame_int16) == 0:
        return -96.0, 0.0
    f32 = frame_int16.astype(np.float32) / 32768.0
    peak = float(np.max(np.abs(f32)))
    rms = float(np.sqrt(np.mean(np.square(f32))))
    rms_db = round(20.0 * math.log10(max(rms, 1e-5)), 2)
    return rms_db, round(min(1.0, peak), 4)


class AudioCapture:
    """Manages push-to-talk capture without executing inference in PortAudio callback.

    - PortAudio ``_audio_callback`` strictly copies 512-sample frames into a bounded
      ``queue.Queue`` and drops oldest frame on overflow (zero blocking, zero ONNX).
    - Pre-roll ``deque`` retains the trailing ``preroll_sec`` of frames in ``NORMAL``
      mode while preventing echo recording during ``MUTED`` / ``MONITOR_ONLY`` modes.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        frame_samples: int = 512,
        preroll_sec: float = 3.0,
        input_device: int | str | None = None,
        vad: SileroVadDetector | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        max_preroll_frames = max(1, int((preroll_sec * sample_rate) / frame_samples))
        self._preroll: deque[np.ndarray] = deque(maxlen=max_preroll_frames)
        self._pre_speech_keep_frames = max(
            1,
            int((min(max(preroll_sec, 0.05), 0.45) * sample_rate) / frame_samples),
        )
        self._frame_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=256)
        self._input_device = input_device
        self._stream_device = input_device
        self._vad = vad

        self._route_mode = AudioRouteMode.NORMAL
        self._user_muted = False
        self._speaking_suspended = False
        self._stream: Any = None
        self._recording = False
        self._recorded_frames: list[np.ndarray] = []
        self._capture_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._cancelled = False
        self._last_capture_reason: str = "stopped"
        self._speech_end_mono: float | None = None
        self._last_capture_metrics = CaptureMetrics()

    @property
    def last_capture_metrics(self) -> CaptureMetrics:
        return self._last_capture_metrics

    @property
    def route_mode(self) -> AudioRouteMode:
        return self._route_mode

    @property
    def is_muted(self) -> bool:
        return self._user_muted or self._speaking_suspended or self._route_mode == AudioRouteMode.MUTED

    @property
    def is_suspended(self) -> bool:
        return self._speaking_suspended

    def set_user_muted(self, muted: bool) -> None:
        """Explicit user mute toggle (never overridden by SPEAKING -> IDLE echo gate)."""
        with self._capture_lock:
            self._user_muted = bool(muted)
            if self._user_muted or self._speaking_suspended:
                self._route_mode = AudioRouteMode.MUTED
                self._preroll.clear()
                self._recorded_frames.clear()
            elif self._route_mode == AudioRouteMode.MUTED:
                self._route_mode = AudioRouteMode.NORMAL

    def suspend_for_playback(self) -> None:
        """Suspend mic recognition and purge stale/echo buffers while AURA speaks (half-duplex)."""
        self._stop_event.set()
        self._close_stream()
        with self._capture_lock:
            self._speaking_suspended = True
            self._route_mode = AudioRouteMode.MUTED
            self._drain_queue()
            self._preroll.clear()
            self._recorded_frames.clear()
            self._recording = False
        if self._vad is not None:
            self._vad.reset()

    def resume_after_playback(self) -> None:
        """Discard residual acoustic frames and resume mic recognition unless user-muted."""
        with self._capture_lock:
            self._drain_queue()
            self._preroll.clear()
            self._recorded_frames.clear()
            self._speaking_suspended = False
            self._route_mode = AudioRouteMode.MUTED if self._user_muted else AudioRouteMode.NORMAL
        if self._vad is not None:
            self._vad.reset()

    def set_route_mode(self, mode: AudioRouteMode) -> None:
        """Gate audio capture during assistant TTS playback while preserving explicit user mute."""
        with self._capture_lock:
            if (self._user_muted or self._speaking_suspended) and mode == AudioRouteMode.NORMAL:
                self._route_mode = AudioRouteMode.MUTED
                self._preroll.clear()
                return
            self._route_mode = mode
            if mode != AudioRouteMode.NORMAL:
                # Clear stale echo frames from pre-roll when muted/monitoring
                self._preroll.clear()

    @property
    def is_recording(self) -> bool:
        return self._recording

    def select_device(self, device: int | str | None) -> None:
        if device is not None and not has_input_microphone(device):
            raise AudioDeviceUnavailableError(f"Requested microphone '{device}' is unavailable.")
        if device != self._input_device:
            self._close_stream()
        self._input_device = device

    def _is_stream_active(self) -> bool:
        stream = self._stream
        if stream is None:
            return False
        try:
            return bool(getattr(stream, "active", True))
        except (OSError, RuntimeError, AttributeError):
            return False

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        _time_info: Any,
        status: Any,
    ) -> None:
        """PortAudio realtime callback: zero inference, zero blocking locks."""
        if status:
            logger.debug("PortAudio status: %s", status)
        if self._speaking_suspended or self._user_muted or self._route_mode == AudioRouteMode.MUTED:
            return
        frame = np.copy(indata[:, 0] if indata.ndim > 1 else indata).astype(np.int16, copy=False)
        if not self._recording:
            if self._route_mode == AudioRouteMode.NORMAL:
                self._preroll.append(frame)
            return
        try:
            self._frame_queue.put_nowait(frame)
        except queue.Full:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frame_queue.put_nowait(frame)
            except queue.Full:
                pass

    def push_test_frame(self, frame: np.ndarray) -> None:
        """Inject a frame directly into the worker queue or pre-roll (for deterministic offline tests)."""
        if self._speaking_suspended or self._user_muted or self._route_mode == AudioRouteMode.MUTED:
            return
        f_i16 = frame.astype(np.int16, copy=False)
        if not self._recording and self._route_mode == AudioRouteMode.NORMAL:
            self._preroll.append(f_i16)
            return
        self._frame_queue.put_nowait(f_i16)

    def _drain_queue(self) -> None:
        while True:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                break

    async def start_ptt_capture(
        self,
        *,
        use_vad_endpointing: bool = True,
        initial_timeout_sec: float | None = None,
        on_level: AudioLevelCallback | None = None,
        open_hardware_stream: bool = True,
    ) -> None:
        """Begin capturing audio frames in background until stop, cancel, or VAD end."""
        reuse_existing = open_hardware_stream and self._is_stream_active() and self._stream_device == self._input_device
        if open_hardware_stream and not reuse_existing and not has_input_microphone(self._input_device):
            raise AudioDeviceUnavailableError("No usable input microphone detected on host.")

        with self._capture_lock:
            self._drain_queue()
            if self._speaking_suspended or self._user_muted or self._route_mode == AudioRouteMode.MUTED:
                self._recording = False
                return
            if use_vad_endpointing and self._vad is not None:
                # Retain bounded pre-roll while trimming excessive stale frames
                if len(self._preroll) > self._pre_speech_keep_frames:
                    tail = list(self._preroll)[-self._pre_speech_keep_frames :]
                    self._preroll.clear()
                    self._preroll.extend(tail)
                self._recorded_frames = []
            else:
                self._recorded_frames = list(self._preroll) if self._route_mode == AudioRouteMode.NORMAL else []
                self._preroll.clear()
            self._stop_event.clear()
            self._cancelled = False
            self._recording = True
            self._last_capture_reason = "recording"
            self._speech_end_mono = None

        if self._vad is not None:
            self._vad.reset(initial_timeout_sec=initial_timeout_sec)

        if open_hardware_stream and not reuse_existing:
            self._close_stream()
            import sounddevice as sd

            try:
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=self.frame_samples,
                    device=self._input_device,
                    latency="low",
                    callback=self._audio_callback,
                )
                self._stream.start()
                self._stream_device = self._input_device
            except Exception as exc:
                self._recording = False
                self._stream = None
                raise AudioDeviceUnavailableError(
                    f"Failed to open microphone input stream: {exc}"
                ) from exc

        try:
            await self._consume_frames_loop(
                use_vad_endpointing=use_vad_endpointing,
                on_level=on_level,
            )
        finally:
            keep_open = (
                open_hardware_stream
                and use_vad_endpointing
                and self._last_capture_reason == "no_speech_timeout"
                and not self._cancelled
                and not self._speaking_suspended
                and not self._user_muted
            )
            if not keep_open:
                self._close_stream()
            with self._capture_lock:
                self._recording = False

    async def _consume_frames_loop(
        self,
        *,
        use_vad_endpointing: bool,
        on_level: AudioLevelCallback | None,
    ) -> None:
        loop = asyncio.get_running_loop()
        vad_gated = use_vad_endpointing and self._vad is not None
        speech_started = not vad_gated
        while not self._stop_event.is_set():
            try:
                frame = await loop.run_in_executor(None, self._poll_queue)
            except queue.Empty:
                continue
            if frame is None:
                continue

            if self._route_mode != AudioRouteMode.NORMAL:
                # Ignore incoming frames while assistant is speaking
                continue

            if not vad_gated:
                with self._capture_lock:
                    self._recorded_frames.append(frame)

            if on_level is not None:
                rms_db, peak = compute_rms_db_and_peak(frame)
                try:
                    await on_level(rms_db, peak)
                except (RuntimeError, OSError, ValueError) as exc:
                    logger.debug("Audio level callback ignored error: %s", exc)

            if vad_gated and self._vad is not None:
                if not speech_started:
                    with self._capture_lock:
                        self._preroll.append(frame)
                else:
                    with self._capture_lock:
                        self._recorded_frames.append(frame)

                outcome = await loop.run_in_executor(None, self._vad.process_frame, frame)
                if outcome.event == "speech_start" and not speech_started:
                    speech_started = True
                    keep_n = self._pre_speech_keep_frames + self._vad.onset_frames
                    with self._capture_lock:
                        self._recorded_frames = list(self._preroll)[-keep_n:]
                        self._preroll.clear()
                elif outcome.event in ("speech_end", "max_duration"):
                    self._speech_end_mono = time.perf_counter()
                    self._last_capture_reason = outcome.event
                    self._stop_event.set()
                    break
                elif outcome.event == "no_speech_timeout":
                    self._last_capture_reason = outcome.event
                    self._stop_event.set()
                    break

    def _poll_queue(self) -> np.ndarray | None:
        return self._frame_queue.get(timeout=0.05)

    def _finalize_capture_metrics_locked(
        self,
        joined: np.ndarray,
        reason: str,
    ) -> CaptureMetrics:
        if joined.size == 0:
            nf = self._vad.noise_floor_rms if self._vad is not None else 0.004
            metrics = CaptureMetrics(
                voiced_ms=0.0,
                noise_floor_rms=nf,
                peak_rms=0.0,
                snr_db=None,
                speech_end_mono=self._speech_end_mono if reason != "cancelled" else None,
            )
            self._last_capture_metrics = metrics
            return metrics

        if self._vad is not None and self._vad.voiced_frames > 0:
            metrics = CaptureMetrics(
                voiced_ms=self._vad.voiced_ms,
                noise_floor_rms=self._vad.noise_floor_rms,
                peak_rms=self._vad.peak_rms,
                snr_db=self._vad.snr_db,
                speech_end_mono=self._speech_end_mono,
            )
            self._last_capture_metrics = metrics
            return metrics

        # Fallback acoustic metrics when VAD was disabled or manual stop captured audio
        n_frames = max(1, len(joined) // self.frame_samples)
        usable = joined[: n_frames * self.frame_samples]
        if usable.size >= self.frame_samples:
            frames = usable.reshape(n_frames, self.frame_samples)
            frame_rms = np.sqrt(np.mean(np.square(frames), axis=1))
        else:
            frame_rms = np.array([float(np.sqrt(np.mean(np.square(joined))))], dtype=np.float32)

        peak_rms = round(float(np.max(frame_rms)), 6) if frame_rms.size else 0.0
        est_floor = (
            self._vad.noise_floor_rms
            if self._vad is not None
            else max(0.001, float(np.percentile(frame_rms, 15)))
        )
        gate = max(0.004, est_floor * 1.4)
        voiced_mask = frame_rms >= gate
        voiced_ms = round(
            float(np.sum(voiced_mask)) * (self.frame_samples / float(self.sample_rate)) * 1000.0,
            2,
        )
        if np.any(voiced_mask):
            voiced_rms = float(np.sqrt(np.mean(np.square(frame_rms[voiced_mask]))))
            snr_db: float | None = round(20.0 * math.log10(max(voiced_rms, 1e-6) / max(est_floor, 1e-6)), 2)
        else:
            snr_db = None

        metrics = CaptureMetrics(
            voiced_ms=voiced_ms,
            noise_floor_rms=round(est_floor, 6),
            peak_rms=peak_rms,
            snr_db=snr_db,
            speech_end_mono=self._speech_end_mono,
        )
        self._last_capture_metrics = metrics
        return metrics

    @property
    def last_capture_reason(self) -> str:
        return self._last_capture_reason

    def stop_capture(self, *, cancel: bool = False) -> tuple[np.ndarray, str]:
        """Stop active PTT capture and return ``(float32_audio_16k, reason)``."""
        self._cancelled = cancel
        if cancel:
            self._last_capture_reason = "cancelled"
        elif self._last_capture_reason == "recording":
            self._last_capture_reason = "manual_stop"
            if self._speech_end_mono is None:
                self._speech_end_mono = time.perf_counter()
        self._stop_event.set()
        self._close_stream()

        with self._capture_lock:
            self._recording = False
            if cancel:
                self._recorded_frames = []
                self._preroll.clear()
                self._finalize_capture_metrics_locked(np.zeros(0, dtype=np.float32), self._last_capture_reason)
                return np.zeros(0, dtype=np.float32), self._last_capture_reason
            if (
                not self._recorded_frames
                and self._vad is not None
                and self._vad.onset_streak > 0
                and self._preroll
            ):
                keep_n = self._pre_speech_keep_frames + self._vad.onset_streak
                self._recorded_frames = list(self._preroll)[-keep_n:]
                self._preroll.clear()
            if not self._recorded_frames:
                self._finalize_capture_metrics_locked(np.zeros(0, dtype=np.float32), self._last_capture_reason)
                return np.zeros(0, dtype=np.float32), self._last_capture_reason
            joined = np.concatenate(self._recorded_frames).astype(np.float32) / 32768.0
            self._recorded_frames = []
            self._finalize_capture_metrics_locked(joined, self._last_capture_reason)
            return joined, self._last_capture_reason

    def get_recorded_audio(self) -> tuple[np.ndarray, str]:
        """Collect buffered audio after ``start_ptt_capture`` completes."""
        with self._capture_lock:
            if self._cancelled or not self._recorded_frames:
                self._recorded_frames = []
                self._finalize_capture_metrics_locked(np.zeros(0, dtype=np.float32), self._last_capture_reason)
                return np.zeros(0, dtype=np.float32), self._last_capture_reason
            joined = np.concatenate(self._recorded_frames).astype(np.float32) / 32768.0
            self._recorded_frames = []
            self._finalize_capture_metrics_locked(joined, self._last_capture_reason)
            return joined, self._last_capture_reason

    def close(self) -> None:
        """Close any reusable hardware input stream."""
        self._stop_event.set()
        self._close_stream()

    def _close_stream(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            except (OSError, RuntimeError) as exc:
                logger.debug("Ignoring audio stream stop error: %s", exc)
            try:
                stream.close()
            except (OSError, RuntimeError) as exc:
                logger.debug("Ignoring audio stream close error: %s", exc)

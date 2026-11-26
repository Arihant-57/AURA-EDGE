"""Offline Silero VAD (ONNX) with adaptive RMS noise floor and timeout endpointing."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import onnxruntime as ort


@dataclass(frozen=True)
class VadFrameOutcome:
    """Result of evaluating a single 512-sample (32 ms) 16 kHz audio frame."""

    probability: float
    rms: float
    is_speech: bool
    event: Literal["none", "speech_start", "speech_end", "no_speech_timeout", "max_duration"]


class SileroVadDetector:
    """CPU-only Silero VAD ONNX endpointing state machine.

    Separates ``initial_timeout_sec`` (aborts quickly when no speech begins) from
    ``trailing_silence_sec`` (ends utterance cleanly after speech stops).
    """

    def __init__(
        self,
        model_path: Path,
        *,
        sample_rate: int = 16000,
        frame_samples: int = 512,
        threshold: float = 0.45,
        initial_timeout_sec: float = 3.0,
        trailing_silence_sec: float = 1.2,
        max_utterance_sec: float = 20.0,
        min_rms_floor: float = 0.004,
        onset_frames: int = 2,
    ) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(
                f"Silero VAD ONNX model not found at '{model_path}'. "
                "Run backend/scripts/prepare_models.py while online to prepare local weights."
            )
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.threshold = threshold
        self.initial_timeout_sec = initial_timeout_sec
        self.trailing_silence_sec = trailing_silence_sec
        self.max_utterance_sec = max_utterance_sec
        self.min_rms_floor = min_rms_floor
        self.onset_frames = max(1, int(onset_frames))

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        input_names = {inp.name for inp in self._session.get_inputs()}
        self._is_v5 = "state" in input_names
        self._context_size = 64 if sample_rate == 16000 else 32
        self._active_initial_timeout_sec = self.initial_timeout_sec
        self.reset()

    def reset(self, *, initial_timeout_sec: float | None = None) -> None:
        """Reset LSTM/GRU states and endpointing timers for a fresh turn."""
        if self._is_v5:
            self._state = np.zeros((2, 1, 128), dtype=np.float32)
            self._context = np.zeros((1, self._context_size), dtype=np.float32)
        else:
            self._h = np.zeros((2, 1, 64), dtype=np.float32)
            self._c = np.zeros((2, 1, 64), dtype=np.float32)
        self._sr = np.array(self.sample_rate, dtype=np.int64)
        self._speech_started = False
        self._elapsed_sec = 0.0
        self._silence_sec = 0.0
        self._noise_floor_rms = self.min_rms_floor
        self._onset_streak = 0
        self._onset_rms_sq: list[float] = []
        self._voiced_frames = 0
        self._voiced_ms = 0.0
        self._voiced_rms_sq_sum = 0.0
        self._peak_rms = 0.0
        self._frame_count = 0
        self._active_initial_timeout_sec = (
            float(initial_timeout_sec)
            if initial_timeout_sec is not None
            else self.initial_timeout_sec
        )

    @property
    def speech_started(self) -> bool:
        return self._speech_started

    @property
    def onset_streak(self) -> int:
        return self._onset_streak

    @property
    def voiced_frames(self) -> int:
        return self._voiced_frames

    @property
    def voiced_ms(self) -> float:
        return self._voiced_ms

    @property
    def noise_floor_rms(self) -> float:
        return round(self._noise_floor_rms, 6)

    @property
    def peak_rms(self) -> float:
        return round(self._peak_rms, 6)

    @property
    def snr_db(self) -> float | None:
        if self._voiced_frames <= 0:
            return None
        voiced_rms = math.sqrt(self._voiced_rms_sq_sum / self._voiced_frames)
        return round(20.0 * math.log10(max(voiced_rms, 1e-6) / max(self._noise_floor_rms, 1e-6)), 2)

    def infer_probability(self, frame_float32: np.ndarray) -> float:
        """Run CPU ONNX inference on one 1-D float32 frame of length ``frame_samples``."""
        if frame_float32.ndim != 1:
            frame_float32 = frame_float32.reshape(-1)
        if len(frame_float32) < self.frame_samples:
            padded = np.zeros(self.frame_samples, dtype=np.float32)
            padded[: len(frame_float32)] = frame_float32
            frame_float32 = padded
        elif len(frame_float32) > self.frame_samples:
            frame_float32 = frame_float32[: self.frame_samples]

        x = frame_float32.astype(np.float32, copy=False).reshape(1, self.frame_samples)

        if self._is_v5:
            model_input = np.concatenate([self._context, x], axis=1)
            ort_inputs = {"input": model_input, "state": self._state, "sr": self._sr}
            out, new_state = self._session.run(None, ort_inputs)
            self._state = new_state
            self._context = model_input[:, -self._context_size :]
            return float(np.squeeze(out))

        ort_inputs = {"input": x, "sr": self._sr, "h": self._h, "c": self._c}
        out, new_h, new_c = self._session.run(None, ort_inputs)
        self._h = new_h
        self._c = new_c
        return float(np.squeeze(out))

    def process_frame(self, frame_int16_or_float: np.ndarray) -> VadFrameOutcome:
        """Evaluate one audio frame and update adaptive noise floor + endpointing state."""
        if frame_int16_or_float.dtype == np.int16:
            frame_f32 = frame_int16_or_float.astype(np.float32) / 32768.0
        else:
            frame_f32 = frame_int16_or_float.astype(np.float32, copy=False)

        rms = float(np.sqrt(np.mean(np.square(frame_f32)))) if len(frame_f32) else 0.0
        prob = self.infer_probability(frame_f32)
        frame_dur = float(self.frame_samples) / float(self.sample_rate)
        self._elapsed_sec += frame_dur
        self._frame_count += 1
        self._peak_rms = max(self._peak_rms, rms)

        adaptive_rms_gate = max(self.min_rms_floor, self._noise_floor_rms * 1.4)
        is_speech = prob >= self.threshold and rms >= adaptive_rms_gate

        event: Literal["none", "speech_start", "speech_end", "no_speech_timeout", "max_duration"] = "none"

        if not self._speech_started:
            if is_speech:
                self._onset_streak += 1
                self._onset_rms_sq.append(rms * rms)
                if self._onset_streak >= self.onset_frames:
                    self._speech_started = True
                    self._silence_sec = 0.0
                    self._voiced_frames += self._onset_streak
                    self._voiced_rms_sq_sum += sum(self._onset_rms_sq)
                    self._voiced_ms = round(self._voiced_frames * frame_dur * 1000.0, 2)
                    self._onset_rms_sq.clear()
                    event = "speech_start"
            else:
                self._onset_streak = 0
                self._onset_rms_sq.clear()
                # Adapt ambient noise floor cleanly without letting transient pops spike the floor
                if prob < self.threshold * 0.85:
                    capped_rms = min(rms, max(self.min_rms_floor * 3.0, self._noise_floor_rms * 2.0, 0.05))
                    alpha = 0.25 if self._frame_count <= 6 else 0.08
                    self._noise_floor_rms = max(
                        1e-4,
                        (1.0 - alpha) * self._noise_floor_rms + alpha * capped_rms,
                    )

            if not self._speech_started and self._elapsed_sec >= self._active_initial_timeout_sec:
                event = "no_speech_timeout"
        else:
            # Gentle continuation hysteresis preserves natural mid-word/phrase dips
            continue_speech = is_speech or (
                prob >= self.threshold * 0.75 and rms >= adaptive_rms_gate * 0.8
            )
            if continue_speech:
                self._silence_sec = 0.0
                self._voiced_frames += 1
                self._voiced_rms_sq_sum += rms * rms
                self._voiced_ms = round(self._voiced_frames * frame_dur * 1000.0, 2)
            else:
                self._silence_sec += frame_dur
                if prob < self.threshold * 0.5:
                    capped_rms = min(rms, max(self.min_rms_floor * 2.5, self._noise_floor_rms * 1.8, 0.05))
                    self._noise_floor_rms = 0.98 * self._noise_floor_rms + 0.02 * capped_rms
                if self._silence_sec >= self.trailing_silence_sec:
                    event = "speech_end"

        if event == "none" and self._elapsed_sec >= self.max_utterance_sec:
            event = "max_duration"

        return VadFrameOutcome(
            probability=prob,
            rms=rms,
            is_speech=is_speech,
            event=event,
        )

"""Offline Silero VAD (ONNX) with adaptive RMS noise floor and timeout endpointing."""

from __future__ import annotations

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
        self.reset()

    def reset(self) -> None:
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

        adaptive_rms_gate = max(self.min_rms_floor, self._noise_floor_rms * 1.4)
        is_speech = prob >= self.threshold and rms >= adaptive_rms_gate

        if not is_speech and not self._speech_started:
            # Slowly adapt ambient noise floor during initial silence
            self._noise_floor_rms = 0.92 * self._noise_floor_rms + 0.08 * min(rms, 0.05)

        event: Literal["none", "speech_start", "speech_end", "no_speech_timeout", "max_duration"] = "none"

        if is_speech:
            self._silence_sec = 0.0
            if not self._speech_started:
                self._speech_started = True
                event = "speech_start"
        else:
            if self._speech_started:
                self._silence_sec += frame_dur
                if self._silence_sec >= self.trailing_silence_sec:
                    event = "speech_end"
            elif self._elapsed_sec >= self.initial_timeout_sec:
                event = "no_speech_timeout"

        if event == "none" and self._elapsed_sec >= self.max_utterance_sec:
            event = "max_duration"

        return VadFrameOutcome(
            probability=prob,
            rms=rms,
            is_speech=is_speech,
            event=event,
        )

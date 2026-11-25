"""Tests for AudioCapture ring buffer, echo suppression, Silero VAD, and missing-mic errors."""

from __future__ import annotations

import asyncio
import wave

import numpy as np
import pytest

from aura_edge.config import Settings
from aura_edge.speech.audio_capture import (
    AudioCapture,
    AudioDeviceUnavailableError,
    AudioRouteMode,
    list_audio_devices,
)
from aura_edge.speech.vad import SileroVadDetector


def test_host_audio_devices_enumerated_via_sounddevice() -> None:
    devices = list_audio_devices()
    assert len(devices) > 0
    assert any(d.max_input_channels > 0 for d in devices)
    assert any(d.max_output_channels > 0 for d in devices)


def test_audio_capture_missing_device_raises_clear_error() -> None:
    cap = AudioCapture()
    with pytest.raises(AudioDeviceUnavailableError, match="unavailable"):
        cap.select_device("nonexistent-usb-mic-99999")


async def test_audio_capture_echo_suppression_and_cancellation() -> None:
    cap = AudioCapture(sample_rate=16000, frame_samples=512, preroll_sec=1.0)
    # In NORMAL mode, frames are captured; in MUTED/MONITOR_ONLY, frames are ignored
    cap.set_route_mode(AudioRouteMode.MONITOR_ONLY)
    assert cap.route_mode == AudioRouteMode.MONITOR_ONLY

    task = asyncio.create_task(
        cap.start_ptt_capture(use_vad_endpointing=False, open_hardware_stream=False)
    )
    await asyncio.sleep(0.05)

    tone = (np.sin(2 * np.pi * 440 * np.arange(512) / 16000) * 8000).astype(np.int16)
    cap.push_test_frame(tone)
    await asyncio.sleep(0.05)

    # Switch back to NORMAL and push one real frame
    cap.set_route_mode(AudioRouteMode.NORMAL)
    cap.push_test_frame(tone)
    await asyncio.sleep(0.08)

    audio, reason = cap.stop_capture(cancel=False)
    await task
    assert reason == "manual_stop"
    assert len(audio) == 512  # Only the NORMAL-mode frame was kept

    # Test cancel discards audio cleanly
    task2 = asyncio.create_task(
        cap.start_ptt_capture(use_vad_endpointing=False, open_hardware_stream=False)
    )
    await asyncio.sleep(0.05)
    cap.push_test_frame(tone)
    await asyncio.sleep(0.05)
    cancelled_audio, cancel_reason = cap.stop_capture(cancel=True)
    await task2
    assert cancel_reason == "cancelled"
    assert cancelled_audio.size == 0


def test_silero_vad_missing_model_and_timeout_and_speech_endpointing() -> None:
    settings = Settings(_env_file=None)
    missing_path = settings.models_dir / "vad" / "does_not_exist.onnx"
    with pytest.raises(FileNotFoundError, match="Silero VAD"):
        SileroVadDetector(missing_path)

    vad_path = settings.models_dir / "vad" / "silero_vad.onnx"
    vad = SileroVadDetector(
        vad_path,
        initial_timeout_sec=0.35,
        trailing_silence_sec=0.35,
        threshold=0.35,
    )

    # 1. Pure silence triggers initial no_speech_timeout quickly (no 30s hang)
    silence = np.zeros(512, dtype=np.int16)
    events = [vad.process_frame(silence).event for _ in range(15)]
    assert "no_speech_timeout" in events

    # 2. Noisy low-level background does not trigger false speech
    rng = np.random.default_rng(42)
    vad.reset()
    quiet_noise = (rng.normal(0, 0.001, 512) * 32767).astype(np.int16)
    noisy_events = [vad.process_frame(quiet_noise).event for _ in range(15)]
    assert "no_speech_timeout" in noisy_events

    # 3. Real speech sample followed by trailing silence triggers speech_start -> speech_end
    eval_wav = settings.models_dir / "eval_audio" / "phase1a_tts_en.wav"
    if eval_wav.is_file():
        with wave.open(str(eval_wav), "rb") as wf:
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            sr = wf.getframerate()
        if sr != 16000:
            import math

            from scipy.signal import resample_poly

            g = math.gcd(16000, sr)
            pcm_f = resample_poly(pcm.astype(np.float32) / 32768.0, 16000 // g, sr // g)
            pcm = (np.clip(pcm_f, -1.0, 1.0) * 32767).astype(np.int16)

        vad.reset()
        observed = []
        for i in range(0, len(pcm) - 512, 512):
            observed.append(vad.process_frame(pcm[i : i + 512]).event)
        for _ in range(25):
            observed.append(vad.process_frame(silence).event)
        assert "speech_start" in observed
        assert "speech_end" in observed

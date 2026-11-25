"""Offline Windows SAPI5 / System.Speech fallback backend (English only, zero network)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from aura_edge.intelligence.schemas import LanguageCode
from aura_edge.speech.tts_backends.base import SynthesizedAudio


class OneCoreFallbackBackend:
    """Zero-download offline Windows SAPI fallback for English when ONNX weights are absent."""

    name = "windows_sapi"

    def supports_language(self, language: LanguageCode) -> bool:
        return sys.platform == "win32" and language == "en"

    def model_info(self, language: LanguageCode) -> tuple[str, str]:
        return ("system_speech_sapi5", "default_en_voice")

    def synthesize(
        self,
        text: str,
        *,
        language: LanguageCode,
        speed: float = 1.0,
    ) -> SynthesizedAudio:
        if not self.supports_language(language):
            raise RuntimeError(
                f"Windows SAPI fallback supports English on Windows only (got {language!r})."
            )
        clean = text.strip()
        if not clean:
            return SynthesizedAudio(
                samples=np.zeros(0, dtype=np.float32),
                sample_rate=22050,
                language=language,
                backend=self.name,
                model_name="system_speech_sapi5",
                voice_name="default_en_voice",
                latency_ms=0.0,
            )

        rate = max(-5, min(5, round((speed - 1.0) * 5)))
        t0 = time.perf_counter()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tmp_wav = Path(tf.name)

        try:
            ps_script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.Rate = {rate}; "
                f"$s.SetOutputToWaveFile('{str(tmp_wav).replace(chr(39), chr(39)*2)}'); "
                "$s.Speak($env:AURA_TTS_TEXT); "
                "$s.Dispose()"
            )
            env = dict(os.environ)
            env["AURA_TTS_TEXT"] = clean
            kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if sys.platform == "win32" else {}
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                **kwargs,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "Windows SAPI synthesis failed.")

            with wave.open(str(tmp_wav), "rb") as wf:
                sr = wf.getframerate()
                raw = wf.readframes(wf.getnframes())
                channels = wf.getnchannels()
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if channels > 1:
                pcm = pcm.reshape(-1, channels)[:, 0]
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return SynthesizedAudio(
                samples=pcm,
                sample_rate=sr,
                language=language,
                backend=self.name,
                model_name="system_speech_sapi5",
                voice_name="default_en_voice",
                latency_ms=latency_ms,
            )
        finally:
            try:
                tmp_wav.unlink(missing_ok=True)
            except OSError:
                pass

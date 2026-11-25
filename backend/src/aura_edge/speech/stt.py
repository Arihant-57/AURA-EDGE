"""Offline multilingual Faster-Whisper STT with Windows CUDA DLL guard & CPU fallback."""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel

from aura_edge.config import Settings, apply_offline_env_guards
from aura_edge.infrastructure.compute import ComputeManager
from aura_edge.intelligence.schemas import LanguageCode, RequestedLanguage

logger = logging.getLogger(__name__)

_SUPPORTED_WHISPER_LANGS = {"en", "hi", "kn"}
_DRAVIDIAN_FALLBACK_LANGS = {"ml", "te", "ta"}
_DOMAIN_INITIAL_PROMPTS: dict[str, str] = {
    "en": (
        "time, date, today, timer, cancel, pause, resume, remaining, minutes, seconds, hours, "
        "stop, yes, no, bedroom, living room, kitchen, hallway, light, fan, AC, TV, curtain, "
        "turn on, turn off, brightness, temperature."
    ),
    "hi": (
        "समय, तारीख, आज, मिनट, सेकंड, घंटे, चाय, टाइमर लगाओ, कितने टाइमर चल रहे हैं, "
        "सभी टाइमर रद्द करो, हाँ, नहीं, रुको, बेडरूम, लिविंग रूम, किचन, हॉलवे, "
        "लाइट, पंखा, एसी, टीवी, पर्दा, चालू करो, बंद करो।"
    ),
    "kn": (
        "ಸಮಯ, ದಿನಾಂಕ, ತಾರೀಖು, ಇವತ್ತು, ಗಂಟೆ, ನಿಮಿಷ, ಸೆಕೆಂಡ್, ಟೈಮರ್ ಇಡು, ಟೈಮರ್ ರದ್ದು ಮಾಡು, "
        "ಹೌದು, ಇಲ್ಲ, ನಿಲ್ಲಿಸು, ಬೆಡ್‌ರೂಮ್, ಲಿವಿಂಗ್ ರೂಮ್, ಕಿಚನ್, ಹಾಲ್‌ವೇ, "
        "ಲೈಟ್, ಫ್ಯಾನ್, ಎಸಿ, ಟಿವಿ, ಕರ್ಟನ್, ಆನ್ ಮಾಡು, ಆಫ್ ಮಾಡು."
    ),
}

_SHORT_VALID_COMMANDS = frozenset({
    "stop",
    "yes",
    "no",
    "ok",
    "okay",
    "cancel",
    "pause",
    "resume",
    "help",
    "ಹೌದು",
    "ಇಲ್ಲ",
    "ನಿಲ್ಲಿಸು",
    "ಸರಿ",
    "हाँ",
    "हां",
    "नहीं",
    "रुको",
    "ठीक",
})

_HALLUCINATION_PHRASES = frozenset({
    "thank you",
    "thank you very much",
    "thanks for watching",
    "thanks",
    "you",
    "bye",
    "bye bye",
    "mbc",
    "subtitles by",
    "amara org",
    "please subscribe",
    "subscribe",
    "the end",
    "silence",
    "music",
})

RejectionReason = Literal["silence", "noise", "unclear_speech", "invalid_script"]
RecognitionMode = Literal["multilingual", "strict"]


def _is_kannada_char(ch: str) -> bool:
    return "\u0C80" <= ch <= "\u0CFF"


def _is_devanagari_char(ch: str) -> bool:
    return "\u0900" <= ch <= "\u097F"


def _is_other_indic_char(ch: str) -> bool:
    # Bengali/Assamese, Gurmukhi, Gujarati, Oriya, Tamil, Telugu, Malayalam
    return (
        ("\u0980" <= ch <= "\u09FF")
        or ("\u0A00" <= ch <= "\u0A7F")
        or ("\u0A80" <= ch <= "\u0AFF")
        or ("\u0B00" <= ch <= "\u0B7F")
        or ("\u0B80" <= ch <= "\u0BFF")
        or ("\u0C00" <= ch <= "\u0C7F")
        or ("\u0D00" <= ch <= "\u0D7F")
    )


def _is_supported_alpha(ch: str) -> bool:
    return (
        ("a" <= ch <= "z")
        or ("A" <= ch <= "Z")
        or _is_devanagari_char(ch)
        or _is_kannada_char(ch)
    )


def _is_short_valid_command(text: str) -> bool:
    cleaned = re.sub(r"[\s.,!?।॥\"'`~;:()\[\]{}\-]+", " ", (text or "").strip().lower()).strip()
    return cleaned in _SHORT_VALID_COMMANDS


def _has_excessive_repetition(text: str) -> bool:
    tokens = re.findall(r"[\w\u0900-\u097F\u0C80-\u0CFF]+", (text or "").lower())
    if len(tokens) < 4:
        return False
    streak = 1
    for idx in range(1, len(tokens)):
        if tokens[idx] == tokens[idx - 1]:
            streak += 1
            if streak >= 4:
                return True
        else:
            streak = 1
    return False


def _validate_kn_ctc_output(text: str) -> tuple[bool, str, RejectionReason | None]:
    """Validate IndicConformer Kannada CTC output against stray 5,633-token multi-script leaks."""
    clean = " ".join((text or "").split()).strip()
    if not clean:
        return False, "", None

    alpha_chars = [ch for ch in clean if ch.isalpha()]
    if not alpha_chars:
        return False, "", "unclear_speech"

    kn_count = sum(1 for ch in alpha_chars if _is_kannada_char(ch))
    other_indic_count = sum(1 for ch in alpha_chars if _is_other_indic_char(ch) or _is_devanagari_char(ch))
    total_alpha = len(alpha_chars)

    if kn_count == 0 and other_indic_count > 0:
        return False, clean, "invalid_script"
    if other_indic_count > 0 and (other_indic_count / total_alpha) > 0.25:
        return False, clean, "invalid_script"

    # Strip isolated single stray non-Kannada Indic tokens while keeping Kannada + Latin + digits
    if other_indic_count > 0:
        clean = "".join(ch for ch in clean if not (_is_other_indic_char(ch) or _is_devanagari_char(ch)))
        clean = " ".join(clean.split()).strip()
        if not clean:
            return False, "", "invalid_script"

    if _has_excessive_repetition(clean):
        return False, clean, "unclear_speech"

    return True, clean, None


class ModelNotFoundError(FileNotFoundError):
    """Raised when local offline model weights are missing."""


class TranscriptionResult(BaseModel):
    """Structured offline speech transcription result."""

    text: str
    detected_language: LanguageCode
    language_probability: float | None = None
    model_size: str
    device: Literal["cpu", "cuda"]
    compute_type: str
    latency_ms: float
    is_empty: bool = False
    fallback_reason: str | None = None

    # R2 & R4 decoupled recognition + quality gate fields
    preferred_reply_language: RequestedLanguage = "auto"
    recognition_mode: RecognitionMode = "multilingual"
    transcript_confidence: float | None = None
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None
    voiced_ms: float = 0.0
    snr_db: float | None = None
    quality_ok: bool = True
    rejection_reason: RejectionReason | None = None


class WhisperTranscriber:
    """Thread-safe offline STT wrapper (Faster-Whisper + IndicConformer KN specialist)."""

    def __init__(self, settings: Settings, compute_manager: ComputeManager) -> None:
        self._settings = settings
        self._compute_manager = compute_manager
        self._lock = threading.Lock()
        self._model: Any = None
        self._loaded_key: tuple[str, str, str] | None = None
        self._kn_recognizer: Any = None
        apply_offline_env_guards(settings)

    def resolve_kannada_specialist_dir(self) -> Path | None:
        """Return prepared offline IndicConformer Kannada directory if available."""
        kn_dir = self._settings.models_dir / "stt" / "indic-conformer-kn"
        if (kn_dir / "model.int8.onnx").is_file() and (kn_dir / "tokens.txt").is_file():
            return kn_dir
        return None

    def _load_kn_recognizer_locked(self, kn_dir: Path) -> Any:
        if self._kn_recognizer is not None:
            return self._kn_recognizer
        import sherpa_onnx

        self._kn_recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            model=str(kn_dir / "model.int8.onnx"),
            tokens=str(kn_dir / "tokens.txt"),
            num_threads=4,
            provider="cpu",
        )
        return self._kn_recognizer

    def _transcribe_kn_specialist_locked(
        self,
        samples: np.ndarray,
        kn_dir: Path,
    ) -> tuple[str, float]:
        rec = self._load_kn_recognizer_locked(kn_dir)
        t0 = time.perf_counter()
        stream = rec.create_stream()
        stream.accept_waveform(16000, samples)
        rec.decode_stream(stream)
        text = str(getattr(stream.result, "text", "") or "").strip()
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return text, latency_ms

    def resolve_model_dir(self, model_size: str | None = None) -> Path:
        size = model_size or self._settings.stt_model_size
        model_dir = self._settings.models_dir / "stt" / f"faster-whisper-{size}"
        if not model_dir.is_dir() or not (model_dir / "model.bin").is_file():
            raise ModelNotFoundError(
                f"Local Faster-Whisper '{size}' model not found at '{model_dir}'. "
                "Run backend/scripts/prepare_models.py before offline execution."
            )
        return model_dir

    def _load_model_locked(self, model_size: str | None = None) -> tuple[Any, str, str, str | None]:
        size = model_size or self._settings.stt_model_size
        model_dir = self.resolve_model_dir(size)
        placement = self._compute_manager.resolve_placement()
        target_key = (str(model_dir), placement.stt_device, placement.stt_compute_type)

        if self._model is not None and self._loaded_key == target_key:
            return self._model, placement.stt_device, placement.stt_compute_type, placement.fallback_reason

        from faster_whisper import WhisperModel

        try:
            self._model = WhisperModel(
                str(model_dir),
                device=placement.stt_device,
                compute_type=placement.stt_compute_type,
                local_files_only=True,
            )
            self._loaded_key = target_key
            return self._model, placement.stt_device, placement.stt_compute_type, placement.fallback_reason
        except Exception as exc:
            if placement.stt_device == "cuda":
                reason = f"CUDA Whisper init failed ({exc}); fell back to CPU int8."
                logger.warning(reason)
                fallback = self._compute_manager.record_runtime_fallback(reason)
                self._model = WhisperModel(
                    str(model_dir),
                    device="cpu",
                    compute_type="int8",
                    local_files_only=True,
                )
                self._loaded_key = (str(model_dir), "cpu", "int8")
                return self._model, "cpu", "int8", fallback.fallback_reason
            raise

    @staticmethod
    def _estimate_audio_stats(
        samples: np.ndarray,
        *,
        vad_voiced_ms: float | None,
        noise_floor_rms: float | None,
    ) -> tuple[float, float, float, float | None, bool]:
        """Compute ``(peak, rms, voiced_ms, snr_db, low_dynamic_range_noise)``."""
        peak = float(np.max(np.abs(samples)))
        rms = float(np.sqrt(np.mean(np.square(samples))))
        frame_samples = 512
        frame_dur_ms = (frame_samples / 16000.0) * 1000.0
        n_frames = len(samples) // frame_samples

        if n_frames >= 1:
            usable = samples[: n_frames * frame_samples].reshape(n_frames, frame_samples)
            frame_rms = np.sqrt(np.mean(np.square(usable), axis=1))
        else:
            frame_rms = np.array([rms], dtype=np.float32)

        p15 = float(np.percentile(frame_rms, 15)) if frame_rms.size else rms
        p90 = float(np.percentile(frame_rms, 90)) if frame_rms.size else rms
        if noise_floor_rms is not None:
            eff_floor = max(1e-5, float(noise_floor_rms))
            can_compute_snr = True
        elif n_frames >= 6 and p15 < 0.05:
            eff_floor = max(0.001, p15)
            can_compute_snr = True
        else:
            eff_floor = 0.004
            can_compute_snr = False

        gate = max(0.004, eff_floor * 1.4)
        voiced_mask = frame_rms >= gate
        computed_voiced_ms = round(float(np.sum(voiced_mask)) * frame_dur_ms, 2)
        eff_voiced_ms = float(vad_voiced_ms) if vad_voiced_ms is not None else computed_voiced_ms

        snr_db: float | None = None
        if can_compute_snr:
            active_rms = (
                float(np.sqrt(np.mean(np.square(frame_rms[voiced_mask]))))
                if np.any(voiced_mask)
                else rms
            )
            snr_db = round(20.0 * math.log10(max(active_rms, 1e-6) / max(eff_floor, 1e-6)), 2)

        # Steady stationary noise (e.g. fan/white noise) has flat crest/frame ratio and low SNR
        flat_noise = (
            n_frames >= 10
            and p15 >= 0.003
            and (p90 / max(p15, 1e-6)) < 1.8
            and (snr_db is None or snr_db < 6.0)
        )
        return peak, rms, eff_voiced_ms, snr_db, flat_noise

    @staticmethod
    def _extract_segment_metrics(
        seg_list: list[Any],
        *,
        allow_short_command: bool = True,
    ) -> tuple[str, float | None, float | None, float | None, bool]:
        """Filter bad Whisper segments and return ``(text, avg_logprob, no_speech_prob, compression_ratio, all_dropped)``."""
        if not seg_list:
            return "", None, None, None, False

        raw_lps: list[float] = []
        raw_nsps: list[float] = []
        raw_comps: list[float] = []
        kept_texts: list[str] = []
        kept_lps: list[float] = []
        kept_nsps: list[float] = []
        kept_comps: list[float] = []

        for seg in seg_list:
            seg_txt = str(getattr(seg, "text", "") or "").strip()
            lp = getattr(seg, "avg_logprob", None)
            nsp = getattr(seg, "no_speech_prob", None)
            comp = getattr(seg, "compression_ratio", None)
            if lp is not None:
                raw_lps.append(float(lp))
            if nsp is not None:
                raw_nsps.append(float(nsp))
            if comp is not None:
                raw_comps.append(float(comp))

            if not seg_txt:
                continue

            is_short_cmd = allow_short_command and _is_short_valid_command(seg_txt)
            drop_nsp = nsp is not None and float(nsp) > (0.85 if is_short_cmd else 0.78)
            drop_lp = lp is not None and float(lp) < (-1.75 if is_short_cmd else -1.55)
            if drop_nsp or drop_lp:
                continue

            kept_texts.append(seg_txt)
            if lp is not None:
                kept_lps.append(float(lp))
            if nsp is not None:
                kept_nsps.append(float(nsp))
            if comp is not None:
                kept_comps.append(float(comp))

        text = " ".join(kept_texts).strip()
        use_lps = kept_lps if kept_lps else raw_lps
        use_nsps = kept_nsps if kept_nsps else raw_nsps
        use_comps = kept_comps if kept_comps else raw_comps

        avg_lp = round(sum(use_lps) / len(use_lps), 4) if use_lps else None
        no_sp = round(max(use_nsps), 4) if use_nsps else None
        comp_r = round(max(use_comps), 4) if use_comps else None
        all_dropped = bool(seg_list) and not text
        return text, avg_lp, no_sp, comp_r, all_dropped

    @staticmethod
    def _compute_transcript_confidence(
        avg_logprob: float | None,
        no_speech_prob: float | None,
    ) -> float | None:
        if avg_logprob is None:
            return None
        prob_lp = math.exp(max(-5.0, min(0.0, avg_logprob)))
        nsp_factor = 1.0 - 0.5 * min(1.0, max(0.0, no_speech_prob or 0.0))
        return round(max(0.0, min(0.9999, prob_lp * nsp_factor)), 4)

    @staticmethod
    def _evaluate_quality_gate(
        *,
        text: str,
        model_used: str,
        rms: float,
        peak: float,
        voiced_ms: float,
        snr_db: float | None,
        flat_noise: bool,
        avg_logprob: float | None,
        no_speech_prob: float | None,
        compression_ratio: float | None,
        all_whisper_segments_dropped: bool,
    ) -> tuple[bool, str, bool, RejectionReason | None]:
        """Return ``(quality_ok, final_text, is_empty, rejection_reason)`` for R4."""
        clean = " ".join((text or "").split()).strip()
        if peak < 1e-4 or rms < 3e-4:
            return False, "", True, "silence"

        if _is_short_valid_command(clean):
            if (
                (no_speech_prob is not None and no_speech_prob > 0.88 and avg_logprob is not None and avg_logprob < -1.8)
                or (snr_db is not None and snr_db < 0.0 and voiced_ms < 64.0)
            ):
                return False, "", True, "noise"
            return True, clean, False, None

        if not clean:
            if (
                (no_speech_prob is not None and no_speech_prob > 0.70)
                or (snr_db is not None and snr_db < 3.0)
                or flat_noise
                or voiced_ms < 64.0
            ):
                reason: RejectionReason = "noise" if rms >= 0.004 else "silence"
                return False, "", True, reason
            return False, "", True, "unclear_speech"

        norm_lower = re.sub(r"[\s.,!?।॥]+", " ", clean.lower()).strip()
        if norm_lower in _HALLUCINATION_PHRASES and (
            (no_speech_prob is not None and no_speech_prob > 0.55)
            or (avg_logprob is not None and avg_logprob < -1.0)
            or (snr_db is not None and snr_db < 4.0)
            or flat_noise
            or voiced_ms < 128.0
        ):
            return False, "", True, "noise"

        if no_speech_prob is not None and no_speech_prob > 0.78:
            return False, "", True, "noise"

        if snr_db is not None and snr_db < 2.0 and voiced_ms < 128.0:
            return False, "", True, "noise"

        if flat_noise and avg_logprob is not None and avg_logprob < -1.25:
            return False, "", True, "noise"

        if _has_excessive_repetition(clean):
            return False, clean, False, "unclear_speech"

        if compression_ratio is not None and compression_ratio > 2.6 and (avg_logprob is None or avg_logprob < -1.0):
            return False, clean, False, "unclear_speech"

        if model_used != "indic-conformer-kn" and avg_logprob is not None and avg_logprob < -1.55:
            return False, clean, False, "unclear_speech"

        # Single non-command character or pure punctuation fragment
        alnum_chars = [ch for ch in clean if ch.isalnum() or _is_kannada_char(ch) or _is_devanagari_char(ch)]
        if len(alnum_chars) <= 1:
            return False, clean, False, "unclear_speech"

        # Script sanity check (allow Whisper 'base' KN fallback only when size == 'base')
        if model_used != "base":
            alpha_chars = [ch for ch in clean if ch.isalpha()]
            if alpha_chars:
                supported_alpha = sum(1 for ch in alpha_chars if _is_supported_alpha(ch))
                if supported_alpha / len(alpha_chars) < 0.5:
                    return False, clean, False, "invalid_script"

        return True, clean, False, None

    def _run_whisper_locked(
        self,
        model: Any,
        samples: np.ndarray,
        size: str,
        used_device: str,
        used_ctype: str,
        fb_reason: str | None,
        *,
        lang_arg: str | None,
        prompt_arg: str | None,
    ) -> tuple[list[Any], Any, str, str, str | None, float]:
        t0 = time.perf_counter()
        try:
            segments, info = model.transcribe(
                samples,
                language=lang_arg,
                beam_size=1,
                vad_filter=False,
                condition_on_previous_text=False,
                repetition_penalty=1.2,
                no_repeat_ngram_size=3,
                max_new_tokens=128,
                initial_prompt=prompt_arg,
            )
            seg_list = list(segments)
        except Exception as exc:
            if used_device == "cuda":
                reason = f"CUDA Whisper transcribe failed ({exc}); retrying on CPU int8."
                logger.warning(reason)
                self._compute_manager.record_runtime_fallback(reason)
                self._model = None
                self._loaded_key = None
                model, used_device, used_ctype, fb_reason = self._load_model_locked(size)
                t0 = time.perf_counter()
                segments, info = model.transcribe(
                    samples,
                    language=lang_arg,
                    beam_size=1,
                    vad_filter=False,
                    condition_on_previous_text=False,
                    repetition_penalty=1.2,
                    no_repeat_ngram_size=3,
                    max_new_tokens=128,
                    initial_prompt=prompt_arg,
                )
                seg_list = list(segments)
            else:
                raise
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return seg_list, info, used_device, used_ctype, fb_reason, latency_ms

    def transcribe(
        self,
        audio_float32: np.ndarray,
        language: RequestedLanguage = "auto",
        *,
        preferred_reply_language: RequestedLanguage | None = None,
        recognition_mode: RecognitionMode = "multilingual",
        strict_language: bool = False,
        model_size: str | None = None,
        fallback_language: LanguageCode = "en",
        vad_voiced_ms: float | None = None,
        noise_floor_rms: float | None = None,
    ) -> TranscriptionResult:
        """Transcribe 16 kHz mono float32 audio offline without network access."""
        size = model_size or self._settings.stt_model_size
        pref_reply_lang: RequestedLanguage = (
            preferred_reply_language if preferred_reply_language is not None else language
        )
        eff_mode: RecognitionMode = (
            "strict" if (strict_language or recognition_mode == "strict") else "multilingual"
        )
        strict_target: RequestedLanguage = language if language != "auto" else pref_reply_lang
        if eff_mode == "strict" and strict_target == "auto":
            eff_mode = "multilingual"

        fallback_det: LanguageCode = (
            fallback_language if fallback_language in _SUPPORTED_WHISPER_LANGS else "en"
        )

        if audio_float32.size == 0:
            placement = self._compute_manager.resolve_placement()
            return TranscriptionResult(
                text="",
                detected_language=(fallback_det if strict_target == "auto" else strict_target),
                language_probability=0.0,
                model_size=size,
                device=placement.stt_device,
                compute_type=placement.stt_compute_type,
                latency_ms=0.0,
                is_empty=True,
                fallback_reason=placement.fallback_reason,
                preferred_reply_language=pref_reply_lang,
                recognition_mode=eff_mode,
                voiced_ms=0.0,
                quality_ok=False,
                rejection_reason="silence",
            )

        samples = audio_float32.astype(np.float32, copy=False).reshape(-1)
        peak, rms, voiced_ms, snr_db, flat_noise = self._estimate_audio_stats(
            samples,
            vad_voiced_ms=vad_voiced_ms,
            noise_floor_rms=noise_floor_rms,
        )

        if peak < 1e-4 or (rms < 3e-4 and voiced_ms == 0.0):
            placement = self._compute_manager.resolve_placement()
            return TranscriptionResult(
                text="",
                detected_language=(fallback_det if strict_target == "auto" else strict_target),
                language_probability=0.0,
                model_size=size,
                device=placement.stt_device,
                compute_type=placement.stt_compute_type,
                latency_ms=0.0,
                is_empty=True,
                fallback_reason=placement.fallback_reason,
                preferred_reply_language=pref_reply_lang,
                recognition_mode=eff_mode,
                voiced_ms=voiced_ms,
                snr_db=snr_db,
                quality_ok=False,
                rejection_reason="silence",
            )

        if vad_voiced_ms is not None and vad_voiced_ms <= 0.0 and snr_db is not None and snr_db < 2.0:
            placement = self._compute_manager.resolve_placement()
            return TranscriptionResult(
                text="",
                detected_language=(fallback_det if strict_target == "auto" else strict_target),
                language_probability=0.0,
                model_size=size,
                device=placement.stt_device,
                compute_type=placement.stt_compute_type,
                latency_ms=0.0,
                is_empty=True,
                fallback_reason=placement.fallback_reason,
                preferred_reply_language=pref_reply_lang,
                recognition_mode=eff_mode,
                voiced_ms=voiced_ms,
                snr_db=snr_db,
                quality_ok=False,
                rejection_reason="noise",
            )

        # Safe quiet-mic AGC (modeled on mic_listener.py; never amplifies silence or flat floor)
        if peak > 1.0:
            samples = samples / peak
        elif 0.004 < peak < 0.25 and (
            noise_floor_rms is None
            or rms > noise_floor_rms * 1.25
            or peak > noise_floor_rms * 2.0
        ):
            gain = min(6.0, 0.35 / peak)
            samples = np.clip(samples * gain, -1.0, 1.0)

        # Explicit strict single-language mode (never fabricates language_probability=1.0)
        if eff_mode == "strict" and strict_target != "auto":
            if strict_target == "kn" and size != "base":
                kn_dir = self.resolve_kannada_specialist_dir()
                if kn_dir is not None:
                    with self._lock:
                        kn_text, kn_ms = self._transcribe_kn_specialist_locked(samples, kn_dir)
                    script_ok, clean_kn, script_reason = _validate_kn_ctc_output(kn_text)
                    if not script_ok:
                        return TranscriptionResult(
                            text=clean_kn if script_reason == "invalid_script" else "",
                            detected_language="kn",
                            language_probability=None,
                            model_size="indic-conformer-kn",
                            device="cpu",
                            compute_type="int8",
                            latency_ms=kn_ms,
                            is_empty=(len(clean_kn) == 0 or script_reason is None),
                            fallback_reason=None,
                            preferred_reply_language=pref_reply_lang,
                            recognition_mode="strict",
                            voiced_ms=voiced_ms,
                            snr_db=snr_db,
                            quality_ok=False,
                            rejection_reason=script_reason or ("noise" if flat_noise else "unclear_speech"),
                        )
                    q_ok, final_txt, is_emp, rej = self._evaluate_quality_gate(
                        text=clean_kn,
                        model_used="indic-conformer-kn",
                        rms=rms,
                        peak=peak,
                        voiced_ms=voiced_ms,
                        snr_db=snr_db,
                        flat_noise=flat_noise,
                        avg_logprob=None,
                        no_speech_prob=None,
                        compression_ratio=None,
                        all_whisper_segments_dropped=False,
                    )
                    return TranscriptionResult(
                        text=final_txt,
                        detected_language="kn",
                        language_probability=None,
                        model_size="indic-conformer-kn",
                        device="cpu",
                        compute_type="int8",
                        latency_ms=kn_ms,
                        is_empty=is_emp,
                        fallback_reason=None,
                        preferred_reply_language=pref_reply_lang,
                        recognition_mode="strict",
                        voiced_ms=voiced_ms,
                        snr_db=snr_db,
                        quality_ok=q_ok,
                        rejection_reason=rej,
                    )

            with self._lock:
                model, used_device, used_ctype, fb_reason = self._load_model_locked(size)
                seg_list, _info, used_device, used_ctype, fb_reason, latency_ms = (
                    self._run_whisper_locked(
                        model,
                        samples,
                        size,
                        used_device,
                        used_ctype,
                        fb_reason,
                        lang_arg=strict_target,
                        prompt_arg=_DOMAIN_INITIAL_PROMPTS.get(strict_target),
                    )
                )
            text, avg_lp, no_sp, comp_r, all_dropped = self._extract_segment_metrics(seg_list)
            q_ok, final_txt, is_emp, rej = self._evaluate_quality_gate(
                text=text,
                model_used=size,
                rms=rms,
                peak=peak,
                voiced_ms=voiced_ms,
                snr_db=snr_db,
                flat_noise=flat_noise,
                avg_logprob=avg_lp,
                no_speech_prob=no_sp,
                compression_ratio=comp_r,
                all_whisper_segments_dropped=all_dropped,
            )
            return TranscriptionResult(
                text=final_txt,
                detected_language=strict_target,  # type: ignore[arg-type]
                language_probability=None,
                model_size=size,
                device=used_device,  # type: ignore[arg-type]
                compute_type=used_ctype,
                latency_ms=latency_ms,
                is_empty=is_emp,
                fallback_reason=fb_reason,
                preferred_reply_language=pref_reply_lang,
                recognition_mode="strict",
                transcript_confidence=self._compute_transcript_confidence(avg_lp, no_sp),
                avg_logprob=avg_lp,
                no_speech_prob=no_sp,
                compression_ratio=comp_r,
                voiced_ms=voiced_ms,
                snr_db=snr_db,
                quality_ok=q_ok,
                rejection_reason=rej,
            )

        # Default multilingual mode: ALWAYS run Whisper multilingual language detection first
        with self._lock:
            model, used_device, used_ctype, fb_reason = self._load_model_locked(size)
            seg_list, info, used_device, used_ctype, fb_reason, latency_ms = (
                self._run_whisper_locked(
                    model,
                    samples,
                    size,
                    used_device,
                    used_ctype,
                    fb_reason,
                    lang_arg=None,
                    prompt_arg=None,
                )
            )
            text, avg_lp, no_sp, comp_r, all_dropped = self._extract_segment_metrics(seg_list)
            raw_lang = str(getattr(info, "language", None) or fallback_det)
            raw_prob_val = getattr(info, "language_probability", None)
            lang_prob: float | None = (
                round(float(raw_prob_val), 4) if raw_prob_val is not None else None
            )

            # Selective IndicConformer Kannada specialist invocation
            has_kn_script = any(_is_kannada_char(ch) for ch in text)
            has_hi_script = any(_is_devanagari_char(ch) for ch in text)
            has_latin_alpha = any(("A" <= ch <= "Z") or ("a" <= ch <= "z") for ch in text)
            clean_mixed_kn = (
                has_kn_script
                and has_latin_alpha
                and (avg_lp is not None and avg_lp > -0.75)
                and (no_sp is None or no_sp < 0.5)
            )
            whisper_flagged_non_speech = (
                not seg_list
                and (
                    (no_sp is not None and no_sp > 0.70)
                    or (snr_db is not None and snr_db < 3.0)
                    or flat_noise
                )
            )
            dravidian_ambiguous = (
                raw_lang in _DRAVIDIAN_FALLBACK_LANGS
                and (lang_prob is None or lang_prob < 0.85)
                and not (has_hi_script or (raw_lang == "en"))
            )
            should_try_kn = (
                size != "base"
                and not whisper_flagged_non_speech
                and not clean_mixed_kn
                and (raw_lang == "kn" or has_kn_script or dravidian_ambiguous)
            )
            if should_try_kn:
                kn_dir = self.resolve_kannada_specialist_dir()
                if kn_dir is not None:
                    kn_text, kn_ms = self._transcribe_kn_specialist_locked(samples, kn_dir)
                    script_ok, clean_kn, script_reason = _validate_kn_ctc_output(kn_text)
                    if script_ok and clean_kn:
                        q_ok, final_txt, is_emp, rej = self._evaluate_quality_gate(
                            text=clean_kn,
                            model_used="indic-conformer-kn",
                            rms=rms,
                            peak=peak,
                            voiced_ms=voiced_ms,
                            snr_db=snr_db,
                            flat_noise=flat_noise,
                            avg_logprob=avg_lp,
                            no_speech_prob=no_sp,
                            compression_ratio=comp_r,
                            all_whisper_segments_dropped=False,
                        )
                        return TranscriptionResult(
                            text=final_txt,
                            detected_language="kn",
                            language_probability=lang_prob,
                            model_size="indic-conformer-kn",
                            device="cpu",
                            compute_type="int8",
                            latency_ms=round(latency_ms + kn_ms, 2),
                            is_empty=is_emp,
                            fallback_reason=fb_reason,
                            preferred_reply_language=pref_reply_lang,
                            recognition_mode="multilingual",
                            transcript_confidence=self._compute_transcript_confidence(avg_lp, no_sp),
                            avg_logprob=avg_lp,
                            no_speech_prob=no_sp,
                            compression_ratio=comp_r,
                            voiced_ms=voiced_ms,
                            snr_db=snr_db,
                            quality_ok=q_ok,
                            rejection_reason=rej,
                        )
                    if script_reason == "invalid_script" and raw_lang == "kn":
                        return TranscriptionResult(
                            text=clean_kn,
                            detected_language="kn",
                            language_probability=lang_prob,
                            model_size="indic-conformer-kn",
                            device="cpu",
                            compute_type="int8",
                            latency_ms=round(latency_ms + kn_ms, 2),
                            is_empty=False,
                            fallback_reason=fb_reason,
                            preferred_reply_language=pref_reply_lang,
                            recognition_mode="multilingual",
                            transcript_confidence=self._compute_transcript_confidence(avg_lp, no_sp),
                            avg_logprob=avg_lp,
                            no_speech_prob=no_sp,
                            compression_ratio=comp_r,
                            voiced_ms=voiced_ms,
                            snr_db=snr_db,
                            quality_ok=False,
                            rejection_reason="invalid_script",
                        )

        resolved_lang: LanguageCode
        kn_chars = sum(1 for ch in text if _is_kannada_char(ch))
        hi_chars = sum(1 for ch in text if _is_devanagari_char(ch))
        if kn_chars > 0 and kn_chars >= hi_chars:
            resolved_lang = "kn"
        elif hi_chars > 0:
            resolved_lang = "hi"
        elif raw_lang in _SUPPORTED_WHISPER_LANGS:
            resolved_lang = raw_lang  # type: ignore[assignment]
        else:
            # Never emit unsupported languages (e.g. Tagalog 'tl'); stay within EN/HI/KN
            resolved_lang = fallback_det

        q_ok, final_txt, is_emp, rej = self._evaluate_quality_gate(
            text=text,
            model_used=size,
            rms=rms,
            peak=peak,
            voiced_ms=voiced_ms,
            snr_db=snr_db,
            flat_noise=flat_noise,
            avg_logprob=avg_lp,
            no_speech_prob=no_sp,
            compression_ratio=comp_r,
            all_whisper_segments_dropped=all_dropped,
        )
        return TranscriptionResult(
            text=final_txt,
            detected_language=resolved_lang,
            language_probability=lang_prob,
            model_size=size,
            device=used_device,  # type: ignore[arg-type]
            compute_type=used_ctype,
            latency_ms=latency_ms,
            is_empty=is_emp,
            fallback_reason=fb_reason,
            preferred_reply_language=pref_reply_lang,
            recognition_mode="multilingual",
            transcript_confidence=self._compute_transcript_confidence(avg_lp, no_sp),
            avg_logprob=avg_lp,
            no_speech_prob=no_sp,
            compression_ratio=comp_r,
            voiced_ms=voiced_ms,
            snr_db=snr_db,
            quality_ok=q_ok,
            rejection_reason=rej,
        )

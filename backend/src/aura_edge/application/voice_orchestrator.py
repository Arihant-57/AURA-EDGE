"""Phase 1 Voice & Text Orchestrator uniting audio capture, STT, routing, tools, TTS, and FSM."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from aura_edge.api.ws_events import WebSocketEventHub
from aura_edge.config import ComputeMode, Settings
from aura_edge.data.repositories.timer_repo import VoiceMetricRepository
from aura_edge.execution.state_machine import (
    AssistantState,
    AssistantStateMachine,
    StaleTurnError,
    TurnContext,
)
from aura_edge.execution.tool_registry import ToolRegistry
from aura_edge.execution.tools.datetime_tool import DateTimeTool
from aura_edge.execution.tools.timer_tool import TimerManager, TimerView
from aura_edge.infrastructure.compute import ComputeManager, ComputePlacement
from aura_edge.infrastructure.network_policy import NetworkPolicyEnforcer
from aura_edge.intelligence.conversation import (
    ConversationSession,
    strip_markdown_for_speech,
    unverified_action_fallback,
    validate_no_unverified_action_claim,
    validate_response_language,
)
from aura_edge.intelligence.fast_router import MultilingualFastRouter
from aura_edge.intelligence.normalizer import normalize_utterance
from aura_edge.intelligence.ollama_client import OllamaClient, OllamaUnavailableError
from aura_edge.intelligence.planner import HybridPlanner
from aura_edge.intelligence.response_catalog import get_phrase
from aura_edge.intelligence.schemas import (
    IntentName,
    LanguageCode,
    RequestedLanguage,
    VerifiedActionResult,
)
from aura_edge.speech.audio_capture import (
    AudioCapture,
    AudioDeviceUnavailableError,
    AudioLevelCallback,
    has_input_microphone,
    list_audio_devices,
)
from aura_edge.speech.playback import AudioPlayback
from aura_edge.speech.stt import ModelNotFoundError, TranscriptionResult, WhisperTranscriber
from aura_edge.speech.tts_router import SentenceStreamBuffer, TTSRouter
from aura_edge.speech.vad import SileroVadDetector

logger = logging.getLogger(__name__)


def _empty_stage_timestamps() -> dict[str, float | None]:
    return {
        "speech_end": None,
        "stt_start": None,
        "stt_completion": None,
        "routing_completion": None,
        "tool_completion": None,
        "llm_first_token": None,
        "validated_response_text": None,
        "first_valid_speech_chunk": None,
        "tts_completion": None,
        "first_non_silent_output_callback": None,
        "playback_completion": None,
    }


def _log_bg_task_exception(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task %s failed: %s", task.get_name(), exc, exc_info=exc)


class ProcessTurnOutput(BaseModel):
    """Unified output for text, audio-upload, PTT, and continuous voice turns."""

    turn_id: str
    intent: str
    route_category: str = "tool"
    confidence: float
    intent_source: str
    detected_language: LanguageCode
    preferred_reply_language: RequestedLanguage = "auto"
    recognition_mode: str = "multilingual"
    language_probability: float | None = None
    transcript_confidence: float | None = None
    stt_model: str | None = None
    slots: dict[str, Any]
    response_text: str
    spoken_text: str = ""
    response_language: LanguageCode
    action_executed: bool
    outcome: Literal["success", "clarification", "refusal", "cancelled", "runtime_failure"] = "success"
    status: str
    state: AssistantState
    data: dict[str, Any]
    latency_ms: float
    stt_ms: float | None = None
    router_ms: float | None = None
    llm_ttft_ms: float | None = None
    llm_ms: float | None = None
    tool_ms: float | None = None
    tts_ms: float | None = None
    audio_startup_ms: float | None = None
    playback_ms: float | None = None
    ttfa_ms: float | None = None
    tts_cache_hit: bool = False
    stage_timestamps: dict[str, float | None] = Field(default_factory=_empty_stage_timestamps)
    time_to_first_text_ms: float | None = None
    time_to_first_synthesized_audio_ms: float | None = None
    time_to_first_audio_output_ms: float | None = None
    playback_duration_ms: float | None = None
    total_turn_duration_ms: float | None = None
    error: str | None = None
    transcript: str | None = None


class VoiceOrchestrator:
    """Coordinates the full Phase 1 offline voice-to-action-to-voice pipeline."""

    def __init__(
        self,
        settings: Settings,
        enforcer: NetworkPolicyEnforcer,
        engine: Any,
        ws_hub: WebSocketEventHub,
        smart_home: Any | None = None,
        *,
        memory_service: Any | None = None,
        scheduler_service: Any | None = None,
        notification_service: Any | None = None,
        task_service: Any | None = None,
        calendar_service: Any | None = None,
        briefing_service: Any | None = None,
        grocery_service: Any | None = None,
        expense_service: Any | None = None,
        parcel_service: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.enforcer = enforcer
        self.engine = engine
        self.ws_hub = ws_hub
        self.smart_home = smart_home
        self.memory_service = memory_service
        self.scheduler_service = scheduler_service
        self.notification_service = notification_service
        self.task_service = task_service
        self.calendar_service = calendar_service
        self.briefing_service = briefing_service
        self.grocery_service = grocery_service
        self.expense_service = expense_service
        self.parcel_service = parcel_service

        self.compute_manager = ComputeManager(settings)
        self.session_language: RequestedLanguage = settings.default_language
        self.stt_recognition_mode: Literal["multilingual", "strict"] = settings.stt_recognition_mode
        self.stt_language: RequestedLanguage = settings.stt_language
        self._last_resolved_language: LanguageCode = (
            "en" if settings.default_language == "auto" else settings.default_language
        )

        # Speech components
        vad_path = settings.models_dir / "vad" / "silero_vad.onnx"
        self.vad: SileroVadDetector | None = None
        if vad_path.is_file():
            try:
                self.vad = SileroVadDetector(
                    vad_path,
                    sample_rate=settings.audio_sample_rate,
                    frame_samples=settings.audio_frame_samples,
                    threshold=settings.vad_threshold,
                    initial_timeout_sec=settings.vad_initial_timeout_sec,
                    trailing_silence_sec=settings.vad_trailing_silence_sec,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("Silero VAD failed to initialize: %s", exc)

        self.audio_capture = AudioCapture(
            sample_rate=settings.audio_sample_rate,
            frame_samples=settings.audio_frame_samples,
            preroll_sec=settings.audio_preroll_sec,
            input_device=settings.audio_input_device,
            vad=self.vad,
        )
        self.stt = WhisperTranscriber(settings, self.compute_manager)
        self.tts_router = TTSRouter(settings)
        self.playback = AudioPlayback(
            self.tts_router,
            output_device=settings.audio_output_device,
            output_enabled=settings.audio_output_enabled,
        )

        # Intelligence & Execution components
        self.fast_router = MultilingualFastRouter()
        self.ollama_client = OllamaClient(settings, enforcer, self.compute_manager)
        self.planner = HybridPlanner(self.fast_router, self.ollama_client)
        self.conversation = ConversationSession(max_turns=12, idle_timeout_sec=1800.0)
        self.datetime_tool = DateTimeTool()
        self.timer_manager = TimerManager(engine, on_timer_event=self._handle_timer_event)
        self.tool_registry = ToolRegistry(
            self.datetime_tool,
            self.timer_manager,
            smart_home,
            memory_service=memory_service,
            scheduler_service=scheduler_service,
            notification_service=notification_service,
            task_service=task_service,
            calendar_service=calendar_service,
            briefing_service=briefing_service,
            grocery_service=grocery_service,
            expense_service=expense_service,
            parcel_service=parcel_service,
            default_timezone=settings.default_timezone,
            clock=clock,
        )
        self.metric_repo = VoiceMetricRepository(engine)
        if self.notification_service is not None:
            self.notification_service.set_speak_callback(self.speak_notification)

        self.fsm = AssistantStateMachine(on_change=self._handle_state_change)
        self._turn_lock = asyncio.Lock()
        self._ptt_task: asyncio.Task[None] | None = None
        self._ptt_turn: TurnContext | None = None
        self._conversation_active = False
        self._conversation_mode = "idle"
        self._conversation_speak = True
        self._conversation_task: asyncio.Task[None] | None = None
        self._conversation_silence_limit_sec = 60.0
        self._timer_audio_tasks: set[asyncio.Task[None]] = set()
        self._completed_turn_ids: deque[str] = deque(maxlen=256)

    async def startup(self) -> dict[str, int]:
        """Recover persistent timers on FastAPI startup and warm common TTS phrases."""
        try:
            self.tts_router.prewarm_common_phrases()
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("TTS phrase prewarm skipped: %s", exc)
        try:
            return await self.timer_manager.recover_on_startup()
        except (SQLAlchemyError, OSError, RuntimeError) as exc:
            logger.warning("Timer recovery skipped (DB unavailable): %s", exc)
            return {"recovered": 0, "missed": 0}

    async def shutdown(self) -> None:
        await self.stop_conversation(reason="shutdown")
        for task in list(self._timer_audio_tasks):
            task.cancel()
        self.playback.close()
        self.audio_capture.stop_capture(cancel=True)
        await self.timer_manager.shutdown()

    async def _handle_state_change(
        self,
        state: AssistantState,
        turn_id: str | None,
        message: str | None,
        language: str | None,
    ) -> None:
        # Strict half-duplex: suspend microphone immediately upon entering SPEAKING
        if state == AssistantState.SPEAKING and not self.audio_capture.is_suspended:
            self.audio_capture.suspend_for_playback()

        await self.ws_hub.broadcast(
            "assistant_state_change",
            {
                "state": state.value,
                "turn_id": turn_id,
                "message": message,
                "language": language or self._last_resolved_language,
            },
        )

    async def speak_notification(self, phrase: str, lang: LanguageCode) -> bool:
        """Speak proactive notification under _turn_lock without colliding or clearing user mute."""
        if not self.settings.proactive_audio_enabled or not self.settings.audio_output_enabled:
            return False
        async with self._turn_lock:
            if self.fsm.state != AssistantState.IDLE or self.playback.is_playing:
                return False
            if not self.audio_capture.is_suspended:
                self.audio_capture.suspend_for_playback()
            did_play = False
            try:
                _chunks, pm = await self.playback.speak_text_with_metrics(
                    phrase, language=lang, play_to_speaker=True
                )
                did_play = pm.audio_playback_ms > 0.0
                return did_play
            except asyncio.CancelledError:
                self.playback.stop()
                raise
            except (OSError, RuntimeError, ValueError) as exc:
                logger.debug("Notification audio skipped: %s", exc)
                return False
            finally:
                await self._resume_mic_after_speaking(did_speak=did_play)

    async def _speak_timer_notification(self, phrase: str, lang: LanguageCode) -> None:
        """Speak completed/missed timer alerts exclusively without colliding with turns."""
        await self.speak_notification(phrase, lang)

    async def _handle_timer_event(self, event_type: str, timer_view: TimerView) -> None:
        await self.ws_hub.broadcast(event_type, {"timer": timer_view.model_dump()})
        if event_type in ("timer_completed", "timer_missed"):
            lang: LanguageCode = (
                timer_view.language if timer_view.language in ("en", "hi", "kn") else "en"
            )  # type: ignore[assignment]
            lbl = f' "{timer_view.label}"' if timer_view.label else ""
            phrase = get_phrase("timer_completed", lang, label_part=lbl)
            if self.notification_service is not None:
                title = (
                    f"Timer missed{lbl}"
                    if event_type == "timer_missed"
                    else f"Timer completed{lbl}"
                )
                await self.notification_service.notify(
                    notification_type=event_type,
                    title=title,
                    message=phrase,
                    language=lang,
                    source_id=timer_view.id,
                    priority="high" if event_type == "timer_missed" else "normal",
                    dedup_key=f"timer:{timer_view.id}:{event_type}",
                    metadata={"timer": timer_view.model_dump()},
                    speak=True,
                )
            else:
                task = asyncio.create_task(self._speak_timer_notification(phrase, lang))
                self._timer_audio_tasks.add(task)
                task.add_done_callback(self._timer_audio_tasks.discard)
                task.add_done_callback(_log_bg_task_exception)

    async def _resume_mic_after_speaking(self, *, did_speak: bool) -> None:
        """Resume microphone only after audio output drain and configurable acoustic cooldown."""
        if did_speak and self.settings.audio_post_playback_cooldown_sec > 0.0:
            await asyncio.sleep(self.settings.audio_post_playback_cooldown_sec)
        self.audio_capture.resume_after_playback()

    async def _preempt_idle_listening(self) -> None:
        """Cancel idle continuous/PTT listening before acquiring exclusive turn lock."""
        active_turn = self.fsm.active_turn
        if self.fsm.state == AssistantState.LISTENING and active_turn is not None:
            await self.cancel_turn(turn_id=active_turn.turn_id)

    def set_language(
        self,
        language: RequestedLanguage,
        *,
        recognition_mode: Literal["multilingual", "strict"] | None = None,
    ) -> RequestedLanguage:
        self.session_language = language
        if language != "auto":
            self._last_resolved_language = language
        if recognition_mode is not None:
            self.stt_recognition_mode = recognition_mode
        return self.session_language

    def set_recognition_mode(
        self,
        mode: Literal["multilingual", "strict"],
    ) -> Literal["multilingual", "strict"]:
        self.stt_recognition_mode = mode
        return self.stt_recognition_mode

    def set_compute_mode(self, mode: ComputeMode) -> ComputePlacement:
        return self.compute_manager.set_mode(mode)

    def get_status(self) -> dict[str, Any]:
        placement = self.compute_manager.resolve_placement()
        try:
            stt_ready = self.stt.resolve_model_dir().is_dir()
        except ModelNotFoundError:
            stt_ready = False

        kn_dir = self.stt.resolve_kannada_specialist_dir()
        return {
            "active_state": self.fsm.state.value,
            "active_turn_id": self.fsm.active_turn.turn_id if self.fsm.active_turn else None,
            "stt_engine": "faster-whisper",
            "stt_model": self.settings.stt_model_size,
            "stt_kannada_specialist": "indic-conformer-kn" if kn_dir is not None else None,
            "stt_recognition_mode": self.stt_recognition_mode,
            "stt_language": self.stt_language,
            "stt_ready": stt_ready,
            "stt_device": placement.stt_device,
            "stt_compute_type": placement.stt_compute_type,
            "vad_ready": self.vad is not None,
            "tts_engine": self.tts_router.primary.name,
            "tts_summary": self.tts_router.active_engine_summary(),
            "supported_languages": ["en", "hi", "kn"],
            "session_language": self.session_language,
            "resolved_language": self._last_resolved_language,
            "compute": placement.model_dump(),
            "mic_detected": has_input_microphone(self.settings.audio_input_device),
            "speaker_detected": self.playback.has_output_device(),
            "audio_devices": [d.model_dump() for d in list_audio_devices()],
            "audio_route_mode": self.audio_capture.route_mode.value,
            "mic_muted": self.audio_capture.is_muted,
            "conversation_active": self._conversation_active,
            "conversation_mode": self._conversation_mode,
            "history_length": len(self.conversation.get_history()),
        }

    async def set_mic_mute(self, muted: bool) -> dict[str, Any]:
        """Mute or unmute microphone capture without altering active conversation registration."""
        self.audio_capture.set_user_muted(muted)
        ptt = self._ptt_turn
        if muted and ptt is not None and self.fsm.active_turn is ptt and self.fsm.state == AssistantState.LISTENING:
            self._ptt_turn = None
            await self.cancel_turn(turn_id=ptt.turn_id)
        elif muted and self.audio_capture.is_recording:
            self.audio_capture.stop_capture(cancel=True)
        await self.ws_hub.broadcast(
            "mic_mute_change",
            {"muted": self.audio_capture.is_muted, "route_mode": self.audio_capture.route_mode.value},
        )
        return {"muted": self.audio_capture.is_muted, "route_mode": self.audio_capture.route_mode.value}

    def get_conversation_history(self) -> list[dict[str, Any]]:
        return [m.model_dump() for m in self.conversation.get_history()]

    async def clear_conversation_history(self) -> dict[str, int]:
        removed = self.conversation.clear()
        await self.ws_hub.broadcast("conversation_history_updated", {"count": 0})
        return {"cleared": removed}

    async def prewarm(self, *, prewarm_ollama: bool = True) -> dict[str, Any]:
        tts_stats = self.tts_router.prewarm_common_phrases()
        ollama_ms: float | None = None
        ollama_ok = False
        if prewarm_ollama:
            try:
                ollama_ms = await self.ollama_client.prewarm()
                ollama_ok = True
            except OllamaUnavailableError as exc:
                logger.info("Ollama prewarm skipped (unavailable): %s", exc)
        return {
            "tts_prewarm": tts_stats,
            "ollama_prewarmed": ollama_ok,
            "ollama_prewarm_ms": ollama_ms,
        }

    async def start_conversation(
        self,
        *,
        language: RequestedLanguage | None = None,
        speak: bool = True,
        mode: str = "continuous",
        open_hardware_stream: bool = True,
        silence_timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        """Start continuous hands-free VAD conversation loop or report active session."""
        if language is not None:
            self.set_language(language)
        if silence_timeout_sec is not None:
            self._conversation_silence_limit_sec = max(5.0, float(silence_timeout_sec))

        if mode != "continuous":
            self._conversation_mode = mode
            self._conversation_active = False
            await self.ws_hub.broadcast(
                "conversation_mode_change",
                {"active": False, "mode": mode, "language": self.session_language},
            )
            return {
                "active": False,
                "mode": mode,
                "language": self.session_language,
                "state": self.fsm.state.value,
            }

        if open_hardware_stream and not has_input_microphone(self.settings.audio_input_device):
            await self.ws_hub.broadcast(
                "error",
                {"code": "mic_unavailable", "message": "No usable input microphone detected."},
            )
            raise AudioDeviceUnavailableError("No usable input microphone detected on host.")

        if self._conversation_active and self._conversation_task and not self._conversation_task.done():
            return {
                "active": True,
                "mode": "continuous",
                "already_active": True,
                "language": self.session_language,
                "state": self.fsm.state.value,
            }

        self._conversation_active = True
        self._conversation_mode = "continuous"
        self._conversation_speak = speak
        await self.ws_hub.broadcast(
            "conversation_mode_change",
            {"active": True, "mode": "continuous", "language": self.session_language},
        )
        self._conversation_task = asyncio.create_task(
            self._continuous_conversation_loop(open_hardware_stream=open_hardware_stream)
        )
        self._conversation_task.add_done_callback(_log_bg_task_exception)
        return {
            "active": True,
            "mode": "continuous",
            "language": self.session_language,
            "state": self.fsm.state.value,
        }

    async def stop_conversation(self, *, reason: str = "user_stopped") -> dict[str, Any]:
        """Stop continuous hands-free conversation loop cleanly."""
        was_active = self._conversation_active
        self._conversation_active = False
        self._conversation_mode = "idle"
        self.playback.stop()
        if self.audio_capture.is_recording:
            self.audio_capture.stop_capture(cancel=True)
        task = self._conversation_task
        self._conversation_task = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.cancel_turn()
        if was_active:
            await self.ws_hub.broadcast(
                "conversation_mode_change",
                {"active": False, "mode": "idle", "reason": reason},
            )
        return {"stopped": True, "was_active": was_active, "reason": reason}

    async def _continuous_conversation_loop(self, *, open_hardware_stream: bool) -> None:
        idle_silence_sec = 0.0
        listen_window_sec = 8.0
        try:
            while self._conversation_active:
                if self.audio_capture.is_muted or self._turn_lock.locked():
                    await asyncio.sleep(0.05)
                    continue

                if self.fsm.state != AssistantState.IDLE and self.fsm.active_turn:
                    await self.fsm.finish_turn(self.fsm.active_turn.turn_id)

                listen_ctx = await self.fsm.start_turn(
                    source="voice_continuous",
                    language=self._last_resolved_language,
                    initial_state=AssistantState.LISTENING,
                )

                async def _emit_level(
                    rms_db: float,
                    peak: float,
                    *,
                    bound_ctx: TurnContext = listen_ctx,
                ) -> None:
                    if not bound_ctx.is_cancelled and not self.audio_capture.is_muted:
                        await self.ws_hub.broadcast("audio_level", {"rms_db": rms_db, "peak": peak})

                try:
                    await self.audio_capture.start_ptt_capture(
                        use_vad_endpointing=True,
                        initial_timeout_sec=listen_window_sec,
                        on_level=_emit_level,
                        open_hardware_stream=open_hardware_stream,
                    )
                except AudioDeviceUnavailableError as exc:
                    await self._safe_error(listen_ctx, str(exc))
                    break

                if not self._conversation_active or listen_ctx.is_cancelled:
                    self.audio_capture.stop_capture(cancel=True)
                    await self.fsm.finish_turn(listen_ctx.turn_id)
                    continue

                audio_f32, stop_reason = self.audio_capture.get_recorded_audio()
                if stop_reason == "no_speech_timeout" or audio_f32.size == 0:
                    await self.fsm.finish_turn(listen_ctx.turn_id)
                    idle_silence_sec += listen_window_sec
                    if idle_silence_sec >= self._conversation_silence_limit_sec:
                        self._conversation_active = False
                        self._conversation_mode = "idle"
                        await self.ws_hub.broadcast(
                            "conversation_mode_change",
                            {
                                "active": False,
                                "mode": "idle",
                                "reason": "silence_timeout",
                            },
                        )
                        break
                    continue

                idle_silence_sec = 0.0
                async with self._turn_lock:
                    if not self._conversation_active or listen_ctx.is_cancelled:
                        await self.fsm.finish_turn(listen_ctx.turn_id)
                        continue
                    out = await self._process_audio_with_turn(
                        listen_ctx,
                        audio_f32,
                        stop_reason=stop_reason,
                        speak=self._conversation_speak,
                        allow_llm_fallback=True,
                        silent_empty=True,
                    )

                if out.intent in (IntentName.FAREWELL.value, IntentName.STOP_ASSISTANT.value):
                    self._conversation_active = False
                    self._conversation_mode = "idle"
                    await self.ws_hub.broadcast(
                        "conversation_mode_change",
                        {
                            "active": False,
                            "mode": "idle",
                            "reason": "user_farewell",
                        },
                    )
                    break
        except asyncio.CancelledError:
            self.audio_capture.stop_capture(cancel=True)
            raise
        except Exception as exc:
            logger.exception("Continuous conversation loop failed unexpectedly")
            self._conversation_active = False
            self._conversation_mode = "idle"
            self.audio_capture.stop_capture(cancel=True)
            with contextlib.suppress(Exception):
                await self.ws_hub.broadcast(
                    "error",
                    {"code": "conversation_loop_error", "message": str(exc)},
                )
                await self.ws_hub.broadcast(
                    "conversation_mode_change",
                    {"active": False, "mode": "idle", "reason": "runtime_error"},
                )

    async def start_ptt(self, *, language: RequestedLanguage | None = None) -> dict[str, Any]:
        if self.audio_capture.is_muted:
            return {"started": False, "reason": "The microphone is muted."}
        if language is not None:
            self.set_language(language)

        if self.audio_capture.is_recording and not self._conversation_active:
            return {
                "started": True,
                "already_recording": True,
                "turn_id": self._ptt_turn.turn_id if self._ptt_turn else None,
                "state": self.fsm.state.value,
            }

        await self._preempt_idle_listening()
        if self.fsm.active_turn is not None:
            await self.cancel_turn(turn_id=self.fsm.active_turn.turn_id)

        if not has_input_microphone(self.settings.audio_input_device):
            await self.ws_hub.broadcast(
                "error",
                {"code": "mic_unavailable", "message": "No usable input microphone detected."},
            )
            raise AudioDeviceUnavailableError("No usable input microphone detected on host.")

        async with self._turn_lock:
            ctx = await self.fsm.start_turn(
                source="voice_ptt",
                language=self._last_resolved_language,
                initial_state=AssistantState.LISTENING,
            )
            self._ptt_turn = ctx

            async def _emit_level(rms_db: float, peak: float) -> None:
                if not ctx.is_cancelled:
                    await self.ws_hub.broadcast("audio_level", {"rms_db": rms_db, "peak": peak})

            self._ptt_task = asyncio.create_task(self._capture_ptt(ctx, _emit_level))
            self._ptt_task.add_done_callback(_log_bg_task_exception)
            return {
                "started": True,
                "turn_id": ctx.turn_id,
                "state": self.fsm.state.value,
                "language": self.session_language,
            }

    async def _capture_ptt(self, ctx: TurnContext, on_level: AudioLevelCallback) -> None:
        try:
            await self.audio_capture.start_ptt_capture(
                use_vad_endpointing=True, on_level=on_level, open_hardware_stream=True,
            )
        except (AudioDeviceUnavailableError, OSError) as exc:
            code, message = "mic_unavailable", f"Could not open the microphone: {exc}"
        else:
            reason = self.audio_capture.last_capture_reason
            if reason != "no_speech_timeout":
                if self._ptt_turn is ctx and not ctx.is_cancelled and reason in ("speech_end", "max_duration"):
                    await self.ws_hub.broadcast("ptt_capture_ready", {"turn_id": ctx.turn_id, "reason": reason})
                return
            code, message = "no_speech_timeout", "No usable speech was heard. Start listening and try again."

        if self._ptt_turn is ctx and not ctx.is_cancelled:
            self._ptt_turn = None
            self.audio_capture.stop_capture(cancel=True)
            await self.fsm.finish_turn(ctx.turn_id)
            await self.ws_hub.broadcast("error", {"turn_id": ctx.turn_id, "code": code, "message": message})

    async def stop_ptt_and_process(self, *, speak: bool = True) -> ProcessTurnOutput:
        ctx = self._ptt_turn
        self._ptt_turn = None
        audio_f32, stop_reason = self.audio_capture.stop_capture(cancel=False)
        if self._ptt_task is not None:
            try:
                await asyncio.wait_for(self._ptt_task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError, AudioDeviceUnavailableError, OSError):
                self._ptt_task.cancel()
            self._ptt_task = None

        async with self._turn_lock:
            if ctx is None:
                ctx = await self.fsm.start_turn(
                    source="voice_ptt",
                    language=self._last_resolved_language,
                    initial_state=AssistantState.TRANSCRIBING,
                )
                stop_reason = "uploaded"
            return await self._process_audio_with_turn(
                ctx,
                audio_f32,
                stop_reason=stop_reason,
                speak=speak,
            )

    async def process_audio_samples(
        self,
        audio_f32: np.ndarray,
        *,
        source: str = "audio_upload",
        language: RequestedLanguage | None = None,
        recognition_mode: Literal["multilingual", "strict"] | None = None,
        speak: bool = False,
        allow_llm_fallback: bool = True,
    ) -> ProcessTurnOutput:
        await self._preempt_idle_listening()
        async with self._turn_lock:
            ctx = await self.fsm.start_turn(
                source=source,
                language=language if language in ("en", "hi", "kn") else self._last_resolved_language,
                initial_state=AssistantState.TRANSCRIBING,
            )
            return await self._process_audio_with_turn(
                ctx,
                audio_f32,
                stop_reason="uploaded",
                speak=speak,
                allow_llm_fallback=allow_llm_fallback,
                recognition_mode=recognition_mode,
                reply_language_override=language,
            )

    def _invoke_stt(
        self,
        audio_f32: np.ndarray,
        *,
        stt_language: RequestedLanguage,
        preferred_reply_language: RequestedLanguage,
        recognition_mode: Literal["multilingual", "strict"],
        strict_language: bool,
        fallback_language: LanguageCode,
        vad_voiced_ms: float | None,
        noise_floor_rms: float | None,
    ) -> TranscriptionResult:
        kwargs: dict[str, Any] = {
            "language": stt_language,
            "preferred_reply_language": preferred_reply_language,
            "recognition_mode": recognition_mode,
            "strict_language": strict_language,
            "fallback_language": fallback_language,
            "vad_voiced_ms": vad_voiced_ms,
            "noise_floor_rms": noise_floor_rms,
        }
        try:
            sig = inspect.signature(self.stt.transcribe)
            has_varkw = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            if not has_varkw:
                kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
        except (TypeError, ValueError):
            pass
        return self.stt.transcribe(audio_f32, **kwargs)

    def _retry_english_clock(
        self,
        audio_f32: np.ndarray,
        primary: TranscriptionResult,
        *,
        preferred_reply_language: RequestedLanguage,
        vad_voiced_ms: float | None,
        noise_floor_rms: float | None,
    ) -> TranscriptionResult:
        primary_intent, _ = self.fast_router.route(primary.text, requested_language="kn")
        if primary_intent is not None:
            return primary
        candidate = self._invoke_stt(
            audio_f32, stt_language="en", preferred_reply_language=preferred_reply_language,
            recognition_mode="strict", strict_language=True, fallback_language="en",
            vad_voiced_ms=vad_voiced_ms, noise_floor_rms=noise_floor_rms,
        )
        if candidate.quality_ok and not candidate.is_empty and candidate.avg_logprob is not None and candidate.avg_logprob >= -0.8:
            intent, _ = self.fast_router.route(candidate.text, requested_language="en")
            if intent is not None and intent.intent_name in {IntentName.GET_TIME, IntentName.GET_DATE} and len(intent.sub_actions) == 1:
                return candidate.model_copy(update={
                    "recognition_mode": "multilingual",
                    "latency_ms": primary.latency_ms + candidate.latency_ms,
                })
        return primary.model_copy(update={"latency_ms": primary.latency_ms + candidate.latency_ms})

    def _retry_hindi(
        self, samples: np.ndarray, primary: TranscriptionResult, preferred: RequestedLanguage,
        voiced_ms: float | None, noise_floor: float | None,
    ) -> TranscriptionResult:
        directory = self.settings.hindi_specialist_dir
        if directory is None or not all((directory / f).is_file() for f in ("model.int8.onnx", "tokens.txt")):
            return primary
        original, _ = self.fast_router.route(primary.text, requested_language="hi")
        if original is not None:
            return primary
        try:
            candidate = self._invoke_stt(samples, stt_language="hi", preferred_reply_language=preferred,
                recognition_mode="strict", strict_language=True, fallback_language="hi",
                vad_voiced_ms=voiced_ms, noise_floor_rms=noise_floor)
        except (OSError, ValueError, RuntimeError):
            logger.warning("Optional Hindi second pass unavailable", exc_info=True)
            return primary
        metrics = {"latency_ms": primary.latency_ms + candidate.latency_ms}
        if candidate.quality_ok and not candidate.is_empty and candidate.model_size == "indic-conformer-hi":
            interpretation, _ = self.fast_router.route(candidate.text, requested_language="hi")
            if interpretation and len(interpretation.sub_actions) <= 1:
                # Keep the first hypothesis if the second changes a recognizable
                # number, reverses polarity or converts a question into a mutation.
                first = normalize_utterance(primary.text, requested_language="hi").normalized
                second = normalize_utterance(candidate.text, requested_language="hi").normalized
                numbers = re.findall(r"\d+", first)
                conflict = ((numbers and numbers != re.findall(r"\d+", second))
                            or ("बंद" in first and "चालू" in second)
                            or ("चालू" in first and "बंद" in second))
                read_only = interpretation.intent_name in {IntentName.GET_TIME, IntentName.GET_DATE, IntentName.CLARIFY}
                if not conflict and (read_only or "?" not in primary.text):
                    return candidate.model_copy(update={**metrics, "recognition_mode": "multilingual",
                        "language_probability": primary.language_probability})
        return primary.model_copy(update=metrics)

    async def _process_audio_with_turn(
        self,
        ctx: TurnContext,
        audio_f32: np.ndarray,
        *,
        stop_reason: str,
        speak: bool,
        allow_llm_fallback: bool = True,
        silent_empty: bool = False,
        recognition_mode: Literal["multilingual", "strict"] | None = None,
        reply_language_override: RequestedLanguage | None = None,
    ) -> ProcessTurnOutput:
        t_start = time.perf_counter()
        loop = asyncio.get_running_loop()
        timestamps = _empty_stage_timestamps()

        capture_metrics = (
            getattr(self.audio_capture, "last_capture_metrics", None) if ctx.source != "audio_upload" else None
        )
        vad_voiced_ms = getattr(capture_metrics, "voiced_ms", None) if capture_metrics is not None else None
        noise_floor_rms = getattr(capture_metrics, "noise_floor_rms", None) if capture_metrics is not None else None
        speech_end_mono = getattr(capture_metrics, "speech_end_mono", None) if capture_metrics is not None else None
        if speech_end_mono is None and stop_reason != "uploaded":
            speech_end_mono = t_start
        timestamps["speech_end"] = speech_end_mono
        base_mono = speech_end_mono if speech_end_mono is not None else t_start

        rec_mode: Literal["multilingual", "strict"] = recognition_mode or self.stt_recognition_mode
        preferred_reply_lang = reply_language_override or self.session_language
        if rec_mode == "strict" and preferred_reply_lang != "auto":
            stt_lang: RequestedLanguage = preferred_reply_lang
            strict_flag = True
        else:
            stt_lang = self.stt_language
            strict_flag = False

        try:
            if self.fsm.state == AssistantState.LISTENING:
                await self.fsm.transition(ctx.turn_id, AssistantState.TRANSCRIBING)

            timestamps["stt_start"] = time.perf_counter()
            stt_res: TranscriptionResult = await loop.run_in_executor(
                None,
                lambda: self._invoke_stt(
                    audio_f32,
                    stt_language=stt_lang,
                    preferred_reply_language=preferred_reply_lang,
                    recognition_mode=rec_mode,
                    strict_language=strict_flag,
                    fallback_language=self._last_resolved_language,
                    vad_voiced_ms=vad_voiced_ms,
                    noise_floor_rms=noise_floor_rms,
                ),
            )
            if (
                not ctx.is_cancelled and ctx.source == "voice_ptt" and rec_mode == "multilingual"
                and stt_res.detected_language == "kn"
                and stt_res.quality_ok and not stt_res.is_empty
                and stt_res.language_probability is not None and stt_res.language_probability < 0.85
                and audio_f32.size <= 4 * self.settings.audio_sample_rate
            ):
                stt_res = await loop.run_in_executor(
                    None,
                    lambda: self._retry_english_clock(
                        audio_f32, stt_res, preferred_reply_language=preferred_reply_lang,
                        vad_voiced_ms=vad_voiced_ms, noise_floor_rms=noise_floor_rms,
                    ),
                )
            elif (not ctx.is_cancelled and rec_mode == "multilingual" and stt_res.detected_language == "hi"
                  and stt_res.quality_ok and not stt_res.is_empty and (stt_res.language_probability or 0) >= 0.85
                  and stt_res.model_size != "base" and any("\u0900" <= c <= "\u097f" for c in stt_res.text)
                  and all(not c.isalpha() or "\u0900" <= c <= "\u097f" for c in stt_res.text)):
                stt_res = await loop.run_in_executor(None, self._retry_hindi, audio_f32, stt_res,
                                                     preferred_reply_lang, vad_voiced_ms, noise_floor_rms)
            timestamps["stt_completion"] = time.perf_counter()

            if ctx.is_cancelled:
                det_lang: LanguageCode = getattr(stt_res, "detected_language", None) or self._last_resolved_language
                return await self._finalize_cancelled_turn(
                    ctx,
                    t_start,
                    timestamps=timestamps,
                    base_mono=base_mono,
                    detected_language=det_lang,
                    preferred_reply_language=preferred_reply_lang,
                    recognition_mode=getattr(stt_res, "recognition_mode", rec_mode),
                    language_probability=getattr(stt_res, "language_probability", None),
                    transcript_confidence=getattr(stt_res, "transcript_confidence", None),
                    stt_model=getattr(stt_res, "model_size", None),
                    stt_ms=getattr(stt_res, "latency_ms", None),
                    router_ms=None,
                    transcript=getattr(stt_res, "text", None),
                )

            lang_prob = getattr(stt_res, "language_probability", None)
            transcript_conf = getattr(stt_res, "transcript_confidence", None)
            stt_rec_mode = getattr(stt_res, "recognition_mode", rec_mode)
            stt_model = getattr(stt_res, "model_size", None)
            quality_ok = bool(getattr(stt_res, "quality_ok", True))
            rejection_reason = getattr(stt_res, "rejection_reason", None)
            is_empty = bool(stt_res.is_empty or not stt_res.text.strip())

            # R4 Gate: Do NOTHING on silence or background noise
            if rejection_reason in ("silence", "noise"):
                await self.fsm.finish_turn(ctx.turn_id)
                end_mono = time.perf_counter()
                e2e_ms = round((end_mono - t_start) * 1000.0, 2)
                total_turn_ms = round((end_mono - base_mono) * 1000.0, 2)
                reply_lang: LanguageCode = (
                    preferred_reply_lang
                    if preferred_reply_lang != "auto"
                    else stt_res.detected_language
                )
                timings = self._build_timings_dict(
                    timestamps=timestamps,
                    base_mono=base_mono,
                    stt_ms=stt_res.latency_ms,
                    total_turn_duration_ms=total_turn_ms,
                    latency_ms=e2e_ms,
                )
                await self._broadcast_response_completed(
                    turn_id=ctx.turn_id,
                    outcome="success",
                    status="ignored_silence",
                    action_executed=False,
                    intent="NoSpeech",
                    route_category="silence",
                    language=reply_lang,
                    detected_language=stt_res.detected_language,
                    preferred_reply_language=preferred_reply_lang,
                    response_text="",
                    spoken_text="",
                    silent=True,
                    rejection_reason=rejection_reason,
                    timings=timings,
                    stage_timestamps=timestamps,
                )
                return ProcessTurnOutput(
                    turn_id=ctx.turn_id,
                    intent="NoSpeech",
                    route_category="silence",
                    confidence=0.0,
                    intent_source="quality_gate",
                    detected_language=stt_res.detected_language,
                    preferred_reply_language=preferred_reply_lang,
                    recognition_mode=stt_rec_mode,
                    language_probability=lang_prob,
                    transcript_confidence=transcript_conf,
                    stt_model=stt_model,
                    slots={},
                    response_text="",
                    spoken_text="",
                    response_language=reply_lang,
                    action_executed=False,
                    outcome="success",
                    status="ignored_silence",
                    state=AssistantState.IDLE,
                    data={"stop_reason": stop_reason, "rejection_reason": rejection_reason},
                    latency_ms=e2e_ms,
                    stt_ms=stt_res.latency_ms,
                    stage_timestamps=timestamps,
                    total_turn_duration_ms=total_turn_ms,
                    transcript="",
                )

            # R4 Gate: Ask ONE short clarification on unclear speech, invalid script, or empty speech
            if (not quality_ok and rejection_reason in ("unclear_speech", "invalid_script")) or is_empty:
                lang: LanguageCode = (
                    preferred_reply_lang
                    if preferred_reply_lang != "auto"
                    else stt_res.detected_language
                )
                await self.fsm.transition(ctx.turn_id, AssistantState.CLARIFYING)
                msg = get_phrase("empty_speech", lang)
                await self.ws_hub.broadcast(
                    "clarification_required",
                    {
                        "turn_id": ctx.turn_id,
                        "language": lang,
                        "message": msg,
                        "reason": rejection_reason or "empty_speech",
                    },
                )
                should_speak_clarify = speak and (
                    rejection_reason in ("unclear_speech", "invalid_script") or not silent_empty
                )
                speak_res = await self._maybe_speak(
                    ctx,
                    msg,
                    lang,
                    speak=should_speak_clarify,
                    turn_start_perf=base_mono,
                    timestamps=timestamps,
                )
                spoken_text = msg if should_speak_clarify and speak_res["did_speak_attempt"] else ""
                await self.fsm.finish_turn(ctx.turn_id)
                end_mono = time.perf_counter()
                e2e_ms = round((end_mono - t_start) * 1000.0, 2)
                total_turn_ms = round((end_mono - base_mono) * 1000.0, 2)
                timings = self._build_timings_dict(
                    timestamps=timestamps,
                    base_mono=base_mono,
                    stt_ms=stt_res.latency_ms,
                    tts_ms=speak_res["tts_ms"],
                    audio_startup_ms=speak_res["audio_startup_ms"],
                    playback_ms=speak_res["playback_ms"],
                    ttfa_ms=speak_res["ttfa_ms"],
                    first_text_mono=timestamps["stt_completion"],
                    total_turn_duration_ms=total_turn_ms,
                    latency_ms=e2e_ms,
                )
                await self._broadcast_response_completed(
                    turn_id=ctx.turn_id,
                    outcome="clarification",
                    status="clarification_required",
                    action_executed=False,
                    intent="Clarify",
                    route_category="clarification",
                    language=lang,
                    detected_language=stt_res.detected_language,
                    preferred_reply_language=preferred_reply_lang,
                    response_text=msg,
                    spoken_text=spoken_text,
                    rejection_reason=rejection_reason,
                    timings=timings,
                    stage_timestamps=timestamps,
                )
                return ProcessTurnOutput(
                    turn_id=ctx.turn_id,
                    intent="Clarify",
                    route_category="clarification",
                    confidence=0.0,
                    intent_source="clarification",
                    detected_language=stt_res.detected_language,
                    preferred_reply_language=preferred_reply_lang,
                    recognition_mode=stt_rec_mode,
                    language_probability=lang_prob,
                    transcript_confidence=transcript_conf,
                    stt_model=stt_model,
                    slots={},
                    response_text=msg,
                    spoken_text=spoken_text,
                    response_language=lang,
                    action_executed=False,
                    outcome="clarification",
                    status="clarification_required",
                    state=AssistantState.IDLE,
                    data={"stop_reason": stop_reason, "rejection_reason": rejection_reason},
                    latency_ms=e2e_ms,
                    stt_ms=stt_res.latency_ms,
                    tts_ms=speak_res["tts_ms"],
                    audio_startup_ms=speak_res["audio_startup_ms"],
                    playback_ms=speak_res["playback_ms"],
                    ttfa_ms=speak_res["ttfa_ms"],
                    tts_cache_hit=speak_res["tts_cache_hit"],
                    stage_timestamps=timestamps,
                    time_to_first_text_ms=timings["time_to_first_text_ms"],
                    time_to_first_synthesized_audio_ms=timings["time_to_first_synthesized_audio_ms"],
                    time_to_first_audio_output_ms=timings["time_to_first_audio_output_ms"],
                    playback_duration_ms=timings["playback_duration_ms"],
                    total_turn_duration_ms=total_turn_ms,
                    transcript="",
                )

            await self.ws_hub.broadcast(
                "transcript_ready",
                {
                    "turn_id": ctx.turn_id,
                    "transcript": stt_res.text,
                    "language": stt_res.detected_language,
                    "preferred_reply_language": preferred_reply_lang,
                    "recognition_mode": stt_rec_mode,
                    "language_probability": lang_prob,
                    "transcript_confidence": transcript_conf,
                    "model_size": stt_model,
                    "stt_ms": stt_res.latency_ms,
                },
            )
            return await self._run_nlu_and_execute(
                ctx=ctx,
                text=stt_res.text,
                nlu_language="auto",
                preferred_reply_language=preferred_reply_lang,
                detected_input_language=stt_res.detected_language,
                recognition_mode=stt_rec_mode,
                language_probability=lang_prob,
                transcript_confidence=transcript_conf,
                stt_model=stt_model,
                speak=speak,
                allow_llm_fallback=allow_llm_fallback,
                t_start=t_start,
                base_mono=base_mono,
                timestamps=timestamps,
                stt_ms=stt_res.latency_ms,
                transcript=stt_res.text,
            )
        except StaleTurnError:
            return await self._finalize_cancelled_turn(
                ctx,
                t_start,
                timestamps=timestamps,
                base_mono=base_mono,
                detected_language=self._last_resolved_language,
                preferred_reply_language=preferred_reply_lang,
                recognition_mode=rec_mode,
                language_probability=None,
                transcript_confidence=None,
                stt_model=None,
                stt_ms=None,
                router_ms=None,
                transcript=None,
            )
        except ModelNotFoundError as exc:
            await self._safe_error(ctx, str(exc))
            return self._error_output(ctx, str(exc), t_start, timestamps=timestamps, base_mono=base_mono)
        except Exception as exc:
            logger.exception("Unexpected error processing audio turn %s", ctx.turn_id)
            await self._safe_error(ctx, str(exc))
            return self._error_output(ctx, str(exc), t_start, timestamps=timestamps, base_mono=base_mono)

    async def process_text(
        self,
        text: str,
        *,
        language: RequestedLanguage | None = None,
        source: str = "text_input",
        speak: bool = False,
        allow_llm_fallback: bool = True,
    ) -> ProcessTurnOutput:
        await self._preempt_idle_listening()
        async with self._turn_lock:
            t_start = time.perf_counter()
            timestamps = _empty_stage_timestamps()
            pref_lang = language if language is not None else self.session_language
            fallback_lang: LanguageCode = (
                pref_lang if pref_lang != "auto" else self._last_resolved_language
            )
            ctx = await self.fsm.start_turn(
                source=source,
                language=fallback_lang,
                initial_state=AssistantState.UNDERSTANDING,
            )
            try:
                return await self._run_nlu_and_execute(
                    ctx=ctx,
                    text=text,
                    nlu_language="auto",
                    preferred_reply_language=pref_lang,
                    detected_input_language=None,
                    recognition_mode=self.stt_recognition_mode,
                    language_probability=None,
                    transcript_confidence=None,
                    stt_model=None,
                    speak=speak,
                    allow_llm_fallback=allow_llm_fallback,
                    t_start=t_start,
                    base_mono=t_start,
                    timestamps=timestamps,
                    stt_ms=None,
                    transcript=text,
                )
            except StaleTurnError:
                return await self._finalize_cancelled_turn(
                    ctx,
                    t_start,
                    timestamps=timestamps,
                    base_mono=t_start,
                    detected_language=fallback_lang,
                    preferred_reply_language=pref_lang,
                    recognition_mode=self.stt_recognition_mode,
                    language_probability=None,
                    transcript_confidence=None,
                    stt_model=None,
                    stt_ms=None,
                    router_ms=None,
                    transcript=text,
                )
            except Exception as exc:
                logger.exception("Unexpected error processing text turn %s", ctx.turn_id)
                await self._safe_error(ctx, str(exc))
                return self._error_output(ctx, str(exc), t_start, timestamps=timestamps, base_mono=t_start)

    async def _run_nlu_and_execute(
        self,
        *,
        ctx: TurnContext,
        text: str,
        nlu_language: RequestedLanguage,
        preferred_reply_language: RequestedLanguage,
        detected_input_language: LanguageCode | None,
        recognition_mode: str,
        language_probability: float | None,
        transcript_confidence: float | None,
        stt_model: str | None,
        speak: bool,
        allow_llm_fallback: bool,
        t_start: float,
        base_mono: float,
        timestamps: dict[str, float | None],
        stt_ms: float | None,
        transcript: str | None,
    ) -> ProcessTurnOutput:
        if self.fsm.state != AssistantState.UNDERSTANDING:
            await self.fsm.transition(ctx.turn_id, AssistantState.UNDERSTANDING)

        fallback_nlu: LanguageCode = (
            detected_input_language
            or (preferred_reply_language if preferred_reply_language != "auto" else self._last_resolved_language)
        )
        resolved_text = self.conversation.resolve_anaphora(text)
        typed_intent, router_ms, llm_ms, llm_err = await self.planner.plan(
            resolved_text,
            requested_language=nlu_language,
            fallback_language=fallback_nlu,
            allow_llm_fallback=allow_llm_fallback,
        )
        timestamps["routing_completion"] = time.perf_counter()

        detected_lang: LanguageCode = detected_input_language or typed_intent.language
        if typed_intent.target_language is not None:
            reply_language: LanguageCode = typed_intent.target_language
        elif preferred_reply_language != "auto":
            reply_language = preferred_reply_language
        else:
            reply_language = detected_lang
        self._last_resolved_language = reply_language
        ctx.language = reply_language

        if ctx.is_cancelled:
            return await self._finalize_cancelled_turn(
                ctx,
                t_start,
                timestamps=timestamps,
                base_mono=base_mono,
                detected_language=detected_lang,
                preferred_reply_language=preferred_reply_language,
                recognition_mode=recognition_mode,
                language_probability=language_probability,
                transcript_confidence=transcript_confidence,
                stt_model=stt_model,
                stt_ms=stt_ms,
                router_ms=router_ms,
                transcript=transcript,
            )

        tool_t0 = time.perf_counter()
        llm_ttft_ms: float | None = None
        tts_ms: float | None = None
        audio_startup_ms: float | None = None
        playback_ms: float | None = None
        ttfa_ms: float | None = None
        tts_cache_hit = False
        already_spoke = False
        spoken_text = ""
        first_text_mono: float | None = timestamps["routing_completion"]
        first_audio_ready_mono: float | None = None

        if typed_intent.intent_name == IntentName.CLARIFY:
            await self.fsm.transition(ctx.turn_id, AssistantState.CLARIFYING)
            result = await self.tool_registry.execute_intent(
                typed_intent, reply_language=reply_language
            )
            timestamps["tool_completion"] = time.perf_counter()
            first_text_mono = timestamps["tool_completion"]
            await self.ws_hub.broadcast(
                "clarification_required",
                {"turn_id": ctx.turn_id, "language": reply_language, "message": result.message},
            )
        elif typed_intent.intent_name in (
            IntentName.GREETING,
            IntentName.ACKNOWLEDGMENT,
            IntentName.FAREWELL,
            IntentName.IDENTITY,
            IntentName.SUPPORTED_LANGUAGES,
        ):
            phrase_key = (
                "supported_languages"
                if typed_intent.intent_name == IntentName.SUPPORTED_LANGUAGES
                else typed_intent.intent_name.value.lower()
            )
            result = VerifiedActionResult(
                success=True,
                action=typed_intent.intent_name.value,
                status="verified",
                message=get_phrase(phrase_key, reply_language),
            )
        elif typed_intent.intent_name == IntentName.SWITCH_LANGUAGE:
            self.set_language(reply_language)
            result = VerifiedActionResult(
                success=True,
                action="SwitchLanguage",
                status="verified",
                data={"language": reply_language},
                message=get_phrase("language_switched", reply_language),
            )
        elif typed_intent.intent_name == IntentName.CONVERSATION:
            (
                result,
                llm_ttft_ms,
                llm_ms,
                speak_res,
                already_spoke,
                spoken_text,
            ) = await self._execute_streaming_conversation(
                ctx=ctx,
                user_text=text,
                reply_language=reply_language,
                speak=speak,
                base_mono=base_mono,
                timestamps=timestamps,
            )
            tts_ms = speak_res["tts_ms"]
            audio_startup_ms = speak_res["audio_startup_ms"]
            playback_ms = speak_res["playback_ms"]
            ttfa_ms = speak_res["ttfa_ms"]
            tts_cache_hit = speak_res["tts_cache_hit"]
            first_audio_ready_mono = speak_res["first_audio_ready_mono"]
            first_text_mono = timestamps["validated_response_text"] or time.perf_counter()
        elif typed_intent.intent_name == IntentName.UNSUPPORTED:
            result = VerifiedActionResult(
                success=False,
                action="Unsupported",
                status="failed" if llm_err else "refused",
                message=(
                    get_phrase("ollama_unavailable", reply_language)
                    if llm_err
                    else get_phrase("unsupported", reply_language)
                ),
                error=llm_err,
            )
        else:
            await self.fsm.transition(ctx.turn_id, AssistantState.EXECUTING)
            result = await self.tool_registry.execute_intent(
                typed_intent, reply_language=reply_language
            )
            timestamps["tool_completion"] = time.perf_counter()
            first_text_mono = timestamps["tool_completion"]
            if result.action == "CompoundAction":
                for step, completed in zip(typed_intent.sub_actions, result.data["sub_results"]):
                    if completed["success"]:
                        ctx.record_completed_action(completed["action"])
                        self.conversation.record_verified_entity(action=completed["action"], slots=step.slots,
                                                                data=completed["data"])
                        if completed["action"] == "DeleteMemory":
                            self.conversation.invalidate_memory_context()
            elif result.success:
                ctx.record_completed_action(result.action)
                self.conversation.record_verified_entity(action=result.action, slots=typed_intent.slots, data=result.data)
                if result.action == "DeleteMemory":
                    self.conversation.invalidate_memory_context()
            elif result.action == "RoutineControl":
                for step in result.data.get("results", []):
                    if step["status"] == "verified":
                        ctx.record_completed_action(step["device_id"])
            await self.fsm.transition(ctx.turn_id, AssistantState.VERIFYING)
            await self.ws_hub.broadcast(
                "action_verified",
                {
                    "turn_id": ctx.turn_id,
                    "action": result.action,
                    "status": result.status,
                    "success": result.success,
                    "data": result.data,
                },
            )
            if result.status == "clarification_required":
                await self.fsm.transition(ctx.turn_id, AssistantState.CLARIFYING)
                await self.ws_hub.broadcast(
                    "clarification_required",
                    {
                        "turn_id": ctx.turn_id,
                        "language": reply_language,
                        "message": result.message,
                    },
                )

        tool_ms = (
            round((time.perf_counter() - tool_t0) * 1000.0, 2)
            if typed_intent.route_category == "tool"
            else 0.0
        )

        if ctx.is_cancelled:
            return await self._finalize_cancelled_turn(
                ctx,
                t_start,
                timestamps=timestamps,
                base_mono=base_mono,
                detected_language=detected_lang,
                preferred_reply_language=preferred_reply_language,
                recognition_mode=recognition_mode,
                language_probability=language_probability,
                transcript_confidence=transcript_confidence,
                stt_model=stt_model,
                stt_ms=stt_ms,
                router_ms=router_ms,
                transcript=transcript,
            )

        if typed_intent.intent_name == IntentName.STOP_ASSISTANT:
            self.playback.stop()

        if not already_spoke:
            speak_res = await self._maybe_speak(
                ctx,
                result.message,
                reply_language,
                speak=speak,
                turn_start_perf=base_mono,
                timestamps=timestamps,
            )
            tts_ms = speak_res["tts_ms"]
            audio_startup_ms = speak_res["audio_startup_ms"]
            playback_ms = speak_res["playback_ms"]
            ttfa_ms = speak_res["ttfa_ms"]
            tts_cache_hit = speak_res["tts_cache_hit"]
            first_audio_ready_mono = speak_res["first_audio_ready_mono"]
            if speak and speak_res["did_speak_attempt"] and result.message.strip():
                spoken_text = result.message

        if ctx.is_cancelled:
            return await self._finalize_cancelled_turn(
                ctx,
                t_start,
                timestamps=timestamps,
                base_mono=base_mono,
                detected_language=detected_lang,
                preferred_reply_language=preferred_reply_language,
                recognition_mode=recognition_mode,
                language_probability=language_probability,
                transcript_confidence=transcript_confidence,
                stt_model=stt_model,
                stt_ms=stt_ms,
                router_ms=router_ms,
                transcript=transcript,
            )

        await self.fsm.finish_turn(ctx.turn_id)

        action_executed = bool(typed_intent.route_category == "tool" and (result.success or ctx.completed_actions))
        if result.status == "clarification_required" or typed_intent.intent_name == IntentName.CLARIFY:
            outcome: Literal["success", "clarification", "refusal", "cancelled", "runtime_failure"] = "clarification"
        elif result.status == "refused":
            outcome = "refusal"
        elif result.status == "failed":
            outcome = "runtime_failure"
        else:
            outcome = "success"

        end_mono = time.perf_counter()
        e2e_ms = round((end_mono - t_start) * 1000.0, 2)
        total_turn_ms = round((end_mono - base_mono) * 1000.0, 2)
        timings = self._build_timings_dict(
            timestamps=timestamps,
            base_mono=base_mono,
            stt_ms=stt_ms,
            router_ms=router_ms,
            llm_ttft_ms=llm_ttft_ms,
            llm_ms=llm_ms,
            tool_ms=tool_ms,
            tts_ms=tts_ms,
            audio_startup_ms=audio_startup_ms,
            playback_ms=playback_ms,
            ttfa_ms=ttfa_ms,
            first_text_mono=first_text_mono,
            first_audio_ready_mono=first_audio_ready_mono,
            total_turn_duration_ms=total_turn_ms,
            latency_ms=e2e_ms,
        )

        await self._broadcast_response_completed(
            turn_id=ctx.turn_id,
            outcome=outcome,
            status=result.status,
            action_executed=action_executed,
            intent=typed_intent.intent_name.value,
            route_category=typed_intent.route_category,
            language=reply_language,
            detected_language=detected_lang,
            preferred_reply_language=preferred_reply_language,
            response_text=result.message,
            spoken_text=spoken_text,
            timings=timings,
            stage_timestamps=timestamps,
        )

        # Commit turn atomically to session memory only when not cancelled or failed due to offline LLM
        if result.error is None and result.message.strip():
            self.conversation.record_turn(
                user_text=text,
                assistant_text=result.message,
                language=reply_language,
                intent=typed_intent.intent_name.value,
                route_category=typed_intent.route_category,
                turn_id=ctx.turn_id,
            )
            if self.memory_service is not None:
                try:
                    self.memory_service.maybe_persist_conversation_turn(
                        turn_id=ctx.turn_id,
                        user_text=text,
                        assistant_text=result.message,
                        language=reply_language,
                        intent=typed_intent.intent_name.value,
                    )
                except (SQLAlchemyError, OSError, RuntimeError) as exc:
                    logger.debug("Skipping persistent conversation log: %s", exc)
            await self.ws_hub.broadcast(
                "conversation_history_updated",
                {"count": len(self.conversation.get_history()), "turn_id": ctx.turn_id},
            )

        placement = self.compute_manager.resolve_placement()
        try:
            self.metric_repo.record_turn(
                turn_id=ctx.turn_id,
                source=ctx.source,
                language=reply_language,
                requested_compute_mode=placement.requested_mode.value,
                effective_compute_mode=placement.effective_mode,
                intent_name=typed_intent.intent_name.value,
                intent_source=typed_intent.source,
                final_state=AssistantState.IDLE.value,
                stt_ms=stt_ms,
                router_ms=router_ms,
                llm_ms=llm_ms,
                tool_ms=tool_ms,
                tts_ms=tts_ms,
                ttfa_ms=ttfa_ms,
                e2e_ms=e2e_ms,
            )
        except (SQLAlchemyError, OSError, RuntimeError) as exc:
            logger.debug("Skipping metric DB write (DB unavailable): %s", exc)

        return ProcessTurnOutput(
            turn_id=ctx.turn_id,
            intent=typed_intent.intent_name.value,
            route_category=typed_intent.route_category,
            confidence=typed_intent.confidence,
            intent_source=typed_intent.source,
            detected_language=detected_lang,
            preferred_reply_language=preferred_reply_language,
            recognition_mode=recognition_mode,
            language_probability=language_probability,
            transcript_confidence=transcript_confidence,
            stt_model=stt_model,
            slots=typed_intent.slots,
            response_text=result.message,
            spoken_text=spoken_text,
            response_language=reply_language,
            action_executed=action_executed,
            outcome=outcome,
            status=result.status,
            state=self.fsm.state,
            data=result.data,
            latency_ms=e2e_ms,
            stt_ms=stt_ms,
            router_ms=router_ms,
            llm_ttft_ms=llm_ttft_ms,
            llm_ms=llm_ms,
            tool_ms=tool_ms,
            tts_ms=tts_ms,
            audio_startup_ms=audio_startup_ms,
            playback_ms=playback_ms,
            ttfa_ms=ttfa_ms,
            tts_cache_hit=tts_cache_hit,
            stage_timestamps=timestamps,
            time_to_first_text_ms=timings["time_to_first_text_ms"],
            time_to_first_synthesized_audio_ms=timings["time_to_first_synthesized_audio_ms"],
            time_to_first_audio_output_ms=timings["time_to_first_audio_output_ms"],
            playback_duration_ms=timings["playback_duration_ms"],
            total_turn_duration_ms=total_turn_ms,
            error=result.error,
            transcript=transcript,
        )

    async def _execute_streaming_conversation(
        self,
        *,
        ctx: TurnContext,
        user_text: str,
        reply_language: LanguageCode,
        speak: bool,
        base_mono: float,
        timestamps: dict[str, float | None],
    ) -> tuple[
        VerifiedActionResult,
        float | None,
        float | None,
        dict[str, Any],
        bool,
        str,
    ]:
        """Stream answer text while assembling validated speech for continuous playback."""
        empty_speak_res: dict[str, Any] = {
            "tts_ms": None,
            "audio_startup_ms": None,
            "playback_ms": None,
            "ttfa_ms": None,
            "tts_cache_hit": False,
            "first_audio_ready_mono": None,
            "did_speak_attempt": False,
        }
        caps = self.tool_registry.describe_capabilities(reply_language)
        mem_context = ""
        if self.memory_service is not None:
            try:
                mem_context = await self.memory_service.build_untrusted_prompt_context(user_text)
            except (SQLAlchemyError, OSError, RuntimeError, ValueError) as exc:
                logger.debug("Skipping memory context lookup: %s", exc)
        messages = self.conversation.build_llm_messages(
            user_text,
            reply_language=reply_language,
            capability_summary=caps,
            memory_context=mem_context,
        )
        want_detail = re.search(
            r"\b(?:continue|details?|in depth|elaborate)\b|विस्तार|ವಿವರ",
            user_text,
            flags=re.IGNORECASE,
        )
        token_budget = 220 if want_detail else 120
        sentence_buf = SentenceStreamBuffer(min_chars=24, split_long_phrases=False)
        spoken_parts: list[str] = []
        skipped_invalid_chunk = False

        def _collect_validated_sentence(chunk_text: str) -> None:
            nonlocal skipped_invalid_chunk
            clean_chunk = strip_markdown_for_speech(chunk_text)
            if not clean_chunk:
                return
            valid_chunk, reason = validate_response_language(clean_chunk, reply_language)
            if valid_chunk:
                valid_chunk, reason = validate_no_unverified_action_claim(clean_chunk, reply_language)
            if not valid_chunk:
                skipped_invalid_chunk = True
                logger.info(
                    "Conversation sentence rejected before TTS (%s, target=%s)",
                    reason,
                    reply_language,
                )
                return
            if timestamps["first_valid_speech_chunk"] is None:
                timestamps["first_valid_speech_chunk"] = time.perf_counter()
            spoken_parts.append(clean_chunk)

        raw_parts: list[str] = []
        llm_stats: dict[str, Any] = {}
        try:
            async for token, stats in self.ollama_client.stream_conversation(
                messages,
                num_predict=token_budget,
                cancel_event=ctx.cancel_event,
            ):
                if stats is not None:
                    llm_stats = stats
                    break
                if token:
                    if timestamps["llm_first_token"] is None:
                        timestamps["llm_first_token"] = time.perf_counter()
                    raw_parts.append(token)
                    if speak:
                        for sentence in sentence_buf.push(token):
                            _collect_validated_sentence(sentence)

            tail = sentence_buf.flush()
            if speak and tail and not ctx.is_cancelled:
                _collect_validated_sentence(tail)
        except OllamaUnavailableError as exc:
            msg = get_phrase("ollama_unavailable", reply_language)
            return (
                VerifiedActionResult(
                    success=False,
                    action="Conversation",
                    status="failed",
                    message=msg,
                    error=str(exc),
                ),
                None,
                None,
                empty_speak_res,
                False,
                "",
            )

        clean_full = strip_markdown_for_speech("".join(raw_parts)) or "..."
        is_valid, reason = validate_response_language(clean_full, reply_language)
        if is_valid:
            is_valid, reason = validate_no_unverified_action_claim(clean_full, reply_language)
        speech = " ".join(spoken_parts)
        if speech:
            valid_speech, speech_reason = validate_no_unverified_action_claim(speech, reply_language)
            if not valid_speech:
                speech = ""
                is_valid, reason = False, speech_reason
            elif skipped_invalid_chunk or not is_valid:
                clean_full = speech
                is_valid = True
        if not is_valid and not ctx.is_cancelled:
            logger.info("Response validation failed (%s); applying safe %s guardrail.", reason, reply_language)
            if reason == "unverified_action_claim":
                clean_full = unverified_action_fallback(reply_language)
            elif reason == "unsupported_language_claim":
                clean_full = get_phrase("supported_languages", reply_language)
            elif reply_language == "kn":
                clean_full = "ಕ್ಷಮಿಸಿ, ನನಗೆ ಈ ಪ್ರಶ್ನೆಗೆ ಸ್ಪಷ್ಟ ಕನ್ನಡದಲ್ಲಿ ಉತ್ತರಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ."
            elif reply_language == "hi":
                clean_full = "माफ कीजिए, मैं इस सवाल का स्पष्ट हिंदी में उत्तर नहीं दे पाया।"
            elif reply_language == "en":
                clean_full = get_phrase("supported_languages", "en")

        # The dashboard and screen readers must receive the same validated answer as TTS.
        if not ctx.is_cancelled:
            timestamps["validated_response_text"] = time.perf_counter()
            await self.ws_hub.broadcast(
                "response_text_delta",
                {"turn_id": ctx.turn_id, "token": clean_full, "language": reply_language},
            )

        speak_res = dict(empty_speak_res)
        already_spoke = False
        spoken_text = ""

        if speech and not ctx.is_cancelled:
            if not self.audio_capture.is_suspended:
                self.audio_capture.suspend_for_playback()
            await self.fsm.transition(ctx.turn_id, AssistantState.SPEAKING, language=reply_language)
            sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
            # Kannada MMS has no punctuation tokens. Keep sentence breaks as audio pauses.
            for segment in spoken_parts if reply_language == "kn" else (speech,):
                sentence_queue.put_nowait(segment)
            sentence_queue.put_nowait(None)
            try:
                _audios, pm = await self.playback.speak_sentence_stream(
                    sentence_queue, language=reply_language, play_to_speaker=True,
                    external_cancel=ctx.cancel_event, turn_start_perf=base_mono,
                )
                speak_res["tts_ms"] = pm.tts_synthesis_ms
                speak_res["audio_startup_ms"] = pm.audio_startup_ms
                speak_res["playback_ms"] = pm.audio_playback_ms
                speak_res["ttfa_ms"] = pm.first_audio_ready_ms
                speak_res["tts_cache_hit"] = pm.cache_hits > 0
                speak_res["first_audio_ready_mono"] = getattr(pm, "first_audio_ready_mono", None)
                speak_res["did_speak_attempt"] = True
                timestamps["tts_completion"] = getattr(pm, "tts_completed_mono", None) or (
                    time.perf_counter() if pm.tts_synthesis_ms > 0.0 else None
                )
                timestamps["first_non_silent_output_callback"] = getattr(
                    pm, "first_non_silent_callback_mono", None
                )
                timestamps["playback_completion"] = getattr(pm, "playback_completed_mono", None) or (
                    time.perf_counter() if pm.audio_playback_ms > 0.0 else None
                )
                already_spoke = True
                if not ctx.is_cancelled and pm.audio_startup_ms is not None:
                    spoken_text = speech
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("Streaming TTS playback failed: %s", exc)
            finally:
                did_play = bool(speak_res["playback_ms"] and speak_res["playback_ms"] > 0.0) and not ctx.is_cancelled
                await self._resume_mic_after_speaking(did_speak=did_play)
        elif speak and not ctx.is_cancelled:
            speak_res = await self._maybe_speak(
                ctx,
                clean_full,
                reply_language,
                speak=True,
                turn_start_perf=base_mono,
                timestamps=timestamps,
            )
            already_spoke = True
            if speak_res["did_speak_attempt"]:
                spoken_text = clean_full

        return (
            VerifiedActionResult(
                success=True,
                action="Conversation",
                status="verified",
                message=clean_full,
            ),
            llm_stats.get("ttft_ms"),
            llm_stats.get("latency_ms"),
            speak_res,
            already_spoke,
            spoken_text,
        )

    async def _maybe_speak(
        self,
        ctx: TurnContext,
        text: str,
        language: LanguageCode,
        *,
        speak: bool,
        turn_start_perf: float | None = None,
        timestamps: dict[str, float | None] | None = None,
    ) -> dict[str, Any]:
        empty: dict[str, Any] = {
            "tts_ms": None,
            "audio_startup_ms": None,
            "playback_ms": None,
            "ttfa_ms": None,
            "tts_cache_hit": False,
            "first_audio_ready_mono": None,
            "did_speak_attempt": False,
        }
        if not speak or not text.strip() or ctx.is_cancelled:
            return empty
        if not self.audio_capture.is_suspended:
            self.audio_capture.suspend_for_playback()
        if self.fsm.state != AssistantState.SPEAKING:
            await self.fsm.transition(ctx.turn_id, AssistantState.SPEAKING, language=language)
        if timestamps is not None and timestamps["first_valid_speech_chunk"] is None:
            timestamps["first_valid_speech_chunk"] = time.perf_counter()
        did_play = False
        try:
            _chunks, pm = await self.playback.speak_text_with_metrics(
                text,
                language=language,
                play_to_speaker=True,
                external_cancel=ctx.cancel_event,
                turn_start_perf=turn_start_perf,
            )
            did_play = pm.audio_playback_ms > 0.0
            if timestamps is not None:
                timestamps["tts_completion"] = getattr(pm, "tts_completed_mono", None) or (
                    time.perf_counter() if pm.tts_synthesis_ms > 0.0 else None
                )
                timestamps["first_non_silent_output_callback"] = getattr(
                    pm, "first_non_silent_callback_mono", None
                )
                timestamps["playback_completion"] = getattr(pm, "playback_completed_mono", None) or (
                    time.perf_counter() if pm.audio_playback_ms > 0.0 else None
                )
            return {
                "tts_ms": pm.tts_synthesis_ms,
                "audio_startup_ms": pm.audio_startup_ms,
                "playback_ms": pm.audio_playback_ms,
                "ttfa_ms": pm.first_audio_ready_ms,
                "tts_cache_hit": pm.cache_hits > 0,
                "first_audio_ready_mono": getattr(pm, "first_audio_ready_mono", None),
                "did_speak_attempt": True,
            }
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("TTS speaking skipped/failed: %s", exc)
            return empty
        finally:
            await self._resume_mic_after_speaking(did_speak=did_play and not ctx.is_cancelled)

    def _build_timings_dict(
        self,
        *,
        timestamps: dict[str, float | None],
        base_mono: float,
        stt_ms: float | None = None,
        router_ms: float | None = None,
        llm_ttft_ms: float | None = None,
        llm_ms: float | None = None,
        tool_ms: float | None = None,
        tts_ms: float | None = None,
        audio_startup_ms: float | None = None,
        playback_ms: float | None = None,
        ttfa_ms: float | None = None,
        first_text_mono: float | None = None,
        first_audio_ready_mono: float | None = None,
        total_turn_duration_ms: float | None = None,
        latency_ms: float | None = None,
    ) -> dict[str, float | None]:
        time_to_first_text_ms = (
            round(max(0.0, (first_text_mono - base_mono) * 1000.0), 2)
            if first_text_mono is not None
            else None
        )
        time_to_first_synth_ms = (
            round(max(0.0, (first_audio_ready_mono - base_mono) * 1000.0), 2)
            if first_audio_ready_mono is not None
            else ttfa_ms
        )
        cb_mono = timestamps.get("first_non_silent_output_callback")
        time_to_first_output_ms = (
            round(max(0.0, (cb_mono - base_mono) * 1000.0), 2)
            if cb_mono is not None
            else audio_startup_ms
        )
        return {
            "stt_ms": stt_ms,
            "router_ms": router_ms,
            "llm_ttft_ms": llm_ttft_ms,
            "llm_ms": llm_ms,
            "tool_ms": tool_ms,
            "tts_ms": tts_ms,
            "audio_startup_ms": audio_startup_ms,
            "playback_ms": playback_ms,
            "ttfa_ms": ttfa_ms,
            "time_to_first_text_ms": time_to_first_text_ms,
            "time_to_first_synthesized_audio_ms": time_to_first_synth_ms,
            "time_to_first_audio_output_ms": time_to_first_output_ms,
            "playback_duration_ms": playback_ms,
            "total_turn_duration_ms": total_turn_duration_ms,
            "latency_ms": latency_ms,
        }

    async def _broadcast_response_completed(
        self,
        *,
        turn_id: str,
        outcome: Literal["success", "clarification", "refusal", "cancelled", "runtime_failure"],
        status: str,
        action_executed: bool,
        intent: str,
        route_category: str,
        language: LanguageCode,
        detected_language: LanguageCode,
        preferred_reply_language: RequestedLanguage,
        response_text: str,
        spoken_text: str,
        timings: dict[str, float | None],
        stage_timestamps: dict[str, float | None],
        silent: bool = False,
        rejection_reason: str | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "turn_id": turn_id,
            "outcome": outcome,
            "status": status,
            "action_executed": action_executed,
            "intent": intent,
            "route_category": route_category,
            "language": language,
            "detected_language": detected_language,
            "preferred_reply_language": preferred_reply_language,
            "response_text": response_text,
            "spoken_text": spoken_text,
            "silent": silent,
            "timings": timings,
            "stage_timestamps": dict(stage_timestamps),
            # Preserve top-level timing keys for existing CLI/WS consumers
            "llm_ttft_ms": timings.get("llm_ttft_ms"),
            "tts_ms": timings.get("tts_ms"),
            "audio_startup_ms": timings.get("audio_startup_ms"),
            "playback_ms": timings.get("playback_ms"),
            "ttfa_ms": timings.get("ttfa_ms"),
        }
        if rejection_reason is not None:
            payload["rejection_reason"] = rejection_reason
        if error is not None:
            payload["error"] = error
        if turn_id in self._completed_turn_ids:
            return
        self._completed_turn_ids.append(turn_id)
        await self.ws_hub.broadcast("response_completed", payload)

    async def _safe_error(self, ctx: TurnContext, message: str) -> None:
        was_speaking = self.playback.is_playing or self.audio_capture.is_suspended
        if was_speaking:
            self.playback.stop()
        try:
            await self.fsm.transition(ctx.turn_id, AssistantState.ERROR, message=message)
        except (StaleTurnError, RuntimeError) as exc:
            logger.debug("Skipping ERROR state transition: %s", exc)
        await self.ws_hub.broadcast("error", {"turn_id": ctx.turn_id, "message": message})
        await self.fsm.finish_turn(ctx.turn_id)
        if was_speaking:
            await self._resume_mic_after_speaking(did_speak=True)

    def _error_output(
        self,
        ctx: TurnContext,
        message: str,
        t_start: float,
        *,
        timestamps: dict[str, float | None] | None = None,
        base_mono: float | None = None,
    ) -> ProcessTurnOutput:
        ts = timestamps or _empty_stage_timestamps()
        end_mono = time.perf_counter()
        anchor = base_mono if base_mono is not None else t_start
        return ProcessTurnOutput(
            turn_id=ctx.turn_id,
            intent="Error",
            route_category="error",
            confidence=0.0,
            intent_source="error",
            detected_language=self._last_resolved_language,
            preferred_reply_language=self.session_language,
            recognition_mode=self.stt_recognition_mode,
            slots={},
            response_text=message,
            spoken_text="",
            response_language=self._last_resolved_language,
            action_executed=False,
            outcome="runtime_failure",
            status="failed",
            state=self.fsm.state,
            data={},
            latency_ms=round((end_mono - t_start) * 1000.0, 2),
            stage_timestamps=ts,
            total_turn_duration_ms=round((end_mono - anchor) * 1000.0, 2),
            error=message,
        )

    async def _finalize_cancelled_turn(
        self,
        ctx: TurnContext,
        t_start: float,
        *,
        timestamps: dict[str, float | None],
        base_mono: float,
        detected_language: LanguageCode,
        preferred_reply_language: RequestedLanguage,
        recognition_mode: str,
        language_probability: float | None,
        transcript_confidence: float | None,
        stt_model: str | None,
        stt_ms: float | None,
        router_ms: float | None,
        transcript: str | None,
    ) -> ProcessTurnOutput:
        out = self._cancelled_output(
            ctx,
            t_start,
            timestamps=timestamps,
            base_mono=base_mono,
            detected_language=detected_language,
            preferred_reply_language=preferred_reply_language,
            recognition_mode=recognition_mode,
            language_probability=language_probability,
            transcript_confidence=transcript_confidence,
            stt_model=stt_model,
            stt_ms=stt_ms,
            router_ms=router_ms,
            transcript=transcript,
        )
        timings = self._build_timings_dict(
            timestamps=timestamps,
            base_mono=base_mono,
            stt_ms=stt_ms,
            router_ms=router_ms,
            total_turn_duration_ms=out.total_turn_duration_ms,
            latency_ms=out.latency_ms,
        )
        await self._broadcast_response_completed(
            turn_id=ctx.turn_id,
            outcome="cancelled",
            status="cancelled",
            action_executed=out.action_executed,
            intent="Cancelled",
            route_category="cancellation",
            language=out.response_language,
            detected_language=detected_language,
            preferred_reply_language=preferred_reply_language,
            response_text=out.response_text,
            spoken_text="",
            timings=timings,
            stage_timestamps=timestamps,
        )
        return out

    def _cancelled_output(
        self,
        ctx: TurnContext,
        t_start: float,
        *,
        timestamps: dict[str, float | None] | None = None,
        base_mono: float | None = None,
        detected_language: LanguageCode | None = None,
        preferred_reply_language: RequestedLanguage | None = None,
        recognition_mode: str | None = None,
        language_probability: float | None = None,
        transcript_confidence: float | None = None,
        stt_model: str | None = None,
        stt_ms: float | None = None,
        router_ms: float | None = None,
        transcript: str | None = None,
    ) -> ProcessTurnOutput:
        ts = timestamps or _empty_stage_timestamps()
        end_mono = time.perf_counter()
        anchor = base_mono if base_mono is not None else t_start
        det_lang = detected_language or self._last_resolved_language
        return ProcessTurnOutput(
            turn_id=ctx.turn_id,
            intent="Cancelled",
            route_category="cancellation",
            confidence=0.0,
            intent_source="cancellation",
            detected_language=det_lang,
            preferred_reply_language=preferred_reply_language or self.session_language,
            recognition_mode=recognition_mode or self.stt_recognition_mode,
            language_probability=language_probability,
            transcript_confidence=transcript_confidence,
            stt_model=stt_model,
            slots={},
            response_text="Cancelled.",
            spoken_text="",
            response_language=self._last_resolved_language,
            action_executed=bool(ctx.completed_actions),
            outcome="cancelled",
            status="cancelled",
            state=self.fsm.state,
            data={"completed_actions_preserved": list(ctx.completed_actions)},
            latency_ms=round((end_mono - t_start) * 1000.0, 2),
            stt_ms=stt_ms,
            router_ms=router_ms,
            stage_timestamps=ts,
            total_turn_duration_ms=round((end_mono - anchor) * 1000.0, 2),
            transcript=transcript,
        )

    async def cancel_turn(self, turn_id: str | None = None) -> dict[str, Any]:
        was_speaking = self.playback.is_playing or self.audio_capture.is_suspended
        self.playback.stop()
        if self.audio_capture.is_recording:
            self.audio_capture.stop_capture(cancel=True)
        if self._ptt_task is not None and not self._ptt_task.done():
            self._ptt_task.cancel()
        res = await self.fsm.cancel_active_turn(turn_id=turn_id)
        # Wait for any active turn holding _turn_lock to finish unwinding
        if self._turn_lock.locked():
            acquired = False
            wait_limit = max(2.0, float(self.settings.audio_post_playback_cooldown_sec) + 1.5)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._turn_lock.acquire(), timeout=wait_limit)
                acquired = True
            if acquired:
                self._turn_lock.release()
        if was_speaking and self.audio_capture.is_suspended:
            await self._resume_mic_after_speaking(did_speak=False)
        return dict(res)

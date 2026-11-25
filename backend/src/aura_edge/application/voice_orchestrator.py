"""Phase 1 Voice & Text Orchestrator uniting audio capture, STT, routing, tools, TTS, and FSM."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import numpy as np
from pydantic import BaseModel
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
from aura_edge.intelligence.fast_router import MultilingualFastRouter
from aura_edge.intelligence.ollama_client import OllamaClient
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
    AudioRouteMode,
    has_input_microphone,
    list_audio_devices,
)
from aura_edge.speech.playback import AudioPlayback
from aura_edge.speech.stt import ModelNotFoundError, TranscriptionResult, WhisperTranscriber
from aura_edge.speech.tts_router import TTSRouter
from aura_edge.speech.vad import SileroVadDetector

logger = logging.getLogger(__name__)


class ProcessTurnOutput(BaseModel):
    """Unified output for text, audio-upload, and PTT voice turns."""

    turn_id: str
    intent: str
    confidence: float
    intent_source: str
    detected_language: LanguageCode
    slots: dict[str, Any]
    response_text: str
    response_language: LanguageCode
    action_executed: bool
    status: str
    state: AssistantState
    data: dict[str, Any]
    latency_ms: float
    stt_ms: float | None = None
    router_ms: float | None = None
    llm_ms: float | None = None
    tool_ms: float | None = None
    tts_ms: float | None = None
    ttfa_ms: float | None = None
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
    ) -> None:
        self.settings = settings
        self.enforcer = enforcer
        self.engine = engine
        self.ws_hub = ws_hub

        self.compute_manager = ComputeManager(settings)
        self.session_language: RequestedLanguage = settings.default_language
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
        )

        # Intelligence & Execution components
        self.fast_router = MultilingualFastRouter()
        self.ollama_client = OllamaClient(settings, enforcer, self.compute_manager)
        self.planner = HybridPlanner(self.fast_router, self.ollama_client)
        self.datetime_tool = DateTimeTool()
        self.timer_manager = TimerManager(engine, on_timer_event=self._handle_timer_event)
        self.tool_registry = ToolRegistry(self.datetime_tool, self.timer_manager)
        self.metric_repo = VoiceMetricRepository(engine)

        self.fsm = AssistantStateMachine(on_change=self._handle_state_change)
        self._ptt_task: asyncio.Task[None] | None = None
        self._ptt_turn: TurnContext | None = None

    async def startup(self) -> dict[str, int]:
        """Recover persistent timers on FastAPI startup."""
        try:
            return await self.timer_manager.recover_on_startup()
        except (SQLAlchemyError, OSError, RuntimeError) as exc:
            logger.warning("Timer recovery skipped (DB unavailable): %s", exc)
            return {"recovered": 0, "missed": 0}

    async def shutdown(self) -> None:
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
        # Prevent assistant self-triggering while speaking
        if state == AssistantState.SPEAKING:
            self.audio_capture.set_route_mode(AudioRouteMode.MONITOR_ONLY)
        elif state in (AssistantState.IDLE, AssistantState.CANCELLED, AssistantState.ERROR):
            self.audio_capture.set_route_mode(AudioRouteMode.NORMAL)

        await self.ws_hub.broadcast(
            "assistant_state_change",
            {
                "state": state.value,
                "turn_id": turn_id,
                "message": message,
                "language": language or self._last_resolved_language,
            },
        )

    async def _handle_timer_event(self, event_type: str, timer_view: TimerView) -> None:
        await self.ws_hub.broadcast(event_type, {"timer": timer_view.model_dump()})
        if event_type in ("timer_completed", "timer_missed") and self.fsm.state == AssistantState.IDLE:
            lang: LanguageCode = (
                timer_view.language if timer_view.language in ("en", "hi", "kn") else "en"
            )  # type: ignore[assignment]
            lbl = f' "{timer_view.label}"' if timer_view.label else ""
            phrase = get_phrase("timer_completed", lang, label_part=lbl)
            try:
                await self.playback.speak_text(phrase, language=lang, play_to_speaker=True)
            except (OSError, RuntimeError, ValueError) as exc:
                logger.debug("Timer completion audio skipped: %s", exc)

    def set_language(self, language: RequestedLanguage) -> RequestedLanguage:
        self.session_language = language
        if language != "auto":
            self._last_resolved_language = language
        return self.session_language

    def set_compute_mode(self, mode: ComputeMode) -> ComputePlacement:
        return self.compute_manager.set_mode(mode)

    def get_status(self) -> dict[str, Any]:
        placement = self.compute_manager.resolve_placement()
        try:
            stt_ready = self.stt.resolve_model_dir().is_dir()
        except ModelNotFoundError:
            stt_ready = False

        return {
            "active_state": self.fsm.state.value,
            "active_turn_id": self.fsm.active_turn.turn_id if self.fsm.active_turn else None,
            "stt_engine": "faster-whisper",
            "stt_model": self.settings.stt_model_size,
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
        }

    async def start_ptt(self, *, language: RequestedLanguage | None = None) -> dict[str, Any]:
        if language is not None:
            self.set_language(language)

        if self.audio_capture.is_recording:
            return {
                "started": True,
                "already_recording": True,
                "turn_id": self._ptt_turn.turn_id if self._ptt_turn else None,
                "state": self.fsm.state.value,
            }

        if not has_input_microphone(self.settings.audio_input_device):
            await self.ws_hub.broadcast(
                "error",
                {"code": "mic_unavailable", "message": "No usable input microphone detected."},
            )
            raise AudioDeviceUnavailableError("No usable input microphone detected on host.")

        ctx = await self.fsm.start_turn(
            source="voice_ptt",
            language=self._last_resolved_language,
            initial_state=AssistantState.LISTENING,
        )
        self._ptt_turn = ctx

        async def _emit_level(rms_db: float, peak: float) -> None:
            if not ctx.is_cancelled:
                await self.ws_hub.broadcast("audio_level", {"rms_db": rms_db, "peak": peak})

        self._ptt_task = asyncio.create_task(
            self.audio_capture.start_ptt_capture(
                use_vad_endpointing=True,
                on_level=_emit_level,
                open_hardware_stream=True,
            )
        )
        return {
            "started": True,
            "turn_id": ctx.turn_id,
            "state": self.fsm.state.value,
            "language": self.session_language,
        }

    async def stop_ptt_and_process(self, *, speak: bool = True) -> ProcessTurnOutput:
        audio_f32, stop_reason = self.audio_capture.stop_capture(cancel=False)
        if self._ptt_task is not None:
            try:
                await asyncio.wait_for(self._ptt_task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError, AudioDeviceUnavailableError, OSError):
                self._ptt_task.cancel()
            self._ptt_task = None

        ctx = self._ptt_turn
        self._ptt_turn = None
        if ctx is None:
            return await self.process_audio_samples(
                audio_f32,
                source="voice_ptt",
                speak=speak,
            )
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
        speak: bool = False,
        allow_llm_fallback: bool = True,
    ) -> ProcessTurnOutput:
        if language is not None:
            self.set_language(language)
        ctx = await self.fsm.start_turn(
            source=source,
            language=self._last_resolved_language,
            initial_state=AssistantState.TRANSCRIBING,
        )
        return await self._process_audio_with_turn(
            ctx,
            audio_f32,
            stop_reason="uploaded",
            speak=speak,
            allow_llm_fallback=allow_llm_fallback,
        )

    async def _process_audio_with_turn(
        self,
        ctx: TurnContext,
        audio_f32: np.ndarray,
        *,
        stop_reason: str,
        speak: bool,
        allow_llm_fallback: bool = True,
    ) -> ProcessTurnOutput:
        t_start = time.perf_counter()
        loop = asyncio.get_running_loop()

        try:
            if self.fsm.state == AssistantState.LISTENING:
                await self.fsm.transition(ctx.turn_id, AssistantState.TRANSCRIBING)

            req_lang = self.session_language
            stt_res: TranscriptionResult = await loop.run_in_executor(
                None,
                lambda: self.stt.transcribe(
                    audio_f32,
                    language=req_lang,
                    fallback_language=self._last_resolved_language,
                ),
            )

            if ctx.is_cancelled:
                return self._cancelled_output(ctx, t_start)

            if stt_res.is_empty or not stt_res.text.strip():
                lang = stt_res.detected_language
                await self.fsm.transition(ctx.turn_id, AssistantState.CLARIFYING)
                msg = get_phrase("empty_speech", lang)
                await self.ws_hub.broadcast(
                    "clarification_required",
                    {"turn_id": ctx.turn_id, "language": lang, "message": msg},
                )
                tts_ms, ttfa_ms = await self._maybe_speak(ctx, msg, lang, speak=speak)
                await self.fsm.finish_turn(ctx.turn_id)
                return ProcessTurnOutput(
                    turn_id=ctx.turn_id,
                    intent="Clarify",
                    confidence=1.0,
                    intent_source="clarification",
                    detected_language=lang,
                    slots={},
                    response_text=msg,
                    response_language=lang,
                    action_executed=False,
                    status="clarification_required",
                    state=AssistantState.IDLE,
                    data={"stop_reason": stop_reason},
                    latency_ms=round((time.perf_counter() - t_start) * 1000.0, 2),
                    stt_ms=stt_res.latency_ms,
                    tts_ms=tts_ms,
                    ttfa_ms=ttfa_ms,
                    transcript="",
                )

            return await self._run_nlu_and_execute(
                ctx=ctx,
                text=stt_res.text,
                requested_language=(stt_res.detected_language if self.session_language == "auto" else self.session_language),
                speak=speak,
                allow_llm_fallback=allow_llm_fallback,
                t_start=t_start,
                stt_ms=stt_res.latency_ms,
                transcript=stt_res.text,
            )
        except StaleTurnError:
            return self._cancelled_output(ctx, t_start)
        except ModelNotFoundError as exc:
            await self._safe_error(ctx, str(exc))
            return ProcessTurnOutput(
                turn_id=ctx.turn_id,
                intent="Error",
                confidence=0.0,
                intent_source="error",
                detected_language=self._last_resolved_language,
                slots={},
                response_text=str(exc),
                response_language=self._last_resolved_language,
                action_executed=False,
                status="failed",
                state=AssistantState.ERROR,
                data={},
                latency_ms=round((time.perf_counter() - t_start) * 1000.0, 2),
                error=str(exc),
            )

    async def process_text(
        self,
        text: str,
        *,
        language: RequestedLanguage | None = None,
        source: str = "text_input",
        speak: bool = False,
        allow_llm_fallback: bool = True,
    ) -> ProcessTurnOutput:
        t_start = time.perf_counter()
        req_lang = language if language is not None else self.session_language
        fallback_lang: LanguageCode = (
            req_lang if req_lang != "auto" else self._last_resolved_language
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
                requested_language=req_lang,
                speak=speak,
                allow_llm_fallback=allow_llm_fallback,
                t_start=t_start,
                stt_ms=None,
                transcript=text,
            )
        except StaleTurnError:
            return self._cancelled_output(ctx, t_start)

    async def _run_nlu_and_execute(
        self,
        *,
        ctx: TurnContext,
        text: str,
        requested_language: RequestedLanguage,
        speak: bool,
        allow_llm_fallback: bool,
        t_start: float,
        stt_ms: float | None,
        transcript: str | None,
    ) -> ProcessTurnOutput:
        if self.fsm.state != AssistantState.UNDERSTANDING:
            await self.fsm.transition(ctx.turn_id, AssistantState.UNDERSTANDING)

        typed_intent, router_ms, llm_ms, llm_err = await self.planner.plan(
            text,
            requested_language=requested_language,
            fallback_language=self._last_resolved_language,
            allow_llm_fallback=allow_llm_fallback,
        )
        self._last_resolved_language = typed_intent.language
        ctx.language = typed_intent.language

        if ctx.is_cancelled:
            return self._cancelled_output(ctx, t_start)

        tool_t0 = time.perf_counter()
        if typed_intent.intent_name == IntentName.CLARIFY:
            await self.fsm.transition(ctx.turn_id, AssistantState.CLARIFYING)
            result = await self.tool_registry.execute_intent(typed_intent)
            await self.ws_hub.broadcast(
                "clarification_required",
                {"turn_id": ctx.turn_id, "language": typed_intent.language, "message": result.message},
            )
        elif typed_intent.intent_name == IntentName.UNSUPPORTED:
            result = VerifiedActionResult(
                success=False,
                action="Unsupported",
                status="refused",
                message=(
                    get_phrase("ollama_unavailable", typed_intent.language)
                    if llm_err
                    else get_phrase("unsupported", typed_intent.language)
                ),
                error=llm_err,
            )
        else:
            await self.fsm.transition(ctx.turn_id, AssistantState.EXECUTING)
            result = await self.tool_registry.execute_intent(typed_intent)
            if result.success:
                ctx.record_completed_action(result.action)
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

        tool_ms = round((time.perf_counter() - tool_t0) * 1000.0, 2)

        if ctx.is_cancelled:
            return self._cancelled_output(ctx, t_start)

        if typed_intent.intent_name == IntentName.STOP_ASSISTANT:
            self.playback.stop()

        tts_ms, ttfa_ms = await self._maybe_speak(
            ctx,
            result.message,
            typed_intent.language,
            speak=speak,
        )
        await self.fsm.finish_turn(ctx.turn_id)

        e2e_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
        placement = self.compute_manager.resolve_placement()
        try:
            self.metric_repo.record_turn(
                turn_id=ctx.turn_id,
                source=ctx.source,
                language=typed_intent.language,
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
            confidence=typed_intent.confidence,
            intent_source=typed_intent.source,
            detected_language=typed_intent.language,
            slots=typed_intent.slots,
            response_text=result.message,
            response_language=typed_intent.language,
            action_executed=result.success,
            status=result.status,
            state=self.fsm.state,
            data=result.data,
            latency_ms=e2e_ms,
            stt_ms=stt_ms,
            router_ms=router_ms,
            llm_ms=llm_ms,
            tool_ms=tool_ms,
            tts_ms=tts_ms,
            ttfa_ms=ttfa_ms,
            error=result.error,
            transcript=transcript,
        )

    async def _maybe_speak(
        self,
        ctx: TurnContext,
        text: str,
        language: LanguageCode,
        *,
        speak: bool,
    ) -> tuple[float | None, float | None]:
        if not speak or not text.strip():
            return None, None
        await self.fsm.transition(ctx.turn_id, AssistantState.SPEAKING, language=language)
        t0 = time.perf_counter()
        try:
            chunks = await self.playback.speak_text(
                text,
                language=language,
                play_to_speaker=True,
                external_cancel=ctx.cancel_event,
            )
            total_tts = round((time.perf_counter() - t0) * 1000.0, 2)
            first_tts = chunks[0].latency_ms if chunks else None
            return total_tts, first_tts
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("TTS speaking skipped/failed: %s", exc)
            return None, None

    async def _safe_error(self, ctx: TurnContext, message: str) -> None:
        try:
            await self.fsm.transition(ctx.turn_id, AssistantState.ERROR, message=message)
        except (StaleTurnError, RuntimeError) as exc:
            logger.debug("Skipping ERROR state transition: %s", exc)
        await self.ws_hub.broadcast("error", {"turn_id": ctx.turn_id, "message": message})
        await self.fsm.finish_turn(ctx.turn_id)

    def _cancelled_output(self, ctx: TurnContext, t_start: float) -> ProcessTurnOutput:
        return ProcessTurnOutput(
            turn_id=ctx.turn_id,
            intent="Cancelled",
            confidence=1.0,
            intent_source="cancellation",
            detected_language=self._last_resolved_language,
            slots={},
            response_text="Cancelled.",
            response_language=self._last_resolved_language,
            action_executed=bool(ctx.completed_actions),
            status="cancelled",
            state=self.fsm.state,
            data={"completed_actions_preserved": list(ctx.completed_actions)},
            latency_ms=round((time.perf_counter() - t_start) * 1000.0, 2),
        )

    async def cancel_turn(self, turn_id: str | None = None) -> dict[str, Any]:
        self.playback.stop()
        if self.audio_capture.is_recording:
            self.audio_capture.stop_capture(cancel=True)
        if self._ptt_task is not None and not self._ptt_task.done():
            self._ptt_task.cancel()
        res = await self.fsm.cancel_active_turn(turn_id=turn_id)
        return dict(res)

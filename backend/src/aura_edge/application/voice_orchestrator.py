"""Phase 1 Voice & Text Orchestrator uniting audio capture, STT, routing, tools, TTS, and FSM."""

from __future__ import annotations

import asyncio
import contextlib
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
from aura_edge.intelligence.conversation import (
    ConversationSession,
    strip_markdown_for_speech,
    validate_response_language,
)
from aura_edge.intelligence.fast_router import MultilingualFastRouter
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
    has_input_microphone,
    list_audio_devices,
)
from aura_edge.speech.playback import AudioPlayback, PlaybackMetrics
from aura_edge.speech.stt import ModelNotFoundError, TranscriptionResult, WhisperTranscriber
from aura_edge.speech.tts_router import SentenceStreamBuffer, TTSRouter
from aura_edge.speech.vad import SileroVadDetector

logger = logging.getLogger(__name__)


class ProcessTurnOutput(BaseModel):
    """Unified output for text, audio-upload, PTT, and continuous voice turns."""

    turn_id: str
    intent: str
    route_category: str = "tool"
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
    llm_ttft_ms: float | None = None
    llm_ms: float | None = None
    tool_ms: float | None = None
    tts_ms: float | None = None
    audio_startup_ms: float | None = None
    playback_ms: float | None = None
    ttfa_ms: float | None = None
    tts_cache_hit: bool = False
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
        self.conversation = ConversationSession(max_turns=12, idle_timeout_sec=1800.0)
        self.datetime_tool = DateTimeTool()
        self.timer_manager = TimerManager(engine, on_timer_event=self._handle_timer_event)
        self.tool_registry = ToolRegistry(self.datetime_tool, self.timer_manager, smart_home)
        self.metric_repo = VoiceMetricRepository(engine)

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
        if state == AssistantState.SPEAKING:
            self.audio_capture.suspend_for_playback()
        elif state in (AssistantState.CANCELLED, AssistantState.ERROR):
            self.audio_capture.resume_after_playback()

        await self.ws_hub.broadcast(
            "assistant_state_change",
            {
                "state": state.value,
                "turn_id": turn_id,
                "message": message,
                "language": language or self._last_resolved_language,
            },
        )

    async def _speak_timer_notification(self, phrase: str, lang: LanguageCode) -> None:
        """Speak completed/missed timer alerts exclusively without colliding with turns."""
        async with self._turn_lock:
            if self.fsm.state != AssistantState.IDLE or self.playback.is_playing:
                return
            self.audio_capture.suspend_for_playback()
            try:
                await self.playback.speak_text(phrase, language=lang, play_to_speaker=True)
            except (OSError, RuntimeError, ValueError) as exc:
                logger.debug("Timer completion audio skipped: %s", exc)
            finally:
                await self._resume_mic_after_speaking(did_speak=True)

    async def _handle_timer_event(self, event_type: str, timer_view: TimerView) -> None:
        await self.ws_hub.broadcast(event_type, {"timer": timer_view.model_dump()})
        if event_type in ("timer_completed", "timer_missed"):
            lang: LanguageCode = (
                timer_view.language if timer_view.language in ("en", "hi", "kn") else "en"
            )  # type: ignore[assignment]
            lbl = f' "{timer_view.label}"' if timer_view.label else ""
            phrase = get_phrase("timer_completed", lang, label_part=lbl)
            task = asyncio.create_task(self._speak_timer_notification(phrase, lang))
            self._timer_audio_tasks.add(task)
            task.add_done_callback(self._timer_audio_tasks.discard)

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

        kn_dir = self.stt.resolve_kannada_specialist_dir()
        return {
            "active_state": self.fsm.state.value,
            "active_turn_id": self.fsm.active_turn.turn_id if self.fsm.active_turn else None,
            "stt_engine": "faster-whisper",
            "stt_model": self.settings.stt_model_size,
            "stt_kannada_specialist": "indic-conformer-kn" if kn_dir is not None else None,
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
        if muted and self.audio_capture.is_recording:
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

    async def start_ptt(self, *, language: RequestedLanguage | None = None) -> dict[str, Any]:
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
        speak: bool = False,
        allow_llm_fallback: bool = True,
    ) -> ProcessTurnOutput:
        await self._preempt_idle_listening()
        async with self._turn_lock:
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
        silent_empty: bool = False,
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
                tts_ms, audio_startup_ms, playback_ms, ttfa_ms, tts_cache_hit = await self._maybe_speak(
                    ctx,
                    msg,
                    lang,
                    speak=speak and not silent_empty,
                    turn_start_perf=t_start,
                )
                await self.fsm.finish_turn(ctx.turn_id)
                return ProcessTurnOutput(
                    turn_id=ctx.turn_id,
                    intent="Clarify",
                    route_category="clarification",
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
                    audio_startup_ms=audio_startup_ms,
                    playback_ms=playback_ms,
                    ttfa_ms=ttfa_ms,
                    tts_cache_hit=tts_cache_hit,
                    transcript="",
                )

            await self.ws_hub.broadcast(
                "transcript_ready",
                {
                    "turn_id": ctx.turn_id,
                    "transcript": stt_res.text,
                    "language": stt_res.detected_language,
                    "model_size": stt_res.model_size,
                    "stt_ms": stt_res.latency_ms,
                },
            )
            effective_req_lang: RequestedLanguage = (
                self.session_language
                if self.session_language != "auto"
                else stt_res.detected_language
            )
            return await self._run_nlu_and_execute(
                ctx=ctx,
                text=stt_res.text,
                requested_language=effective_req_lang,
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
        await self._preempt_idle_listening()
        async with self._turn_lock:
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
        reply_language: LanguageCode = typed_intent.target_language or typed_intent.language
        self._last_resolved_language = reply_language
        ctx.language = reply_language

        if ctx.is_cancelled:
            return self._cancelled_output(ctx, t_start)

        tool_t0 = time.perf_counter()
        llm_ttft_ms: float | None = None
        tts_ms: float | None = None
        audio_startup_ms: float | None = None
        playback_ms: float | None = None
        ttfa_ms: float | None = None
        tts_cache_hit = False
        already_spoke = False

        if typed_intent.intent_name == IntentName.CLARIFY:
            await self.fsm.transition(ctx.turn_id, AssistantState.CLARIFYING)
            result = await self.tool_registry.execute_intent(typed_intent)
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
            await self.ws_hub.broadcast(
                "response_completed",
                {
                    "turn_id": ctx.turn_id,
                    "intent": typed_intent.intent_name.value,
                    "route_category": "greeting",
                    "language": reply_language,
                    "response_text": result.message,
                },
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
            await self.ws_hub.broadcast(
                "response_completed",
                {
                    "turn_id": ctx.turn_id,
                    "intent": "SwitchLanguage",
                    "route_category": "language_switch",
                    "language": reply_language,
                    "response_text": result.message,
                },
            )
        elif typed_intent.intent_name == IntentName.CONVERSATION:
            (
                result,
                llm_ttft_ms,
                llm_ms,
                tts_ms,
                audio_startup_ms,
                playback_ms,
                ttfa_ms,
                tts_cache_hit,
                already_spoke,
            ) = await self._execute_streaming_conversation(
                ctx=ctx,
                user_text=text,
                reply_language=reply_language,
                speak=speak,
                t_start=t_start,
            )
        elif typed_intent.intent_name == IntentName.UNSUPPORTED:
            result = VerifiedActionResult(
                success=False,
                action="Unsupported",
                status="refused",
                message=(
                    get_phrase("ollama_unavailable", reply_language)
                    if llm_err
                    else get_phrase("unsupported", reply_language)
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

        tool_ms = round((time.perf_counter() - tool_t0) * 1000.0, 2) if typed_intent.route_category == "tool" else 0.0

        if ctx.is_cancelled:
            return self._cancelled_output(ctx, t_start)

        if typed_intent.intent_name == IntentName.STOP_ASSISTANT:
            self.playback.stop()

        if not already_spoke:
            tts_ms, audio_startup_ms, playback_ms, ttfa_ms, tts_cache_hit = await self._maybe_speak(
                ctx,
                result.message,
                reply_language,
                speak=speak,
                turn_start_perf=t_start,
            )

        await self.fsm.finish_turn(ctx.turn_id)

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
            await self.ws_hub.broadcast(
                "conversation_history_updated",
                {"count": len(self.conversation.get_history()), "turn_id": ctx.turn_id},
            )

        e2e_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
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
            detected_language=typed_intent.language,
            slots=typed_intent.slots,
            response_text=result.message,
            response_language=reply_language,
            action_executed=result.success,
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
        t_start: float,
    ) -> tuple[
        VerifiedActionResult,
        float | None,
        float | None,
        float | None,
        float | None,
        float | None,
        float | None,
        bool,
        bool,
    ]:
        """Stream Ollama tokens live, validate script/claims, and drive half-duplex playback."""
        caps = self.tool_registry.describe_capabilities(reply_language)
        messages = self.conversation.build_llm_messages(
            user_text,
            reply_language=reply_language,
            capability_summary=caps,
        )
        low_u = user_text.lower()
        want_detail = any(
            kw in low_u for kw in ("explain", "continue", "detail", "more", "why", "how", "विस्तार", "ವಿವರ")
        )
        token_budget = 220 if want_detail else 120
        sentence_buf = SentenceStreamBuffer(min_chars=24)
        sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
        speak_task: asyncio.Task[tuple[list[Any], PlaybackMetrics]] | None = None
        stream_tts_started = False

        async def _ensure_stream_tts_started() -> None:
            nonlocal speak_task, stream_tts_started
            if not speak or speak_task is not None or ctx.is_cancelled:
                return
            self.audio_capture.suspend_for_playback()
            await self.fsm.transition(ctx.turn_id, AssistantState.SPEAKING, language=reply_language)
            stream_tts_started = True
            speak_task = asyncio.create_task(
                self.playback.speak_sentence_stream(
                    sentence_queue,
                    language=reply_language,
                    play_to_speaker=True,
                    external_cancel=ctx.cancel_event,
                    turn_start_perf=t_start,
                )
            )

        # For English, open playback pipeline immediately; for HI/KN, validate first sentence script first!
        if speak and reply_language == "en":
            await _ensure_stream_tts_started()

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
                    raw_parts.append(token)
                    await self.ws_hub.broadcast(
                        "response_text_delta",
                        {
                            "turn_id": ctx.turn_id,
                            "token": token,
                            "language": reply_language,
                        },
                    )
                    if speak:
                        for sentence in sentence_buf.push(token):
                            clean_sent = strip_markdown_for_speech(sentence)
                            if not clean_sent:
                                continue
                            if speak_task is None:
                                valid_sent, _ = validate_response_language(clean_sent, reply_language)
                                if not valid_sent:
                                    continue
                                await _ensure_stream_tts_started()
                            if speak_task is not None:
                                await sentence_queue.put(clean_sent)

            tail = sentence_buf.flush()
            if speak and tail and not ctx.is_cancelled:
                clean_tail = strip_markdown_for_speech(tail)
                if clean_tail and speak_task is not None:
                    await sentence_queue.put(clean_tail)
        except OllamaUnavailableError as exc:
            if speak_task is not None:
                await sentence_queue.put(None)
                with contextlib.suppress(OSError, RuntimeError, ValueError):
                    await speak_task
            if stream_tts_started:
                await self._resume_mic_after_speaking(did_speak=False)
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
                None,
                None,
                None,
                None,
                False,
                False,
            )

        clean_full = strip_markdown_for_speech("".join(raw_parts)) or "..."
        is_valid, reason = validate_response_language(clean_full, reply_language)
        if not is_valid and not ctx.is_cancelled:
            logger.info("Response validation failed (%s); applying safe %s guardrail.", reason, reply_language)
            if reason == "unsupported_language_claim":
                clean_full = get_phrase("supported_languages", reply_language)
            elif reply_language == "kn":
                clean_full = "ಕ್ಷಮಿಸಿ, ನನಗೆ ಈ ಪ್ರಶ್ನೆಗೆ ಸ್ಪಷ್ಟ ಕನ್ನಡದಲ್ಲಿ ಉತ್ತರಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ."
            elif reply_language == "hi":
                clean_full = "माफ कीजिए, मैं इस सवाल का स्पष्ट हिंदी में उत्तर नहीं दे पाया।"

        tts_ms: float | None = None
        audio_startup_ms: float | None = None
        playback_ms: float | None = None
        ttfa_ms: float | None = None
        cache_hit = False
        already_spoke = False

        if speak_task is not None:
            await sentence_queue.put(None)
            try:
                _audios, pm = await speak_task
                tts_ms = pm.tts_synthesis_ms
                audio_startup_ms = pm.audio_startup_ms
                playback_ms = pm.audio_playback_ms
                ttfa_ms = pm.first_audio_ready_ms
                cache_hit = pm.cache_hits > 0
                already_spoke = True
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("Streaming TTS playback failed: %s", exc)
            finally:
                await self._resume_mic_after_speaking(did_speak=bool(playback_ms and playback_ms > 0.0))
        elif speak and not ctx.is_cancelled:
            tts_ms, audio_startup_ms, playback_ms, ttfa_ms, cache_hit = await self._maybe_speak(
                ctx,
                clean_full,
                reply_language,
                speak=True,
                turn_start_perf=t_start,
            )
            already_spoke = True

        await self.ws_hub.broadcast(
            "response_completed",
            {
                "turn_id": ctx.turn_id,
                "intent": IntentName.CONVERSATION.value,
                "route_category": "conversation",
                "language": reply_language,
                "response_text": clean_full,
                "llm_ttft_ms": llm_stats.get("ttft_ms"),
                "llm_ms": llm_stats.get("latency_ms"),
                "tts_ms": tts_ms,
                "audio_startup_ms": audio_startup_ms,
                "playback_ms": playback_ms,
                "ttfa_ms": ttfa_ms,
            },
        )
        return (
            VerifiedActionResult(
                success=True,
                action="Conversation",
                status="verified",
                message=clean_full,
            ),
            llm_stats.get("ttft_ms"),
            llm_stats.get("latency_ms"),
            tts_ms,
            audio_startup_ms,
            playback_ms,
            ttfa_ms,
            cache_hit,
            already_spoke,
        )

    async def _maybe_speak(
        self,
        ctx: TurnContext,
        text: str,
        language: LanguageCode,
        *,
        speak: bool,
        turn_start_perf: float | None = None,
    ) -> tuple[float | None, float | None, float | None, float | None, bool]:
        if not speak or not text.strip() or ctx.is_cancelled:
            return None, None, None, None, False
        self.audio_capture.suspend_for_playback()
        if self.fsm.state != AssistantState.SPEAKING:
            await self.fsm.transition(ctx.turn_id, AssistantState.SPEAKING, language=language)
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
            return (
                pm.tts_synthesis_ms,
                pm.audio_startup_ms,
                pm.audio_playback_ms,
                pm.first_audio_ready_ms,
                pm.cache_hits > 0,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("TTS speaking skipped/failed: %s", exc)
            return None, None, None, None, False
        finally:
            await self._resume_mic_after_speaking(did_speak=did_play)

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

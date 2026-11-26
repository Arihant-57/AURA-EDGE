"""Phase 1 voice, intent processing, synthesis, language, and compute-mode endpoints."""

from __future__ import annotations

import io
import math
import wave
from typing import Annotated, Literal

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from aura_edge.application.voice_orchestrator import ProcessTurnOutput, VoiceOrchestrator
from aura_edge.config import ComputeMode
from aura_edge.intelligence.schemas import LanguageCode, RequestedLanguage
from aura_edge.speech.audio_capture import AudioDeviceUnavailableError
from aura_edge.speech.stt import ModelNotFoundError

router = APIRouter(prefix="/api/v1", tags=["voice"])
_FILE_PARAM = File()
_FORM_PARAM = Form()


class IntentProcessRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    language: RequestedLanguage | None = None
    source: str = "text_input"
    speak: bool = False
    allow_llm_fallback: bool = True


class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    language: LanguageCode = "en"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class LanguageSelectRequest(BaseModel):
    language: RequestedLanguage
    recognition_mode: Literal["multilingual", "strict"] | None = None


class ComputeModeRequest(BaseModel):
    compute_mode: ComputeMode


class PttStartRequest(BaseModel):
    language: RequestedLanguage | None = None


class PttStopRequest(BaseModel):
    speak: bool = True


class CancelTurnRequest(BaseModel):
    turn_id: str | None = None


class ConversationStartRequest(BaseModel):
    language: RequestedLanguage | None = None
    speak: bool = True
    mode: Literal["continuous", "ptt"] = "continuous"
    silence_timeout_sec: float = Field(default=60.0, ge=5.0, le=600.0)


class MicMuteRequest(BaseModel):
    muted: bool


class PrewarmRequest(BaseModel):
    prewarm_ollama: bool = True


def _decode_wav_bytes_to_16k_float32(raw_bytes: bytes) -> np.ndarray:
    try:
        with wave.open(io.BytesIO(raw_bytes), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            sr = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
    except wave.Error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid WAV audio file: {exc}") from exc

    if sampwidth == 2:
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        audio = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported WAV sample width: {sampwidth} bytes")

    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    if sr != 16000 and audio.size > 0:
        from scipy.signal import resample_poly

        g = math.gcd(16000, sr)
        audio = resample_poly(audio, 16000 // g, sr // g).astype(np.float32)

    return audio


@router.get("/voice/status")
def get_voice_status(request: Request) -> dict[str, object]:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    return orch.get_status()


@router.post("/voice/mic/start")
@router.post("/voice/ptt/start")
async def start_microphone_capture(
    request: Request,
    body: PttStartRequest | None = None,
) -> dict[str, object]:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    try:
        return await orch.start_ptt(language=body.language if body else None)
    except AudioDeviceUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/voice/mic/stop", response_model=ProcessTurnOutput)
@router.post("/voice/ptt/stop", response_model=ProcessTurnOutput)
async def stop_microphone_capture(
    request: Request,
    body: PttStopRequest | None = None,
) -> ProcessTurnOutput:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    return await orch.stop_ptt_and_process(speak=body.speak if body else True)


@router.post("/voice/transcribe", response_model=ProcessTurnOutput)
async def transcribe_uploaded_audio(
    request: Request,
    file: Annotated[UploadFile, _FILE_PARAM],
    language: Annotated[RequestedLanguage, _FORM_PARAM] = "auto",
    recognition_mode: Annotated[Literal["multilingual", "strict"] | None, _FORM_PARAM] = None,
    speak: Annotated[bool, _FORM_PARAM] = False,
    allow_llm_fallback: Annotated[bool, _FORM_PARAM] = True,
) -> ProcessTurnOutput:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    raw = await file.read()
    audio_f32 = _decode_wav_bytes_to_16k_float32(raw)
    try:
        return await orch.process_audio_samples(
            audio_f32,
            source="audio_upload",
            language=language,
            recognition_mode=recognition_mode,
            speak=speak,
            allow_llm_fallback=allow_llm_fallback,
        )
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/intent/process", response_model=ProcessTurnOutput)
async def process_intent(body: IntentProcessRequest, request: Request) -> ProcessTurnOutput:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    return await orch.process_text(
        body.text,
        language=body.language,
        source=body.source,
        speak=body.speak,
        allow_llm_fallback=body.allow_llm_fallback,
    )


@router.post("/voice/synthesize")
def synthesize_voice(body: SynthesizeRequest, request: Request) -> Response:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    try:
        wav_bytes, audio = orch.tts_router.synthesize_wav(
            body.text,
            language=body.language,
            speed=body.speed,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Aura-TTS-Backend": audio.backend,
            "X-Aura-TTS-Model": audio.model_name,
            "X-Aura-TTS-Cached": str(audio.cached).lower(),
            "X-Aura-TTS-Latency-Ms": str(audio.latency_ms),
        },
    )


@router.post("/voice/cancel")
async def cancel_voice_turn(
    request: Request,
    body: CancelTurnRequest | None = None,
) -> dict[str, object]:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    return await orch.cancel_turn(turn_id=body.turn_id if body else None)


@router.put("/voice/language")
def select_language(body: LanguageSelectRequest, request: Request) -> dict[str, object]:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    selected = orch.set_language(body.language, recognition_mode=body.recognition_mode)
    return {
        "session_language": selected,
        "resolved_language": orch._last_resolved_language,
        "recognition_mode": orch.stt_recognition_mode,
        "stt_language": orch.stt_language,
    }


@router.put("/voice/compute-mode")
def select_compute_mode(body: ComputeModeRequest, request: Request) -> dict[str, object]:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    placement = orch.set_compute_mode(body.compute_mode)
    return placement.model_dump()


@router.post("/voice/conversation/start")
async def start_conversation_mode(
    request: Request,
    body: ConversationStartRequest | None = None,
) -> dict[str, object]:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    try:
        return await orch.start_conversation(
            language=body.language if body else None,
            speak=body.speak if body else True,
            mode=body.mode if body else "continuous",
            silence_timeout_sec=body.silence_timeout_sec if body else None,
        )
    except AudioDeviceUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/voice/conversation/stop")
async def stop_conversation_mode(request: Request) -> dict[str, object]:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    return await orch.stop_conversation(reason="api_stop")


@router.put("/voice/mic/mute")
async def set_microphone_mute(body: MicMuteRequest, request: Request) -> dict[str, object]:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    return await orch.set_mic_mute(body.muted)


@router.get("/conversation/history")
def get_conversation_history(request: Request) -> dict[str, object]:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    history = orch.get_conversation_history()
    return {"count": len(history), "messages": history}


@router.delete("/conversation/history")
async def clear_conversation_history(request: Request) -> dict[str, object]:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    return await orch.clear_conversation_history()


@router.post("/voice/prewarm")
async def prewarm_voice_engines(
    request: Request,
    body: PrewarmRequest | None = None,
) -> dict[str, object]:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    return await orch.prewarm(prewarm_ollama=body.prewarm_ollama if body else True)

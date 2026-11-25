"""Policy-wrapped local Ollama client with VRAM serialization and true NDJSON streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import BaseModel

from aura_edge.config import Settings
from aura_edge.infrastructure.compute import ComputeManager
from aura_edge.infrastructure.network_policy import (
    NetworkPolicyEnforcer,
    NetworkPolicyViolationError,
    build_async_http_client,
)

logger = logging.getLogger(__name__)


class OllamaUnavailableError(RuntimeError):
    """Raised when loopback Ollama is unreachable or blocked by policy."""


class OllamaStructuredResult(BaseModel):
    """Parsed JSON response and timing telemetry from local Ollama."""

    content: str
    parsed_json: dict[str, Any] | None = None
    model: str
    latency_ms: float
    ttft_ms: float | None = None
    eval_count: int | None = None
    tokens_per_sec: float | None = None
    llm_device: str
    num_gpu: int | None


class OllamaClient:
    """Async Ollama client routed strictly through ``AsyncPolicyEnforcingTransport``."""

    def __init__(
        self,
        settings: Settings,
        enforcer: NetworkPolicyEnforcer,
        compute_manager: ComputeManager,
    ) -> None:
        self._settings = settings
        self._enforcer = enforcer
        self._compute_manager = compute_manager
        self._vram_lock = asyncio.Lock()

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.0,
        num_predict: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream NDJSON chunks from loopback ``/api/chat`` obeying CPU/GPU placement."""
        target_model = model or self._settings.ollama_model
        url = f"{self._settings.ollama_url.rstrip('/')}/api/chat"
        placement = self._compute_manager.resolve_placement()

        options: dict[str, Any] = {
            "num_ctx": int(self._settings.ollama_num_ctx),
            "temperature": float(temperature),
        }
        if num_predict is not None:
            options["num_predict"] = int(num_predict)
        if placement.llm_num_gpu is not None:
            options["num_gpu"] = placement.llm_num_gpu

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "stream": True,
            "think": False,
            "keep_alive": "15m",
            "options": options,
        }
        if json_mode:
            payload["format"] = "json"

        async with self._vram_lock:
            try:
                async with build_async_http_client(
                    self._enforcer,
                    timeout=self._settings.ollama_timeout_sec,
                ) as client, client.stream("POST", url, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        yield json.loads(line)
            except NetworkPolicyViolationError as exc:
                raise OllamaUnavailableError(f"Ollama blocked by network policy: {exc}") from exc
            except (httpx.HTTPError, OSError, json.JSONDecodeError) as exc:
                raise OllamaUnavailableError(f"Local Ollama request failed: {exc}") from exc

    async def stream_conversation(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        num_predict: int = 120,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any] | None]]:
        """Yield ``(token_delta, final_stats_or_none)`` while respecting cancellation."""
        t0 = time.perf_counter()
        ttft_ms: float | None = None
        last_chunk: dict[str, Any] = {}

        async for chunk in self.stream_chat(
            messages,
            model=model,
            json_mode=False,
            temperature=temperature,
            num_predict=num_predict,
        ):
            if cancel_event is not None and cancel_event.is_set():
                break
            msg = chunk.get("message") or {}
            token = str(msg.get("content") or "")
            if token and ttft_ms is None:
                ttft_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            last_chunk = chunk
            done = bool(chunk.get("done"))
            if token:
                yield token, None
            if done:
                total_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                eval_count = last_chunk.get("eval_count")
                eval_dur_ns = last_chunk.get("eval_duration")
                tps: float | None = None
                if isinstance(eval_count, int) and isinstance(eval_dur_ns, int) and eval_dur_ns > 0:
                    tps = round(eval_count / (eval_dur_ns / 1e9), 2)
                yield (
                    "",
                    {
                        "ttft_ms": ttft_ms,
                        "latency_ms": total_ms,
                        "eval_count": eval_count if isinstance(eval_count, int) else None,
                        "tokens_per_sec": tps,
                    },
                )
                return

        # Cancelled early or stream closed without done flag
        total_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        yield (
            "",
            {
                "ttft_ms": ttft_ms,
                "latency_ms": total_ms,
                "eval_count": last_chunk.get("eval_count"),
                "tokens_per_sec": None,
            },
        )

    async def prewarm(self, *, model: str | None = None) -> float:
        """Load target Ollama model weights into memory with a minimal 1-token call."""
        t0 = time.perf_counter()
        async for _chunk in self.stream_chat(
            [{"role": "user", "content": "hi"}],
            model=model,
            json_mode=False,
            temperature=0.0,
            num_predict=1,
        ):
            pass
        return round((time.perf_counter() - t0) * 1000.0, 2)

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
    ) -> OllamaStructuredResult:
        """Stream ``/api/chat`` in JSON mode, measuring TTFT and tokens/sec accurately."""
        target_model = model or self._settings.ollama_model
        placement = self._compute_manager.resolve_placement()
        t0 = time.perf_counter()
        ttft_ms: float | None = None
        parts: list[str] = []
        last_chunk: dict[str, Any] = {}

        async for chunk in self.stream_chat(messages, model=target_model, json_mode=True):
            msg = chunk.get("message") or {}
            token = msg.get("content") or ""
            if token and ttft_ms is None:
                ttft_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if token:
                parts.append(str(token))
            last_chunk = chunk

        total_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        full_text = "".join(parts).strip()

        eval_count = last_chunk.get("eval_count")
        eval_duration_ns = last_chunk.get("eval_duration")
        tps: float | None = None
        if isinstance(eval_count, int) and isinstance(eval_duration_ns, int) and eval_duration_ns > 0:
            tps = round(eval_count / (eval_duration_ns / 1e9), 2)

        parsed: dict[str, Any] | None = None
        try:
            obj = json.loads(full_text)
            if isinstance(obj, dict):
                parsed = obj
        except json.JSONDecodeError:
            # Try recovering JSON object substring if surrounded by whitespace/fence
            start = full_text.find("{")
            end = full_text.rfind("}")
            if start != -1 and end > start:
                try:
                    obj = json.loads(full_text[start : end + 1])
                    if isinstance(obj, dict):
                        parsed = obj
                except json.JSONDecodeError:
                    parsed = None

        return OllamaStructuredResult(
            content=full_text,
            parsed_json=parsed,
            model=target_model,
            latency_ms=total_ms,
            ttft_ms=ttft_ms,
            eval_count=eval_count if isinstance(eval_count, int) else None,
            tokens_per_sec=tps,
            llm_device=placement.llm_device,
            num_gpu=placement.llm_num_gpu,
        )

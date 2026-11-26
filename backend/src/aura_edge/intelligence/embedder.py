"""Offline CPU embedding client routed through NetworkPolicyEnforcer."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from aura_edge.config import Settings
from aura_edge.infrastructure.network_policy import (
    NetworkPolicyEnforcer,
    NetworkPolicyViolationError,
    build_async_http_client,
    build_http_client,
)

logger = logging.getLogger(__name__)


class LocalEmbedder:
    """Computes normalized local embeddings via loopback Ollama on CPU."""

    def __init__(self, settings: Settings, enforcer: NetworkPolicyEnforcer) -> None:
        self._settings = settings
        self._enforcer = enforcer
        self.model = settings.embedding_model
        self.dimensions = int(settings.embedding_dim)
        self._last_available: bool | None = None
        self._last_error: str | None = None

    def _endpoint(self) -> str:
        return f"{self._settings.ollama_url.rstrip('/')}/api/embed"

    def _validate_vector(self, raw: Any) -> list[float] | None:
        if not isinstance(raw, list) or len(raw) != self.dimensions:
            return None
        try:
            vec = [float(x) for x in raw]
        except (TypeError, ValueError):
            return None
        # Never return an all-zero fake vector
        if not any(abs(v) > 1e-12 for v in vec):
            return None
        return vec

    def embed_sync(self, text: str) -> list[float] | None:
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        payload = {
            "model": self.model,
            "input": cleaned,
            "options": {"num_gpu": 0},
            "keep_alive": "5m",
        }
        try:
            with build_http_client(self._enforcer, timeout=15.0) as client:
                resp = client.post(self._endpoint(), json=payload)
                resp.raise_for_status()
                data = resp.json()
        except (NetworkPolicyViolationError, httpx.HTTPError, OSError, ValueError) as exc:
            self._last_available = False
            self._last_error = str(exc)
            logger.debug("LocalEmbedder unavailable (%s); falling back to keyword search.", exc)
            return None

        embeddings = data.get("embeddings") or []
        vec = self._validate_vector(embeddings[0] if embeddings else None)
        if vec is None:
            self._last_available = False
            self._last_error = f"Unexpected embedding dimension from {self.model}"
            return None
        self._last_available = True
        self._last_error = None
        return vec

    async def embed_async(self, text: str) -> list[float] | None:
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        payload = {
            "model": self.model,
            "input": cleaned,
            "options": {"num_gpu": 0},
            "keep_alive": "5m",
        }
        try:
            async with build_async_http_client(self._enforcer, timeout=15.0) as client:
                resp = await client.post(self._endpoint(), json=payload)
                resp.raise_for_status()
                data = resp.json()
        except (NetworkPolicyViolationError, httpx.HTTPError, OSError, ValueError) as exc:
            self._last_available = False
            self._last_error = str(exc)
            logger.debug("LocalEmbedder unavailable (%s); falling back to keyword search.", exc)
            return None

        embeddings = data.get("embeddings") or []
        vec = self._validate_vector(embeddings[0] if embeddings else None)
        if vec is None:
            self._last_available = False
            self._last_error = f"Unexpected embedding dimension from {self.model}"
            return None
        self._last_available = True
        self._last_error = None
        return vec

    def status(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "dimensions": self.dimensions,
            "device": "cpu",
            "available": self._last_available,
            "last_error": self._last_error,
        }

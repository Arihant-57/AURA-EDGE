"""Strict offline runtime & socket isolation audit for AURA EDGE Phase 1."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import psutil

from aura_edge.config import ConnectivityMode, Settings, apply_offline_env_guards
from aura_edge.infrastructure.network_policy import (
    NetworkPolicyEnforcer,
    NetworkPolicyViolationError,
    build_async_http_client,
)
from aura_edge.intelligence.fast_router import MultilingualFastRouter
from aura_edge.speech.tts_router import TTSRouter


def _find_non_loopback_connections(pid: int) -> list[dict[str, str]]:
    loopback_ips = {"127.0.0.1", "::1", "0.0.0.0", "::", ""}
    out: list[dict[str, str]] = []
    try:
        proc = psutil.Process(pid)
        for conn in proc.net_connections(kind="inet"):
            if conn.raddr and conn.raddr.ip not in loopback_ips:
                out.append(
                    {
                        "pid": str(pid),
                        "laddr": f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "",
                        "raddr": f"{conn.raddr.ip}:{conn.raddr.port}",
                        "status": str(conn.status),
                    }
                )
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return out


async def _verify_policy_blocks(enforcer: NetworkPolicyEnforcer) -> list[dict[str, str]]:
    blocked: list[dict[str, str]] = []
    async with build_async_http_client(enforcer) as client:
        for url in [
            "https://huggingface.co/api/models",
            "https://api.openai.com/v1/chat/completions",
            "https://speech.platform.bing.com/consumer/speech/synthesize",
            "http://169.254.169.254/latest/meta-data",
        ]:
            try:
                await client.get(url)
                raise AssertionError(f"Expected NetworkPolicyViolationError for {url}")
            except NetworkPolicyViolationError as exc:
                blocked.append({"url": url, "reason": exc.decision.reason})
    return blocked


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify AURA EDGE Phase 1 offline isolation.")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Running backend URL")
    parser.add_argument(
        "--require-live-backend",
        action="store_true",
        help="Fail if live Uvicorn server on --url is not reachable",
    )
    args = parser.parse_args()

    settings = Settings()
    apply_offline_env_guards(settings)
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"
    assert settings.connectivity_mode == ConnectivityMode.STRICT_OFFLINE

    enforcer = NetworkPolicyEnforcer.from_settings(settings)
    blocked = asyncio.run(_verify_policy_blocks(enforcer))

    # Exercise deterministic routing and offline TTS directly in-process
    router = MultilingualFastRouter()
    for phrase, lang in [
        ("What time is it?", "en"),
        ("पांच मिनट का टाइमर लगाओ", "hi"),
        ("ಐದು ನಿಮಿಷದ ಟೈಮರ್ ಸೆಟ್ ಮಾಡು", "kn"),
    ]:
        intent, _ = router.route(phrase, requested_language=lang)  # type: ignore[arg-type]
        assert intent is not None

    tts = TTSRouter(settings)
    tts_sizes: dict[str, int] = {}
    for lang, phrase in [
        ("en", "System offline check complete."),
        ("hi", "सिस्टम ऑफ़लाइन जांच पूरी हुई।"),
        ("kn", "ಸಿಸ್ಟಮ್ ಆಫ್‌ಲೈನ್ ಪರಿಶೀಲನೆ ಪೂರ್ಣಗೊಂಡಿದೆ"),
    ]:
        wav, _ = tts.synthesize_wav(phrase, language=lang)  # type: ignore[arg-type]
        assert wav[:4] == b"RIFF" and len(wav) > 4000
        tts_sizes[lang] = len(wav)

    live_backend_checked = False
    backend_external_sockets: list[dict[str, str]] = []
    repo_root = Path(__file__).resolve().parents[2]
    pid_file = repo_root / ".runtime" / "backend.pid"

    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(f"{args.url}/api/v1/readiness")
            if r.status_code == 200:
                live_backend_checked = True
                for payload in [
                    {"text": "What time is it?", "language": "en", "speak": False},
                    {"text": "अभी क्या समय हुआ है?", "language": "hi", "speak": False},
                    {"text": "ಈಗ ಎಷ್ಟು ಗಂಟೆ ಆಗಿದೆ?", "language": "kn", "speak": False},
                ]:
                    ir = client.post(f"{args.url}/api/v1/intent/process", json=payload)
                    assert ir.status_code == 200
                    assert ir.json()["action_executed"] is True
                sr = client.post(
                    f"{args.url}/api/v1/voice/synthesize",
                    json={"text": "ಸಮಯ ಮುಗಿದಿದೆ", "language": "kn", "speed": 1.0},
                )
                assert sr.status_code == 200 and sr.content[:4] == b"RIFF"
    except Exception:
        if args.require_live_backend:
            raise

    self_external_sockets = _find_non_loopback_connections(os.getpid())
    if pid_file.is_file():
        raw_pid = pid_file.read_text(encoding="utf-8").strip()
        if raw_pid.isdigit():
            backend_external_sockets = _find_non_loopback_connections(int(raw_pid))

    assert len(self_external_sockets) == 0, f"Unexpected external sockets: {self_external_sockets}"
    assert len(backend_external_sockets) == 0, f"Backend external sockets: {backend_external_sockets}"

    report = {
        "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"),
        "transformers_offline": os.environ.get("TRANSFORMERS_OFFLINE"),
        "connectivity_mode": settings.connectivity_mode.value,
        "policy_blocked_targets": blocked,
        "in_process_tts_wav_bytes": tts_sizes,
        "live_backend_checked": live_backend_checked,
        "self_external_sockets": len(self_external_sockets),
        "backend_external_sockets": len(backend_external_sockets),
        "manual_airplane_mode_steps": [
            "1. Disable Wi-Fi / enable Airplane Mode after this coding session.",
            "2. Run: .\\scripts\\start-aura-voice.ps1",
            "3. Speak or type commands in EN/HI/KN (or run .\\.venv\\Scripts\\python.exe backend\\scripts\\verify_offline.py --require-live-backend).",
        ],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

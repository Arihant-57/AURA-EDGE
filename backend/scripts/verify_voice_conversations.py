"""Live end-to-end multilingual voice conversation verification against http://127.0.0.1:8000."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = REPO_ROOT / "models" / "eval_audio" / "phase1_voice_conversations.json"

_CONVERSATIONS: dict[str, list[dict[str, Any]]] = {
    "en": [
        {"phrase": "What time is it?", "expected_intent": "GetTime", "expected_exec": True},
        {
            "phrase": "Set a timer for two minutes for tea.",
            "expected_intent": "SetTimer",
            "expected_exec": True,
        },
        {
            "phrase": "How much time is left on the tea timer?",
            "expected_intent": "GetTimerRemaining",
            "expected_exec": True,
        },
        {"phrase": "Cancel the tea timer.", "expected_intent": "CancelTimer", "expected_exec": True},
        {"phrase": "Order pizza online.", "expected_intent": "Unsupported", "expected_exec": False},
    ],
    "hi": [
        {"phrase": "अभी क्या समय हुआ है?", "expected_intent": "GetTime", "expected_exec": True},
        {
            "phrase": "दो मिनट का चाय टाइमर लगाओ।",
            "expected_intent": "SetTimer",
            "expected_exec": True,
        },
        {"phrase": "कितने टाइमर चल रहे हैं?", "expected_intent": "ListTimers", "expected_exec": True},
        {"phrase": "सभी टाइमर रद्द करो।", "expected_intent": "CancelAllTimers", "expected_exec": True},
        {
            "phrase": "ऑनलाइन खाना ऑर्डर करो।",
            "expected_intent": "Unsupported",
            "expected_exec": False,
        },
    ],
    "kn": [
        {"phrase": "ಈಗ ಎಷ್ಟು ಗಂಟೆ ಆಗಿದೆ?", "expected_intent": "GetTime", "expected_exec": True},
        {
            "phrase": "ಎರಡು ನಿಮಿಷದ ಟೀ ಟೈಮರ್ ಸೆಟ್ ಮಾಡು.",
            "expected_intent": "SetTimer",
            "expected_exec": True,
        },
        {"phrase": "ಟೈಮರ್ ಪಟ್ಟಿ ತೋರಿಸು.", "expected_intent": "ListTimers", "expected_exec": True},
        {"phrase": "ಎಲ್ಲಾ ಟೈಮರ್ ರದ್ದು ಮಾಡು.", "expected_intent": "CancelAllTimers", "expected_exec": True},
        {
            "phrase": "ಆನ್‌ಲೈನ್‌ನಲ್ಲಿ ಆಹಾರ ಆರ್ಡರ್ ಮಾಡು.",
            "expected_intent": "Unsupported",
            "expected_exec": False,
        },
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify EN/HI/KN voice conversations via live API.")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    results: dict[str, Any] = {"languages": {}, "live_mic_ptt": {}}
    with httpx.Client(timeout=120.0) as client:
        readiness = client.get(f"{args.url}/api/v1/readiness")
        readiness.raise_for_status()
        assert readiness.json()["ready"] is True

        # Clear any pre-existing timers cleanly before conversation test
        for t in client.get(f"{args.url}/api/v1/timers").json():
            client.delete(f"{args.url}/api/v1/timers/{t['id']}")

        for lang, turns in _CONVERSATIONS.items():
            turn_logs: list[dict[str, Any]] = []
            for idx, step in enumerate(turns, start=1):
                # 1. Generate spoken prompt WAV offline via backend TTS
                tts_in = client.post(
                    f"{args.url}/api/v1/voice/synthesize",
                    json={"text": step["phrase"], "language": lang, "speed": 1.0},
                )
                tts_in.raise_for_status()
                assert tts_in.content[:4] == b"RIFF"

                # 2. Upload WAV to /voice/transcribe -> STT -> Router -> Tool -> State
                tr_resp = client.post(
                    f"{args.url}/api/v1/voice/transcribe",
                    files={"file": (f"{lang}_{idx}.wav", tts_in.content, "audio/wav")},
                    data={"language": lang, "speak": "false", "allow_llm_fallback": "false"},
                )
                tr_resp.raise_for_status()
                body = tr_resp.json()

                assert body["intent"] == step["expected_intent"], (
                    f"[{lang}#{idx}] Expected {step['expected_intent']}, got {body['intent']} "
                    f"(transcript={body.get('transcript')!r})"
                )
                assert body["action_executed"] is step["expected_exec"]

                # 3. Synthesize assistant reply back into spoken WAV offline
                tts_out = client.post(
                    f"{args.url}/api/v1/voice/synthesize",
                    json={"text": body["response_text"], "language": lang, "speed": 1.0},
                )
                tts_out.raise_for_status()
                assert tts_out.content[:4] == b"RIFF" and len(tts_out.content) > 4000

                turn_logs.append(
                    {
                        "step": idx,
                        "prompt": step["phrase"],
                        "transcript": body.get("transcript"),
                        "detected_language": body.get("detected_language"),
                        "intent": body.get("intent"),
                        "action_executed": body.get("action_executed"),
                        "response_text": body.get("response_text"),
                        "stt_ms": body.get("stt_ms"),
                        "router_ms": body.get("router_ms"),
                        "tool_ms": body.get("tool_ms"),
                        "reply_tts_ms": float(tts_out.headers.get("X-Aura-TTS-Latency-Ms", "0") or 0),
                        "reply_wav_bytes": len(tts_out.content),
                    }
                )
            results["languages"][lang] = turn_logs

        # Clean up any remaining timers and test real microphone PTT hardware cycle
        for t in client.get(f"{args.url}/api/v1/timers").json():
            client.delete(f"{args.url}/api/v1/timers/{t['id']}")

        ptt_start = client.post(f"{args.url}/api/v1/voice/ptt/start", json={"language": "en"})
        ptt_start.raise_for_status()
        time.sleep(0.4)
        ptt_stop = client.post(f"{args.url}/api/v1/voice/ptt/stop", json={"speak": False})
        ptt_stop.raise_for_status()
        results["live_mic_ptt"] = {
            "started": ptt_start.json().get("started"),
            "stop_state": ptt_stop.json().get("state"),
            "stop_status": ptt_stop.json().get("status"),
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

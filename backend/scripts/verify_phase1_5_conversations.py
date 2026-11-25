"""Live Phase 1.5 conversational, multilingual, streaming, and cache verification harness."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from websockets.sync.client import connect as ws_connect

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = REPO_ROOT / "models" / "eval_audio" / "phase1_5_report.json"


def _send_turn(
    client: httpx.Client,
    url: str,
    *,
    text: str,
    language: str = "auto",
    speak: bool = True,
) -> dict[str, Any]:
    r = client.post(
        f"{url}/api/v1/intent/process",
        json={"text": text, "language": language, "speak": speak, "allow_llm_fallback": True},
    )
    r.raise_for_status()
    return r.json()


def _send_voice_wav(
    client: httpx.Client,
    url: str,
    *,
    spoken_prompt: str,
    language: str,
    speak: bool = True,
) -> dict[str, Any]:
    wav_r = client.post(
        f"{url}/api/v1/voice/synthesize",
        json={"text": spoken_prompt, "language": language, "speed": 1.0},
    )
    wav_r.raise_for_status()
    tr = client.post(
        f"{url}/api/v1/voice/transcribe",
        files={"file": (f"{language}_conv.wav", wav_r.content, "audio/wav")},
        data={"language": language, "speak": str(speak).lower(), "allow_llm_fallback": "true"},
    )
    tr.raise_for_status()
    return tr.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Phase 1.5 live EN/HI/KN conversations.")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=OUT_PATH)
    parser.add_argument("--request-timeout-sec", type=float, default=60.0)
    parser.add_argument(
        "--speaker-playback",
        action="store_true",
        help="Play generated replies; disabled by default for unattended verification.",
    )
    args = parser.parse_args()

    report: dict[str, Any] = {
        "prewarm": {},
        "streaming_ws_verified": False,
        "tts_cache_check": {},
        "voice_turns": [],
        "conversation_turns": {},
        "followup_chain": [],
        "refusal_checks": [],
        "continuous_controls": {},
    }

    with httpx.Client(timeout=max(5.0, args.request_timeout_sec)) as client:
        rd = client.get(f"{args.url}/api/v1/readiness")
        rd.raise_for_status()
        assert rd.json()["ready"] is True

        # Clear state cleanly
        client.delete(f"{args.url}/api/v1/conversation/history")
        for t in client.get(f"{args.url}/api/v1/timers").json():
            client.delete(f"{args.url}/api/v1/timers/{t['id']}")

        # 1. Cold vs Prewarm Ollama + TTS check
        pw = client.post(f"{args.url}/api/v1/voice/prewarm", json={"prewarm_ollama": True}).json()
        report["prewarm"] = pw

        # 2. Verify TTS LRU cache hit vs miss directly via /voice/synthesize headers
        miss_r = client.post(
            f"{args.url}/api/v1/voice/synthesize",
            json={"text": "Phase one point five cache check sentence.", "language": "en", "speed": 1.0},
        )
        hit_r = client.post(
            f"{args.url}/api/v1/voice/synthesize",
            json={"text": "Phase one point five cache check sentence.", "language": "en", "speed": 1.0},
        )
        report["tts_cache_check"] = {
            "first_cached_hdr": miss_r.headers.get("X-Aura-TTS-Cached"),
            "first_synth_ms": float(miss_r.headers.get("X-Aura-TTS-Latency-Ms", "0") or 0),
            "second_cached_hdr": hit_r.headers.get("X-Aura-TTS-Cached"),
            "second_synth_ms": float(hit_r.headers.get("X-Aura-TTS-Latency-Ms", "0") or 0),
        }
        assert report["tts_cache_check"]["second_cached_hdr"] == "true"

        # 3. Verify real-time WebSocket token streaming + spoken voice turns across EN/HI/KN
        ws_url = args.url.replace("http://", "ws://") + "/api/v1/ws/live"
        ws_stop = threading.Event()
        ws_ready = threading.Event()
        seen_counts: dict[str, int] = {}

        def _ws_reader() -> None:
            with ws_connect(ws_url, open_timeout=5, close_timeout=2, max_queue=None) as ws:
                ws_ready.set()
                while not ws_stop.is_set():
                    try:
                        raw = ws.recv(timeout=0.2)
                        ev = json.loads(raw).get("type")
                        if ev:
                            seen_counts[ev] = seen_counts.get(ev, 0) + 1
                    except TimeoutError:
                        continue

        ws_thread = threading.Thread(target=_ws_reader, daemon=True)
        ws_thread.start()
        assert ws_ready.wait(timeout=5.0), "WebSocket failed to connect"
        time.sleep(0.2)

        # Spoken WAV greetings in EN/HI/KN.
        for lang, prompt, exp in [
            ("en", "Hello", "Greeting"),
            ("hi", "नमस्ते", "Greeting"),
            ("kn", "ನಮಸ್ಕಾರ", "Greeting"),
        ]:
            out = _send_voice_wav(
                client,
                args.url,
                spoken_prompt=prompt,
                language=lang,
                speak=args.speaker_playback,
            )
            report["voice_turns"].append(out)
            assert out["intent"] == exp, (
                f"Mismatch {lang}/{prompt}: exp={exp}, got={out['intent']}, "
                f"transcript={out.get('transcript')!r}, reply={out.get('response_text')!r}"
            )

        # Exact multilingual text verifies registered tool execution without
        # conflating it with synthetic TTS-to-STT transcription variance.
        for lang, prompt in [
            ("en", "What time is it?"),
            ("hi", "अभी कितने बजे हैं?"),
            ("kn", "ಈಗ ಎಷ್ಟು ಗಂಟೆ ಆಗಿದೆ?"),
        ]:
            out = _send_turn(client, args.url, text=prompt, language=lang, speak=False)
            assert out["intent"] == "GetTime" and out["action_executed"] is True, out
            report["voice_turns"].append(out)

        # Multilingual Qwen natural conversations (spoken in EN/HI/KN)
        conv_scenarios = {
            "en": [
                ("How are you doing today?", False),
                ("Why is the sky blue?", False),
            ],
            "hi": [
                ("तुम कैसे हो?", False),
                ("सूर्य पूर्व से क्यों उगता है?", False),
            ],
            "kn": [
                ("ನೀವು ಹೇಗಿದ್ದೀರಿ?", False),
                ("ಆಕಾಶ ಯಾಕೆ ನೀಲಿಯಾಗಿದೆ?", False),
            ],
        }
        for lang, prompts in conv_scenarios.items():
            client.delete(f"{args.url}/api/v1/conversation/history")
            lang_logs = []
            for p, spk in prompts:
                res = _send_turn(client, args.url, text=p, language=lang, speak=spk)
                lang_logs.append(res)
                assert res["intent"] == "Conversation"
                assert res["action_executed"] is True
                assert len(res["response_text"]) > 10
            report["conversation_turns"][lang] = lang_logs

        # 4. Bounded Session Context & Cross-Language Follow-ups
        client.delete(f"{args.url}/api/v1/conversation/history")
        followups = [
            ("Why do leaves turn yellow in autumn?", "en", False),
            ("Explain that again simply", "en", False),
            ("Make it shorter", "en", False),
            ("Repeat in Kannada", "auto", False),
            ("What did I just ask?", "en", False),
            ("Continue explaining", "en", False),
            ("Switch to Hindi", "auto", False),
            ("धन्यवाद", "auto", False),
        ]
        for prompt, lang, spk in followups:
            r = _send_turn(client, args.url, text=prompt, language=lang, speak=spk)
            report["followup_chain"].append(
                {
                    "prompt": prompt,
                    "intent": r["intent"],
                    "route_category": r["route_category"],
                    "response_language": r["response_language"],
                    "response_text": r["response_text"],
                    "llm_ttft_ms": r["llm_ttft_ms"],
                    "llm_ms": r["llm_ms"],
                    "tts_ms": r["tts_ms"],
                    "playback_ms": r["playback_ms"],
                    "ttfa_ms": r["ttfa_ms"],
                    "total_ms": r["latency_ms"],
                }
            )

        # 5. Restricted external action refusals even with LLM fallback enabled
        for p, lang in [
            ("Order pizza online", "en"),
            ("Transfer 5000 rupees to my friend", "en"),
            ("Unlock my front door", "en"),
            ("ಆನ್‌ಲೈನ್‌ನಲ್ಲಿ ಆಹಾರ ಆರ್ಡರ್ ಮಾಡು", "kn"),
        ]:
            r = _send_turn(client, args.url, text=p, language=lang, speak=False)
            assert r["intent"] == "Unsupported" and r["action_executed"] is False
            report["refusal_checks"].append({"prompt": p, "intent": r["intent"], "reply": r["response_text"]})

        # 6. Continuous hands-free mode start -> mute -> unmute -> cancel -> stop
        c_start = client.post(
            f"{args.url}/api/v1/voice/conversation/start",
            json={"language": "en", "speak": False, "mode": "continuous", "silence_timeout_sec": 15.0},
        ).json()
        time.sleep(0.3)
        m_on = client.put(f"{args.url}/api/v1/voice/mic/mute", json={"muted": True}).json()
        m_off = client.put(f"{args.url}/api/v1/voice/mic/mute", json={"muted": False}).json()
        c_stop = client.post(f"{args.url}/api/v1/voice/conversation/stop").json()
        report["continuous_controls"] = {
            "start": c_start,
            "mute": m_on,
            "unmute": m_off,
            "stop": c_stop,
        }

        time.sleep(0.3)
        ws_stop.set()
        ws_thread.join(timeout=3.0)
        assert "response_text_delta" in seen_counts and "transcript_ready" in seen_counts, (
            f"Missing streaming WS events: {seen_counts}"
        )
        report["streaming_ws_verified"] = True
        report["ws_event_counts"] = seen_counts

        # Final cleanup
        client.delete(f"{args.url}/api/v1/conversation/history")
        for t in client.get(f"{args.url}/api/v1/timers").json():
            client.delete(f"{args.url}/api/v1/timers/{t['id']}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved Phase 1.5 verification report to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

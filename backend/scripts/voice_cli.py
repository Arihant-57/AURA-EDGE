"""Interactive microphone & text CLI client for AURA EDGE Phase 1."""

from __future__ import annotations

import argparse
import sys

import httpx


def _print_banner(base_url: str, client: httpx.Client) -> None:
    try:
        ready = client.get(f"{base_url}/api/v1/readiness", timeout=5.0).json()
        status = client.get(f"{base_url}/api/v1/voice/status", timeout=5.0).json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"[ERROR] Cannot reach AURA EDGE backend at {base_url}: {exc}")
        sys.exit(1)

    db_info = ready.get("database", {})
    hw = ready.get("hardware", {})
    gpu = hw.get("gpu", {})
    ollama = hw.get("ollama", {})
    compute = status.get("compute", {})
    mics = [d["name"] for d in status.get("audio_devices", []) if d.get("max_input_channels", 0) > 0]
    default_mic = next(
        (d["name"] for d in status.get("audio_devices", []) if d.get("is_default_input")),
        mics[0] if mics else "None detected",
    )

    print("=" * 68)
    print(" AURA EDGE — Offline Multilingual Voice Assistant (Phase 1 CLI)")
    print("=" * 68)
    print(f" Backend Readiness : ready={ready.get('ready')} | DB={db_info.get('alembic_revision')} (connected={db_info.get('connected')})")
    print(f" Offline Guard     : HF_HUB_OFFLINE + strict_offline ({ready.get('connectivity_mode')})")
    print(f" Compute Placement : mode={compute.get('effective_mode')} | STT={compute.get('stt_device')}:{compute.get('stt_compute_type')} | GPU={gpu.get('name', 'CPU')}")
    print(f" STT Engine        : {status.get('stt_engine')} ({status.get('stt_model')}) | KN specialist={status.get('stt_kannada_specialist', 'none')}")
    print(f" TTS Engine        : {status.get('tts_engine')} | Languages={status.get('supported_languages')}")
    print(f" Ollama Local LLM  : reachable={ollama.get('reachable')} ({ollama.get('url')})")
    print(f" Microphone        : {default_mic} (detected={status.get('mic_detected')}) | Speaker={status.get('speaker_detected')}")
    print(f" Session Language  : {status.get('session_language')} (resolved={status.get('resolved_language')})")
    print("-" * 68)
    print(" Controls:")
    print("   [ENTER]            Start Push-to-Talk microphone recording, press [ENTER] again to stop & process")
    print("   <any text>         Send typed EN / HI / KN / Romanized command directly")
    print("   /lang en|hi|kn|auto   Switch active language hint")
    print("   /mode auto|cpu|gpu    Switch compute mode")
    print("   /timers | /cancel | /status | /quit")
    print("=" * 68)


def _print_turn(res: dict) -> None:
    print(f"\n  Transcript   : {res.get('transcript') or '(none)'}")
    print(f"  Language/Int : {res.get('detected_language')} | {res.get('intent')} ({res.get('intent_source')}, conf={res.get('confidence')})")
    print(f"  Executed     : {res.get('action_executed')} (status={res.get('status')})")
    print(f"  Assistant    : {res.get('response_text')}")
    print(
        f"  Latencies ms : total={res.get('latency_ms')} | stt={res.get('stt_ms')} "
        f"| router={res.get('router_ms')} | llm={res.get('llm_ms')} "
        f"| tool={res.get('tool_ms')} | tts={res.get('tts_ms')}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive Phase 1 Microphone CLI")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Backend loopback URL")
    parser.add_argument("--no-speak", action="store_true", help="Disable speaker playback on responses")
    args = parser.parse_args()
    speak = not args.no_speak

    with httpx.Client(timeout=120.0) as client:
        _print_banner(args.url, client)

        while True:
            try:
                cmd = input("AURA> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting CLI.")
                break

            if not cmd:
                try:
                    start_resp = client.post(f"{args.url}/api/v1/voice/ptt/start", json={})
                    if start_resp.status_code != 200:
                        print(f"  [MIC ERROR] {start_resp.text}")
                        continue
                    print("  [LISTENING] Speak now into your microphone... Press [ENTER] when finished.")
                    input()
                    stop_resp = client.post(
                        f"{args.url}/api/v1/voice/ptt/stop",
                        json={"speak": speak},
                    )
                    if stop_resp.status_code == 200:
                        _print_turn(stop_resp.json())
                    else:
                        print(f"  [ERROR] {stop_resp.status_code}: {stop_resp.text}")
                except KeyboardInterrupt:
                    client.post(f"{args.url}/api/v1/voice/cancel", json={})
                    print("\n  [CANCELLED]")
                continue

            if cmd in ("/quit", "/exit", "quit", "exit"):
                break
            if cmd == "/status":
                _print_banner(args.url, client)
                continue
            if cmd == "/timers":
                timers = client.get(f"{args.url}/api/v1/timers").json()
                print(f"  Timers ({len(timers)}): {timers}\n")
                continue
            if cmd == "/cancel":
                res = client.post(f"{args.url}/api/v1/voice/cancel", json={}).json()
                print(f"  Cancelled: {res}\n")
                continue
            if cmd.startswith("/lang "):
                lang = cmd.split(maxsplit=1)[1].strip()
                r = client.put(f"{args.url}/api/v1/voice/language", json={"language": lang})
                print(f"  Language -> {r.status_code}: {r.json()}\n")
                continue
            if cmd.startswith("/mode "):
                mode = cmd.split(maxsplit=1)[1].strip()
                r = client.put(f"{args.url}/api/v1/voice/compute-mode", json={"compute_mode": mode})
                print(f"  Compute -> {r.status_code}: {r.json()}\n")
                continue

            resp = client.post(
                f"{args.url}/api/v1/intent/process",
                json={"text": cmd, "speak": speak},
            )
            if resp.status_code == 200:
                _print_turn(resp.json())
            else:
                print(f"  [ERROR] {resp.status_code}: {resp.text}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

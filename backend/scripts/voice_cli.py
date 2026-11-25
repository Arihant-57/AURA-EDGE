"""Interactive Continuous & Push-to-Talk Voice CLI for AURA EDGE Phase 1.5."""

from __future__ import annotations

import argparse
import json
import sys
import threading

import httpx
from websockets.sync.client import connect as ws_connect


def _print_banner(base_url: str, client: httpx.Client, *, mode: str) -> None:
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

    print("=" * 72)
    print(" AURA EDGE — Natural Multilingual Conversational Assistant (Phase 1.5)")
    print("=" * 72)
    print(f" Backend Readiness : ready={ready.get('ready')} | DB={db_info.get('alembic_revision')} | Ollama={ollama.get('reachable')}")
    print(f" Compute Placement : mode={compute.get('effective_mode')} | STT={compute.get('stt_device')}:{compute.get('stt_compute_type')} | GPU={gpu.get('name', 'CPU')}")
    print(f" STT / TTS         : {status.get('stt_model')} + KN={status.get('stt_kannada_specialist')} | {status.get('tts_engine')}")
    print(f" Microphone        : {default_mic} (detected={status.get('mic_detected')}, muted={status.get('mic_muted')})")
    print(f" Conversation Mode : {mode} (lang={status.get('session_language')}, history={status.get('history_length', 0)} msgs)")
    print("-" * 72)
    print(" Controls:")
    print("   [Hands-Free]  Just speak into your microphone (auto VAD start/end/reply/resume)")
    print("   [ENTER]       (In PTT mode) press ENTER to record, ENTER again to send")
    print("   <any text>    Type English, Hindi, or Kannada message at any time")
    print("   /handsfree | /ptt | /stop | /mute | /unmute | /history | /clear")
    print("   /lang en|hi|kn|auto | /mode auto|cpu|gpu | /timers | /cancel | /status | /quit")
    print("=" * 72)


def _print_turn(res: dict) -> None:
    print(f"\n  Transcript   : {res.get('transcript') or '(none)'}")
    print(
        f"  Route/Intent : [{res.get('route_category')}] {res.get('intent')} "
        f"({res.get('intent_source')}, lang={res.get('detected_language')}->{res.get('response_language')})"
    )
    print(f"  Executed     : {res.get('action_executed')} (status={res.get('status')})")
    print(f"  AURA         : {res.get('response_text')}")
    print(
        f"  Latencies ms : stt={res.get('stt_ms')} | llm_ttft={res.get('llm_ttft_ms')} "
        f"| llm_gen={res.get('llm_ms')} | tool={res.get('tool_ms')} "
        f"| tts_synth={res.get('tts_ms')} (hit={res.get('tts_cache_hit')}) "
        f"| playback={res.get('playback_ms')} | ttfa={res.get('ttfa_ms')} | total={res.get('latency_ms')}\n"
    )


def _ws_event_worker(ws_url: str, stop_ev: threading.Event, stream_active: list[bool]) -> None:
    while not stop_ev.is_set():
        try:
            with ws_connect(ws_url, open_timeout=3, close_timeout=2) as ws:
                while not stop_ev.is_set():
                    try:
                        raw = ws.recv(timeout=0.5)
                    except TimeoutError:
                        continue
                    frame = json.loads(raw)
                    ev = frame.get("type")
                    payload = frame.get("payload") or {}
                    if ev == "transcript_ready":
                        print(
                            f"\n  [YOU ({payload.get('language')}, {payload.get('stt_ms')}ms)]: "
                            f"{payload.get('transcript')}"
                        )
                        print("  [AURA]: ", end="", flush=True)
                        stream_active[0] = True
                    elif ev == "response_text_delta":
                        print(payload.get("token", ""), end="", flush=True)
                    elif ev == "response_completed":
                        if stream_active[0]:
                            print(flush=True)
                            stream_active[0] = False
                        else:
                            print(f"\n  [AURA]: {payload.get('response_text')}")
                        if payload.get("tts_ms") is not None or payload.get("ttfa_ms") is not None:
                            print(
                                f"  [LATENCY] llm_ttft={payload.get('llm_ttft_ms')}ms "
                                f"tts_synth={payload.get('tts_ms')}ms "
                                f"playback={payload.get('playback_ms')}ms "
                                f"ttfa={payload.get('ttfa_ms')}ms"
                            )
                        print("AURA> ", end="", flush=True)
                    elif ev == "conversation_mode_change" and not payload.get("active"):
                        print(
                            f"\n  [CONVERSATION STOPPED: {payload.get('reason', 'stopped')}]\nAURA> ",
                            end="",
                            flush=True,
                        )
                    elif ev == "timer_completed":
                        t = payload.get("timer") or {}
                        print(f"\n  [TIMER COMPLETED] {t.get('label') or t.get('id')}\nAURA> ", end="", flush=True)
        except (OSError, ValueError, RuntimeError):
            if not stop_ev.is_set():
                stop_ev.wait(1.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive Phase 1.5 Voice Assistant CLI")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Backend loopback URL")
    parser.add_argument("--mode", choices=["continuous", "ptt"], default="continuous")
    parser.add_argument("--language", choices=["auto", "en", "hi", "kn"], default=None)
    parser.add_argument("--no-speak", action="store_true", help="Disable speaker playback")
    args = parser.parse_args()
    speak = not args.no_speak
    active_mode = args.mode

    ws_url = args.url.replace("http://", "ws://").replace("https://", "wss://") + "/api/v1/ws/live"
    stop_ws = threading.Event()
    stream_flag = [False]

    with httpx.Client(timeout=120.0) as client:
        client.post(f"{args.url}/api/v1/voice/prewarm", json={"prewarm_ollama": True})
        if args.language is not None:
            client.put(f"{args.url}/api/v1/voice/language", json={"language": args.language})

        _print_banner(args.url, client, mode=active_mode)

        ws_thread = threading.Thread(
            target=_ws_event_worker,
            args=(ws_url, stop_ws, stream_flag),
            daemon=True,
        )
        ws_thread.start()

        if active_mode == "continuous":
            r = client.post(
                f"{args.url}/api/v1/voice/conversation/start",
                json={"language": args.language, "speak": speak, "mode": "continuous"},
            )
            if r.status_code == 200:
                print("  [HANDS-FREE LISTENING] Speak naturally anytime — type commands or /quit below.\n")
            else:
                print(f"  [WARN] Continuous mic mode unavailable ({r.text}); staying in PTT/text mode.\n")
                active_mode = "ptt"

        try:
            while True:
                try:
                    cmd = input("AURA> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nExiting CLI.")
                    break

                if not cmd:
                    if active_mode == "continuous":
                        continue
                    try:
                        start_resp = client.post(f"{args.url}/api/v1/voice/ptt/start", json={})
                        if start_resp.status_code != 200:
                            print(f"  [MIC ERROR] {start_resp.text}")
                            continue
                        print("  [LISTENING] Speak now... Press [ENTER] when finished.")
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
                if cmd == "/handsfree":
                    active_mode = "continuous"
                    r = client.post(
                        f"{args.url}/api/v1/voice/conversation/start",
                        json={"speak": speak, "mode": "continuous"},
                    )
                    print(f"  [CONTINUOUS MODE]: {r.status_code} {r.json()}\n")
                    continue
                if cmd == "/ptt":
                    active_mode = "ptt"
                    client.post(f"{args.url}/api/v1/voice/conversation/stop")
                    print("  [PTT MODE] Press ENTER to record or type a message.\n")
                    continue
                if cmd == "/stop":
                    r = client.post(f"{args.url}/api/v1/voice/conversation/stop")
                    active_mode = "ptt"
                    print(f"  [STOPPED CONVERSATION]: {r.json()}\n")
                    continue
                if cmd == "/mute":
                    print(f"  [MIC MUTED]: {client.put(f'{args.url}/api/v1/voice/mic/mute', json={'muted': True}).json()}\n")
                    continue
                if cmd == "/unmute":
                    print(f"  [MIC UNMUTED]: {client.put(f'{args.url}/api/v1/voice/mic/mute', json={'muted': False}).json()}\n")
                    continue
                if cmd == "/history":
                    h = client.get(f"{args.url}/api/v1/conversation/history").json()
                    for m in h.get("messages", []):
                        print(f"    {m['role'].upper()} ({m['language']}): {m['content']}")
                    print(f"  (total={h.get('count', 0)})\n")
                    continue
                if cmd == "/clear":
                    print(f"  [HISTORY CLEARED]: {client.delete(f'{args.url}/api/v1/conversation/history').json()}\n")
                    continue
                if cmd == "/status":
                    _print_banner(args.url, client, mode=active_mode)
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
        finally:
            stop_ws.set()
            if active_mode == "continuous":
                client.post(f"{args.url}/api/v1/voice/conversation/stop")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

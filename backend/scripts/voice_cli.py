"""Interactive Continuous & Push-to-Talk Voice CLI for AURA EDGE."""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from collections import OrderedDict
from typing import Any

import httpx
from websockets.sync.client import connect as ws_connect

_HAS_WORD_CHAR_RE = re.compile(r"[A-Za-z0-9\u0900-\u097F\u0C80-\u0CFF]", re.UNICODE)
_VALID_LANGS = {"en", "hi", "kn", "auto"}
_VALID_MODES = {"auto", "cpu", "gpu"}
_SIMPLE_SLASH_COMMANDS = {
    "/help": "help",
    "/handsfree": "handsfree",
    "/ptt": "ptt",
    "/stop": "stop",
    "/mute": "mute",
    "/unmute": "unmute",
    "/history": "history",
    "/clear": "clear",
    "/status": "status",
    "/timers": "timers",
    "/cancel": "cancel",
    "/quit": "quit",
    "/exit": "quit",
}


class TurnOutputDeduplicator:
    """Thread-safe deduplicator preventing double WS/HTTP printing and repeated prompts."""

    def __init__(self, *, max_turns: int = 128) -> None:
        self._max_turns = max(16, int(max_turns))
        self._lock = threading.Lock()
        self._rendered: OrderedDict[str, bool] = OrderedDict()
        self._streamed: OrderedDict[str, bool] = OrderedDict()
        self._http_in_flight = threading.Event()

    def begin_http_turn(self) -> None:
        self._http_in_flight.set()

    def end_http_turn(self) -> None:
        self._http_in_flight.clear()

    def should_emit_ws_prompt(self) -> bool:
        return not self._http_in_flight.is_set()

    def mark_streamed(self, turn_id: str | None) -> None:
        if not turn_id:
            return
        with self._lock:
            self._streamed[turn_id] = True
            self._streamed.move_to_end(turn_id)
            while len(self._streamed) > self._max_turns:
                self._streamed.popitem(last=False)

    def was_streamed(self, turn_id: str | None) -> bool:
        if not turn_id:
            return False
        with self._lock:
            return turn_id in self._streamed

    def claim_render(self, turn_id: str | None) -> bool:
        """Return True only once per unique turn_id."""
        if not turn_id:
            return True
        with self._lock:
            if turn_id in self._rendered:
                return False
            self._rendered[turn_id] = True
            while len(self._rendered) > self._max_turns:
                self._rendered.popitem(last=False)
            return True


def parse_cli_command(raw: str) -> dict[str, Any]:
    """Validate interactive CLI input strictly before any HTTP or LLM call."""
    cmd = (raw or "").strip()
    if not cmd:
        return {"kind": "empty"}

    if cmd.lower() in ("quit", "exit"):
        return {"kind": "quit"}

    if cmd.startswith("/"):
        if cmd == "/":
            return {
                "kind": "error",
                "message": "Incomplete command '/'. Type /help for available commands.",
            }
        parts = cmd.split(maxsplit=1)
        head = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if head in _SIMPLE_SLASH_COMMANDS:
            return {"kind": _SIMPLE_SLASH_COMMANDS[head]}

        if head == "/lang":
            if not arg or arg.lower() not in _VALID_LANGS:
                return {"kind": "error", "message": "Usage: /lang en|hi|kn|auto"}
            return {"kind": "lang", "value": arg.lower()}

        if head == "/mode":
            if not arg or arg.lower() not in _VALID_MODES:
                return {"kind": "error", "message": "Usage: /mode auto|cpu|gpu"}
            return {"kind": "mode", "value": arg.lower()}

        return {
            "kind": "error",
            "message": f"Unknown command '{head}'. Type /help for valid commands.",
        }

    if not _HAS_WORD_CHAR_RE.search(cmd):
        return {
            "kind": "error",
            "message": "Please enter a spoken/typed message or a valid command (/help).",
        }

    return {"kind": "text", "value": cmd}


def _print_help() -> None:
    print(
        "  Commands:\n"
        "    /lang en|hi|kn|auto   Switch active conversation language\n"
        "    /mode auto|cpu|gpu    Select compute placement\n"
        "    /handsfree | /ptt     Toggle continuous listening or Push-to-Talk\n"
        "    /stop | /mute | /unmute | /cancel\n"
        "    /history | /clear | /timers | /status | /help | /quit\n"
    )


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
    print(" AURA EDGE — Natural Multilingual Conversational Assistant")
    print("=" * 72)
    print(f" Backend Readiness : ready={ready.get('ready')} | DB={db_info.get('alembic_revision')} | Ollama={ollama.get('reachable')}")
    print(f" Compute Placement : mode={compute.get('effective_mode')} | STT={compute.get('stt_device')}:{compute.get('stt_compute_type')} | GPU={gpu.get('name', 'CPU')}")
    print(f" STT / TTS         : {status.get('stt_model')} + KN={status.get('stt_kannada_specialist')} | {status.get('tts_engine')}")
    print(f" Microphone        : {default_mic} (detected={status.get('mic_detected')}, muted={status.get('mic_muted')})")
    print(f" Conversation Mode : {mode} (lang={status.get('session_language')}, history={status.get('history_length', 0)} msgs)")
    print("-" * 72)
    print(" Controls:")
    print("   [Hands-Free]  /handsfree to speak naturally (half-duplex auto VAD)")
    print("   [ENTER]       (In PTT mode) press ENTER to record, ENTER again to send")
    print("   <any text>    Type English, Hindi, or Kannada message at any time")
    print("   /lang en|hi|kn|auto | /mode auto|cpu|gpu | /help | /quit")
    print("=" * 72)


def _print_turn(res: dict[str, Any], dedup: TurnOutputDeduplicator, out_lock: threading.Lock) -> None:
    turn_id = res.get("turn_id")
    if not dedup.claim_render(turn_id):
        return
    with out_lock:
        if res.get("transcript"):
            print(f"\n  Transcript   : {res.get('transcript')}")
        print(
            f"  Route/Intent : [{res.get('route_category')}] {res.get('intent')} "
            f"({res.get('intent_source')}, lang={res.get('detected_language')}->{res.get('response_language')})"
        )
        print(f"  Executed     : {res.get('action_executed')} (status={res.get('status')})")
        print(f"  AURA         : {res.get('response_text')}")
        print(
            f"  Latencies ms : stt={res.get('stt_ms')} | llm_ttft={res.get('llm_ttft_ms')} "
            f"| tts_synth={res.get('tts_ms')} (hit={res.get('tts_cache_hit')}) "
            f"| startup={res.get('audio_startup_ms')} | playback={res.get('playback_ms')} "
            f"| ttfa={res.get('ttfa_ms')} | total={res.get('latency_ms')}\n",
            flush=True,
        )


def _ws_event_worker(
    ws_url: str,
    stop_ev: threading.Event,
    dedup: TurnOutputDeduplicator,
    out_lock: threading.Lock,
) -> None:
    stream_turn_id: str | None = None
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
                    turn_id = payload.get("turn_id")

                    if ev == "transcript_ready":
                        with out_lock:
                            print(
                                f"\n  [YOU ({payload.get('language')}, {payload.get('stt_ms')}ms)]: "
                                f"{payload.get('transcript')}",
                                flush=True,
                            )
                    elif ev == "response_text_delta":
                        # Only stream live deltas asynchronously when not also waiting synchronously on HTTP
                        if not dedup.should_emit_ws_prompt():
                            continue
                        with out_lock:
                            if turn_id and stream_turn_id != turn_id:
                                print("  [AURA]: ", end="", flush=True)
                                stream_turn_id = turn_id
                                dedup.mark_streamed(turn_id)
                            print(payload.get("token", ""), end="", flush=True)
                    elif ev == "action_verified":
                        # Action metadata arrives before TTS finishes; response_completed prints the final reply.
                        if dedup.should_emit_ws_prompt() and payload.get("status") == "verified":
                            with out_lock:
                                print(f"  [ACTION] {payload.get('action')}: verified", flush=True)
                    elif ev == "clarification_required":
                        # Rendered once on response_completed after playback completes to prevent duplicates.
                        continue
                    elif ev == "error":
                        if dedup.should_emit_ws_prompt() and dedup.claim_render(turn_id):
                            with out_lock:
                                print(f"\n  [ERROR]: {payload.get('message')}", flush=True)
                                print("AURA> ", end="", flush=True)
                    elif ev == "response_completed":
                        if payload.get("silent") or (
                            not payload.get("response_text")
                            and payload.get("status") == "ignored_silence"
                        ):
                            stream_turn_id = None
                            continue
                        if dedup.was_streamed(turn_id):
                            dedup.claim_render(turn_id)
                            with out_lock:
                                print(flush=True)
                                if payload.get("tts_ms") is not None or payload.get("ttfa_ms") is not None:
                                    print(
                                        f"  [LATENCY] llm_ttft={payload.get('llm_ttft_ms')}ms "
                                        f"tts_synth={payload.get('tts_ms')}ms "
                                        f"startup={payload.get('audio_startup_ms')}ms "
                                        f"playback={payload.get('playback_ms')}ms "
                                        f"ttfa={payload.get('ttfa_ms')}ms",
                                        flush=True,
                                    )
                            stream_turn_id = None
                        elif dedup.should_emit_ws_prompt() and dedup.claim_render(turn_id):
                            with out_lock:
                                print(
                                    f"\n  [AURA ({payload.get('language')})]: {payload.get('response_text')}",
                                    flush=True,
                                )
                                if payload.get("tts_ms") is not None or payload.get("ttfa_ms") is not None:
                                    print(
                                        f"  [LATENCY] tts_synth={payload.get('tts_ms')}ms "
                                        f"startup={payload.get('audio_startup_ms')}ms "
                                        f"playback={payload.get('playback_ms')}ms "
                                        f"ttfa={payload.get('ttfa_ms')}ms",
                                        flush=True,
                                    )
                        if dedup.should_emit_ws_prompt():
                            with out_lock:
                                print("AURA> ", end="", flush=True)
                    elif ev == "conversation_mode_change" and not payload.get("active"):
                        if dedup.should_emit_ws_prompt():
                            with out_lock:
                                print(
                                    f"\n  [CONVERSATION STOPPED: {payload.get('reason', 'stopped')}]\nAURA> ",
                                    end="",
                                    flush=True,
                                )
                    elif ev in ("timer_completed", "timer_missed"):
                        t = payload.get("timer") or {}
                        tag = "TIMER MISSED" if ev == "timer_missed" else "TIMER COMPLETED"
                        with out_lock:
                            msg = f"\n  [{tag}] {t.get('label') or t.get('id')}"
                            if dedup.should_emit_ws_prompt():
                                msg += "\nAURA> "
                            print(msg, end="" if dedup.should_emit_ws_prompt() else "\n", flush=True)
        except (OSError, ValueError, RuntimeError):
            if not stop_ev.is_set():
                stop_ev.wait(1.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive Voice Assistant CLI")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Backend loopback URL")
    parser.add_argument("--mode", choices=["continuous", "ptt"], default="ptt")
    parser.add_argument("--language", choices=["auto", "en", "hi", "kn"], default=None)
    parser.add_argument("--no-speak", action="store_true", help="Disable speaker playback")
    args = parser.parse_args()
    speak = not args.no_speak
    active_mode = args.mode

    ws_url = args.url.replace("http://", "ws://").replace("https://", "wss://") + "/api/v1/ws/live"
    stop_ws = threading.Event()
    dedup = TurnOutputDeduplicator()
    out_lock = threading.Lock()

    with httpx.Client(timeout=120.0) as client:
        client.post(f"{args.url}/api/v1/voice/prewarm", json={"prewarm_ollama": True})
        if args.language is not None:
            client.put(f"{args.url}/api/v1/voice/language", json={"language": args.language})

        _print_banner(args.url, client, mode=active_mode)

        ws_thread = threading.Thread(
            target=_ws_event_worker,
            args=(ws_url, stop_ws, dedup, out_lock),
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
                    raw_cmd = input("AURA> ")
                except (EOFError, KeyboardInterrupt):
                    print("\nExiting CLI.")
                    break

                parsed = parse_cli_command(raw_cmd)
                kind = parsed["kind"]

                if kind == "empty":
                    if active_mode == "continuous":
                        continue
                    try:
                        start_resp = client.post(f"{args.url}/api/v1/voice/ptt/start", json={})
                        if start_resp.status_code != 200:
                            print(f"  [MIC ERROR] {start_resp.text}")
                            continue
                        print("  [LISTENING] Speak now... Press [ENTER] when finished.")
                        input()
                        dedup.begin_http_turn()
                        try:
                            stop_resp = client.post(
                                f"{args.url}/api/v1/voice/ptt/stop",
                                json={"speak": speak},
                            )
                        finally:
                            dedup.end_http_turn()
                        if stop_resp.status_code == 200:
                            _print_turn(stop_resp.json(), dedup, out_lock)
                        else:
                            print(f"  [ERROR] {stop_resp.status_code}: {stop_resp.text}")
                    except KeyboardInterrupt:
                        client.post(f"{args.url}/api/v1/voice/cancel", json={})
                        print("\n  [CANCELLED]")
                    continue

                if kind == "quit":
                    break
                if kind == "help":
                    _print_help()
                    continue
                if kind == "error":
                    print(f"  {parsed['message']}")
                    continue
                if kind == "handsfree":
                    active_mode = "continuous"
                    r = client.post(
                        f"{args.url}/api/v1/voice/conversation/start",
                        json={"language": args.language, "speak": speak, "mode": "continuous"},
                    )
                    print(f"  [CONTINUOUS MODE]: {r.status_code} {r.json()}\n")
                    continue
                if kind == "ptt":
                    active_mode = "ptt"
                    client.post(f"{args.url}/api/v1/voice/conversation/stop")
                    print("  [PTT MODE] Press ENTER to record or type a message.\n")
                    continue
                if kind == "stop":
                    r = client.post(f"{args.url}/api/v1/voice/conversation/stop")
                    active_mode = "ptt"
                    print(f"  [STOPPED CONVERSATION]: {r.json()}\n")
                    continue
                if kind == "mute":
                    print(f"  [MIC MUTED]: {client.put(f'{args.url}/api/v1/voice/mic/mute', json={'muted': True}).json()}\n")
                    continue
                if kind == "unmute":
                    print(f"  [MIC UNMUTED]: {client.put(f'{args.url}/api/v1/voice/mic/mute', json={'muted': False}).json()}\n")
                    continue
                if kind == "history":
                    h = client.get(f"{args.url}/api/v1/conversation/history").json()
                    for m in h.get("messages", []):
                        print(f"    {m['role'].upper()} ({m['language']}): {m['content']}")
                    print(f"  (total={h.get('count', 0)})\n")
                    continue
                if kind == "clear":
                    print(f"  [HISTORY CLEARED]: {client.delete(f'{args.url}/api/v1/conversation/history').json()}\n")
                    continue
                if kind == "status":
                    _print_banner(args.url, client, mode=active_mode)
                    continue
                if kind == "timers":
                    timers = client.get(f"{args.url}/api/v1/timers").json()
                    print(f"  Timers ({len(timers)}): {timers}\n")
                    continue
                if kind == "cancel":
                    res = client.post(f"{args.url}/api/v1/voice/cancel", json={}).json()
                    print(f"  Cancelled: {res}\n")
                    continue
                if kind == "lang":
                    lang = parsed["value"]
                    if active_mode == "continuous":
                        client.post(f"{args.url}/api/v1/voice/conversation/stop").raise_for_status()
                        active_mode = "ptt"
                    r = client.put(f"{args.url}/api/v1/voice/language", json={"language": lang})
                    if r.is_success:
                        args.language = lang
                    print(f"  Language -> {r.status_code}: {r.json()}\n")
                    continue
                if kind == "mode":
                    mode = parsed["value"]
                    r = client.put(f"{args.url}/api/v1/voice/compute-mode", json={"compute_mode": mode})
                    print(f"  Compute -> {r.status_code}: {r.json()}\n")
                    continue

                if active_mode == "continuous":
                    client.post(f"{args.url}/api/v1/voice/conversation/stop").raise_for_status()
                    active_mode = "ptt"
                    print("  Hands-free stopped for typed input. Use /handsfree to resume.")

                payload: dict[str, Any] = {"text": parsed["value"], "speak": speak}
                if args.language is not None:
                    payload["language"] = args.language

                dedup.begin_http_turn()
                try:
                    resp = client.post(f"{args.url}/api/v1/intent/process", json=payload)
                finally:
                    dedup.end_http_turn()
                if resp.status_code == 200:
                    _print_turn(resp.json(), dedup, out_lock)
                else:
                    print(f"  [ERROR] {resp.status_code}: {resp.text}\n")
        finally:
            stop_ws.set()
            if active_mode == "continuous":
                client.post(f"{args.url}/api/v1/voice/conversation/stop")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

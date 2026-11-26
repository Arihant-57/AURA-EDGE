"""Replay public test phrases and locally synthesized WAVs against a disposable AURA instance."""

import argparse
import io
import json
import random
import time
import wave
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx
from aura_edge.intelligence.conversation import validate_response_language
from websockets.sync.client import connect

from backend.scripts.benchmark_stabilization import compute_wer_cer

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "qa" / "acceptance_cases.json").read_text(encoding="utf-8"))
TIMING = (
    "stt_ms", "router_ms", "llm_ttft_ms", "tool_ms", "tts_ms", "time_to_first_audio_output_ms",
    "total_turn_duration_ms", "playback_duration_ms",
)
MIXED_LANGUAGE = {"hinglish": "hi", "kanglish": "kn"}
NON_MUTATING = {"greeting", "conversation", "negative"}
QUERY_INTENTS = {"GetDate", "GetTime", "ListReminders", "ListTasks", "ListCalendarEvents", "ListGroceryList", "RetrieveMemory"}


def request(client, path, method="GET", **kwargs):
    return client.request(method, f"/api/v1{path}", **kwargs).raise_for_status()


def json_api(client, path, method="GET", **kwargs):
    return request(client, path, method, **kwargs).json()


def wav_pcm(samples):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", ""))
        audio.writeframes(b"".join(value.to_bytes(2, "little", signed=True) for value in samples))
    return buffer.getvalue()


def synthesize(client, case):
    language = MIXED_LANGUAGE.get(case["language"], case["language"])
    return request(client, "/voice/synthesize", "POST", json={"text": case["text"], "language": language}).content


def validate_turn(client, case, actual, before):
    expected_intent = case.get("intent")
    category = case["category"]
    action = actual.get("action_executed", False)
    if category == "negative":
        return {"safe": not action and actual.get("outcome") in {"clarification", "refusal", "runtime_failure"}}
    if category == "silence":
        return {"safe": not action and actual.get("status") == "ignored_silence"}
    if category in NON_MUTATING:
        return {"outcome": actual.get("outcome") == "success", "no_action": not action, "reply": bool(actual.get("response_text"))}
    data = actual.get("data") or {}
    sub_results = data.get("sub_results", [])
    if sub_results:
        data = next((result["data"] for result in sub_results if result["action"] == expected_intent), {})
    checks = {"intent": actual.get("intent") == expected_intent, "outcome": actual.get("outcome") == "success",
              "verified": action and actual.get("status") == "verified"}
    criterion = case.get("check")
    if isinstance(criterion, list):
        device, key, value = criterion
        observed = json_api(client, f"/smart-home/devices/{device}")["state"].get(key)
        checks["persisted_state"] = checks["verified"] and (
            observed == value and (not isinstance(value, bool) or isinstance(observed, bool))
        )
    elif isinstance(criterion, int):
        timer = data.get("timer") or {}
        checks["duration"] = timer.get("duration_seconds") == criterion
        if timer.get("id"):
            checks["persisted_timer"] = json_api(client, f"/timers/{timer['id']}").get("duration_seconds") == criterion
    elif expected_intent == "GetTime":
        raw = data.get("iso_time")
        checks["host_clock"] = bool(raw) and before - 2 <= datetime.fromisoformat(raw).timestamp() <= time.time() + 2
    elif expected_intent == "GetDate":
        checks["host_clock"] = data.get("iso_date") in {
            datetime.fromtimestamp(before).date().isoformat(), datetime.now().date().isoformat(),
        }
    elif expected_intent not in QUERY_INTENTS:
        checks["tool_result"] = bool(data)
    return checks


def replay(client, case, mode, *, pcm=None, speak=False, run_type=None):
    before = time.time()
    exact_language = case["language"] if case["language"] in {"en", "hi", "kn"} else "auto"
    # Typed monolingual cases set reply language. Audio always exercises automatic STT language detection.
    prompt = case["text"]
    if pcm is None:
        answer = json_api(client, "/intent/process", "POST", json={
            "text": prompt, "language": exact_language, "speak": speak,
            "allow_llm_fallback": case["category"] in {"conversation", "negative"},
        })
    else:
        answer = json_api(client, "/voice/transcribe", "POST", files={"file": ("qa.wav", pcm, "audio/wav")}, data={
            "language": "auto", "recognition_mode": "multilingual", "speak": str(speak).lower(), "allow_llm_fallback": "false",
        })
    checks = validate_turn(client, case, answer, before)
    observed = {key: answer.get(key) for key in (
        "intent", "outcome", "status", "route_category", "action_executed", "transcript",
        "response_text", "detected_language", "response_language", "spoken_text", "error",
    )}
    observed["timings"] = {key: answer.get(key) for key in TIMING}
    stamps = answer.get("stage_timestamps") or {}
    observed["non_silent_output_callback"] = stamps.get("first_non_silent_output_callback") is not None
    observed["playback_completed"] = stamps.get("playback_completion") is not None
    if observed["response_text"] and observed["response_language"] in {"en", "hi", "kn"}:
        observed["reply_language_valid"] = validate_response_language(
            observed["response_text"], observed["response_language"]
        )[0]
    if pcm is not None and prompt:
        observed["wer"], observed["cer"] = compute_wer_cer(prompt, answer.get("transcript") or "")
        observed["language_identification_match"] = observed["detected_language"] == MIXED_LANGUAGE.get(case["language"], case["language"])
    if speak:
        checks["audio_output"] = observed["non_silent_output_callback"] and observed["playback_completed"]
    return {"id": case["id"], "expected": case, "input": "synthetic_audio" if pcm is not None else "text",
            "phase": run_type, "compute": mode, "actual": observed, "checks": checks, "passed": all(checks.values())}


def exercise_devices(client, port):
    devices = json_api(client, "/smart-home/devices")
    rooms = json_api(client, "/smart-home/rooms")
    entries = [{"id": "inventory", "expected": {"devices": 19, "rooms": 5},
                "actual": {"devices": len(devices), "rooms": len(rooms)}, "passed": len(devices) == 19 and len(rooms) == 5}]
    with connect(f"ws://127.0.0.1:{port}/api/v1/ws/live", open_timeout=5, proxy=None) as socket:
        for device in devices:
            route = "/smart-home/devices/" + device["id"]
            for action, spec in device["capabilities"].items():
                if action == "activity":
                    values = [value for value in ("start", "pause", "resume", "dock" if "dock" in spec["enum"] else "cancel") if value in spec["enum"]]
                elif spec.get("type") == "bool" or "type" in spec and spec["type"] == "boolean":
                    values = [not device["state"][action]]
                elif "enum" in spec:
                    values = [next((value for value in spec["enum"] if value != device["state"].get(action)), spec["enum"][0])]
                elif "min" in spec:
                    values = [spec["min"] if device["state"].get(action) != spec["min"] else spec["max"]]
                else:
                    continue
                for value in values:
                    try:
                        response = json_api(client, route + "/command", "POST", json={"action": action, "value": value, "source": "ui"})
                    except httpx.HTTPError as exc:
                        entries.append({"id": device["id"], "expected": {"action": action, "value": value},
                                        "actual": {"error": str(exc)}, "passed": False})
                        continue
                    stored = json_api(client, route)["state"]
                    changed = False
                    until = time.monotonic() + 2
                    while time.monotonic() < until:
                        try:
                            event = json.loads(socket.recv(timeout=max(.01, until - time.monotonic())))
                        except TimeoutError:
                            break
                        changed = event["type"] == "device_state_changed" and event["payload"].get("device", {}).get("id") == device["id"]
                        if changed:
                            break
                    entries.append({"id": device["id"], "expected": {"action": action, "value": value},
                                    "actual": {"status": response["status"], "state": stored, "websocket": changed},
                                    "passed": response["status"] == "verified" and changed
                                    and (stored.get(action) == value if action != "activity" else "activity" in stored)})
            if not device["capabilities"]:
                state = json_api(client, route)["state"]
                entries.append({"id": device["id"], "expected": {"read_only": True},
                                "actual": state, "passed": {"temperature", "humidity"} <= state.keys()})
    return entries


def benchmark(client, result, save):
    selected = [CASES[0], next(case for case in CASES if case["category"] == "conversation")]
    recorded = synthesize(client, CASES[8])
    for mode in ("cpu", "gpu", "auto"):
        placement = json_api(client, "/voice/compute-mode", "PUT", json={"compute_mode": mode})
        result["placement"][mode] = {key: placement.get(key) for key in (
            "requested_mode", "effective_mode", "stt_device", "llm_device", "vad_device", "tts_device", "fallback_reason",
        )}
        for repeat in range(4):
            stage = "first_after_placement" if repeat == 0 else "warm"
            for case, pcm in [(case, None) for case in selected] + [(CASES[8], recorded)]:
                append_turn(client, result["turns"], case, mode, pcm=pcm, speak=True, run_type=stage)
            save()
        hardware = json_api(client, "/readiness")["hardware"]
        result["resources"][mode] = {key: hardware.get(key) for key in ("cpu_percent", "ram_used_gb", "ram_total_gb", "gpu")}
        save()


def append_turn(client, store, case, mode, **kwargs):
    try:
        record = replay(client, case, mode, **kwargs)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        record = {"id": case["id"], "expected": case, "compute": mode, "phase": kwargs.get("run_type"),
                  "input": "synthetic_audio" if kwargs.get("pcm") else "text", "actual": {"error": str(exc)}, "passed": False}
    store.append(record)
    print(f"{mode} {record['input']} {case['id']}: {'PASS' if record['passed'] else 'FAIL'} {record['actual'].get('intent', '')}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", choices=("accuracy", "benchmark", "devices", "hindi-repeat"), required=True)
    parser.add_argument("--models", type=Path)
    args = parser.parse_args()
    if not 8010 <= args.port <= 8099:
        parser.error("Use a verified disposable backend on port 8010–8099.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = {"kind": args.kind, "turns": [], "devices": [], "placement": {}, "resources": {}}

    def save():
        result["summary"] = dict(Counter(f"{item['input']}:{'pass' if item['passed'] else 'fail'}" for item in result["turns"]))
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=150, trust_env=False) as client:
        ready = json_api(client, "/readiness")
        if ready["connectivity"]["mode"] != "strict_offline" or ready["database"]["alembic_revision"] != "0005_phase4_personal_assistant":
            parser.error("The disposable service must be strict-offline and use the approved migration.")
        if args.kind == "devices":
            result["devices"] = exercise_devices(client, args.port)
        elif args.kind == "benchmark":
            benchmark(client, result, save)
        elif args.kind == "hindi-repeat":
            if args.models is None:
                parser.error("--models is required for the saved Hindi fixture.")
            wave_bytes = (args.models / "eval_audio" / "phase1a_tts_hi.wav").read_bytes()
            result["placement"]["cpu"] = json_api(client, "/voice/compute-mode", "PUT", json={"compute_mode": "cpu"})
            try:
                for run in range(1, 6):
                    actual = json_api(client, "/voice/transcribe", "POST",
                                      files={"file": ("synthetic-hi.wav", wave_bytes, "audio/wav")},
                                      data={"language": "kn", "recognition_mode": "multilingual", "speak": "false"})
                    expected = {"detected": "hi", "reply": "kn", "outcome": "success", "action_executed": True}
                    observed = {key: actual[key] for key in (
                        "transcript", "response_language", "detected_language", "outcome", "action_executed", "stt_ms",
                    )}
                    passed = all((actual["detected_language"] == "hi", actual["response_language"] == "kn",
                                  actual["outcome"] == "success", actual["action_executed"]))
                    result["turns"].append({"id": f"hindi-repeat-{run}", "input": "synthetic_audio",
                                            "expected": expected, "actual": observed, "passed": passed})
                    save()
                    print(f"Saved Hindi fixture {run}/5: {'PASS' if passed else 'FAIL'}", flush=True)
            finally:
                json_api(client, "/voice/compute-mode", "PUT", json={"compute_mode": "auto"})
        else:
            for case in CASES:
                if case["id"] == "turn-22":
                    json_api(client, "/conversation/history", "DELETE")
                append_turn(client, result["turns"], case, "auto")
                save()
            for case in CASES:
                if not case.get("audio"):
                    continue
                try:
                    audio = synthesize(client, case)
                except httpx.HTTPError as exc:
                    result["turns"].append({"id": case["id"], "expected": case, "compute": "auto",
                                            "input": "synthetic_audio", "actual": {"synthesis_error": str(exc)}, "passed": False})
                else:
                    append_turn(client, result["turns"], case, "auto", pcm=audio)
                save()
            for kind in ("silence", "noise"):
                rng = random.Random(2026)
                pcm = wav_pcm([0] * 16000 if kind == "silence" else [rng.randint(-90, 90) for _ in range(16000)])
                append_turn(client, result["turns"], {"id": kind, "text": "", "language": "en", "category": "silence"}, "auto", pcm=pcm)
            if args.models:
                original = {"en": "Set a timer for five minutes and tell me the current time.",
                            "hi": "पांच मिनट का टाइमर लगाओ और अभी क्या समय हुआ है बताओ।",
                            "kn": "ಐದು ನಿಮಿಷದ ಟೈಮರ್ ಇಡು ಮತ್ತು ಈಗ ಎಷ್ಟು ಗಂಟೆ ಆಗಿದೆ ಹೇಳು."}
                for language, text in original.items():
                    pcm = (args.models / "eval_audio" / f"phase1a_tts_{language}.wav").read_bytes()
                    case = {"id": f"saved-{language}", "intent": "SetTimer", "language": language, "text": text, "check": 300, "category": "timer"}
                    append_turn(client, result["turns"], case, "auto", pcm=pcm)
                save()
    save()


if __name__ == "__main__":
    main()

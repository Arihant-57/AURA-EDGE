"""Interactive microphone and audible-output check on the local AURA backend."""

import argparse
import json
from pathlib import Path

import httpx

SAMPLES = [
    ("English", "What time is it now?", "GetTime"),
    ("Hindi", "अभी कितने बजे हैं?", "GetTime"),
    ("Kannada", "ಇಂದಿನ ದಿನಾಂಕ ಏನು?", "GetDate"),
    ("Hinglish", "अभी current time क्या है?", "GetTime"),
    ("Kannada-English", "ಇವತ್ತಿನ date ಏನು?", "GetDate"),
]


def run(port, output, sample_count):
    rows = []
    with httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1", trust_env=False, timeout=120) as client:
        def call(method, path, body=None):
            return client.request(method, path, json=body).raise_for_status().json()

        status = call("GET", "/voice/status")
        if not status["mic_detected"] or not status["speaker_detected"] or status["mic_muted"]:
            raise SystemExit("Connect the microphone and speaker and unmute AURA.")
        print("This check uses the active local audio devices. It saves pass/fail and timings without saving audio or transcripts.")
        for language, phrase, expected in SAMPLES[:sample_count]:
            input(f"\n{language}: {phrase}\nPress Enter when ready to speak.")
            status = call("GET", "/voice/status")
            if status["active_state"] != "IDLE" or status["conversation_active"] or status["mic_muted"]:
                raise SystemExit("The existing session is busy or muted. Finish it before testing.")
            started = call("POST", "/voice/mic/start", {"language": "auto"})
            turn_id = started.get("turn_id")
            if not started.get("started") or not turn_id:
                print("The microphone could not start.")
                rows.append({"language": language, "expected_intent": expected, "passed": False, "error": "capture_not_started"})
                continue
            try:
                input("Speak now. Press Enter when the phrase is complete.")
                current = call("GET", "/voice/status")
                if current["active_turn_id"] != turn_id:
                    print("Recording timed out or was interrupted. Repeat this check with the microphone ready.")
                    rows.append({"language": language, "expected_intent": expected, "passed": False, "error": "capture_not_active"})
                    continue
                result = call("POST", "/voice/mic/stop", {"speak": True})
                print("Heard:", result.get("transcript") or "(no usable speech)")
                print("Reply:", result.get("response_text") or "(no spoken reply)")
                audible = input("Was the reply audible and understandable? [y/N] ").strip().lower() == "y"
                callback = bool((result.get("stage_timestamps") or {}).get("first_non_silent_output_callback"))
                rows.append({
                    "language": language, "expected_intent": expected, "actual_intent": result["intent"],
                    "detected_language": result["detected_language"], "stt_model": result["stt_model"],
                    "reply_language": result["response_language"], "status": result["status"],
                    "verified": result["action_executed"] and result["status"] == "verified",
                    "output_callback": callback, "human_audible_understandable": audible,
                    "timing_ms": result["total_turn_duration_ms"],
                    "passed": result["intent"] == expected and result["action_executed"] and result["status"] == "verified" and callback and audible,
                })
            finally:
                if call("GET", "/voice/status")["active_turn_id"] == turn_id:
                    call("POST", "/voice/cancel", {"turn_id": turn_id})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"Passed {sum(row['passed'] for row in rows)}/{len(rows)} human checks. Local result: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--samples", type=int, choices=range(1, len(SAMPLES) + 1), default=len(SAMPLES))
    parser.add_argument("--output", type=Path, default=Path(".runtime/human-microphone-check.json"))
    options = parser.parse_args()
    run(options.port, options.output, options.samples)

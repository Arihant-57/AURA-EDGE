"""Check compound results, negative readback and explicit Hindi recognition on a disposable instance."""
import argparse
import json
import time
from pathlib import Path

import httpx

from qa.run_acceptance import CASES, json_api, synthesize, validate_turn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, choices=range(8010, 8100), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=120, trust_env=False) as client:
        def turn(text):
            return json_api(client, "/intent/process", "POST", json={"text": text, "speak": False})

        timers = "/timers?include_closed=true"
        count = len(json_api(client, timers))
        for quantity in ("3 months", "5 sandwiches", "-5 minutes", "2.5 minutes", "30 milliseconds"):
            result = turn(f"Set a timer for {quantity}")
            rows.append({"case": quantity, "intent": result["intent"], "passed": result["outcome"] == "clarification"
                         and not result["action_executed"] and len(json_api(client, timers)) == count})
        for request in ("Do not turn on the bedroom light", "बेडरूम की लाइट बंद मत करो",
                        "ಬೆಡ್‌ರೂಮ್ ಲೈಟ್ ಆಫ್ ಮಾಡಬೇಡಿ", "What happens if I switch off the study fan?",
                        "Turn on the bedroom light and set the bedroom AC to 99 degrees",
                        "Turn on the bedroom light and turn off the fan"):
            audit = "/smart-home/history?limit=1"
            before_audit = json_api(client, audit)
            result = turn(request)
            unchanged = json_api(client, audit) == before_audit
            rows.append({"case": request, "intent": result["intent"], "outcome": result["outcome"],
                         "history_unchanged": unchanged, "passed": not result["action_executed"] and unchanged})
        result = turn("Set the bedroom AC to 24 degrees and remind me in ten minutes to turn it off.")
        steps = result["data"].get("sub_results", [])
        readback = json_api(client, "/smart-home/devices/ac.bedroom")["state"]
        reminders = json_api(client, "/reminders")
        reminder_id = steps[-1]["data"].get("reminder", {}).get("id") if steps else None
        rows.append({"case": "device_and_reminder", "steps": [s["status"] for s in steps],
                     "temperature": readback["temperature"], "reminder_found": any(r["id"] == reminder_id for r in reminders),
                     "passed": len(steps) == 2 and all(s["success"] for s in steps) and readback["temperature"] == 24
                     and any(r["id"] == reminder_id for r in reminders)})
        result = turn("Make it 25 instead.")
        rows.append({"case": "referent_after_compound", "intent": result["intent"], "passed": result["action_executed"]
                     and json_api(client, "/smart-home/devices/ac.bedroom")["state"]["temperature"] == 25})
        result = turn("Summarize my day and tell me what tasks are still pending.")
        steps = result["data"].get("sub_results", [])
        rows.append({"case": "briefing_and_tasks", "passed": [s["action"] for s in steps] == ["DailyBriefing", "ListTasks"]
                     and all(s["success"] for s in steps)})

        for case in CASES:
            if case["language"] != "hi" or not case.get("audio"):
                continue
            audio = synthesize(client, case)
            before = time.time()
            result = json_api(client, "/voice/transcribe", "POST", files={"file": ("public.wav", audio, "audio/wav")},
                              data={"language": "hi", "recognition_mode": "strict", "speak": "false", "allow_llm_fallback": "false"})
            checks = validate_turn(client, case, result, before)
            rows.append({"case": case["id"], "mode": "strict Hindi", "model": result["stt_model"], "transcript": result["transcript"],
                         "intent": result["intent"], "slots": result["slots"], "checks": checks,
                         "passed": result["stt_model"] == "indic-conformer-hi" and all(checks.values())})
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    for row in rows:
        print(row["case"], row["passed"])


if __name__ == "__main__":
    main()

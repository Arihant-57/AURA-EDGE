"""Audit ambiguous and negated commands against persisted state on a disposable service."""
import argparse
import json
from pathlib import Path

import httpx


def snapshots(client):
    def read(path):
        return client.get(path).raise_for_status().json()

    return {
        "device_history": [r["id"] for r in read("/smart-home/history?limit=5")],
        "device_states": {r["id"]: r["state"] for r in read("/smart-home/devices")
                          if r["device_type"] in {"air_conditioner", "light", "fan"}},
        "timers": [r["id"] for r in read("/timers?include_closed=true")],
        "schedules": [r["id"] for r in read("/schedules?include_closed=true")],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, type=int, choices=range(8010, 8100))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    phrases = [
        "I do not want you to turn on the bedroom light",
        "Don’t turn on the bedroom light",
        "Never turn on the bedroom light",
        "बेडरूम की लाइट ऑन नहीं करनी है",
        "ಬೇಡ, ಬೆಡ್ ರೂಮ್ ಲೈಟ್ ಆನ್ ಮಾಡಿ",
        "Set the bedroom AC to 100 degrees",
        "Set the study fan speed to 99",
        "Turn on the thing in that room",
        "Set a timer for two boxes and one minute",
        "Set a timer for one minute and two apples",
        "Turn on the bedroom light and set the bedroom AC to 99 degrees",
        "Book me an international flight",
    ]
    rows = []
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}/api/v1/", timeout=120, trust_env=False) as client:
        for phrase in phrases:
            before = snapshots(client)
            result = client.post("/intent/process", json={"text": phrase, "speak": False}).raise_for_status().json()
            after = snapshots(client)
            changed = [key for key in before if before[key] != after[key]]
            rows.append({"input": phrase, "intent": result["intent"], "outcome": result["outcome"],
                         "action_executed": result["action_executed"], "changed_records": changed,
                         "reply": result["response_text"], "passed": not changed and not result["action_executed"]
                         and result["outcome"] in {"clarification", "refusal"}})
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(f"{sum(r['passed'] for r in rows)} / {len(rows)} passed")


if __name__ == "__main__":
    main()

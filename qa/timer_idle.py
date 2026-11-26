"""Check five minutes of idle operation on a fresh, silent QA backend."""
import argparse
import json
import time
from pathlib import Path

import httpx


def check_idle(port):
    with httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=6, trust_env=False) as client:
        def read(path):
            return client.get(path).raise_for_status().json()

        before = read("/notifications")["items"]
        started = time.monotonic()
        for sample in range(31):
            assert read("/notifications")["items"] == before
            assert read("/voice/status")["active_state"] == "IDLE"
            assert not read("/timers?include_closed=true"), "Use a fresh disposable database"
            if sample < 30:
                time.sleep(10)
        return {"samples": 31, "elapsed_seconds": round(time.monotonic() - started, 1),
                "new_timers": 0, "new_notifications": 0, "idle_checks_passed": 31}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, choices=range(8010, 8100), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = check_idle(args.port)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(report)

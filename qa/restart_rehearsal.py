"""Exercise real countdown and scheduled events and compare their state across a process restart."""
import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx


def probe(client, ids):
    def get(path):
        return client.get(path).raise_for_status().json()

    executions = get(f"/schedules/{ids['schedule']}/executions")
    notifications = get("/notifications")["items"]
    return {
        "task": any(r["id"] == ids["task"] for r in get("/tasks")["items"]),
        "calendar": any(r["id"] == ids["calendar"] for r in get("/calendar/events")["items"]),
        "reminder": any(r["id"] == ids["reminder"] for r in get("/reminders")),
        "memory": any(r["id"] == ids["memory"] for r in get("/memory")["items"]),
        "light_off": get("/smart-home/devices/light.bedroom_main")["state"]["power"] is False,
        "timer_completed": get(f"/timers/{ids['timer']}")["status"] == "completed",
        "schedule_executed_once": len(executions) == 1 and executions[0]["status"] == "completed",
        "schedule_notification": any(n["source_id"] == ids["schedule"] for n in notifications),
        "timer_notification": any(n["source_id"] == ids["timer"] for n in notifications),
    }


def prepare(client):
    def post(path, **data):
        return client.post(path, json=data).raise_for_status().json()

    def turn(text):
        return post("/intent/process", text=text, language="en", speak=False)

    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    records = {
        "task": post("/tasks", title="Audit rehearsal", create_reminder=False)["task"]["id"],
        "calendar": post("/calendar/events", title="Audit rehearsal", start_at_utc=future)["event"]["id"],
        "reminder": post("/reminders", message="Audit rehearsal", run_at_utc=future)["reminder"]["id"],
        "memory": post("/memory", original_text="I prefer my bedroom AC at 24 degrees")["item"]["id"],
    }
    quantity = turn("Set a timer for thirty two minutes")
    timer = quantity["data"]["timer"]
    checks = {"spoken_number_32_minutes": timer["duration_seconds"] == 1920}
    timer_url = f"/timers/{timer['id']}"
    checks["paused"] = post(timer_url + "/pause")["timer"]["status"] == "paused"
    checks["resumed"] = post(timer_url + "/resume")["timer"]["status"] == "active"
    checks["cancelled"] = client.delete(timer_url).raise_for_status().json()["timer"]["status"] == "cancelled"
    before = client.get("/timers?include_closed=true").raise_for_status().json()
    invalid = turn("Set a timer for one day and five minutes")
    checks["unsupported_duration_refused"] = (not invalid["action_executed"] and
        len(client.get("/timers?include_closed=true").raise_for_status().json()) == len(before))
    light = turn("Turn on the bedroom light")
    checks["light_initially_on"] = light["action_executed"] and client.get(
        "/smart-home/devices/light.bedroom_main").raise_for_status().json()["state"]["power"] is True
    records["timer"] = turn("Start a ten-second timer")["data"]["timer"]["id"]
    due = (datetime.now(UTC) + timedelta(seconds=8)).isoformat()
    records["schedule"] = post("/schedules", job_type="device_action", title="Audit light off",
        run_at_utc=due, device_id="light.bedroom_main", action="power", value=False)["schedule"]["id"]
    time.sleep(13)
    return {"ids": records, "checks": checks, "before_restart": probe(client, records)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "verify"])
    parser.add_argument("--port", type=int, choices=range(8010, 8100), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}/api/v1/", timeout=120, trust_env=False) as client:
        if args.phase == "prepare":
            report = prepare(client)
        else:
            report = json.loads(args.output.read_text(encoding="utf8"))
            report["after_restart"] = probe(client, report["ids"])
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(report)

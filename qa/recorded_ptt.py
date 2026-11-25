"""Exercise the microphone route with synthetic PCM injected ahead of the real VAD."""

import argparse
import io
import json
import math
import time
import wave
from pathlib import Path

import numpy as np
from aura_edge.config import Settings
from aura_edge.main import create_app
from fastapi.testclient import TestClient
from scipy.signal import resample_poly
from sqlalchemy.engine import make_url


def wait_for(condition, message, timeout=8):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if condition():
            return
        time.sleep(.02)
    raise AssertionError(message)


def run(output, study_fan):
    cfg = Settings()
    database = make_url(cfg.database_url)
    if database.host != "127.0.0.1" or database.port != 55434:
        raise SystemExit("Set AURA_EDGE_DATABASE_URL to the dedicated disposable database on port 55434.")
    app = create_app(cfg)
    events = []
    with TestClient(app) as client:
        orch = app.state.orchestrator
        capture = orch.audio_capture
        original_capture = capture.start_ptt_capture
        original_broadcast = orch.ws_hub.broadcast

        async def inject_capture(**kwargs):
            kwargs["open_hardware_stream"] = False
            await original_capture(**kwargs)

        async def observe(kind, payload):
            if kind in {"assistant_state_change", "ptt_capture_ready", "transcript_ready", "action_verified", "response_completed"}:
                events.append({"kind": kind, "payload": payload})
            await original_broadcast(kind, payload)

        capture.start_ptt_capture = inject_capture
        orch.ws_hub.broadcast = observe
        phrase, device, expected_power = (
            ("Switch off the study fan.", "fan.study", False)
            if study_fan else ("Turn on the bedroom light.", "light.bedroom_main", True)
        )
        route = f"/api/v1/smart-home/devices/{device}"
        client.post(route + "/command", json={"action": "power", "value": not expected_power, "source": "ui"}).raise_for_status()
        recording = client.post("/api/v1/voice/synthesize", json={"text": phrase, "language": "en"}).raise_for_status()
        with wave.open(io.BytesIO(recording.content), "rb") as wav:
            pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
            factor = math.gcd(wav.getframerate(), cfg.audio_sample_rate)
            pcm = resample_poly(pcm.astype(np.float32), cfg.audio_sample_rate // factor, wav.getframerate() // factor)
        pcm = np.clip(np.pad(pcm, (cfg.audio_sample_rate // 5, cfg.audio_sample_rate * 2)), -32768, 32767).astype(np.int16)
        start = client.post("/api/v1/voice/mic/start", json={"language": "auto"}).raise_for_status().json()
        assert start["started"], start
        wait_for(lambda: capture.is_recording, "Software microphone stream did not enter recording")
        for offset in range(0, pcm.size - cfg.audio_frame_samples + 1, cfg.audio_frame_samples):
            if not capture.is_recording:
                break
            capture.push_test_frame(pcm[offset:offset + cfg.audio_frame_samples])
            time.sleep(cfg.audio_frame_samples / cfg.audio_sample_rate)
        wait_for(lambda: any(row["kind"] == "ptt_capture_ready" for row in events), "VAD did not end the recording")
        turn = client.post("/api/v1/voice/mic/stop", json={"speak": True}).raise_for_status().json()
        stored = client.get(route).raise_for_status().json()["state"]["power"]
        states = [row["payload"]["state"] for row in events if row["kind"] == "assistant_state_change"]
        checks = {
            "captured_voiced_audio": capture.last_capture_metrics.voiced_ms > 0,
            "detected_english": turn["detected_language"] == "en",
            "verified_device": turn["intent"] == "DeviceControl" and turn["status"] == "verified" and turn["action_executed"] and stored is expected_power,
            "speaker_callback": turn["stage_timestamps"]["first_non_silent_output_callback"] is not None,
            "playback_completed": turn["stage_timestamps"]["playback_completion"] is not None,
            "returned_to_idle": states[-1] == "IDLE",
            "stages_observed": {"LISTENING", "TRANSCRIBING", "UNDERSTANDING", "EXECUTING", "VERIFYING", "SPEAKING"} <= set(states),
        }
    report = {
        "input": "synthetic PCM injected before VAD", "hardware_microphone_exercised": False,
        "expected_transcript": phrase, "actual_transcript": turn["transcript"], "states": states,
        "events": [row["kind"] for row in events], "checks": checks,
        "timing": turn["stage_timestamps"], "passed": all(checks.values()),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--study-fan", action="store_true")
    options = parser.parse_args()
    run(options.output, options.study_fan)

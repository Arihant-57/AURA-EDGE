"""Integration tests for Phase 1 REST endpoints, WebSocket stream, offline lock & fallbacks."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.data.database import check_database_readiness
from aura_edge.data.models import TimerRecord, VoiceTurnMetric
from aura_edge.main import create_app


def test_phase1_voice_status_language_compute_and_offline_guard() -> None:
    cfg = Settings(_env_file=None, connectivity_mode=ConnectivityMode.STRICT_OFFLINE)
    app = create_app(cfg)
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"

    with TestClient(app) as client:
        # Phase 0 compatibility preserved
        assert client.get("/api/v1/health").status_code == 200
        assert client.post("/api/v1/policy/check", json={"url": "https://example.com"}).json()[
            "allowed"
        ] is False

        status_resp = client.get("/api/v1/voice/status")
        assert status_resp.status_code == 200
        st = status_resp.json()
        assert st["active_state"] == "IDLE"
        assert st["supported_languages"] == ["en", "hi", "kn"]

        lang_resp = client.put("/api/v1/voice/language", json={"language": "kn"})
        assert lang_resp.status_code == 200
        assert lang_resp.json()["session_language"] == "kn"

        cpu_resp = client.put("/api/v1/voice/compute-mode", json={"compute_mode": "cpu"})
        assert cpu_resp.status_code == 200
        assert cpu_resp.json()["effective_mode"] == "cpu"
        assert cpu_resp.json()["llm_num_gpu"] == 0


def test_phase1_intent_synthesize_transcribe_and_ws_live(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Settings(_env_file=None, stt_model_size="base", compute_mode=ComputeMode.CPU)
    app = create_app(cfg)
    created_timer_ids: list[uuid.UUID] = []

    with TestClient(app) as client:
        # 1. WebSocket connection receives initial IDLE state
        with client.websocket_connect("/api/v1/ws/live") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "assistant_state_change"
            assert hello["payload"]["state"] == "IDLE"

        # 2. Text intent processing in EN / HI / KN (time, date, refusal)
        en_time = client.post(
            "/api/v1/intent/process",
            json={"text": "What time is it?", "language": "en", "allow_llm_fallback": False},
        )
        assert en_time.status_code == 200
        assert en_time.json()["intent"] == "GetTime"
        assert en_time.json()["action_executed"] is True

        hi_date = client.post(
            "/api/v1/intent/process",
            json={"text": "आज क्या तारीख है?", "language": "hi", "allow_llm_fallback": False},
        )
        assert hi_date.status_code == 200
        assert hi_date.json()["intent"] == "GetDate"
        assert hi_date.json()["action_executed"] is True

        kn_refusal = client.post(
            "/api/v1/intent/process",
            json={
                "text": "ಆನ್‌ಲೈನ್‌ನಲ್ಲಿ ಆಹಾರ ಆರ್ಡರ್ ಮಾಡು",
                "language": "kn",
                "allow_llm_fallback": False,
            },
        )
        assert kn_refusal.status_code == 200
        assert kn_refusal.json()["intent"] == "Unsupported"
        assert kn_refusal.json()["action_executed"] is False

        # 3. Synthesize EN/HI/KN audio offline via /api/v1/voice/synthesize
        synth = client.post(
            "/api/v1/voice/synthesize",
            json={"text": "ಸಮಯ ಮುಗಿದಿದೆ", "language": "kn", "speed": 1.0},
        )
        assert synth.status_code == 200
        assert synth.headers["content-type"].startswith("audio/wav")
        assert synth.content[:4] == b"RIFF"

        # 4. Upload WAV fixture to /api/v1/voice/transcribe
        eval_wav = Path(cfg.models_dir) / "eval_audio" / "phase1a_tts_en.wav"
        assert eval_wav.is_file()
        with eval_wav.open("rb") as fh:
            up = client.post(
                "/api/v1/voice/transcribe",
                files={"file": ("sample.wav", fh, "audio/wav")},
                data={"language": "en", "speak": "false", "allow_llm_fallback": "false"},
            )
        assert up.status_code == 200
        assert len(up.json()["transcript"]) > 0

        # 5. Cancel endpoint works cleanly
        cancel_resp = client.post("/api/v1/voice/cancel", json={})
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["cancelled"] is False  # IDLE at that moment

        # 6. Timer REST endpoints when DB is live
        if check_database_readiness(app.state.engine).connected:
            c_timer = client.post(
                "/api/v1/timers",
                json={"duration_seconds": 120, "label": "api_test", "language": "en"},
            )
            assert c_timer.status_code == 201
            tid = c_timer.json()["timer"]["id"]
            created_timer_ids.append(uuid.UUID(tid))

            listed = client.get("/api/v1/timers")
            assert listed.status_code == 200
            assert any(t["id"] == tid for t in listed.json())

            paused = client.post(f"/api/v1/timers/{tid}/pause")
            assert paused.status_code == 200
            assert paused.json()["timer"]["status"] == "paused"

            resumed = client.post(f"/api/v1/timers/{tid}/resume")
            assert resumed.status_code == 200
            assert resumed.json()["timer"]["status"] == "active"

            deleted = client.delete(f"/api/v1/timers/{tid}")
            assert deleted.status_code == 200
            assert deleted.json()["timer"]["status"] == "cancelled"

        # 7. Missing mic endpoint returns honest 503
        monkeypatch.setattr("aura_edge.application.voice_orchestrator.has_input_microphone", lambda _d: False)
        mic_err = client.post("/api/v1/voice/mic/start", json={})
        assert mic_err.status_code == 503

        # 8. Unreachable Ollama & unavailable DB resilience
        bad_cfg = Settings(
            _env_file=None,
            ollama_url="http://127.0.0.1:59999",
            database_url="postgresql+psycopg://aura_edge:bad@127.0.0.1:59998/aura_edge",
        )
        bad_app = create_app(bad_cfg)
        with TestClient(bad_app) as bad_client:
            # Local deterministic time still works even when DB & Ollama are down!
            ok_local = bad_client.post(
                "/api/v1/intent/process",
                json={"text": "What time is it?", "language": "en"},
            )
            assert ok_local.status_code == 200
            assert ok_local.json()["action_executed"] is True

            # Unmatched query when Ollama is down returns explicit refusal
            no_ollama = bad_client.post(
                "/api/v1/intent/process",
                json={"text": "Summarize quantum gravity", "language": "en"},
            )
            assert no_ollama.status_code == 200
            assert no_ollama.json()["action_executed"] is False
            assert no_ollama.json()["status"] == "refused"

    # Cleanup test rows if DB was connected
    if check_database_readiness(app.state.engine).connected:
        with Session(app.state.engine) as session:
            if created_timer_ids:
                session.execute(delete(TimerRecord).where(TimerRecord.id.in_(created_timer_ids)))
            session.execute(delete(VoiceTurnMetric))
            session.commit()

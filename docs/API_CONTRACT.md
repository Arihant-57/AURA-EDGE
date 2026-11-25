# AURA EDGE â€” API Contract & Interface Specification

> **Target Version:** Phase 0 (Baseline) through Phase 2 (Smart Home & Multilingual Voice)  
> **Base URL:** `http://127.0.0.1:8000/api/v1`  
> **Vite UI Proxy:** `/api/v1` (proxied to `http://127.0.0.1:8000` via `vite.config.ts`)  
> **Security & Connectivity:** Bound strictly to loopback (`127.0.0.1`). Strict offline enforcement prevents outbound leaks.

---

## 1. Overview & Architectural Conventions

This document serves as the binding contract between:
- **Backend Developer (`feat/backend-intelligence`)**: Implements endpoints in `backend/src/aura_edge/api/` using FastAPI and Pydantic v2.
- **Frontend Developer (`feat/frontend-experience`)**: Consumes endpoints in `frontend/src/api/client.ts` using TypeScript and React.

### Standard Response Format
- All REST requests and responses use `application/json; charset=utf-8` unless delivering audio streams (`audio/wav`).
- Success responses return HTTP `200 OK` (or `201 Created` for new resources).
- Errors follow FastAPI standard error format:
  ```json
  {
    "detail": "Descriptive error message"
  }
  ```
  Or structured validation errors (HTTP `422 Unprocessable Entity`):
  ```json
  {
    "detail": [
      {
        "loc": ["body", "field_name"],
        "msg": "Field required",
        "type": "value_error.missing"
      }
    ]
  }
  ```

---

## 2. Phase 0 Endpoints (Implemented & Verified)

### 2.1 Health Check
- **Method & Path:** `GET /api/v1/health`
- **Purpose:** Fast liveness probe (does not touch database).
- **Response (HTTP 200):**
  ```json
  {
    "status": "ok",
    "service": "aura-edge",
    "version": "0.1.0",
    "phase": 0,
    "connectivity_mode": "strict_offline",
    "compute_mode": "auto",
    "bind_host": "127.0.0.1",
    "timestamp_utc": "2026-11-25T12:00:00.000000Z"
  }
  ```

### 2.2 System Readiness & Telemetry Probe
- **Method & Path:** `GET /api/v1/readiness`
- **Purpose:** Deep readiness probe verifying PostgreSQL, pgvector extension, Alembic migration revision, CPU/RAM telemetry, GPU detection (NVIDIA NVML / RTX 3050), and Ollama reachability.
- **Response (HTTP 200 when ready, HTTP 503 when degraded/DB down):**
  ```json
  {
    "ready": true,
    "phase": 0,
    "service": "aura-edge",
    "version": "0.1.0",
    "connectivity": {
      "mode": "strict_offline",
      "external_requests_blocked": true,
      "lan_allowed_cidrs": ["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"],
      "online_allowed_hosts": []
    },
    "database": {
      "status": "ok",
      "connected": true,
      "postgres_version": "PostgreSQL 16.6 on x86_64...",
      "pgvector_installed": true,
      "pgvector_version": "0.8.0",
      "alembic_revision": "0001_phase0_foundation",
      "error": null
    },
    "hardware": {
      "os": "Windows 11",
      "architecture": "AMD64",
      "python_version": "3.13.2",
      "compute_mode_configured": "auto",
      "effective_compute_backend": "gpu",
      "cpu_cores_physical": 14,
      "cpu_cores_logical": 20,
      "cpu_percent": 12.5,
      "ram_total_gb": 15.78,
      "ram_used_gb": 8.12,
      "ram_percent": 51.4,
      "gpu": {
        "available": true,
        "name": "NVIDIA GeForce RTX 3050 6GB Laptop GPU",
        "vram_total_mb": 6144,
        "vram_used_mb": 1240,
        "driver_version": "621.24",
        "error": null
      },
      "ollama": {
        "url": "http://127.0.0.1:11434",
        "reachable": true,
        "models": ["qwen3.5:4b", "nemotron-3-nano:4b", "nomic-embed-text:latest"],
        "error": null
      }
    },
    "timestamp_utc": "2026-11-25T12:00:00.000000Z"
  }
  ```

### 2.3 Connectivity Policy Summary & Dry-Run
- **Method & Path:** `GET /api/v1/policy`
- **Method & Path:** `POST /api/v1/policy/check`
- **Request Body (`POST /api/v1/policy/check`):**
  ```json
  {
    "url": "https://example.com/api"
  }
  ```
- **Response (HTTP 200):**
  ```json
  {
    "allowed": false,
    "mode": "strict_offline",
    "target": "https://example.com/api",
    "host": "example.com",
    "reason": "strict_offline mode allows loopback targets only"
  }
  ```

---

## 3. Phase 1 Voice & Multilingual Intent Contracts (Upcoming)

Owned by `feat/backend-intelligence`; consumed by `feat/frontend-experience`.

### 3.1 Voice Pipeline Status
- **Method & Path:** `GET /api/v1/voice/status`
- **Response (HTTP 200):**
  ```json
  {
    "active_state": "idle",
    "stt_engine": "faster-whisper",
    "stt_model": "base",
    "stt_device": "cuda",
    "tts_engine": "indic-tts-local",
    "supported_languages": ["en", "hi", "kn"],
    "mic_detected": true,
    "speaker_detected": true
  }
  ```

### 3.2 Process User Utterance (Text or Intent Processing)
- **Method & Path:** `POST /api/v1/intent/process`
- **Purpose:** Parse user command across EN/HI/KN, route intent deterministically, trigger local action, and return spoken response text.
- **Request Body:**
  ```json
  {
    "text": "Living room light turn on",
    "language": "en",
    "source": "text_input"
  }
  ```
- **Response (HTTP 200):**
  ```json
  {
    "intent": "smart_home.device_set",
    "confidence": 0.98,
    "detected_language": "en",
    "slots": {
      "target_device": "living_room_light",
      "action": "turn_on",
      "value": null
    },
    "response_text": "Turning on the living room light.",
    "response_language": "en",
    "action_executed": true,
    "latency_ms": 42.1
  }
  ```

### 3.3 Audio Synthesis (Local TTS)
- **Method & Path:** `POST /api/v1/voice/synthesize`
- **Request Body:**
  ```json
  {
    "text": "à²¬à³†à²³à²•à²¨à³à²¨à³ à²¹à²¾à²•à²²à²¾à²—à²¿à²¦à³†",
    "language": "kn",
    "speed": 1.0
  }
  ```
- **Response:** `audio/wav` binary stream.

---

## 4. Phase 2 Smart-Home Simulator Contracts

### 4.1 List Simulated Devices
- **Method & Path:** `GET /api/v1/devices`
- **Response (HTTP 200):**
  ```json
  [
    {
      "id": "dev_living_light",
      "name": "Living Room Light",
      "type": "light",
      "room": "Living Room",
      "state": {
        "powered": true,
        "brightness": 80,
        "color_temp_k": 3000
      },
      "last_updated": "2026-11-25T12:00:00Z"
    },
    {
      "id": "dev_thermostat",
      "name": "AC Thermostat",
      "type": "climate",
      "room": "Bedroom",
      "state": {
        "powered": true,
        "target_temperature_c": 24.0,
        "current_temperature_c": 25.2,
        "mode": "cool"
      },
      "last_updated": "2026-11-25T12:00:00Z"
    }
  ]
  ```

### 4.2 Control Device State
- **Method & Path:** `PATCH /api/v1/devices/{device_id}`
- **Request Body:**
  ```json
  {
    "state": {
      "powered": false
    }
  }
  ```
- **Response (HTTP 200):** Updated Device schema.

---

## 5. Real-Time WebSocket Interface (Dynamic Island & Dashboard)

- **WebSocket URL:** `ws://127.0.0.1:8000/api/v1/ws/live`
- **Client Connection Handshake:** Frontend connects on mount, auto-reconnects with exponential backoff on disconnect.

### Incoming Server Event Schema
```typescript
export type AssistantState = "idle" | "listening" | "processing" | "speaking" | "error";

export interface AssistantEvent {
  type: "assistant_state_change";
  payload: {
    state: AssistantState;
    message?: string;
    language?: "en" | "hi" | "kn";
  };
}

export interface AudioLevelEvent {
  type: "audio_level";
  payload: {
    rms_db: number;
    peak: number;
  };
}

export interface DeviceStateEvent {
  type: "device_state_change";
  payload: {
    device_id: string;
    state: Record<string, unknown>;
  };
}
```

---

## 6. TypeScript Frontend Integration Model

Frontend developers can directly paste these types into `frontend/src/api/types.ts`:

```typescript
export type ConnectivityMode = "strict_offline" | "lan_only" | "online_opt_in";
export type ComputeMode = "auto" | "cpu" | "gpu";
export type SupportedLanguage = "en" | "hi" | "kn";

export interface IntentProcessRequest {
  text: string;
  language: SupportedLanguage;
  source?: "voice" | "text_input";
}

export interface IntentProcessResponse {
  intent: string;
  confidence: number;
  detected_language: SupportedLanguage;
  slots: Record<string, string | number | boolean | null>;
  response_text: string;
  response_language: SupportedLanguage;
  action_executed: boolean;
  latency_ms: number;
}
```

# Phase 5 backend gaps

The approved Phase 4 source remains frozen.

| Capability | Runtime behavior and follow-up |
| --- | --- |
| Wake word | Click-to-talk uses the existing microphone API. A reliable offline AURA keyword model and a way to hand off ownership and buffered audio to the backend are needed before wake activation can be enabled. |
| Spoken Stop during TTS | The backend's playback guard suspends capture in half-duplex mode. The Stop control calls the existing cancellation endpoint. Acoustic interruption needs a validated playback-aware detector and coordinated microphone ownership. |
| Measured speaker waveform | The documented audio event reports microphone RMS and peak. Speaking uses the real assistant state. Measured output visualization needs a backend speaker-level event. |
| Existing stale process | The original listener on port 8000 exposes the Phase 4 database revision but lacks personal assistant routes in OpenAPI. The desktop reports per-section HTTP errors and preserves the running service. A process started from the approved Phase 4 source exposes those routes. |
| Edit recurring calendar rules, linked event reminder offsets and scheduled action targets | Their PATCH routes accept the documented update fields only. The create forms accept recurrence and targets where supported. Create a new schedule or event to change fields unavailable on an existing record. |
| Expiry or delivery-date clearing | Existing upsert and patch routes do not provide an explicit clear contract for every optional date. The dashboard submits supported changes and displays the persisted record. |
| Historical energy | The energy endpoint supplies an estimated snapshot in watts. Historical kWh requires persistent energy samples and a query contract. |

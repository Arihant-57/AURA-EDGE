# AURA EDGE Desktop architecture

PySide6 provides one transparent native companion window and one lazily created Qt WebEngine dashboard. QML draws the original pet. The companion expands in place and returns to its recorded screen position. Qt window activation controls outside collapse, with exceptions for popups, dialogs and a pinned dashboard. Settings use the per-user Qt settings store.

One Qt WebSocket owns the backend event subscription. The native UI consumes state, input audio levels and notification events. Qt WebChannel relays the same events to React and exposes fixed window, voice and appearance actions. React maintains shared REST snapshots keyed by endpoint and refreshes them after relevant live events, reconnection and writes. FastAPI and PostgreSQL own device, voice and planner state.

The production Vite bundle is served by a private HTTP listener on a random loopback port and session path. It proxies JSON into the fixed loopback backend API. The dashboard accepts navigation only to its initial local URL. Requests are restricted to the session path. Writes require the matching origin. The embedded profile runs without a disk cache. No external assets are loaded at runtime.

The launcher reuses the existing backend environment. If a new backend is required, the desktop owns a QProcess wrapping the original Uvicorn application. Shutdown stops the voice session, closes events and requests graceful Uvicorn exit for that child. Process termination is scoped to that child after a timeout. Ollama, Docker and PostgreSQL are independent services.

import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  checkPolicyUrl,
  fetchSystemSnapshot,
  PolicyDecision,
  SystemSnapshotResult,
} from "./api/client";

type LoadState =
  | { kind: "loading" }
  | { kind: "loaded"; data: SystemSnapshotResult }
  | { kind: "unreachable"; message: string };

const UNIMPLEMENTED_PHASES = [
  "Phase 1: Multilingual STT/TTS (EN/HI/KN), timers, date/time, refusal",
  "Phase 2: Virtual home simulator & optional ESP32 MQTT adapter",
  "Phase 3: Persistent preferences, local retrieval & scheduled events",
  "Phase 4: Tasks, calendar, groceries, recipes, expenses & parcel records",
  "Phase 5: Full dashboard, Dynamic Island & live WebSocket state stream",
];

function StatusBadge({
  tone,
  label,
}: {
  tone: "ok" | "warn" | "error" | "neutral";
  label: string;
}) {
  const styles = {
    ok: "border-emerald-700/70 bg-emerald-950/60 text-emerald-300",
    warn: "border-amber-700/70 bg-amber-950/60 text-amber-300",
    error: "border-rose-700/70 bg-rose-950/60 text-rose-300",
    neutral: "border-zinc-700 bg-zinc-900 text-zinc-300",
  }[tone];

  return (
    <span
      className={`inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-medium tracking-wide ${styles}`}
    >
      {label}
    </span>
  );
}

export function App() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [testUrl, setTestUrl] = useState("https://example.com");
  const [policyDecision, setPolicyDecision] = useState<PolicyDecision | null>(null);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [checkingPolicy, setCheckingPolicy] = useState(false);

  const loadStatus = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const snapshot = await fetchSystemSnapshot();
      setState({ kind: "loaded", data: snapshot });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to reach local FastAPI backend.";
      setState({ kind: "unreachable", message });
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  async function handlePolicyCheck(event: FormEvent) {
    event.preventDefault();
    setPolicyError(null);
    setCheckingPolicy(true);
    try {
      const result = await checkPolicyUrl(testUrl);
      setPolicyDecision(result);
    } catch (err) {
      setPolicyDecision(null);
      setPolicyError(err instanceof Error ? err.message : "Policy check request failed.");
    } finally {
      setCheckingPolicy(false);
    }
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
        <header className="flex flex-col gap-4 border-b border-zinc-800 pb-6 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-2xl font-semibold tracking-tight text-zinc-50">
                AURA EDGE
              </h1>
              <StatusBadge tone="neutral" label="Phase 0 · Foundation Shell" />
              {state.kind === "loaded" && (
                <StatusBadge
                  tone={state.data.readiness.ready ? "ok" : "warn"}
                  label={
                    state.data.readiness.ready
                      ? "Backend + DB Ready"
                      : "Backend Up · DB Not Ready"
                  }
                />
              )}
              {state.kind === "unreachable" && (
                <StatusBadge tone="error" label="Backend Unreachable" />
              )}
            </div>
            <p className="mt-2 text-sm text-zinc-400">
              Offline-first multilingual personal AI assistant runtime verification console.
              Local assets only; no external CDN or telemetry dependencies.
            </p>
          </div>

          <button
            type="button"
            onClick={() => void loadStatus()}
            className="inline-flex items-center justify-center rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-100 transition hover:border-zinc-500 hover:bg-zinc-800 focus:outline-none focus:ring-2 focus:ring-zinc-500"
          >
            Refresh Status
          </button>
        </header>

        <main className="mt-8 space-y-6">
          {state.kind === "loading" && (
            <section
              aria-live="polite"
              className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-6 text-sm text-zinc-300"
            >
              Querying local backend at <code>/api/v1/health</code> and{" "}
              <code>/api/v1/readiness</code>...
            </section>
          )}

          {state.kind === "unreachable" && (
            <section
              role="alert"
              className="rounded-xl border border-rose-800/80 bg-rose-950/30 p-6"
            >
              <h2 className="text-base font-semibold text-rose-200">
                Local Backend Unreachable
              </h2>
              <p className="mt-2 text-sm text-rose-300">{state.message}</p>
              <p className="mt-3 text-xs text-zinc-400">
                Start the FastAPI backend on <code>127.0.0.1:8000</code> and select{" "}
                <strong>Refresh Status</strong>.
              </p>
            </section>
          )}

          {state.kind === "loaded" && (
            <>
              <div className="grid gap-6 md:grid-cols-2">
                {/* Backend Liveness */}
                <section className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
                  <div className="flex items-center justify-between">
                    <h2 className="text-base font-semibold text-zinc-100">
                      Backend Liveness
                    </h2>
                    <StatusBadge tone="ok" label={state.data.health.status.toUpperCase()} />
                  </div>
                  <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                    <dt className="text-zinc-400">Service</dt>
                    <dd className="font-mono text-zinc-200">{state.data.health.service}</dd>
                    <dt className="text-zinc-400">Version</dt>
                    <dd className="font-mono text-zinc-200">{state.data.health.version}</dd>
                    <dt className="text-zinc-400">Bind Host</dt>
                    <dd className="font-mono text-zinc-200">{state.data.health.bind_host}</dd>
                    <dt className="text-zinc-400">Compute Pref</dt>
                    <dd className="font-mono text-zinc-200">{state.data.health.compute_mode}</dd>
                    <dt className="text-zinc-400">Checked UTC</dt>
                    <dd className="font-mono text-xs text-zinc-300">
                      {state.data.health.timestamp_utc}
                    </dd>
                  </dl>
                </section>

                {/* PostgreSQL + pgvector */}
                <section className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
                  <div className="flex items-center justify-between">
                    <h2 className="text-base font-semibold text-zinc-100">
                      PostgreSQL &amp; pgvector
                    </h2>
                    <StatusBadge
                      tone={state.data.readiness.database.status === "ok" ? "ok" : "error"}
                      label={
                        state.data.readiness.database.status === "ok"
                          ? "CONNECTED"
                          : `UNAVAILABLE (HTTP ${state.data.readinessHttpStatus})`
                      }
                    />
                  </div>
                  <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                    <dt className="text-zinc-400">Connected</dt>
                    <dd className="font-mono text-zinc-200">
                      {String(state.data.readiness.database.connected)}
                    </dd>
                    <dt className="text-zinc-400">pgvector Extension</dt>
                    <dd className="font-mono text-zinc-200">
                      {state.data.readiness.database.pgvector_installed
                        ? `v${state.data.readiness.database.pgvector_version ?? "unknown"}`
                        : "not installed"}
                    </dd>
                    <dt className="text-zinc-400">Alembic Revision</dt>
                    <dd className="font-mono text-zinc-200">
                      {state.data.readiness.database.alembic_revision ?? "none"}
                    </dd>
                    <dt className="text-zinc-400">Server Version</dt>
                    <dd className="truncate font-mono text-xs text-zinc-300">
                      {state.data.readiness.database.postgres_version ?? "unavailable"}
                    </dd>
                  </dl>
                  {state.data.readiness.database.error && (
                    <div
                      role="alert"
                      className="mt-4 rounded-lg border border-rose-800/70 bg-rose-950/40 p-3 text-xs text-rose-200"
                    >
                      {state.data.readiness.database.error}
                    </div>
                  )}
                </section>

                {/* Connectivity Policy & Dry-Run Check */}
                <section className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
                  <div className="flex items-center justify-between">
                    <h2 className="text-base font-semibold text-zinc-100">
                      Connectivity Policy
                    </h2>
                    <StatusBadge
                      tone={
                        state.data.readiness.connectivity.external_requests_blocked
                          ? "ok"
                          : "warn"
                      }
                      label={state.data.readiness.connectivity.mode}
                    />
                  </div>
                  <p className="mt-2 text-xs text-zinc-400">
                    External outbound requests:{" "}
                    <strong className="text-zinc-200">
                      {state.data.readiness.connectivity.external_requests_blocked
                        ? "Blocked (fail-closed)"
                        : "Opt-in hosts allowed"}
                    </strong>
                  </p>

                  <form onSubmit={(e) => void handlePolicyCheck(e)} className="mt-4 space-y-2">
                    <label
                      htmlFor="policy-url-input"
                      className="block text-xs font-medium text-zinc-300"
                    >
                      Test URL against centralized policy (no outbound traffic sent)
                    </label>
                    <div className="flex gap-2">
                      <input
                        id="policy-url-input"
                        type="text"
                        value={testUrl}
                        onChange={(e) => setTestUrl(e.target.value)}
                        className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 font-mono text-xs text-zinc-100 focus:border-zinc-500 focus:outline-none"
                      />
                      <button
                        type="submit"
                        disabled={checkingPolicy}
                        className="shrink-0 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-100 hover:bg-zinc-700 disabled:opacity-50"
                      >
                        {checkingPolicy ? "Checking..." : "Evaluate"}
                      </button>
                    </div>
                  </form>

                  {policyError && (
                    <p className="mt-3 text-xs text-rose-300">{policyError}</p>
                  )}

                  {policyDecision && (
                    <div className="mt-3 rounded-lg border border-zinc-800 bg-zinc-950/70 p-3 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-zinc-300">
                          {policyDecision.host || policyDecision.target}
                        </span>
                        <StatusBadge
                          tone={policyDecision.allowed ? "ok" : "error"}
                          label={policyDecision.allowed ? "ALLOWED" : "DENIED"}
                        />
                      </div>
                      <p className="mt-1 text-zinc-400">{policyDecision.reason}</p>
                    </div>
                  )}
                </section>

                {/* Measured Hardware & Ollama */}
                <section className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
                  <div className="flex items-center justify-between">
                    <h2 className="text-base font-semibold text-zinc-100">
                      Host Hardware &amp; Local Runtime
                    </h2>
                    <StatusBadge
                      tone="neutral"
                      label={`Compute: ${state.data.readiness.hardware.effective_compute_backend}`}
                    />
                  </div>
                  <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                    <dt className="text-zinc-400">OS / Arch</dt>
                    <dd className="font-mono text-xs text-zinc-200">
                      {state.data.readiness.hardware.os} (
                      {state.data.readiness.hardware.architecture})
                    </dd>
                    <dt className="text-zinc-400">CPU Cores / Load</dt>
                    <dd className="font-mono text-zinc-200">
                      {state.data.readiness.hardware.cpu_cores_physical ?? "?"}C /{" "}
                      {state.data.readiness.hardware.cpu_cores_logical ?? "?"}T ·{" "}
                      {state.data.readiness.hardware.cpu_percent}%
                    </dd>
                    <dt className="text-zinc-400">System RAM</dt>
                    <dd className="font-mono text-zinc-200">
                      {state.data.readiness.hardware.ram_used_gb} /{" "}
                      {state.data.readiness.hardware.ram_total_gb} GB (
                      {state.data.readiness.hardware.ram_percent}%)
                    </dd>
                    <dt className="text-zinc-400">GPU</dt>
                    <dd className="font-mono text-xs text-zinc-200">
                      {state.data.readiness.hardware.gpu.available
                        ? `${state.data.readiness.hardware.gpu.name} (${state.data.readiness.hardware.gpu.vram_total_mb} MiB)`
                        : `Unavailable (${state.data.readiness.hardware.gpu.error ?? "no GPU"})`}
                    </dd>
                    <dt className="text-zinc-400">Ollama Loopback</dt>
                    <dd className="font-mono text-xs text-zinc-200">
                      {state.data.readiness.hardware.ollama.reachable
                        ? `Reachable (${state.data.readiness.hardware.ollama.models.length} models)`
                        : "Not running / unreachable"}
                    </dd>
                  </dl>
                </section>
              </div>
            </>
          )}

          {/* Honest Scope Boundary */}
          <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-300">
              Phase 0 Scope Boundary (Not Yet Implemented)
            </h2>
            <p className="mt-1 text-xs text-zinc-400">
              Only foundation health, offline connectivity enforcement, hardware inspection,
              and PostgreSQL+pgvector persistence are active in Phase 0.
            </p>
            <ul className="mt-3 grid gap-2 text-xs text-zinc-400 sm:grid-cols-2">
              {UNIMPLEMENTED_PHASES.map((item) => (
                <li key={item} className="rounded-md border border-zinc-800/80 bg-zinc-950/60 px-3 py-2">
                  {item}
                </li>
              ))}
            </ul>
          </section>
        </main>
      </div>
    </div>
  );
}

export default App;

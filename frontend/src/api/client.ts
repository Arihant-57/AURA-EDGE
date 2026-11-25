export type ConnectivityMode = "strict_offline" | "lan_only" | "online_opt_in";

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  phase: number;
  connectivity_mode: ConnectivityMode;
  compute_mode: "auto" | "cpu" | "gpu";
  bind_host: string;
  timestamp_utc: string;
}

export interface PolicySummary {
  mode: ConnectivityMode;
  external_requests_blocked: boolean;
  lan_allowed_cidrs: string[];
  online_allowed_hosts: string[];
}

export interface DatabaseReadiness {
  status: "ok" | "unavailable";
  connected: boolean;
  postgres_version: string | null;
  pgvector_installed: boolean;
  pgvector_version: string | null;
  alembic_revision: string | null;
  error: string | null;
}

export interface GpuInfo {
  available: boolean;
  name: string | null;
  vram_total_mb: number | null;
  vram_used_mb: number | null;
  driver_version: string | null;
  error: string | null;
}

export interface OllamaInfo {
  url: string;
  reachable: boolean;
  models: string[];
  error: string | null;
}

export interface HardwareSnapshot {
  os: string;
  architecture: string;
  python_version: string;
  compute_mode_configured: string;
  effective_compute_backend: string;
  cpu_cores_physical: number | null;
  cpu_cores_logical: number | null;
  cpu_percent: number;
  ram_total_gb: number;
  ram_used_gb: number;
  ram_percent: number;
  gpu: GpuInfo;
  ollama: OllamaInfo;
}

export interface ReadinessResponse {
  ready: boolean;
  phase: number;
  service: string;
  version: string;
  connectivity: PolicySummary;
  database: DatabaseReadiness;
  hardware: HardwareSnapshot;
  timestamp_utc: string;
}

export interface PolicyDecision {
  allowed: boolean;
  mode: ConnectivityMode;
  target: string;
  host: string;
  reason: string;
}

export interface SystemSnapshotResult {
  health: HealthResponse;
  readiness: ReadinessResponse;
  readinessHttpStatus: number;
}

export async function fetchSystemSnapshot(signal?: AbortSignal): Promise<SystemSnapshotResult> {
  const [healthRes, readinessRes] = await Promise.all([
    fetch("/api/v1/health", { signal }),
    fetch("/api/v1/readiness", { signal }),
  ]);

  if (!healthRes.ok) {
    throw new Error(`Health endpoint failed with HTTP ${healthRes.status}`);
  }

  const health = (await healthRes.json()) as HealthResponse;
  const readiness = (await readinessRes.json()) as ReadinessResponse;

  return {
    health,
    readiness,
    readinessHttpStatus: readinessRes.status,
  };
}

export async function checkPolicyUrl(url: string): Promise<PolicyDecision> {
  const res = await fetch("/api/v1/policy/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });

  if (!res.ok) {
    throw new Error(`Policy check failed with HTTP ${res.status}`);
  }

  return (await res.json()) as PolicyDecision;
}

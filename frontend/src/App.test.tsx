import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const MOCK_HEALTH = {
  status: "ok",
  service: "aura-edge",
  version: "0.1.0-phase0",
  phase: 0,
  connectivity_mode: "strict_offline",
  compute_mode: "auto",
  bind_host: "127.0.0.1",
  timestamp_utc: "2026-11-25T06:20:00+00:00",
};

const BASE_HARDWARE = {
  os: "Windows 11",
  architecture: "AMD64",
  python_version: "3.13.2",
  compute_mode_configured: "auto",
  effective_compute_backend: "cuda",
  cpu_cores_physical: 14,
  cpu_cores_logical: 20,
  cpu_percent: 12.5,
  ram_total_gb: 15.78,
  ram_used_gb: 8.1,
  ram_percent: 51.3,
  gpu: {
    available: true,
    name: "NVIDIA GeForce RTX 3050 6GB Laptop GPU",
    vram_total_mb: 6144,
    vram_used_mb: 420,
    driver_version: "621.24",
    error: null,
  },
  ollama: {
    url: "http://127.0.0.1:11434",
    reachable: false,
    models: [],
    error: "Connection refused",
  },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("App Phase 0 Console", () => {
  it("renders real DB, pgvector, policy, and hardware metrics when ready", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url === "/api/v1/health") {
        return new Response(JSON.stringify(MOCK_HEALTH), { status: 200 });
      }
      if (url === "/api/v1/readiness") {
        return new Response(
          JSON.stringify({
            ready: true,
            phase: 0,
            service: "aura-edge",
            version: "0.1.0-phase0",
            connectivity: {
              mode: "strict_offline",
              external_requests_blocked: true,
              lan_allowed_cidrs: ["192.168.0.0/16"],
              online_allowed_hosts: [],
            },
            database: {
              status: "ok",
              connected: true,
              postgres_version: "PostgreSQL 16.4",
              pgvector_installed: true,
              pgvector_version: "0.8.0",
              alembic_revision: "0001_phase0_foundation",
              error: null,
            },
            hardware: BASE_HARDWARE,
            timestamp_utc: "2026-11-25T06:20:00+00:00",
          }),
          { status: 200 },
        );
      }
      if (url === "/api/v1/policy/check") {
        return new Response(
          JSON.stringify({
            allowed: false,
            mode: "strict_offline",
            target: "https://example.com",
            host: "example.com",
            reason: "strict_offline permits loopback targets only; non-loopback host denied.",
          }),
          { status: 200 },
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(await screen.findByText("Backend + DB Ready")).toBeInTheDocument();
    expect(screen.getByText("0001_phase0_foundation")).toBeInTheDocument();
    expect(screen.getByText("v0.8.0")).toBeInTheDocument();
    expect(screen.getByText(/RTX 3050 6GB Laptop GPU/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Evaluate" }));
    await waitFor(() => {
      expect(screen.getByText("DENIED")).toBeInTheDocument();
    });
  });

  it("surfaces PostgreSQL 503 unavailability honestly without fake success", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url === "/api/v1/health") {
        return new Response(JSON.stringify(MOCK_HEALTH), { status: 200 });
      }
      if (url === "/api/v1/readiness") {
        return new Response(
          JSON.stringify({
            ready: false,
            phase: 0,
            service: "aura-edge",
            version: "0.1.0-phase0",
            connectivity: {
              mode: "strict_offline",
              external_requests_blocked: true,
              lan_allowed_cidrs: ["192.168.0.0/16"],
              online_allowed_hosts: [],
            },
            database: {
              status: "unavailable",
              connected: false,
              postgres_version: null,
              pgvector_installed: false,
              pgvector_version: null,
              alembic_revision: null,
              error: "connection to server at 127.0.0.1, port 5432 failed",
            },
            hardware: BASE_HARDWARE,
            timestamp_utc: "2026-11-25T06:20:00+00:00",
          }),
          { status: 503 },
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(await screen.findByText("Backend Up · DB Not Ready")).toBeInTheDocument();
    expect(screen.getByText("UNAVAILABLE (HTTP 503)")).toBeInTheDocument();
    expect(
      screen.getByText(/connection to server at 127\.0\.0\.1, port 5432 failed/),
    ).toBeInTheDocument();
  });

  it("shows Backend Unreachable alert when network request throws", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("ECONNREFUSED"));

    render(<App />);

    expect(await screen.findByText("Local Backend Unreachable")).toBeInTheDocument();
    expect(screen.getByText("ECONNREFUSED")).toBeInTheDocument();
  });
});

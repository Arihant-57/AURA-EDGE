export type Language = "auto" | "en" | "hi" | "kn";
export type AssistantState = "IDLE" | "LISTENING" | "TRANSCRIBING" | "UNDERSTANDING" | "CLARIFYING" | "EXECUTING" | "VERIFYING" | "SPEAKING" | "CANCELLED" | "ERROR";
export const stateLabels: Record<AssistantState, string> = {
  IDLE: "Ready", LISTENING: "Listening", TRANSCRIBING: "Transcribing",
  UNDERSTANDING: "Thinking", CLARIFYING: "Needs clarification", EXECUTING: "Executing",
  VERIFYING: "Verifying", SPEAKING: "Speaking", CANCELLED: "Cancelled", ERROR: "Error",
};

export interface Voice {
  active_state: AssistantState;
  active_turn_id: string | null;
  session_language: Language;
  stt_recognition_mode: "multilingual" | "strict";
  mic_muted: boolean;
  mic_detected: boolean;
  conversation_active: boolean;
  conversation_mode: "continuous" | "ptt" | "idle";
  stt_model: string;
  compute: { requested_mode: string; effective_mode: string; stt_device: string; llm_device: string; fallback_reason: string | null };
}

export type Value = number | boolean | string;
export interface Capability { type?: "bool"; min?: number; max?: number; enum?: string[] }
export interface Device {
  id: string; name: string; room: string; device_type: string;
  available: boolean; capabilities: Record<string, Capability>; state: Record<string, Value>;
  estimated_power_w: number; updated_at_utc: string;
}
export interface Room { id: string; device_count: number }
export interface Job {
  id: string; title: string; job_type: string; status: string; enabled: boolean;
  scheduled_at_utc: string; next_run_at_utc: string | null; timezone: string;
  recurrence: { kind: "once" | "daily" | "weekly" }; missed_policy: string;
  payload: { message?: string; action?: string; value?: Value };
  device_id?: string; routine_id?: string; last_error?: string;
}
export interface Task { id: string; title: string; description?: string; status: string; priority: string; category: string; due_at_utc?: string; completed_at_utc?: string }
export interface CalendarEvent { id: string; title: string; description?: string; location?: string; start_at_utc: string; end_at_utc?: string }
export interface Timer { id: string; label: string | null; status: string; remaining_seconds: number; fire_at_utc: string | null }
export interface Activities { running: Device[]; scheduled: Job[]; completed: Device[] }
export interface Energy { total_power_w: number; by_room_power_w: Record<string, number>; devices: { device_id: string; power_w: number }[] }
export interface Notice { id: string; title: string; message: string; is_read: boolean; created_at_utc: string }
export interface NoticePrefs {
  visual_enabled: boolean; spoken_enabled: boolean; quiet_hours_enabled: boolean;
  quiet_hours_start: string; quiet_hours_end: string; timezone_name: string;
}
export interface Items<T> { items: T[]; count: number }
export interface Briefing {
  summary_text: string; low_stock_items: { display_name: string }[]; parcels_due: { order_name: string }[];
  upcoming_reminders: Job[];
}
export interface Turn {
  turn_id: string; transcript?: string; response_text: string; silent?: boolean;
  outcome: string; status: string; action_executed: boolean; error?: string;
}
export interface Message { role: string; content: string; turn_id: string | null }
export interface LiveEvent { type: string; timestamp_utc: string; payload: Record<string, unknown> }

export function apiPath(path: string) {
  return `./api/v1${path}`;
}

export async function api<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(apiPath(path), {
    method, signal, cache: "no-store",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json().catch(() => null);
  if ((!response.ok && !(path === "/readiness" && response.status === 503 && result?.database)) || result === null) {
    const detail = result?.detail;
    const message = typeof detail === "string" ? detail :
      Array.isArray(detail) ? detail.map((issue) => issue.msg).join(". ") : response.statusText;
    throw new Error(message ? `${message} (HTTP ${response.status})` : `Local API returned HTTP ${response.status}`);
  }
  return result as T;
}

export const devicePath = (id: string) => `/smart-home/devices/${encodeURIComponent(id)}`;

export function validateValue(spec: Capability, value: Value): boolean {
  if (spec.type === "bool") return typeof value === "boolean";
  if (spec.enum) return typeof value === "string" && spec.enum.includes(value);
  return typeof value === "number" && Number.isFinite(value) &&
    spec.min !== undefined && spec.max !== undefined && value >= spec.min && value <= spec.max;
}

export async function commandDevice(device: Device, action: string, value: Value) {
  const spec = device.capabilities[action];
  if (!device.available || !spec || !validateValue(spec, value)) throw new Error("This device does not support that value.");
  const result = await api<{ status: string; device: Device }>(`${devicePath(device.id)}/command`, "POST", { action, value, source: "ui" });
  if (result.status !== "verified") throw new Error("The backend could not verify this change.");
  return result.device;
}

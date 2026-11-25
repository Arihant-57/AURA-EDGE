import { useEffect, useRef, useState } from "react";
import { api, LiveEvent } from "./api/desktop";

interface Signal<T> {
  connect(callback: (value: T) => void): void;
  disconnect?(callback: (value: T) => void): void;
}
export interface DesktopBridge {
  liveEvent: Signal<string>;
  connection: Signal<boolean>;
  preferencesChanged: Signal<string>;
  navigate: Signal<string>;
  snapshot(callback: (value: string) => void): void;
  preferences(callback: (value: string) => void): void;
  windowAction(action: string): void;
  preference(name: string, value: string): void;
  voice(action: string): void;
}
declare global {
  interface Window {
    auraDesktop?: DesktopBridge;
  }
}

type Listener = (event: LiveEvent) => void;
const listeners = new Set<Listener>();
const seen = new Set<string>();
let connected = false;
let started = false;

function publish(event: LiveEvent) {
  if (event.type !== "audio_level" && event.type !== "response_text_delta" && event.timestamp_utc) {
    const key = `${event.type}:${event.timestamp_utc}:${JSON.stringify(event.payload)}`;
    if (seen.has(key)) return;
    seen.add(key);
    if (seen.size > 256) seen.delete(seen.values().next().value!);
  }
  listeners.forEach((listener) => listener(event));
}

function receive(raw: string) {
  try {
    const event = JSON.parse(raw) as LiveEvent;
    if (event && typeof event.type === "string" && event.payload && typeof event.payload === "object") publish(event);
  } catch { /* Ignore malformed frames. */ }
}

function connection(value: boolean) {
  connected = value;
  publish({ type: value ? "connected" : "disconnected", payload: {}, timestamp_utc: "" });
}

export function startLive() {
  if (started) return;
  started = true;
  const bridge = window.auraDesktop;
  if (bridge) {
    bridge.liveEvent.connect(receive);
    bridge.connection.connect(connection);
    bridge.snapshot((value) => {
      const snapshot = JSON.parse(value) as { connected: boolean; events: LiveEvent[] };
      connection(snapshot.connected);
      snapshot.events.forEach(publish);
    });
    return;
  }
  let delay = 500;
  function open() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${location.host}/api/v1/ws/live`);
    socket.onopen = () => { delay = 500; connection(true); };
    socket.onmessage = (message) => receive(String(message.data));
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      connection(false);
      window.setTimeout(open, delay);
      delay = Math.min(delay * 2, 8_000);
    };
  }
  open();
}

export function useLive(callback: Listener) {
  const current = useRef(callback);
  current.current = callback;
  useEffect(() => {
    const listener: Listener = (event) => current.current(event);
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  }, []);
}

export function useConnection() {
  const [online, setOnline] = useState(connected);
  useLive((event) => {
    if (event.type === "connected" || event.type === "disconnected") setOnline(event.type === "connected");
  });
  return online;
}

interface Resource<T> { data?: T; error?: string; loading: boolean }
type ResourceListener = (state: Resource<unknown>) => void;
interface Entry {
  state: Resource<unknown>;
  listeners: Set<ResourceListener>;
  pattern: RegExp | undefined;
  request?: AbortController;
  pendingRefresh?: number;
}
const entries = new Map<string, Entry>();

function emit(entry: Entry, state: Resource<unknown>) {
  entry.state = state;
  entry.listeners.forEach((listener) => listener(state));
}

async function load(path: string, entry: Entry) {
  entry.request?.abort();
  const request = new AbortController();
  entry.request = request;
  emit(entry, { data: entry.state.data, loading: true });
  try {
    const data = await api(path, "GET", undefined, request.signal);
    if (!request.signal.aborted) emit(entry, { data, loading: false });
  } catch (error) {
    if (!request.signal.aborted) emit(entry, { data: entry.state.data, error: String(error instanceof Error ? error.message : error), loading: false });
  }
}

listeners.add((event) => {
  for (const [path, entry] of entries) {
    if (!entry.listeners.size || (event.type !== "connected" && !entry.pattern?.test(event.type))) continue;
    // Coalesce consecutive updates from a single verified backend action.
    if (entry.pendingRefresh !== undefined) continue;
    entry.pendingRefresh = window.setTimeout(() => {
      entry.pendingRefresh = undefined;
      if (entry.listeners.size) void load(path, entry);
    }, 100);
  }
});

export function useResource<T>(path: string, eventPattern?: RegExp): Resource<T> & { refresh: () => void } {
  const [state, setState] = useState<Resource<T>>({ loading: Boolean(path) });
  const pattern = eventPattern?.source;
  useEffect(() => {
    if (!path) { setState({ loading: false }); return; }
    let entry = entries.get(path);
    if (!entry) {
      entry = { state: { loading: true }, listeners: new Set(), pattern: pattern ? new RegExp(pattern) : undefined };
      entries.set(path, entry);
    } else if (pattern && !entry.pattern) {
      entry.pattern = new RegExp(pattern);
    }
    const current = entry;
    const listener: ResourceListener = (value) => setState(value as Resource<T>);
    current.listeners.add(listener);
    listener(current.state);
    if (current.listeners.size === 1) void load(path, current);
    return () => {
      current.listeners.delete(listener);
      if (!current.listeners.size) {
        current.request?.abort();
        if (current.pendingRefresh !== undefined) window.clearTimeout(current.pendingRefresh);
        entries.delete(path);
      }
    };
  }, [path, pattern]);
  return { ...state, refresh: () => refreshResources(path) };
}

export function refreshResources(...prefixes: string[]) {
  for (const [path, entry] of entries) {
    if (entry.listeners.size && prefixes.some((prefix) => path.startsWith(prefix))) void load(path, entry);
  }
}

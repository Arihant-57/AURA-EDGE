import { useState } from "react";
import { Assistant } from "./Assistant";
import { api, Language, Notice, NoticePrefs } from "./api/desktop";
import { ReadinessResponse } from "./api/client";
import { refreshResources, useResource } from "./live";
import { Card, ConfirmButton, field, formatDate, formData, human, Row, Status, TextField, useAction } from "./ui";

export const settingsTabs = ["General", "Desktop appearance", "Voice & language", "Performance", "Personal memory", "Notifications", "Privacy"] as const;

export interface Preferences {
  size: string; position: string; reducedMotion: boolean; onTop: boolean; pinned: boolean; translucent: boolean; page?: string;
}

function Switch({ value, label, disabled, choose }: { value: boolean; label: string; disabled?: boolean; choose: (value: boolean) => void }) {
  return <button className="switch" type="button" role="switch" aria-label={label} aria-checked={value} disabled={disabled} onClick={() => choose(!value)}><span/></button>;
}

export function Settings({ tab, voice, prefs, timezone }: { tab: string; voice: Assistant; prefs?: Preferences; timezone: string }) {
  const action = useAction();
  const runtime = useResource<ReadinessResponse>(["General", "Performance", "Privacy"].includes(tab) ? "/readiness" : "");
  const desktop = window.auraDesktop;
  let panel;
  if (tab === "Desktop appearance") {
    panel = <Card title="Desktop companion">{desktop && prefs ? <>
      <Row name="Pet size"><select aria-label="Pet size" value={prefs.size} onChange={(event) => desktop.preference("size", event.target.value)}>{["small", "medium", "large"].map((size) => <option key={size} value={size}>{human(size)}</option>)}</select></Row>
      <Row name="Pet position"><select aria-label="Pet position" value={prefs.position} onChange={(event) => desktop.preference("position", event.target.value)}>{["free", "top-center", "top-right", "bottom-right", "bottom-left"].map((position) => <option key={position} value={position}>{human(position.replaceAll("-", " "))}</option>)}</select></Row>
      {([["onTop", "Always on top"], ["reducedMotion", "Reduce motion"], ["pinned", "Keep dashboard open"], ["translucent", "Smoked glass"]] as const).map(([key, name]) => <Row key={key} name={name}><Switch value={prefs[key]} label={name} choose={(value) => desktop.preference(key, String(value))}/></Row>)}
    </> : <Status empty="Desktop preferences are available in the native companion."/>}</Card>;
  } else if (tab === "Voice & language") {
    panel = <Card title="Voice & language">
      <Row name="Reply language"><select aria-label="Reply language" value={voice.state.session_language ?? "auto"} disabled={!voice.online || action.pending} onChange={(event) => {
        const value = event.target.value as Language;
        void action.run(() => voice.language(value));
      }}><option value="auto">Auto</option><option value="en">English</option><option value="hi">Hindi</option><option value="kn">Kannada</option></select></Row>
      <Row name="Recognition mode" detail="Multilingual mode recognizes English, Hindi and Kannada independently of the reply language."><select aria-label="Recognition mode" value={voice.state.stt_recognition_mode ?? "multilingual"} disabled={!voice.online || action.pending} onChange={(event) => {
        const value = event.target.value as "multilingual" | "strict";
        void action.run(() => voice.language(voice.state.session_language ?? "auto", value));
      }}><option value="multilingual">Multilingual</option><option value="strict">Strict</option></select></Row>
      <Row name="Microphone" detail={voice.state.mic_detected ? "Local microphone detected" : "Microphone unavailable"}><Switch value={!voice.state.mic_muted} label="Microphone enabled" disabled={!voice.online} choose={() => voice.control("mute")}/></Row>
      <Row name="Continuous conversation" detail="Use Talk for individual voice commands."><Switch value={!!voice.state.conversation_active} label="Continuous conversation" disabled={!voice.online || (!voice.state.conversation_active && !voice.canTalk)} choose={(value) => voice.control(value ? "continuous-start" : "continuous-stop")}/></Row>
      <Row name="Wake word" detail="Use Talk to activate AURA."/><Row name="Spoken interruption" detail="Use Stop to cancel the current turn."/>
      {action.feedback}
    </Card>;
  } else if (tab === "Performance") {
    const compute = voice.state.compute;
    panel = <Card title="Compute & performance" action={<button onClick={runtime.refresh}>Refresh measurements</button>}>
      <Row name="Compute preference"><select aria-label="Compute preference" disabled={!voice.online || action.pending} value={compute?.requested_mode ?? "auto"} onChange={(event) => {
        const mode = event.target.value;
        void action.run(async () => { await api("/voice/compute-mode", "PUT", { compute_mode: mode }); voice.refresh(); runtime.refresh(); });
      }}><option value="auto">Auto</option><option value="cpu">CPU</option><option value="gpu">GPU</option></select></Row>
      <Row name="Effective compute" detail={compute ? `STT ${compute.stt_device} · Ollama ${compute.llm_device === "cpu" ? "CPU requested" : "automatic placement"}` : "Unavailable"}/>
      {compute?.fallback_reason && <Row name="Fallback reason" detail={compute.fallback_reason}/>}
      <Row name="Speech model" detail={voice.state.stt_model || "Unavailable"}/>
      <Status {...runtime}/>
      {runtime.data && <>
        <Row name="System CPU and memory" detail={`${runtime.data.hardware.cpu_percent}% CPU · ${runtime.data.hardware.ram_used_gb} of ${runtime.data.hardware.ram_total_gb} GB RAM`}/>
        <Row name="GPU memory" detail={runtime.data.hardware.gpu.available ? `${runtime.data.hardware.gpu.vram_used_mb ?? "Unavailable"} of ${runtime.data.hardware.gpu.vram_total_mb} MB` : "Unavailable"}/>
        <Row name="Local Ollama models" detail={runtime.data.hardware.ollama.models.join(", ") || "Unavailable"}/>
      </>}{action.feedback}
    </Card>;
  } else if (tab === "Personal memory") {
    panel = <Memory/>;
  } else if (tab === "Notifications") {
    panel = <NotificationSettings/>;
  } else if (tab === "Privacy") {
    panel = <Card title="Privacy"><Status {...runtime}/><Row name="Connectivity mode" detail={runtime.data ? human(runtime.data.connectivity.mode) : "Unavailable"}/><MemorySettings privacy/>
      <ConfirmButton label="Clear conversation history" question="Clear AURA’s conversation history?" disabled={action.pending || !voice.online} action={() => void action.run(async () => {
        await api("/conversation/history", "DELETE"); refreshResources("/conversation/history");
      }, "Conversation history cleared.")}/>{action.feedback}</Card>;
  } else {
    panel = <Card title="General"><Row name="AURA EDGE" detail="Local desktop assistant"/><Row name="Time zone" detail={timezone}/><Row name="Local backend" detail={voice.online ? "Connected" : "Disconnected"}/><Status {...runtime}/>
      {runtime.data && <><Row name="PostgreSQL" detail={runtime.data.database.connected ? "Connected" : runtime.data.database.error ?? "Unavailable"}/><Row name="Ollama" detail={runtime.data.hardware.ollama.reachable ? "Available on loopback" : "Unavailable"}/></>}
    </Card>;
  }
  return panel;
}

interface SavedMemory { id: string; original_text: string; category: string; enabled: boolean }
interface MemoryPreferences { memory_enabled: boolean; auto_save_preferences: boolean; semantic_search_enabled: boolean; persist_conversation_history: boolean; retention_days: number | null }
const memoryUpdates = /^memory_/;
function MemorySettings({ privacy }: { privacy?: boolean }) {
  const settings = useResource<MemoryPreferences>("/memory/settings", memoryUpdates);
  const action = useAction();
  async function save(payload: Record<string, boolean | number>) {
    await api("/memory/settings", "PUT", payload);
    refreshResources("/memory");
  }
  const fields = privacy ? [["persist_conversation_history", "Save conversation history"]] as const :
    [["memory_enabled", "Personal memory"], ["auto_save_preferences", "Automatically save preferences"], ["semantic_search_enabled", "Semantic search"]] as const;
  return <>
    <Status {...settings}/>
    {settings.data && <>
      {fields.map(([key, label]) => <Row key={key} name={label}><Switch label={label} value={settings.data![key]} disabled={action.pending} choose={(value) => void action.run(() => save({ [key]: value }))}/></Row>)}
      {privacy && <form className="inline-form compact-form" key={settings.data.retention_days ?? "none"} onSubmit={(event) => {
        const days = field(formData(event), "days");
        void action.run(() => save(days ? { retention_days: Number(days) } : { clear_retention_days: true }), "Retention saved.");
      }}><TextField name="days" label="Memory retention (days)" type="number" min={1} max={3650} value={settings.data.retention_days ?? ""} placeholder="No limit"/><button disabled={action.pending}>Save</button></form>}
    </>}{action.feedback}
  </>;
}
function Memory() {
  const [query, search] = useState("");
  const [editing, select] = useState<SavedMemory>();
  const list = useResource<{ items: SavedMemory[]; memory_enabled: boolean }>(query ? `/memory/search?q=${encodeURIComponent(query)}` : "/memory?enabled_only=false", memoryUpdates);
  const action = useAction();
  return <Card title="Personal memory">
    <MemorySettings/>
    <form className="inline-form compact-form" onSubmit={(event) => search(field(formData(event), "q"))}><input name="q" aria-label="Search saved memory" placeholder="Search saved memory"/><button>Search</button></form>
    <Status {...list} empty={list.data?.memory_enabled === false ? "Memory is disabled." : !list.data?.items.length ? "No saved memories found." : undefined}/>
    {list.data?.items.map((item) => <Row key={item.id} name={item.original_text} detail={`${human(item.category)} · ${item.enabled ? "Enabled" : "Disabled"}`}>
      <button onClick={() => select(item)}>Edit</button>
      <ConfirmButton label="Delete" question="Delete this saved memory?" disabled={action.pending} action={() => void action.run(async () => {
        await api(`/memory/${item.id}`, "DELETE");
        if (editing?.id === item.id) select(undefined);
        list.refresh();
      })}/>
    </Row>)}
    {editing && <form key={editing.id} className="form compact-form" onSubmit={(event) => {
      const data = formData(event);
      void action.run(async () => {
        await api(`/memory/${editing.id}`, "PATCH", { original_text: field(data, "text"), enabled: data.has("enabled") });
        select(undefined);
        list.refresh();
      }, "Memory saved.");
    }}><TextField name="text" label="Saved preference" required value={editing.original_text}/><label className="checkbox-line"><input type="checkbox" name="enabled" defaultChecked={editing.enabled}/> Enabled</label><button className="primary" disabled={action.pending}>Save memory</button></form>}
    <ConfirmButton label="Clear saved memory" question="Permanently delete all saved memories?" disabled={action.pending || !list.data?.memory_enabled} action={() => void action.run(async () => {
      await api("/memory", "DELETE"); select(undefined); list.refresh();
    }, "Saved memory cleared.")}/>
    {action.feedback}
  </Card>;
}

function NotificationSettings() {
  const prefs = useResource<NoticePrefs>("/notifications/preferences", /^notification_preferences_/);
  const action = useAction();
  return <Card title="Notifications">
    <Status {...prefs}/>
    {prefs.data && <form className="form" key={JSON.stringify(prefs.data)} onSubmit={(event) => {
      const data = formData(event);
      void action.run(async () => {
        await api("/notifications/preferences", "PUT", {
          visual_enabled: data.has("visual"), spoken_enabled: data.has("spoken"), quiet_hours_enabled: data.has("quiet"),
          quiet_hours_start: field(data, "start") || null, quiet_hours_end: field(data, "end") || null, timezone_name: field(data, "timezone"),
        });
        prefs.refresh();
      }, "Preferences saved.");
    }}>
      <label className="checkbox-line"><input type="checkbox" name="visual" defaultChecked={prefs.data.visual_enabled}/> Visual notifications</label>
      <label className="checkbox-line"><input type="checkbox" name="spoken" defaultChecked={prefs.data.spoken_enabled}/> Spoken notifications</label>
      <label className="checkbox-line"><input type="checkbox" name="quiet" defaultChecked={prefs.data.quiet_hours_enabled}/> Quiet hours</label>
      <TextField name="start" label="Quiet hours start" type="time" value={prefs.data.quiet_hours_start}/>
      <TextField name="end" label="Quiet hours end" type="time" value={prefs.data.quiet_hours_end}/>
      <TextField name="timezone" label="Time zone" required value={prefs.data.timezone_name}/>
      <button className="primary" disabled={action.pending}>Save preferences</button>
    </form>}{action.feedback}
  </Card>;
}

export function useNotices() {
  return useResource<{ items: Notice[]; unread_count: number }>("/notifications", /^notification_/);
}
export function NotificationCenter({ notices, timezone, close }: { notices: ReturnType<typeof useNotices>; timezone: string; close: () => void }) {
  const action = useAction();
  return <aside className="notification-popover" aria-label="Notification center">
    <Card title="Notifications" action={<button onClick={close}>Close</button>}>
      <button disabled={action.pending || !notices.data?.unread_count} onClick={() => void action.run(async () => { await api("/notifications/read-all", "POST"); notices.refresh(); })}>Mark all read</button>
      <Status {...notices} empty={!notices.data?.items.length ? "No notifications." : undefined}/>
      {notices.data?.items.map((notice) => <Row key={notice.id} name={notice.is_read ? notice.title : <strong>{notice.title}</strong>} detail={<>{notice.message}<br/>{formatDate(notice.created_at_utc, timezone)}</>}>
        {!notice.is_read && <button disabled={action.pending} onClick={() => void action.run(async () => { await api(`/notifications/${notice.id}/read`, "POST"); notices.refresh(); })}>Read</button>}
        <button disabled={action.pending} onClick={() => void action.run(async () => { await api(`/notifications/${notice.id}/dismiss`, "POST"); notices.refresh(); })}>Dismiss</button>
      </Row>)}{action.feedback}
    </Card>
  </aside>;
}

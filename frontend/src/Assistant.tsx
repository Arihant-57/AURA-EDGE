import { FormEvent, useState } from "react";
import { api, Language, LiveEvent, Message, stateLabels, Turn, Voice } from "./api/desktop";
import { refreshResources, useLive, useResource } from "./live";
import { Card, ConfirmButton, field, formData, human, Icon, IconButton, Row, Status, useAction } from "./ui";

const voiceEvents = /^(assistant_state_change|mic_mute_change|conversation_mode_change|language_change)$/;
const zeros = Array<number>(14).fill(0);
interface ActiveTurn extends Partial<Turn> { turn_id: string; provisional?: string }

export function useVoice(online: boolean) {
  const voice = useResource<Voice>("/voice/status", voiceEvents);
  const [events, setEvents] = useState<Partial<Voice>>({});
  const [wave, setWave] = useState(zeros);
  const [latest, setLatest] = useState<ActiveTurn | null>(null);
  const action = useAction();
  const typed = useAction();
  const state = { ...voice.data, ...events };
  const listening = state.active_state === "LISTENING" && !state.conversation_active;
  const label = !online ? "Backend disconnected" : state.mic_muted && state.active_state === "IDLE" ? "Microphone muted" :
    state.active_state ? stateLabels[state.active_state] : "Connecting…";

  function onEvent(event: LiveEvent) {
    const p = event.payload;
    switch (event.type) {
      case "connected":
      case "disconnected":
        setEvents({});
        setWave(zeros);
        setLatest((old) => old?.outcome ? old : null);
        break;
      case "voice_status":
        setEvents((old) => ({
          ...old, active_state: p.active_state as Voice["active_state"], active_turn_id: p.active_turn_id as Voice["active_turn_id"],
          mic_muted: Boolean(p.mic_muted), mic_detected: Boolean(p.mic_detected),
          conversation_active: Boolean(p.conversation_active), conversation_mode: p.conversation_mode as Voice["conversation_mode"],
        }));
        break;
      case "assistant_state_change":
        setEvents((old) => ({ ...old, active_state: p.state as Voice["active_state"], active_turn_id: p.turn_id as Voice["active_turn_id"], session_language: p.language as Language, ...(p.recognition_mode ? { stt_recognition_mode: p.recognition_mode as Voice["stt_recognition_mode"] } : {}) }));
        if (p.state !== "LISTENING") setWave(zeros);
        if (p.turn_id && p.state === "LISTENING") setLatest((old) => old?.turn_id === p.turn_id ? old : { turn_id: String(p.turn_id) });
        break;
      case "audio_level":
        if (typeof p.rms_db === "number") {
          const level = Math.max(0, Math.min(1, (p.rms_db + 60) / 60));
          setWave((prior) => [...prior.slice(1), level]);
        }
        break;
      case "mic_mute_change":
        setEvents((old) => ({ ...old, mic_muted: Boolean(p.muted) }));
        setWave(zeros);
        break;
      case "conversation_mode_change":
        setEvents((old) => ({ ...old, conversation_active: Boolean(p.active), conversation_mode: p.mode as Voice["conversation_mode"] }));
        break;
      case "transcript_ready":
        if (p.turn_id) setLatest((old) => old && old.turn_id === p.turn_id ? { ...old, transcript: String(p.transcript) } : { turn_id: String(p.turn_id), transcript: String(p.transcript) });
        break;
      case "response_text_delta":
        if (p.turn_id) setLatest((old) => old && old.turn_id === p.turn_id
          ? old.outcome ? old : { ...old, provisional: (old.provisional ?? "") + String(p.token) }
          : { turn_id: String(p.turn_id), provisional: String(p.token) });
        break;
      case "response_completed":
        if (p.turn_id) setLatest((old) => ({ ...(old?.turn_id === p.turn_id ? old : {}), ...p, turn_id: String(p.turn_id) } as ActiveTurn));
        break;
      case "error":
        if (p.turn_id) setLatest({ turn_id: String(p.turn_id), outcome: "runtime_failure", error: String(p.message) });
        break;
      case "conversation_history_updated":
        if (p.count === 0) setLatest(null);
        break;
    }
  }

  useLive(onEvent);
  function control(kind: "talk" | "stop" | "mute" | "continuous-start" | "continuous-stop") {
    if (window.auraDesktop) {
      window.auraDesktop.voice(kind);
      return;
    }
    async function request() {
      if (kind === "talk") {
        if (listening) await api("/voice/mic/stop", "POST", { speak: true });
        else {
          const result = await api<{ started: boolean; reason?: string }>("/voice/mic/start", "POST");
          if (!result.started) throw new Error(result.reason ?? "Microphone did not start.");
        }
      } else if (kind === "mute") await api("/voice/mic/mute", "PUT", { muted: !state.mic_muted });
      else if (kind === "continuous-start") await api("/voice/conversation/start", "POST", { mode: "continuous", speak: true });
      else if (kind === "continuous-stop" || state.conversation_active) await api("/voice/conversation/stop", "POST");
      else await api("/voice/cancel", "POST", { turn_id: null });
      voice.refresh();
    }
    if (kind === "stop") {
      void request().catch((error) => setLatest({ turn_id: "cancel-error", outcome: "runtime_failure", error: error.message }));
    } else void action.run(request);
  }

  function type(text: string) {
    if (!online || !text || state.active_state !== "IDLE" || typed.pending) return;
    setLatest({ turn_id: "pending", transcript: text });
    void typed.run(async () => {
      const result = await api<Turn>("/intent/process", "POST", { text, source: "text_input", speak: true });
      setLatest({ ...result, transcript: result.transcript || text });
      refreshResources("/conversation/history");
    });
  }

  async function language(value: Language, recognition = state.stt_recognition_mode ?? "multilingual") {
    const response = await api<{ session_language: Language; recognition_mode: Voice["stt_recognition_mode"] }>("/voice/language", "PUT", { language: value, recognition_mode: recognition });
    setEvents((old) => ({ ...old, session_language: response.session_language, stt_recognition_mode: response.recognition_mode }));
    voice.refresh();
  }

  return {
    state, label, online, listening, latest, control, type, language, refresh: voice.refresh,
    wave: state.mic_muted || state.active_state !== "LISTENING" ? zeros : wave,
    feedback: <>{action.feedback}{typed.feedback}<Status error={voice.error}/></>,
    canTalk: online && state.mic_detected && !state.mic_muted && !state.conversation_active && (state.active_state === "IDLE" || listening) && !action.pending && !typed.pending,
    canType: online && state.active_state === "IDLE" && !typed.pending,
    canStop: online && (state.conversation_active || (state.active_state && state.active_state !== "IDLE") || typed.pending),
  };
}
export type Assistant = ReturnType<typeof useVoice>;
export function VoiceButtons({ voice, mute = false }: { voice: Assistant; mute?: boolean }) {
  return <div className="voice-buttons">
    <IconButton icon="mic" active label={voice.listening ? "Send recording" : "Talk to AURA"} disabled={!voice.canTalk} onClick={() => voice.control("talk")}/>
    <div className="waveform" aria-label="Live microphone input" role="img">{voice.wave.map((value, i) => <i key={i} style={{ height: `${2 + value * 22}px` }}/>)}</div>
    {mute && <IconButton icon={voice.state.mic_muted ? "mute" : "mic"} label={voice.state.mic_muted ? "Unmute microphone" : "Mute microphone"} disabled={!voice.online} onClick={() => voice.control("mute")}/>}
    <IconButton icon="stop" label="Stop assistant" disabled={!voice.canStop} onClick={() => voice.control("stop")}/>
  </div>;
}
export function Ask({ voice, header, onSend }: { voice: Assistant; header?: boolean; onSend?: () => void }) {
  function submit(event: FormEvent<HTMLFormElement>) {
    const form = event.currentTarget;
    const text = field(formData(event), "ask");
    if (!voice.canType || !text) return;
    voice.type(text);
    onSend?.();
    form.reset();
  }
  return <form className={`ask ${header ? "ask-header" : ""}`} onSubmit={submit}>
    <Icon name={header ? "search" : "assistant"}/>
    <input autoComplete="off" aria-label="Ask AURA" name="ask" maxLength={2000} placeholder="Ask AURA anything…" disabled={!voice.online}/>
    {!header && <button disabled={!voice.canType} className="primary">Send</button>}
  </form>;
}

export function AssistantView({ voice }: { voice: Assistant }) {
  const history = useResource<{ messages: Message[]; count: number }>("/conversation/history", /^conversation_history_updated$/);
  const [spokenHint, setHint] = useState("");
  const action = useAction();
  const quick = [["Summarize my day", "Give me my daily briefing"], ["Set a timer", "Set a timer for 5 minutes"], ["Show reminders", "Show my reminders"]] as const;
  const latest = voice.latest;
  const messages = history.data?.messages ?? [];
  const inHistory = latest?.outcome && messages.some((message) => message.turn_id === latest.turn_id);
  return <div className="detail-grid">
    <Card title="Conversation" action={<div className="row-actions"><span className="meta-pill">{voice.label}</span><ConfirmButton label="New conversation" question="Clear the current conversation and start a new one?" disabled={!voice.online || action.pending || Boolean(voice.canStop)} action={() => void action.run(async () => { await api("/conversation/history", "DELETE"); history.refresh(); })}/></div>}>
      <div className="conversation" aria-live="polite">
        <Status {...history} empty={!messages.length && !latest ? "Ask AURA about your home, tasks, or reminders." : undefined}/>
        {messages.map((message, index) => <div className={`bubble ${message.role}`} key={`${message.turn_id}-${index}`}><small>{message.role === "user" ? "You" : "AURA"}</small><p>{message.content}</p></div>)}
        {latest && !latest.silent && <>
          {!inHistory && latest.transcript && <div className="bubble user"><small>You</small><p>{latest.transcript}</p></div>}
          {!inHistory && (latest.response_text || latest.provisional) && <div className="bubble assistant"><small>{latest.response_text ? "AURA" : "AURA · drafting"}</small><p>{latest.response_text || latest.provisional}</p></div>}
          {latest.action_executed && <p className="inline-success">Verified action{latest.outcome === "cancelled" ? " preserved" : ""}</p>}
          {latest.outcome && latest.outcome !== "success" && <p className="inline-error">{human(latest.outcome)}{latest.error ? ` · ${latest.error}` : ""}</p>}
        </>}
      </div>
      <Ask voice={voice}/>{voice.feedback}
    </Card>
    <div className="stack">
      <Card title="Quick actions">{quick.map(([name, prompt]) => <Row key={name} icon="automation" name={name}><button disabled={!voice.canType} onClick={() => voice.type(prompt)}>Ask</button></Row>)}</Card>
      <Card title="Audio controls" action={<VoiceButtons voice={voice} mute/>}>
        <label className="field">Reply language<select value={voice.state.session_language ?? "auto"} disabled={!voice.online || action.pending} onChange={(event) => {
          const value = event.target.value as Language;
          void action.run(() => voice.language(value));
        }}><option value="auto">Auto</option><option value="en">English</option><option value="hi">Hindi</option><option value="kn">Kannada</option></select></label>
        <button disabled={!voice.online || (!voice.state.conversation_active && !voice.canTalk)} onClick={() => {
          voice.control(voice.state.conversation_active ? "continuous-stop" : "continuous-start");
          setHint("");
        }}>{voice.state.conversation_active ? "End continuous conversation" : "Start continuous conversation"}</button>
        <button className="text-button" onClick={() => setHint("Use Talk to record a command, Send to process it, and Stop to cancel the active turn.")}>Push-to-talk help</button>
        {spokenHint && <p className="muted">{spokenHint}</p>}{action.feedback}
      </Card>
    </div>
  </div>;
}

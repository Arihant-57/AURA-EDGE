import { FormEvent, ReactNode, useRef, useState } from "react";

const glyphs: Record<string, ReactNode> = {
  logo: <path d="M12 2 2 21l10-6 10 6-10-19Zm0 7 5 8-5-3-5 3Z" fill="currentColor" stroke="none"/>,
  home: <><path d="m3 10 9-7 9 7"/><path d="M5 9v12h5v-6h4v6h5V9"/></>,
  automation: <><path d="m12 2 2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4Z"/><path d="M20 2v5M17.5 4.5h5"/></>,
  rooms: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
  tasks: <><rect x="4" y="5" width="16" height="16" rx="2"/><path d="m8 13 3 3 5-6M8 3v4m8-4v4"/></>,
  routines: <><path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/></>,
  assistant: <path d="M4 4h16v12h-7l-5 5v-5H4Zm4 6h8"/>,
  household: <><path d="M3 8h18l-2 12H5L3 8Zm4 0 3-5m7 5-3-5M9 11v5m6-5v5"/></>,
  settings: <><path d="m10 3-.8 2.3-2 .9-2.2-.7-2 3.5 1.7 1.6v2.8L3 15l2 3.5 2.2-.7 2 .9L10 21h4l.8-2.3 2-.9 2.2.7 2-3.5-1.7-1.6v-2.8L21 9l-2-3.5-2.2.7-2-.9L14 3Z"/><circle cx="12" cy="12" r="3"/></>,
  mic: <><rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v1a7 7 0 0 0 14 0v-1M12 18v4m-3 0h6"/></>,
  mute: <path d="m3 3 18 18M9 5a3 3 0 0 1 6 0v5M5 10v1a7 7 0 0 0 11 6m3-7v1m-7 7v4m-3 0h6"/>,
  stop: <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>,
  send: <path d="m5 12 5 5 9-10"/>,
  bell: <path d="M18 9a6 6 0 0 0-12 0v5l-2 3h16l-2-3Zm-8 11a2 2 0 0 0 4 0"/>,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v5m10-5v5M3 10h18m-13 5h2m4 0h2"/></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>,
  left: <path d="m14 5-7 7 7 7"/>,
  right: <path d="m9 5 7 7-7 7"/>,
  expand: <path d="M8 4H4v4m12-4h4v4m0 8v4h-4M4 16v4h4"/>,
  minimize: <path d="M5 12h14"/>,
  shutdown: <><path d="M12 2v9M6 5a9 9 0 1 0 12 0"/></>,
  pin: <path d="m15 3 6 6-4 1-3 5-5-5 5-3 1-4ZM9 15l-5 5"/>,
  plus: <path d="M12 4v16M4 12h16"/>,
  light: <path d="M9 18h6m-5 3h4m-6-7a6 6 0 1 1 8 0l-1 2H9l-1-2Z"/>,
  fan: <><circle cx="12" cy="12" r="2"/><path d="M12 10c-7-2-2-10 2-6 1 1 0 4-2 6Zm2 2c2-7 10-2 6 2-1 1-4 0-6-2Zm-2 2c7 2 2 10-2 6-1-1 0-4 2-6Zm-2-2c-2 7-10 2-6-2 1-1 4 0 6 2Z"/></>,
  television: <><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M12 17v4m-5 0h10"/></>,
  air_conditioner: <><rect x="3" y="4" width="18" height="10" rx="2"/><path d="M6 10h12m-9 6-2 4m8-4 2 4m-5-4v5"/></>,
  bolt: <path d="m13 2-9 12h7l-1 8 10-12h-8l1-8Z"/>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></>,
  close: <path d="m5 5 14 14M5 19 19 5"/>,
  sofa: <><path d="M4 19v2m16-2v2M6 13V8a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v5M4 13H3v6h18v-6h-1l-2 2H6Z"/></>,
};
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round">{glyphs[name] ?? glyphs.rooms}</svg>;
}
export function IconButton({ icon, label, onClick, disabled, active }: { icon: string; label: string; onClick: () => void; disabled?: boolean; active?: boolean }) {
  return <button type="button" className={`icon-button ${active ? "accent" : ""}`} title={label} aria-label={label} disabled={disabled} onClick={onClick}><Icon name={icon}/></button>;
}
export function Card({ title, action, children, className = "" }: { title: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`card ${className}`}><div className="card-header"><h2>{title}</h2>{action}</div>{children}</section>;
}
export function Tabs<T extends string>({ options, selected, choose }: { options: readonly T[]; selected: T; choose: (item: T) => void }) {
  return <div className="tabs">{options.map((item) => <button type="button" key={item} aria-pressed={selected === item} onClick={() => choose(item)}>{item}</button>)}</div>;
}
export function Row({ name, detail, icon, children }: { name: ReactNode; detail?: ReactNode; icon?: string; children?: ReactNode }) {
  return <div className="data-row">{icon && <span className="row-icon"><Icon name={icon}/></span>}<div className="row-text"><div>{name}</div>{detail && <small>{detail}</small>}</div>{children && <div className="row-actions">{children}</div>}</div>;
}
export function Status({ error, loading, empty }: { error?: string; loading?: boolean; empty?: string }) {
  if (error) return <p className="inline-error" role="status">{error}</p>;
  if (loading) return <p className="empty">Loading…</p>;
  return empty ? <p className="empty">{empty}</p> : null;
}
export function useAction() {
  const locked = useRef(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  async function run(action: () => Promise<unknown>, success = "") {
    if (locked.current) return false;
    locked.current = true;
    setPending(true);
    setMessage("");
    setFailed(false);
    try {
      await action();
      setMessage(success);
      return true;
    } catch (error) {
      setFailed(true);
      setMessage(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      locked.current = false;
      setPending(false);
    }
  }
  return { pending, run, feedback: message && <p role="status" className={failed ? "inline-error" : "inline-success"}>{message}</p> };
}
export const human = (raw: string) => raw.replaceAll("_", " ").replace(/^./, (first) => first.toUpperCase());
export const formatDate = (value?: string | null, timezone = "Asia/Kolkata") => value ? new Intl.DateTimeFormat("en-IN", { timeZone: timezone, dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "No date";
export const dateKey = (value: string | Date, timezone: string) => new Intl.DateTimeFormat("sv-SE", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(value));
export function localDate(value: string | undefined | null, timezone: string) {
  if (!value) return "";
  const date = new Date(value);
  const time = new Intl.DateTimeFormat("en-GB", { timeZone: timezone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
  return `${dateKey(date, timezone)}T${time}`;
}
export function dateInput(value: string, timezone: string) {
  const wallClock = Date.parse(`${value}:00Z`);
  if (!Number.isFinite(wallClock)) throw new Error("Enter a valid date and time.");
  let result = wallClock;
  for (let attempt = 0; attempt < 3; attempt++) {
    const observed = Date.parse(`${localDate(new Date(result).toISOString(), timezone)}:00Z`);
    if (observed === wallClock) return new Date(result).toISOString();
    result += wallClock - observed;
  }
  throw new Error(`This local time does not exist in ${timezone}.`);
}
export function recurrence(kind: string, wallClock: string) {
  const [hour, minute] = wallClock.split("T")[1].split(":").map(Number);
  const weekday = (new Date(`${wallClock}:00Z`).getUTCDay() + 6) % 7;
  return kind === "weekly" ? { kind, hour, minute, weekdays: [weekday] } :
    kind === "daily" ? { kind, hour, minute } : { kind: "once" };
}
export function formData(event: FormEvent<HTMLFormElement>) {
  event.preventDefault();
  return new FormData(event.currentTarget);
}
export const field = (data: FormData, key: string) => String(data.get(key) ?? "").trim();
export function TextField({ name, label, type = "text", value, required, min, max, step, placeholder }: { name: string; label: string; type?: string; value?: string | number; required?: boolean; min?: number; max?: number; step?: string; placeholder?: string }) {
  return <label className="field">{label}<input name={name} type={type} defaultValue={value} required={required} min={min} max={max} step={step} placeholder={placeholder}/></label>;
}
export function SelectField({ name, label, options, value }: { name: string; label: string; options: readonly string[]; value?: string }) {
  return <label className="field">{label}<select name={name} defaultValue={value}>{options.map((option) => <option key={option} value={option}>{human(option)}</option>)}</select></label>;
}
export function ConfirmButton({ label, question, action, disabled }: { label: string; question: string; action: () => void; disabled?: boolean }) {
  return <button type="button" className="danger" disabled={disabled} onClick={() => { if (window.confirm(question)) action(); }}>{label}</button>;
}
export function remaining(seconds: number) {
  const whole = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")} left`;
}

import { useEffect, useState } from "react";
import { Activities, api, Briefing, CalendarEvent, Capability, commandDevice, Device, Energy, Items, Job, Room, Task, Timer, Value } from "./api/desktop";
import { refreshResources, useResource } from "./live";
import { Card, dateKey, field, formatDate, formData, human, Icon, IconButton, remaining, Row, Status, Tabs, TextField, useAction } from "./ui";

const homeEvents = /^(device_|simulated_sensor|activity_|connected)/;
const jobEvents = /^(schedule_|reminder_)/;
const roomOrder = ["living_room", "bedroom", "kitchen", "study", "utility_room"];
const roomLabels: Record<string, string> = { living_room: "Living Room", bedroom: "Bedroom", kitchen: "Kitchen", study: "Study", utility_room: "Utility Room" };
const deviceLabels: Record<string, string> = {
  "tv.living": "Smart TV",
  "light.living_main": "Ceiling Light",
  "fan.living_ceiling": "Ceiling Fan",
  "ac.living": "Air Conditioner",
  "light.bedroom_main": "Bedroom Light",
  "fan.bedroom_ceiling": "Ceiling Fan",
  "ac.bedroom": "Bedroom AC",
  "curtain.bedroom": "Bedroom Curtains",
};
const controlLabels: Record<string, string> = { door_open: "Door open", power: "Power", activity: "Cycle action" };
const units: Record<string, string> = { temperature: "°C", humidity: "%", brightness: "%", volume: "%", position: "%" };
export const deviceName = (device: Device) => deviceLabels[device.id] ?? device.name;
const roomName = (id: string) => roomLabels[id] ?? human(id);
const supportedActivity = (state: Value) => state === "running" || state === "cleaning" ? ["pause", "cancel", "dock"] :
  state === "paused" ? ["resume", "cancel", "dock"] : ["start", "dock"];

export function CapabilityInput({ name, spec, value, onChange, label, disabled }: { name: string; spec: Capability; value?: Value; onChange: (value: Value) => void; label: string; disabled?: boolean }) {
  const [draft, setDraft] = useState<number | null>(null);
  if (spec.type === "bool") return <button className="switch" role="switch" type="button" aria-label={label} aria-checked={value === true} disabled={disabled} onClick={() => onChange(value !== true)}><span/></button>;
  if (name === "activity" && spec.enum) return <div className="device-operations">
    {spec.enum.map((action) => <button type="button" key={action} disabled={disabled} onClick={() => onChange(action)}>{human(action)}</button>)}
  </div>;
  if (spec.enum) return <select aria-label={label} disabled={disabled} value={String(value ?? "")} onChange={(e) => onChange(e.currentTarget.value)}>
    {value == null && <option value="" disabled>Choose action</option>}
    {spec.enum.map((item) => <option key={item} value={item}>{human(item)}</option>)}
  </select>;
  const { min, max } = spec;
  if (min === undefined || max === undefined) return null;
  const current = draft ?? Number(value ?? min);
  if ((name === "temperature" || name === "speed") && Number.isInteger(min) && Number.isInteger(max) && max - min <= 50) {
    const choices = Array.from({ length: max - min + 1 }, (_, i) => min + i);
    return <select aria-label={label} disabled={disabled} value={current} onChange={(event) => onChange(Number(event.currentTarget.value))}>
      {choices.map((choice) => <option key={choice} value={choice}>{choice}{units[name]}</option>)}
    </select>;
  }
  const percent = (current - min) / Math.max(1, max - min) * 100;
  return <div className="slider"><output>{current}{units[name]}</output><input aria-label={label} type="range" min={min} max={max} value={current} disabled={disabled}
    style={{ background: `linear-gradient(to right, var(--accent) ${percent}%, var(--range-track) ${percent}%)` }}
    onChange={(e) => setDraft(e.currentTarget.valueAsNumber)}
    onPointerUp={(e) => { if (draft !== null) onChange(e.currentTarget.valueAsNumber); setDraft(null); }}
    onPointerCancel={() => setDraft(null)}
    onKeyUp={() => { if (draft !== null) onChange(draft); setDraft(null); }}
    onBlur={() => { if (draft !== null) onChange(draft); setDraft(null); }}
  /></div>;
}

function DeviceCard({ device, compact, schedule }: { device: Device; compact?: boolean; schedule?: () => void }) {
  const result = useAction();
  const [details, setDetails] = useState(false);
  const [history, setHistory] = useState(false);
  const [simulator, setSimulator] = useState(false);
  const audit = useResource<{ action: string; context_id: string; created_at_utc?: string; source?: string }[]>(history ? `/smart-home/history?device_id=${encodeURIComponent(device.id)}&limit=8` : "", homeEvents);
  const sim = `/smart-home/simulator/devices/${encodeURIComponent(device.id)}`;
  const name = deviceName(device);
  const entries = Object.entries(device.capabilities);
  const summary = device.available ? Object.entries(device.state).filter(([key]) => key !== "remaining_seconds").slice(0, 3).map(([key, value]) =>
    typeof value === "boolean" ? (key === "door_open" ? (value ? "Door open" : "Door closed") : (value ? "On" : "Off")) :
      typeof value === "number" ? `${human(key)} ${value}${units[key] ?? ""}` : human(value)).join(" · ") : "Unavailable";
  const prominent = entries.find(([key]) => key === "power");
  const additional = entries.filter(([key]) => key !== prominent?.[0]);
  function control([key, spec]: [string, Capability]) {
    const options = key === "activity" ? { ...spec, enum: spec.enum?.filter((option) => supportedActivity(device.state.activity).includes(option)) } : spec;
    return <CapabilityInput key={key} name={key} spec={options} value={device.state[key]} label={`${name} ${controlLabels[key] ?? key}`}
      disabled={!device.available || result.pending} onChange={(value) => void result.run(async () => {
        await commandDevice(device, key, value);
        refreshResources("/smart-home/devices", "/smart-home/activities", "/smart-home/energy");
      })}/>;
  }
  return <article className="device-card">
    <Row icon={device.device_type} name={additional.length ? <button className="text-button device-name" onClick={() => setDetails(!details)} aria-expanded={details}>{name}</button> : name} detail={summary}>
      {prominent && control(prominent)}
      {additional.length > 0 && <button type="button" className="icon-button device-disclosure" onClick={() => setDetails(!details)} aria-label={`${details ? "Hide" : "Show"} ${name} controls`} aria-expanded={details}><Icon name="right" size={16}/></button>}
    </Row>
    {details && additional.length > 0 && <div className="device-controls">{additional.map((entry) => <div className="control-line" key={entry[0]}><span>{controlLabels[entry[0]] ?? human(entry[0])}</span>{control(entry)}</div>)}</div>}
    {!compact && <div className="device-footer">
      <button onClick={() => setHistory(!history)}>History</button>
      {entries.length > 0 && <button onClick={schedule}>Schedule</button>}
      <button onClick={() => setSimulator(!simulator)}>Simulator</button>
    </div>}
    {history && <div className="device-controls">
      <Status {...audit} empty={!audit.data?.length ? "No state changes recorded." : undefined}/>
      {audit.data?.map((item, index) => <small key={`${item.context_id}-${index}`}>{human(item.action)} · {item.source} · {formatDate(item.created_at_utc)}</small>)}
    </div>}
    {simulator && <div className="device-controls">
      <small>Simulator demonstration</small>
      <button disabled={result.pending} onClick={() => void result.run(async () => {
        await api(`${sim}/availability`, "PUT", { available: !device.available });
        refreshResources("/smart-home");
      })}>{device.available ? "Set unavailable" : "Set available"}</button>
      {["running", "cleaning", "paused"].includes(String(device.state.activity)) && <button disabled={result.pending} onClick={() => void result.run(async () => {
        await api(`${sim}/complete`, "POST");
        refreshResources("/smart-home");
      })}>Complete cycle</button>}
      {device.device_type === "climate_sensor" && <form className="form" onSubmit={(event) => {
        const data = formData(event);
        void result.run(async () => {
          await api(`${sim}/sensor`, "POST", { state: { temperature: Number(field(data, "temperature")), humidity: Number(field(data, "humidity")) } });
          refreshResources("/smart-home");
        });
      }}><TextField name="temperature" label="Temperature (°C)" type="number" step="0.1" required value={Number(device.state.temperature)}/><TextField name="humidity" label="Humidity (%)" type="number" min={0} max={100} step="0.1" required value={Number(device.state.humidity)}/><button disabled={result.pending}>Apply readings</button></form>}
    </div>}
    {result.feedback}
  </article>;
}

export function useHome() {
  const rooms = useResource<Room[]>("/smart-home/rooms", homeEvents);
  const devices = useResource<Device[]>("/smart-home/devices", homeEvents);
  const [selection, select] = useState("living_room");
  const sorted = [...(rooms.data ?? [])].sort((a, b) => roomOrder.indexOf(a.id) - roomOrder.indexOf(b.id));
  const room = sorted.find((item) => item.id === selection)?.id ?? sorted[0]?.id ?? selection;
  return { rooms, devices, sorted, room, select, roomDevices: (devices.data ?? []).filter((device) => device.room === room) };
}

type HomeData = ReturnType<typeof useHome>;
export function Rooms({ data, compact, openRooms, schedule }: { data: HomeData; compact?: boolean; openRooms?: () => void; schedule?: (id: string) => void }) {
  const { room, sorted, select, roomDevices, devices } = data;
  const sensor = roomDevices.find((device) => device.device_type === "climate_sensor" && device.available);
  const brightness = Math.max(0, ...roomDevices.filter((device) => device.device_type === "light" && device.state.power === true).map((device) => Number(device.state.brightness ?? 100)));
  const watts = roomDevices.reduce((sum, device) => sum + device.estimated_power_w, 0);
  const roomIndex = sorted.findIndex((item) => item.id === room);
  const shift = (by: number) => select(sorted[(roomIndex + by + sorted.length) % sorted.length].id);
  const order = ["television", "light", "fan", "air_conditioner"];
  const ranked = (device: Device) => order.includes(device.device_type) ? order.indexOf(device.device_type) : order.length;
  return <>
    <section className="room-scene" aria-label={roomName(room)}>
      <img key={room} src={`./rooms/${roomOrder.includes(room) ? room : "hallway"}.webp`} alt={`Simulated ${roomName(room).toLowerCase()}`} style={{ filter: `brightness(${0.76 + brightness / 300})` }}/>
      <div className="scene-top"><span className="sim-badge"><i/>Simulated home</span>{devices.data && <span className="scene-reading">{sensor && <span>{sensor.state.temperature}°C <span className="reading-dot">·</span> {sensor.state.humidity}% <span className="reading-dot">·</span> </span>}<Icon name="bolt" size={16}/> {watts} W</span>}</div>
      {sorted.length > 1 && <div className="scene-arrows"><IconButton icon="left" label="Previous room" onClick={() => shift(-1)}/><IconButton icon="right" label="Next room" onClick={() => shift(1)}/></div>}
      <div className="scene-bottom"><div className="room-tabs">{sorted.map(({ id }) => <button key={id} aria-pressed={id === room} onClick={() => select(id)}>{id === "living_room" && <Icon name="sofa" size={18}/>} {roomName(id)}</button>)}</div>{openRooms && <IconButton icon="expand" label="Open room details" onClick={openRooms}/>}</div>
    </section>
    <Card className="room-device-panel" title={compact ? "Room Devices" : `${roomName(room)} devices`} action={<select aria-label="Select room" value={sorted.length ? room : ""} onChange={(event) => select(event.target.value)}>{sorted.length === 0 && <option value="">Rooms</option>}{sorted.map(({ id }) => <option key={id} value={id}>{roomName(id)}</option>)}</select>}>
      <div className="device-list"><Status {...devices} empty={!roomDevices.length ? "No devices available." : undefined}/>{[...roomDevices].sort((a, b) => ranked(a) - ranked(b)).map((device) => <DeviceCard key={device.id} device={device} compact={compact} schedule={() => schedule?.(device.id)}/>)}</div>
    </Card>
  </>;
}

export function ActivityPanel({ timezone, detailed = false, open }: { timezone: string; detailed?: boolean; open?: () => void }) {
  const [tab, choose] = useState<"Running" | "Scheduled" | "Completed">("Running");
  const activities = useResource<Activities>("/smart-home/activities", /^(device_|activity_|schedule_)/);
  const timers = useResource<Timer[]>("/timers?include_closed=true", /^timer_/);
  const closed = useResource<Job[]>("/schedules?include_closed=true", jobEvents);
  const action = useAction();
  const [now, setNow] = useState(Date.now());
  const countDown = tab === "Running" && timers.data?.some((timer) => timer.status === "active");
  useEffect(() => {
    if (!countDown) return;
    const clock = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(clock);
  }, [countDown]);
  const devices = tab === "Running" ? activities.data?.running : tab === "Completed" ? activities.data?.completed.filter((device) => device.state.activity !== "idle") : [];
  const shownTimers = timers.data?.filter((timer) => tab === "Running" ? ["active", "paused"].includes(timer.status) : tab === "Completed" && !["active", "paused"].includes(timer.status));
  const jobs = tab === "Scheduled" ? activities.data?.scheduled : tab === "Completed" ? closed.data?.filter((job) => job.job_type !== "reminder" && ["completed", "failed", "missed"].includes(job.status)) : [];
  return <Card title="Activities" className={detailed ? "all-activities" : "activity-panel"} action={open && <button className="text-button" onClick={open}>View all <Icon name="right" size={14}/></button>}>
    <Tabs options={["Running", "Scheduled", "Completed"] as const} selected={tab} choose={choose}/>
    <div className="activity-list">
      <Status error={activities.error ?? timers.error} loading={activities.loading && !activities.data} empty={!devices?.length && !shownTimers?.length && !jobs?.length ? "No activities here." : undefined}/>
      {devices?.map((device) => <Row key={device.id} name={deviceName(device)} icon={device.device_type} detail={`${human(String(device.state.activity))}${Number(device.state.remaining_seconds) > 0 ? ` · ${remaining(Number(device.state.remaining_seconds))}` : ""}`}>
        {tab === "Running" && supportedActivity(device.state.activity).filter((option) => device.capabilities.activity?.enum?.includes(option)).map((option) => <button key={option} disabled={action.pending || !device.available} onClick={() => void action.run(async () => { await commandDevice(device, "activity", option); activities.refresh(); })}>{human(option)}</button>)}
      </Row>)}
      {shownTimers?.map((timer) => <Row key={timer.id} name={timer.label || "Timer"} icon="clock" detail={timer.status === "active" && timer.fire_at_utc ? remaining((Date.parse(timer.fire_at_utc) - now) / 1000) : timer.status === "paused" ? remaining(timer.remaining_seconds ?? 0) : human(timer.status)}>
        {tab === "Running" && <><button disabled={action.pending} onClick={() => void action.run(async () => { await api(`/timers/${timer.id}/${timer.status === "paused" ? "resume" : "pause"}`, "POST"); timers.refresh(); })}>{timer.status === "paused" ? "Resume" : "Pause"}</button><IconButton icon="close" label="Cancel timer" disabled={action.pending} onClick={() => void action.run(async () => { await api(`/timers/${timer.id}`, "DELETE"); timers.refresh(); })}/></>}
      </Row>)}
      {jobs?.map((job) => <Row key={job.id} name={job.title} icon="calendar" detail={`${formatDate(job.next_run_at_utc ?? job.scheduled_at_utc, timezone)} · ${human(job.status)}`}/>)}
    </div>{action.feedback}
  </Card>;
}

export function Home({ data, timezone, open, briefingLanguage }: { data: HomeData; timezone: string; open: (section: string, tab?: string) => void; briefingLanguage: string }) {
  const events = useResource<Items<CalendarEvent>>("/calendar/events?range_kind=upcoming", /^calendar_/);
  const tasks = useResource<Items<Task>>("/tasks", /^task_/);
  const reminders = useResource<Job[]>("/reminders", jobEvents);
  const briefing = useResource<Briefing>(`/briefing?language=${briefingLanguage === "auto" ? "en" : briefingLanguage}`, /^(calendar_|task_|reminder_|grocery_|parcel_|notification_)/);
  const energy = useResource<Energy>("/smart-home/energy", homeEvents);
  const [showBriefing, setBriefing] = useState(false);
  const action = useAction();
  const upcoming = [...(events.data?.items ?? []).map((event) => ({ ...event, date: event.start_at_utc, icon: "calendar" })),
    ...(reminders.data ?? []).filter((job) => job.status === "scheduled" && job.enabled).map((job) => ({ ...job, date: job.next_run_at_utc ?? job.scheduled_at_utc, icon: "bell" }))]
    .filter((item) => Date.parse(item.date) >= Date.now()).sort((a, b) => Date.parse(a.date) - Date.parse(b.date)).slice(0, 4);
  const today = dateKey(new Date(), timezone);
  const todaysTasks = tasks.data?.items.filter((task) => [task.due_at_utc, task.completed_at_utc].some((date) => date && dateKey(date, timezone) === today)).slice(0, 4) ?? [];
  const total = energy.data?.total_power_w ?? 0;
  const groups = [
    { type: "air_conditioner", label: "AC", color: "#dce99b", watts: 0 },
    { type: "television", label: "TV", color: "#b8cd68", watts: 0 },
    { type: "light", label: "Lights", color: "#97a657", watts: 0 },
    { type: "", label: "Others", color: "#74764c", watts: 0 },
  ];
  const types = new Map((data.devices.data ?? []).map((device) => [device.id, device.device_type]));
  for (const entry of energy.data?.devices ?? []) {
    const group = groups.find((item) => item.type === types.get(entry.device_id)) ?? groups[3];
    group.watts += entry.power_w;
  }
  let start = 0;
  const segments = groups.filter((group) => group.watts > 0).map((group) => {
    const from = start;
    start += group.watts / total * 100;
    return `${group.color} ${from}% ${Math.min(100, start)}%`;
  });
  return <div className="home-grid">
    <div className="home-upper"><Rooms data={data} compact openRooms={() => open("Rooms")}/><ActivityPanel timezone={timezone} open={() => open("Automation", "Activities")}/></div>
    <div className="home-lower">
      <Card title="AURA Highlights" className="highlights">
        <Status {...briefing}/>
        {briefing.data && <>
          <Row icon="household" name={briefing.data.low_stock_items.length ? briefing.data.low_stock_items.slice(0, 3).map((item) => item.display_name).join(", ") : "No low-stock items"} detail={briefing.data.low_stock_items.length ? "Low in stock" : undefined}><IconButton icon="right" label="Open inventory" onClick={() => open("Household", "Inventory")}/></Row>
          <Row icon="bell" name={briefing.data.upcoming_reminders.length ? `${briefing.data.upcoming_reminders.length} upcoming reminders` : "No upcoming reminders"}><IconButton icon="right" label="Open reminders" onClick={() => open("Tasks", "Reminders")}/></Row>
          <Row icon="household" name={briefing.data.parcels_due.length ? `${briefing.data.parcels_due.length} expected deliveries` : "No saved deliveries due"}><IconButton icon="right" label="Open deliveries" onClick={() => open("Household", "Deliveries")}/></Row>
          <button className="text-button briefing-button" onClick={() => setBriefing(!showBriefing)}>{showBriefing ? "Hide daily briefing" : "Read daily briefing"}</button>
          {showBriefing && <p className="briefing-text">{briefing.data.summary_text}</p>}
        </>}
      </Card>
      <Card title="Upcoming Schedule" action={<button className="text-button" onClick={() => open("Tasks", "Calendar")}>View all <Icon name="right" size={14}/></button>}>
        <Status error={events.error ?? reminders.error} loading={(events.loading || reminders.loading) && !upcoming.length} empty={!upcoming.length ? "Nothing upcoming." : undefined}/>
        {upcoming.map((item) => <Row key={item.id} icon={item.icon} name={item.title} detail={formatDate(item.date, timezone)}/>)}
      </Card>
      <Card title="Today’s Tasks" action={<IconButton icon="plus" label="New task" onClick={() => open("Tasks", "New task")}/>}>
        <Status {...tasks} empty={!todaysTasks.length ? "No tasks due today." : undefined}/>
        {todaysTasks.map((task) => <Row key={task.id} name={<button className={`text-button ${task.status === "completed" ? "strike" : ""}`} onClick={() => open("Tasks", task.id)}>{task.title}</button>}>
          <input className="check" type="checkbox" aria-label={`Complete ${task.title}`} checked={task.status === "completed"} disabled={action.pending || task.status === "completed"} onChange={() => void action.run(async () => { await api(`/tasks/${task.id}/complete`, "POST"); tasks.refresh(); })}/>
        </Row>)}{action.feedback}
      </Card>
      <Card title="Estimated Energy" action={<span className="meta-pill">Now</span>}>
        <Status {...energy}/>
        {energy.data && <div className="energy"><div className="energy-ring" style={{ background: segments.length ? `conic-gradient(${segments.join(", ")}, #64634a 0)` : "#64634a" }}><div><Icon name="bolt"/><strong>{total} W</strong><small>Total</small></div></div><ul>{groups.map((group) => <li key={group.label}><i style={{ backgroundColor: group.color }}/><span>{group.label}</span><b>{group.watts} W</b></li>)}</ul></div>}
      </Card>
    </div>
  </div>;
}

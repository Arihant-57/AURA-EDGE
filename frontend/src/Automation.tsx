import { useState } from "react";
import { api, Device, Job, validateValue, Value } from "./api/desktop";
import { ActivityPanel, deviceName } from "./Home";
import { refreshResources, useResource } from "./live";
import { timing, TimingFields } from "./Planner";
import { Card, ConfirmButton, field, formatDate, formData, human, Row, Status, TextField, useAction } from "./ui";

interface Command { device_id: string; action: string; value: Value }
interface Routine {
  id: string; name: string; is_builtin: boolean; enabled: boolean; description?: string;
  actions: Command[]; triggers: Record<string, unknown>[]; conditions: Record<string, unknown>[];
}
interface Execution { id: string; status: string; scheduled_occurrence_utc: string; error_message?: string }
interface RoutineResult { status: string; results: { device_id?: string; action?: string; status?: string; success?: boolean; error?: string }[] }

const routinePath = "/smart-home/routines";
const jobEvents = /^(schedule_|reminder_)/;
const routineEvents = /^routine_/;
const writable = (devices: Device[]) => devices.filter((device) => Object.keys(device.capabilities).length > 0);
const initialCommand = (device?: Device): Command => {
  const action = Object.keys(device?.capabilities ?? {})[0] ?? "";
  const spec = device?.capabilities[action];
  return { device_id: device?.id ?? "", action, value: spec?.type === "bool" ? true : spec?.enum?.[0] ?? spec?.min ?? "" };
};
function CommandFields({ devices, command, change }: { devices: Device[]; command: Command; change: (command: Command) => void }) {
  const device = devices.find((item) => item.id === command.device_id);
  const spec = device?.capabilities[command.action];
  return <div className="command-fields">
    <label className="field">Device<select value={command.device_id} required onChange={(event) => change(initialCommand(devices.find((item) => item.id === event.target.value)))}><option value="" disabled>Choose device</option>{devices.map((item) => <option key={item.id} value={item.id}>{deviceName(item)}</option>)}</select></label>
    <label className="field">Action<select value={command.action} required onChange={(event) => {
      const next = device?.capabilities[event.target.value];
      change({ ...command, action: event.target.value, value: next?.type === "bool" ? true : next?.enum?.[0] ?? next?.min ?? "" });
    }}><option value="" disabled>Choose action</option>{Object.keys(device?.capabilities ?? {}).map((key) => <option key={key} value={key}>{human(key)}</option>)}</select></label>
    {spec?.type === "bool" ? <label className="field">Value<select aria-label="Action value" value={String(command.value)} onChange={(event) => change({ ...command, value: event.target.value === "true" })}><option value="true">{command.action === "power" ? "On" : "Yes"}</option><option value="false">{command.action === "power" ? "Off" : "No"}</option></select></label> :
      spec?.enum ? <label className="field">Value<select aria-label="Action value" value={String(command.value)} onChange={(event) => change({ ...command, value: event.target.value })}>{spec.enum.map((value) => <option key={value} value={value}>{human(value)}</option>)}</select></label> :
      spec?.min !== undefined && <label className="field">Value<input aria-label="Action value" type="number" min={spec.min} max={spec.max} required value={String(command.value)} onChange={(event) => change({ ...command, value: event.target.value === "" ? "" : event.target.valueAsNumber })}/></label>}
  </div>;
}
function checkCommand(devices: Device[], command: Command) {
  const spec = devices.find((device) => device.id === command.device_id)?.capabilities[command.action];
  if (!spec || !validateValue(spec, command.value)) throw new Error("Choose a device, supported action and valid value.");
}

export function Automation({ devices, timezone, requested }: { devices: Device[]; timezone: string; requested: string }) {
  if (requested === "Activities") return <ActivityPanel timezone={timezone} detailed/>;
  return <Schedules devices={writable(devices)} timezone={timezone} requested={requested}/>;
}
function Schedules({ devices, timezone, requested }: { devices: Device[]; timezone: string; requested: string }) {
  const schedules = useResource<Job[]>("/schedules?include_closed=true", jobEvents);
  const routines = useResource<Routine[]>(routinePath, routineEvents);
  const [editing, select] = useState<Job>();
  const [historyId, setHistory] = useState("");
  const [kind, setKind] = useState(requested.startsWith("routine:") ? "routine" : "device_action");
  const [routine, setRoutine] = useState(requested.replace(/^routine:/, ""));
  const [command, setCommand] = useState(() => initialCommand(devices.find((item) => item.id === requested)));
  const executions = useResource<Execution[]>(historyId ? `/schedules/${historyId}/executions` : "", jobEvents);
  const action = useAction();
  const list = schedules.data?.filter((job) => job.job_type !== "reminder") ?? [];
  function mutate(job: Job, kind: "pause" | "resume" | "delete") {
    void action.run(async () => {
      await api(`/schedules/${job.id}${kind === "delete" ? "" : `/${kind}`}`, kind === "delete" ? "DELETE" : "POST");
      if (editing?.id === job.id && kind === "delete") select(undefined);
      schedules.refresh();
    });
  }
  return <div className="detail-grid">
    <div className="stack">
      <Card title="Scheduled actions">
        <Status {...schedules} empty={!list.length ? "No scheduled actions." : undefined}/>
        {list.map((job) => <Row key={job.id} icon="automation" name={job.title} detail={`${formatDate(job.next_run_at_utc ?? job.scheduled_at_utc, timezone)} · ${human(job.status)}${job.last_error ? ` · ${job.last_error}` : ""}`}>
          {["scheduled", "paused"].includes(job.status) && <><button onClick={() => select(job)}>Edit</button>
            <button disabled={action.pending} onClick={() => mutate(job, job.status === "paused" ? "resume" : "pause")}>{job.status === "paused" ? "Resume" : "Pause"}</button>
            <ConfirmButton label="Cancel" question={`Cancel “${job.title}”?`} action={() => mutate(job, "delete")}/></>}
          <button onClick={() => setHistory(job.id === historyId ? "" : job.id)}>History</button>
        </Row>)}
      </Card>
      {historyId && <Card title="Execution history"><Status {...executions} empty={!executions.data?.length ? "No executions yet." : undefined}/>{executions.data?.map((item) => <Row key={item.id} name={human(item.status)} detail={[formatDate(item.scheduled_occurrence_utc, timezone), item.error_message].filter(Boolean).join(" · ")}/>)}</Card>}
    </div>
    <Card title={editing ? "Edit schedule" : "New schedule"} action={editing && <button onClick={() => select(undefined)}>New schedule</button>}>
      <form key={editing?.id ?? "new"} className="form" onSubmit={(event) => {
        const data = formData(event);
        const form = event.currentTarget;
        void action.run(async () => {
          const title = field(data, "title");
          const common = { ...timing(data, timezone), ...(title ? { title } : {}) };
          if (editing) await api(`/schedules/${editing.id}`, "PATCH", common);
          else if (kind === "routine") await api("/schedules", "POST", { ...common, job_type: kind, routine_id: routine });
          else {
            checkCommand(devices, command);
            await api("/schedules", "POST", { ...common, job_type: kind, ...command });
          }
          if (!editing) form.reset();
          schedules.refresh();
        }, "Schedule saved.");
      }}>
        {!editing && <><label className="field">Action type<select value={kind} onChange={(event) => setKind(event.target.value)}><option value="device_action">Device action</option><option value="routine">Routine</option></select></label>
          {kind === "routine" ? <label className="field">Routine<select required value={routine} onChange={(event) => setRoutine(event.target.value)}><option value="">Choose routine</option>{routines.data?.filter((item) => item.enabled).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> :
            <CommandFields devices={devices} command={command} change={setCommand}/>}</>}
        <TextField name="title" label="Title" value={editing?.title}/>
        <TimingFields timezone={timezone} job={editing}/>
        <button className="primary" disabled={action.pending}>Save schedule</button>
      </form>{action.feedback}
    </Card>
  </div>;
}

export function Routines({ devices: allDevices, schedule }: { devices: Device[]; schedule: (id: string) => void }) {
  const devices = writable(allDevices);
  const routines = useResource<Routine[]>(routinePath, routineEvents);
  const [selected, select] = useState<Routine>();
  const [creating, create] = useState(false);
  const [lastRun, setLastRun] = useState<RoutineResult>();
  const action = useAction();
  return <div className="detail-grid">
    <div className="stack">
      <Card title="Your routines" action={<button className="primary" onClick={() => { select(undefined); create(true); }}>New routine</button>}>
        <Status {...routines} empty={!routines.data?.length ? "No routines available." : undefined}/>
        {routines.data?.map((item) => <Row key={item.id} icon="routines" name={<button className="text-button" onClick={() => { select(item); create(false); }}>{item.name}</button>} detail={`${item.is_builtin ? "Built-in" : "Custom"} · ${item.actions.length} actions · ${item.enabled ? "Enabled" : "Disabled"}`}>
          <button onClick={() => schedule(item.id)} disabled={!item.enabled}>Schedule</button>
          <button disabled={!item.enabled || action.pending} onClick={() => void action.run(async () => {
            setLastRun(undefined);
            const result = await api<RoutineResult>(`${routinePath}/${item.id}/run?check_conditions=true`, "POST");
            setLastRun(result);
            refreshResources("/smart-home");
          })}>Run</button>
        </Row>)}
        {action.feedback}
      </Card>
      {lastRun && <Card title={`Last run · ${human(lastRun.status)}`}>{lastRun.results.map((step, i) => <Row key={i} name={step.device_id ?? step.action ?? `Step ${i + 1}`} detail={step.error ?? step.status ?? (step.success ? "Verified" : "See routine status")}/>)}</Card>}
    </div>
    {(selected || creating) && <RoutineEditor key={selected?.id ?? "new"} selected={selected} devices={devices} cancel={() => { select(undefined); create(false); }} refresh={routines.refresh}/>}
  </div>;
}

function RoutineEditor({ selected, devices, cancel, refresh }: { selected?: Routine; devices: Device[]; cancel: () => void; refresh: () => void }) {
  const [commands, setCommands] = useState<Command[]>(selected?.actions ?? [initialCommand()]);
  const action = useAction();
  const conditionText = JSON.stringify(selected?.conditions ?? [], null, 2);
  const triggerText = JSON.stringify(selected?.triggers ?? [{ type: "manual" }], null, 2);
  return <Card title={selected ? `Edit ${selected.name}` : "New routine"} action={<button onClick={cancel}>Close</button>}>
    <form className="form" onSubmit={(event) => {
      const data = formData(event);
      void action.run(async () => {
        commands.forEach((command) => checkCommand(devices, command));
        const triggers: unknown = JSON.parse(field(data, "triggers"));
        const conditions: unknown = JSON.parse(field(data, "conditions"));
        if (!Array.isArray(triggers) || !Array.isArray(conditions)) throw new Error("Triggers and conditions must be lists.");
        await api(selected ? `${routinePath}/${selected.id}` : routinePath, selected ? "PATCH" : "POST", {
          name: field(data, "name"), description: field(data, "description"), enabled: data.has("enabled"), actions: commands, triggers, conditions,
        });
        refresh();
        if (!selected) cancel();
      }, "Routine saved.");
    }}>
      <TextField name="name" label="Name" required value={selected?.name}/>
      <TextField name="description" label="Description" value={selected?.description ?? ""}/>
      <label className="checkbox-line"><input name="enabled" type="checkbox" defaultChecked={selected?.enabled ?? true}/> Enabled</label>
      {commands.map((command, index) => <fieldset key={index}>
        <legend>Action {index + 1}</legend>
        <CommandFields devices={devices} command={command} change={(value) => setCommands(commands.map((item, i) => i === index ? value : item))}/>
        {commands.length > 1 && <button type="button" onClick={() => setCommands(commands.filter((_, i) => i !== index))}>Remove action</button>}
      </fieldset>)}
      <button type="button" disabled={commands.length >= 15} onClick={() => setCommands([...commands, initialCommand()])}>Add action</button>
      <details><summary>Triggers and conditions</summary>
        <label className="field">Triggers (JSON)<textarea name="triggers" rows={4} defaultValue={triggerText}/></label>
        <label className="field">Conditions (JSON)<textarea name="conditions" rows={4} defaultValue={conditionText}/></label>
      </details>
      <button className="primary" disabled={action.pending}>Save routine</button>
    </form>{action.feedback}
    {selected && !selected.is_builtin && <ConfirmButton label="Delete routine" question={`Delete “${selected.name}”?`} action={() => void action.run(async () => { await api(`${routinePath}/${selected.id}`, "DELETE"); refresh(); cancel(); })}/>}
  </Card>;
}

import { useState } from "react";
import { api, CalendarEvent, Items, Job, Task } from "./api/desktop";
import { refreshResources, useResource } from "./live";
import { Card, ConfirmButton, dateInput, field, formatDate, formData, human, localDate, recurrence, Row, SelectField, Status, TextField, useAction } from "./ui";

const jobs = /^(reminder_|schedule_)/;
export const plannerTabs = ["Tasks", "Calendar", "Reminders"] as const;

export function Planner({ requested, timezone }: { requested: string; timezone: string }) {
  const tab = plannerTabs.find((name) => name === requested) ?? "Tasks";
  const requestedTask = requested && !plannerTabs.some((name) => name === requested) && requested !== "New task" ? requested : "";
  return tab === "Tasks" ? <Tasks requestedTask={requestedTask} timezone={timezone}/> :
    tab === "Calendar" ? <Calendar timezone={timezone}/> : <Reminders timezone={timezone}/>;
}

function Tasks({ requestedTask, timezone }: { requestedTask: string; timezone: string }) {
  const tasks = useResource<Items<Task>>("/tasks", /^task_/);
  const [filter, setFilter] = useState("all");
  const [selected, select] = useState(requestedTask);
  const task = tasks.data?.items.find((item) => item.id === selected);
  const action = useAction();
  function removeOrComplete(id: string, complete: boolean) {
    void action.run(async () => {
      await api(`/tasks/${id}${complete ? "/complete" : ""}`, complete ? "POST" : "DELETE");
      if (selected === id && !complete) select("");
      tasks.refresh();
    });
  }
  return <div className="detail-grid">
    <Card title="My tasks" action={<select aria-label="Task status filter" value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">All statuses</option><option value="pending">Pending</option><option value="completed">Completed</option></select>}>
      <Status {...tasks} empty={!tasks.data?.items.some((item) => filter === "all" || item.status === filter) ? "No tasks in this view." : undefined}/>
      {tasks.data?.items.filter((item) => filter === "all" || item.status === filter).map((item) => <Row key={item.id}
        icon="tasks" name={item.title} detail={[human(item.status), human(item.priority), item.category, item.due_at_utc && formatDate(item.due_at_utc, timezone)].filter(Boolean).join(" · ")}>
        {item.status === "pending" && <button disabled={action.pending} onClick={() => removeOrComplete(item.id, true)}>Complete</button>}
        <button onClick={() => select(item.id)}>Edit</button>
        <ConfirmButton label="Delete" question={`Delete “${item.title}”?`} disabled={action.pending} action={() => removeOrComplete(item.id, false)}/>
      </Row>)}
    </Card>
    <Card title={task ? "Edit task" : "New task"} action={task && <button onClick={() => select("")}>New task</button>}>
      <form key={task?.id ?? "new"} className="form" onSubmit={(event) => {
        const form = event.currentTarget;
        const data = formData(event);
        void action.run(async () => {
          const due = field(data, "due");
          const payload = { title: field(data, "title"), description: field(data, "description"), priority: field(data, "priority"), category: field(data, "category"), ...(due ? { due_at_utc: dateInput(due, timezone) } : {}) };
          await api(task ? `/tasks/${task.id}` : "/tasks", task ? "PATCH" : "POST", task ? payload : { ...payload, create_reminder: data.has("reminder") });
          if (!task) form.reset();
          tasks.refresh();
        }, "Task saved.");
      }}>
        <TextField name="title" label="Title" required value={task?.title}/>
        <TextField name="description" label="Description" value={task?.description ?? ""}/>
        <TextField name="due" label={`Due · ${timezone}`} type="datetime-local" value={localDate(task?.due_at_utc, timezone)} required={!!task?.due_at_utc}/>
        <SelectField name="priority" label="Priority" options={["low", "normal", "high", "urgent"]} value={task?.priority ?? "normal"}/>
        <TextField name="category" label="Category" value={task?.category ?? "general"} required/>
        {!task && <label className="checkbox-line"><input name="reminder" type="checkbox" defaultChecked/> Remind me at the due time</label>}
        <button className="primary" disabled={action.pending}>Save task</button>
      </form>{action.feedback}
    </Card>
  </div>;
}

function Calendar({ timezone }: { timezone: string }) {
  const [range, setRange] = useState("upcoming");
  const events = useResource<Items<CalendarEvent>>(`/calendar/events?range_kind=${range}`, /^calendar_/);
  const [editing, select] = useState<CalendarEvent>();
  const [conflicts, setConflicts] = useState<CalendarEvent[]>([]);
  const action = useAction();
  return <div className="detail-grid">
    <Card title="Calendar" action={<select aria-label="Calendar range" value={range} onChange={(e) => setRange(e.target.value)}><option value="today">Today</option><option value="tomorrow">Tomorrow</option><option value="upcoming">Upcoming</option></select>}>
      <Status {...events} empty={!events.data?.items.length ? "No events in this range." : undefined}/>
      {events.data?.items.map((item) => <Row key={`${item.id}-${item.start_at_utc}`} icon="calendar" name={item.title} detail={[formatDate(item.start_at_utc, timezone), item.location].filter(Boolean).join(" · ")}>
        <button onClick={() => { select(item); setConflicts([]); }}>Edit</button>
        <ConfirmButton label="Delete" question={`Delete “${item.title}” and its linked reminder?`} disabled={action.pending} action={() => void action.run(async () => {
          await api(`/calendar/events/${item.id}`, "DELETE");
          if (editing?.id === item.id) select(undefined);
          refreshResources("/calendar/events", "/reminders");
        })}/>
      </Row>)}
    </Card>
    <Card title={editing ? "Edit event" : "New event"} action={editing && <button onClick={() => select(undefined)}>New event</button>}>
      <form key={editing ? `${editing.id}-${editing.start_at_utc}` : "new"} className="form" onSubmit={(event) => {
        const form = event.currentTarget;
        const data = formData(event);
        setConflicts([]);
        void action.run(async () => {
          const start = field(data, "start");
          const end = field(data, "end");
          const payload = { title: field(data, "title"), description: field(data, "description"), location: field(data, "location"), start_at_utc: dateInput(start, timezone), ...(end ? { end_at_utc: dateInput(end, timezone) } : {}) };
          const minutes = field(data, "reminder");
          const result = await api<{ conflicts: CalendarEvent[] }>(editing ? `/calendar/events/${editing.id}` : "/calendar/events", editing ? "PATCH" : "POST", editing ? payload : {
            ...payload, timezone_name: timezone, recurrence: recurrence(field(data, "recurrence"), start), reminder_minutes_before: minutes ? Number(minutes) : null, all_day: data.has("all_day"),
          });
          setConflicts(result.conflicts);
          if (!editing) form.reset();
          refreshResources("/calendar/events", "/reminders");
        }, "Event saved.");
      }}>
        <TextField name="title" label="Title" required value={editing?.title}/>
        <TextField name="start" label={`Starts · ${timezone}`} type="datetime-local" required value={localDate(editing?.start_at_utc, timezone)}/>
        <TextField name="end" label="Ends" type="datetime-local" value={localDate(editing?.end_at_utc, timezone)}/>
        <TextField name="location" label="Location" value={editing?.location ?? ""}/>
        <TextField name="description" label="Description" value={editing?.description ?? ""}/>
        {!editing && <><SelectField name="recurrence" label="Repeat" options={["once", "daily", "weekly"]}/><TextField name="reminder" label="Remind me (minutes before)" type="number" min={0} max={10080}/><label className="checkbox-line"><input name="all_day" type="checkbox"/> All-day event</label></>}
        <button className="primary" disabled={action.pending}>Save event</button>
      </form>{action.feedback}
      {conflicts.length > 0 && <p className="inline-error" role="status">Overlaps with {conflicts.map((event) => event.title).join(", ")}.</p>}
    </Card>
  </div>;
}

export function TimingFields({ timezone, job }: { timezone: string; job?: Job }) {
  return <>
    <TextField name="when" label={`When · ${timezone}`} type="datetime-local" required value={localDate(job?.next_run_at_utc ?? job?.scheduled_at_utc, timezone)}/>
    <SelectField name="recurrence" label="Repeat" options={["once", "daily", "weekly"]} value={job?.recurrence.kind}/>
    <SelectField name="missed_policy" label="If missed" options={["notify_missed", "skip"]} value={job?.missed_policy}/>
  </>;
}
export function timing(data: FormData, timezone: string) {
  const when = field(data, "when");
  return { run_at_utc: dateInput(when, timezone), timezone, recurrence: recurrence(field(data, "recurrence"), when), missed_policy: field(data, "missed_policy") };
}
const languages = ["en", "hi", "kn"];

function Reminders({ timezone }: { timezone: string }) {
  const reminders = useResource<Job[]>("/reminders?include_closed=true", jobs);
  const [closed, setClosed] = useState(false);
  const [editing, select] = useState<Job>();
  const action = useAction();
  const visible = reminders.data?.filter((item) => closed !== ["scheduled", "paused", "running"].includes(item.status)) ?? [];
  return <div className="detail-grid">
    <Card title="Reminders" action={<button onClick={() => setClosed(!closed)}>{closed ? "Show upcoming" : "Show history"}</button>}>
      <Status {...reminders} empty={!visible.length ? "No reminders in this view." : undefined}/>
      {visible.map((item) => <Row key={item.id} icon="bell" name={item.title} detail={`${formatDate(item.next_run_at_utc ?? item.scheduled_at_utc, timezone)} · ${human(item.status)}`}>
        {!closed && <><button onClick={() => select(item)}>Edit</button><ConfirmButton label="Cancel" question={`Cancel reminder “${item.title}”?`} disabled={action.pending} action={() => void action.run(async () => {
          await api(`/reminders/${item.id}`, "DELETE");
          if (editing?.id === item.id) select(undefined);
          reminders.refresh();
        })}/></>}
      </Row>)}
    </Card>
    <Card title={editing ? "Edit reminder" : "New reminder"} action={editing && <button onClick={() => select(undefined)}>New reminder</button>}>
      <form key={editing?.id ?? "new"} className="form" onSubmit={(event) => {
        const form = event.currentTarget;
        const data = formData(event);
        void action.run(async () => {
          const message = field(data, "message");
          const payload = { ...timing(data, timezone), message, title: message.slice(0, 200) };
          await api(editing ? `/reminders/${editing.id}` : "/reminders", editing ? "PATCH" : "POST",
            editing ? payload : { ...payload, language: field(data, "language") });
          if (!editing) form.reset();
          reminders.refresh();
        }, "Reminder saved.");
      }}>
        <TextField name="message" label="Message" required value={editing?.payload.message ?? editing?.title}/>
        <TimingFields timezone={timezone} job={editing}/>
        {!editing && <SelectField name="language" label="Language" options={languages}/>}
        <button className="primary" disabled={action.pending}>Save reminder</button>
      </form>{action.feedback}
    </Card>
  </div>;
}

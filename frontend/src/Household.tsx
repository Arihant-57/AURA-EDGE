import { useState } from "react";
import { api, Items } from "./api/desktop";
import { refreshResources, useResource } from "./live";
import { Card, ConfirmButton, dateInput, field, formatDate, formData, localDate, Row, Status, TextField, useAction } from "./ui";

export const householdTabs = ["Groceries", "Inventory", "Recipes", "Expenses", "Deliveries"] as const;
const groceryEvents = /^grocery_/;
interface Shopping { id: string; item_name: string; purchased: boolean; quantity: number; unit: string }
interface Stock { id: string; display_name: string; quantity: number; unit: string; min_threshold: number; category: string; is_low_stock: boolean; expires_at_utc?: string }
interface Recipe { id: string; title: string; prep_minutes: number; steps: string[]; available_ingredients: string[]; missing_ingredients: string[] }
interface Expense { id: string; amount_inr: string; description?: string; merchant?: string; category: string; occurred_at_utc: string }
interface Summary { total_inr: string; by_category: { category: string; total_inr: string }[] }
interface Parcel { id: string; order_name: string; carrier?: string; tracking_ref?: string; expected_delivery_date?: string; last_known_status: string; is_sample: boolean; last_updated_at_utc: string; notes?: string; source: string }

export function Household({ tab, timezone }: { tab: string; timezone: string }) {
  if (tab === "Inventory") return <Inventory timezone={timezone}/>;
  if (tab === "Recipes") return <Recipes/>;
  if (tab === "Expenses") return <Expenses timezone={timezone}/>;
  if (tab === "Deliveries") return <Deliveries timezone={timezone}/>;
  return <Groceries/>;
}
function Groceries() {
  const list = useResource<Items<Shopping>>("/groceries/list", groceryEvents);
  const stock = useResource<Items<Stock>>("/groceries/inventory", groceryEvents);
  const items = list.data?.items ?? [];
  const low = stock.data?.items.filter((item) => item.is_low_stock) ?? [];
  const action = useAction();
  return <div className="detail-grid">
    <Card title="Shopping list">
      <Status {...list} empty={!items.length ? "The shopping list is empty." : undefined}/>
      {items.map((item) => <Row key={item.id} icon="household" name={item.item_name} detail={`${item.quantity} ${item.unit}`}>
        <input className="check" type="checkbox" aria-label={`Purchased ${item.item_name}`} checked={item.purchased} disabled={action.pending} onChange={(e) => {
          const purchased = e.currentTarget.checked;
          void action.run(async () => { await api(`/groceries/list/${item.id}`, "PATCH", { purchased }); list.refresh(); });
        }}/>
        <ConfirmButton label="Remove" question={`Remove “${item.item_name}”?`} disabled={action.pending} action={() => void action.run(async () => { await api(`/groceries/list/${item.id}`, "DELETE"); list.refresh(); })}/>
      </Row>)}
      <form className="form compact-form" onSubmit={(event) => {
        const form = event.currentTarget;
        const data = formData(event);
        void action.run(async () => {
          await api("/groceries/list", "POST", { item_name: field(data, "name"), quantity: field(data, "quantity"), unit: field(data, "unit") });
          form.reset();
          list.refresh();
        }, "Item added.");
      }}><TextField name="name" label="Item" required/><TextField name="quantity" label="Quantity" type="number" min={0.001} step="any" value={1} required/><TextField name="unit" label="Unit" value="unit" required/><button className="primary" disabled={action.pending}>Add item</button></form>
      {action.feedback}
    </Card>
    <Card title="Low stock"><Status {...stock} empty={!low.length ? "Nothing is low in stock." : undefined}/>
      {low.map((item) => <Row key={item.id} icon="household" name={item.display_name} detail={`${item.quantity} ${item.unit} left`}/>)}
    </Card>
  </div>;
}
function Inventory({ timezone }: { timezone: string }) {
  const list = useResource<Items<Stock>>("/groceries/inventory", groceryEvents);
  const [editing, select] = useState<Stock>();
  const action = useAction();
  return <div className="detail-grid">
    <Card title="Pantry inventory">
      <Status {...list} empty={!list.data?.items.length ? "No inventory records." : undefined}/>
      {list.data?.items.map((item) => <Row key={item.id} icon="household" name={item.display_name} detail={[`${item.quantity} ${item.unit}`, item.is_low_stock ? "Low stock" : "In stock", item.expires_at_utc && `Expires ${formatDate(item.expires_at_utc, timezone)}`].filter(Boolean).join(" · ")}>
        <button onClick={() => select(item)}>Edit</button><ConfirmButton label="Remove" question={`Remove “${item.display_name}” from inventory?`} disabled={action.pending} action={() => void action.run(async () => {
          await api(`/groceries/inventory/${item.id}`, "DELETE");
          if (editing?.id === item.id) select(undefined);
          list.refresh();
        })}/>
      </Row>)}
    </Card>
    <Card title={editing ? "Update stock" : "Add inventory item"} action={editing && <button onClick={() => select(undefined)}>Add item</button>}>
      <form key={editing?.id ?? "new"} className="form" onSubmit={(event) => {
        const form = event.currentTarget;
        const data = formData(event);
        void action.run(async () => {
          const expires = field(data, "expires");
          await api("/groceries/inventory", "POST", {
            name: field(data, "name"), quantity: field(data, "quantity"), unit: field(data, "unit"), category: field(data, "category"), min_threshold: field(data, "threshold"),
            expires_at_utc: expires ? dateInput(expires, timezone) : null,
          });
          if (!editing) form.reset();
          list.refresh();
        }, "Inventory saved.");
      }}>
        <TextField name="name" label="Name" required value={editing?.display_name}/>
        <TextField name="quantity" label="Quantity" type="number" min={0} step="any" value={editing?.quantity ?? 1} required/>
        <TextField name="unit" label="Unit" value={editing?.unit ?? "unit"} required/>
        <TextField name="category" label="Category" value={editing?.category ?? "pantry"} required/>
        <TextField name="threshold" label="Low-stock threshold" type="number" min={0} step="any" value={editing?.min_threshold ?? 1} required/>
        <TextField name="expires" label={`Expires · ${timezone}`} type="datetime-local" value={localDate(editing?.expires_at_utc, timezone)}/>
        <button className="primary" disabled={action.pending}>Save inventory</button>
      </form>{action.feedback}
    </Card>
  </div>;
}
function Recipes() {
  const [query, setQuery] = useState("");
  const recipes = useResource<{ recipes: Recipe[] }>(`/recipes/suggest?q=${encodeURIComponent(query)}`, groceryEvents);
  return <Card title="Offline recipes" action={<form className="inline-form" onSubmit={(event) => setQuery(field(formData(event), "q"))}><input name="q" aria-label="Find a recipe" placeholder="Find a recipe"/><button>Search</button></form>}>
    <Status {...recipes} empty={!recipes.data?.recipes.length ? "No matching recipes." : undefined}/>
    {recipes.data?.recipes.map((recipe) => <details className="recipe" key={recipe.id}><summary>{recipe.title}<span>{recipe.prep_minutes} min</span></summary>
      <p>Available: {recipe.available_ingredients.join(", ") || "None recorded"}</p>
      <p>Missing: {recipe.missing_ingredients.join(", ") || "None"}</p>
      <ol>{recipe.steps.map((step, i) => <li key={i}>{step}</li>)}</ol>
    </details>)}
  </Card>;
}
function Expenses({ timezone }: { timezone: string }) {
  const [period, setPeriod] = useState("month");
  const entries = useResource<Items<Expense>>("/expenses", /^expense_/);
  const summary = useResource<Summary>(`/expenses/summary?period=${period}`, /^expense_/);
  const action = useAction();
  const [importReport, report] = useState("");
  return <div className="detail-grid">
    <div className="stack">
      <Card title="Expense summary" action={<select aria-label="Expense period" value={period} onChange={(e) => setPeriod(e.target.value)}><option value="day">Today</option><option value="month">This month</option><option value="all">All time</option></select>}>
        <Status {...summary}/>{summary.data && <><p className="expense-total">₹{summary.data.total_inr}</p>{summary.data.by_category.map((item) => <Row key={item.category} name={item.category}><strong>₹{item.total_inr}</strong></Row>)}</>}
      </Card>
      <Card title="Transactions">
        <Status {...entries} empty={!entries.data?.items.length ? "No expenses recorded." : undefined}/>
        {entries.data?.items.map((item) => <Row key={item.id} icon="household" name={item.description || item.merchant || item.category} detail={`${item.category} · ${formatDate(item.occurred_at_utc, timezone)}`}><strong>₹{item.amount_inr}</strong><ConfirmButton label="Delete" question="Delete this expense?" disabled={action.pending} action={() => void action.run(async () => { await api(`/expenses/${item.id}`, "DELETE"); refreshResources("/expenses"); })}/></Row>)}
      </Card>
    </div>
    <div className="stack">
      <Card title="Record an expense">
        <form className="form" onSubmit={(event) => {
          const form = event.currentTarget;
          const data = formData(event);
          void action.run(async () => {
            const date = field(data, "when");
            await api("/expenses", "POST", { amount: field(data, "amount"), category: field(data, "category"), merchant: field(data, "merchant"), description: field(data, "description"), occurred_at_utc: date ? dateInput(date, timezone) : null });
            form.reset();
            refreshResources("/expenses");
          }, "Expense saved.");
        }}><TextField name="amount" label="Amount (INR)" type="number" min={0.01} step="0.01" required/><TextField name="category" label="Category" required/><TextField name="merchant" label="Merchant"/><TextField name="description" label="Description"/><TextField name="when" label={`Date · ${timezone}`} type="datetime-local"/><button className="primary" disabled={action.pending}>Save expense</button></form>
      </Card>
      <Card title="Import CSV">
        <form className="form" onSubmit={(event) => {
          const data = formData(event);
          const file = data.get("csv");
          if (!(file instanceof File) || !file.size) return;
          report("");
          void action.run(async () => {
            if (file.size > 1_000_000) throw new Error("Choose a CSV under 1 MB.");
            const result = await api<Record<string, unknown>>("/expenses/import-csv", "POST", { filename: file.name, csv_content: await file.text() });
            report(Object.entries(result).filter(([, value]) => ["number", "boolean"].includes(typeof value)).map(([key, value]) => `${key.replaceAll("_", " ")}: ${value}`).join(" · "));
            refreshResources("/expenses");
          }, "Import processed.");
        }}><label className="field">CSV file<input name="csv" type="file" accept=".csv,text/csv" required/></label><button disabled={action.pending}>Import</button></form>
        {importReport && <p role="status">{importReport}</p>}
      </Card>{action.feedback}
    </div>
  </div>;
}
function Deliveries({ timezone }: { timezone: string }) {
  const list = useResource<Items<Parcel>>("/parcels", /^parcel_/);
  const [editing, select] = useState<Parcel>();
  const action = useAction();
  return <div className="detail-grid">
    <Card title="Saved parcels">
      <p className="muted">Statuses come from local records. Live carrier lookup is unavailable offline.</p>
      <Status {...list} empty={!list.data?.items.length ? "No saved deliveries." : undefined}/>
      {list.data?.items.map((item) => <Row key={item.id} icon="household" name={item.order_name} detail={[item.last_known_status, item.is_sample ? "Sample" : item.source, item.expected_delivery_date && `Expected ${item.expected_delivery_date}`, `Updated ${formatDate(item.last_updated_at_utc, timezone)}`].filter(Boolean).join(" · ")}>
        <button onClick={() => select(item)}>Edit</button><ConfirmButton label="Remove" question={`Remove “${item.order_name}”?`} disabled={action.pending} action={() => void action.run(async () => {
          await api(`/parcels/${item.id}`, "DELETE");
          if (editing?.id === item.id) select(undefined);
          list.refresh();
        })}/>
      </Row>)}
    </Card>
    <Card title={editing ? `Edit ${editing.order_name}` : "Add parcel"} action={editing && <button onClick={() => select(undefined)}>Add parcel</button>}>
      <form className="form" key={editing?.id ?? "new"} onSubmit={(event) => {
        const form = event.currentTarget;
        const data = formData(event);
        void action.run(async () => {
          const common = { last_known_status: field(data, "status"), expected_delivery_date: field(data, "date") || null, notes: field(data, "notes") };
          await api(editing ? `/parcels/${editing.id}` : "/parcels", editing ? "PATCH" : "POST", editing ? common : { ...common, order_name: field(data, "name"), carrier: field(data, "carrier") || null, tracking_ref: field(data, "tracking") || null, create_delivery_reminder: data.has("reminder") });
          if (!editing) form.reset();
          refreshResources("/parcels", "/reminders");
        }, "Parcel saved.");
      }}>
        {!editing && <><TextField name="name" label="Parcel name" required/><TextField name="carrier" label="Carrier"/><TextField name="tracking" label="Tracking reference"/></>}
        <TextField name="status" label="Last known status" required value={editing?.last_known_status ?? "saved"}/>
        <TextField name="date" label="Expected delivery" type="date" value={editing?.expected_delivery_date ?? ""}/>
        <TextField name="notes" label="Notes" value={editing?.notes ?? ""}/>
        {!editing && <label className="checkbox-line"><input type="checkbox" name="reminder" defaultChecked/> Create delivery reminder</label>}
        <button className="primary" disabled={action.pending}>Save parcel</button>
      </form>{action.feedback}
    </Card>
  </div>;
}

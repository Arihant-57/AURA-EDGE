import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { commandDevice, Device, validateValue } from "./api/desktop";
import { dateInput, recurrence } from "./ui";

const light: Device = {
  id: "light.bedroom_main", name: "Bedroom Light", room: "bedroom", device_type: "light", available: true,
  capabilities: { power: { type: "bool" }, brightness: { min: 0, max: 100 } }, state: { power: false, brightness: 60 },
  estimated_power_w: 0, updated_at_utc: "2026-11-26T00:00:00Z",
};

function response(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status });
}

afterEach(() => { vi.restoreAllMocks(); });

describe("Phase 5 dashboard", () => {
  it("selects the same room across Home and Rooms and sends a boolean device command", async () => {
    let device = light;
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (path, options) => {
      const url = String(path);
      if (url.endsWith("/smart-home/rooms")) return response([{ id: "living_room", device_count: 0 }, { id: "bedroom", device_count: 1 }]);
      if (url.endsWith("/smart-home/devices")) return response([device]);
      if (url.endsWith("/command")) {
        expect(options?.method).toBe("POST");
        const body = JSON.parse(String(options?.body));
        expect(body).toEqual({ action: "power", value: true, source: "ui" });
        device = { ...device, state: { ...device.state, power: body.value } };
        return response({ status: "verified", device });
      }
      if (url.endsWith("/smart-home/activities")) return response({ running: [], scheduled: [], completed: [] });
      if (url.endsWith("/smart-home/energy")) return response({ total_power_w: 0, by_room_power_w: {}, devices: [] });
      if (url.includes("/briefing?")) return response({ summary_text: "", low_stock_items: [], parcels_due: [], upcoming_reminders: [] });
      if (url.includes("/timers") || url.includes("/reminders") || url.includes("/schedules")) return response([]);
      if (url.includes("/tasks") || url.includes("/calendar") || url.endsWith("/notifications")) return response({ items: [], unread_count: 0 });
      return response({});
    });

    render(<App/>);
    fireEvent.change(await screen.findByLabelText("Select room"), { target: { value: "bedroom" } });
    await screen.findByRole("button", { name: "Bedroom Light" });
    fireEvent.click(screen.getByRole("button", { name: "Open room details" }));
    expect(await screen.findByRole("heading", { name: "Bedroom devices" })).toBeInTheDocument();
    const power = await screen.findByRole("switch", { name: "Bedroom Light Power" });
    expect(power).toHaveAttribute("aria-checked", "false");
    fireEvent.click(power);
    await waitFor(() => expect(power).toHaveAttribute("aria-checked", "true"));
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/light.bedroom_main/command"), expect.any(Object));
  });

  it("commits capability values only after verified API success and displays rejection detail", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response({ detail: "Device is unavailable." }, 409));
    await expect(commandDevice(light, "power", "off")).rejects.toThrow("does not support");
    expect(fetch).not.toHaveBeenCalled();
    await expect(commandDevice(light, "brightness", 80)).rejects.toThrow("Device is unavailable. (HTTP 409)");
    expect(validateValue({ min: 0, max: 100 }, 101)).toBe(false);
    expect(validateValue({ enum: ["cool"] }, "heat")).toBe(false);
  });

  it("reports a missing planner endpoint without presenting an empty database", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => response({ detail: "Not Found" }, 404));
    render(<App/>);
    act(() => fireEvent.click(within(screen.getByRole("navigation")).getByRole("button", { name: "Tasks" })));
    expect(await within(screen.getByRole("main")).findByText("Not Found (HTTP 404)")).toBeInTheDocument();
    expect(screen.queryByText("No tasks in this view.")).not.toBeInTheDocument();
  });

  it("translates scheduler wall time with the configured timezone and weekday", () => {
    expect(dateInput("2026-11-26T09:00", "Asia/Kolkata")).toBe("2026-11-26T03:30:00.000Z");
    expect(recurrence("weekly", "2026-11-26T09:00")).toEqual({ kind: "weekly", hour: 9, minute: 0, weekdays: [3] });
    expect(() => dateInput("2026-03-08T02:30", "America/New_York")).toThrow("does not exist");
  });
});

import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AssistantView, useVoice } from "./Assistant";
import type { LiveEvent } from "./api/desktop";

const stream = vi.hoisted(() => ({ listener: null as ((event: LiveEvent) => void) | null }));
vi.mock("./live", () => ({
  useLive: (listener: (event: LiveEvent) => void) => { stream.listener = listener; },
  useResource: () => ({ loading: false, refresh: vi.fn() }),
  refreshResources: vi.fn(),
}));

function publish(type: string, payload: LiveEvent["payload"]) {
  act(() => stream.listener?.({ type, payload, timestamp_utc: "" }));
}
function Conversation() {
  const voice = useVoice(true);
  return <AssistantView voice={voice}/>;
}

describe("voice capture feedback", () => {
  it("distinguishes a VAD-ended recording from active listening", () => {
    render(<Conversation/>);
    publish("assistant_state_change", { state: "LISTENING", turn_id: "ptt" });
    publish("ptt_capture_ready", { turn_id: "ptt", reason: "speech_end" });
    expect(screen.getByText("Recording ready to send")).toBeInTheDocument();
    publish("assistant_state_change", { state: "TRANSCRIBING", turn_id: "ptt" });
    expect(screen.queryByText("Recording ready to send")).not.toBeInTheDocument();
  });

  it("makes an ignored silent recording visible without claiming a successful action", () => {
    render(<Conversation/>);
    publish("response_completed", { turn_id: "silent", status: "ignored_silence", outcome: "success", silent: true, action_executed: false });
    expect(screen.getByText(/No usable speech was heard/)).toBeInTheDocument();
    expect(screen.queryByText(/Verified action/)).not.toBeInTheDocument();
  });
});

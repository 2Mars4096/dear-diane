import { describe, expect, it } from "vitest";
import { eventAgentName, eventDisclosure, streamedMessageContent } from "../eventPresentation";

describe("shared native agent presentation", () => {
  it("uses recorded backend identity without claiming an unknown Claude worker", () => {
    expect(eventAgentName({ type: "worker_started", payload: { backend: "codex" } })).toBe("Codex");
    expect(eventAgentName({ type: "worker_started", payload: { backend: "claude_code" } })).toBe("Claude Code");
    expect(eventAgentName({ type: "worker_started" })).toBe("Diane");
  });
  it("presents legacy assistant names while retaining native worker identity", () => {
    for (const name of ["DAN", "Super DAN", "Diane"]) {
      expect(eventAgentName({ type: "message", payload: { agent_name: name } })).toBe("Diane");
    }
    expect(eventAgentName({ type: "message", payload: { worker_name: "Claude Code" } })).toBe("Claude Code");
  });
  it("separates emitted reasoning from the assistant answer", () => {
    const event = { type: "model_text_delta", payload: { backend: "codex", text: "Planning", codex_event: { item: { type: "reasoning", text: "Planning" } } } };
    expect(eventDisclosure(event)).toMatchObject({ thinking: true, label: "Thinking summary", body: "Planning" });
    expect(streamedMessageContent("Answer", event)).toBe("Answer");
  });
  it("renders Claude thinking blocks through the same disclosure", () => {
    expect(eventDisclosure({ type: "status_reported", payload: { claude_event: { content_block: { type: "thinking", thinking: "Checking the files" } } } })).toMatchObject({ thinking: true, body: "Checking the files" });
  });
  it("distinguishes token deltas, snapshots, full native messages and telemetry", () => {
    expect(streamedMessageContent("Hel", { type: "model_text_delta", payload: { delta: "lo" } })).toBe("Hello");
    expect(streamedMessageContent("Hel", { type: "model_text_delta", payload: { accumulated: "Hello" } })).toBe("Hello");
    expect(streamedMessageContent("First", { type: "model_text_delta", payload: { text: "Second" } })).toBe("First\n\nSecond");
    expect(streamedMessageContent("Answer", { type: "model_text_delta", summary: "model started" })).toBe("Answer");
  });
  it("exposes actual command output instead of inventing tool results", () => {
    expect(eventDisclosure({ type: "tool_used", payload: { codex_event: { item: { type: "command_execution", command: "npm test", aggregated_output: "5 passed" } } } })).toMatchObject({ command: "npm test", body: "5 passed", thinking: false });
  });
});

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

it('unwraps actual live lead events and presents command output as output', () => {
  expect(eventDisclosure({ type: 'status_reported', summary: 'native_lead.event', payload: { raw: { type: 'item.completed', item: { type: 'command_execution', command: 'npm test', aggregatedOutput: '5 passed', exitCode: 0, status: 'completed' } } } })).toMatchObject({ label: 'Command finished', command: 'npm test', body: '5 passed', output: true, exitCode: 0 });
});
it('reads reasoning summaries and file paths without rendering their JSON envelopes', () => {
  expect(eventDisclosure({ type: 'status_reported', payload: { raw: { item: { type: 'reasoning', summary: [{ text: 'Checking the camera update' }] } } } })).toMatchObject({ label: 'Thinking summary', body: 'Checking the camera update' });
  expect(eventDisclosure({ type: 'status_reported', payload: { raw: { item: { type: 'fileChange', changes: [{ path: 'camera.gd' }] } } } })).toMatchObject({ label: 'Updating files', body: '• camera.gd' });
});
it('hides empty bookkeeping and never uses an internal event identifier as its label', () => {
  expect(eventDisclosure({ type: 'status_reported', summary: 'native_lead.event', payload: { raw: { type: 'thread.tokenUsage.updated' } } }).hidden).toBe(true);
  expect(eventDisclosure({ type: 'status_reported', summary: 'native_lead.event' }).label).toBe('Activity update');
});


it('hides worker streaming fragments while keeping completed answers and errors', () => {
  const base = { type: 'status_reported', summary: 'codex team member: running', source_event_type: 'native_worker.event' };
  expect(eventDisclosure({ ...base, payload: { raw: { type: 'item.agentMessage.delta', delta: 'fragment' } } }).hidden).toBe(true);
  expect(eventDisclosure({ ...base, payload: { raw: { type: 'mcpServer.startupStatus.updated' } } }).hidden).toBe(true);
  expect(eventDisclosure({ ...base, payload: { raw: { type: 'item.completed', item: { type: 'agentMessage', text: 'Review complete' } } } })).toMatchObject({ label: 'Worker message', body: 'Review complete', hidden: false });
  expect(eventDisclosure({ ...base, payload: { raw: { type: 'error', error: { message: 'Command failed' } } } })).toMatchObject({ body: 'Command failed', hidden: false });
});

it('does not evict readable activity when streaming fragments arrive', async () => {
  const { appendActivityEvent } = await import('../eventPresentation');
  const event = { type: 'status_reported', event_type: 'native_worker.event', summary: 'Codex worker: running', detail: { payload: { raw: { type: 'item.completed', item: { type: 'command_execution', command: 'npm test', aggregatedOutput: 'passed' } } } } };
  const initial = appendActivityEvent([], event);
  let current = initial;
  for (let i = 0; i < 100; i++) current = appendActivityEvent(current, { ...event, detail: { payload: { raw: { type: 'item.agentMessage.delta', delta: 'token' } } } });
  expect(current).toBe(initial);
  expect(current[0].activity_id).toBeTruthy();
  expect(appendActivityEvent(current, event)[0].activity_id).toBe(current[0].activity_id);
});

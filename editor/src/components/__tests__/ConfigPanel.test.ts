// @vitest-environment happy-dom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ConfigPanel from "../ConfigPanel";
import { danEdgeToReactFlow, danNodeToReactFlow } from "../../lib/graphAdapter";
import { useGraphStore } from "../../store/useGraphStore";
import type { DanGraph, DataEdge, WorkerNodeType } from "../../types/graph";

function makeRootGraph(): DanGraph {
  return {
    version: "dan_graph_v1",
    metadata: { name: "worker-panel-test" },
    nodes: [],
    edges: [],
    sub_graphs: {},
    entry_points: [],
    exit_points: [],
    shared_context: [],
    artifact_refs: [],
  };
}

function makeWorkerNode(): WorkerNodeType {
  return {
    id: "worker_1",
    node_type: "worker",
    name: "Worker One",
    description: "A test worker",
    input_ports: [{ name: "input", schema: {}, required: false }],
    output_ports: [{ name: "result", schema: {} }],
    position: { x: 0, y: 0 },
    ui: {},
    metadata: {},
    role: "researcher",
    instruction: "Investigate the topic",
    persona: "Concise analyst",
    authority: "leaf",
    model: "gpt-4o",
    tool_ids: ["web_search"],
    code: "",
    language: "python",
    llm_hints: {
      prompt_template: "Analyze: {input}",
      system_prompt: "Stay concise.",
      temperature: 0.4,
    },
    context: {
      instruction_profile_ref: "analyst_profile",
    },
    authority_policy: null,
    execution: null,
    body_graph: null,
    sub_workers: {},
    boundary_contract: null,
  };
}

function makeDataEdge(): DataEdge {
  return {
    id: "edge_1",
    edge_type: "data",
    source_node_id: "worker_1",
    source_port: "result",
    target_node_id: "worker_2",
    target_port: "input",
    ui: {},
    metadata: {},
  };
}

async function renderPanel() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(React.createElement(ConfigPanel));
  });
  return { container, root };
}

function setInputValue(node: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )?.set;
  setter?.call(node, value);
  node.dispatchEvent(new Event("input", { bubbles: true }));
  node.dispatchEvent(new Event("change", { bubbles: true }));
}

function setTextareaValue(node: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    "value",
  )?.set;
  setter?.call(node, value);
  node.dispatchEvent(new Event("input", { bubbles: true }));
  node.dispatchEvent(new Event("change", { bubbles: true }));
}

function setSelectValue(node: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLSelectElement.prototype,
    "value",
  )?.set;
  setter?.call(node, value);
  node.dispatchEvent(new Event("change", { bubbles: true }));
}

function blurNode(node: HTMLElement) {
  node.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
  node.dispatchEvent(new FocusEvent("blur", { bubbles: true }));
}

function toggleCheckbox(node: HTMLInputElement) {
  node.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

describe("ConfigPanel worker section", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const workerNode = makeWorkerNode();
    useGraphStore.setState({
      danGraph: makeRootGraph(),
      graphId: null,
      runId: null,
      layerStack: [],
      nodes: [danNodeToReactFlow(workerNode)],
      edges: [],
      selectedNodeId: workerNode.id,
      selectedEdgeId: null,
      selectedNodeIds: new Set<string>(),
      nodeOutputs: {},
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("renders dedicated worker identity, capability, policy, and composition sections", async () => {
    const { container, root } = await renderPanel();

    expect(container.textContent).toContain("Worker Identity");
    expect(container.textContent).toContain("Worker Capability");
    expect(container.textContent).toContain("Worker Policies");
    expect(container.textContent).toContain("Worker Composition");
    expect(container.textContent).toContain("Body Graph");
    expect(container.textContent).toContain("sub-workers");

    const roleInput = container.querySelector(
      'input[placeholder="manager / reviewer / tool_runner"]',
    ) as HTMLInputElement | null;
    expect(roleInput?.value).toBe("researcher");

    await act(async () => {
      root.unmount();
    });
  });

  it("updates worker identity, llm hints, tool refs, and sub-workers through the store", async () => {
    const { container, root } = await renderPanel();

    const roleInput = container.querySelector(
      'input[placeholder="manager / reviewer / tool_runner"]',
    ) as HTMLInputElement;
    const toolIdsInput = container.querySelector(
      'input[placeholder="web_search, file_read"]',
    ) as HTMLInputElement;
    const promptTemplateArea = container.querySelector(
      'textarea[placeholder="Prompt with {input} placeholders"]',
    ) as HTMLTextAreaElement;
    const authoritySelect = Array.from(container.querySelectorAll("select")).find(
      (node) => (node as HTMLSelectElement).value === "leaf",
    ) as HTMLSelectElement;
    const addSubWorkerButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("+ Add Sub-worker"),
    ) as HTMLButtonElement;

    await act(async () => {
      setInputValue(roleInput, "reviewer");
      setInputValue(toolIdsInput, "web_search, file_read");
      setTextareaValue(promptTemplateArea, "Review: {input}");
      setSelectValue(authoritySelect, "lead");

      addSubWorkerButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const state = useGraphStore.getState();
    const worker = state.nodes.find((node) => node.id === "worker_1")?.data as unknown as WorkerNodeType;

    expect(worker.role).toBe("reviewer");
    expect(worker.tool_ids).toEqual(["web_search", "file_read"]);
    expect(worker.authority).toBe("lead");
    expect(worker.llm_hints).toMatchObject({
      prompt_template: "Review: {input}",
    });
    expect(worker.sub_workers).toBeTruthy();
    expect(Object.keys(worker.sub_workers ?? {})).toHaveLength(1);

    const subGraphKeys = Object.keys(state.danGraph?.sub_graphs ?? {});
    expect(subGraphKeys).toHaveLength(1);

    await act(async () => {
      root.unmount();
    });
  });

  it("updates worker port descriptions and schemas through the store", async () => {
    const { container, root } = await renderPanel();

    const inputDescription = container.querySelector(
      'input[aria-label="input port input description"]',
    ) as HTMLInputElement;
    const inputSchema = container.querySelector(
      'textarea[aria-label="input port input schema"]',
    ) as HTMLTextAreaElement;
    const outputDescription = container.querySelector(
      'input[aria-label="output port result description"]',
    ) as HTMLInputElement;
    const outputSchema = container.querySelector(
      'textarea[aria-label="output port result schema"]',
    ) as HTMLTextAreaElement;

    await act(async () => {
      setInputValue(inputDescription, "Incoming research brief");
      setTextareaValue(inputSchema, '{"type":"object","required":["topic"]}');
      setInputValue(outputDescription, "Reviewed result");
      setTextareaValue(outputSchema, '{"type":"object","properties":{"summary":{"type":"string"}}}');
      await Promise.resolve();
    });

    await act(async () => {
      blurNode(inputSchema);
      blurNode(outputSchema);
    });

    const state = useGraphStore.getState();
    const worker = state.nodes.find((node) => node.id === "worker_1")?.data as unknown as WorkerNodeType;

    expect(worker.input_ports[0].description).toBe("Incoming research brief");
    expect(worker.input_ports[0].schema).toEqual({
      type: "object",
      required: ["topic"],
    });
    expect(worker.output_ports[0].description).toBe("Reviewed result");
    expect(worker.output_ports[0].schema).toEqual({
      type: "object",
      properties: {
        summary: { type: "string" },
      },
    });

    await act(async () => {
      root.unmount();
    });
  });
});

describe("ConfigPanel edge lint section", () => {
  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const workerA = makeWorkerNode();
    const workerB = { ...makeWorkerNode(), id: "worker_2", name: "Worker Two" };
    const danEdge = makeDataEdge();
    useGraphStore.setState({
      danGraph: {
        ...makeRootGraph(),
        nodes: [workerA, workerB],
        edges: [danEdge],
      },
      graphId: null,
      runId: null,
      layerStack: [],
      nodes: [danNodeToReactFlow(workerA), danNodeToReactFlow(workerB)],
      edges: [danEdgeToReactFlow(danEdge)],
      selectedNodeId: null,
      selectedEdgeId: danEdge.id,
      selectedNodeIds: new Set<string>(),
      nodeOutputs: {},
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("enables edge lint and mirrors it into top-level lint plus metadata", async () => {
    const { container, root } = await renderPanel();

    expect(container.textContent).toContain("Lint Config");
    const toggle = container.querySelector(
      'input[aria-label="enable edge lint"]',
    ) as HTMLInputElement;

    await act(async () => {
      toggleCheckbox(toggle);
    });

    const edge = useGraphStore.getState().edges[0].data?.danEdge as unknown as DataEdge;
    expect(edge.lint).toEqual({ severity: "error" });
    expect(edge.metadata?.lint).toEqual({ severity: "error" });

    await act(async () => {
      root.unmount();
    });
  });

  it("applies edited edge lint JSON through the store", async () => {
    const { container, root } = await renderPanel();
    const toggle = container.querySelector(
      'input[aria-label="enable edge lint"]',
    ) as HTMLInputElement;

    await act(async () => {
      toggleCheckbox(toggle);
    });

    const lintArea = container.querySelector(
      'textarea[aria-label="edge lint json"]',
    ) as HTMLTextAreaElement;
    const applyButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("Apply lint JSON"),
    ) as HTMLButtonElement;

    await act(async () => {
      setTextareaValue(
        lintArea,
        '{"structural":{"required_keys":["summary"]},"severity":"warning"}',
      );
      applyButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const edge = useGraphStore.getState().edges[0].data?.danEdge as unknown as DataEdge;
    expect(edge.lint).toEqual({
      structural: { required_keys: ["summary"] },
      severity: "warning",
    });
    expect(edge.metadata?.lint).toEqual({
      structural: { required_keys: ["summary"] },
      severity: "warning",
    });

    await act(async () => {
      root.unmount();
    });
  });
});

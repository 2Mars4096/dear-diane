import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchOrganismLogAnalysis,
  fetchOrganismLogs,
} from "../lib/api";

describe("organism log api helpers", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    vi.restoreAllMocks();
    globalThis.fetch = originalFetch;
  });

  it("requests discovered organism logs for a workspace root", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ root_path: "/tmp/workspace", logs: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ) as typeof fetch;

    await fetchOrganismLogs("/tmp/workspace", { limit: 12 });

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    const [url] = vi.mocked(globalThis.fetch).mock.calls[0] ?? [];
    expect(url).toBe("/api/organism-logs?root_path=%2Ftmp%2Fworkspace&limit=12");
  });

  it("requests analysis for one specific organism log path", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({
          path: "/tmp/workspace/.dan-code/runs/turn-01/events.jsonl",
          log: {
            path: "/tmp/workspace/.dan-code/runs/turn-01/events.jsonl",
            root_path: "/tmp/workspace",
            relative_path: ".dan-code/runs/turn-01/events.jsonl",
            display_name: "Code turn-01",
            product: "dan_code",
            stream_kind: "bounded_run",
            session_id: "",
            turn_id: "",
            task_id: "",
            trace_id: "",
            organism_id: "",
            organ_id: "",
            schema_version: "organism_log_v1",
            event_count: 2,
            span_count: 1,
            size_bytes: 42,
            updated_at: null,
            started_at: null,
            ended_at: null,
          },
          analysis: {
            schema_version: "organism_log_v1",
            event_count: 2,
            span_count: 1,
            timeline: {
              started_at: "",
              ended_at: "",
              duration_ms: 1000,
              max_parallel_spans: 1,
              lanes: [],
              spans: [],
            },
            graph: {
              nodes: [],
              edges: [],
              blocker_chains: [],
              critical_path_span_ids: [],
              critical_path_duration_ms: 1000,
            },
          },
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    ) as typeof fetch;

    await fetchOrganismLogAnalysis(
      ".dan-code/runs/turn-01/events.jsonl",
      { rootPath: "/tmp/workspace" },
    );

    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    const [url] = vi.mocked(globalThis.fetch).mock.calls[0] ?? [];
    expect(url).toBe(
      "/api/organism-logs/analyze?path=.dan-code%2Fruns%2Fturn-01%2Fevents.jsonl&root_path=%2Ftmp%2Fworkspace",
    );
  });
});

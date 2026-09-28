import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getServerHealth } from "../lib/api";

describe("api request timeouts", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    globalThis.fetch = originalFetch;
  });

  it("rejects stalled requests with a timeout error", async () => {
    globalThis.fetch = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      const signal = init?.signal as AbortSignal | undefined;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        );
      });
    }) as typeof fetch;

    const result = expect(getServerHealth()).rejects.toThrow(
      "Request timed out after 10000ms: /api/health",
    );
    await vi.advanceTimersByTimeAsync(10000);
    await result;
  });
});

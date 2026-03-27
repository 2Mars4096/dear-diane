import { EventEmitter } from "node:events";
import http from "node:http";
import { afterEach, describe, expect, it, vi } from "vitest";

import { probeBackendHealth, waitForBackendHealth } from "../../electron/backendHealth";

class FakeIncomingMessage extends EventEmitter {
  statusCode: number;

  constructor(statusCode: number) {
    super();
    this.statusCode = statusCode;
  }

  setEncoding(_encoding: BufferEncoding) {
    return this;
  }
}

class FakeRequest extends EventEmitter {
  setTimeout(_timeoutMs: number, _handler?: () => void) {
    return this;
  }

  destroy(_error?: Error) {
    this.emit("error", _error ?? new Error("destroyed"));
    return this;
  }

  end() {
    return this;
  }
}

describe("backendHealth", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("accepts the DAN health payload", async () => {
    vi.spyOn(http, "request").mockImplementation((_options: any, callback: any) => {
      const req = new FakeRequest();
      queueMicrotask(() => {
        const res = new FakeIncomingMessage(200);
        callback(res);
        res.emit("data", JSON.stringify({ status: "ok", pid: 1 }));
        res.emit("end");
      });
      return req as any;
    });

    await expect(probeBackendHealth({ port: 8000 })).resolves.toBe(true);
  });

  it("rejects non-ok or malformed responses", async () => {
    vi.spyOn(http, "request").mockImplementation((_options: any, callback: any) => {
      const req = new FakeRequest();
      queueMicrotask(() => {
        const res = new FakeIncomingMessage(200);
        callback(res);
        res.emit("data", JSON.stringify({ status: "starting" }));
        res.emit("end");
      });
      return req as any;
    });

    await expect(probeBackendHealth({ port: 8000 })).resolves.toBe(false);
  });

  it("retries until a probe succeeds", async () => {
    let attempts = 0;

    const healthy = await waitForBackendHealth({
      totalTimeoutMs: 100,
      probeIntervalMs: 0,
      probeFn: async () => {
        attempts += 1;
        return attempts >= 3;
      },
    });

    expect(healthy).toBe(true);
    expect(attempts).toBe(3);
  });

  it("returns false when the health budget is exhausted", async () => {
    const healthy = await waitForBackendHealth({
      totalTimeoutMs: 20,
      probeIntervalMs: 5,
      probeFn: async () => false,
    });

    expect(healthy).toBe(false);
  });
});

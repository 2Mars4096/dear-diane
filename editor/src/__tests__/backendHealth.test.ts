import { EventEmitter } from "node:events";
import http from "node:http";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  probeBackendHealth,
  waitForBackendHealth,
  waitForBackendHealthOrRelease,
} from "../../electron/backendHealth";

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

  it("accepts the Diane health payload", async () => {
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

  it("returns released when an unhealthy listener disappears", async () => {
    let checks = 0;

    const result = await waitForBackendHealthOrRelease({
      totalTimeoutMs: 100,
      probeIntervalMs: 0,
      probeFn: async () => false,
      portInUseFn: async () => {
        checks += 1;
        return checks < 3;
      },
    });

    expect(result).toBe("released");
    expect(checks).toBe(3);
  });

  it("prefers healthy reuse when the backend becomes healthy before release", async () => {
    let attempts = 0;

    const result = await waitForBackendHealthOrRelease({
      totalTimeoutMs: 100,
      probeIntervalMs: 0,
      probeFn: async () => {
        attempts += 1;
        return attempts >= 2;
      },
      portInUseFn: async () => true,
    });

    expect(result).toBe("healthy");
  });

  it("returns timeout when the port stays occupied and never becomes healthy", async () => {
    const result = await waitForBackendHealthOrRelease({
      totalTimeoutMs: 20,
      probeIntervalMs: 5,
      probeFn: async () => false,
      portInUseFn: async () => true,
    });

    expect(result).toBe("timeout");
  });
});

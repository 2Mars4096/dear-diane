import { EventEmitter } from "node:events";
import http from "node:http";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildHugoPreviewEnv,
  isUrlReachable,
  resolveHugoCommand,
  resolvePreviewReadyTimeoutMs,
} from "../../electron/hugoPreviewManager";

class FakeIncomingMessage extends EventEmitter {
  destroyed = false;

  destroy() {
    this.destroyed = true;
  }
}

class FakeRequest extends EventEmitter {
  setTimeout(_timeoutMs: number, _handler?: () => void) {
    return this;
  }

  destroy(error?: Error) {
    this.emit("error", error ?? new Error("destroyed"));
    return this;
  }

  end() {
    return this;
  }
}

describe("hugoPreviewManager", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("uses a larger default preview readiness timeout", () => {
    expect(resolvePreviewReadyTimeoutMs({} as NodeJS.ProcessEnv)).toBe(60_000);
  });

  it("allows an env override but clamps very small values", () => {
    expect(
      resolvePreviewReadyTimeoutMs({
        DAN_CONTENT_PREVIEW_READY_TIMEOUT_MS: "90000",
      } as NodeJS.ProcessEnv),
    ).toBe(90_000);
    expect(
      resolvePreviewReadyTimeoutMs({
        DAN_CONTENT_PREVIEW_READY_TIMEOUT_MS: "500",
      } as NodeJS.ProcessEnv),
    ).toBe(5_000);
  });

  it("resolves Hugo from common macOS install paths when app PATH is minimal", () => {
    expect(
      resolveHugoCommand({
        env: { PATH: "/usr/bin:/bin", HOME: "/Users/test" } as NodeJS.ProcessEnv,
        platform: "darwin",
        isExecutable: (targetPath) => targetPath === "/opt/homebrew/bin/hugo",
      }),
    ).toBe("/opt/homebrew/bin/hugo");
  });

  it("allows DAN_HUGO_BIN to provide the exact Hugo binary path", () => {
    expect(
      resolveHugoCommand({
        env: {
          DAN_HUGO_BIN: "/custom/bin/hugo",
          PATH: "",
        } as NodeJS.ProcessEnv,
        platform: "darwin",
        isExecutable: (targetPath) => targetPath === "/custom/bin/hugo",
      }),
    ).toBe("/custom/bin/hugo");
  });

  it("adds the resolved Hugo directory to the preview process PATH", () => {
    const env = buildHugoPreviewEnv("/opt/homebrew/bin/hugo", {
      PATH: "/usr/bin:/bin",
    } as NodeJS.ProcessEnv);

    expect(env.HUGO_ENV).toBe("development");
    expect(env.PATH.split(":").slice(0, 3)).toEqual([
      "/opt/homebrew/bin",
      "/usr/local/bin",
      "/opt/local/bin",
    ]);
  });

  it("treats the preview origin as reachable once headers arrive", async () => {
    const calls: Array<{ method?: string }> = [];

    vi.spyOn(http, "request").mockImplementation((...args: any[]) => {
      const options =
        typeof args[1] === "object" && args[1] ? (args[1] as any) : {};
      const callback =
        typeof args[2] === "function"
          ? (args[2] as (response: FakeIncomingMessage) => void)
          : (args[1] as (response: FakeIncomingMessage) => void);
      calls.push({ method: options.method });

      const request = new FakeRequest();
      queueMicrotask(() => {
        callback(new FakeIncomingMessage());
      });
      return request as any;
    });

    await expect(isUrlReachable("http://127.0.0.1:1313/")).resolves.toBe(true);
    expect(calls).toEqual([{ method: "HEAD" }]);
  });

  it("returns false when the readiness probe errors", async () => {
    vi.spyOn(http, "request").mockImplementation((_url: any, _options: any) => {
      const request = new FakeRequest();
      queueMicrotask(() => {
        request.emit("error", new Error("boom"));
      });
      return request as any;
    });

    await expect(isUrlReachable("http://127.0.0.1:1313/")).resolves.toBe(false);
  });
});

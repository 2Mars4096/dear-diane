import { afterEach, describe, expect, it, vi } from "vitest";

import { createThreadPersistenceCoordinator } from "../threadPersistenceCoordinator";

describe("threadPersistenceCoordinator", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not let an older scheduled snapshot overwrite a newer immediate save", async () => {
    vi.useFakeTimers();
    const persist = vi.fn(async () => {});
    const coordinator = createThreadPersistenceCoordinator<string[]>({ persist });

    coordinator.schedule("wf-1", "thread-1", ["older"], 5000, { silent: true });
    await coordinator.persistNow("wf-1", "thread-1", ["newer"]);

    await vi.runAllTimersAsync();

    expect(persist).toHaveBeenCalledTimes(1);
    expect(persist).toHaveBeenCalledWith(
      "wf-1",
      "thread-1",
      ["newer"],
      undefined,
    );
  });

  it("still flushes a scheduled snapshot when no newer immediate save supersedes it", async () => {
    vi.useFakeTimers();
    const persist = vi.fn(async () => {});
    const coordinator = createThreadPersistenceCoordinator<string[]>({ persist });

    coordinator.schedule("wf-1", "thread-1", ["scheduled"], 5000, {
      silent: true,
    });

    await vi.advanceTimersByTimeAsync(5000);

    expect(persist).toHaveBeenCalledTimes(1);
    expect(persist).toHaveBeenCalledWith(
      "wf-1",
      "thread-1",
      ["scheduled"],
      { silent: true },
    );
  });
});

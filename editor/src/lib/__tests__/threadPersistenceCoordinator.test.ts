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

  it("keeps scheduled snapshots isolated per thread", async () => {
    vi.useFakeTimers();
    const persist = vi.fn(async () => {});
    const coordinator = createThreadPersistenceCoordinator<string[]>({ persist });

    coordinator.schedule("wf-1", "thread-a", ["alpha"], 5000, { silent: true });
    coordinator.schedule("wf-1", "thread-b", ["beta"], 5000, { silent: true });

    await vi.advanceTimersByTimeAsync(5000);

    expect(persist).toHaveBeenCalledTimes(2);
    expect(persist).toHaveBeenNthCalledWith(
      1,
      "wf-1",
      "thread-a",
      ["alpha"],
      { silent: true },
    );
    expect(persist).toHaveBeenNthCalledWith(
      2,
      "wf-1",
      "thread-b",
      ["beta"],
      { silent: true },
    );
  });

  it("flushes pending snapshots across threads instead of dropping non-active ones", async () => {
    vi.useFakeTimers();
    const persist = vi.fn(async () => {});
    const coordinator = createThreadPersistenceCoordinator<string[]>({ persist });

    coordinator.schedule("wf-1", "thread-a", ["alpha"], 5000, { silent: true });
    coordinator.schedule("wf-1", "thread-b", ["beta"], 5000, { silent: true });

    await coordinator.flushPending({
      workflowId: "wf-1",
      threadId: "thread-b",
      value: ["beta"],
      options: { silent: true },
    });

    expect(persist).toHaveBeenCalledTimes(2);
    expect(persist).toHaveBeenCalledWith(
      "wf-1",
      "thread-a",
      ["alpha"],
      { silent: true },
    );
    expect(persist).toHaveBeenCalledWith(
      "wf-1",
      "thread-b",
      ["beta"],
      { silent: true },
    );
  });
});

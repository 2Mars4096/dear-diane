export type ThreadPersistOptions = {
  silent?: boolean;
  label?: string;
};

type PersistFn<T> = (
  workflowId: string,
  threadId: string,
  value: T,
  options?: ThreadPersistOptions,
) => Promise<void>;

type PendingPersist<T> = {
  workflowId: string;
  threadId: string;
  value: T;
  options?: ThreadPersistOptions;
};

export function createThreadPersistenceCoordinator<T>(args: {
  persist: PersistFn<T>;
}) {
  const persistSequence: Record<string, number> = {};
  const persistInFlight: Record<string, boolean> = {};
  const persistLatest: Record<
    string,
    PendingPersist<T> & {
      seq: number;
    }
  > = {};

  const pendingPersists: Record<string, PendingPersist<T>> = {};
  const persistTimers: Record<string, ReturnType<typeof setTimeout>> = {};

  const keyFor = (workflowId: string, threadId: string) =>
    `${workflowId}::${threadId}`;

  const clearPendingIfSuperseded = (workflowId: string, threadId: string) => {
    const key = keyFor(workflowId, threadId);
    delete pendingPersists[key];
    const timer = persistTimers[key];
    if (timer) {
      clearTimeout(timer);
      delete persistTimers[key];
    }
  };

  const flushScheduledKey = async (
    key: string,
    fallback?: PendingPersist<T>,
  ): Promise<void> => {
    const timer = persistTimers[key];
    if (timer) {
      clearTimeout(timer);
      delete persistTimers[key];
    }
    const pending = pendingPersists[key];
    delete pendingPersists[key];
    if (pending) {
      await persistNow(
        pending.workflowId,
        pending.threadId,
        pending.value,
        pending.options,
      );
      return;
    }
    if (fallback) {
      await persistNow(
        fallback.workflowId,
        fallback.threadId,
        fallback.value,
        fallback.options,
      );
    }
  };

  const flushKey = async (key: string): Promise<void> => {
    const pending = persistLatest[key];
    if (!pending) return;
    persistInFlight[key] = true;
    try {
      await args.persist(
        pending.workflowId,
        pending.threadId,
        pending.value,
        pending.options,
      );
    } finally {
      persistInFlight[key] = false;
      const latest = persistLatest[key];
      if (latest?.seq === pending.seq) {
        delete persistLatest[key];
        return;
      }
      await flushKey(key);
    }
  };

  const persistNow = async (
    workflowId: string,
    threadId: string,
    value: T,
    options?: ThreadPersistOptions,
  ): Promise<void> => {
    clearPendingIfSuperseded(workflowId, threadId);
    const key = keyFor(workflowId, threadId);
    const seq = (persistSequence[key] ?? 0) + 1;
    persistSequence[key] = seq;
    persistLatest[key] = {
      seq,
      workflowId,
      threadId,
      value,
      options,
    };
    if (persistInFlight[key]) return;
    await flushKey(key);
  };

  const schedule = (
    workflowId: string,
    threadId: string,
    value: T,
    delayMs = 5000,
    options?: ThreadPersistOptions,
  ): void => {
    const key = keyFor(workflowId, threadId);
    pendingPersists[key] = { workflowId, threadId, value, options };
    if (persistTimers[key]) return;
    persistTimers[key] = setTimeout(() => {
      delete persistTimers[key];
      const pending = pendingPersists[key];
      delete pendingPersists[key];
      if (!pending) return;
      void persistNow(
        pending.workflowId,
        pending.threadId,
        pending.value,
        pending.options,
      );
    }, delayMs);
  };

  const flushPending = async (
    fallback?: PendingPersist<T>,
  ): Promise<void> => {
    const preferredKey = fallback
      ? keyFor(fallback.workflowId, fallback.threadId)
      : null;
    if (preferredKey) {
      await flushScheduledKey(preferredKey, fallback);
    }
    for (const key of Object.keys(pendingPersists)) {
      if (key === preferredKey) continue;
      await flushScheduledKey(key);
    }
    if (
      fallback &&
      !preferredKey &&
      !persistLatest[keyFor(fallback.workflowId, fallback.threadId)]
    ) {
      await persistNow(
        fallback.workflowId,
        fallback.threadId,
        fallback.value,
        fallback.options,
      );
    }
  };

  const cancelForThread = (workflowId: string, threadId: string): void => {
    clearPendingIfSuperseded(workflowId, threadId);
    const key = keyFor(workflowId, threadId);
    delete persistLatest[key];
    delete persistSequence[key];
  };

  const dispose = (): void => {
    for (const timer of Object.values(persistTimers)) {
      clearTimeout(timer);
    }
    for (const key of Object.keys(persistTimers)) {
      delete persistTimers[key];
    }
    for (const key of Object.keys(pendingPersists)) {
      delete pendingPersists[key];
    }
  };

  return {
    persistNow,
    schedule,
    flushPending,
    cancelForThread,
    dispose,
  };
}

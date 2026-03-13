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

  let pendingPersist: PendingPersist<T> | null = null;
  let persistTimer: ReturnType<typeof setTimeout> | null = null;

  const keyFor = (workflowId: string, threadId: string) =>
    `${workflowId}::${threadId}`;

  const clearPendingIfSuperseded = (workflowId: string, threadId: string) => {
    if (
      pendingPersist &&
      pendingPersist.workflowId === workflowId &&
      pendingPersist.threadId === threadId
    ) {
      pendingPersist = null;
      if (persistTimer) {
        clearTimeout(persistTimer);
        persistTimer = null;
      }
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
    pendingPersist = { workflowId, threadId, value, options };
    if (persistTimer) return;
    persistTimer = setTimeout(() => {
      persistTimer = null;
      const pending = pendingPersist;
      pendingPersist = null;
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
    if (persistTimer) {
      clearTimeout(persistTimer);
      persistTimer = null;
    }
    const pending = pendingPersist;
    pendingPersist = null;
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

  const cancelForThread = (workflowId: string, threadId: string): void => {
    clearPendingIfSuperseded(workflowId, threadId);
    const key = keyFor(workflowId, threadId);
    delete persistLatest[key];
    delete persistSequence[key];
  };

  const dispose = (): void => {
    if (persistTimer) {
      clearTimeout(persistTimer);
      persistTimer = null;
    }
    pendingPersist = null;
  };

  return {
    persistNow,
    schedule,
    flushPending,
    cancelForThread,
    dispose,
  };
}

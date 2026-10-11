/** One host-owned schedule, independent of whether Settings is open. */
export const UPDATE_CHECK_INTERVAL = 15 * 60 * 1000;

export function updateSchedule(check: () => Promise<void>, now = Date.now) {
  let lastAttempt: number | null = null;
  let running = false;
  return async () => {
    if (running || lastAttempt !== null && now() - lastAttempt < UPDATE_CHECK_INTERVAL) return;
    lastAttempt = now();
    running = true;
    try { await check(); } finally { running = false; }
  };
}

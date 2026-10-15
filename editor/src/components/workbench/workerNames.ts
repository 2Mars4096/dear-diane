import type { WorkerProfiles } from './modelSelection';

/** Fill missing names once without renumbering or replacing custom names. */
export function namedWorkers(profiles: WorkerProfiles): WorkerProfiles {
  const names = new Set(Object.values(profiles).map(profile => profile.name?.trim()).filter(Boolean));
  let next = 1;
  return Object.fromEntries(Object.entries(profiles).map(([id, profile]) => {
    if (!(profile.enabled || profile.backend) || profile.name?.trim()) return [id, profile];
    while (names.has(`Worker ${next}`)) next++;
    const name = `Worker ${next++}`; names.add(name);
    return [id, { ...profile, name }];
  }));
}
export function nextWorkerName(profiles: WorkerProfiles): string {
  const names = new Set(Object.values(namedWorkers(profiles)).map(profile => profile.name));
  let n = 1; while (names.has(`Worker ${n}`)) n++;
  return `Worker ${n}`;
}

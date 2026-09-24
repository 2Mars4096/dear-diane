import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';

async function digest(file: string): Promise<string> {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}

const comparisons = new Map<string, { stamp: string; different: boolean }>();

/** Only inspect explicit build locations, never scan the user's disk. */
export async function findPreparedBuild(marker: string, target: string, remembered?: string): Promise<string | null> {
  let recorded = '';
  try { recorded = JSON.parse(await fs.readFile(marker, 'utf8')).path || ''; } catch { /* release build */ }
  for (const candidate of [process.env.DAN_LOCAL_UPDATE_PATH, remembered, recorded]) {
    if (typeof candidate !== 'string' || !path.isAbsolute(candidate) || !candidate.endsWith('.app')) continue;
    try {
      const source = await fs.realpath(candidate);
      const installed = await fs.realpath(target);
      if (source === installed || source.startsWith(installed + path.sep)) continue;
      const asar = (bundle: string) => path.join(bundle, 'Contents/Resources/app.asar');
      const [a, b] = await Promise.all([fs.stat(asar(source)), fs.stat(asar(installed))]);
      const key = `${source}\0${installed}`;
      const stamp = `${a.size}:${a.mtimeMs}:${a.ctimeMs}:${b.size}:${b.mtimeMs}:${b.ctimeMs}`;
      let result = comparisons.get(key);
      if (result?.stamp !== stamp) {
        result = {stamp, different: await digest(asar(source)) !== await digest(asar(installed))};
        comparisons.set(key, result);
      }
      if (result.different) return source;
    } catch { /* missing or unfinished build: normal release checking remains available */ }
  }
  return null;
}

export function updateErrorMessage(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  if (/releases\/latest|no published versions|cannot find latest/i.test(raw) && /404|not found|no published/i.test(raw)) return 'No published release is available, or this GitHub account cannot access it. You can install a prepared local update instead.';
  if (/401|403|unauthorized|bad credentials/i.test(raw)) return 'GitHub could not authorize the update check. Refresh your connection or sign in again.';
  if (/ENOTFOUND|ETIMEDOUT|ECONN|network/i.test(raw)) return 'Could not reach the update server. Check your connection and try again.';
  // HTTP dumps and stack traces are never useful in the settings surface.
  if (/headers:|at .*\(|HttpError|https?:\/\//i.test(raw)) return 'The update could not be completed. Try again or use a prepared local update.';
  return raw.split('\n')[0].slice(0, 240);
}

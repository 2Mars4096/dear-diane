// Detached handoff: only runs a previously staged, verified bundle after DAN exits.
const fs = require('node:fs/promises');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const exec = promisify(execFile);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function alive(pid) {
  if (!Number.isInteger(pid) || pid <= 1) return false;
  try { process.kill(pid, 0); return true; } catch (error) { return error.code !== 'ESRCH'; }
}
async function install(job, deps = {}) {
  const exists = async p => { try { await fs.access(p); return true; } catch { return false; } };
  const running = deps.alive || alive;
  const sleep = deps.pause || pause;
  const launch = deps.launch || (target => exec('/usr/bin/open', ['-a', target]));
  const verify = deps.verify || (target => exec('/usr/bin/codesign', ['--verify', '--deep', '--strict', target]));
  const status = async (state, error = '') => fs.writeFile(job.statusPath, JSON.stringify({ state, error, version: job.version, backup: job.backup }));
  let moved = false, replaced = false;
  try {
    await status('waiting');
    for (let i = 0; running(job.appPid) || running(job.backendPid); i++) {
      if (i >= 240) throw new Error('DAN did not finish closing. The installed app was not changed.');
      await sleep(500);
    }
    await verify(job.staged);
    if (await exists(job.backup)) throw new Error('The rollback location already exists.');
    await fs.rename(job.target, job.backup); moved = true;
    await fs.rename(job.staged, job.target); replaced = true;
    await launch(job.target);
    await status('installed');
  } catch (error) {
    if (moved) {
      if (replaced) await fs.rename(job.target, job.staged);
      await fs.rename(job.backup, job.target);
      await launch(job.target).catch(() => {});
    }
    await status('failed', String(error.message || error));
    throw error;
  }
}
module.exports = { install, alive };
if (require.main === module) {
  fs.readFile(process.argv[2], 'utf8').then(JSON.parse).then(job => install(job)).catch(error => { console.error(error); process.exitCode = 1; });
}

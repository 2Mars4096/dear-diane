// One-time handoff for installations that predate the Updates settings panel.
// Invoke from an existing DAN agent session; the native prompt appears only once idle.
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const exec = promisify(execFile);
const { stageBundle, assertNoActiveWork } = require('../dist-electron/localUpdate.js');
const { install, alive } = require('../electron/updateInstaller.cjs');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function main() {
  const [source, target, graphs, updatesDir, appPidText, backendPidText] = process.argv.slice(2);
  const appPid = Number(appPidText), backendPid = Number(backendPidText);
  if (![source, target, graphs, updatesDir].every(value => value && path.isAbsolute(value)) || ![appPid, backendPid].every(pid => Number.isInteger(pid) && pid > 1)) throw Error('Expected absolute source, target, graphs, updates paths, then app and owned-backend PIDs.');
  const command = (await exec('/bin/ps', ['-p', String(appPid), '-o', 'comm='])).stdout.trim();
  if (!command.startsWith(target + '/Contents/MacOS/')) throw Error('The target is not the running DAN app.');
  const backendParent = Number((await exec('/bin/ps', ['-p', String(backendPid), '-o', 'ppid='])).stdout.trim());
  if (backendParent !== appPid) throw Error('The backend is not owned by this DAN app.');
  const prepared = await stageBundle(source, target, updatesDir);
  console.log("Build staged and verified. Waiting for DAN work to finish.");
  try {
    let idle = false;
    for (let i = 0; i < 360; i++) {
      if (!alive(appPid)) throw Error('DAN closed before update confirmation.');
      try { await assertNoActiveWork(graphs); idle = true; } catch { idle = false; }
      if (idle) break;
      await sleep(5000);
    }
    if (!idle) throw Error('Active work did not finish within 30 minutes.');
    console.log("DAN is idle. Showing Install and restart prompt.");
    const answer = await exec('/usr/bin/osascript', ['-e', 'display dialog "The new DAN build is ready. Install it and restart now? Your chats and settings will be kept." with title "DAN update" buttons {"Later", "Install and restart"} default button "Install and restart" cancel button "Later"']);
    if (!answer.stdout.includes('Install and restart')) return;
    await assertNoActiveWork(graphs);
    await exec('/usr/bin/osascript', ['-e', 'tell application id "com.dan.desktop" to quit']);
    await install({ ...prepared, appPid, backendPid });
  } finally { await fs.rm(prepared.staged, { recursive: true, force: true }); }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });

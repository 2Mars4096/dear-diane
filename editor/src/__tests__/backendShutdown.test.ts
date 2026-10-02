import { spawn, execFileSync } from "node:child_process";
import { once } from "node:events";
import { expect, it } from "vitest";
import { stopBackendTree } from "../../electron/backendShutdown";

it.skipIf(process.platform === "win32")("quit reaps an owned backend and its detached, TERM-resistant descendant without touching unrelated processes", async () => {
  const descendant = `process.on('SIGTERM',()=>{}); console.log(process.pid); setInterval(()=>{},1000)`;
  const script = `const {spawn}=require('node:child_process'); const c=spawn(process.execPath,['-e',${JSON.stringify(descendant)}],{detached:true,stdio:['ignore','pipe','ignore']}); c.stdout.on('data',d=>process.stdout.write(d)); process.on('SIGTERM',()=>process.exit(0)); setInterval(()=>{},1000);`;
  const backend = spawn(process.execPath, ["-e", script], {stdio:["ignore","pipe","ignore"]});
  const unrelated = spawn(process.execPath, ["-e", "setInterval(()=>{},1000)"], {stdio:"ignore"});
  let pid = 0;
  try {
    const [chunk] = await once(backend.stdout!, "data"); pid = Number(String(chunk).trim());
    expect(pid).toBeGreaterThan(0);
    await stopBackendTree(backend, 150);
    await new Promise(resolve=>setTimeout(resolve,150));
    let state = "";
    try { state = execFileSync("ps", ["-p",String(pid),"-o","stat="], {stdio:["ignore","pipe","ignore"]}).toString().trim(); } catch { /* reaped */ }
    expect(!state || state.startsWith("Z")).toBe(true);
    expect(backend.exitCode !== null || backend.signalCode !== null).toBe(true);
    expect(unrelated.exitCode).toBeNull();
    expect(unrelated.signalCode).toBeNull();
  } finally {
    backend.kill("SIGKILL"); unrelated.kill("SIGKILL");
    if (pid) { try { process.kill(pid,"SIGKILL"); } catch { /* already reaped */ } }
  }
}, 10000);

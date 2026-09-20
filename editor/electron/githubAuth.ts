import { spawn, execFile, type ChildProcess } from "node:child_process";
import { promisify } from "node:util";
import fs from "node:fs";
const exec = promisify(execFile);
export const githubRelease = { provider: "github" as const, owner: "2Mars4096", repo: "deep-agent-network" };
export type GitHubLogin = { status: "unknown" | "signed_out" | "signing_in" | "connected" | "unavailable"; login: string; code: string; message: string };
export function deviceCode(text: string): string {
  return text.replace(/\x1b\[[0-9;]*m/g, "").match(/one-time code:\s*([A-Z0-9]{4}-[A-Z0-9]{4})/i)?.[1] || "";
}
export function githubEnvironment(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, GH_PROMPT_DISABLED: "1", NO_COLOR: "1" };
  // Use the browser-authorized local account, not inherited automation tokens.
  delete env.GH_TOKEN; delete env.GITHUB_TOKEN;
  return env;
}
function binary(): string {
  return ["/opt/homebrew/bin/gh", "/usr/local/bin/gh"].find(candidate => fs.existsSync(candidate)) || "gh";
}
export class GitHubAuth {
  state: GitHubLogin = { status: "unknown", login: "", code: "", message: "" };
  private generation = 0;
  private child: ChildProcess | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  async refresh() {
    const generation = this.generation;
    try {
      const result = await exec(binary(), ["api", "user", "--jq", ".login"], { env: githubEnvironment(), timeout: 15000 });
      if (generation !== this.generation) return this.state;
      this.state = { status: "connected", login: result.stdout.trim(), code: "", message: "GitHub connected." };
    } catch (error) {
      if (generation !== this.generation) return this.state;
      const missing = (error as NodeJS.ErrnoException).code === "ENOENT";
      this.state = { status: missing ? "unavailable" : "signed_out", login: "", code: "", message: missing ? "Install GitHub CLI to enable browser sign-in on this Mac." : "Sign in to GitHub to access private releases." };
    }
    return this.state;
  }
  async token(): Promise<string> {
    try {
      const result = await exec(binary(), ["auth", "token", "--hostname", "github.com"], { env: githubEnvironment(), timeout: 10000 });
      const token = result.stdout.trim();
      if (!token) throw Error();
      return token;
    } catch { throw new Error("Sign in to GitHub before checking private releases."); }
  }
  start() {
    if (this.child) return this.state;
    this.generation++;
    this.state = { status: "signing_in", login: "", code: "", message: "Opening GitHub browser sign-in…" };
    const child = spawn(binary(), ["auth", "login", "--hostname", "github.com", "--git-protocol", "ssh", "--web", "--skip-ssh-key"], { env: githubEnvironment(), stdio: ["pipe", "pipe", "pipe"] });
    this.child = child;
    let output = "";
    const read = (chunk: Buffer) => {
      if (this.child !== child) return;
      output = (output + chunk.toString()).slice(-4000);
      const code = deviceCode(output);
      if (code) this.state = { ...this.state, code, message: "Enter this code in the GitHub page, then authorize the login." };
    };
    child.stdout?.on("data", read); child.stderr?.on("data", read);
    child.stdin?.on("error", () => {}); child.stdin?.end("\n");
    child.once("error", () => {
      if (this.child !== child) return;
      this.clear(); this.state = { status: "unavailable", login: "", code: "", message: "GitHub sign-in could not start. Check that GitHub CLI is installed." };
    });
    child.once("close", code => {
      if (this.child !== child) return;
      this.clear();
      if (code === 0) void this.refresh();
      else this.state = { status: "signed_out", login: "", code: "", message: "Sign-in was cancelled or expired. Try again." };
    });
    this.timer = setTimeout(() => this.cancel(), 15 * 60 * 1000);
    return this.state;
  }
  private clear() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null; this.child = null;
  }
  cancel() {
    this.generation++;
    const child = this.child; this.clear(); child?.kill();
    this.state = { status: "signed_out", login: "", code: "", message: "Sign-in cancelled." };
    return this.state;
  }
}

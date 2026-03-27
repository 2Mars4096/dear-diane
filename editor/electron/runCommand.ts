import { spawn, type ChildProcess } from "node:child_process";

export type CommandResult = { stdout: string; stderr: string; code: number | null };

type SpawnLike = (
  command: string,
  args?: readonly string[],
  options?: {
    cwd?: string;
    env?: NodeJS.ProcessEnv;
    stdio?: ["ignore", "pipe", "pipe"];
  },
) => ChildProcess;

export const COMMAND_NOT_FOUND_EXIT_CODE = 127;

function isMissingCommandError(err: NodeJS.ErrnoException): boolean {
  return err.code === "ENOENT" || err.message.includes("ENOENT");
}

function missingCommandMessage(command: string): string {
  return `${command} is not installed or not available in DAN's environment. Install ${command} and restart DAN.`;
}

function buildSpawnErrorResult(command: string, err: NodeJS.ErrnoException, stderr: string): CommandResult {
  const detail = isMissingCommandError(err) ? missingCommandMessage(command) : (err.message || String(err));
  const mergedStderr = [stderr.trim(), detail].filter(Boolean).join("\n");
  return {
    stdout: "",
    stderr: mergedStderr,
    code: isMissingCommandError(err) ? COMMAND_NOT_FOUND_EXIT_CODE : -1,
  };
}

export function runCommand(
  command: string,
  args: string[],
  options: {
    cwd: string;
    env?: NodeJS.ProcessEnv;
    timeoutMs: number;
    spawnFn?: SpawnLike;
  },
): Promise<CommandResult> {
  const runSpawn = options.spawnFn ?? spawn;

  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const finish = (result: CommandResult) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      resolve(result);
    };

    let proc: ChildProcess;
    try {
      proc = runSpawn(command, args, {
        cwd: options.cwd,
        env: options.env,
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (err) {
      finish(buildSpawnErrorResult(command, err as NodeJS.ErrnoException, stderr));
      return;
    }

    proc.stdout?.on("data", (chunk: Buffer | string) => {
      stdout += chunk.toString();
    });
    proc.stderr?.on("data", (chunk: Buffer | string) => {
      stderr += chunk.toString();
    });

    timer = setTimeout(() => {
      proc.kill();
      finish({ stdout, stderr, code: -1 });
    }, options.timeoutMs);

    proc.on("close", (code) => {
      finish({ stdout, stderr, code });
    });
    proc.on("error", (err) => {
      finish(buildSpawnErrorResult(command, err as NodeJS.ErrnoException, stderr));
    });
  });
}

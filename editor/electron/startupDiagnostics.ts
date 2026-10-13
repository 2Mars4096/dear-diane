import fs from "node:fs";
import path from "node:path";

/** One bounded receipt per launch; never record inherited environment values. */
export function startupDiagnostics(directory: string) {
  const file = path.join(directory, "startup.log");
  let lines = "";
  return {
    file,
    record(message: string) {
      lines = (lines + `${new Date().toISOString()} ${message}\n`).slice(-32_768);
      try {
        fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
        fs.writeFileSync(file, lines, { mode: 0o600 });
      } catch { /* Diagnostics must not prevent startup or shutdown. */ }
    },
  };
}

/** An asynchronous alert keeps macOS Quit responsive after startup failure. */
export async function reportStartupFailure(
  show: (options: { type: "error"; title: string; message: string; detail: string; buttons: string[] }) => Promise<unknown>,
  quit: () => void,
  error: unknown,
  logFile: string,
) {
  try {
    await show({ type: "error", title: "Dear Diane Startup Failed", message: "Dear Diane could not start its local services.",
      detail: `${String(error)}\n\nStartup diagnostics: ${logFile}`, buttons: ["Quit"] });
  } finally { quit(); }
}

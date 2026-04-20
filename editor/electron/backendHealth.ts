import http from "node:http";
import net from "node:net";

export interface ProbeBackendHealthOptions {
  hostname?: string;
  port?: number;
  path?: string;
  requestTimeoutMs?: number;
}

export interface WaitForBackendHealthOptions extends ProbeBackendHealthOptions {
  totalTimeoutMs?: number;
  probeIntervalMs?: number;
  probeFn?: () => Promise<boolean>;
}

export interface WaitForBackendHealthOrReleaseOptions
  extends WaitForBackendHealthOptions {
  portInUseFn?: () => Promise<boolean>;
}

export type BackendPortWaitResult = "healthy" | "released" | "timeout";

export async function probePortInUse(
  options: ProbeBackendHealthOptions = {},
): Promise<boolean> {
  const hostname = options.hostname ?? "127.0.0.1";
  const port = options.port ?? 8000;
  const requestTimeoutMs = options.requestTimeoutMs ?? 1000;

  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(requestTimeoutMs);
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => {
      resolve(false);
    });
    socket.once("timeout", () => {
      socket.destroy();
      resolve(false);
    });
    socket.connect(port, hostname);
  });
}

export async function probeBackendHealth(
  options: ProbeBackendHealthOptions = {},
): Promise<boolean> {
  const hostname = options.hostname ?? "127.0.0.1";
  const port = options.port ?? 8000;
  const path = options.path ?? "/api/health";
  const requestTimeoutMs = options.requestTimeoutMs ?? 1000;

  return new Promise((resolve) => {
    const req = http.request(
      {
        hostname,
        port,
        path,
        method: "GET",
      },
      (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => {
          body += chunk;
        });
        res.on("end", () => {
          if (res.statusCode !== 200) {
            resolve(false);
            return;
          }
          try {
            const parsed = JSON.parse(body) as { status?: string };
            resolve(parsed.status === "ok");
          } catch {
            resolve(false);
          }
        });
      },
    );

    req.setTimeout(requestTimeoutMs, () => {
      req.destroy(new Error("Backend health probe timed out"));
    });
    req.on("error", () => {
      resolve(false);
    });
    req.end();
  });
}

export async function waitForBackendHealth(
  options: WaitForBackendHealthOptions = {},
): Promise<boolean> {
  const probe = options.probeFn ?? (() => probeBackendHealth(options));
  const totalTimeoutMs = options.totalTimeoutMs ?? 30000;
  const probeIntervalMs = options.probeIntervalMs ?? 250;
  const deadline = Date.now() + totalTimeoutMs;

  while (Date.now() <= deadline) {
    if (await probe()) return true;
    if (Date.now() >= deadline) break;
    await new Promise((resolve) => setTimeout(resolve, probeIntervalMs));
  }

  return false;
}

export async function waitForBackendHealthOrRelease(
  options: WaitForBackendHealthOrReleaseOptions = {},
): Promise<BackendPortWaitResult> {
  const healthProbe = options.probeFn ?? (() => probeBackendHealth(options));
  const portInUseProbe =
    options.portInUseFn ?? (() => probePortInUse(options));
  const totalTimeoutMs = options.totalTimeoutMs ?? 30000;
  const probeIntervalMs = options.probeIntervalMs ?? 250;
  const deadline = Date.now() + totalTimeoutMs;

  while (Date.now() <= deadline) {
    if (await healthProbe()) return "healthy";
    if (!(await portInUseProbe())) return "released";
    if (Date.now() >= deadline) break;
    await new Promise((resolve) => setTimeout(resolve, probeIntervalMs));
  }

  return "timeout";
}

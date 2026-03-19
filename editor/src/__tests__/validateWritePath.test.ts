import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import path from "node:path";
import os from "node:os";

const WORKSPACE = path.join(os.tmpdir(), "dan-test-workspace");

vi.stubEnv("DAN_WORKSPACE_ROOT", WORKSPACE);

function expandHome(p: string): string {
  if (p.startsWith("~")) return p.replace("~", os.homedir());
  return p;
}

function getWorkspaceRoot(): string {
  return process.env.DAN_WORKSPACE_ROOT || process.cwd();
}

function isStrictSandbox(): boolean {
  const val = (process.env.DAN_STRICT_SANDBOX || "").trim().toLowerCase();
  return val === "1" || val === "true";
}

function validateWritePath(filePath: string): string {
  const expanded = expandHome(filePath);
  const resolved = path.resolve(expanded);
  if (!isStrictSandbox()) return resolved;
  const root = path.resolve(getWorkspaceRoot());
  const normalRoot = root.endsWith(path.sep) ? root : root + path.sep;
  if (resolved !== root && !resolved.startsWith(normalRoot)) {
    throw new Error(
      `Write path '${filePath}' resolves outside workspace root '${root}' and DAN_STRICT_SANDBOX=1 is enabled.`,
    );
  }
  return resolved;
}

describe("validateWritePath", () => {
  afterEach(() => {
    delete process.env.DAN_STRICT_SANDBOX;
  });

  it("allows any path when strict sandbox is off", () => {
    delete process.env.DAN_STRICT_SANDBOX;
    expect(() => validateWritePath("/etc/hosts")).not.toThrow();
  });

  it("allows paths inside workspace with strict sandbox on", () => {
    process.env.DAN_STRICT_SANDBOX = "1";
    const result = validateWritePath(path.join(WORKSPACE, "foo.txt"));
    expect(result).toBe(path.join(WORKSPACE, "foo.txt"));
  });

  it("allows the workspace root itself", () => {
    process.env.DAN_STRICT_SANDBOX = "1";
    const result = validateWritePath(WORKSPACE);
    expect(result).toBe(WORKSPACE);
  });

  it("rejects paths outside workspace with strict sandbox on", () => {
    process.env.DAN_STRICT_SANDBOX = "1";
    expect(() => validateWritePath("/etc/hosts")).toThrow(
      /resolves outside workspace root/,
    );
  });

  it("rejects relative traversal outside workspace", () => {
    process.env.DAN_STRICT_SANDBOX = "1";
    expect(() =>
      validateWritePath(path.join(WORKSPACE, "..", "escape.txt")),
    ).toThrow(/resolves outside workspace root/);
  });

  it("treats DAN_STRICT_SANDBOX=true as enabled", () => {
    process.env.DAN_STRICT_SANDBOX = "true";
    expect(() => validateWritePath("/etc/hosts")).toThrow(
      /resolves outside workspace root/,
    );
  });

  it("treats DAN_STRICT_SANDBOX=0 as disabled", () => {
    process.env.DAN_STRICT_SANDBOX = "0";
    expect(() => validateWritePath("/etc/hosts")).not.toThrow();
  });
});

import { describe, expect, it } from "vitest";

import {
  getBuiltinTerminalProfiles,
  getDefaultShellArgs,
  getDefaultShellPath,
} from "../lib/terminalProfiles";
import { normalizeTerminalSettings } from "../store/useSettingsStore";

describe("terminalProfiles", () => {
  it("returns windows terminal profiles", () => {
    const profiles = getBuiltinTerminalProfiles("win32");
    expect(profiles[0]).toMatchObject({
      id: "powershell",
      shell: "powershell.exe",
      isDefault: true,
    });
    expect(profiles.map((profile) => profile.id)).toContain("cmd");
  });

  it("returns linux defaults without zsh assumption", () => {
    const profiles = getBuiltinTerminalProfiles("linux");
    expect(profiles[0]).toMatchObject({
      id: "bash",
      shell: "/bin/bash",
      isDefault: true,
    });
  });

  it("prefers comspec on windows", () => {
    expect(getDefaultShellPath("win32", { ComSpec: "C:\\Windows\\System32\\cmd.exe" })).toBe(
      "C:\\Windows\\System32\\cmd.exe",
    );
  });

  it("prefers env shell on unix", () => {
    expect(getDefaultShellPath("linux", { SHELL: "/usr/bin/fish" })).toBe("/usr/bin/fish");
  });

  it("adds login args for common unix shells only", () => {
    expect(getDefaultShellArgs("/bin/zsh", "darwin")).toEqual(["-l"]);
    expect(getDefaultShellArgs("/usr/bin/fish", "linux")).toEqual([]);
    expect(getDefaultShellArgs("powershell.exe", "win32")).toEqual([]);
  });

  it("migrates legacy unix terminal settings to platform defaults", () => {
    const normalized = normalizeTerminalSettings(
      {
        terminalProfiles: [
          { id: "zsh", name: "zsh", shell: "/bin/zsh", isDefault: true },
          { id: "bash", name: "bash", shell: "/bin/bash" },
          { id: "sh", name: "sh", shell: "/bin/sh" },
        ],
        defaultTerminalProfile: "zsh",
      },
      "linux",
    );

    expect(normalized.terminalProfiles[0]).toMatchObject({
      id: "bash",
      shell: "/bin/bash",
      args: ["-l"],
      isDefault: true,
    });
    expect(normalized.defaultTerminalProfile).toBe("bash");
  });

  it("preserves custom terminal settings", () => {
    const normalized = normalizeTerminalSettings(
      {
        terminalProfiles: [
          { id: "fish", name: "fish", shell: "/usr/bin/fish", args: ["-l"] },
        ],
        defaultTerminalProfile: "fish",
      },
      "linux",
    );

    expect(normalized.terminalProfiles).toEqual([
      { id: "fish", name: "fish", shell: "/usr/bin/fish", args: ["-l"] },
    ]);
    expect(normalized.defaultTerminalProfile).toBe("fish");
  });
});


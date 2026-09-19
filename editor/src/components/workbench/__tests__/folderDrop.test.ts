import { describe, expect, it, vi } from "vitest";
import { droppedFolderPath } from "../folderDrop";

const transfer = (directory: boolean, count = 1) => ({
  files: Array.from({ length: count }, () => ({ name: "Project" })),
  items: [{ kind: "file", webkitGetAsEntry: () => ({ isDirectory: directory }) }],
}) as unknown as DataTransfer;

describe("project folder drop", () => {
  it("uses the resolved native path including spaces and Unicode", async () => {
    expect(await droppedFolderPath(transfer(true), async () => "/Users/me/研究 Project"))
      .toEqual({ path: "/Users/me/研究 Project" });
  });
  it("rejects files and multiple folders without invoking the bridge", async () => {
    const resolve = vi.fn();
    expect(await droppedFolderPath(transfer(false), resolve)).toHaveProperty("error");
    expect(await droppedFolderPath(transfer(true, 2), resolve)).toHaveProperty("error");
    expect(resolve).not.toHaveBeenCalled();
  });
  it("does not manufacture a path from a browser folder name", async () => {
    expect(await droppedFolderPath(transfer(true), async () => null)).toHaveProperty("error");
    expect(await droppedFolderPath(transfer(true), async () => { throw new Error("unavailable"); })).toHaveProperty("error");
  });
});

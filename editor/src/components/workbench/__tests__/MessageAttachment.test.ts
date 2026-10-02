// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { MessageAttachment } from "../MessageAttachment";
import { nativeShell } from "../../../lib/electronBridge";
vi.mock("../../../lib/electronBridge", () => ({ isElectron: () => true, nativeShell: { openPath: vi.fn() } }));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it("stages saved attachments in the sidecar and reports unavailable originals", async () => {
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const opened = vi.fn(); window.addEventListener('dan:open-file', opened);
  try {
    for (const ext of ["png", "mp4", "docx", "pdf", "txt"]) {
      const path = `/saved files/image #1.${ext}`;
      vi.mocked(nativeShell.openPath).mockResolvedValue(true);
      await act(async () => root.render(createElement(MessageAttachment, {attachment:{path,filename:`image #1.${ext}`,size:2048}})));
      await act(async () => host.querySelector("a")!.click());
      expect((opened.mock.lastCall![0] as CustomEvent).detail.file.path).toBe(path);
      expect(nativeShell.openPath).not.toHaveBeenCalled();
      expect(host.textContent).toContain("2 KB");
      expect(host.querySelector("a")!.href).toContain(`image%20%231.${ext}`);
    }
    await act(async () => root.render(createElement(MessageAttachment, {attachment:{filename:"old.png"}})));
    expect(host.querySelector("a")).toBeNull();
    expect(host.textContent).toContain("Original file unavailable");
  } finally { await act(async () => root.unmount()); host.remove(); window.removeEventListener('dan:open-file', opened); }
});

it("keeps remote attachments on the remote preview even if an Electron bridge is present", async () => {
  const meta = document.createElement('meta'); meta.name = 'dan-remote-machine'; meta.content = 'mini'; document.head.append(meta);
  const host = document.createElement('div'), root = createRoot(host);
  vi.mocked(nativeShell.openPath).mockClear();
  try {
    await act(async () => root.render(createElement(MessageAttachment, { attachment: { path: '/home/me/paper.pdf', filename: 'paper.pdf' } })));
    await act(async () => host.querySelector('a')!.click());
    expect(nativeShell.openPath).not.toHaveBeenCalled();
    expect(host.querySelector('a')!.href).toContain('/api/workspace-files/preview/');
  } finally { act(() => root.unmount()); meta.remove(); }
});

// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { RequestActions } from "../RequestActions";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

it("cancels without resending, blocks empty edits, and keeps drafts on failure", async () => {
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const onEdit = vi.fn().mockRejectedValue(new Error("Connection failed"));
  const button = (text: string) => [...host.querySelectorAll("button")].find((item) => item.textContent === text)!;
  const change = async (value: string) => {
    await act(async () => {
      const field = host.querySelector("textarea")!;
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(field, value);
      field.dispatchEvent(new Event("input", { bubbles: true }));
    });
  };
  try {
    await act(async () => root.render(createElement(RequestActions, { text: "Original", onEdit })));
    await act(async () => button("Edit").click());
    expect(document.activeElement).toBe(host.querySelector("textarea"));
    await change("Draft");
    await act(async () => button("Cancel").click());
    expect(onEdit).not.toHaveBeenCalled();
    await act(async () => button("Edit").click());
    expect(host.querySelector("textarea")!.value).toBe("Original");
    await change("   ");
    expect(button("Save & resend").disabled).toBe(true);
    await change("Corrected request");
    await act(async () => button("Save & resend").click());
    expect(onEdit).toHaveBeenCalledWith("Corrected request");
    expect(host.querySelector("textarea")!.value).toBe("Corrected request");
    expect(host.querySelector('[role="alert"]')!.textContent).toBe("Connection failed");
    onEdit.mockResolvedValueOnce(undefined);
    await act(async () => button("Save & resend").click());
    expect(host.querySelector("textarea")).toBeNull();
    await act(async () => root.render(createElement(RequestActions, { text: "Corrected request", onEdit, disabled: true })));
    expect(button("Edit").disabled).toBe(true);
  } finally { act(() => root.unmount()); host.remove(); }
});

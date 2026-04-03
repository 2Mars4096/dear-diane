// @vitest-environment happy-dom

import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppMode } from "../../store/useAppStore";
import { useAppStore } from "../../store/useAppStore";
import { useModeScopedWindowEvent } from "../useModeScopedWindowEvent";

function Listener({
  mode,
  onEvent,
}: {
  mode: AppMode;
  onEvent: (event: Event) => void;
}) {
  useModeScopedWindowEvent(mode, "mode:test", onEvent);
  return null;
}

describe("useModeScopedWindowEvent", () => {
  beforeEach(() => {
    useAppStore.setState({ activeMode: "research" });
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("only fires while its mode is active", async () => {
    const onEvent = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(
        React.createElement(Listener, {
          mode: "research",
          onEvent,
        }),
      );
    });

    window.dispatchEvent(new CustomEvent("mode:test"));
    expect(onEvent).toHaveBeenCalledTimes(1);

    useAppStore.setState({ activeMode: "development" });
    window.dispatchEvent(new CustomEvent("mode:test"));
    expect(onEvent).toHaveBeenCalledTimes(1);

    useAppStore.setState({ activeMode: "research" });
    window.dispatchEvent(new CustomEvent("mode:test"));
    expect(onEvent).toHaveBeenCalledTimes(2);

    await act(async () => {
      root.unmount();
    });
  });

  it("removes the listener on unmount", async () => {
    const onEvent = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(
        React.createElement(Listener, {
          mode: "research",
          onEvent,
        }),
      );
    });

    await act(async () => {
      root.unmount();
    });

    window.dispatchEvent(new CustomEvent("mode:test"));
    expect(onEvent).not.toHaveBeenCalled();
  });

  it("keeps one DOM listener while still calling the latest callback", async () => {
    const first = vi.fn();
    const second = vi.fn();
    const addSpy = vi.spyOn(window, "addEventListener");
    const removeSpy = vi.spyOn(window, "removeEventListener");
    const countAdds = () =>
      addSpy.mock.calls.filter(([eventName]) => eventName === "mode:test").length;
    const countRemoves = () =>
      removeSpy.mock.calls.filter(([eventName]) => eventName === "mode:test").length;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(
        React.createElement(Listener, {
          mode: "research",
          onEvent: first,
        }),
      );
    });

    expect(countAdds()).toBe(1);
    expect(countRemoves()).toBe(0);

    await act(async () => {
      root.render(
        React.createElement(Listener, {
          mode: "research",
          onEvent: second,
        }),
      );
    });

    expect(countAdds()).toBe(1);
    expect(countRemoves()).toBe(0);

    window.dispatchEvent(new CustomEvent("mode:test"));
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);

    await act(async () => {
      root.unmount();
    });

    expect(countRemoves()).toBe(1);
  });
});

import { useEffect, useRef } from "react";
import type { AppMode } from "../store/useAppStore";
import { useAppStore } from "../store/useAppStore";

export function useModeScopedWindowEvent<TEvent extends Event = Event>(
  mode: AppMode,
  eventName: string,
  listener: (event: TEvent) => void,
  options?: boolean | AddEventListenerOptions,
) {
  const listenerRef = useRef(listener);

  useEffect(() => {
    listenerRef.current = listener;
  }, [listener]);

  useEffect(() => {
    const wrappedListener = (event: Event) => {
      if (useAppStore.getState().activeMode !== mode) return;
      listenerRef.current(event as TEvent);
    };

    window.addEventListener(eventName, wrappedListener, options);
    return () => {
      window.removeEventListener(eventName, wrappedListener, options);
    };
  }, [eventName, mode, options]);
}

import { useEffect, useRef, useState } from "react";
import type { ChatV2TaskSnapshot } from "../../lib/chatV2Api";
import type { TeamWorker } from "./teamPresentation";
import "../../lib/electronBridge";
import { requestJson } from "../../lib/http";
const KEY = "dan.work.notifications";
export type Attention = { id: string; state: string; thread: string; worker?: string; title: string };
const terminal = new Set(["completed", "failed", "blocked", "needs_input", "interrupted"]);
export function attentionTransitions(previous: Map<string, string>, items: Attention[], watching: string, notifyNew = false): Attention[] {
  const alerts: Attention[] = [];
  for (const item of items) {
    const old = previous.get(item.id);
    previous.set(item.id, item.state);
    if ((old !== undefined || notifyNew) && old !== item.state && terminal.has(item.state) && item.thread !== watching) alerts.push(item);
  }
  return alerts;
}
function enabled() { return localStorage.getItem(KEY) !== "off"; }
export function NotificationSetting() {
  const browser = !window.electronAPI?.attention;
  const [on, setOn] = useState(() => enabled() && (!browser || ("Notification" in window && Notification.permission === "granted")));
  const [message, setMessage] = useState("");
  return <div className="wb-notification-setting"><label><input type="checkbox" checked={on} onChange={async event => {
    const value = event.target.checked;
    if (value && browser) {
      if (!("Notification" in window)) { setMessage("This browser does not support notifications."); return; }
      if (await Notification.requestPermission() !== "granted") { setMessage("Allow notifications in your browser settings."); return; }
    }
    localStorage.setItem(KEY, value ? "on" : "off"); setOn(value); setMessage("");
  }} />Notify when work finishes or needs me</label>{message && <p role="status">{message}</p>}</div>;
}

export default function WorkNotifications({ tasks, tasksReady = true, activeThread, watching, onOpen }: { tasks: ChatV2TaskSnapshot[]; tasksReady?: boolean; activeThread: string; watching: boolean; onOpen: (thread: string, worker?: string) => void }) {
  const previous = useRef(new Map<string, string>());
  const initialized = useRef({ tasks: false, workers: false });
  const latest = useRef({ tasks, tasksReady, activeThread, watching, onOpen }); latest.current = { tasks, tasksReady, activeThread, watching, onOpen };
  useEffect(() => {
    let disposed = false, pending = false;
    const controller = new AbortController();
    const off = window.electronAPI?.attention?.onOpen(target => latest.current.onOpen(target.thread, target.worker));
    async function refresh() {
      if (pending || disposed) return;
      pending = true;
      let workerItems: Attention[] = [];
      let workersLoaded = false;
      try {
        const { workers } = await requestJson<{ workers: (TeamWorker & { thread_id: string; attempt: string; request: string })[] }>("/api/worker-attention", { signal: controller.signal });
        workersLoaded = true;
        workerItems = workers.map(worker => ({ id: `worker:${worker.worker_id}:${worker.attempt}:${worker.request}`, state: `${worker.status}`, thread: worker.thread_id, worker: worker.worker_id, title: worker.status === "completed" ? "Team task finished" : "A worker needs your attention" }));
      } catch { /* task notifications remain available */ }
      pending = false;
      if (disposed) return;
      const current = latest.current;
      const items: Attention[] = current.tasks.map(task => ({ id: `task:${task.task_id}:${String(task.metadata.active_run_id || "")}`, state: task.status, thread: task.thread_id, title: task.status === "completed" ? "Work finished" : "Work needs your attention" }));
      const visible = current.watching && document.visibilityState === "visible" && document.hasFocus() ? current.activeThread : "";
      const alerts = [...attentionTransitions(previous.current, items, visible, initialized.current.tasks), ...attentionTransitions(previous.current, workerItems, visible, initialized.current.workers)];
      if (current.tasksReady) initialized.current.tasks = true;
      if (workersLoaded) initialized.current.workers = true;
      if (!enabled()) return;
      // Group simultaneous changes in a session into one alert, prioritizing input.
      const grouped = new Map<string, Attention>();
      for (const item of alerts) if (!grouped.has(item.thread) || item.state !== "completed") grouped.set(item.thread, item);
      for (const item of grouped.values()) {
        const target = { thread: item.thread, worker: item.worker, title: item.title };
        if (window.electronAPI?.attention) await window.electronAPI.attention.show(target).catch(() => {});
        else if ("Notification" in window && Notification.permission === "granted") {
          const notification = new Notification(item.title, { tag: item.thread });
          notification.onclick = () => { window.focus(); latest.current.onOpen(item.thread, item.worker); notification.close(); };
        }
      }
    }
    void refresh(); const timer = window.setInterval(() => void refresh(), 3000);
    return () => { disposed = true; controller.abort(); clearInterval(timer); off?.(); };
  }, []);
  return null;
}

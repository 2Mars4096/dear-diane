// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import RecentSessions, { visitSession, previousSessions, sessionKey, type RecentSession } from "../RecentSessions";
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const row = (id: string): RecentSession => ({ id, workflowId: "project", title: id, project: "Project", workspaceId: "p", root: "/p" });
it("tracks visits automatically and retains nine destinations excluding the current session", () => {
  let rows: RecentSession[] = [];
  for (let i=0;i<12;i++) rows=visitSession(rows,row(String(i)));
  expect(rows).toHaveLength(10);
  expect(previousSessions(rows,sessionKey(row("11"))).map(r=>r.id)).toEqual(["10","9","8","7","6","5","4","3","2"]);
  rows=visitSession(rows,row("5"));
  expect(previousSessions(rows,sessionKey(row("5")))[0].id).toBe("11");
});
it("freezes number targets until modifier release, persists history, and works with sidebar hidden", () => {
  localStorage.clear();
  const host=document.createElement("div"), sidebar=document.createElement("div"), root=createRoot(host), select=vi.fn();
  const render=(id:string, visible=true)=>act(()=>root.render(createElement(RecentSessions,{active:row(id),host:visible?sidebar:null,titles:{},excluded:[],onSelect:select})));
  const down=(key:string)=>act(()=>window.dispatchEvent(new KeyboardEvent("keydown",{key,ctrlKey:true,cancelable:true})));
  try {
    render("a");render("b");render("c");
    expect(sidebar.textContent).toContain("bCtrl+1");
    down("Control");down("1");expect(select).toHaveBeenLastCalledWith(row("b"));render("b");
    down("2");expect(select).toHaveBeenLastCalledWith(row("a"));render("a");
    expect(sidebar.textContent).toContain("bCtrl+1");
    act(()=>window.dispatchEvent(new KeyboardEvent("keyup",{key:"Control"})));
    render("a",false); down("1");expect(select).toHaveBeenLastCalledWith(row("b"));
    expect(JSON.parse(localStorage.getItem("dan.recentSessions.v1")!).map((r:RecentSession)=>r.id)).toEqual(["a","b","c"]);
  } finally {act(()=>root.unmount());localStorage.clear();}
});
it("ignores background title changes and releases frozen targets when the window loses focus", () => {
  localStorage.setItem("dan.recentSessions.v1",JSON.stringify([row("c"),row("b"),row("a")]));
  const host=document.createElement("div"),root=createRoot(host),select=vi.fn();
  const render=(id:string,title=id)=>act(()=>root.render(createElement(RecentSessions,{active:{...row(id),title},host:null,titles:{},excluded:[],onSelect:select})));
  try {
    render("c");render("c","New title");
    act(()=>window.dispatchEvent(new KeyboardEvent("keydown",{key:"1",ctrlKey:true})));
    render("b");act(()=>window.dispatchEvent(new Event("blur")));
    act(()=>window.dispatchEvent(new KeyboardEvent("keydown",{key:"1",ctrlKey:true})));
    expect(select.mock.calls.at(-1)?.[0].id).toBe("c");
  }finally{act(()=>root.unmount());localStorage.clear();}
});

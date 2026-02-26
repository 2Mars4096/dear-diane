import { useState, useRef, useEffect, useMemo, useCallback } from "react";
import { createPortal } from "react-dom";
import { useGraphStore } from "../store/useGraphStore";
import { PREDEFINED_AGENT_TEMPLATES } from "../lib/paletteTemplates";

const STATUS_DOT: Record<string, string> = {
  running: "bg-yellow-400 animate-pulse",
  pending: "bg-yellow-400 animate-pulse",
  completed: "bg-green-500",
  failed: "bg-red-500",
};

function getFreqMap(): Record<string, number> {
  try {
    const raw = localStorage.getItem("dan_tpl_freq");
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function bumpFreq(graphId: string) {
  const map = getFreqMap();
  map[graphId] = (map[graphId] ?? 0) + 1;
  localStorage.setItem("dan_tpl_freq", JSON.stringify(map));
}

type PickerMode = "new" | "replace";

interface PickerState {
  mode: PickerMode;
  anchorRect: DOMRect;
}

export default function TabBar() {
  const tabs = useGraphStore((s) => s.tabs);
  const activeTabId = useGraphStore((s) => s.activeTabId);
  const graphList = useGraphStore((s) => s.graphList);
  const runStatus = useGraphStore((s) => s.runStatus);
  const switchTab = useGraphStore((s) => s.switchTab);
  const closeTab = useGraphStore((s) => s.closeTab);
  const openTab = useGraphStore((s) => s.openTab);
  const openTabFromTemplate = useGraphStore((s) => s.openTabFromTemplate);
  const replaceActiveTabGraph = useGraphStore((s) => s.replaceActiveTabGraph);
  const tabCache = useGraphStore((s) => s.tabCache);
  const loadGraphList = useGraphStore((s) => s.loadGraphList);

  const [picker, setPicker] = useState<PickerState | null>(null);
  const [search, setSearch] = useState("");
  const [refreshingList, setRefreshingList] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const closePicker = useCallback(() => {
    setPicker(null);
    setSearch("");
  }, []);

  const refreshList = useCallback(async () => {
    setRefreshingList(true);
    try {
      await loadGraphList();
    } finally {
      setRefreshingList(false);
    }
  }, [loadGraphList]);

  useEffect(() => {
    if (!picker) return;
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        closePicker();
      }
    };
    document.addEventListener("mousedown", handler);
    requestAnimationFrame(() => searchRef.current?.focus());
    return () => document.removeEventListener("mousedown", handler);
  }, [picker, closePicker]);

  // Re-read localStorage each time picker opens
  const frequentGraphs = useMemo(() => {
    const freq = getFreqMap();
    return [...graphList]
      .filter((g) => (freq[g.graph_id] ?? 0) > 0)
      .sort((a, b) => (freq[b.graph_id] ?? 0) - (freq[a.graph_id] ?? 0))
      .slice(0, 5);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphList, picker]);

  const filteredGraphs = useMemo(() => {
    if (!search) return graphList;
    const lower = search.toLowerCase();
    return graphList.filter((g) =>
      (g.name || g.graph_id).toLowerCase().includes(lower),
    );
  }, [graphList, search]);

  const handleSelect = (graphId: string) => {
    bumpFreq(graphId);
    if (picker?.mode === "new") openTab(graphId);
    else replaceActiveTabGraph(graphId);
    closePicker();
  };

  const handleBlank = () => {
    openTab("blank");
    closePicker();
  };

  const handleTemplateStarter = (templateId: string) => {
    openTabFromTemplate(templateId);
    closePicker();
  };

  const tabDisplayNames = (() => {
    const counts: Record<string, number> = {};
    const names: Record<string, string> = {};
    for (const tab of tabs) {
      const base = tab.graphName || tab.graphId;
      counts[base] = (counts[base] ?? 0) + 1;
    }
    const seen: Record<string, number> = {};
    for (const tab of tabs) {
      const base = tab.graphName || tab.graphId;
      if (counts[base] > 1) {
        seen[base] = (seen[base] ?? 0) + 1;
        names[tab.id] = seen[base] === 1 ? base : `${base} (${seen[base]})`;
      } else {
        names[tab.id] = base;
      }
    }
    return names;
  })();

  const getTabRunStatus = (tab: (typeof tabs)[0]): string | null => {
    if (tab.id === activeTabId) return runStatus;
    return tab.cachedRunStatus ?? tabCache[tab.id]?.runStatus ?? null;
  };

  const togglePicker = async (mode: PickerMode, el: HTMLElement) => {
    if (picker?.mode === mode) { closePicker(); return; }
    setRefreshingList(true);
    await loadGraphList(); // Actively reload from server (picks up newly built graphs)
    setRefreshingList(false);
    setPicker({ mode, anchorRect: el.getBoundingClientRect() });
  };

  const dropdownWidth = picker ? Math.max(picker.anchorRect.width, 220) : 220;

  return (
    <div className="flex items-center gap-0 bg-gray-50 border-b border-gray-200 px-1 min-h-[32px] shrink-0">
      {/* Scrollable tab strip */}
      <div className="flex items-center gap-0 overflow-x-auto flex-1 min-w-0">
        {tabs.map((tab) => {
          const isActive = tab.id === activeTabId;
          const status = getTabRunStatus(tab);
          return (
            <div
              key={tab.id}
              onClick={(e) => {
                if (isActive) togglePicker("replace", e.currentTarget);
                else switchTab(tab.id);
              }}
              className={`group flex items-center gap-1.5 px-3 py-1.5 cursor-pointer text-xs whitespace-nowrap border-b-2 transition-colors ${
                isActive
                  ? "bg-white border-indigo-500 text-gray-800 font-medium"
                  : "border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-100"
              }`}
            >
              {status && (
                <span
                  className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${STATUS_DOT[status] ?? "bg-gray-400"}`}
                />
              )}
              <span className="max-w-[160px] truncate">
                {tabDisplayNames[tab.id] ?? (tab.graphName || tab.graphId)}
              </span>
              {isActive && (
                <span className="text-[8px] text-gray-400 ml-0.5">▾</span>
              )}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  closeTab(tab.id);
                }}
                className="ml-0.5 opacity-0 group-hover:opacity-100 text-gray-400 hover:text-gray-700 text-[10px] leading-none"
                title="Close tab"
              >
                ×
              </button>
            </div>
          );
        })}
      </div>

      {/* + New button */}
      <button
        onClick={(e) => togglePicker("new", e.currentTarget)}
        className="flex-shrink-0 px-2 py-1 text-xs text-gray-400 hover:text-indigo-600 hover:bg-gray-100 rounded transition-colors"
        title="Open new tab"
      >
        + New
      </button>

      {/* Shared template picker — portal to avoid tab-strip overflow clipping */}
      {picker && createPortal(
        <div
          ref={dropdownRef}
          className="bg-white border border-gray-200 rounded-md shadow-lg flex flex-col"
          style={{
            position: "fixed",
            left: Math.max(0, Math.min(picker.anchorRect.left, window.innerWidth - dropdownWidth - 8)),
            top: picker.anchorRect.bottom + 4,
            width: dropdownWidth,
            maxHeight: 340,
            zIndex: 9999,
          }}
        >
          {/* Header + Refresh */}
          <div className="flex items-center justify-between px-3 py-1 border-b border-gray-100">
            <span className="text-[10px] uppercase tracking-wide text-gray-400">
              {picker.mode === "new" ? "Open in new tab" : "Switch template"}
            </span>
            <button
              onClick={refreshList}
              disabled={refreshingList}
              className="p-1 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded transition-colors disabled:opacity-50"
              title="Reload graph list from server"
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={refreshingList ? "animate-spin" : ""}>
                <path d="M21 2v6h-6" /><path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
                <path d="M3 22v-6h6" /><path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
              </svg>
            </button>
          </div>

          {/* Search */}
          <div className="px-2 py-1.5 border-b border-gray-100">
            <input
              ref={searchRef}
              type="text"
              placeholder="Search templates…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full text-[11px] px-2 py-1 rounded border border-gray-200 bg-gray-50 text-gray-700 outline-none focus:ring-1 focus:ring-indigo-400"
            />
          </div>

          <div className="overflow-y-auto flex-1">
            {/* Start from: Blank + built-in templates (when not searching or when search matches) */}
            {picker.mode === "new" && (
              <>
                {(!search || "blank".includes(search.toLowerCase())) && (
                  <button
                    onClick={handleBlank}
                    className="flex items-center gap-1.5 w-full text-left px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-50 border-b border-gray-100"
                  >
                    <span className="text-gray-400 text-[10px]">+</span>
                    <span>Blank</span>
                  </button>
                )}
                {PREDEFINED_AGENT_TEMPLATES.filter(
                  (t) =>
                    !search ||
                    t.label.toLowerCase().includes(search.toLowerCase()) ||
                    t.description.toLowerCase().includes(search.toLowerCase()),
                ).map((t) => (
                  <button
                    key={t.id}
                    onClick={() => handleTemplateStarter(t.id)}
                    className="flex items-center gap-1.5 w-full text-left px-3 py-1.5 text-xs text-indigo-700 hover:bg-indigo-50 border-b border-gray-100"
                  >
                    <span className="text-indigo-400 text-[10px]">◇</span>
                    <span>{t.label}</span>
                  </button>
                ))}
                <div className="mx-2 my-0.5 border-b border-gray-100" />
              </>
            )}

            {/* Frequent (when not searching, and at least 1 tracked) */}
            {!search && frequentGraphs.length > 0 && (
              <>
                <div className="px-3 pt-1.5 pb-0.5 text-[10px] uppercase tracking-wide text-gray-400">
                  Frequent
                </div>
                {frequentGraphs.map((g) => (
                  <button
                    key={`freq-${g.graph_id}`}
                    onClick={() => handleSelect(g.graph_id)}
                    className="block w-full text-left px-3 py-1.5 text-xs text-gray-700 hover:bg-indigo-50 hover:text-indigo-700"
                  >
                    {g.name || g.graph_id}
                  </button>
                ))}
                <div className="mx-2 my-0.5 border-b border-gray-100" />
              </>
            )}

            {/* All / filtered results */}
            {!search && graphList.length > 0 && (
              <div className="px-3 pt-1.5 pb-0.5 text-[10px] uppercase tracking-wide text-gray-400">
                All templates
              </div>
            )}
            {filteredGraphs.length > 0 ? (
              filteredGraphs.map((g) => (
                <button
                  key={g.graph_id}
                  onClick={() => handleSelect(g.graph_id)}
                  className="block w-full text-left px-3 py-1.5 text-xs text-gray-700 hover:bg-indigo-50 hover:text-indigo-700"
                >
                  {g.name || g.graph_id}
                </button>
              ))
            ) : (
              <div className="px-3 py-2 text-xs text-gray-400">
                {search ? "No matching templates" : "No saved graphs"}
              </div>
            )}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

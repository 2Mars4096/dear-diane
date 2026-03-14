import { useState, useEffect, useRef, useCallback } from "react";
import {
  getKeybindings,
  getKeybindingForAction,
  setKeybinding,
  resetKeybinding,
  resetAllKeybindings,
  formatKey,
  getUserOverrides,
  type Keybinding,
} from "../../lib/keybindings";
import { RotateCcw } from "lucide-react";

function KeyCaptureInput({
  onCapture,
  onCancel,
}: {
  onCapture: (key: string) => void;
  onCancel: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    const handler = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();

      if (e.key === "Escape") {
        onCancel();
        return;
      }

      const parts: string[] = [];
      if (e.metaKey) parts.push("Cmd");
      if (e.ctrlKey) parts.push("Ctrl");
      if (e.altKey) parts.push("Alt");
      if (e.shiftKey) parts.push("Shift");

      if (!["Meta", "Control", "Alt", "Shift"].includes(e.key)) {
        parts.push(e.key.length === 1 ? e.key.toUpperCase() : e.key);
        onCapture(parts.join("+"));
      }
    };

    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [onCapture, onCancel]);

  return (
    <input
      ref={inputRef}
      readOnly
      placeholder="Press keys..."
      className="px-2 py-0.5 bg-blue-900/30 border border-blue-500 rounded text-xs text-blue-300 w-36 outline-none"
      onBlur={onCancel}
    />
  );
}

export default function KeybindingsPanel() {
  const [bindings] = useState<Keybinding[]>(() => getKeybindings());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [, forceUpdate] = useState(0);

  const grouped = bindings.reduce(
    (acc, kb) => {
      if (!acc[kb.category]) acc[kb.category] = [];
      acc[kb.category].push(kb);
      return acc;
    },
    {} as Record<string, Keybinding[]>,
  );

  const filteredGroups = filter
    ? Object.fromEntries(
        Object.entries(grouped)
          .map(([cat, items]) => [
            cat,
            items.filter((kb) =>
              kb.label.toLowerCase().includes(filter.toLowerCase()),
            ),
          ])
          .filter(([, items]) => (items as Keybinding[]).length > 0),
      )
    : grouped;

  const handleCapture = useCallback(
    (id: string, key: string) => {
      setKeybinding(id, key);
      setEditingId(null);
      forceUpdate((n) => n + 1);
    },
    [],
  );

  const handleReset = useCallback((id: string) => {
    resetKeybinding(id);
    forceUpdate((n) => n + 1);
  }, []);

  const handleResetAll = useCallback(() => {
    resetAllKeybindings();
    forceUpdate((n) => n + 1);
  }, []);

  const overrides = getUserOverrides();

  return (
    <div className="h-full overflow-y-auto bg-[#1e1e1e] text-gray-300">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-[#2d2d2d] bg-[#1e1e1e] px-6 py-3">
        <h2 className="text-base font-semibold text-white">Keyboard Shortcuts</h2>
        <button
          onClick={handleResetAll}
          className="flex items-center gap-1.5 rounded px-2.5 py-1 text-xs text-gray-400 hover:text-white hover:bg-[#3c3c3c] transition-colors"
          title="Reset all to defaults"
        >
          <RotateCcw size={13} />
          Reset All
        </button>
      </div>

      <div className="px-6 pb-8">
        <input
          type="text"
          placeholder="Search shortcuts..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full mt-4 mb-4 bg-[#3c3c3c] border border-[#555] rounded px-3 py-2 text-sm text-white placeholder-gray-500 outline-none focus:border-blue-500"
        />

        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-500 text-xs uppercase tracking-wider border-b border-gray-800">
              <th className="text-left py-2">Command</th>
              <th className="text-left py-2">Keybinding</th>
              <th className="py-2 w-8" />
            </tr>
          </thead>
          <tbody>
            {Object.entries(filteredGroups).map(([category, items]) => (
              <>
                <tr key={`cat-${category}`}>
                  <td
                    colSpan={3}
                    className="pt-4 pb-1 text-xs font-semibold text-gray-400 uppercase tracking-wider"
                  >
                    {category}
                  </td>
                </tr>
                {(items as Keybinding[]).map((kb) => {
                  const currentKey = getKeybindingForAction(kb.id) ?? kb.defaultKey;
                  const isOverridden = kb.id in overrides;

                  return (
                    <tr
                      key={kb.id}
                      className="border-b border-gray-800/50 hover:bg-gray-800/30"
                    >
                      <td className="py-2 text-gray-300">{kb.label}</td>
                      <td className="py-2">
                        {editingId === kb.id ? (
                          <KeyCaptureInput
                            onCapture={(key) => handleCapture(kb.id, key)}
                            onCancel={() => setEditingId(null)}
                          />
                        ) : (
                          <button
                            onClick={() => setEditingId(kb.id)}
                            className={`px-2 py-0.5 bg-[#3c3c3c] border rounded text-xs hover:border-gray-500 transition-colors ${
                              isOverridden
                                ? "border-blue-600 text-blue-300"
                                : "border-[#555] text-gray-300"
                            }`}
                          >
                            {formatKey(currentKey)}
                          </button>
                        )}
                      </td>
                      <td className="py-2 text-center">
                        {isOverridden && (
                          <button
                            onClick={() => handleReset(kb.id)}
                            className="text-gray-600 hover:text-gray-400 text-sm"
                            title="Reset to default"
                          >
                            ↩
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

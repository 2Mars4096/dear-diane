import { useEffect, useCallback } from "react";
import { useGraphStore } from "../store/useGraphStore";

interface ContextMenuProps {
  type: "canvas" | "node" | "edge";
  position: { x: number; y: number };
  targetId?: string;
  onClose: () => void;
}

const EDGE_TYPES = ["data", "control", "context"] as const;

export default function ContextMenu({ type, position, targetId, onClose }: ContextMenuProps) {
  const copySelected = useGraphStore((s) => s.copySelected);
  const pasteClipboard = useGraphStore((s) => s.pasteClipboard);
  const duplicateSelected = useGraphStore((s) => s.duplicateSelected);
  const deleteSelected = useGraphStore((s) => s.deleteSelected);
  const clipboard = useGraphStore((s) => s.clipboard);
  const onEdgesChange = useGraphStore((s) => s.onEdgesChange);
  const updateEdgeData = useGraphStore((s) => s.updateEdgeData);
  const pushSnapshot = useGraphStore((s) => s.pushSnapshot);
  const setSelectedNode = useGraphStore((s) => s.setSelectedNode);
  const setSelectedEdge = useGraphStore((s) => s.setSelectedEdge);

  const close = useCallback(() => onClose(), [onClose]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    const onClick = () => close();
    window.addEventListener("keydown", onKey);
    window.addEventListener("click", onClick, { capture: true });
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("click", onClick, { capture: true });
    };
  }, [close]);

  const action = (fn: () => void) => {
    fn();
    close();
  };

  const items: { label: string; shortcut?: string; disabled?: boolean; onClick: () => void }[] = [];

  if (type === "canvas") {
    items.push({
      label: "Paste",
      shortcut: "⌘V",
      disabled: clipboard.nodes.length === 0,
      onClick: () => action(() => pasteClipboard(position)),
    });
  }

  if (type === "node") {
    if (targetId) {
      setSelectedNode(targetId);
    }
    items.push(
      { label: "Copy", shortcut: "⌘C", onClick: () => action(copySelected) },
      { label: "Duplicate", shortcut: "⌘D", onClick: () => action(duplicateSelected) },
      { label: "Delete", shortcut: "⌫", onClick: () => action(deleteSelected) },
    );
  }

  if (type === "edge") {
    items.push({
      label: "Delete",
      onClick: () =>
        action(() => {
          if (!targetId) return;
          pushSnapshot();
          onEdgesChange([{ id: targetId, type: "remove" }]);
        }),
    });
    for (const et of EDGE_TYPES) {
      items.push({
        label: `Change to ${et.charAt(0).toUpperCase() + et.slice(1)}`,
        onClick: () =>
          action(() => {
            if (!targetId) return;
            updateEdgeData(targetId, { edge_type: et });
          }),
      });
    }
  }

  return (
    <div
      className="fixed z-50 min-w-[160px] rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
      style={{ left: position.x, top: position.y }}
      onContextMenu={(e) => e.preventDefault()}
    >
      {items.map((item) => (
        <button
          key={item.label}
          disabled={item.disabled}
          onClick={item.onClick}
          className="flex w-full items-center justify-between px-3 py-1.5 text-left text-sm
            text-gray-700 hover:bg-indigo-50 hover:text-indigo-700
            disabled:cursor-not-allowed disabled:text-gray-300 disabled:hover:bg-white"
        >
          <span>{item.label}</span>
          {item.shortcut && (
            <span className="ml-4 text-xs text-gray-400">{item.shortcut}</span>
          )}
        </button>
      ))}
    </div>
  );
}

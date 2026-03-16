import { useEffect } from "react";
import { useGraphStore } from "../store/useGraphStore";

export function useKeyboardShortcuts() {
  const saveGraph = useGraphStore((s) => s.saveGraph);
  const undo = useGraphStore((s) => s.undo);
  const redo = useGraphStore((s) => s.redo);
  const copySelected = useGraphStore((s) => s.copySelected);
  const pasteClipboard = useGraphStore((s) => s.pasteClipboard);
  const duplicateSelected = useGraphStore((s) => s.duplicateSelected);
  const commandPaletteOpen = useGraphStore((s) => s.commandPaletteOpen);
  const setCommandPaletteOpen = useGraphStore((s) => s.setCommandPaletteOpen);
  const groupIntoComposite = useGraphStore((s) => s.groupIntoComposite);

  useEffect(() => {
    const hasDocumentSelection = () => {
      const selection = window.getSelection();
      return Boolean(selection && !selection.isCollapsed && selection.toString().length > 0);
    };

    const isNativeClipboardZone = (target: EventTarget | null) => {
      const el = target instanceof Element ? target : null;
      if (!el) return false;
      return Boolean(
        el.closest("[data-native-clipboard]") ||
        el.closest("[data-chat-copy-zone]") ||
        el.closest("[data-mode-chat-copy-zone]"),
      );
    };

    const handler = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;

      const tag = (e.target as HTMLElement)?.tagName;
      const isTextInput = tag === "INPUT" || tag === "TEXTAREA" || (e.target as HTMLElement)?.isContentEditable;

      if (e.key === "k") {
        e.preventDefault();
        setCommandPaletteOpen(!commandPaletteOpen);
        return;
      } else if (e.key === "s") {
        e.preventDefault();
        saveGraph();
      } else if (e.key === "g" && e.shiftKey && !isTextInput) {
        e.preventDefault();
        groupIntoComposite();
        return;
      } else if (e.key === "z" && !e.shiftKey && !isTextInput) {
        e.preventDefault();
        undo();
      } else if (e.key === "z" && e.shiftKey && !isTextInput) {
        e.preventDefault();
        redo();
      } else if (e.key === "c" && !isTextInput) {
        if (hasDocumentSelection() || isNativeClipboardZone(e.target)) return;
        e.preventDefault();
        copySelected();
      } else if (e.key === "v" && !isTextInput) {
        if (isNativeClipboardZone(e.target)) return;
        e.preventDefault();
        pasteClipboard();
      } else if (e.key === "d" && !isTextInput) {
        e.preventDefault();
        duplicateSelected();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [saveGraph, undo, redo, copySelected, pasteClipboard, duplicateSelected, commandPaletteOpen, setCommandPaletteOpen, groupIntoComposite]);
}

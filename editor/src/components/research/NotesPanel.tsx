import { useState, useMemo, useCallback } from "react";
import { X, StickyNote, Search } from "lucide-react";
import {
  useResearchStore,
  type ResearchNote,
} from "../../store/useResearchStore";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function extractTags(text: string): string[] {
  const matches = text.match(/#\w+/g);
  return matches ? matches.map((t) => t.slice(1)) : [];
}

function formatRelativeTime(timestamp: number): string {
  const delta = Date.now() - timestamp;
  const seconds = Math.floor(delta / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

const SOURCE_ICONS: Record<string, string> = {
  pdf: "📄",
  chat: "💬",
  manual: "✏️",
};

// ---------------------------------------------------------------------------
// NoteItem
// ---------------------------------------------------------------------------

function NoteItem({
  note,
  onDelete,
}: {
  note: ResearchNote;
  onDelete: () => void;
}) {
  return (
    <div className="px-3 py-2 border-b border-gray-800/30 group hover:bg-gray-800/20">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-xs text-gray-300 whitespace-pre-wrap leading-relaxed">
            {note.content}
          </p>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            <span className="text-[9px] text-gray-600">
              {SOURCE_ICONS[note.sourceType ?? "manual"]}{" "}
              {formatRelativeTime(note.createdAt)}
            </span>
            {note.sourceRef && (
              <span className="text-[8px] text-gray-600 italic truncate max-w-[120px]">
                {note.sourceRef}
              </span>
            )}
            {note.tags.map((t) => (
              <span
                key={t}
                className="text-[8px] px-1 py-0.5 bg-gray-800 text-gray-500 rounded"
              >
                #{t}
              </span>
            ))}
          </div>
        </div>
        <button
          onClick={onDelete}
          className="p-0.5 text-gray-600 hover:text-red-400 opacity-0 group-hover:opacity-100 flex-shrink-0"
        >
          <X size={12} />
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// NotesPanel (default export)
// ---------------------------------------------------------------------------

export default function NotesPanel() {
  const notes = useResearchStore((s) => s.notes);
  const addNote = useResearchStore((s) => s.addNote);
  const removeNote = useResearchStore((s) => s.removeNote);

  const [newNote, setNewNote] = useState("");
  const [tagFilter, setTagFilter] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [showSearch, setShowSearch] = useState(false);

  const allTags = useMemo(() => {
    const tags = new Set<string>();
    notes.forEach((n) => n.tags.forEach((t) => tags.add(t)));
    return Array.from(tags).sort();
  }, [notes]);

  const filtered = useMemo(() => {
    let result = notes;
    if (tagFilter) {
      result = result.filter((n) => n.tags.includes(tagFilter));
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter((n) =>
        n.content.toLowerCase().includes(q),
      );
    }
    return [...result].sort((a, b) => b.createdAt - a.createdAt);
  }, [notes, tagFilter, searchQuery]);

  const handleSubmit = useCallback(() => {
    if (!newNote.trim()) return;
    const tags = extractTags(newNote);
    addNote({ content: newNote.trim(), tags, sourceType: "manual" });
    setNewNote("");
  }, [newNote, addNote]);

  return (
    <div className="h-full flex flex-col">
      {/* New note input */}
      <div className="px-3 py-2 border-b border-gray-800">
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-1.5">
            <StickyNote size={13} className="text-gray-500" />
            <span className="text-xs font-semibold text-gray-300">Notes</span>
            <span className="text-[9px] text-gray-600">
              ({notes.length})
            </span>
          </div>
          <button
            onClick={() => setShowSearch((s) => !s)}
            className={`p-1 ${
              showSearch
                ? "text-blue-400"
                : "text-gray-500 hover:text-gray-300"
            }`}
          >
            <Search size={13} />
          </button>
        </div>

        {showSearch && (
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search notes..."
            className="w-full mb-1.5 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-[11px] text-gray-200 placeholder-gray-600"
            autoFocus
          />
        )}

        <textarea
          value={newNote}
          onChange={(e) => setNewNote(e.target.value)}
          placeholder="Quick note... (Ctrl+Enter to save, use #tag for tags)"
          className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 placeholder-gray-500 resize-none h-[60px] focus:border-gray-600 focus:outline-none"
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              handleSubmit();
            }
          }}
        />

        {allTags.length > 0 && (
          <div className="flex gap-1 mt-1.5 flex-wrap">
            <button
              onClick={() => setTagFilter(null)}
              className={`text-[9px] px-1.5 py-0.5 rounded ${
                !tagFilter
                  ? "bg-blue-600/30 text-blue-300"
                  : "bg-gray-800 text-gray-500 hover:text-gray-300"
              }`}
            >
              All
            </button>
            {allTags.map((tag) => (
              <button
                key={tag}
                onClick={() =>
                  setTagFilter(tagFilter === tag ? null : tag)
                }
                className={`text-[9px] px-1.5 py-0.5 rounded ${
                  tagFilter === tag
                    ? "bg-blue-600/30 text-blue-300"
                    : "bg-gray-800 text-gray-500 hover:text-gray-300"
                }`}
              >
                #{tag}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Notes list */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length > 0 ? (
          filtered.map((note) => (
            <NoteItem
              key={note.id}
              note={note}
              onDelete={() => removeNote(note.id)}
            />
          ))
        ) : (
          <EmptyNotes hasNotes={notes.length > 0} />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyNotes({ hasNotes }: { hasNotes: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center h-full p-4 text-center gap-2">
      <StickyNote size={28} className="text-gray-700" />
      <p className="text-xs text-gray-600">
        {hasNotes
          ? "No notes match the current filter."
          : "No notes yet. Type above and press Ctrl+Enter to save."}
      </p>
      {!hasNotes && (
        <p className="text-[10px] text-gray-700">
          Use <code className="text-gray-500">#tags</code> to organize
          notes. Capture from PDFs or chat with quick actions.
        </p>
      )}
    </div>
  );
}

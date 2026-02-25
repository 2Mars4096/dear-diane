export interface MentionRef {
  name: string;
  type: "node" | "workflow" | "subgraph";
  id: string;
}

export type MentionSegment =
  | { type: "text"; content: string }
  | { type: "mention"; mention: MentionRef };

const MENTION_RE = /@\[([^\]]+)\]\((node|workflow|subgraph):([^)]+)\)/g;

export function serializeMention(mention: MentionRef): string {
  return `@[${mention.name}](${mention.type}:${mention.id})`;
}

export function parseMentions(text: string): { segments: MentionSegment[] } {
  const segments: MentionSegment[] = [];
  let lastIndex = 0;

  for (const match of text.matchAll(MENTION_RE)) {
    const start = match.index!;
    if (start > lastIndex) {
      segments.push({ type: "text", content: text.slice(lastIndex, start) });
    }
    segments.push({
      type: "mention",
      mention: {
        name: match[1],
        type: match[2] as MentionRef["type"],
        id: match[3],
      },
    });
    lastIndex = start + match[0].length;
  }

  if (lastIndex < text.length) {
    segments.push({ type: "text", content: text.slice(lastIndex) });
  }

  return { segments };
}

export function findMentionQuery(
  text: string,
  cursorPos: number,
): { query: string; atPos: number } | null {
  const before = text.slice(0, cursorPos);
  const atIdx = before.lastIndexOf("@");
  if (atIdx === -1) return null;

  const fragment = before.slice(atIdx + 1);
  if (/\s/.test(fragment)) return null;

  // Don't trigger inside an already-completed mention: @[...](...) 
  if (atIdx > 0 || fragment.startsWith("[")) {
    const completed = before.slice(atIdx);
    if (/^@\[[^\]]*\]\([^)]*\)/.test(completed)) return null;
  }

  return { query: fragment, atPos: atIdx };
}

export function insertMention(
  text: string,
  cursorPos: number,
  mention: MentionRef,
): { newText: string; newCursorPos: number } {
  const result = findMentionQuery(text, cursorPos);
  if (!result) return { newText: text, newCursorPos: cursorPos };

  const serialized = serializeMention(mention) + " ";
  const newText =
    text.slice(0, result.atPos) + serialized + text.slice(cursorPos);
  return { newText, newCursorPos: result.atPos + serialized.length };
}

// ---------------------------------------------------------------------------
// Co-navigation
// ---------------------------------------------------------------------------

export function navigateToMention(
  mention: MentionRef,
  store: {
    setSelectedNode: (id: string | null) => void;
    drillIn: (nodeId: string) => void;
    danGraph: unknown;
    layerStack: unknown[];
  },
): void {
  switch (mention.type) {
    case "node":
      store.setSelectedNode(mention.id);
      break;
    case "subgraph":
      store.drillIn(mention.id);
      break;
    case "workflow":
      break;
  }
}

export function mentionTypeColor(
  type: "node" | "workflow" | "subgraph",
): string {
  switch (type) {
    case "node":
      return "bg-blue-100 text-blue-700";
    case "workflow":
      return "bg-green-100 text-green-700";
    case "subgraph":
      return "bg-amber-100 text-amber-700";
  }
}

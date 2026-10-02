import { cleanRect, type PdfSelectionAnchor } from "./paper-comments";
export type PaperReferenceLine = {
  label: string;
  lineId: string;
};

export type PaperReferenceTag = {
  selection?: PdfSelectionAnchor;
  createdAt: string;
  label: string;
  lineId: string;
  materialId: string;
  pageNumber: number;
  tagId: string;
};

export type PaperReferenceTray = {
  lines: PaperReferenceLine[];
  tags: PaperReferenceTag[];
  version: 2;
};

export const MAX_PAPER_REFERENCE_LINES = 4;
export const MAX_PAPER_REFERENCE_TAGS = 8;
export const DEFAULT_PAPER_REFERENCE_LINE_ID = "saved";

export function paperReferenceTagsStorageKey(materialId: string): string {
  return `dan.reader:paper-reference-tags:v1:${materialId}`;
}

export function paperReferenceTrayStorageKey(materialId: string): string {
  return `dan.reader:paper-reference-tray:v2:${materialId}`;
}

function cleanDate(value: unknown): string | null {
  if (typeof value !== "string" || Number.isNaN(Date.parse(value))) {
    return null;
  }
  return new Date(value).toISOString();
}

function cleanLine(value: unknown): PaperReferenceLine | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const lineId = typeof record.lineId === "string"
    ? record.lineId.trim().slice(0, 100)
    : "";
  const label = typeof record.label === "string"
    ? record.label.replace(/\s+/g, " ").trim().slice(0, 40)
    : "";
  return lineId ? { label: label || "Untitled line", lineId } : null;
}

export function normalizePaperReferenceTags(
  value: unknown,
  materialId: string,
  pageCount = Number.POSITIVE_INFINITY
): PaperReferenceTag[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const byPage = new Map<string, PaperReferenceTag>();
  value.forEach((entry) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      return;
    }
    const record = entry as Record<string, unknown>;
    const pageNumber = typeof record.pageNumber === "number" &&
      Number.isInteger(record.pageNumber) && record.pageNumber > 0 &&
      record.pageNumber <= pageCount
      ? record.pageNumber
      : 0;
    const tagId = typeof record.tagId === "string"
      ? record.tagId.trim().slice(0, 100)
      : "";
    const label = typeof record.label === "string"
      ? record.label.replace(/\s+/g, " ").trim().slice(0, 80)
      : "";
    const lineId = typeof record.lineId === "string" && record.lineId.trim()
      ? record.lineId.trim().slice(0, 100)
      : DEFAULT_PAPER_REFERENCE_LINE_ID;
    const createdAt = cleanDate(record.createdAt);
    if (!pageNumber || !tagId || !createdAt) {
      return;
    }
    const raw = record.selection as Partial<PdfSelectionAnchor> | undefined;
    const rects = Array.isArray(raw?.rects) ? raw.rects.map(cleanRect).filter((r): r is NonNullable<typeof r> => !!r).slice(0, 40) : [];
    const selection: PdfSelectionAnchor | undefined = rects.length ? {
      ...(raw?.quoteSource === 'vision' ? { quoteSource: 'vision' as const, quoteModel: raw.quoteModel, originalQuote: raw.originalQuote } : {}),
      pageNumber, rects, quote: typeof raw?.quote === "string" ? raw.quote.slice(0, 700) : "Selected area",
      kind: raw?.kind === "area" ? "area" : "text",
      rotation: [0, 90, 180, 270].includes(raw?.rotation ?? -1) ? raw!.rotation! : 0,
    } : undefined;
    byPage.set(selection ? tagId : `page-${pageNumber}`, {
      ...(selection ? { selection } : {}),
      createdAt,
      label: label || `Page ${pageNumber}`,
      lineId,
      materialId,
      pageNumber,
      tagId
    });
  });

  return [...byPage.values()].slice(-MAX_PAPER_REFERENCE_TAGS);
}

export function normalizePaperReferenceTray(
  value: unknown,
  materialId: string,
  pageCount = Number.POSITIVE_INFINITY
): PaperReferenceTray {
  const record = value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
  const rawLines = record && Array.isArray(record.lines) ? record.lines : [];
  const linesById = new Map<string, PaperReferenceLine>();
  rawLines.forEach((entry) => {
    const line = cleanLine(entry);
    if (line && !linesById.has(line.lineId) && linesById.size < MAX_PAPER_REFERENCE_LINES) {
      linesById.set(line.lineId, line);
    }
  });
  if (linesById.size === 0) {
    linesById.set(DEFAULT_PAPER_REFERENCE_LINE_ID, {
      label: "Saved",
      lineId: DEFAULT_PAPER_REFERENCE_LINE_ID
    });
  }
  const lines = [...linesById.values()];
  const rawTags = Array.isArray(value) ? value : record?.tags;
  const tags = normalizePaperReferenceTags(rawTags, materialId, pageCount).map((tag) => ({
    ...tag,
    lineId: linesById.has(tag.lineId) ? tag.lineId : lines[0].lineId
  }));
  return { lines, tags, version: 2 };
}

export function movePaperReferenceTag(
  tags: PaperReferenceTag[],
  tagId: string,
  targetLineId: string,
  beforeTagId: string | null
): PaperReferenceTag[] {
  const moving = tags.find((tag) => tag.tagId === tagId);
  if (!moving || beforeTagId === tagId) return tags;
  const remaining = tags.filter((tag) => tag.tagId !== tagId);
  const moved = { ...moving, lineId: targetLineId };
  const beforeIndex = beforeTagId
    ? remaining.findIndex((tag) => tag.tagId === beforeTagId)
    : -1;
  if (beforeIndex < 0) {
    const lastTargetIndex = remaining.reduce(
      (last, tag, index) => tag.lineId === targetLineId ? index : last,
      -1
    );
    remaining.splice(lastTargetIndex + 1, 0, moved);
    return remaining;
  }
  remaining.splice(beforeIndex, 0, moved);
  return remaining;
}

export function readPaperReferenceTray(
  materialId: string,
  pageCount = Number.POSITIVE_INFINITY
): PaperReferenceTray {
  try {
    const storage = (globalThis as typeof globalThis & {
      localStorage?: { getItem(key: string): string | null };
    }).localStorage;
    const current = storage?.getItem(paperReferenceTrayStorageKey(materialId));
    if (current) {
      return normalizePaperReferenceTray(JSON.parse(current), materialId, pageCount);
    }
    const legacy = storage?.getItem(paperReferenceTagsStorageKey(materialId));
    return legacy
      ? normalizePaperReferenceTray(JSON.parse(legacy), materialId, pageCount)
      : normalizePaperReferenceTray(null, materialId, pageCount);
  } catch {
    return normalizePaperReferenceTray(null, materialId, pageCount);
  }
}

export function writePaperReferenceTray(
  materialId: string,
  tray: PaperReferenceTray,
  pageCount = Number.POSITIVE_INFINITY
): void {
  const storage = (globalThis as typeof globalThis & {
    localStorage?: { setItem(key: string, value: string): void };
  }).localStorage;
  storage?.setItem(
    paperReferenceTrayStorageKey(materialId),
    JSON.stringify(normalizePaperReferenceTray(tray, materialId, pageCount))
  );
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("dan:reader-progress", { detail: materialId }));
}

export function readPaperReferenceTags(
  materialId: string,
  pageCount = Number.POSITIVE_INFINITY
): PaperReferenceTag[] {
  return readPaperReferenceTray(materialId, pageCount).tags;
}

export function writePaperReferenceTags(
  materialId: string,
  tags: PaperReferenceTag[],
  pageCount = Number.POSITIVE_INFINITY
): void {
  writePaperReferenceTray(
    materialId,
    normalizePaperReferenceTray(tags, materialId, pageCount),
    pageCount
  );
}

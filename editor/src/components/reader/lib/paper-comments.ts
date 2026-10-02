import {
  paperHistoryIdentity,
  paperHistoryStorage,
  type PaperHistoryMaterial,
  type PaperHistoryStorage
} from "./paper-history";

export type PdfSelectionRect = {
  height: number;
  left: number;
  top: number;
  width: number;
};

export type PdfSelectionAnchor = {
  kind?: "text" | "area";
  pageNumber: number;
  quote: string;
  rects: PdfSelectionRect[];
  rotation: 0 | 90 | 180 | 270;
};

/** Diane: text anchor so a highlight can be re-found when the PDF or its layout changes. */
export type PaperCommentAnchor = {
  end: number;
  fingerprint: string;
  pageFingerprint: string;
  prefix: string;
  quote: string;
  scope?: "block" | "passage";
  start: number;
  suffix: string;
};

export type PaperComment = PdfSelectionAnchor & {
  anchor?: PaperCommentAnchor | null;
  commentId: string;
  createdAt: string;
  materialId: string;
  text: string;
  updatedAt: string;
};

function cleanAnchor(value: unknown): PaperCommentAnchor | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const text = (key: string, limit: number) => (typeof record[key] === "string" ? (record[key] as string).slice(0, limit) : null);
  const quote = text("quote", 700); const prefix = text("prefix", 64); const suffix = text("suffix", 64);
  const fingerprint = text("fingerprint", 80); const pageFingerprint = text("pageFingerprint", 80);
  if (!quote || prefix === null || suffix === null || !fingerprint || !pageFingerprint) return null;
  if (!Number.isInteger(record.start) || !Number.isInteger(record.end) || (record.start as number) < 0 || (record.end as number) <= (record.start as number)) return null;
  return { end: record.end as number, fingerprint, pageFingerprint, prefix, quote, start: record.start as number, suffix,
    ...(record.scope === "block" ? { scope: "block" as const } : {}) };
}

const MAX_COMMENTS = 200;
const MAX_COMMENT_LENGTH = 4_000;
const MAX_RECTS = 40;

export function paperCommentsStorageKey(materialId: string): string {
  return `dan.reader:paper-comments:v1:${materialId}`;
}

export function sharedPaperCommentsStorageKey(material: PaperHistoryMaterial): string {
  return `dan.reader:paper-comments:v2:${paperHistoryIdentity(material)}`;
}

function cleanUnit(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  return Math.min(1, Math.max(0, value));
}

export function cleanRect(value: unknown): PdfSelectionRect | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const left = cleanUnit(record.left);
  const top = cleanUnit(record.top);
  const width = cleanUnit(record.width);
  const height = cleanUnit(record.height);
  if (left === null || top === null || width === null || height === null ||
    width <= 0 || height <= 0) {
    return null;
  }
  const cleaned = {
    height: Math.min(height, 1 - top),
    left,
    top,
    width: Math.min(width, 1 - left)
  };
  return cleaned.left <= 0.002 && cleaned.width < 0.015 && cleaned.height > 0.03
    ? null
    : cleaned;
}

function cleanDate(value: unknown): string | null {
  if (typeof value !== "string" || Number.isNaN(Date.parse(value))) {
    return null;
  }
  return new Date(value).toISOString();
}

export function normalizePaperComments(
  value: unknown,
  materialId: string
): PaperComment[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((entry): PaperComment[] => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      return [];
    }
    const record = entry as Record<string, unknown>;
    const commentId = typeof record.commentId === "string"
      ? record.commentId.trim().slice(0, 100)
      : "";
    const quote = typeof record.quote === "string" ? record.quote.trim().slice(0, 700) : "";
    const text = typeof record.text === "string"
      ? record.text.trim().slice(0, MAX_COMMENT_LENGTH)
      : "";
    const pageNumber = typeof record.pageNumber === "number" &&
      Number.isInteger(record.pageNumber) && record.pageNumber > 0
      ? record.pageNumber
      : 0;
    const rotation = [0, 90, 180, 270].includes(record.rotation as number)
      ? record.rotation as PdfSelectionAnchor["rotation"]
      : 0;
    const rects = Array.isArray(record.rects)
      ? record.rects.map(cleanRect).filter((rect): rect is PdfSelectionRect => rect !== null).slice(0, MAX_RECTS)
      : [];
    const createdAt = cleanDate(record.createdAt);
    const updatedAt = cleanDate(record.updatedAt);
    if (!commentId || !quote || !pageNumber || (!rects.length && !cleanAnchor(record.anchor)) || !createdAt || !updatedAt) {
      return [];
    }
    const anchor = cleanAnchor(record.anchor);
    return [{
      ...(anchor ? { anchor } : {}),
      ...(record.kind === "area" ? { kind: "area" as const } : {}),
      commentId,
      createdAt,
      materialId,
      pageNumber,
      quote,
      rects,
      rotation,
      text,
      updatedAt
    }];
  }).slice(-MAX_COMMENTS);
}

function mergePaperComments(
  values: PaperComment[][],
  materialId: string
): PaperComment[] {
  const commentsById = new Map<string, PaperComment>();
  values.flat().forEach((comment) => {
    const current = commentsById.get(comment.commentId);
    if (!current || Date.parse(comment.updatedAt) >= Date.parse(current.updatedAt)) {
      commentsById.set(comment.commentId, { ...comment, materialId });
    }
  });
  return [...commentsById.values()]
    .sort((left, right) => Date.parse(left.createdAt) - Date.parse(right.createdAt))
    .slice(-MAX_COMMENTS);
}

function parsePaperComments(raw: string | null, materialId: string): PaperComment[] {
  if (!raw) return [];
  try {
    return normalizePaperComments(JSON.parse(raw), materialId);
  } catch {
    return [];
  }
}

export function readPaperComments(
  material: PaperHistoryMaterial,
  providedStorage?: PaperHistoryStorage,
  legacyMaterialIds: string[] = [material.material_id]
): PaperComment[] {
  try {
    const storage = providedStorage ?? paperHistoryStorage();
    if (!storage) return [];
    const sharedKey = sharedPaperCommentsStorageKey(material);
    const sharedRaw = storage.getItem(sharedKey);
    const legacyKeys = [...new Set([material.material_id, ...legacyMaterialIds])]
      .map(paperCommentsStorageKey);
    const legacyValues = legacyKeys.map((key) => storage.getItem(key));
    const comments = mergePaperComments([
      parsePaperComments(sharedRaw, material.material_id),
      ...legacyValues.map((raw) => parsePaperComments(raw, material.material_id))
    ], material.material_id);
    if (legacyValues.some(Boolean)) {
      storage.setItem(sharedKey, JSON.stringify(comments));
      legacyKeys.forEach((key) => storage.removeItem?.(key));
    }
    return comments;
  } catch {
    return [];
  }
}

export function writePaperComments(
  material: PaperHistoryMaterial,
  comments: PaperComment[],
  providedStorage?: PaperHistoryStorage
): void {
  const storage = providedStorage ?? paperHistoryStorage();
  storage?.setItem(
    sharedPaperCommentsStorageKey(material),
    JSON.stringify(normalizePaperComments(comments, material.material_id))
  );
}

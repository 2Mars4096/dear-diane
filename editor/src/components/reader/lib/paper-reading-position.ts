// Ported from learning-assistant lib/paper-reading-continuity.ts (PDF position only).
import { paperHistoryStorage } from "./paper-history";

export type PaperPdfPosition = {
  page: number;
  top: number;
  left: number;
  zoom: number;
};
const positionKey = (identity: string) => `dan.reader:pdf-position:v1:${identity}`;

export function readPaperPdfPosition(identity: string, storage = paperHistoryStorage()): PaperPdfPosition | null {
  try {
    const value = JSON.parse(storage?.getItem(positionKey(identity)) ?? "null");
    return value && Number.isInteger(value.page) && value.page > 0 &&
      Number.isFinite(value.top) && Math.abs(value.top) <= 2 &&
      Number.isFinite(value.left) && value.left >= 0 && value.left <= 1 &&
      Number.isFinite(value.zoom) && value.zoom >= 0.7 && value.zoom <= 4 ? value : null;
  } catch { return null; }
}

export function clearPaperPdfPosition(identity: string) {
  try { paperHistoryStorage()?.removeItem?.(positionKey(identity)); } catch { /* Best effort. */ }
}

export function writePaperPdfPosition(identity: string, value: PaperPdfPosition, storage = paperHistoryStorage()) {
  try { storage?.setItem(positionKey(identity), JSON.stringify(value)); } catch { /* Reading still works without storage. */ }
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("dan:reader-progress", { detail: identity }));
}

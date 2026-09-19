import type { PdfSelectionRect } from "./paper-comments";

const EDGE_SHADE_LEFT_LIMIT = 0.002;
const EDGE_SHADE_WIDTH_LIMIT = 0.015;
const EDGE_SHADE_HEIGHT_FLOOR = 0.03;
const SAME_LINE_GAP_LIMIT = 0.003;
const SAME_LINE_OVERLAP_FLOOR = 0.6;

export function isPaperHighlightEdgeShadeArtifact(rect: PdfSelectionRect): boolean {
  return rect.left <= EDGE_SHADE_LEFT_LIMIT &&
    rect.width < EDGE_SHADE_WIDTH_LIMIT &&
    rect.height > EDGE_SHADE_HEIGHT_FLOOR;
}

function bottom(rect: PdfSelectionRect): number {
  return rect.top + rect.height;
}

function right(rect: PdfSelectionRect): number {
  return rect.left + rect.width;
}

function canMergeDisplayRects(left: PdfSelectionRect, rightRect: PdfSelectionRect): boolean {
  const verticalOverlap = Math.max(
    0,
    Math.min(bottom(left), bottom(rightRect)) - Math.max(left.top, rightRect.top)
  );
  const smallerHeight = Math.min(left.height, rightRect.height);
  const horizontalGap = Math.max(
    0,
    Math.max(left.left, rightRect.left) - Math.min(right(left), right(rightRect))
  );
  return smallerHeight > 0 &&
    verticalOverlap / smallerHeight >= SAME_LINE_OVERLAP_FLOOR &&
    horizontalGap <= SAME_LINE_GAP_LIMIT;
}

function mergeDisplayRects(left: PdfSelectionRect, rightRect: PdfSelectionRect): PdfSelectionRect {
  const mergedLeft = Math.min(left.left, rightRect.left);
  const mergedTop = Math.min(left.top, rightRect.top);
  return {
    height: Math.max(bottom(left), bottom(rightRect)) - mergedTop,
    left: mergedLeft,
    top: mergedTop,
    width: Math.max(right(left), right(rightRect)) - mergedLeft
  };
}

export function paperHighlightDisplayRects(rects: PdfSelectionRect[]): PdfSelectionRect[] {
  const pending = rects
    .filter((rect) => !isPaperHighlightEdgeShadeArtifact(rect))
    .map((rect) => ({ ...rect }))
    .sort((left, rightRect) => left.top - rightRect.top || left.left - rightRect.left);
  const merged: PdfSelectionRect[] = [];

  pending.forEach((rect) => {
    let candidate = rect;
    let mergedAgain = true;
    while (mergedAgain) {
      mergedAgain = false;
      for (let index = 0; index < merged.length; index += 1) {
        const current = merged[index];
        if (current && canMergeDisplayRects(current, candidate)) {
          candidate = mergeDisplayRects(current, candidate);
          merged.splice(index, 1);
          mergedAgain = true;
          break;
        }
      }
    }
    merged.push(candidate);
  });

  return merged.sort(
    (left, rightRect) => left.top - rightRect.top || left.left - rightRect.left
  );
}

function pathNumber(value: number): string {
  return (value * 100).toFixed(4).replace(/\.?0+$/, "");
}

export function paperHighlightDisplayPath(rects: PdfSelectionRect[]): string {
  return paperHighlightDisplayRects(rects).map((rect) => {
    const left = pathNumber(rect.left);
    const top = pathNumber(rect.top);
    const rectRight = pathNumber(right(rect));
    const rectBottom = pathNumber(bottom(rect));
    return `M${left} ${top}H${rectRight}V${rectBottom}H${left}Z`;
  }).join(" ");
}

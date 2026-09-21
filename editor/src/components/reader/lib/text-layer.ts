// Locate a quote inside a rendered page's text layer and return page-relative rects,
// so an anchored highlight can be redrawn after the PDF or layout changes.
import type { PdfSelectionRect } from "./paper-comments";
import { isPaperHighlightEdgeShadeArtifact } from "./paper-highlight-display";

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

/** Text of a rendered page as the browser would select it. */
export function textLayerText(pageElement: HTMLElement): string {
  const layer = pageElement.querySelector<HTMLElement>("[class*='textLayer']");
  return (layer?.textContent ?? "").replace(/\s+/g, " ").trim();
}

export function rectsForQuote(pageElement: HTMLElement, quote: string): PdfSelectionRect[] {
  const layer = pageElement.querySelector<HTMLElement>("[class*='textLayer']");
  if (!layer) return [];
  const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  const compact: string[] = [];
  const positions: Array<{ node: Text; offset: number }> = [];
  for (let node = walker.nextNode() as Text | null; node; node = walker.nextNode() as Text | null) {
    nodes.push(node);
    const text = node.data;
    for (let index = 0; index < text.length; index += 1) {
      if (/\s/u.test(text[index])) continue;
      compact.push(text[index]);
      positions.push({ node, offset: index });
    }
  }
  const needle = quote.replace(/\s+/gu, "");
  if (needle.length < 2 || !nodes.length) return [];
  const haystack = compact.join("");
  const first = haystack.indexOf(needle);
  if (first < 0 || haystack.indexOf(needle, first + 1) >= 0) return [];  // must be unique on the page
  const start = positions[first];
  const last = positions[first + needle.length - 1];
  const range = document.createRange();
  range.setStart(start.node, start.offset);
  range.setEnd(last.node, last.offset + 1);
  const pageRect = pageElement.getBoundingClientRect();
  if (pageRect.width <= 0 || pageRect.height <= 0) return [];
  return Array.from(range.getClientRects()).flatMap((rect) => {
    const left = clamp((rect.left - pageRect.left) / pageRect.width, 0, 1);
    const top = clamp((rect.top - pageRect.top) / pageRect.height, 0, 1);
    const right = clamp((rect.right - pageRect.left) / pageRect.width, 0, 1);
    const bottom = clamp((rect.bottom - pageRect.top) / pageRect.height, 0, 1);
    if (right <= left || bottom <= top) return [];
    const anchor = { height: bottom - top, left, top, width: right - left };
    return isPaperHighlightEdgeShadeArtifact(anchor) ? [] : [anchor];
  }).slice(0, 40);
}

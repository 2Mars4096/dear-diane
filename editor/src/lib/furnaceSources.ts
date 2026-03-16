export interface ParsedFurnaceSources {
  source_ids: string[];
  pdf_paths: string[];
  urls: string[];
}

function looksLikeUrl(value: string): boolean {
  const lower = value.toLowerCase();
  return lower.startsWith("http://") || lower.startsWith("https://");
}

function looksLikePath(value: string): boolean {
  return (
    value.startsWith("/") ||
    value.startsWith("~/") ||
    value.startsWith("./") ||
    value.startsWith("../") ||
    value.includes("/")
  );
}

function explodeSourceEntry(value: string): string[] {
  const text = String(value || "").trim();
  if (!text) return [];

  const urlMatches = text.match(/https?:\/\/\S+/g);
  if (urlMatches && urlMatches.length > 1) return urlMatches;

  // Handles accidentally pasted absolute paths on one line:
  // "/Users/.../a.pdf /Users/.../b.pdf"
  const pathMatches = text.match(/(?:~\/|\/)\S+/g);
  if (pathMatches && pathMatches.length > 1) return pathMatches;

  const segments = text.split(/\s+/g).filter(Boolean);
  if (
    segments.length > 1
    && segments.every((s) => looksLikeUrl(s) || looksLikePath(s))
  ) {
    return segments;
  }

  return [text];
}

export function parseFurnaceSources(inputs: string[]): ParsedFurnaceSources {
  const sourceIds: string[] = [];
  const pdfPaths: string[] = [];
  const urls: string[] = [];

  for (const raw of inputs) {
    for (const text of explodeSourceEntry(raw)) {
      const lower = text.toLowerCase();
      if (looksLikeUrl(text)) {
        urls.push(text);
        continue;
      }
      if (lower.endsWith(".pdf") || looksLikePath(text)) {
        pdfPaths.push(text);
        continue;
      }
      sourceIds.push(text);
    }
  }

  return {
    source_ids: Array.from(new Set(sourceIds)),
    pdf_paths: Array.from(new Set(pdfPaths)),
    urls: Array.from(new Set(urls)),
  };
}

export function splitSourceTextBlock(value: string): string[] {
  const tokens = value
    .split(/[\n,;\t]+/g)
    .map((v) => v.trim())
    .filter(Boolean);
  return tokens.flatMap(explodeSourceEntry);
}

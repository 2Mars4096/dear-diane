export interface ClipboardCodeContext {
  filePath: string;
  startLine: number;
  endLine: number;
  lang: string;
  code: string;
}

const DAN_FILE_ATTR_RE = /data-dan-file="([^"]*)"/;
const DAN_START_ATTR_RE = /data-dan-start="(\d+)"/;
const DAN_END_ATTR_RE = /data-dan-end="(\d+)"/;
const DAN_LANG_ATTR_RE = /data-dan-lang="([^"]*)"/;
const PRE_CODE_RE = /<code>([\s\S]*?)<\/code>/;

// Plain-text fallback is intentionally strict: it should only match paths that
// look like real file paths, not arbitrary comment text.
const PLAIN_HEADER_RE =
  /^\/\/\s*((?:[A-Za-z]:)?(?:[^:\n]*[\\/])[^:\n]+\.[A-Za-z0-9]+):L(\d+)(?:-L(\d+))?\s*\n/;
const RECENT_COPY_TTL_MS = 5 * 60_000;

let lastCopiedCodeContext:
  | {
      plainText: string;
      context: ClipboardCodeContext;
      copiedAt: number;
    }
  | null = null;

function decodeHtmlEntities(value: string): string {
  return value
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&");
}

export function parseClipboardCodeContext(
  plainText: string,
  htmlText?: string,
): ClipboardCodeContext | null {
  if (htmlText) {
    const fileMatch = DAN_FILE_ATTR_RE.exec(htmlText);
    const startMatch = DAN_START_ATTR_RE.exec(htmlText);
    if (fileMatch && startMatch) {
      const endMatch = DAN_END_ATTR_RE.exec(htmlText);
      const langMatch = DAN_LANG_ATTR_RE.exec(htmlText);
      const codeMatch = PRE_CODE_RE.exec(htmlText);
      const rawCode = codeMatch
        ? decodeHtmlEntities(codeMatch[1])
        : plainText.replace(PLAIN_HEADER_RE, "");
      return {
        filePath: decodeHtmlEntities(fileMatch[1]),
        startLine: parseInt(startMatch[1], 10),
        endLine: endMatch ? parseInt(endMatch[1], 10) : parseInt(startMatch[1], 10),
        lang: langMatch?.[1] ?? "",
        code: rawCode,
      };
    }
  }

  const headerMatch = PLAIN_HEADER_RE.exec(plainText);
  if (headerMatch) {
    const filePath = headerMatch[1];
    const startLine = parseInt(headerMatch[2], 10);
    const endLine = headerMatch[3] ? parseInt(headerMatch[3], 10) : startLine;
    const code = plainText.slice(headerMatch[0].length);
    const ext = filePath.split(".").pop() ?? "";
    return { filePath, startLine, endLine, lang: ext, code };
  }

  return null;
}

export function rememberCopiedCodeContext(
  plainText: string,
  context: ClipboardCodeContext,
): void {
  lastCopiedCodeContext = {
    plainText,
    context,
    copiedAt: Date.now(),
  };
}

export function resolveClipboardCodeContext(
  plainText: string,
  htmlText?: string,
): ClipboardCodeContext | null {
  const parsed = parseClipboardCodeContext(plainText, htmlText);
  if (parsed) {
    return parsed;
  }

  if (
    lastCopiedCodeContext &&
    Date.now() - lastCopiedCodeContext.copiedAt <= RECENT_COPY_TTL_MS &&
    lastCopiedCodeContext.plainText === plainText
  ) {
    return lastCopiedCodeContext.context;
  }

  return null;
}

export function formatCodeContextForChat(ctx: ClipboardCodeContext): string {
  const lineRange =
    ctx.startLine === ctx.endLine
      ? `L${ctx.startLine}`
      : `L${ctx.startLine}-L${ctx.endLine}`;
  const lang = ctx.lang || ctx.filePath.split(".").pop() || "";
  return `\`${ctx.filePath}:${lineRange}\`\n\`\`\`${lang}\n${ctx.code}\n\`\`\``;
}

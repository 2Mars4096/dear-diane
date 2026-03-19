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

const PLAIN_HEADER_RE = /^\/\/\s*(\S+\.\w+):L(\d+)(?:-L(\d+))?\s*\n/;

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
        ? codeMatch[1]
            .replace(/&lt;/g, "<")
            .replace(/&gt;/g, ">")
            .replace(/&quot;/g, '"')
            .replace(/&amp;/g, "&")
        : plainText.replace(PLAIN_HEADER_RE, "");
      return {
        filePath: fileMatch[1].replace(/&quot;/g, '"'),
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

export function formatCodeContextForChat(ctx: ClipboardCodeContext): string {
  const lineRange =
    ctx.startLine === ctx.endLine
      ? `L${ctx.startLine}`
      : `L${ctx.startLine}-L${ctx.endLine}`;
  const lang = ctx.lang || ctx.filePath.split(".").pop() || "";
  return `\`${ctx.filePath}:${lineRange}\`\n\`\`\`${lang}\n${ctx.code}\n\`\`\``;
}

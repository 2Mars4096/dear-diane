import { extractFrontmatterSummary } from "./contentModeModel";

export type PreviewEditableKind =
  | "heading"
  | "paragraph"
  | "list_item"
  | "blockquote";

export interface PreviewEditableSelection {
  kind: PreviewEditableKind;
  text: string;
  allIndex: number;
  kindIndex: number;
  tagName: string;
}

export interface MarkdownEditableBlock {
  targetType: "markdown-block";
  kind: PreviewEditableKind;
  text: string;
  start: number;
  end: number;
  allIndex: number;
  kindIndex: number;
  headingLevel?: number;
  listMarker?: string;
  listIndent?: string;
}

export interface FrontmatterEditableTarget {
  targetType: "frontmatter-field";
  kind: "frontmatter-title";
  text: string;
  start: number;
  end: number;
  linePrefix: string;
}

export type PreviewEditTarget =
  | MarkdownEditableBlock
  | FrontmatterEditableTarget;

interface IndexedLine {
  text: string;
  start: number;
  end: number;
  lineEnding: string;
}

function splitIndexedLines(value: string): IndexedLine[] {
  const lines: IndexedLine[] = [];
  const regex = /.*(?:\r?\n|$)/g;
  let cursor = 0;

  for (const match of value.matchAll(regex)) {
    const segment = match[0];
    if (segment === "" && cursor >= value.length) break;
    const lineEnding = segment.endsWith("\r\n")
      ? "\r\n"
      : segment.endsWith("\n")
        ? "\n"
        : "";
    const text = lineEnding ? segment.slice(0, -lineEnding.length) : segment;
    lines.push({
      text,
      start: cursor,
      end: cursor + segment.length,
      lineEnding,
    });
    cursor += segment.length;
    if (cursor >= value.length) break;
  }

  return lines;
}

function normalizeLineBreaks(value: string) {
  return value.replace(/\r\n/g, "\n");
}

function detectPreferredNewline(value: string) {
  return value.includes("\r\n") ? "\r\n" : "\n";
}

function normalizeEditableText(value: string) {
  return value
    .replace(/\u00a0/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function isFenceDelimiter(line: string) {
  return /^(\s*)(`{3,}|~{3,})/.exec(line);
}

function isHeadingLine(line: string) {
  return /^(#{1,6})\s+(.+?)\s*$/.exec(line);
}

function isListItemLine(line: string) {
  return /^(\s*)([-*+]|\d+\.)\s+(.+?)\s*$/.exec(line);
}

function isBlockquoteLine(line: string) {
  return /^\s*>\s?(.*)$/.exec(line);
}

function isImageLine(trimmed: string) {
  return /^!\[[^\]]*]\([^)]+\)\s*$/.test(trimmed);
}

function isHtmlLikeLine(trimmed: string) {
  return /^<[^>]+>\s*$/.test(trimmed);
}

function isShortcodeLine(trimmed: string) {
  return /^{{[%<][\s\S]*[%>]}}\s*$/.test(trimmed);
}

function isHorizontalRule(trimmed: string) {
  return /^([-*_])\1{2,}\s*$/.test(trimmed);
}

function isTableRow(trimmed: string) {
  return /^\|.*\|\s*$/.test(trimmed);
}

function isParagraphBlocked(line: string) {
  const trimmed = line.trim();
  if (!trimmed) return true;
  return Boolean(
    isHeadingLine(line) ||
      isListItemLine(line) ||
      isBlockquoteLine(line) ||
      isFenceDelimiter(line) ||
      isImageLine(trimmed) ||
      isHtmlLikeLine(trimmed) ||
      isShortcodeLine(trimmed) ||
      isHorizontalRule(trimmed) ||
      isTableRow(trimmed),
  );
}

function pushMarkdownBlock(
  blocks: MarkdownEditableBlock[],
  kindCounts: Record<PreviewEditableKind, number>,
  block: Omit<MarkdownEditableBlock, "targetType" | "allIndex" | "kindIndex">,
) {
  const kindIndex = kindCounts[block.kind];
  kindCounts[block.kind] += 1;
  blocks.push({
    targetType: "markdown-block",
    ...block,
    kindIndex,
    allIndex: blocks.length,
  });
}

function buildRange(
  bodyStart: number,
  lines: IndexedLine[],
  startLine: number,
  endLine: number,
) {
  const endLineEntry = lines[endLine];
  return {
    start: bodyStart + lines[startLine].start,
    end: bodyStart + endLineEntry.end - endLineEntry.lineEnding.length,
  };
}

function buildParagraphText(lines: IndexedLine[], startLine: number, endLine: number) {
  return lines
    .slice(startLine, endLine + 1)
    .map((line) => line.text.trim())
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

function findFrontmatterTitleTarget(markdown: string): FrontmatterEditableTarget | null {
  const frontmatter = extractFrontmatterSummary(markdown);
  if (!frontmatter.raw || !frontmatter.title) return null;

  const match = markdown.match(/^---\s*\n([\s\S]*?)\n---\s*(?:\n|$)/);
  if (!match) return null;

  const rawStart = match[0].indexOf(frontmatter.raw);
  const titleLineMatch = /^(\s*title:\s*)(.+)$/.exec(
    frontmatter.raw
      .split(/\r?\n/)
      .find((line) => /^\s*title:\s*/.test(line)) ?? "",
  );
  if (!titleLineMatch) return null;

  const titleMatch = frontmatter.raw.match(/^(\s*title:\s*)(.+)$/m);
  if (!titleMatch || titleMatch.index == null) return null;

  return {
    targetType: "frontmatter-field",
    kind: "frontmatter-title",
    text: frontmatter.title,
    start: rawStart + titleMatch.index,
    end: rawStart + titleMatch.index + titleMatch[0].length,
    linePrefix: titleLineMatch[1],
  };
}

export function extractMarkdownEditableBlocks(markdown: string) {
  const frontmatterMatch = markdown.match(/^---\s*\n([\s\S]*?)\n---\s*(?:\n|$)/);
  const bodyStart = frontmatterMatch ? frontmatterMatch[0].length : 0;
  const body = markdown.slice(bodyStart);
  const lines = splitIndexedLines(body);
  const blocks: MarkdownEditableBlock[] = [];
  const kindCounts: Record<PreviewEditableKind, number> = {
    heading: 0,
    paragraph: 0,
    list_item: 0,
    blockquote: 0,
  };

  let index = 0;
  let inFence = false;
  let activeFence: string | null = null;

  while (index < lines.length) {
    const line = lines[index].text;
    const trimmed = line.trim();

    const fenceMatch = isFenceDelimiter(line);
    if (fenceMatch) {
      const fenceMarker = fenceMatch[2][0];
      if (!inFence) {
        inFence = true;
        activeFence = fenceMarker;
      } else if (fenceMarker === activeFence) {
        inFence = false;
        activeFence = null;
      }
      index += 1;
      continue;
    }

    if (inFence || trimmed === "") {
      index += 1;
      continue;
    }

    const headingMatch = isHeadingLine(line);
    if (headingMatch) {
      const range = buildRange(bodyStart, lines, index, index);
      pushMarkdownBlock(blocks, kindCounts, {
        kind: "heading",
        text: headingMatch[2].trim(),
        start: range.start,
        end: range.end,
        headingLevel: headingMatch[1].length,
      });
      index += 1;
      continue;
    }

    const listMatch = isListItemLine(line);
    if (listMatch) {
      const range = buildRange(bodyStart, lines, index, index);
      pushMarkdownBlock(blocks, kindCounts, {
        kind: "list_item",
        text: listMatch[3].trim(),
        start: range.start,
        end: range.end,
        listIndent: listMatch[1],
        listMarker: listMatch[2],
      });
      index += 1;
      continue;
    }

    const blockquoteMatch = isBlockquoteLine(line);
    if (blockquoteMatch) {
      const startLine = index;
      const quotedLines: string[] = [];
      while (index < lines.length) {
        const current = isBlockquoteLine(lines[index].text);
        if (!current) break;
        quotedLines.push(current[1].trimEnd());
        index += 1;
      }
      const range = buildRange(bodyStart, lines, startLine, index - 1);
      pushMarkdownBlock(blocks, kindCounts, {
        kind: "blockquote",
        text: normalizeLineBreaks(quotedLines.join("\n")).trim(),
        start: range.start,
        end: range.end,
      });
      continue;
    }

    if (isParagraphBlocked(line)) {
      index += 1;
      continue;
    }

    const startLine = index;
    while (index < lines.length && !isParagraphBlocked(lines[index].text)) {
      index += 1;
    }
    const endLine = index - 1;
    const range = buildRange(bodyStart, lines, startLine, endLine);
    pushMarkdownBlock(blocks, kindCounts, {
      kind: "paragraph",
      text: buildParagraphText(lines, startLine, endLine),
      start: range.start,
      end: range.end,
    });
  }

  return blocks;
}

function textMatchesSelection(blockText: string, selectionText: string) {
  const normalizedBlock = normalizeEditableText(blockText);
  const normalizedSelection = normalizeEditableText(selectionText);
  if (!normalizedBlock || !normalizedSelection) return false;
  return (
    normalizedBlock === normalizedSelection ||
    normalizedBlock.includes(normalizedSelection) ||
    normalizedSelection.includes(normalizedBlock)
  );
}

export function findPreviewEditTarget(
  markdown: string,
  selection: PreviewEditableSelection,
): PreviewEditTarget | null {
  const normalizedSelectionText = normalizeEditableText(selection.text);
  if (!normalizedSelectionText) return null;

  const titleTarget = findFrontmatterTitleTarget(markdown);
  if (
    selection.kind === "heading" &&
    titleTarget &&
    textMatchesSelection(titleTarget.text, selection.text)
  ) {
    return titleTarget;
  }

  const blocks = extractMarkdownEditableBlocks(markdown);
  const sameKind = blocks.filter((block) => block.kind === selection.kind);
  const exactMatches = sameKind.filter((block) =>
    textMatchesSelection(block.text, selection.text),
  );

  if (exactMatches.length === 1) return exactMatches[0];
  if (exactMatches.length > 1) {
    return (
      exactMatches.find((block) => block.kindIndex === selection.kindIndex) ??
      exactMatches.find((block) => block.allIndex === selection.allIndex) ??
      exactMatches[0]
    );
  }

  return (
    sameKind.find((block) => block.kindIndex === selection.kindIndex) ??
    blocks.find((block) => block.allIndex === selection.allIndex) ??
    null
  );
}

function buildMarkdownBlockReplacement(
  target: MarkdownEditableBlock,
  nextText: string,
  newline: string,
) {
  const normalized = normalizeLineBreaks(nextText).trim();

  switch (target.kind) {
    case "heading":
      return `${"#".repeat(target.headingLevel ?? 1)} ${normalized.replace(/\s+/g, " ").trim()}`;
    case "list_item": {
      const lines = normalized.split("\n").map((line) => line.trim()).filter(Boolean);
      if (lines.length === 0) {
        return `${target.listIndent ?? ""}${target.listMarker ?? "-"} `;
      }
      const continuationIndent = `${target.listIndent ?? ""}  `;
      return [
        `${target.listIndent ?? ""}${target.listMarker ?? "-"} ${lines[0]}`,
        ...lines.slice(1).map((line) => `${continuationIndent}${line}`),
      ].join(newline);
    }
    case "blockquote":
      return normalized
        .split("\n")
        .map((line) => `> ${line.trimEnd()}`)
        .join(newline);
    case "paragraph":
    default:
      return normalized;
  }
}

export function applyPreviewEditTarget(
  markdown: string,
  target: PreviewEditTarget,
  nextText: string,
) {
  const newline = detectPreferredNewline(markdown);

  if (target.targetType === "frontmatter-field") {
    const replacementLine = `${target.linePrefix}${JSON.stringify(nextText.trim())}`;
    return `${markdown.slice(0, target.start)}${replacementLine}${markdown.slice(target.end)}`;
  }

  const replacement = buildMarkdownBlockReplacement(target, nextText, newline);
  return `${markdown.slice(0, target.start)}${replacement}${markdown.slice(target.end)}`;
}

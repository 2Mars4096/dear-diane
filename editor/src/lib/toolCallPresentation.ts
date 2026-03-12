import type { ToolCallInfo } from "../types/chat";

export interface FileReadGroupInfo {
  path: string;
  label: string;
  readCount: number;
  summaries: string[];
}

export interface ToolCallDisplayItem {
  key: string;
  toolCall: ToolCallInfo;
  fileReadGroup?: FileReadGroupInfo;
}

function basename(filePath: string): string {
  const parts = filePath.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? filePath;
}

function parseArgsPreview(argsPreview: string): Record<string, unknown> | null {
  const trimmed = argsPreview.trim();
  if (!trimmed) return null;
  try {
    const parsed = JSON.parse(trimmed);
    return parsed && typeof parsed === "object"
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function extractJsonStringField(raw: string, field: string): string | null {
  const match = raw.match(
    new RegExp(`"${field}"\\s*:\\s*"((?:\\\\.|[^"])*)"`, "i"),
  );
  if (!match) return null;
  return match[1].replace(/\\"/g, '"').replace(/\\\\/g, "\\");
}

function extractJsonNumberField(raw: string, field: string): number | null {
  const match = raw.match(new RegExp(`"${field}"\\s*:\\s*(\\d+)`, "i"));
  if (!match) return null;
  const parsed = Number(match[1]);
  return Number.isFinite(parsed) ? parsed : null;
}

function summarizeFileReadRange(
  startLine: number | null,
  endLine: number | null,
  grep: string | null,
): string {
  if (grep) return `grep "${grep}"`;
  if (startLine !== null && endLine !== null) return `L${startLine}-${endLine}`;
  if (startLine !== null) return `from L${startLine}`;
  if (endLine !== null) return `to L${endLine}`;
  return "full file";
}

function extractFileReadInfo(
  toolCall: ToolCallInfo,
): { path: string; summary: string } | null {
  if (toolCall.toolName !== "file_read") return null;

  const parsed = parseArgsPreview(toolCall.argsPreview);
  const path =
    (typeof parsed?.path === "string" ? parsed.path : null) ??
    extractJsonStringField(toolCall.argsPreview, "path");
  if (!path) return null;

  const startLine =
    typeof parsed?.start_line === "number"
      ? parsed.start_line
      : extractJsonNumberField(toolCall.argsPreview, "start_line");
  const endLine =
    typeof parsed?.end_line === "number"
      ? parsed.end_line
      : extractJsonNumberField(toolCall.argsPreview, "end_line");
  const grep =
    (typeof parsed?.grep === "string" ? parsed.grep.trim() : "") ||
    extractJsonStringField(toolCall.argsPreview, "grep") ||
    null;

  return {
    path,
    summary: summarizeFileReadRange(startLine ?? null, endLine ?? null, grep),
  };
}

function mergeToolCallStatus(
  left: ToolCallInfo["status"],
  right: ToolCallInfo["status"],
): ToolCallInfo["status"] {
  if (left === "error" || right === "error") return "error";
  if (left === "running" || right === "running") return "running";
  return "success";
}

export function groupToolCallsForDisplay(
  toolCalls: ToolCallInfo[],
): ToolCallDisplayItem[] {
  const items: ToolCallDisplayItem[] = [];
  const fileGroups = new Map<string, ToolCallDisplayItem>();

  for (const toolCall of toolCalls) {
    const fileRead = extractFileReadInfo(toolCall);
    if (!fileRead) {
      items.push({ key: toolCall.id, toolCall });
      continue;
    }

    const existing = fileGroups.get(fileRead.path);
    if (!existing) {
      const group: ToolCallDisplayItem = {
        key: `file_read:${fileRead.path}`,
        toolCall: {
          ...toolCall,
          argsPreview: "",
        },
        fileReadGroup: {
          path: fileRead.path,
          label: basename(fileRead.path),
          readCount: 1,
          summaries: [fileRead.summary],
        },
      };
      fileGroups.set(fileRead.path, group);
      items.push(group);
      continue;
    }

    const group = existing.fileReadGroup!;
    group.readCount += 1;
    if (!group.summaries.includes(fileRead.summary)) {
      group.summaries.push(fileRead.summary);
    }
    existing.toolCall = {
      ...existing.toolCall,
      status: mergeToolCallStatus(existing.toolCall.status, toolCall.status),
      durationMs:
        (existing.toolCall.durationMs ?? 0) + (toolCall.durationMs ?? 0),
      outputPreview: toolCall.outputPreview || existing.toolCall.outputPreview,
      argsPreview: "",
    };
  }

  return items;
}

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

function truncateText(value: string, maxChars = 48): string {
  const clean = value.trim();
  if (clean.length <= maxChars) return clean;
  return `${clean.slice(0, maxChars - 1).trimEnd()}…`;
}

function humanizeToolName(toolName: string): string {
  const spaced = toolName
    .replace(/^mcp[_-]/i, "")
    .replace(/[_-]+/g, " ")
    .trim();
  if (!spaced) return "tool";
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function extractStringArg(
  toolCall: ToolCallInfo,
  ...fieldNames: string[]
): string | null {
  const parsed = parseArgsPreview(toolCall.argsPreview);
  for (const fieldName of fieldNames) {
    const parsedValue = parsed?.[fieldName];
    if (typeof parsedValue === "string" && parsedValue.trim()) {
      return parsedValue.trim();
    }
    const rawValue = extractJsonStringField(toolCall.argsPreview, fieldName);
    if (rawValue?.trim()) return rawValue.trim();
  }
  return null;
}

function formatPathTarget(pathValue: string): string {
  const label = basename(pathValue);
  return truncateText(label || pathValue, 40);
}

function extractToolTarget(toolCall: ToolCallInfo): string | null {
  switch (toolCall.toolName) {
    case "file_read":
    case "read_file":
    case "write_file":
    case "edit_file":
    case "list_directory": {
      const pathValue = extractStringArg(
        toolCall,
        "path",
        "file_path",
        "directory",
        "dir",
      );
      return pathValue ? formatPathTarget(pathValue) : null;
    }
    case "web_search":
    case "search_web": {
      const query = extractStringArg(toolCall, "query", "search_term", "term");
      return query ? `"${truncateText(query, 36)}"` : null;
    }
    case "grep":
    case "rg":
    case "search_files": {
      const pattern = extractStringArg(toolCall, "pattern", "query", "grep");
      return pattern ? `"${truncateText(pattern, 36)}"` : null;
    }
    case "web_fetch":
    case "open_url":
    case "fetch_url": {
      const url = extractStringArg(toolCall, "url");
      return url ? truncateText(url, 48) : null;
    }
    case "shell":
    case "run_command":
    case "execute_command": {
      const command = extractStringArg(toolCall, "command", "cmd");
      return command ? truncateText(command, 40) : null;
    }
    default: {
      const pathValue = extractStringArg(toolCall, "path", "file_path");
      if (pathValue) return formatPathTarget(pathValue);
      const query = extractStringArg(toolCall, "query", "search_term", "term");
      if (query) return `"${truncateText(query, 36)}"`;
      return null;
    }
  }
}

function formatToolProgressMessage(
  toolCall: ToolCallInfo,
  phase: ToolCallInfo["status"],
): string {
  const target = extractToolTarget(toolCall);
  switch (toolCall.toolName) {
    case "list_directory":
      if (phase === "running") {
        return target
          ? `Listing directory contents of ${target}`
          : "Listing directory contents";
      }
      if (phase === "success") {
        return target
          ? `Listed directory contents of ${target}. Continuing...`
          : "Listed directory contents. Continuing...";
      }
      return target
        ? `Directory listing failed for ${target}. Continuing...`
        : "Directory listing failed. Continuing...";
    case "file_read":
    case "read_file":
      if (phase === "running") {
        return target ? `Reading ${target}` : "Reading a file";
      }
      if (phase === "success") {
        return target ? `Read ${target}. Continuing...` : "Read file. Continuing...";
      }
      return target ? `Failed to read ${target}. Continuing...` : "File read failed. Continuing...";
    case "web_search":
    case "search_web":
      if (phase === "running") {
        return target ? `Searching the web for ${target}` : "Searching the web";
      }
      if (phase === "success") {
        return target
          ? `Finished web search for ${target}. Continuing...`
          : "Finished web search. Continuing...";
      }
      return target
        ? `Web search failed for ${target}. Continuing...`
        : "Web search failed. Continuing...";
    case "write_file":
    case "edit_file":
      if (phase === "running") {
        return target ? `Updating ${target}` : "Updating a file";
      }
      if (phase === "success") {
        return target ? `Updated ${target}. Continuing...` : "Updated file. Continuing...";
      }
      return target ? `Failed to update ${target}. Continuing...` : "File update failed. Continuing...";
    default: {
      const name = humanizeToolName(toolCall.toolName);
      if (phase === "running") {
        return target ? `${name}: ${target}` : name;
      }
      if (phase === "success") {
        return target ? `${name} complete: ${target}. Continuing...` : `${name} complete. Continuing...`;
      }
      return target ? `${name} failed: ${target}. Continuing...` : `${name} failed. Continuing...`;
    }
  }
}

export function describeLatestToolProgress(toolCalls: ToolCallInfo[]): string | null {
  const latest =
    [...toolCalls].reverse().find((toolCall) => toolCall.status === "running") ??
    toolCalls[toolCalls.length - 1];
  if (!latest) return null;
  return formatToolProgressMessage(latest, latest.status);
}

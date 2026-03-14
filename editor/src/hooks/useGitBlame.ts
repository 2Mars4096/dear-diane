import { useEffect, useState, useRef } from "react";
import { nativeGit } from "../lib/electronBridge";
import { useCodeStore } from "../store/useCodeStore";

export interface BlameLine {
  lineNumber: number;
  hash: string;
  author: string;
  email: string;
  date: string;
  dateAbsolute: string;
  summary: string;
}

function formatRelativeTime(date: Date): string {
  const diff = Date.now() - date.getTime();
  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const months = Math.floor(days / 30);
  const years = Math.floor(days / 365);

  if (years > 0) return `${years}y ago`;
  if (months > 0) return `${months}mo ago`;
  if (days > 0) return `${days}d ago`;
  if (hours > 0) return `${hours}h ago`;
  if (minutes > 0) return `${minutes}m ago`;
  return "just now";
}

function parseBlamePortable(output: string): BlameLine[] {
  const lines: BlameLine[] = [];

  let currentHash = "";
  let currentAuthor = "";
  let currentEmail = "";
  let currentTime = 0;
  let currentSummary = "";
  let currentLine = 0;

  for (const rawLine of output.split("\n")) {
    const hashMatch = rawLine.match(/^([a-f0-9]{40})\s+\d+\s+(\d+)/);
    if (hashMatch) {
      currentHash = hashMatch[1];
      currentLine = parseInt(hashMatch[2], 10);
      continue;
    }

    if (rawLine.startsWith("author ")) {
      currentAuthor = rawLine.slice(7);
    } else if (rawLine.startsWith("author-mail ")) {
      currentEmail = rawLine.slice(12).replace(/^<|>$/g, "");
    } else if (rawLine.startsWith("author-time ")) {
      currentTime = parseInt(rawLine.slice(12), 10);
    } else if (rawLine.startsWith("summary ")) {
      currentSummary = rawLine.slice(8);
    } else if (rawLine.startsWith("\t")) {
      const date = new Date(currentTime * 1000);
      lines.push({
        lineNumber: currentLine,
        hash: currentHash,
        author: currentAuthor,
        email: currentEmail,
        date: formatRelativeTime(date),
        dateAbsolute: date.toLocaleString(),
        summary: currentSummary,
      });
    }
  }

  return lines;
}

export function useGitBlame(disabled = false): BlameLine[] {
  const activeFilePath = useCodeStore((s) => s.activeFilePath);
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const [blame, setBlame] = useState<BlameLine[]>([]);
  const fetchRef = useRef(0);

  useEffect(() => {
    if (disabled || !activeFilePath || pinnedRoots.length === 0) {
      setBlame([]);
      return;
    }

    const fetchId = ++fetchRef.current;
    const cwd =
      pinnedRoots.find((r) => activeFilePath.startsWith(r)) ?? pinnedRoots[0];
    const relPath = activeFilePath.startsWith(cwd)
      ? activeFilePath.slice(cwd.length + 1)
      : activeFilePath;

    nativeGit.blame(cwd, relPath).then((result) => {
      if (fetchId !== fetchRef.current) return;
      if (result.code === 0) {
        setBlame(parseBlamePortable(result.stdout));
      } else {
        setBlame([]);
      }
    });
  }, [activeFilePath, pinnedRoots, disabled]);

  return blame;
}

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { nativeGit } from "../../lib/electronBridge";
import { useCodeStore } from "../../store/useCodeStore";

const LANE_COLORS = [
  "#4ec9b0", "#569cd6", "#dcdcaa", "#ce9178",
  "#c586c0", "#d16969", "#6a9955", "#d7ba7d",
];

const ROW_H = 28;
const LANE_W = 20;
const RADIUS = 4;
const GRAPH_PAD = 10;

interface GraphCommit {
  hash: string;
  shortHash: string;
  parents: string[];
  message: string;
  author: string;
  email: string;
  date: string;
  refs: string[];
  lane: number;
}

function parseLogGraph(output: string): Omit<GraphCommit, "lane">[] {
  if (!output.trim()) return [];
  return output
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      const parts = line.split("|");
      if (parts.length < 8) return null;
      const [hash, shortHash, parentStr, message, author, email, date, ...refParts] = parts;
      const parents = parentStr.trim() ? parentStr.trim().split(" ") : [];
      const refsRaw = refParts.join("|").trim();
      const refs = refsRaw
        ? refsRaw.split(",").map((r) => r.trim()).filter(Boolean)
        : [];
      return { hash, shortHash, parents, message, author, email, date, refs };
    })
    .filter((c): c is Omit<GraphCommit, "lane"> => c !== null);
}

function assignLanes(rawCommits: Omit<GraphCommit, "lane">[]): GraphCommit[] {
  if (rawCommits.length === 0) return [];

  const commits: GraphCommit[] = [];
  const activeLanes: (string | null)[] = [];

  const findOrCreateLane = (hash: string): number => {
    const idx = activeLanes.indexOf(hash);
    if (idx !== -1) return idx;
    const emptyIdx = activeLanes.indexOf(null);
    if (emptyIdx !== -1) {
      activeLanes[emptyIdx] = hash;
      return emptyIdx;
    }
    activeLanes.push(hash);
    return activeLanes.length - 1;
  };

  for (const raw of rawCommits) {
    let lane = activeLanes.indexOf(raw.hash);
    if (lane === -1) {
      lane = findOrCreateLane(raw.hash);
    }

    commits.push({ ...raw, lane });

    activeLanes[lane] = null;

    if (raw.parents.length > 0) {
      const primaryParent = raw.parents[0];
      if (activeLanes[lane] === null) {
        activeLanes[lane] = primaryParent;
      } else {
        findOrCreateLane(primaryParent);
      }
    }

    for (let i = 1; i < raw.parents.length; i++) {
      findOrCreateLane(raw.parents[i]);
    }
  }

  return commits;
}

function formatRef(ref: string): { label: string; type: "head" | "local" | "remote" | "tag" } {
  if (ref === "HEAD") return { label: "HEAD", type: "head" };
  if (ref.startsWith("HEAD -> ")) return { label: ref.slice(8), type: "head" };
  if (ref.startsWith("tag: ")) return { label: ref.slice(5), type: "tag" };
  if (ref.startsWith("origin/") || ref.startsWith("refs/remotes/"))
    return { label: ref.replace("refs/remotes/", ""), type: "remote" };
  return { label: ref, type: "local" };
}

const REF_COLORS: Record<string, string> = {
  head: "bg-yellow-600 text-yellow-100",
  local: "bg-green-700 text-green-100",
  remote: "bg-red-700 text-red-100",
  tag: "bg-blue-700 text-blue-100",
};

interface GitGraphProps {
  onViewCommit?: (hash: string) => void;
}

export default function GitGraph({ onViewCommit }: GitGraphProps) {
  const cwd = useCodeStore((s) => s.pinnedRoots[0] ?? "");
  const [commits, setCommits] = useState<GraphCommit[]>([]);
  const [loading, setLoading] = useState(false);
  const [maxCount, setMaxCount] = useState(80);
  const [selectedHash, setSelectedHash] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const loadGraph = useCallback(async (count: number) => {
    if (!cwd) return;
    setLoading(true);
    try {
      const res = await nativeGit.logGraph(cwd, count);
      if (res.code === 0) {
        const raw = parseLogGraph(res.stdout);
        setCommits(assignLanes(raw));
      }
    } finally {
      setLoading(false);
    }
  }, [cwd]);

  useEffect(() => {
    loadGraph(maxCount);
  }, [loadGraph, maxCount]);

  const maxLane = useMemo(
    () => Math.max(0, ...commits.map((c) => c.lane)),
    [commits],
  );

  const svgWidth = (maxLane + 1) * LANE_W + GRAPH_PAD * 2;

  const parentRowMap = useMemo(() => {
    const map = new Map<string, number>();
    commits.forEach((c, i) => map.set(c.hash, i));
    return map;
  }, [commits]);

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el || loading) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 50) {
      setMaxCount((c) => c + 60);
    }
  }, [loading]);

  const cx = (lane: number) => lane * LANE_W + GRAPH_PAD;
  const cy = (row: number) => row * ROW_H + ROW_H / 2;

  return (
    <div
      ref={containerRef}
      className="flex h-full flex-col overflow-y-auto bg-gray-900 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-700 [&::-webkit-scrollbar]:w-1.5"
      onScroll={handleScroll}
    >
      {commits.length === 0 && !loading && (
        <div className="px-3 py-4 text-center text-xs text-gray-500">No commits</div>
      )}

      {commits.map((commit, rowIdx) => {
        const isSelected = selectedHash === commit.hash;
        return (
          <div
            key={commit.hash}
            className={`flex items-center border-b border-gray-800/50 hover:bg-gray-800/40 cursor-pointer ${isSelected ? "bg-gray-800/60" : ""}`}
            style={{ minHeight: ROW_H }}
            onClick={() => {
              setSelectedHash(commit.hash);
              onViewCommit?.(commit.hash);
            }}
          >
            {/* SVG column */}
            <svg
              width={svgWidth}
              height={ROW_H}
              className="shrink-0"
              style={{ minWidth: svgWidth }}
            >
              {/* Lines from this commit to its parents */}
              {commit.parents.map((parentHash, pi) => {
                const parentRow = parentRowMap.get(parentHash);
                if (parentRow === undefined) return null;
                const parentCommit = commits[parentRow];
                if (!parentCommit) return null;

                const x1 = cx(commit.lane);
                const y1 = ROW_H / 2;
                const x2 = cx(parentCommit.lane);
                const yEnd = (parentRow - rowIdx) * ROW_H + ROW_H / 2;
                const color = LANE_COLORS[commit.lane % LANE_COLORS.length];

                if (commit.lane === parentCommit.lane) {
                  return (
                    <line
                      key={`${parentHash}-${pi}`}
                      x1={x1} y1={y1} x2={x2} y2={yEnd}
                      stroke={color} strokeWidth={1.5}
                    />
                  );
                }

                const midY = y1 + ROW_H * 0.6;
                return (
                  <path
                    key={`${parentHash}-${pi}`}
                    d={`M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${yEnd}`}
                    stroke={LANE_COLORS[parentCommit.lane % LANE_COLORS.length]}
                    strokeWidth={1.5}
                    fill="none"
                  />
                );
              })}

              {/* Commit dot */}
              <circle
                cx={cx(commit.lane)}
                cy={ROW_H / 2}
                r={RADIUS}
                fill={LANE_COLORS[commit.lane % LANE_COLORS.length]}
                stroke="#1e1e1e"
                strokeWidth={1}
              />
            </svg>

            {/* Details column */}
            <div className="flex min-w-0 flex-1 items-center gap-2 px-2 py-0.5">
              {/* Ref badges */}
              {commit.refs.length > 0 && (
                <div className="flex shrink-0 items-center gap-1">
                  {commit.refs.map((r) => {
                    const { label, type } = formatRef(r);
                    return (
                      <span
                        key={r}
                        className={`rounded px-1 py-px text-[9px] font-semibold leading-tight ${REF_COLORS[type]}`}
                      >
                        {label}
                      </span>
                    );
                  })}
                </div>
              )}

              <span className="shrink-0 font-mono text-[11px] text-yellow-400">
                {commit.shortHash}
              </span>
              <span className="min-w-0 truncate text-xs text-gray-300">
                {commit.message}
              </span>
              <span className="ml-auto shrink-0 text-[10px] text-gray-500">
                {commit.author}
              </span>
              <span className="shrink-0 text-[10px] text-gray-600">
                {commit.date.split(" ").slice(0, 1).join("")}
              </span>
            </div>
          </div>
        );
      })}

      {loading && (
        <div className="flex items-center justify-center gap-2 py-3 text-xs text-gray-500">
          <Loader2 size={14} className="animate-spin" />
          Loading…
        </div>
      )}
    </div>
  );
}

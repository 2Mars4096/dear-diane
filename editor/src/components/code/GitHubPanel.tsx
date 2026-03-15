import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  GitMerge,
  GitPullRequest,
  Loader2,
  Plus,
  RefreshCw,
  FileText,
  ChevronLeft,
} from "lucide-react";
import { useCodeStore } from "../../store/useCodeStore";
import { nativeGitHub, type GitResult } from "../../lib/electronBridge";

// ─── Types ──────────────────────────────────────────────────────────────

interface GhPR {
  number: number;
  title: string;
  state: string;
  author: { login: string };
  createdAt: string;
  updatedAt: string;
  headRefName: string;
  baseRefName: string;
  isDraft: boolean;
  url: string;
  reviewDecision: string;
  additions: number;
  deletions: number;
}

interface GhPRDetail {
  number: number;
  title: string;
  body: string;
  state: string;
  author: { login: string };
  comments: any[];
  reviews: any[];
  files: Array<{ path: string; additions: number; deletions: number }>;
  additions: number;
  deletions: number;
  url: string;
  headRefName: string;
  baseRefName: string;
  mergeable: string;
  reviewDecision: string;
  labels: Array<{ name: string; color: string }>;
}

interface GhIssue {
  number: number;
  title: string;
  state: string;
  author: { login: string };
  labels: Array<{ name: string; color: string }>;
  createdAt: string;
  url: string;
}

type MergeMethod = "merge" | "squash" | "rebase";

// ─── Helpers ────────────────────────────────────────────────────────────

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

function parseJsonResult<T>(result: GitResult): T | null {
  if (result.code !== 0) return null;
  try {
    return JSON.parse(result.stdout);
  } catch {
    return null;
  }
}

const STATE_COLORS: Record<string, string> = {
  OPEN: "bg-green-600",
  CLOSED: "bg-red-600",
  MERGED: "bg-purple-600",
  open: "bg-green-600",
  closed: "bg-gray-600",
};

const REVIEW_LABELS: Record<string, { text: string; color: string }> = {
  APPROVED: { text: "Approved", color: "text-green-400" },
  CHANGES_REQUESTED: { text: "Changes requested", color: "text-red-400" },
  REVIEW_REQUIRED: { text: "Review required", color: "text-yellow-400" },
};

// ─── PR List Item ───────────────────────────────────────────────────────

function PRItem({ pr, onClick }: { pr: GhPR; onClick: () => void }) {
  const review = REVIEW_LABELS[pr.reviewDecision];

  return (
    <button
      onClick={onClick}
      className="w-full text-left px-3 py-2 hover:bg-gray-700/50 transition-colors group"
    >
      <div className="flex items-start gap-2">
        <GitPullRequest
          size={14}
          className={`shrink-0 mt-0.5 ${pr.state === "MERGED" ? "text-purple-400" : pr.state === "OPEN" ? "text-green-400" : "text-gray-500"}`}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-gray-200 truncate">{pr.title}</span>
            {pr.isDraft && (
              <span className="shrink-0 text-[9px] bg-gray-700 text-gray-400 px-1 rounded">draft</span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-0.5 text-[10px] text-gray-500">
            <span>#{pr.number}</span>
            <span>{pr.author.login}</span>
            <span className="text-green-500">+{pr.additions}</span>
            <span className="text-red-500">-{pr.deletions}</span>
            {review && <span className={review.color}>{review.text}</span>}
            <span className="ml-auto">{relativeTime(pr.updatedAt)}</span>
          </div>
          <div className="text-[10px] text-gray-600 mt-0.5">
            {pr.headRefName} → {pr.baseRefName}
          </div>
        </div>
        <ChevronRight size={12} className="shrink-0 text-gray-600 mt-1 opacity-0 group-hover:opacity-100" />
      </div>
    </button>
  );
}

// ─── Issue List Item ────────────────────────────────────────────────────

function IssueItem({ issue }: { issue: GhIssue }) {
  return (
    <a
      href={issue.url}
      target="_blank"
      rel="noopener noreferrer"
      className="flex items-start gap-2 px-3 py-2 hover:bg-gray-700/50 transition-colors group"
    >
      <AlertCircle
        size={14}
        className={`shrink-0 mt-0.5 ${issue.state === "OPEN" ? "text-green-400" : "text-gray-500"}`}
      />
      <div className="flex-1 min-w-0">
        <span className="text-xs text-gray-200 truncate block">{issue.title}</span>
        <div className="flex items-center gap-2 mt-0.5 text-[10px] text-gray-500">
          <span>#{issue.number}</span>
          <span>{issue.author.login}</span>
          {issue.labels.map((l) => (
            <span
              key={l.name}
              className="px-1 rounded text-[9px]"
              style={{ backgroundColor: `#${l.color}30`, color: `#${l.color}` }}
            >
              {l.name}
            </span>
          ))}
          <span className="ml-auto">{relativeTime(issue.createdAt)}</span>
        </div>
      </div>
      <ExternalLink size={10} className="shrink-0 text-gray-600 mt-1 opacity-0 group-hover:opacity-100" />
    </a>
  );
}

// ─── PR Detail View ─────────────────────────────────────────────────────

function PRDetailView({
  pr,
  cwd,
  onBack,
  onRefresh,
}: {
  pr: GhPRDetail;
  cwd: string;
  onBack: () => void;
  onRefresh: () => void;
}) {
  const [mergeDropdown, setMergeDropdown] = useState(false);
  const [merging, setMerging] = useState(false);
  const [checkingOut, setCheckingOut] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; msg: string } | null>(null);
  const openDiff = useCodeStore((s) => s.openDiff);
  const feedbackTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const showFeedback = useCallback((type: "success" | "error", msg: string) => {
    setFeedback({ type, msg });
    clearTimeout(feedbackTimer.current);
    feedbackTimer.current = setTimeout(() => setFeedback(null), 4000);
  }, []);

  const handleCheckout = async () => {
    setCheckingOut(true);
    try {
      const res = await nativeGitHub.prCheckout(cwd, pr.number);
      if (res.code === 0) {
        showFeedback("success", `Checked out PR #${pr.number}`);
      } else {
        showFeedback("error", res.stderr.split("\n")[0] || "Checkout failed");
      }
    } catch {
      showFeedback("error", "Checkout failed");
    } finally {
      setCheckingOut(false);
    }
  };

  const handleMerge = async (method: MergeMethod) => {
    setMerging(true);
    setMergeDropdown(false);
    try {
      const res = await nativeGitHub.prMerge(cwd, pr.number, method);
      if (res.code === 0) {
        showFeedback("success", `PR #${pr.number} merged (${method})`);
        onRefresh();
      } else {
        showFeedback("error", res.stderr.split("\n")[0] || "Merge failed");
      }
    } catch {
      showFeedback("error", "Merge failed");
    } finally {
      setMerging(false);
    }
  };

  const handleViewDiff = async () => {
    const res = await nativeGitHub.prDiff(cwd, pr.number);
    if (res.code === 0) {
      openDiff("", res.stdout, `PR #${pr.number} base`, `PR #${pr.number} diff`);
    }
  };

  const review = REVIEW_LABELS[pr.reviewDecision];
  const btnCls = "rounded px-2 py-1 text-xs transition-colors disabled:opacity-40";

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-gray-800 px-3 py-1.5">
        <button onClick={onBack} className="text-gray-400 hover:text-gray-200 transition-colors">
          <ChevronLeft size={14} />
        </button>
        <span className="text-xs text-gray-300 font-medium truncate">#{pr.number} {pr.title}</span>
        <a
          href={pr.url}
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto text-gray-500 hover:text-gray-300 transition-colors"
        >
          <ExternalLink size={12} />
        </a>
      </div>

      {feedback && (
        <div className={`mx-2 mt-1.5 rounded px-2.5 py-1 text-xs ${
          feedback.type === "success" ? "bg-green-900/40 text-green-300" : "bg-red-900/40 text-red-300"
        }`}>
          {feedback.msg}
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-3 py-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-700 [&::-webkit-scrollbar]:w-1.5">
        {/* Meta */}
        <div className="flex items-center gap-2 mb-2 text-[10px] text-gray-500">
          <span className={`px-1.5 py-0.5 rounded text-white text-[9px] font-medium ${STATE_COLORS[pr.state] ?? "bg-gray-600"}`}>
            {pr.state}
          </span>
          <span>{pr.headRefName} → {pr.baseRefName}</span>
          {review && <span className={review.color}>{review.text}</span>}
          <span className="text-green-500">+{pr.additions}</span>
          <span className="text-red-500">-{pr.deletions}</span>
        </div>

        {/* Labels */}
        {pr.labels.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-2">
            {pr.labels.map((l) => (
              <span
                key={l.name}
                className="px-1.5 py-0.5 rounded text-[9px]"
                style={{ backgroundColor: `#${l.color}30`, color: `#${l.color}` }}
              >
                {l.name}
              </span>
            ))}
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-1.5 mb-3">
          <button
            className={`${btnCls} bg-gray-700 text-gray-200 hover:bg-gray-600`}
            onClick={handleCheckout}
            disabled={checkingOut}
          >
            {checkingOut ? <Loader2 size={11} className="animate-spin" /> : "Checkout"}
          </button>
          <button
            className={`${btnCls} bg-gray-700 text-gray-200 hover:bg-gray-600`}
            onClick={handleViewDiff}
          >
            View Diff
          </button>
          {pr.state === "OPEN" && (
            <div className="relative">
              <button
                className={`${btnCls} bg-green-700 text-white hover:bg-green-600 flex items-center gap-1`}
                onClick={() => setMergeDropdown(!mergeDropdown)}
                disabled={merging}
              >
                {merging ? <Loader2 size={11} className="animate-spin" /> : <GitMerge size={11} />}
                Merge
                <ChevronDown size={10} />
              </button>
              {mergeDropdown && (
                <div className="absolute top-full left-0 z-50 mt-1 w-32 rounded border border-gray-700 bg-gray-800 shadow-xl">
                  {(["merge", "squash", "rebase"] as MergeMethod[]).map((m) => (
                    <button
                      key={m}
                      className="w-full text-left px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700 capitalize"
                      onClick={() => handleMerge(m)}
                    >
                      {m}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Body */}
        {pr.body && (
          <div className="mb-3">
            <div className="text-[10px] font-semibold uppercase text-gray-500 mb-1">Description</div>
            <div className="text-xs text-gray-300 whitespace-pre-wrap bg-gray-800/50 rounded p-2 max-h-[200px] overflow-y-auto">
              {pr.body}
            </div>
          </div>
        )}

        {/* Files */}
        {pr.files && pr.files.length > 0 && (
          <div className="mb-3">
            <div className="text-[10px] font-semibold uppercase text-gray-500 mb-1">
              Files Changed ({pr.files.length})
            </div>
            <div className="space-y-0.5">
              {pr.files.map((f) => (
                <div key={f.path} className="flex items-center gap-1.5 text-xs py-0.5">
                  <FileText size={10} className="text-gray-500 shrink-0" />
                  <span className="text-gray-300 truncate">{f.path}</span>
                  <span className="ml-auto text-green-500 text-[10px] shrink-0">+{f.additions}</span>
                  <span className="text-red-500 text-[10px] shrink-0">-{f.deletions}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Create PR Form ─────────────────────────────────────────────────────

function CreatePRForm({
  cwd,
  onDone,
}: {
  cwd: string;
  onDone: () => void;
}) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [base, setBase] = useState("main");
  const [head, setHead] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const res = await nativeGitHub.listPRs(cwd);
      void res;
      // Auto-detect current branch for head
      if (window.electronAPI) {
        const br = await window.electronAPI.git.branch(cwd);
        if (br.code === 0) setHead(br.stdout.trim());
      }
    })();
  }, [cwd]);

  const handleCreate = async () => {
    if (!title.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const res = await nativeGitHub.createPR(cwd, title, body, base, head);
      if (res.code === 0) {
        onDone();
      } else {
        setError(res.stderr.split("\n")[0] || "Failed to create PR");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create PR");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="flex flex-col gap-2 p-3">
      <div className="text-xs font-semibold text-gray-300">Create Pull Request</div>
      <input
        className="rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-xs text-white placeholder-gray-500 outline-none focus:border-blue-500"
        placeholder="Title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <textarea
        className="rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-xs text-white placeholder-gray-500 outline-none focus:border-blue-500 resize-none"
        placeholder="Description (optional)"
        rows={4}
        value={body}
        onChange={(e) => setBody(e.target.value)}
      />
      <div className="flex gap-2">
        <div className="flex-1">
          <label className="text-[10px] text-gray-500">Base</label>
          <input
            className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-white outline-none focus:border-blue-500"
            value={base}
            onChange={(e) => setBase(e.target.value)}
          />
        </div>
        <div className="flex-1">
          <label className="text-[10px] text-gray-500">Head</label>
          <input
            className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-white outline-none focus:border-blue-500"
            value={head}
            onChange={(e) => setHead(e.target.value)}
          />
        </div>
      </div>
      {error && <div className="text-xs text-red-400">{error}</div>}
      <div className="flex gap-2">
        <button
          className="flex-1 rounded bg-green-700 py-1.5 text-xs text-white hover:bg-green-600 disabled:opacity-40 flex items-center justify-center gap-1"
          onClick={handleCreate}
          disabled={creating || !title.trim()}
        >
          {creating && <Loader2 size={11} className="animate-spin" />}
          Create PR
        </button>
        <button
          className="rounded bg-gray-700 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-600"
          onClick={onDone}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// ─── Main Component ─────────────────────────────────────────────────────

export default function GitHubPanel() {
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);
  const cwd = pinnedRoots[0] ?? "";

  const [tab, setTab] = useState<"prs" | "issues">("prs");
  const [loading, setLoading] = useState(false);
  const [ghAvailable, setGhAvailable] = useState<boolean | null>(null);
  const [prs, setPrs] = useState<GhPR[]>([]);
  const [issues, setIssues] = useState<GhIssue[]>([]);
  const [selectedPR, setSelectedPR] = useState<GhPRDetail | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const checkGh = useCallback(async () => {
    if (!cwd) return;
    const res = await nativeGitHub.checkAvailable(cwd);
    setGhAvailable(res);
  }, [cwd]);

  const loadPRs = useCallback(async () => {
    if (!cwd) return;
    setLoading(true);
    setError(null);
    try {
      const res = await nativeGitHub.listPRs(cwd);
      const parsed = parseJsonResult<GhPR[]>(res);
      if (parsed) {
        setPrs(parsed);
      } else if (res.stderr) {
        setError(res.stderr.split("\n")[0]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load PRs");
    } finally {
      setLoading(false);
    }
  }, [cwd]);

  const loadIssues = useCallback(async () => {
    if (!cwd) return;
    setLoading(true);
    setError(null);
    try {
      const res = await nativeGitHub.listIssues(cwd);
      const parsed = parseJsonResult<GhIssue[]>(res);
      if (parsed) {
        setIssues(parsed);
      } else if (res.stderr) {
        setError(res.stderr.split("\n")[0]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load issues");
    } finally {
      setLoading(false);
    }
  }, [cwd]);

  const loadPRDetail = useCallback(async (number: number) => {
    if (!cwd) return;
    setLoading(true);
    try {
      const res = await nativeGitHub.getPR(cwd, number);
      const parsed = parseJsonResult<GhPRDetail>(res);
      if (parsed) {
        setSelectedPR(parsed);
      }
    } finally {
      setLoading(false);
    }
  }, [cwd]);

  const refresh = useCallback(() => {
    setSelectedPR(null);
    setShowCreate(false);
    if (tab === "prs") loadPRs();
    else loadIssues();
  }, [tab, loadPRs, loadIssues]);

  useEffect(() => {
    checkGh();
  }, [checkGh]);

  useEffect(() => {
    if (ghAvailable) refresh();
  }, [ghAvailable, tab]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!cwd) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-gray-500">
        Open a folder to view GitHub
      </div>
    );
  }

  if (ghAvailable === false) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
        <AlertCircle size={24} className="text-gray-600" />
        <p className="text-xs text-gray-400">GitHub CLI not found</p>
        <p className="text-[10px] text-gray-500">
          Install <code className="text-gray-300">gh</code> and run <code className="text-gray-300">gh auth login</code>
        </p>
      </div>
    );
  }

  if (selectedPR) {
    return (
      <PRDetailView
        pr={selectedPR}
        cwd={cwd}
        onBack={() => setSelectedPR(null)}
        onRefresh={refresh}
      />
    );
  }

  if (showCreate) {
    return <CreatePRForm cwd={cwd} onDone={refresh} />;
  }

  const headerBtn = "rounded p-1 text-gray-500 hover:bg-gray-700/60 hover:text-gray-300 transition-colors";
  const tabCls = (active: boolean) =>
    `px-2 py-1 text-[11px] font-medium rounded transition-colors ${active ? "bg-gray-700 text-gray-200" : "text-gray-500 hover:text-gray-300"}`;

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-gray-900 text-gray-300">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-800 px-3 py-1.5">
        <div className="flex items-center gap-1">
          <button className={tabCls(tab === "prs")} onClick={() => setTab("prs")}>
            <GitPullRequest size={11} className="inline mr-0.5 -mt-0.5" />
            Pull Requests
          </button>
          <button className={tabCls(tab === "issues")} onClick={() => setTab("issues")}>
            <AlertCircle size={11} className="inline mr-0.5 -mt-0.5" />
            Issues
          </button>
        </div>
        <div className="flex items-center gap-1">
          {tab === "prs" && (
            <button className={headerBtn} title="Create PR" onClick={() => setShowCreate(true)}>
              <Plus size={14} />
            </button>
          )}
          <button className={headerBtn} title="Refresh" onClick={refresh} disabled={loading}>
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {error && (
        <div className="mx-2 mt-1.5 rounded bg-red-900/40 px-2.5 py-1 text-xs text-red-300">{error}</div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-700 [&::-webkit-scrollbar]:w-1.5">
        {loading && (tab === "prs" ? prs : issues).length === 0 && (
          <div className="flex items-center gap-2 px-3 py-4 text-xs text-gray-500">
            <Loader2 size={14} className="animate-spin" /> Loading…
          </div>
        )}

        {tab === "prs" && !loading && prs.length === 0 && (
          <div className="px-3 py-4 text-center text-xs text-gray-500">No pull requests</div>
        )}

        {tab === "prs" && prs.map((pr) => (
          <PRItem key={pr.number} pr={pr} onClick={() => loadPRDetail(pr.number)} />
        ))}

        {tab === "issues" && !loading && issues.length === 0 && (
          <div className="px-3 py-4 text-center text-xs text-gray-500">No issues</div>
        )}

        {tab === "issues" && issues.map((issue) => (
          <IssueItem key={issue.number} issue={issue} />
        ))}
      </div>
    </div>
  );
}

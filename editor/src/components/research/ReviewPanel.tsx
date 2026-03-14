import React, { useState, useMemo, useCallback } from "react";
import { Plus, RefreshCw, X, ChevronDown, ChevronUp } from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ReviewComment {
  id: string;
  severity: "major" | "minor" | "editorial";
  section: string;
  comment: string;
  suggestion?: string;
  status: "pending" | "accepted" | "dismissed";
  round: number;
}

const SEVERITY_COLORS: Record<
  ReviewComment["severity"],
  string
> = {
  major: "text-red-400 bg-red-900/20 border-red-800/30",
  minor: "text-yellow-400 bg-yellow-900/20 border-yellow-800/30",
  editorial: "text-blue-400 bg-blue-900/20 border-blue-800/30",
};

// ---------------------------------------------------------------------------
// AddReviewForm
// ---------------------------------------------------------------------------

function AddReviewForm({
  round,
  onAdd,
  onClose,
}: {
  round: number;
  onAdd: (c: ReviewComment) => void;
  onClose: () => void;
}) {
  const [severity, setSeverity] = useState<ReviewComment["severity"]>("minor");
  const [section, setSection] = useState("");
  const [comment, setComment] = useState("");
  const [suggestion, setSuggestion] = useState("");

  const submit = useCallback(() => {
    if (!comment.trim()) return;
    onAdd({
      id: `rev-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      severity,
      section: section || "(general)",
      comment: comment.trim(),
      suggestion: suggestion.trim() || undefined,
      status: "pending",
      round,
    });
    onClose();
  }, [severity, section, comment, suggestion, round, onAdd, onClose]);

  return (
    <div className="px-3 py-2 border-b border-gray-800 bg-gray-900/60">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] font-semibold text-gray-400">
          New Comment — Round {round}
        </span>
        <button
          onClick={onClose}
          className="p-0.5 text-gray-500 hover:text-gray-300"
        >
          <X size={12} />
        </button>
      </div>

      <div className="flex gap-1.5 mb-2">
        {(["major", "minor", "editorial"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setSeverity(s)}
            className={`text-[9px] px-1.5 py-0.5 rounded border ${
              severity === s
                ? SEVERITY_COLORS[s]
                : "border-gray-700 text-gray-500"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      <input
        value={section}
        onChange={(e) => setSection(e.target.value)}
        placeholder="Section (e.g. Methodology)"
        className="w-full mb-1.5 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-[11px] text-gray-200 placeholder-gray-600"
      />
      <textarea
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        placeholder="Comment..."
        className="w-full mb-1.5 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-[11px] text-gray-200 placeholder-gray-600 resize-none h-16"
      />
      <textarea
        value={suggestion}
        onChange={(e) => setSuggestion(e.target.value)}
        placeholder="Suggestion (optional)"
        className="w-full mb-2 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-[11px] text-gray-200 placeholder-gray-600 resize-none h-12"
      />
      <button
        onClick={submit}
        disabled={!comment.trim()}
        className="w-full py-1.5 text-[10px] bg-green-700/40 text-green-300 rounded hover:bg-green-700/60 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Add Comment
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ReviewCommentItem
// ---------------------------------------------------------------------------

function ReviewCommentItem({
  comment,
  onUpdate,
}: {
  comment: ReviewComment;
  onUpdate: (u: Partial<ReviewComment>) => void;
}) {
  return (
    <div
      className={`px-3 py-2 border-b border-gray-800/30 ${
        comment.status !== "pending" ? "opacity-50" : ""
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <span
          className={`text-[9px] px-1.5 py-0.5 rounded border ${SEVERITY_COLORS[comment.severity]}`}
        >
          {comment.severity}
        </span>
        <span className="text-[10px] text-gray-500">
          § {comment.section}
        </span>
        {comment.status !== "pending" && (
          <span
            className={`text-[9px] ml-auto ${
              comment.status === "accepted"
                ? "text-green-500"
                : "text-gray-600"
            }`}
          >
            {comment.status}
          </span>
        )}
      </div>

      <p className="text-xs text-gray-300 leading-relaxed">
        {comment.comment}
      </p>

      {comment.suggestion && (
        <p className="text-[11px] text-green-400/70 mt-1 italic">
          Suggestion: {comment.suggestion}
        </p>
      )}

      {comment.status === "pending" && (
        <div className="flex gap-1.5 mt-2">
          <button
            onClick={() => onUpdate({ status: "accepted" })}
            className="text-[10px] px-2 py-0.5 bg-green-800/30 text-green-400 rounded hover:bg-green-800/50"
          >
            Accept
          </button>
          <button
            onClick={() => onUpdate({ status: "dismissed" })}
            className="text-[10px] px-2 py-0.5 bg-gray-800 text-gray-400 rounded hover:bg-gray-700"
          >
            Dismiss
          </button>
          <button className="text-[10px] px-2 py-0.5 bg-gray-800 text-gray-400 rounded hover:bg-gray-700">
            Reply
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// IterationTracker — shows trend across review rounds
// ---------------------------------------------------------------------------

function IterationTracker({
  rounds,
}: {
  rounds: Record<number, ReviewComment[]>;
}) {
  const entries = Object.entries(rounds);
  if (entries.length === 0) return null;

  return (
    <div className="px-3 py-1.5 border-b border-gray-800/50 flex items-center gap-2 text-[10px] text-gray-500 overflow-x-auto">
      {entries.map(([round, comments], i) => {
        const major = comments.filter(
          (c) => c.severity === "major",
        ).length;
        const minor = comments.filter(
          (c) => c.severity === "minor",
        ).length;
        return (
          <React.Fragment key={round}>
            {i > 0 && <span className="text-gray-700">→</span>}
            <span>
              R{round}: {major}M, {minor}m
            </span>
          </React.Fragment>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ReviewPanel (default export)
// ---------------------------------------------------------------------------

export default function ReviewPanel() {
  const [reviews, setReviews] = useState<ReviewComment[]>([]);
  const [activeRound, setActiveRound] = useState(1);
  const [showAddReview, setShowAddReview] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  const rounds = useMemo(() => {
    const grouped: Record<number, ReviewComment[]> = {};
    for (const r of reviews) {
      (grouped[r.round] ??= []).push(r);
    }
    return grouped;
  }, [reviews]);

  const roundKeys = useMemo(
    () => Object.keys(rounds).map(Number).sort((a, b) => a - b),
    [rounds],
  );

  const maxRound = roundKeys.length > 0 ? roundKeys[roundKeys.length - 1] : 1;

  const addComment = useCallback((c: ReviewComment) => {
    setReviews((prev) => [...prev, c]);
  }, []);

  const updateComment = useCallback(
    (id: string, updates: Partial<ReviewComment>) => {
      setReviews((prev) =>
        prev.map((c) => (c.id === id ? { ...c, ...updates } : c)),
      );
    },
    [],
  );

  const requestNewRound = useCallback(() => {
    const next = maxRound + 1;
    setActiveRound(next);
  }, [maxRound]);

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-3 py-2 border-b border-gray-800">
        <div className="flex items-center justify-between mb-2">
          <button
            onClick={() => setCollapsed((c) => !c)}
            className="flex items-center gap-1 text-xs font-semibold text-gray-300"
          >
            {collapsed ? (
              <ChevronDown size={12} />
            ) : (
              <ChevronUp size={12} />
            )}
            Reviews
          </button>
          <div className="flex gap-1">
            <button
              onClick={() => setShowAddReview(true)}
              className="p-1 text-gray-400 hover:text-green-400"
              title="Add comment"
            >
              <Plus size={14} />
            </button>
            <button
              onClick={requestNewRound}
              className="p-1 text-gray-400 hover:text-purple-400"
              title="Request review round"
            >
              <RefreshCw size={14} />
            </button>
          </div>
        </div>

        {!collapsed && roundKeys.length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {roundKeys.map((r) => {
              const comments = rounds[r];
              const majorCount = comments.filter(
                (c) => c.severity === "major",
              ).length;
              const minorCount = comments.filter(
                (c) => c.severity === "minor",
              ).length;
              return (
                <button
                  key={r}
                  onClick={() => setActiveRound(r)}
                  className={`px-2 py-0.5 text-[10px] rounded ${
                    activeRound === r
                      ? "bg-blue-600/30 text-blue-300"
                      : "text-gray-500 hover:bg-gray-800"
                  }`}
                >
                  Round {r}
                  <span className="ml-1 text-gray-600">
                    {majorCount}M {minorCount}m
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Add-review form */}
      {showAddReview && (
        <AddReviewForm
          round={activeRound}
          onAdd={addComment}
          onClose={() => setShowAddReview(false)}
        />
      )}

      {/* Iteration tracker */}
      {!collapsed && <IterationTracker rounds={rounds} />}

      {/* Comments list */}
      <div className="flex-1 overflow-y-auto">
        {collapsed ? null : (
          <>
            {(rounds[activeRound] ?? []).map((comment) => (
              <ReviewCommentItem
                key={comment.id}
                comment={comment}
                onUpdate={(u) => updateComment(comment.id, u)}
              />
            ))}
            {(rounds[activeRound] ?? []).length === 0 && (
              <div className="p-4 text-xs text-gray-600 text-center">
                No reviews for this round yet.
                <br />
                Click{" "}
                <button
                  onClick={() => setShowAddReview(true)}
                  className="text-green-500 hover:underline"
                >
                  + Add comment
                </button>{" "}
                or{" "}
                <button
                  onClick={requestNewRound}
                  className="text-purple-400 hover:underline"
                >
                  Request review round
                </button>{" "}
                to start.
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

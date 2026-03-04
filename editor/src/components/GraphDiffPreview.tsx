import { useState, useMemo } from "react";
import { Plus, Minus, Pencil, X, Check } from "lucide-react";
import type { GraphDiff, NodeDiff, EdgeDiff, FieldDiff } from "../lib/graphDiff";
import { isEmptyDiff } from "../lib/graphDiff";

interface GraphDiffPreviewProps {
  diff: GraphDiff;
  onApplyAll: () => void;
  onApplySelected: (selectedIndices: number[]) => void;
  onReject: () => void;
  onClose: () => void;
  /** When false, hides "Apply Selected" (partial apply not yet supported). Default true. */
  allowPartialApply?: boolean;
  /** When true, disables Apply/Reject buttons (e.g. during API call). */
  disabled?: boolean;
  /** Error message from a failed apply (shown inside modal with Try again). */
  applyError?: string | null;
  /** Mutation source tag (e.g. "debug-fix") for visual badge. */
  mutationSource?: string | null;
}

type FlatItem =
  | { kind: "node"; diff: NodeDiff }
  | { kind: "edge"; diff: EdgeDiff };

function buildFlatList(diff: GraphDiff): FlatItem[] {
  const items: FlatItem[] = [];
  for (const n of diff.addedNodes) items.push({ kind: "node", diff: n });
  for (const n of diff.removedNodes) items.push({ kind: "node", diff: n });
  for (const n of diff.modifiedNodes) items.push({ kind: "node", diff: n });
  for (const e of diff.addedEdges) items.push({ kind: "edge", diff: e });
  for (const e of diff.removedEdges) items.push({ kind: "edge", diff: e });
  for (const e of diff.modifiedEdges) items.push({ kind: "edge", diff: e });
  return items;
}

function formatValue(v: unknown): string {
  if (v === undefined || v === null) return "—";
  if (typeof v === "string") return v.length > 80 ? v.slice(0, 77) + "…" : v;
  return JSON.stringify(v);
}

function TypeBadge({ type }: { type: string }) {
  return (
    <span className="ml-2 px-1.5 py-0.5 text-[10px] font-medium rounded bg-gray-100 text-gray-500">
      {type}
    </span>
  );
}

function FieldDiffRow({ fd }: { fd: FieldDiff }) {
  return (
    <div className="flex items-start gap-2 text-xs font-mono mt-1">
      <span className="text-gray-400 min-w-[90px] shrink-0">{fd.field}:</span>
      <span className="text-red-500 line-through break-all">
        {formatValue(fd.oldValue)}
      </span>
      <span className="text-gray-300">→</span>
      <span className="text-green-600 break-all">
        {formatValue(fd.newValue)}
      </span>
    </div>
  );
}

function NodeCard({
  diff,
  checked,
  onToggle,
}: {
  diff: NodeDiff;
  checked: boolean;
  onToggle: () => void;
}) {
  const border =
    diff.status === "added"
      ? "border-green-500"
      : diff.status === "removed"
        ? "border-red-500"
        : "border-amber-500";

  return (
    <div className={`border-l-4 ${border} p-3 rounded-lg bg-white shadow-sm mb-2 flex gap-3`}>
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        className="mt-0.5 accent-indigo-500"
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-center">
          <span className="text-sm font-medium text-gray-800 truncate">
            {diff.name}
          </span>
          <TypeBadge type={diff.nodeType} />
        </div>
        {diff.fieldDiffs?.map((fd) => (
          <FieldDiffRow key={fd.field} fd={fd} />
        ))}
      </div>
    </div>
  );
}

function EdgeRow({
  diff,
  checked,
  onToggle,
}: {
  diff: EdgeDiff;
  checked: boolean;
  onToggle: () => void;
}) {
  const color =
    diff.status === "added"
      ? "text-green-600"
      : diff.status === "removed"
        ? "text-red-500"
        : "text-amber-600";

  return (
    <div className="flex items-start gap-2 py-1.5 px-2 rounded hover:bg-gray-50 text-xs">
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        className="mt-0.5 accent-indigo-500"
      />
      <span className={`font-mono ${color} break-all`}>{diff.edgeKey}</span>
      <TypeBadge type={diff.edgeType} />
      {diff.fieldDiffs?.map((fd) => (
        <FieldDiffRow key={fd.field} fd={fd} />
      ))}
    </div>
  );
}

function SectionHeader({
  icon,
  label,
  count,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  count: number;
  color: string;
}) {
  if (count === 0) return null;
  return (
    <div className="flex items-center gap-2 mt-4 mb-2 first:mt-0">
      <span className={`w-2 h-2 rounded-full ${color}`} />
      <span className="text-sm font-semibold text-gray-700 flex items-center gap-1">
        {icon} {label}
      </span>
      <span className="text-xs text-gray-400">({count})</span>
    </div>
  );
}

export default function GraphDiffPreview({
  diff,
  onApplyAll,
  onApplySelected,
  onReject,
  onClose,
  allowPartialApply = true,
  disabled = false,
  applyError = null,
  mutationSource = null,
}: GraphDiffPreviewProps) {
  const flatList = useMemo(() => buildFlatList(diff), [diff]);
  const [checked, setChecked] = useState<boolean[]>(() =>
    flatList.map(() => true),
  );

  const allChecked = checked.every(Boolean);
  const someUnchecked = checked.some((c) => !c);

  const toggle = (i: number) =>
    setChecked((prev) => {
      const next = [...prev];
      next[i] = !next[i];
      return next;
    });

  const selectedIndices = checked
    .map((c, i) => (c ? i : -1))
    .filter((i) => i >= 0);

  if (isEmptyDiff(diff)) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
        <div className="bg-white rounded-xl shadow-2xl max-w-sm w-full p-6 text-center">
          <p className="text-sm text-gray-500 mb-4">No changes proposed</p>
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    );
  }

  const nodeAddedStart = 0;
  const nodeRemovedStart = nodeAddedStart + diff.addedNodes.length;
  const nodeModifiedStart = nodeRemovedStart + diff.removedNodes.length;
  const edgeAddedStart = nodeModifiedStart + diff.modifiedNodes.length;
  const edgeRemovedStart = edgeAddedStart + diff.addedEdges.length;
  const edgeModifiedStart = edgeRemovedStart + diff.removedEdges.length;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="bg-white rounded-xl shadow-2xl max-w-2xl w-full max-h-[80vh] flex flex-col">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between shrink-0">
          <div>
            <h2 className="text-base font-semibold text-gray-800 flex items-center gap-2">
              Proposed Changes
              {mutationSource === "debug-fix" && (
                <span className="px-1.5 py-0.5 text-[10px] font-semibold rounded bg-amber-100 text-amber-700 border border-amber-300">
                  debug-fix
                </span>
              )}
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">{diff.summary}</p>
          </div>
          <button
            onClick={onClose}
            disabled={disabled}
            className="p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <X size={16} />
          </button>
        </div>

        {applyError && (
          <div className="mx-5 mt-3 px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex items-center justify-between gap-2 shrink-0">
            <span className="flex-1">{applyError}</span>
            <button
              onClick={onApplyAll}
              disabled={disabled}
              className="text-xs font-medium text-red-600 hover:text-red-800 underline disabled:opacity-50"
            >
              Try again
            </button>
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-5 py-3">
          <SectionHeader
            icon={<Plus size={13} />}
            label="Added"
            count={diff.addedNodes.length}
            color="bg-green-500"
          />
          {diff.addedNodes.map((n, i) => (
            <NodeCard
              key={n.nodeId}
              diff={n}
              checked={checked[nodeAddedStart + i]}
              onToggle={() => toggle(nodeAddedStart + i)}
            />
          ))}

          <SectionHeader
            icon={<Minus size={13} />}
            label="Removed"
            count={diff.removedNodes.length}
            color="bg-red-500"
          />
          {diff.removedNodes.map((n, i) => (
            <NodeCard
              key={n.nodeId}
              diff={n}
              checked={checked[nodeRemovedStart + i]}
              onToggle={() => toggle(nodeRemovedStart + i)}
            />
          ))}

          <SectionHeader
            icon={<Pencil size={13} />}
            label="Modified"
            count={diff.modifiedNodes.length}
            color="bg-amber-500"
          />
          {diff.modifiedNodes.map((n, i) => (
            <NodeCard
              key={n.nodeId}
              diff={n}
              checked={checked[nodeModifiedStart + i]}
              onToggle={() => toggle(nodeModifiedStart + i)}
            />
          ))}

          {(diff.addedEdges.length > 0 ||
            diff.removedEdges.length > 0 ||
            diff.modifiedEdges.length > 0) && (
            <div className="mt-4 pt-3 border-t border-gray-100">
              <p className="text-xs font-semibold text-gray-500 mb-2">Edges</p>

              {diff.addedEdges.length > 0 && (
                <>
                  <SectionHeader
                    icon={<Plus size={13} />}
                    label="Added"
                    count={diff.addedEdges.length}
                    color="bg-green-500"
                  />
                  {diff.addedEdges.map((e, i) => (
                    <EdgeRow
                      key={e.edgeKey}
                      diff={e}
                      checked={checked[edgeAddedStart + i]}
                      onToggle={() => toggle(edgeAddedStart + i)}
                    />
                  ))}
                </>
              )}

              {diff.removedEdges.length > 0 && (
                <>
                  <SectionHeader
                    icon={<Minus size={13} />}
                    label="Removed"
                    count={diff.removedEdges.length}
                    color="bg-red-500"
                  />
                  {diff.removedEdges.map((e, i) => (
                    <EdgeRow
                      key={e.edgeKey}
                      diff={e}
                      checked={checked[edgeRemovedStart + i]}
                      onToggle={() => toggle(edgeRemovedStart + i)}
                    />
                  ))}
                </>
              )}

              {diff.modifiedEdges.length > 0 && (
                <>
                  <SectionHeader
                    icon={<Pencil size={13} />}
                    label="Modified"
                    count={diff.modifiedEdges.length}
                    color="bg-amber-500"
                  />
                  {diff.modifiedEdges.map((e, i) => (
                    <EdgeRow
                      key={e.edgeKey}
                      diff={e}
                      checked={checked[edgeModifiedStart + i]}
                      onToggle={() => toggle(edgeModifiedStart + i)}
                    />
                  ))}
                </>
              )}
            </div>
          )}
        </div>

        <div className="px-5 py-3 border-t border-gray-100 flex items-center justify-end gap-2 shrink-0">
          <button
            onClick={onReject}
            disabled={disabled}
            className="px-3 py-1.5 text-xs font-medium text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Reject
          </button>
          {allowPartialApply && someUnchecked && (
            <button
              onClick={() => onApplySelected(selectedIndices)}
              disabled={disabled}
              className="px-3 py-1.5 text-xs font-medium text-indigo-600 bg-white border border-indigo-300 rounded-lg hover:bg-indigo-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <span className="flex items-center gap-1">
                <Check size={13} />
                Apply Selected ({selectedIndices.length})
              </span>
            </button>
          )}
          <button
            onClick={onApplyAll}
            disabled={disabled}
            className="px-3 py-1.5 text-xs font-medium text-white bg-indigo-500 rounded-lg hover:bg-indigo-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <span className="flex items-center gap-1">
              <Check size={13} />
              {allChecked ? "Apply All" : "Apply All"}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}

import { useState } from "react";
import { ArrowRight, X, GraduationCap, Code, BarChart3, PenTool, Workflow } from "lucide-react";
import { useAppStore, type AppMode } from "../store/useAppStore";

interface EscalationSuggestion {
  targetMode: AppMode;
  reason: string;
}

const MODE_META: Record<string, { icon: typeof Code; label: string; color: string }> = {
  research: { icon: GraduationCap, label: "Research", color: "from-emerald-500 to-teal-600" },
  development: { icon: Code, label: "Development", color: "from-blue-500 to-cyan-600" },
  analytics: { icon: BarChart3, label: "Analytics", color: "from-amber-500 to-orange-600" },
  content: { icon: PenTool, label: "Content", color: "from-pink-500 to-rose-600" },
  operations: { icon: Workflow, label: "Operations", color: "from-violet-500 to-purple-600" },
};

export function detectEscalation(userMessage: string, assistantMessage?: string): EscalationSuggestion | null {
  const lower = userMessage.toLowerCase();

  if (/\b(paper|literature|review|cite|citation|academic|journal|arxiv|pubmed)\b/.test(lower)) {
    return { targetMode: "research", reason: "This looks like a research task" };
  }
  if (/\b(debug|stack\s*trace|error|exception|traceback|fix.*bug|code\s*review)\b/.test(lower) &&
      /\b(file|function|class|module|import|def |const |let |var )\b/.test(lower)) {
    return { targetMode: "development", reason: "This looks like a development task" };
  }
  if (/\b(csv|dataset|chart|plot|graph|regression|correlation|distribution|histogram|scatter)\b/.test(lower)) {
    return { targetMode: "analytics", reason: "This involves data analysis" };
  }
  if (/\b(blog\s*post|article|copy|newsletter|marketing|social\s*media|draft|press\s*release)\b/.test(lower)) {
    return { targetMode: "content", reason: "This is a content creation task" };
  }
  if (/\b(workflow|pipeline|automate|schedule|trigger|multi.?step|orchestrat)\b/.test(lower)) {
    return { targetMode: "operations", reason: "This could use a multi-step workflow" };
  }

  if (assistantMessage) {
    if (/created? (a |the )?workflow/i.test(assistantMessage) || /pipeline.*\d+ (step|node)/i.test(assistantMessage)) {
      return { targetMode: "operations", reason: "A workflow was created — manage it in Operations" };
    }
  }

  return null;
}

interface Props {
  suggestion: EscalationSuggestion;
  onDismiss: () => void;
}

export default function EscalationBanner({ suggestion, onDismiss }: Props) {
  const [dismissed, setDismissed] = useState(false);
  const setMode = useAppStore((s) => s.setMode);
  const meta = MODE_META[suggestion.targetMode];

  if (dismissed || !meta) return null;

  const Icon = meta.icon;

  return (
    <div className="flex items-center gap-3 my-3 px-4 py-3 bg-gradient-to-r from-gray-50 to-gray-100 border border-gray-200 rounded-xl animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div className={`w-8 h-8 rounded-lg bg-gradient-to-br ${meta.color} flex items-center justify-center flex-shrink-0`}>
        <Icon size={16} className="text-white" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-gray-700 font-medium">{suggestion.reason}</p>
        <p className="text-xs text-gray-400">Switch to {meta.label} mode for a specialized workspace</p>
      </div>
      <button
        onClick={() => setMode(suggestion.targetMode)}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-gray-800 hover:bg-gray-900 rounded-lg transition-colors flex-shrink-0"
      >
        Open {meta.label}
        <ArrowRight size={12} />
      </button>
      <button
        onClick={() => { setDismissed(true); onDismiss(); }}
        className="text-gray-300 hover:text-gray-500 transition-colors flex-shrink-0"
      >
        <X size={14} />
      </button>
    </div>
  );
}

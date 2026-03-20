import React from "react";

const S = 16;

const icons: Record<string, React.JSX.Element> = {
  llm_operator: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <path d="M8 1l1.5 3.5L13 6l-3.5 1.5L8 11 6.5 7.5 3 6l3.5-1.5L8 1z" fill="currentColor" />
      <path d="M3 11l1 2 2 1-2 1-1 2-1-2-2-1 2-1 1-2z" fill="currentColor" opacity={0.6} />
    </svg>
  ),
  tool_operator: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <path d="M10.5 2a3 3 0 00-2.83 4L3 10.5V13h2.5L10 8.33A3 3 0 1010.5 2z" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  code_operator: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <path d="M5 4L1.5 8 5 12M11 4l3.5 4L11 12" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  gate_if_else: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <path d="M8 2v4M8 6L4 10M8 6l4 4M4 10v4M12 10v4" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  gate_while: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <path d="M12 8a4 4 0 11-8 0 4 4 0 018 0z" stroke="currentColor" strokeWidth={1.5} />
      <path d="M12 5l1 3h-3" fill="currentColor" />
    </svg>
  ),
  for_each: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <rect x="2" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth={1.3} />
      <rect x="9" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth={1.3} />
      <rect x="2" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth={1.3} />
      <rect x="9" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth={1.3} />
    </svg>
  ),
  parallel_subagents: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <path d="M2 4h4v4H2V4z" stroke="currentColor" strokeWidth={1.3} fill="none" />
      <path d="M10 4h4v4h-4V4z" stroke="currentColor" strokeWidth={1.3} fill="none" />
      <path d="M6 8l4-2" stroke="currentColor" strokeWidth={1} strokeLinecap="round" />
      <path d="M6 8v4h4V8" stroke="currentColor" strokeWidth={1.3} fill="none" />
    </svg>
  ),
  orchestrator: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="2.2" stroke="currentColor" strokeWidth={1.3} />
      <circle cx="3" cy="4" r="1.5" stroke="currentColor" strokeWidth={1.2} />
      <circle cx="13" cy="4" r="1.5" stroke="currentColor" strokeWidth={1.2} />
      <circle cx="8" cy="13" r="1.5" stroke="currentColor" strokeWidth={1.2} />
      <path d="M4.3 4.8L6.3 6.7M11.7 4.8L9.7 6.7M8 10.2v1.3" stroke="currentColor" strokeWidth={1.1} strokeLinecap="round" />
    </svg>
  ),
  reduce: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <path d="M3 3l5 5M13 3L8 8M8 8v6" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  router: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeWidth={1.3} />
      <path d="M8 2.5v11M2.5 8h11" stroke="currentColor" strokeWidth={1} />
      <path d="M8 2.5L12 8 8 13.5" stroke="currentColor" strokeWidth={1} strokeLinejoin="round" />
    </svg>
  ),
  human_in_the_loop: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="5" r="2.5" stroke="currentColor" strokeWidth={1.3} />
      <path d="M3 14c0-2.8 2.2-5 5-5s5 2.2 5 5" stroke="currentColor" strokeWidth={1.3} strokeLinecap="round" />
    </svg>
  ),
  human: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="5" r="2.5" stroke="currentColor" strokeWidth={1.3} />
      <path d="M3 14c0-2.8 2.2-5 5-5s5 2.2 5 5" stroke="currentColor" strokeWidth={1.3} strokeLinecap="round" />
    </svg>
  ),
  gate: (
    <svg width={S} height={S} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M12 2 L22 12 L12 22 L2 12 Z" />
    </svg>
  ),
  rag_operator: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <circle cx="6" cy="6" r="4" stroke="currentColor" strokeWidth={1.3} />
      <path d="M9 9l4.5 4.5" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" />
      <path d="M5 4v4M3 6h4" stroke="currentColor" strokeWidth={1} strokeLinecap="round" opacity={0.6} />
    </svg>
  ),
  validator: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <path d="M8 1L2 4v4c0 3.3 2.6 6.4 6 7 3.4-.6 6-3.7 6-7V4L8 1z" stroke="currentColor" strokeWidth={1.3} strokeLinejoin="round" />
      <path d="M5.5 8l2 2 3.5-4" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  reflection: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <path d="M6 3.5a3 3 0 015 2.3c0 1-.4 1.6-1.1 2.2-.5.4-.9.8-.9 1.5H7.9c0-1 .5-1.7 1.2-2.3.5-.4.9-.8.9-1.5A1.8 1.8 0 006 5.5" stroke="currentColor" strokeWidth={1.3} strokeLinecap="round" strokeLinejoin="round" />
      <path d="M7.7 11.5h1.6M7.5 13h2" stroke="currentColor" strokeWidth={1.3} strokeLinecap="round" />
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth={1.1} opacity={0.35} />
    </svg>
  ),
  goal_loop: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeWidth={1.3} />
      <path d="M8 4.2v3.8l2.6 1.6" stroke="currentColor" strokeWidth={1.3} strokeLinecap="round" strokeLinejoin="round" />
      <path d="M12 4.5l1 2.7H10.3" fill="currentColor" />
    </svg>
  ),
  vote: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <path d="M4 5.5l1.2 2.3L7.7 8 6 9.6l.4 2.4L4 10.8 1.6 12l.4-2.4L.3 8l2.5-.2L4 5.5z" fill="currentColor" opacity={0.85} />
      <path d="M11.5 3.5l.9 1.8 2 .2-1.4 1.3.3 1.9-1.8-.9-1.8.9.3-1.9-1.4-1.3 2-.2.9-1.8z" fill="currentColor" opacity={0.55} />
    </svg>
  ),
  agent_team: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <circle cx="5" cy="5.2" r="1.6" stroke="currentColor" strokeWidth={1.2} />
      <circle cx="11" cy="5.2" r="1.6" stroke="currentColor" strokeWidth={1.2} />
      <circle cx="8" cy="10.8" r="1.6" stroke="currentColor" strokeWidth={1.2} />
      <path d="M6.2 6.2L7.1 8M9.8 6.2L8.9 8M6.7 11h2.6" stroke="currentColor" strokeWidth={1.1} strokeLinecap="round" />
    </svg>
  ),
  composite: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <rect x="2" y="5" width="12" height="8" rx="1.5" stroke="currentColor" strokeWidth={1.3} />
      <rect x="4" y="2" width="8" height="8" rx="1.5" stroke="currentColor" strokeWidth={1.3} opacity={0.5} />
    </svg>
  ),
  input: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <path d="M4 3v10l9-5-9-5z" fill="currentColor" />
    </svg>
  ),
};

export function NodeIcon({ type, className }: { type: string; className?: string }) {
  return <span className={`inline-flex items-center ${className ?? ""}`}>{icons[type] ?? null}</span>;
}

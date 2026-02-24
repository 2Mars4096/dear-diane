const S = 16;

const icons: Record<string, JSX.Element> = {
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
  if_else: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <path d="M8 2v4M8 6L4 10M8 6l4 4M4 10v4M12 10v4" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  while_loop: (
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
  composite: (
    <svg width={S} height={S} viewBox="0 0 16 16" fill="none">
      <rect x="2" y="5" width="12" height="8" rx="1.5" stroke="currentColor" strokeWidth={1.3} />
      <rect x="4" y="2" width="8" height="8" rx="1.5" stroke="currentColor" strokeWidth={1.3} opacity={0.5} />
    </svg>
  ),
};

export function NodeIcon({ type, className }: { type: string; className?: string }) {
  return <span className={`inline-flex items-center ${className ?? ""}`}>{icons[type] ?? null}</span>;
}

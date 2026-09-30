import { useEffect, useState, type ReactNode } from "react";

/** Mount on first visit, then preserve drafts, scroll, and running work when hidden. */
export function PersistentPanel({ active, name, children }: {
  active: boolean;
  name: string;
  children: ReactNode;
}) {
  const [visited, setVisited] = useState(active);
  useEffect(() => { if (active) setVisited(true); }, [active]);
  return <div className="wb-side-body" role="region" aria-label={name} hidden={!active}>
    {(active || visited) && children}
  </div>;
}

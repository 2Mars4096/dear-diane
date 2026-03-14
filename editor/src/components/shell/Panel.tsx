/**
 * Generic panel component with header, collapse toggle, and content area.
 * Low-dependency building block for mode-specific panel layouts.
 */
import { useState } from "react";
import { ChevronRight } from "lucide-react";

export interface PanelProps {
  id: string;
  title: string;
  icon?: React.ReactNode;
  actions?: React.ReactNode;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  /** Controlled collapse state (use with layout persistence) */
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
  children: React.ReactNode;
  className?: string;
  headerClassName?: string;
}

export default function Panel({
  id,
  title,
  icon,
  actions,
  collapsible = true,
  defaultCollapsed = false,
  collapsed: controlledCollapsed,
  onCollapsedChange,
  children,
  className,
  headerClassName,
}: PanelProps) {
  const [internalCollapsed, setInternalCollapsed] = useState(defaultCollapsed);
  const isControlled = controlledCollapsed !== undefined;
  const collapsed = isControlled ? controlledCollapsed : internalCollapsed;

  const setCollapsed = (value: boolean | ((v: boolean) => boolean)) => {
    const next = typeof value === "function" ? value(collapsed) : value;
    if (isControlled) {
      onCollapsedChange?.(next);
    } else {
      setInternalCollapsed(next);
    }
  };

  return (
    <div
      className={`flex flex-col overflow-hidden ${className ?? ""} ${collapsed ? "h-[32px]" : ""}`}
      data-panel-id={id}
    >
      <div
        className={`flex items-center justify-between h-[32px] shrink-0 px-3 bg-gray-50 dark:bg-[#252526] border-b border-gray-200 dark:border-gray-800 select-none ${headerClassName ?? ""}`}
      >
        <div className="flex items-center gap-2">
          {collapsible && (
            <button
              type="button"
              onClick={() => setCollapsed((v) => !v)}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
              aria-expanded={!collapsed}
              aria-label={collapsed ? "Expand panel" : "Collapse panel"}
            >
              <ChevronRight
                size={12}
                className={`transition-transform duration-150 ${collapsed ? "" : "rotate-90"}`}
              />
            </button>
          )}
          {icon && <span className="text-gray-400">{icon}</span>}
          <span className="text-xs font-semibold text-gray-600 dark:text-gray-300 uppercase tracking-wider">
            {title}
          </span>
        </div>
        {actions && !collapsed && (
          <div className="flex items-center gap-1">{actions}</div>
        )}
      </div>
      {!collapsed && (
        <div className="flex-1 min-h-0 overflow-auto">{children}</div>
      )}
    </div>
  );
}

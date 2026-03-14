/**
 * Configurable panel layout system using Allotment for resizable splits.
 * Supports left, center, right, and bottom panels. Any mode can use this
 * framework; Code mode keeps its own Allotment setup.
 */
import { Allotment } from "allotment";

export type PanelPosition = "left" | "center" | "right" | "bottom";

export interface PanelSlot {
  id: string;
  position: PanelPosition;
  defaultSize?: number;
  minSize?: number;
  maxSize?: number;
  visible?: boolean;
  content: React.ReactNode;
}

export interface PanelLayoutConfig {
  slots: PanelSlot[];
  bottomHeight?: number;
}

export default function PanelLayout({ config }: { config: PanelLayoutConfig }) {
  const leftSlots = config.slots.filter(
    (s) => s.position === "left" && s.visible !== false,
  );
  const centerSlots = config.slots.filter(
    (s) => s.position === "center" && s.visible !== false,
  );
  const rightSlots = config.slots.filter(
    (s) => s.position === "right" && s.visible !== false,
  );
  const bottomSlots = config.slots.filter(
    (s) => s.position === "bottom" && s.visible !== false,
  );

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <Allotment vertical>
        <Allotment.Pane minSize={200}>
          <Allotment>
            {leftSlots.length > 0 && (
              <Allotment.Pane
                preferredSize={leftSlots[0].defaultSize ?? 250}
                minSize={leftSlots[0].minSize ?? 150}
                maxSize={leftSlots[0].maxSize ?? 500}
              >
                <div className="h-full flex flex-col">
                  {leftSlots.map((s) => (
                    <div key={s.id} className="flex-1 min-h-0">{s.content}</div>
                  ))}
                </div>
              </Allotment.Pane>
            )}

            <Allotment.Pane>
              <div className="h-full flex flex-col">
                {centerSlots.map((s) => (
                  <div key={s.id} className="flex-1 min-h-0">{s.content}</div>
                ))}
              </div>
            </Allotment.Pane>

            {rightSlots.length > 0 && (
              <Allotment.Pane
                preferredSize={rightSlots[0].defaultSize ?? 300}
                minSize={rightSlots[0].minSize ?? 200}
                maxSize={rightSlots[0].maxSize ?? 600}
              >
                <div className="h-full flex flex-col">
                  {rightSlots.map((s) => (
                    <div key={s.id} className="flex-1 min-h-0">{s.content}</div>
                  ))}
                </div>
              </Allotment.Pane>
            )}
          </Allotment>
        </Allotment.Pane>

        {bottomSlots.length > 0 && (
          <Allotment.Pane preferredSize={config.bottomHeight ?? 200} minSize={50}>
            <div className="h-full flex flex-col">
              {bottomSlots.map((s) => (
                <div key={s.id} className="flex-1 min-h-0">{s.content}</div>
              ))}
            </div>
          </Allotment.Pane>
        )}
      </Allotment>
    </div>
  );
}

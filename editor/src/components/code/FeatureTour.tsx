import { useState, useEffect, useCallback, useRef } from "react";
import { ChevronRight, X, Lightbulb } from "lucide-react";

interface TourStep {
  title: string;
  description: string;
  selector: string;
  position: "bottom" | "right" | "left" | "top";
}

const STEPS: TourStep[] = [
  {
    title: "Mode Bar",
    description: "Switch between Research and Code modes to match your workflow.",
    selector: "[data-tour='mode-bar']",
    position: "bottom",
  },
  {
    title: "AI Chat",
    description: "Ask AI to help write, explain, or refactor your code.",
    selector: "[title='AI Chat (⌘J)']",
    position: "right",
  },
  {
    title: "File Explorer",
    description: "Browse and manage your project files in the sidebar.",
    selector: "[title='Explorer (Cmd+B)']",
    position: "right",
  },
  {
    title: "Terminal",
    description: "Run commands and see output. Toggle with ⌘` anytime.",
    selector: "[data-tour='bottom-panel'], [role='tablist']",
    position: "top",
  },
];

interface FeatureTourProps {
  onComplete: () => void;
}

export default function FeatureTour({ onComplete }: FeatureTourProps) {
  const [step, setStep] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [entering, setEntering] = useState(true);
  const tooltipRef = useRef<HTMLDivElement>(null);

  const currentStep = STEPS[step];

  const measureTarget = useCallback(() => {
    if (!currentStep) return;
    const el = document.querySelector(currentStep.selector);
    if (el) {
      setRect(el.getBoundingClientRect());
    } else {
      setRect(null);
    }
  }, [currentStep]);

  useEffect(() => {
    measureTarget();
    setEntering(true);
    const timer = setTimeout(() => setEntering(false), 50);
    const resizeObs = () => measureTarget();
    window.addEventListener("resize", resizeObs);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("resize", resizeObs);
    };
  }, [step, measureTarget]);

  const handleNext = () => {
    if (step < STEPS.length - 1) {
      setStep(step + 1);
    } else {
      onComplete();
    }
  };

  const tooltipStyle = (): React.CSSProperties => {
    if (!rect) return { top: "50%", left: "50%", transform: "translate(-50%, -50%)" };

    const pad = 12;
    const pos = currentStep?.position ?? "bottom";

    switch (pos) {
      case "bottom":
        return {
          top: rect.bottom + pad,
          left: rect.left + rect.width / 2,
          transform: "translateX(-50%)",
        };
      case "right":
        return {
          top: rect.top + rect.height / 2,
          left: rect.right + pad,
          transform: "translateY(-50%)",
        };
      case "left":
        return {
          top: rect.top + rect.height / 2,
          left: rect.left - pad,
          transform: "translate(-100%, -50%)",
        };
      case "top":
        return {
          top: rect.top - pad,
          left: rect.left + rect.width / 2,
          transform: "translate(-50%, -100%)",
        };
    }
  };

  return (
    <div className="fixed inset-0 z-[9999]">
      {/* Backdrop with cutout */}
      <svg className="absolute inset-0 w-full h-full" style={{ pointerEvents: "none" }}>
        <defs>
          <mask id="tour-mask">
            <rect width="100%" height="100%" fill="white" />
            {rect && (
              <rect
                x={rect.left - 4}
                y={rect.top - 4}
                width={rect.width + 8}
                height={rect.height + 8}
                rx={6}
                fill="black"
              />
            )}
          </mask>
        </defs>
        <rect
          width="100%"
          height="100%"
          fill="rgba(0,0,0,0.6)"
          mask="url(#tour-mask)"
          style={{ pointerEvents: "auto" }}
          onClick={handleNext}
        />
      </svg>

      {/* Highlight ring */}
      {rect && (
        <div
          className="absolute rounded-md ring-2 ring-blue-500 ring-offset-2 ring-offset-transparent pointer-events-none transition-all duration-300"
          style={{
            top: rect.top - 4,
            left: rect.left - 4,
            width: rect.width + 8,
            height: rect.height + 8,
          }}
        />
      )}

      {/* Tooltip */}
      <div
        ref={tooltipRef}
        className={`absolute z-10 w-72 rounded-xl border border-[#3c3c3c] bg-[#1e1e1e] p-4 shadow-2xl transition-all duration-300 ${
          entering ? "opacity-0 scale-95" : "opacity-100 scale-100"
        }`}
        style={tooltipStyle()}
      >
        <div className="flex items-start justify-between gap-2 mb-2">
          <div className="flex items-center gap-2">
            <Lightbulb size={14} className="text-yellow-400 shrink-0" />
            <h3 className="text-sm font-semibold text-gray-200">{currentStep?.title}</h3>
          </div>
          <button
            onClick={onComplete}
            className="rounded p-0.5 text-gray-500 transition-colors hover:bg-[#3c3c3c] hover:text-gray-300"
          >
            <X size={12} />
          </button>
        </div>

        <p className="text-xs leading-relaxed text-gray-400 mb-4">
          {currentStep?.description}
        </p>

        <div className="flex items-center justify-between">
          <div className="flex gap-1">
            {STEPS.map((_, i) => (
              <div
                key={i}
                className={`h-1.5 w-1.5 rounded-full transition-colors ${
                  i === step ? "bg-blue-400" : i < step ? "bg-blue-400/40" : "bg-gray-600"
                }`}
              />
            ))}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onComplete}
              className="px-2 py-1 text-[11px] text-gray-500 transition-colors hover:text-gray-300"
            >
              Skip
            </button>
            <button
              onClick={handleNext}
              className="flex items-center gap-1 rounded-md bg-blue-600 px-3 py-1 text-[11px] font-medium text-white transition-colors hover:bg-blue-500"
            >
              {step < STEPS.length - 1 ? "Next" : "Done"}
              {step < STEPS.length - 1 && <ChevronRight size={10} />}
            </button>
          </div>
        </div>

        <div className="mt-2 text-center text-[10px] text-gray-600">
          {step + 1} / {STEPS.length}
        </div>
      </div>
    </div>
  );
}

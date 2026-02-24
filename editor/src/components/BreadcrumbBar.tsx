import { useGraphStore } from "../store/useGraphStore";

export default function BreadcrumbBar() {
  const layerStack = useGraphStore((s) => s.layerStack);
  const jumpToLayer = useGraphStore((s) => s.jumpToLayer);
  const drillOut = useGraphStore((s) => s.drillOut);

  if (layerStack.length === 0) return null;

  return (
    <div className="flex items-center gap-1 px-3 py-1.5 bg-white border-b border-gray-200 text-[11px] text-gray-500">
      <button
        onClick={() => jumpToLayer(-1)}
        className="hover:text-indigo-600 hover:underline font-medium"
      >
        Root
      </button>

      {layerStack.map((layer, i) => {
        const isLast = i === layerStack.length - 1;
        return (
          <span key={layer.graphKey} className="flex items-center gap-1">
            <span className="text-gray-300">/</span>
            {isLast ? (
              <span className="text-gray-700 font-medium">
                {layer.nodeName || layer.graphKey}
              </span>
            ) : (
              <button
                onClick={() => jumpToLayer(i)}
                className="hover:text-indigo-600 hover:underline"
              >
                {layer.nodeName || layer.graphKey}
              </button>
            )}
          </span>
        );
      })}

      <div className="flex-1" />
      <button
        onClick={drillOut}
        className="text-[10px] text-indigo-500 hover:underline"
      >
        &#x2190; Back
      </button>
    </div>
  );
}

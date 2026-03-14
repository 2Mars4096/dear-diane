import { useState, useEffect, useRef, useCallback } from "react";
import {
  Search,
  X,
  Star,
  Download,
  Trash2,
  RefreshCw,
  Check,
  Upload,
  Filter,
  Loader2,
  AlertCircle,
  Puzzle,
  ToggleLeft,
  ToggleRight,
  ArrowLeft,
  Brain,
  Plug,
  Play,
  Square,
  Plus,
  Circle,
  Power,
  Settings,
  RotateCcw,
  Package,
  Cpu,
  HeartPulse,
  TrendingUp,
  GitBranch,
  Cog,
  Cloud,
  CloudOff,
} from "lucide-react";
import { useMarketplaceStore } from "../../store/useMarketplaceStore";
import { marketplaceManager } from "../../lib/marketplace/marketplaceManager";
import type { MarketplaceSettings } from "../../lib/marketplace/marketplaceManager";
import { useCodeStore } from "../../store/useCodeStore";
import { nativeDialog, nativeExtension, nativeSkills, nativeMcp } from "../../lib/electronBridge";
import type { SkillEntry } from "../../lib/electronBridge";
import { detectProjectType } from "../../lib/workspaceIntelligence";
import type { MarketplaceItem, MarketplaceItemDetail, InstalledItem } from "../../lib/marketplace/types";
import { classifyExtension, tierBadgeColor, tierLabel, type TierResult } from "../../lib/marketplace/compatibilityTier";
import { shouldPreferNative } from "../../lib/marketplace/nativeFallback";
import { getAllEssentials, getEssentialsForProject, type EssentialExtension } from "../../lib/marketplace/curatedEssentials";
import { nativeExtensionHost } from "../../lib/electronBridge";
import { DanSkillsAdapter } from "../../lib/marketplace/skillsAdapter";
import { Info } from "lucide-react";

const CATEGORIES = ["Languages", "Themes", "Linters", "Formatters", "Debuggers", "Snippets", "Other"];

function StarRating({ rating }: { rating?: number }) {
  if (rating == null) return null;
  const full = Math.round(rating);
  return (
    <span className="flex items-center gap-0.5 text-yellow-400">
      {Array.from({ length: 5 }, (_, i) => (
        <Star key={i} size={10} fill={i < full ? "currentColor" : "none"} />
      ))}
      <span className="text-[10px] text-gray-400 ml-0.5">{rating.toFixed(1)}</span>
    </span>
  );
}

function formatCount(n?: number): string {
  if (n == null) return "";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function CompatBadge({ tier }: { tier: TierResult }) {
  const [showTooltip, setShowTooltip] = useState(false);
  return (
    <span className="relative inline-flex">
      <span
        className={`text-[9px] px-1 py-0.5 rounded font-medium cursor-default ${tierBadgeColor(tier.tier)}`}
        onMouseEnter={() => setShowTooltip(true)}
        onMouseLeave={() => setShowTooltip(false)}
      >
        {tierLabel(tier.tier)}
      </span>
      {showTooltip && (tier.supportedFeatures.length > 0 || tier.unsupportedFeatures.length > 0) && (
        <div className="absolute left-0 top-full mt-1 z-50 bg-[#252526] border border-[#3c3c3c] rounded shadow-lg p-2 min-w-[180px] text-[10px]">
          {tier.supportedFeatures.length > 0 && (
            <div className="mb-1">
              <span className="text-green-300 font-medium">Supported:</span>
              <ul className="mt-0.5">
                {tier.supportedFeatures.map((f) => (
                  <li key={f} className="text-gray-300 pl-2">+ {f}</li>
                ))}
              </ul>
            </div>
          )}
          {tier.unsupportedFeatures.length > 0 && (
            <div>
              <span className="text-red-300 font-medium">Unsupported:</span>
              <ul className="mt-0.5">
                {tier.unsupportedFeatures.map((f) => (
                  <li key={f} className="text-gray-400 pl-2">- {f}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </span>
  );
}

function NativeBadge({ reason }: { reason: string }) {
  const [showTooltip, setShowTooltip] = useState(false);
  return (
    <span className="relative inline-flex">
      <span
        className="text-[9px] px-1 py-0.5 rounded font-medium bg-blue-800/60 text-blue-200 cursor-default flex items-center gap-0.5"
        onMouseEnter={() => setShowTooltip(true)}
        onMouseLeave={() => setShowTooltip(false)}
      >
        <Info size={8} /> Native
      </span>
      {showTooltip && (
        <div className="absolute left-0 top-full mt-1 z-50 bg-[#252526] border border-[#3c3c3c] rounded shadow-lg p-2 min-w-[160px] text-[10px] text-gray-300">
          {reason}
        </div>
      )}
    </span>
  );
}

function SkeletonCard() {
  return (
    <div className="p-3 border-b border-[#3c3c3c] animate-pulse">
      <div className="flex gap-2.5">
        <div className="w-10 h-10 rounded bg-[#3c3c3c] shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="h-3.5 bg-[#3c3c3c] rounded w-3/4 mb-1.5" />
          <div className="h-2.5 bg-[#3c3c3c] rounded w-1/2 mb-1.5" />
          <div className="h-2.5 bg-[#3c3c3c] rounded w-full" />
        </div>
      </div>
    </div>
  );
}

function ExtensionCard({
  item,
  isInstalled,
  isInstalling,
  compatTier,
  nativeInfo,
  onSelect,
  onInstall,
  onUninstall,
}: {
  item: MarketplaceItem;
  isInstalled: boolean;
  isInstalling: boolean;
  compatTier?: TierResult;
  nativeInfo?: { prefer: boolean; reason: string };
  onSelect: () => void;
  onInstall: () => void;
  onUninstall: () => void;
}) {
  return (
    <button
      className="w-full text-left p-3 border-b border-[#3c3c3c] hover:bg-[#2a2d2e] transition-colors cursor-pointer"
      onClick={onSelect}
    >
      <div className="flex gap-2.5">
        {item.icon ? (
          <img
            src={item.icon}
            alt=""
            className="w-10 h-10 rounded shrink-0 bg-[#3c3c3c] object-cover"
            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
          />
        ) : (
          <div className="w-10 h-10 rounded bg-[#3c3c3c] shrink-0 flex items-center justify-center">
            <Puzzle size={18} className="text-gray-500" />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-[13px] font-medium text-white truncate">
              {item.displayName}
            </span>
            <span className="text-[11px] text-gray-500">v{item.version}</span>
            {compatTier && <CompatBadge tier={compatTier} />}
            {nativeInfo?.prefer && <NativeBadge reason={nativeInfo.reason} />}
          </div>
          <div className="text-[11px] text-gray-400 truncate">{item.publisher}</div>
          <div className="text-[11px] text-gray-500 truncate mt-0.5 leading-snug">
            {item.description}
          </div>
          <div className="flex items-center gap-3 mt-1">
            <StarRating rating={item.rating} />
            {item.downloadCount != null && (
              <span className="text-[10px] text-gray-500 flex items-center gap-0.5">
                <Download size={9} /> {formatCount(item.downloadCount)}
              </span>
            )}
          </div>
        </div>
        <div className="shrink-0 flex items-start pt-0.5">
          {isInstalling ? (
            <Loader2 size={14} className="text-[#007acc] animate-spin" />
          ) : isInstalled ? (
            <button
              className="text-[10px] px-2 py-0.5 rounded bg-[#3c3c3c] text-gray-300 hover:bg-red-900/40 hover:text-red-300 transition-colors"
              onClick={(e) => { e.stopPropagation(); onUninstall(); }}
              title="Uninstall"
            >
              <Trash2 size={11} />
            </button>
          ) : (
            <button
              className="text-[10px] px-2 py-1 rounded bg-[#007acc] text-white hover:bg-[#0098ff] transition-colors font-medium"
              onClick={(e) => { e.stopPropagation(); onInstall(); }}
            >
              Install
            </button>
          )}
        </div>
      </div>
    </button>
  );
}

function InstalledCard({
  item,
  onSelect,
  onToggle,
  onUninstall,
  isUninstalling,
}: {
  item: InstalledItem;
  onSelect: () => void;
  onToggle: () => void;
  onUninstall: () => void;
  isUninstalling: boolean;
}) {
  return (
    <button
      className="w-full text-left p-3 border-b border-[#3c3c3c] hover:bg-[#2a2d2e] transition-colors cursor-pointer"
      onClick={onSelect}
    >
      <div className="flex gap-2.5">
        {item.icon ? (
          <img
            src={item.icon}
            alt=""
            className="w-10 h-10 rounded shrink-0 bg-[#3c3c3c] object-cover"
            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
          />
        ) : (
          <div className="w-10 h-10 rounded bg-[#3c3c3c] shrink-0 flex items-center justify-center">
            <Puzzle size={18} className="text-gray-500" />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className={`text-[13px] font-medium truncate ${item.enabled ? "text-white" : "text-gray-500"}`}>
              {item.displayName}
            </span>
            <span className="text-[11px] text-gray-500">v{item.version}</span>
          </div>
          <div className="text-[11px] text-gray-400 truncate">{item.publisher}</div>
          <div className="text-[11px] text-gray-500 truncate mt-0.5">{item.description}</div>
        </div>
        <div className="shrink-0 flex items-center gap-1.5">
          <button
            className="p-1 rounded hover:bg-[#3c3c3c] transition-colors"
            onClick={(e) => { e.stopPropagation(); onToggle(); }}
            title={item.enabled ? "Disable" : "Enable"}
          >
            {item.enabled ? (
              <ToggleRight size={16} className="text-[#007acc]" />
            ) : (
              <ToggleLeft size={16} className="text-gray-500" />
            )}
          </button>
          {isUninstalling ? (
            <Loader2 size={14} className="text-gray-400 animate-spin" />
          ) : (
            <button
              className="p-1 rounded hover:bg-red-900/40 transition-colors"
              onClick={(e) => { e.stopPropagation(); onUninstall(); }}
              title="Uninstall"
            >
              <Trash2 size={12} className="text-gray-400 hover:text-red-300" />
            </button>
          )}
        </div>
      </div>
    </button>
  );
}

function DetailView({
  item,
  isInstalled,
  isInstalling,
  onBack,
  onInstall,
  onUninstall,
}: {
  item: MarketplaceItemDetail;
  isInstalled: boolean;
  isInstalling: boolean;
  onBack: () => void;
  onInstall: () => void;
  onUninstall: () => void;
}) {
  return (
    <div className="h-full flex flex-col">
      <button
        className="flex items-center gap-1 px-3 py-2 text-[11px] text-gray-400 hover:text-white transition-colors border-b border-[#3c3c3c]"
        onClick={onBack}
      >
        <ArrowLeft size={12} /> Back to list
      </button>
      <div className="flex-1 overflow-y-auto">
        <div className="p-3 border-b border-[#3c3c3c]">
          <div className="flex gap-3">
            {item.icon ? (
              <img src={item.icon} alt="" className="w-14 h-14 rounded bg-[#3c3c3c] object-cover"
                onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
              />
            ) : (
              <div className="w-14 h-14 rounded bg-[#3c3c3c] flex items-center justify-center">
                <Puzzle size={24} className="text-gray-500" />
              </div>
            )}
            <div className="flex-1 min-w-0">
              <h3 className="text-[14px] font-semibold text-white">{item.displayName}</h3>
              <div className="text-[11px] text-gray-400">{item.publisher}</div>
              <div className="flex items-center gap-3 mt-1">
                <StarRating rating={item.rating} />
                {item.downloadCount != null && (
                  <span className="text-[10px] text-gray-500 flex items-center gap-0.5">
                    <Download size={9} /> {formatCount(item.downloadCount)}
                  </span>
                )}
                <span className="text-[10px] text-gray-500">v{item.version}</span>
              </div>
            </div>
          </div>
          <p className="text-[12px] text-gray-300 mt-2 leading-relaxed">{item.description}</p>
          <div className="flex items-center gap-2 mt-3">
            {isInstalling ? (
              <button className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-[#007acc]/60 text-white text-[11px] font-medium" disabled>
                <Loader2 size={12} className="animate-spin" /> Installing...
              </button>
            ) : isInstalled ? (
              <button
                className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-red-900/40 text-red-300 text-[11px] font-medium hover:bg-red-900/60 transition-colors"
                onClick={onUninstall}
              >
                <Trash2 size={12} /> Uninstall
              </button>
            ) : (
              <button
                className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-[#007acc] text-white text-[11px] font-medium hover:bg-[#0098ff] transition-colors"
                onClick={onInstall}
              >
                <Download size={12} /> Install
              </button>
            )}
          </div>
          {(item.license || item.repository) && (
            <div className="flex items-center gap-3 mt-2 text-[10px] text-gray-500">
              {item.license && <span>License: {item.license}</span>}
              {item.repository && (
                <a
                  href={item.repository}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[#007acc] hover:underline"
                >
                  Repository
                </a>
              )}
            </div>
          )}
          {item.categories.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {item.categories.map((c) => (
                <span key={c} className="text-[10px] px-1.5 py-0.5 rounded bg-[#3c3c3c] text-gray-400">
                  {c}
                </span>
              ))}
            </div>
          )}
        </div>
        {item.readme && (
          <div className="p-3">
            <div
              className="text-[12px] text-gray-300 leading-relaxed prose prose-invert prose-sm max-w-none
                [&_h1]:text-[15px] [&_h1]:font-bold [&_h1]:text-white [&_h1]:mt-4 [&_h1]:mb-2
                [&_h2]:text-[14px] [&_h2]:font-semibold [&_h2]:text-white [&_h2]:mt-3 [&_h2]:mb-1.5
                [&_h3]:text-[13px] [&_h3]:font-medium [&_h3]:text-gray-200 [&_h3]:mt-2 [&_h3]:mb-1
                [&_p]:mb-2 [&_ul]:mb-2 [&_ol]:mb-2 [&_li]:mb-0.5
                [&_code]:bg-[#3c3c3c] [&_code]:px-1 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-[11px]
                [&_pre]:bg-[#1e1e1e] [&_pre]:p-3 [&_pre]:rounded [&_pre]:overflow-x-auto [&_pre]:mb-3
                [&_a]:text-[#007acc] [&_a]:hover:underline
                [&_img]:max-w-full [&_img]:rounded"
              dangerouslySetInnerHTML={{ __html: basicMarkdownToHtml(item.readme) }}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function basicMarkdownToHtml(md: string): string {
  return md
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" />')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\n\n/g, "</p><p>")
    .replace(/^(.+)$/gm, (line) => {
      if (line.startsWith("<h") || line.startsWith("<p") || line.startsWith("<img") || line.startsWith("</p")) return line;
      return line;
    })
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    .replace(/(<li>.*<\/li>\n?)+/g, "<ul>$&</ul>");
}

function RecommendedSection({
  onInstall,
  installedIds,
}: {
  onInstall: (id: string) => void;
  installedIds: Set<string>;
}) {
  const [recs, setRecs] = useState<string[]>([]);
  const [essentials, setEssentials] = useState<EssentialExtension[]>([]);
  const pinnedRoots = useCodeStore((s) => s.pinnedRoots);

  useEffect(() => {
    const root = pinnedRoots[0];
    if (!root) {
      setEssentials(getAllEssentials().filter((e) => !installedIds.has(e.id)).slice(0, 6));
      return;
    }
    detectProjectType(root).then((det) => {
      setRecs(det.recommendedExtensions.filter((r) => !installedIds.has(r)));
      const projectEssentials = getEssentialsForProject(det.type ?? "");
      const allEssentials = [...projectEssentials, ...getAllEssentials()];
      const seen = new Set<string>();
      const deduped: EssentialExtension[] = [];
      for (const e of allEssentials) {
        if (!seen.has(e.id) && !installedIds.has(e.id)) {
          seen.add(e.id);
          deduped.push(e);
        }
      }
      setEssentials(deduped.slice(0, 8));
    });
  }, [pinnedRoots, installedIds]);

  if (recs.length === 0 && essentials.length === 0) return null;

  return (
    <div className="border-b border-[#3c3c3c]">
      {essentials.length > 0 && (
        <>
          <div className="px-3 py-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wide flex items-center gap-1.5">
            <Star size={10} className="text-yellow-400" /> Curated Essentials
          </div>
          {essentials.map((ext) => (
            <div
              key={ext.id}
              className="px-3 py-1.5 flex items-center justify-between hover:bg-[#2a2d2e]"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-[12px] text-gray-200 font-medium">{ext.name}</span>
                  <span className="text-[9px] px-1 py-0.5 rounded bg-[#3c3c3c] text-gray-400">{ext.category}</span>
                </div>
                <div className="text-[10px] text-gray-500 truncate">{ext.description}</div>
              </div>
              <button
                className="text-[10px] px-2 py-0.5 rounded bg-[#007acc] text-white hover:bg-[#0098ff] transition-colors shrink-0 ml-2"
                onClick={() => onInstall(ext.name)}
              >
                Search
              </button>
            </div>
          ))}
        </>
      )}
      {recs.length > 0 && (
        <>
          <div className="px-3 py-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wide">
            Recommended for this project
          </div>
          {recs.map((name) => (
            <div
              key={name}
              className="px-3 py-1.5 flex items-center justify-between hover:bg-[#2a2d2e]"
            >
              <span className="text-[12px] text-gray-300">{name}</span>
              <button
                className="text-[10px] px-2 py-0.5 rounded bg-[#007acc] text-white hover:bg-[#0098ff] transition-colors"
                onClick={() => onInstall(name)}
              >
                Search
              </button>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

/* ── Skills Tab ── */

function SkillCard({
  skill,
  onToggle,
  onDelete,
  onSelect,
}: {
  skill: SkillEntry;
  onToggle: () => void;
  onDelete: () => void;
  onSelect: () => void;
}) {
  return (
    <button
      className="w-full text-left p-3 border-b border-[#3c3c3c] hover:bg-[#2a2d2e] transition-colors cursor-pointer"
      onClick={onSelect}
    >
      <div className="flex gap-2.5">
        <div className="w-10 h-10 rounded bg-purple-900/30 shrink-0 flex items-center justify-center">
          <Brain size={18} className="text-purple-400" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className={`text-[13px] font-medium truncate ${skill.enabled ? "text-white" : "text-gray-500"}`}>
              {skill.name}
            </span>
            {skill.version && (
              <span className="text-[11px] text-gray-500">v{skill.version}</span>
            )}
          </div>
          <div className="text-[11px] text-gray-400 truncate">{skill.author ?? "local"}</div>
          <div className="text-[11px] text-gray-500 truncate mt-0.5">{skill.description}</div>
          {skill.triggers && skill.triggers.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              {skill.triggers.slice(0, 4).map((t) => (
                <span key={t} className="text-[9px] px-1 py-0.5 rounded bg-purple-900/20 text-purple-300">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="shrink-0 flex items-center gap-1.5">
          <button
            className="p-1 rounded hover:bg-[#3c3c3c] transition-colors"
            onClick={(e) => { e.stopPropagation(); onToggle(); }}
            title={skill.enabled ? "Disable" : "Enable"}
          >
            {skill.enabled ? (
              <ToggleRight size={16} className="text-purple-400" />
            ) : (
              <ToggleLeft size={16} className="text-gray-500" />
            )}
          </button>
          <button
            className="p-1 rounded hover:bg-red-900/40 transition-colors"
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            title="Delete skill"
          >
            <Trash2 size={12} className="text-gray-400 hover:text-red-300" />
          </button>
        </div>
      </div>
    </button>
  );
}

function SkillHubToggle({
  enabled,
  onToggle,
}: {
  enabled: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="flex items-center justify-between px-3 py-1.5 border-b border-[#3c3c3c] bg-[#1e1e1e]">
      <button
        className="flex items-center gap-1.5 text-[11px] text-gray-400 hover:text-white transition-colors"
        onClick={onToggle}
      >
        {enabled ? (
          <Cloud size={12} className="text-blue-400" />
        ) : (
          <CloudOff size={12} className="text-gray-500" />
        )}
        <span className={enabled ? "text-blue-300" : "text-gray-500"}>
          Skill Hub
        </span>
      </button>
      <button
        className="p-0.5 rounded hover:bg-[#3c3c3c] transition-colors"
        onClick={onToggle}
        title={enabled ? "Disable Skill Hub" : "Enable Skill Hub"}
      >
        {enabled ? (
          <ToggleRight size={16} className="text-blue-400" />
        ) : (
          <ToggleLeft size={16} className="text-gray-500" />
        )}
      </button>
    </div>
  );
}

function getSkillsAdapter(): DanSkillsAdapter | null {
  const reg = marketplaceManager.getRegistry("dan-skills");
  return reg instanceof DanSkillsAdapter ? reg : null;
}

function SkillsTabContent({
  onSelectSkill,
}: {
  onSelectSkill: (skill: SkillEntry) => void;
}) {
  const [skills, setSkills] = useState<SkillEntry[]>([]);
  const [hubResults, setHubResults] = useState<MarketplaceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [hubEnabled, setHubEnabled] = useState(() => getSkillsAdapter()?.isHubEnabled() ?? false);
  const { error, setError } = useMarketplaceStore();

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const result = await nativeSkills.scan();
      setSkills(result);
    } catch {
      setSkills([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadHubResults = useCallback(async () => {
    const adapter = getSkillsAdapter();
    if (!adapter || !adapter.isHubEnabled()) {
      setHubResults([]);
      return;
    }
    try {
      const results = await adapter.search("");
      const localIds = new Set(skills.map((s) => s.id));
      setHubResults(results.filter((r) => !localIds.has(r.id) && r.publisher !== "local"));
    } catch {
      setHubResults([]);
    }
  }, [skills]);

  useEffect(() => { loadSkills(); }, [loadSkills]);

  useEffect(() => {
    if (hubEnabled) loadHubResults();
    else setHubResults([]);
  }, [hubEnabled, loadHubResults]);

  const handleToggleHub = useCallback(() => {
    const adapter = getSkillsAdapter();
    if (!adapter) return;
    const next = !hubEnabled;
    adapter.enableHub(next);
    setHubEnabled(next);
  }, [hubEnabled]);

  const handleToggle = useCallback(async (skillId: string) => {
    try {
      await nativeSkills.toggleEnabled(skillId);
      setSkills((prev) =>
        prev.map((s) => s.id === skillId ? { ...s, enabled: !s.enabled } : s),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Toggle failed");
    }
  }, [setError]);

  const handleDelete = useCallback(async (skillId: string) => {
    try {
      await nativeSkills.remove(skillId);
      setSkills((prev) => prev.filter((s) => s.id !== skillId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }, [setError]);

  const handleNewSkill = useCallback(async () => {
    const name = prompt("Skill name:");
    if (!name?.trim()) return;
    try {
      await nativeSkills.createTemplate(name.trim());
      await loadSkills();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    }
  }, [loadSkills, setError]);

  if (loading) {
    return <>{Array.from({ length: 3 }, (_, i) => <SkeletonCard key={i} />)}</>;
  }

  const hasContent = skills.length > 0 || hubResults.length > 0;

  if (!hasContent) {
    return (
      <>
        <SkillHubToggle enabled={hubEnabled} onToggle={handleToggleHub} />
        <div className="flex flex-col items-center justify-center py-12 text-center px-4">
          <Brain size={32} className="text-purple-500/40 mb-3" />
          <p className="text-[12px] text-gray-400 mb-1">No skills installed</p>
          <p className="text-[11px] text-gray-600 mb-4">
            Skills extend DAN with specialized knowledge and workflows.
            {!hubEnabled && " Enable Skill Hub to browse remote skills."}
          </p>
          <button
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-purple-600 text-white text-[11px] font-medium hover:bg-purple-500 transition-colors"
            onClick={handleNewSkill}
          >
            <Plus size={12} /> New Skill
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <SkillHubToggle enabled={hubEnabled} onToggle={handleToggleHub} />
      {skills.map((skill) => (
        <SkillCard
          key={skill.id}
          skill={skill}
          onToggle={() => handleToggle(skill.id)}
          onDelete={() => handleDelete(skill.id)}
          onSelect={() => onSelectSkill(skill)}
        />
      ))}
      {hubResults.length > 0 && (
        <>
          <div className="px-3 py-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wide flex items-center gap-1.5 border-t border-[#3c3c3c]">
            <Cloud size={10} className="text-blue-400" /> From Skill Hub
          </div>
          {hubResults.map((item) => (
            <button
              key={item.id}
              className="w-full text-left p-3 border-b border-[#3c3c3c] hover:bg-[#2a2d2e] transition-colors cursor-pointer"
              onClick={() => onSelectSkill({
                id: item.id,
                name: item.name,
                description: item.description,
                author: item.publisher,
                version: item.version,
                triggers: item.tags,
                path: "",
                modifiedAt: Date.now(),
                enabled: false,
              })}
            >
              <div className="flex gap-2.5">
                <div className="w-10 h-10 rounded bg-purple-900/30 shrink-0 flex items-center justify-center relative">
                  <Brain size={18} className="text-purple-400" />
                  <Cloud size={8} className="absolute -top-0.5 -right-0.5 text-blue-400" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[13px] font-medium text-white truncate">
                      {item.displayName}
                    </span>
                    <span className="text-[11px] text-gray-500">v{item.version}</span>
                  </div>
                  <div className="text-[11px] text-gray-400 truncate">{item.publisher}</div>
                  <div className="text-[11px] text-gray-500 truncate mt-0.5">{item.description}</div>
                  <div className="flex items-center gap-3 mt-1">
                    <StarRating rating={item.rating} />
                    {item.downloadCount != null && (
                      <span className="text-[10px] text-gray-500 flex items-center gap-0.5">
                        <Download size={9} /> {formatCount(item.downloadCount)}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </button>
          ))}
        </>
      )}
      <div className="border-t border-[#3c3c3c] px-2 py-1.5 flex items-center gap-1.5">
        <button
          className="flex items-center gap-1 px-2 py-1 rounded text-[11px] text-gray-400 hover:bg-[#3c3c3c] hover:text-white transition-colors"
          onClick={handleNewSkill}
        >
          <Plus size={12} /> New Skill
        </button>
        <div className="flex-1" />
        <button
          className="p-1 rounded text-gray-500 hover:text-white hover:bg-[#3c3c3c] transition-colors"
          onClick={loadSkills}
          title="Refresh skills"
        >
          <RefreshCw size={12} />
        </button>
      </div>
    </>
  );
}

/* ── MCP Tab ── */

interface McpDisplayItem {
  id: string;
  name: string;
  displayName: string;
  description: string;
  tags: string[];
  installed: boolean;
  enabled: boolean;
  status: "connected" | "disconnected" | "error";
}

function StatusDot({ status }: { status: "connected" | "disconnected" | "error" }) {
  const color =
    status === "connected" ? "bg-green-400" :
    status === "error" ? "bg-red-400" :
    "bg-gray-500";
  return (
    <span className={`inline-block w-2 h-2 rounded-full ${color}`} title={status} />
  );
}

function McpCard({
  server,
  isInstalling,
  onInstall,
  onRemove,
  onStart,
  onStop,
  onToggle,
  onSelect,
}: {
  server: McpDisplayItem;
  isInstalling: boolean;
  onInstall: () => void;
  onRemove: () => void;
  onStart: () => void;
  onStop: () => void;
  onToggle: () => void;
  onSelect: () => void;
}) {
  return (
    <button
      className="w-full text-left p-3 border-b border-[#3c3c3c] hover:bg-[#2a2d2e] transition-colors cursor-pointer"
      onClick={onSelect}
    >
      <div className="flex gap-2.5">
        <div className="w-10 h-10 rounded bg-blue-900/30 shrink-0 flex items-center justify-center">
          <Plug size={18} className="text-blue-400" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-[13px] font-medium text-white truncate">
              {server.displayName}
            </span>
            {server.installed && <StatusDot status={server.status} />}
          </div>
          <div className="text-[11px] text-gray-500 truncate mt-0.5">{server.description}</div>
          <div className="flex flex-wrap gap-1 mt-1">
            {server.tags.slice(0, 4).map((t) => (
              <span key={t} className="text-[9px] px-1 py-0.5 rounded bg-blue-900/20 text-blue-300">
                {t}
              </span>
            ))}
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-1">
          {isInstalling ? (
            <Loader2 size={14} className="text-[#007acc] animate-spin" />
          ) : server.installed ? (
            <>
              {server.status === "connected" ? (
                <button
                  className="p-1 rounded hover:bg-[#3c3c3c] transition-colors"
                  onClick={(e) => { e.stopPropagation(); onStop(); }}
                  title="Stop"
                >
                  <Square size={12} className="text-red-400" />
                </button>
              ) : (
                <button
                  className="p-1 rounded hover:bg-[#3c3c3c] transition-colors"
                  onClick={(e) => { e.stopPropagation(); onStart(); }}
                  title="Start"
                >
                  <Play size={12} className="text-green-400" />
                </button>
              )}
              <button
                className="p-1 rounded hover:bg-[#3c3c3c] transition-colors"
                onClick={(e) => { e.stopPropagation(); onToggle(); }}
                title={server.enabled ? "Disable" : "Enable"}
              >
                {server.enabled ? (
                  <Power size={12} className="text-blue-400" />
                ) : (
                  <Power size={12} className="text-gray-500" />
                )}
              </button>
              <button
                className="p-1 rounded hover:bg-red-900/40 transition-colors"
                onClick={(e) => { e.stopPropagation(); onRemove(); }}
                title="Remove"
              >
                <Trash2 size={12} className="text-gray-400 hover:text-red-300" />
              </button>
            </>
          ) : (
            <button
              className="text-[10px] px-2 py-1 rounded bg-[#007acc] text-white hover:bg-[#0098ff] transition-colors font-medium"
              onClick={(e) => { e.stopPropagation(); onInstall(); }}
            >
              Install
            </button>
          )}
        </div>
      </div>
    </button>
  );
}

function McpTabContent({
  onSelectMcp,
}: {
  onSelectMcp: (item: MarketplaceItem) => void;
}) {
  const [servers, setServers] = useState<McpDisplayItem[]>([]);
  const [loading, setLoading] = useState(true);
  const { isInstalling, setIsInstalling, error, setError, mcpStatuses, setMcpStatuses } = useMarketplaceStore();

  const loadServers = useCallback(async () => {
    setLoading(true);
    try {
      const registry = marketplaceManager.getRegistry("mcp-servers");
      if (!registry) return;
      const curated = await registry.search("");
      const installed = await nativeMcp.listInstalled();
      const statuses = await nativeMcp.status();

      const installedIds = new Set(installed.map((s) => s.id));
      const statusMap: Record<string, "connected" | "disconnected" | "error"> = {};
      for (const s of statuses) {
        statusMap[s.id] = s.status as "connected" | "disconnected" | "error";
      }
      setMcpStatuses(statusMap);

      const displayItems: McpDisplayItem[] = curated.map((c) => ({
        id: c.id,
        name: c.name,
        displayName: c.displayName,
        description: c.description,
        tags: c.tags,
        installed: installedIds.has(c.id),
        enabled: installed.find((i) => i.id === c.id)?.enabled ?? false,
        status: statusMap[c.id] ?? "disconnected",
      }));
      setServers(displayItems);
    } catch {
      setServers([]);
    } finally {
      setLoading(false);
    }
  }, [setMcpStatuses]);

  useEffect(() => { loadServers(); }, [loadServers]);

  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const statuses = await nativeMcp.status();
        const statusMap: Record<string, "connected" | "disconnected" | "error"> = {};
        for (const s of statuses) {
          statusMap[s.id] = s.status as "connected" | "disconnected" | "error";
        }
        setMcpStatuses(statusMap);
        setServers((prev) =>
          prev.map((s) => ({ ...s, status: statusMap[s.id] ?? "disconnected" })),
        );
      } catch { /* ignore */ }
    }, 30_000);
    return () => clearInterval(interval);
  }, [setMcpStatuses]);

  const handleInstall = useCallback(async (serverId: string) => {
    setIsInstalling(serverId);
    setError(null);
    try {
      const registry = marketplaceManager.getRegistry("mcp-servers");
      if (registry) await registry.install(serverId);
      await loadServers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Install failed");
    } finally {
      setIsInstalling(null);
    }
  }, [setIsInstalling, setError, loadServers]);

  const handleRemove = useCallback(async (serverId: string) => {
    setError(null);
    try {
      await nativeMcp.remove(serverId);
      await loadServers();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Remove failed");
    }
  }, [setError, loadServers]);

  const handleStart = useCallback(async (serverId: string) => {
    try {
      await nativeMcp.start(serverId);
      const statuses = await nativeMcp.status();
      const statusMap: Record<string, "connected" | "disconnected" | "error"> = {};
      for (const s of statuses) statusMap[s.id] = s.status as any;
      setMcpStatuses(statusMap);
      setServers((prev) =>
        prev.map((s) => s.id === serverId ? { ...s, status: statusMap[serverId] ?? "connected" } : s),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Start failed");
    }
  }, [setError, setMcpStatuses]);

  const handleStop = useCallback(async (serverId: string) => {
    try {
      await nativeMcp.stop(serverId);
      setServers((prev) =>
        prev.map((s) => s.id === serverId ? { ...s, status: "disconnected" } : s),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Stop failed");
    }
  }, [setError]);

  const handleToggle = useCallback(async (serverId: string) => {
    try {
      const newEnabled = await nativeMcp.toggleEnabled(serverId);
      setServers((prev) =>
        prev.map((s) => s.id === serverId ? { ...s, enabled: newEnabled, status: newEnabled ? s.status : "disconnected" } : s),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Toggle failed");
    }
  }, [setError]);

  if (loading) {
    return <>{Array.from({ length: 4 }, (_, i) => <SkeletonCard key={i} />)}</>;
  }

  return (
    <>
      {servers.map((server) => (
        <McpCard
          key={server.id}
          server={server}
          isInstalling={isInstalling === server.id}
          onInstall={() => handleInstall(server.id)}
          onRemove={() => handleRemove(server.id)}
          onStart={() => handleStart(server.id)}
          onStop={() => handleStop(server.id)}
          onToggle={() => handleToggle(server.id)}
          onSelect={() => {
            const item: MarketplaceItem = {
              id: server.id,
              name: server.name,
              displayName: server.displayName,
              description: server.description,
              publisher: "modelcontextprotocol",
              version: "1.0.0",
              categories: ["MCP Servers"],
              tags: server.tags,
              registry: "mcp-servers",
            };
            onSelectMcp(item);
          }}
        />
      ))}
      <div className="border-t border-[#3c3c3c] px-2 py-1.5 flex items-center gap-1.5">
        <div className="flex items-center gap-2 text-[10px] text-gray-500">
          <span className="flex items-center gap-1"><Circle size={6} className="text-green-400 fill-green-400" /> Connected</span>
          <span className="flex items-center gap-1"><Circle size={6} className="text-red-400 fill-red-400" /> Error</span>
          <span className="flex items-center gap-1"><Circle size={6} className="text-gray-500 fill-gray-500" /> Off</span>
        </div>
        <div className="flex-1" />
        <button
          className="p-1 rounded text-gray-500 hover:text-white hover:bg-[#3c3c3c] transition-colors"
          onClick={loadServers}
          title="Refresh MCP servers"
        >
          <RefreshCw size={12} />
        </button>
      </div>
    </>
  );
}

/* ── Recipes Tab ── */

const DOMAIN_STYLE: Record<string, { colorClass: string; Icon: typeof Package }> = {
  Operations: { colorClass: "text-emerald-400 bg-emerald-900/30", Icon: Cog },
  Finance: { colorClass: "text-blue-400 bg-blue-900/30", Icon: TrendingUp },
  Economics: { colorClass: "text-amber-400 bg-amber-900/30", Icon: GitBranch },
  "ML/AI": { colorClass: "text-purple-400 bg-purple-900/30", Icon: Cpu },
  Medicine: { colorClass: "text-red-400 bg-red-900/30", Icon: HeartPulse },
};
const DEFAULT_DOMAIN_STYLE = { colorClass: "text-gray-400 bg-gray-800", Icon: Package };

function RecipeCard({
  recipe,
  onClick,
}: {
  recipe: MarketplaceItem;
  onClick: () => void;
}) {
  const domain = recipe.categories.find((c) => c !== "Recipes") ?? "";
  const { colorClass, Icon } = DOMAIN_STYLE[domain] ?? DEFAULT_DOMAIN_STYLE;

  return (
    <div
      className="px-2 py-2 border-b border-[#2d2d2d] hover:bg-[#2a2a2a] cursor-pointer flex gap-2"
      onClick={onClick}
    >
      <div
        className={`w-8 h-8 rounded flex items-center justify-center shrink-0 ${colorClass}`}
      >
        <Icon size={14} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-[12px] text-gray-200 font-medium truncate">
            {recipe.name}
          </span>
          {domain && (
            <span className={`text-[9px] px-1.5 py-0.5 rounded ${colorClass}`}>
              {domain}
            </span>
          )}
        </div>
        <p className="text-[10px] text-gray-500 truncate mt-0.5">
          {recipe.description}
        </p>
        <div className="flex items-center gap-2 mt-1">
          {recipe.downloadCount != null && (
            <span className="text-[9px] text-gray-600">
              {recipe.downloadCount} papers
            </span>
          )}
          {recipe.rating != null && (
            <span className="text-[9px] text-yellow-500">
              <Star size={8} className="inline mr-0.5" fill="currentColor" />
              {recipe.rating}
            </span>
          )}
          <span className="text-[9px] text-gray-600">v{recipe.version}</span>
        </div>
      </div>
    </div>
  );
}

function RecipeTabContent({
  onSelectRecipe,
}: {
  onSelectRecipe: (item: MarketplaceItem) => void;
}) {
  const [recipes, setRecipes] = useState<MarketplaceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>("");

  const loadRecipes = useCallback(async () => {
    setLoading(true);
    try {
      const adapter = marketplaceManager.getRegistry("recipes");
      if (adapter) {
        const r = await adapter.search("");
        setRecipes(r);
      }
    } catch {
      setRecipes([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRecipes();
  }, [loadRecipes]);

  const filtered = filter
    ? recipes.filter(
        (r) =>
          r.tags.some((t) => t.includes(filter)) ||
          r.categories.some((c) => c.toLowerCase().includes(filter)),
      )
    : recipes;

  const domains = [
    ...new Set(
      recipes.flatMap((r) => r.categories.filter((c) => c !== "Recipes")),
    ),
  ];

  return (
    <div className="flex flex-col h-full">
      <div className="px-2 py-1.5 flex flex-wrap gap-1 border-b border-[#3c3c3c]">
        <button
          className={`text-[10px] px-2 py-0.5 rounded ${
            !filter
              ? "bg-[#007acc] text-white"
              : "bg-[#3c3c3c] text-gray-400 hover:bg-[#4c4c4c]"
          }`}
          onClick={() => setFilter("")}
        >
          All
        </button>
        {domains.map((d) => (
          <button
            key={d}
            className={`text-[10px] px-2 py-0.5 rounded ${
              filter === d.toLowerCase()
                ? "bg-[#007acc] text-white"
                : "bg-[#3c3c3c] text-gray-400 hover:bg-[#4c4c4c]"
            }`}
            onClick={() =>
              setFilter(filter === d.toLowerCase() ? "" : d.toLowerCase())
            }
          >
            {d}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">
        {loading ? (
          Array.from({ length: 3 }, (_, i) => <SkeletonCard key={i} />)
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center px-4">
            <Package size={32} className="text-gray-600 mb-3" />
            <p className="text-[12px] text-gray-500">No recipes found</p>
          </div>
        ) : (
          filtered.map((recipe) => (
            <RecipeCard
              key={recipe.id}
              recipe={recipe}
              onClick={() => onSelectRecipe(recipe)}
            />
          ))
        )}
      </div>

      <div className="border-t border-[#3c3c3c] px-2 py-1.5 flex items-center gap-1.5">
        <button
          className="flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded text-[11px] bg-[#2d2d2d] text-gray-400 hover:bg-[#007acc] hover:text-white transition-colors"
          onClick={() => {
            /* placeholder: opens recipe creation flow via Distillation tab */
          }}
        >
          <Plus size={12} /> Create New Recipe
        </button>
        <button
          className="p-1 rounded text-gray-500 hover:text-white hover:bg-[#3c3c3c] transition-colors"
          onClick={loadRecipes}
          title="Refresh recipes"
        >
          <RefreshCw size={12} />
        </button>
      </div>
    </div>
  );
}

/* ── Marketplace Settings Panel ── */

const REGISTRY_LABELS: Record<string, string> = {
  openvsx: "VS Code (Open VSX)",
  "dan-skills": "DAN Skills",
  "mcp-servers": "MCP Servers",
  recipes: "Recipe Store",
};

function MarketplaceSettingsPanel({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<MarketplaceSettings>(() =>
    marketplaceManager.getSettings(),
  );
  const [customUrl, setCustomUrl] = useState(settings.customRegistryUrl ?? "");
  const registryIds = marketplaceManager.getRegisteredIds();

  const handleToggleRegistry = (id: string) => {
    const next = {
      ...settings,
      registries: {
        ...settings.registries,
        [id]: !settings.registries[id],
      },
    };
    setSettings(next);
    marketplaceManager.saveSettings(next);
  };

  const handleUrlChange = (url: string) => {
    setCustomUrl(url);
  };

  const handleUrlBlur = () => {
    const trimmed = customUrl.trim();
    const next: MarketplaceSettings = {
      ...settings,
      customRegistryUrl: trimmed || undefined,
    };
    setSettings(next);
    marketplaceManager.saveSettings(next);
  };

  const handleReset = () => {
    marketplaceManager.resetSettings();
    setSettings(marketplaceManager.getSettings());
    setCustomUrl("");
  };

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e] text-white">
      <div className="flex items-center justify-between px-3 py-2 border-b border-[#3c3c3c]">
        <button
          className="flex items-center gap-1 text-[11px] text-gray-400 hover:text-white transition-colors"
          onClick={onClose}
        >
          <ArrowLeft size={12} /> Back
        </button>
        <span className="text-[11px] font-semibold text-gray-300">Marketplace Settings</span>
        <div className="w-12" />
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        <div>
          <h4 className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide mb-2">
            Registries
          </h4>
          {registryIds.map((id) => (
            <button
              key={id}
              className="flex items-center justify-between w-full px-2 py-2 rounded hover:bg-[#2a2d2e] transition-colors"
              onClick={() => handleToggleRegistry(id)}
            >
              <span className="text-[12px] text-gray-300">
                {REGISTRY_LABELS[id] ?? id}
              </span>
              {settings.registries[id] !== false ? (
                <ToggleRight size={18} className="text-[#007acc]" />
              ) : (
                <ToggleLeft size={18} className="text-gray-500" />
              )}
            </button>
          ))}
        </div>

        <div>
          <h4 className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide mb-2">
            Custom Open VSX URL
          </h4>
          <input
            type="text"
            value={customUrl}
            onChange={(e) => handleUrlChange(e.target.value)}
            onBlur={handleUrlBlur}
            onKeyDown={(e) => { if (e.key === "Enter") handleUrlBlur(); }}
            placeholder="https://open-vsx.org/api"
            className="w-full bg-[#3c3c3c] text-[12px] text-white px-2.5 py-1.5 rounded border border-transparent focus:border-[#007acc] outline-none placeholder-gray-500"
          />
          <p className="text-[10px] text-gray-600 mt-1">
            Override the Open VSX registry endpoint for self-hosted instances.
          </p>
        </div>

        <button
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] text-gray-400 hover:bg-[#3c3c3c] hover:text-white transition-colors"
          onClick={handleReset}
        >
          <RotateCcw size={12} /> Reset to Defaults
        </button>
      </div>
    </div>
  );
}

/* ── Main Panel ── */

const TABS = [
  ["all", "All"],
  ["vscode", "VS Code"],
  ["skills", "Skills"],
  ["mcp", "MCP"],
  ["recipes", "Recipes"],
  ["installed", "Installed"],
] as const;

export default function ExtensionsPanel() {
  const {
    searchQuery, setSearchQuery,
    activeTab, setActiveTab,
    searchResults, setSearchResults,
    installedItems, setInstalledItems,
    selectedItem, setSelectedItem,
    isSearching, setIsSearching,
    isInstalling, setIsInstalling,
    sortBy, setSortBy,
    categoryFilter, setCategoryFilter,
    error, setError,
    toggleItemEnabled,
    removeInstalledItem,
    addInstalledItem,
  } = useMarketplaceStore();

  const searchTimeout = useRef<ReturnType<typeof setTimeout>>();
  const inputRef = useRef<HTMLInputElement>(null);
  const [showSortMenu, setShowSortMenu] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [tierCache, setTierCache] = useState<Record<string, TierResult>>({});
  const [hostStatus, setHostStatus] = useState<{ running: boolean }>({ running: false });

  const installedIds = new Set(installedItems.map((i) => i.id));

  useEffect(() => {
    nativeExtensionHost.status().then(setHostStatus).catch(() => {});
  }, []);

  const loadInstalled = useCallback(async () => {
    try {
      const items = await nativeExtension.listInstalled();
      setInstalledItems(items);
    } catch {
      /* no-op in browser mode */
    }
  }, [setInstalledItems]);

  useEffect(() => {
    loadInstalled();
  }, [loadInstalled]);

  const doSearch = useCallback(
    async (query: string) => {
      if (!query.trim()) {
        setSearchResults([]);
        return;
      }
      setIsSearching(true);
      setError(null);
      try {
        const results = await marketplaceManager.searchAll(query, {
          sortBy,
          category: categoryFilter ?? undefined,
        });
        setSearchResults(results);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Search failed");
      } finally {
        setIsSearching(false);
      }
    },
    [sortBy, categoryFilter, setSearchResults, setIsSearching, setError],
  );

  const handleSearchChange = useCallback(
    (q: string) => {
      setSearchQuery(q);
      clearTimeout(searchTimeout.current);
      searchTimeout.current = setTimeout(() => doSearch(q), 500);
    },
    [setSearchQuery, doSearch],
  );

  const handleInstall = useCallback(
    async (item: MarketplaceItem) => {
      setIsInstalling(item.id);
      setError(null);
      try {
        const registry = marketplaceManager.getRegistry(item.registry);
        if (!registry) throw new Error("Registry not found");
        const installed = await registry.install(item.id);
        addInstalledItem(installed);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Install failed");
      } finally {
        setIsInstalling(null);
      }
    },
    [setIsInstalling, setError, addInstalledItem],
  );

  const handleUninstall = useCallback(
    async (itemId: string, registryId?: string) => {
      setIsInstalling(itemId);
      setError(null);
      try {
        const registry = marketplaceManager.getRegistry(registryId ?? "openvsx");
        if (registry) await registry.uninstall(itemId);
        removeInstalledItem(itemId);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Uninstall failed");
      } finally {
        setIsInstalling(null);
      }
    },
    [setIsInstalling, setError, removeInstalledItem],
  );

  const getTierForItem = useCallback(
    async (itemId: string): Promise<TierResult | undefined> => {
      if (tierCache[itemId]) return tierCache[itemId];
      try {
        const manifest = await nativeExtension.getManifest(itemId);
        if (manifest) {
          const tier = classifyExtension(manifest);
          setTierCache((prev) => ({ ...prev, [itemId]: tier }));
          return tier;
        }
      } catch { /* not installed, no manifest available */ }
      return undefined;
    },
    [tierCache],
  );

  useEffect(() => {
    for (const item of installedItems) {
      if (!tierCache[item.id]) {
        getTierForItem(item.id);
      }
    }
  }, [installedItems, tierCache, getTierForItem]);

  const handleSelectItem = useCallback(
    async (item: MarketplaceItem) => {
      try {
        const registry = marketplaceManager.getRegistry(item.registry);
        if (!registry) return;
        const detail = await registry.getDetails(item.id);
        setSelectedItem(detail);
      } catch {
        setSelectedItem({
          ...item,
          readme: undefined,
          changelog: undefined,
          repository: undefined,
          license: undefined,
        });
      }
    },
    [setSelectedItem],
  );

  const handleSelectSkill = useCallback(
    async (skill: SkillEntry) => {
      try {
        const content = await nativeSkills.readSkill(skill.path);
        setSelectedItem({
          id: skill.id,
          name: skill.name,
          displayName: skill.name,
          description: skill.description,
          publisher: skill.author ?? "local",
          version: skill.version ?? "1.0.0",
          categories: ["Skills"],
          tags: skill.triggers ?? [],
          registry: "dan-skills",
          readme: content ?? "",
        });
      } catch {
        setSelectedItem({
          id: skill.id,
          name: skill.name,
          displayName: skill.name,
          description: skill.description,
          publisher: skill.author ?? "local",
          version: skill.version ?? "1.0.0",
          categories: ["Skills"],
          tags: skill.triggers ?? [],
          registry: "dan-skills",
        });
      }
    },
    [setSelectedItem],
  );

  const handleImportVsix = useCallback(async () => {
    const files = await nativeDialog.openFile({
      filters: [{ name: "VSIX Package", extensions: ["vsix"] }],
    });
    if (!files?.length) return;
    setError(null);
    try {
      const result = await nativeExtension.importVsix(files[0]);
      if (result) {
        addInstalledItem(result);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
    }
  }, [setError, addInstalledItem]);

  const handleRecSearch = useCallback(
    (name: string) => {
      handleSearchChange(name);
      setActiveTab("all");
      inputRef.current?.focus();
    },
    [handleSearchChange, setActiveTab],
  );

  if (showSettings) {
    return <MarketplaceSettingsPanel onClose={() => setShowSettings(false)} />;
  }

  if (selectedItem) {
    return (
      <DetailView
        item={selectedItem}
        isInstalled={installedIds.has(selectedItem.id)}
        isInstalling={isInstalling === selectedItem.id}
        onBack={() => setSelectedItem(null)}
        onInstall={() => handleInstall(selectedItem)}
        onUninstall={() => handleUninstall(selectedItem.id, selectedItem.registry)}
      />
    );
  }

  const showCategoryPills = activeTab !== "installed" && activeTab !== "skills" && activeTab !== "mcp" && activeTab !== "recipes";

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e] text-white select-none">
      {/* Header */}
      <div className="px-3 pt-2 pb-1 flex items-center justify-between">
        <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide">
          Extensions
        </span>
        <div className="flex items-center gap-1.5">
          <button
            className="p-0.5 rounded text-gray-500 hover:text-white hover:bg-[#3c3c3c] transition-colors"
            onClick={() => setShowSettings(true)}
            title="Marketplace Settings"
          >
            <Settings size={13} />
          </button>
          <span
            className={`text-[9px] px-1.5 py-0.5 rounded flex items-center gap-1 ${
              hostStatus.running
                ? "bg-green-900/40 text-green-300"
                : "bg-gray-700/40 text-gray-500"
            }`}
            title={hostStatus.running ? "Extension host is running" : "Extension host is stopped"}
          >
            <span className={`inline-block w-1.5 h-1.5 rounded-full ${hostStatus.running ? "bg-green-400" : "bg-gray-500"}`} />
            Host
          </span>
        </div>
      </div>

      {/* Search */}
      <div className="px-2 pb-2">
        <div className="relative">
          <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            ref={inputRef}
            type="text"
            value={searchQuery}
            onChange={(e) => handleSearchChange(e.target.value)}
            placeholder={
              activeTab === "skills" ? "Search skills..." :
              activeTab === "mcp" ? "Search MCP servers..." :
              activeTab === "recipes" ? "Search recipes..." :
              "Search extensions..."
            }
            className="w-full bg-[#3c3c3c] text-[12px] text-white pl-7 pr-7 py-1.5 rounded border border-transparent focus:border-[#007acc] outline-none placeholder-gray-500"
          />
          {searchQuery && (
            <button
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-white"
              onClick={() => { handleSearchChange(""); }}
            >
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center border-b border-[#3c3c3c] px-1 shrink-0 overflow-x-auto">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={`px-2 py-1.5 text-[11px] font-medium border-b-2 transition-colors whitespace-nowrap flex items-center gap-1 ${
              activeTab === id
                ? "text-white border-white"
                : "text-gray-500 hover:text-gray-300 border-transparent"
            }`}
          >
            {id === "skills" && <Brain size={11} />}
            {id === "mcp" && <Plug size={11} />}
            {id === "recipes" && <Package size={11} />}
            {label}
            {id === "installed" && installedItems.length > 0 && (
              <span className="text-[10px] text-gray-500">({installedItems.length})</span>
            )}
          </button>
        ))}
        <div className="flex-1" />
        {showCategoryPills && (
          <div className="relative">
            <button
              className="p-1.5 text-gray-500 hover:text-white transition-colors"
              onClick={() => setShowSortMenu((v) => !v)}
              title="Sort & Filter"
            >
              <Filter size={13} />
            </button>
            {showSortMenu && (
              <div className="absolute right-0 top-full mt-1 bg-[#252526] border border-[#3c3c3c] rounded shadow-lg z-50 py-1 min-w-[140px]">
                {(["relevance", "downloads", "rating", "updated"] as const).map((s) => (
                  <button
                    key={s}
                    className={`w-full text-left px-3 py-1.5 text-[11px] hover:bg-[#3c3c3c] transition-colors flex items-center justify-between ${
                      sortBy === s ? "text-white" : "text-gray-400"
                    }`}
                    onClick={() => {
                      setSortBy(s);
                      setShowSortMenu(false);
                      if (searchQuery) doSearch(searchQuery);
                    }}
                  >
                    <span className="capitalize">{s}</span>
                    {sortBy === s && <Check size={11} className="text-[#007acc]" />}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Category pills */}
      {showCategoryPills && (
        <div className="flex flex-wrap gap-1 px-2 py-1.5 border-b border-[#3c3c3c]">
          <button
            className={`text-[10px] px-1.5 py-0.5 rounded transition-colors ${
              !categoryFilter
                ? "bg-[#007acc] text-white"
                : "bg-[#3c3c3c] text-gray-400 hover:text-white"
            }`}
            onClick={() => {
              setCategoryFilter(null);
              if (searchQuery) doSearch(searchQuery);
            }}
          >
            All
          </button>
          {CATEGORIES.map((cat) => (
            <button
              key={cat}
              className={`text-[10px] px-1.5 py-0.5 rounded transition-colors ${
                categoryFilter === cat
                  ? "bg-[#007acc] text-white"
                  : "bg-[#3c3c3c] text-gray-400 hover:text-white"
              }`}
              onClick={() => {
                setCategoryFilter(categoryFilter === cat ? null : cat);
                if (searchQuery) doSearch(searchQuery);
              }}
            >
              {cat}
            </button>
          ))}
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div className="mx-2 mt-1 px-2.5 py-1.5 bg-red-900/30 border border-red-800/40 rounded text-[11px] text-red-300 flex items-center gap-1.5">
          <AlertCircle size={12} />
          <span className="flex-1 truncate">{error}</span>
          <button onClick={() => setError(null)} className="shrink-0 hover:text-white">
            <X size={11} />
          </button>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {activeTab === "skills" ? (
          <SkillsTabContent onSelectSkill={handleSelectSkill} />
        ) : activeTab === "mcp" ? (
          <McpTabContent onSelectMcp={handleSelectItem} />
        ) : activeTab === "recipes" ? (
          <RecipeTabContent onSelectRecipe={handleSelectItem} />
        ) : activeTab === "installed" ? (
          installedItems.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center px-4">
              <Puzzle size={32} className="text-gray-600 mb-3" />
              <p className="text-[12px] text-gray-500 mb-1">No extensions installed</p>
              <p className="text-[11px] text-gray-600">
                Search for extensions or import a VSIX file.
              </p>
            </div>
          ) : (
            installedItems.map((item) => (
              <InstalledCard
                key={item.id}
                item={item}
                onSelect={() => handleSelectItem(item)}
                onToggle={() => toggleItemEnabled(item.id)}
                onUninstall={() => handleUninstall(item.id)}
                isUninstalling={isInstalling === item.id}
              />
            ))
          )
        ) : isSearching ? (
          Array.from({ length: 5 }, (_, i) => <SkeletonCard key={i} />)
        ) : searchResults.length > 0 ? (
          searchResults.map((item) => (
            <ExtensionCard
              key={item.id}
              item={item}
              isInstalled={installedIds.has(item.id)}
              isInstalling={isInstalling === item.id}
              compatTier={tierCache[item.id]}
              nativeInfo={shouldPreferNative(item.id)}
              onSelect={() => handleSelectItem(item)}
              onInstall={() => handleInstall(item)}
              onUninstall={() => handleUninstall(item.id)}
            />
          ))
        ) : searchQuery ? (
          <div className="flex flex-col items-center justify-center py-12 text-center px-4">
            <Search size={28} className="text-gray-600 mb-3" />
            <p className="text-[12px] text-gray-500">No results for &ldquo;{searchQuery}&rdquo;</p>
          </div>
        ) : (
          <>
            <RecommendedSection onInstall={handleRecSearch} installedIds={installedIds} />
            <div className="flex flex-col items-center justify-center py-12 text-center px-4">
              <Puzzle size={32} className="text-gray-600 mb-3" />
              <p className="text-[12px] text-gray-400 mb-1">Extension Marketplace</p>
              <p className="text-[11px] text-gray-600 mb-4">
                Search for VS Code extensions from Open VSX.
              </p>
              <div className="flex flex-wrap justify-center gap-1.5">
                {["Python", "Prettier", "ESLint", "Themes", "TypeScript"].map((q) => (
                  <button
                    key={q}
                    className="text-[10px] px-2 py-1 rounded bg-[#3c3c3c] text-gray-400 hover:bg-[#007acc] hover:text-white transition-colors"
                    onClick={() => handleSearchChange(q)}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
      </div>

      {/* Footer: Import VSIX + Refresh (only for non-skills/mcp tabs) */}
      {activeTab !== "skills" && activeTab !== "mcp" && activeTab !== "recipes" && (
        <div className="border-t border-[#3c3c3c] px-2 py-1.5 flex items-center gap-1.5">
          <button
            className="flex items-center gap-1 px-2 py-1 rounded text-[11px] text-gray-400 hover:bg-[#3c3c3c] hover:text-white transition-colors"
            onClick={handleImportVsix}
            title="Install from VSIX file"
          >
            <Upload size={12} /> Install from VSIX...
          </button>
          <div className="flex-1" />
          <button
            className="p-1 rounded text-gray-500 hover:text-white hover:bg-[#3c3c3c] transition-colors"
            onClick={loadInstalled}
            title="Refresh installed extensions"
          >
            <RefreshCw size={12} />
          </button>
        </div>
      )}
    </div>
  );
}

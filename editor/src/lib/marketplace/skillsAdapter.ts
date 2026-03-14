import type {
  MarketplaceRegistry,
  MarketplaceItem,
  MarketplaceItemDetail,
  InstalledItem,
} from "./types";
import { nativeSkills } from "../electronBridge";

const SKILL_HUB_API = "https://hub.dan.dev/api/v1/skills";

export interface SkillHubSearchResult {
  id: string;
  name: string;
  description: string;
  author: string;
  version: string;
  downloads: number;
  rating: number;
  tags: string[];
  url: string;
  readme_url: string;
}

interface HubCache {
  items: MarketplaceItem[];
  ts: number;
}

export class DanSkillsAdapter implements MarketplaceRegistry {
  id = "dan-skills";
  name = "DAN Skills";
  icon = "brain";

  private hubEnabled = false;
  private hubCache: HubCache | null = null;
  private readonly HUB_CACHE_TTL = 60_000;

  private async getSkillPath(itemId: string): Promise<string | null> {
    const skills = await nativeSkills.scan();
    const match = skills.find((s) => s.id === itemId);
    return match?.path ?? null;
  }

  enableHub(enabled: boolean) {
    this.hubEnabled = enabled;
    if (!enabled) this.hubCache = null;
  }

  isHubEnabled(): boolean {
    return this.hubEnabled;
  }

  async search(query: string): Promise<MarketplaceItem[]> {
    const local = await this.listInstalled();

    let remote: MarketplaceItem[] = [];
    if (this.hubEnabled) {
      remote = await this.searchHub(query);
      const localIds = new Set(local.map((l) => l.id));
      remote = remote.filter((r) => !localIds.has(r.id));
    }

    const all = [...local, ...remote];
    if (!query) return all;
    const q = query.toLowerCase();
    return all.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        s.tags.some((t) => t.toLowerCase().includes(q)),
    );
  }

  private async searchHub(query: string): Promise<MarketplaceItem[]> {
    if (this.hubCache && Date.now() - this.hubCache.ts < this.HUB_CACHE_TTL) {
      const cached = this.hubCache.items;
      if (!query) return cached;
      const q = query.toLowerCase();
      return cached.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.description.toLowerCase().includes(q) ||
          s.tags.some((t) => t.toLowerCase().includes(q)),
      );
    }

    try {
      const url = query
        ? `${SKILL_HUB_API}/search?q=${encodeURIComponent(query)}`
        : `${SKILL_HUB_API}/featured`;
      const resp = await fetch(url);
      if (!resp.ok) return [];
      const data: SkillHubSearchResult[] = await resp.json();

      const items: MarketplaceItem[] = data.map((s) => ({
        id: s.id,
        name: s.name,
        displayName: s.name,
        description: s.description,
        publisher: s.author,
        version: s.version,
        downloadCount: s.downloads,
        rating: s.rating,
        categories: ["Skills"],
        tags: s.tags,
        registry: this.id,
      }));

      this.hubCache = { items, ts: Date.now() };
      return items;
    } catch {
      return [];
    }
  }

  async getDetails(itemId: string): Promise<MarketplaceItemDetail> {
    try {
      const skillPath = await this.getSkillPath(itemId);
      const content = skillPath ? await nativeSkills.readSkill(skillPath) : null;
      if (content) {
        const meta = parseSkillFrontmatter(content);
        return {
          id: itemId,
          name: meta.name ?? itemId,
          displayName: meta.name ?? itemId,
          description: meta.description ?? "",
          publisher: meta.author ?? "local",
          version: meta.version ?? "1.0.0",
          categories: ["Skills"],
          tags: meta.triggers ?? [],
          registry: this.id,
          readme: content,
        };
      }
    } catch {
      /* not local — fall through to hub */
    }

    if (this.hubEnabled) {
      try {
        const resp = await fetch(
          `${SKILL_HUB_API}/${encodeURIComponent(itemId)}`,
        );
        if (resp.ok) {
          const data = await resp.json();
          return {
            id: data.id,
            name: data.name,
            displayName: data.name,
            description: data.description,
            publisher: data.author,
            version: data.version,
            downloadCount: data.downloads,
            rating: data.rating,
            categories: ["Skills"],
            tags: data.tags ?? [],
            registry: this.id,
            readme: data.readme ?? data.description,
            repository: data.repo_url,
            license: data.license,
          };
        }
      } catch {
        /* hub unavailable */
      }
    }

    throw new Error(`Skill ${itemId} not found`);
  }

  async install(itemId: string): Promise<InstalledItem> {
    if (this.hubEnabled) {
      try {
        const resp = await fetch(
          `${SKILL_HUB_API}/${encodeURIComponent(itemId)}`,
        );
        if (resp.ok) {
          const data = await resp.json();
          if (data.url) {
            await nativeSkills.importFromUrl(data.url);
            const details = await this.getDetails(itemId);
            return {
              ...details,
              installDate: Date.now(),
              enabled: true,
              extensionPath: "",
            };
          }
        }
      } catch {
        /* fallback to direct import */
      }
    }

    await nativeSkills.importFromUrl(itemId);
    const details = await this.getDetails(itemId);
    return {
      ...details,
      installDate: Date.now(),
      enabled: true,
      extensionPath: "",
    };
  }

  async uninstall(itemId: string): Promise<void> {
    await nativeSkills.remove(itemId);
  }

  async update(itemId: string): Promise<InstalledItem> {
    return this.install(itemId);
  }

  async listInstalled(): Promise<InstalledItem[]> {
    const skills = await nativeSkills.scan();
    return skills.map((s) => ({
      id: s.id,
      name: s.name,
      displayName: s.name,
      description: s.description,
      publisher: s.author ?? "local",
      version: s.version ?? "1.0.0",
      categories: ["Skills"],
      tags: s.triggers ?? [],
      registry: this.id,
      installDate: s.modifiedAt ?? Date.now(),
      enabled: s.enabled,
      extensionPath: s.path,
    }));
  }

  async checkUpdates(): Promise<
    Array<{ id: string; currentVersion: string; latestVersion: string }>
  > {
    if (!this.hubEnabled) return [];

    const installed = await this.listInstalled();
    const updates: Array<{
      id: string;
      currentVersion: string;
      latestVersion: string;
    }> = [];

    for (const item of installed) {
      try {
        const resp = await fetch(
          `${SKILL_HUB_API}/${encodeURIComponent(item.id)}`,
        );
        if (!resp.ok) continue;
        const data = await resp.json();
        if (data.version && data.version !== item.version) {
          updates.push({
            id: item.id,
            currentVersion: item.version,
            latestVersion: data.version,
          });
        }
      } catch {
        /* skip failed checks */
      }
    }
    return updates;
  }
}

interface SkillMeta {
  name?: string;
  description?: string;
  author?: string;
  version?: string;
  triggers?: string[];
}

function parseSkillFrontmatter(content: string): SkillMeta {
  const meta: SkillMeta = {};

  const fmMatch = content.match(/^---\s*\n([\s\S]*?)\n---/);
  if (fmMatch) {
    const fm = fmMatch[1];
    const nameMatch = fm.match(/name:\s*(.+)/);
    if (nameMatch)
      meta.name = nameMatch[1].trim().replace(/^["']|["']$/g, "");
    const descMatch = fm.match(/description:\s*(.+)/);
    if (descMatch)
      meta.description = descMatch[1].trim().replace(/^["']|["']$/g, "");
    const authorMatch = fm.match(/author:\s*(.+)/);
    if (authorMatch)
      meta.author = authorMatch[1].trim().replace(/^["']|["']$/g, "");
    const versionMatch = fm.match(/version:\s*(.+)/);
    if (versionMatch)
      meta.version = versionMatch[1].trim().replace(/^["']|["']$/g, "");
    const triggerMatch = fm.match(/triggers?:\s*\[([^\]]*)\]/);
    if (triggerMatch)
      meta.triggers = triggerMatch[1]
        .split(",")
        .map((t) => t.trim().replace(/^["']|["']$/g, ""));
  }

  if (!meta.name) {
    const headingMatch = content.match(/^#\s+(.+)/m);
    if (headingMatch) meta.name = headingMatch[1].trim();
  }
  return meta;
}

import { nativeExtension } from "../electronBridge";
import type {
  MarketplaceItem,
  MarketplaceItemDetail,
  InstalledItem,
  MarketplaceRegistry,
  SearchOptions,
} from "./types";

const OPEN_VSX_API = "https://open-vsx.org/api";

interface CacheEntry<T> {
  data: T;
  ts: number;
}

const CACHE_TTL = 30_000;
const searchCache = new Map<string, CacheEntry<MarketplaceItem[]>>();

function getCached<T>(cache: Map<string, CacheEntry<T>>, key: string): T | null {
  const entry = cache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.ts > CACHE_TTL) {
    cache.delete(key);
    return null;
  }
  return entry.data;
}

function mapExtension(ext: any, registryId: string): MarketplaceItem {
  return {
    id: `${ext.namespace}.${ext.name}`,
    name: ext.name,
    displayName: ext.displayName ?? ext.name,
    description: ext.description ?? "",
    publisher: ext.namespace,
    version: ext.version,
    icon: ext.files?.icon,
    rating: ext.averageRating,
    downloadCount: ext.downloadCount,
    categories: ext.categories ?? [],
    tags: ext.tags ?? [],
    registry: registryId,
  };
}

export class OpenVsxAdapter implements MarketplaceRegistry {
  id = "openvsx";
  name = "VS Code Extensions";
  icon = "puzzle";

  private baseUrl = OPEN_VSX_API;

  setBaseUrl(url: string) {
    this.baseUrl = url;
    searchCache.clear();
  }

  getBaseUrl(): string {
    return this.baseUrl;
  }

  async search(query: string, opts?: SearchOptions): Promise<MarketplaceItem[]> {
    const cacheKey = `${query}|${opts?.category ?? ""}|${opts?.sortBy ?? ""}|${opts?.page ?? 0}`;
    const cached = getCached(searchCache, cacheKey);
    if (cached) return cached;

    const params = new URLSearchParams({
      query,
      size: "20",
      offset: String((opts?.page ?? 0) * 20),
      sortBy: opts?.sortBy ?? "relevance",
      sortOrder: "desc",
    });
    if (opts?.category) params.set("category", opts.category);

    const res = await fetch(`${this.baseUrl}/-/search?${params}`);
    if (!res.ok) throw new Error(`Open VSX search failed: ${res.status}`);
    const data = await res.json();

    const results = (data.extensions ?? []).map((ext: any) =>
      mapExtension(ext, this.id),
    );
    searchCache.set(cacheKey, { data: results, ts: Date.now() });
    return results;
  }

  async getDetails(itemId: string): Promise<MarketplaceItemDetail> {
    const [namespace, name] = itemId.split(".");
    const res = await fetch(`${this.baseUrl}/${namespace}/${name}`);
    if (!res.ok) throw new Error(`Failed to fetch details for ${itemId}: ${res.status}`);
    const ext = await res.json();

    let readme = "";
    if (ext.files?.readme) {
      try {
        readme = await (await fetch(ext.files.readme)).text();
      } catch { /* readme fetch is best-effort */ }
    }

    return {
      ...mapExtension(ext, this.id),
      readme,
      repository: ext.repository,
      license: ext.license,
      engines: ext.engines,
    };
  }

  async install(itemId: string): Promise<InstalledItem> {
    const [namespace, name] = itemId.split(".");
    const res = await fetch(`${this.baseUrl}/${namespace}/${name}`);
    if (!res.ok) throw new Error(`Failed to fetch ${itemId}: ${res.status}`);
    const ext = await res.json();

    const downloadUrl = ext.files?.download;
    if (!downloadUrl) throw new Error("No download URL available");

    const result = await nativeExtension.install(itemId, downloadUrl);

    return {
      ...mapExtension(ext, this.id),
      installDate: Date.now(),
      enabled: true,
      extensionPath: result.extensionPath,
    };
  }

  async uninstall(itemId: string): Promise<void> {
    await nativeExtension.uninstall(itemId);
  }

  async update(itemId: string): Promise<InstalledItem> {
    return this.install(itemId);
  }

  async listInstalled(): Promise<InstalledItem[]> {
    return nativeExtension.listInstalled();
  }

  async checkUpdates(): Promise<Array<{ id: string; currentVersion: string; latestVersion: string }>> {
    const installed = await this.listInstalled();
    const updates: Array<{ id: string; currentVersion: string; latestVersion: string }> = [];

    for (const item of installed) {
      try {
        const [ns, name] = item.id.split(".");
        const res = await fetch(`${this.baseUrl}/${ns}/${name}`);
        if (!res.ok) continue;
        const ext = await res.json();
        if (ext.version !== item.version) {
          updates.push({
            id: item.id,
            currentVersion: item.version,
            latestVersion: ext.version,
          });
        }
      } catch { /* skip failed checks */ }
    }
    return updates;
  }
}

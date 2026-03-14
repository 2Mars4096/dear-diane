# Adding a New Marketplace Adapter

This guide explains how to add a new marketplace source to DAN's extension system.

## Architecture

DAN's marketplace framework uses a plugin architecture where each source (VS Code extensions, Skills, MCP servers, Recipes, etc.) is a **registry adapter** that implements the `MarketplaceRegistry` interface.

```
MarketplaceManager (singleton)
├── OpenVsxAdapter (VS Code extensions from Open VSX)
├── DanSkillsAdapter (local + remote Skill Hub)
├── McpServerAdapter (MCP tool servers)
├── RecipeStoreAdapter (domain knowledge recipes)
└── YourAdapter (your new source)
```

The `MarketplaceManager` (in `marketplaceManager.ts`) holds all registered adapters and provides:
- Unified search across all registries (`searchAll`)
- Per-registry enable/disable via settings stored in localStorage
- Periodic update checking with notification integration
- Custom registry URL support (currently for Open VSX)

## Step-by-Step

### 1. Implement the Interface

Create a new file in `editor/src/lib/marketplace/`:

```typescript
// editor/src/lib/marketplace/yourAdapter.ts
import type {
  MarketplaceRegistry,
  MarketplaceItem,
  MarketplaceItemDetail,
  InstalledItem,
  SearchOptions,
} from "./types";

export class YourAdapter implements MarketplaceRegistry {
  id = "your-source";     // unique identifier, used as key in settings
  name = "Your Source";    // display name shown in UI
  icon = "box";           // lucide-react icon name

  async search(query: string, opts?: SearchOptions): Promise<MarketplaceItem[]> {
    // Return items matching the query
    // Use opts.category and opts.sortBy if provided
    // Items MUST have registry: this.id
    // Return empty array on errors — never throw from search
  }

  async getDetails(itemId: string): Promise<MarketplaceItemDetail> {
    // Return full details including readme, license, contributes
    // Throw if item is not found
  }

  async install(itemId: string, version?: string): Promise<InstalledItem> {
    // Download and install the item
    // Return InstalledItem with extensionPath pointing to local install
  }

  async uninstall(itemId: string): Promise<void> {
    // Remove installed files and cleanup
  }

  async update(itemId: string): Promise<InstalledItem> {
    // Update to latest version — can delegate to install()
  }

  async listInstalled(): Promise<InstalledItem[]> {
    // Return all currently installed items from this source
    // Return empty array on errors — never throw
  }

  async checkUpdates(): Promise<Array<{ id: string; currentVersion: string; latestVersion: string }>> {
    // Compare installed versions against latest available
    // Return empty array if updates aren't supported
  }
}
```

### 2. Register with MarketplaceManager

In `editor/src/lib/marketplace/marketplaceManager.ts`, import and register:

```typescript
import { YourAdapter } from "./yourAdapter";

// At the bottom, alongside other registrations:
marketplaceManager.register(new YourAdapter());
```

Registration must happen before `loadFromSettings()` so the adapter appears in the settings toggle list.

### 3. Add Tab Filter (Optional)

If your source deserves its own tab in the UI, add it to the `TabFilter` type in `editor/src/store/useMarketplaceStore.ts`:

```typescript
type TabFilter = "all" | "vscode" | "installed" | "skills" | "mcp" | "your-source";
```

### 4. Add Tab Content (Optional)

In `editor/src/components/code/ExtensionsPanel.tsx`:

1. Add the tab id/label to the `TABS` array:
   ```typescript
   const TABS = [
     ["all", "All"],
     ["vscode", "VS Code"],
     ["skills", "Skills"],
     ["mcp", "MCP"],
     ["your-source", "Your Source"],
     ["installed", "Installed"],
   ] as const;
   ```

2. Create a `YourTabContent` component following the pattern of `SkillsTabContent` or `McpTabContent`.

3. Add a conditional render in the content area:
   ```typescript
   {activeTab === "your-source" ? (
     <YourTabContent onSelect={handleSelectItem} />
   ) : activeTab === "skills" ? (
     // ... existing tabs ...
   ```

### 5. Configure via Settings

Your adapter is automatically included in the marketplace settings panel (enable/disable toggle) because `MarketplaceManager.getRegisteredIds()` enumerates all registered adapters. Add a display label in `ExtensionsPanel.tsx`:

```typescript
const REGISTRY_LABELS: Record<string, string> = {
  openvsx: "VS Code (Open VSX)",
  "dan-skills": "DAN Skills",
  "mcp-servers": "MCP Servers",
  "your-source": "Your Source",
};
```

## Interface Reference

### MarketplaceItem (search result)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | string | yes | Unique item identifier |
| name | string | yes | Technical name |
| displayName | string | yes | Human-readable name |
| description | string | yes | Short description |
| publisher | string | yes | Author/publisher name |
| version | string | yes | Semver version |
| icon | string | no | Icon URL or lucide icon name |
| rating | number | no | 0-5 star rating |
| downloadCount | number | no | Install/download count |
| categories | string[] | yes | Categorization tags |
| tags | string[] | yes | Searchable tags |
| registry | string | yes | Must match adapter `id` |

### MarketplaceItemDetail (extends MarketplaceItem)

| Field | Type | Description |
|-------|------|-------------|
| readme | string | Markdown content for detail view |
| changelog | string | Version history |
| repository | string | Source code URL |
| license | string | License identifier (e.g. "MIT") |
| engines | Record\<string, string\> | Compatibility constraints |
| contributes | object | VS Code contribution points (themes, grammars, snippets, languages, configuration) |

### InstalledItem (extends MarketplaceItem)

| Field | Type | Description |
|-------|------|-------------|
| installDate | number | Unix timestamp of install |
| enabled | boolean | Whether item is active |
| extensionPath | string | Local filesystem path |

### SearchOptions

| Field | Type | Description |
|-------|------|-------------|
| category | string | Filter by category |
| sortBy | string | Sort order (relevance, downloads, rating, updated) |
| page | number | Pagination offset |

## Trust & Safety

All items go through the trust model (`trustModel.ts`):
- New items start untrusted
- User grants permissions per item
- Safe mode (`safeMode.ts`) disables all extensions on repeated failures
- Enable scope (`enableScope.ts`) controls global vs workspace-level activation

## Patterns to Follow

These patterns are established by the existing adapters. Follow them for consistency.

### Caching

Cache search results for 30–60s to avoid redundant network requests. See `openVsxAdapter.ts` for the canonical pattern:

```typescript
interface CacheEntry<T> { data: T; ts: number; }
const CACHE_TTL = 30_000;
const cache = new Map<string, CacheEntry<MarketplaceItem[]>>();

function getCached<T>(map: Map<string, CacheEntry<T>>, key: string): T | null {
  const entry = map.get(key);
  if (!entry) return null;
  if (Date.now() - entry.ts > CACHE_TTL) { map.delete(key); return null; }
  return entry.data;
}
```

### Error Handling

- **Never throw from `search()` or `listInstalled()`** — return empty arrays on failure
- `getDetails()` may throw if the item is genuinely not found
- `install()` may throw on download/permission failures
- Wrap remote calls in try/catch and degrade gracefully

### Local-First

When combining local and remote data (like `DanSkillsAdapter`):
1. Load local items first
2. Fetch remote items
3. Deduplicate — local versions always take precedence
4. Mark remote-only items visually (e.g. cloud icon)

### Offline Resilience

Adapters must work offline:
- `listInstalled()` returns locally cached data
- `search()` returns local matches, skips remote
- Network failures are silent (no thrown errors, no UI error state)

### Electron Bridge

If your adapter needs native filesystem or process operations, add the API surface to `electronBridge.ts` following the existing pattern:

```typescript
// In ElectronAPI interface:
yourSource: {
  install: (id: string, url: string) => Promise<{ path: string }>;
  listInstalled: () => Promise<YourEntry[]>;
  remove: (id: string) => Promise<void>;
};

// Export a bridge object:
export const nativeYourSource = {
  async install(id: string, url: string): Promise<{ path: string }> {
    if (window.electronAPI) return window.electronAPI.yourSource.install(id, url);
    throw new Error("Install requires Electron");
  },
  // ... etc
};
```

## Existing Adapters

| Adapter | Source | Storage | Features |
|---------|--------|---------|----------|
| `OpenVsxAdapter` | open-vsx.org API | `~/.dan/extensions/` | VSIX install, themes, grammars, snippets, compatibility tier classification |
| `DanSkillsAdapter` | Local + Skill Hub API | `~/.dan/skills/` | Frontmatter parsing, import from URL, hub toggle with 60s cache |
| `McpServerAdapter` | Curated list + config | `~/.dan/mcp/` | Process lifecycle, health monitoring, status polling |
| `RecipeStoreAdapter` | Local + catalog | `~/.dan/recipes/` | Domain knowledge, paper distillation, quality scoring |

## Future Registry Ideas

These registries can be added by implementing `MarketplaceRegistry`:

| Registry | Source | Item Type | Install Method |
|----------|--------|-----------|---------------|
| npm packages | npmjs.com API | Node.js tools/libraries | `npm install` to workspace |
| pip packages | PyPI API | Python tools/libraries | `pip install` to venv |
| Docker images | Docker Hub API | Isolated environments | `docker pull` |
| Homebrew formulas | Homebrew API | CLI tools | `brew install` |
| Theme galleries | Custom API | Color themes / icon themes | Download JSON |
| Prompt libraries | Custom API | System prompts / templates | Download text files |
| Dataset registries | HuggingFace / Kaggle | Datasets | Download + extract |
| Model registries | HuggingFace / Ollama | AI models | Download weights |

### Template for a Remote API Adapter

A reusable starting point for any adapter that talks to a remote REST API:

```typescript
import type {
  MarketplaceRegistry,
  MarketplaceItem,
  MarketplaceItemDetail,
  InstalledItem,
} from "./types";

export class RemoteRegistryAdapter implements MarketplaceRegistry {
  id = "remote-source";
  name = "Remote Source";
  icon = "globe";

  private apiBase: string;
  private cache = new Map<string, { data: any; ts: number }>();
  private cacheTtl = 30_000;

  constructor(apiBase: string) {
    this.apiBase = apiBase;
  }

  setApiBase(url: string) {
    this.apiBase = url;
    this.cache.clear();
  }

  private async cachedFetch<T>(key: string, url: string): Promise<T | null> {
    const cached = this.cache.get(key);
    if (cached && Date.now() - cached.ts < this.cacheTtl) return cached.data;
    try {
      const resp = await fetch(url);
      if (!resp.ok) return null;
      const data = await resp.json();
      this.cache.set(key, { data, ts: Date.now() });
      return data;
    } catch {
      return null;
    }
  }

  async search(query: string): Promise<MarketplaceItem[]> {
    const params = query ? `?q=${encodeURIComponent(query)}` : "";
    const data = await this.cachedFetch<any[]>(`search:${query}`, `${this.apiBase}/search${params}`);
    if (!data) return [];
    return data.map((item) => ({
      id: item.id,
      name: item.name,
      displayName: item.display_name ?? item.name,
      description: item.description ?? "",
      publisher: item.author ?? "unknown",
      version: item.version ?? "0.0.0",
      categories: item.categories ?? [],
      tags: item.tags ?? [],
      registry: this.id,
      rating: item.rating,
      downloadCount: item.downloads,
    }));
  }

  async getDetails(id: string): Promise<MarketplaceItemDetail> {
    const data = await this.cachedFetch<any>(`detail:${id}`, `${this.apiBase}/${encodeURIComponent(id)}`);
    if (!data) throw new Error(`Item ${id} not found`);
    return {
      id: data.id,
      name: data.name,
      displayName: data.display_name ?? data.name,
      description: data.description ?? "",
      publisher: data.author ?? "unknown",
      version: data.version ?? "0.0.0",
      categories: data.categories ?? [],
      tags: data.tags ?? [],
      registry: this.id,
      readme: data.readme,
      repository: data.repo_url,
      license: data.license,
    };
  }

  async install(id: string): Promise<InstalledItem> {
    const details = await this.getDetails(id);
    // Implement actual download/install logic here
    return { ...details, installDate: Date.now(), enabled: true, extensionPath: "" };
  }

  async uninstall(id: string): Promise<void> {
    // Implement cleanup logic
  }

  async update(id: string): Promise<InstalledItem> {
    return this.install(id);
  }

  async listInstalled(): Promise<InstalledItem[]> {
    return [];
  }

  async checkUpdates(): Promise<Array<{ id: string; currentVersion: string; latestVersion: string }>> {
    return [];
  }
}
```

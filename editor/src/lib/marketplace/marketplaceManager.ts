import type { MarketplaceItem, MarketplaceRegistry, SearchOptions } from "./types";
import { OpenVsxAdapter } from "./openVsxAdapter";
import { DanSkillsAdapter } from "./skillsAdapter";
import { McpServerAdapter } from "./mcpAdapter";
import { RecipeStoreAdapter } from "./recipeAdapter";
import { useAppStore } from "../../store/useAppStore";
import { useMarketplaceStore } from "../../store/useMarketplaceStore";

export interface MarketplaceSettings {
  registries: Record<string, boolean>;
  customRegistryUrl?: string;
}

const SETTINGS_KEY = "dan-marketplace-settings";

class MarketplaceManager {
  private registries = new Map<string, MarketplaceRegistry>();
  private allRegistries = new Map<string, MarketplaceRegistry>();
  private updateCheckInterval: ReturnType<typeof setInterval> | null = null;

  register(registry: MarketplaceRegistry) {
    this.registries.set(registry.id, registry);
    this.allRegistries.set(registry.id, registry);
  }

  getRegistry(id: string) {
    return this.registries.get(id);
  }

  getAllRegistries() {
    return Array.from(this.registries.values());
  }

  getRegisteredIds(): string[] {
    return Array.from(this.allRegistries.keys());
  }

  loadFromSettings() {
    try {
      const raw = localStorage.getItem(SETTINGS_KEY);
      if (!raw) return;
      const config: MarketplaceSettings = JSON.parse(raw);

      for (const [id, enabled] of Object.entries(config.registries ?? {})) {
        if (!enabled) {
          this.registries.delete(id);
        } else {
          const original = this.allRegistries.get(id);
          if (original && !this.registries.has(id)) {
            this.registries.set(id, original);
          }
        }
      }

      if (config.customRegistryUrl) {
        const adapter = this.allRegistries.get("openvsx");
        if (adapter && adapter instanceof OpenVsxAdapter) {
          adapter.setBaseUrl(config.customRegistryUrl);
        }
      }
    } catch { /* ignore corrupt settings */ }
  }

  saveSettings(settings: MarketplaceSettings) {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));

    this.registries.clear();
    for (const [id, registry] of this.allRegistries) {
      if (settings.registries[id] !== false) {
        this.registries.set(id, registry);
      }
    }

    if (settings.customRegistryUrl) {
      const adapter = this.allRegistries.get("openvsx");
      if (adapter && adapter instanceof OpenVsxAdapter) {
        adapter.setBaseUrl(settings.customRegistryUrl);
      }
    } else {
      const adapter = this.allRegistries.get("openvsx");
      if (adapter && adapter instanceof OpenVsxAdapter) {
        adapter.setBaseUrl("https://open-vsx.org/api");
      }
    }
  }

  getSettings(): MarketplaceSettings {
    try {
      const raw = localStorage.getItem(SETTINGS_KEY);
      if (raw) return JSON.parse(raw);
    } catch { /* ignore */ }
    const registries: Record<string, boolean> = {};
    for (const id of this.allRegistries.keys()) registries[id] = true;
    return { registries };
  }

  resetSettings() {
    localStorage.removeItem(SETTINGS_KEY);
    this.registries.clear();
    for (const [id, registry] of this.allRegistries) {
      this.registries.set(id, registry);
    }
    const adapter = this.allRegistries.get("openvsx");
    if (adapter && adapter instanceof OpenVsxAdapter) {
      adapter.setBaseUrl("https://open-vsx.org/api");
    }
  }

  async searchAll(query: string, opts?: SearchOptions): Promise<MarketplaceItem[]> {
    const results = await Promise.allSettled(
      Array.from(this.registries.values()).map((r) => r.search(query, opts)),
    );
    return results.flatMap((r) =>
      r.status === "fulfilled" ? r.value : [],
    );
  }

  startUpdateChecking(intervalMs = 3_600_000) {
    this.stopUpdateChecking();
    this.updateCheckInterval = setInterval(async () => {
      const updates = await this.checkAllUpdates();
      if (updates.length > 0) {
        useAppStore.getState().addNotification({
          type: "info",
          title: "Extension Updates Available",
          message: `${updates.length} extension(s) have updates available`,
        });
        useMarketplaceStore.getState().setAvailableUpdates(updates);
      }
    }, intervalMs);
  }

  stopUpdateChecking() {
    if (this.updateCheckInterval) {
      clearInterval(this.updateCheckInterval);
      this.updateCheckInterval = null;
    }
  }

  async checkAllUpdates(): Promise<
    Array<{ id: string; currentVersion: string; latestVersion: string }>
  > {
    const allUpdates: Array<{
      id: string;
      currentVersion: string;
      latestVersion: string;
    }> = [];
    for (const registry of this.registries.values()) {
      try {
        const updates = await registry.checkUpdates();
        allUpdates.push(...updates);
      } catch {
        /* ignore per-registry failures */
      }
    }
    return allUpdates;
  }
}

export const marketplaceManager = new MarketplaceManager();
marketplaceManager.register(new OpenVsxAdapter());
marketplaceManager.register(new DanSkillsAdapter());
marketplaceManager.register(new McpServerAdapter());
marketplaceManager.register(new RecipeStoreAdapter());
marketplaceManager.loadFromSettings();

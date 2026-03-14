import { create } from "zustand";
import type { MarketplaceItem, MarketplaceItemDetail, InstalledItem } from "../lib/marketplace/types";

type TabFilter = "all" | "vscode" | "installed" | "skills" | "mcp" | "recipes";
type SortOption = "relevance" | "downloads" | "rating" | "updated";

export interface UpdateInfo {
  id: string;
  currentVersion: string;
  latestVersion: string;
}

interface MarketplaceState {
  searchQuery: string;
  activeTab: TabFilter;
  searchResults: MarketplaceItem[];
  installedItems: InstalledItem[];
  selectedItem: MarketplaceItemDetail | null;
  isSearching: boolean;
  isInstalling: string | null;
  sortBy: SortOption;
  categoryFilter: string | null;
  error: string | null;
  availableUpdates: UpdateInfo[];
  mcpStatuses: Record<string, "connected" | "disconnected" | "error">;

  setSearchQuery: (q: string) => void;
  setActiveTab: (tab: TabFilter) => void;
  setSearchResults: (items: MarketplaceItem[]) => void;
  setInstalledItems: (items: InstalledItem[]) => void;
  setSelectedItem: (item: MarketplaceItemDetail | null) => void;
  setIsSearching: (v: boolean) => void;
  setIsInstalling: (id: string | null) => void;
  setSortBy: (sort: SortOption) => void;
  setCategoryFilter: (cat: string | null) => void;
  setError: (err: string | null) => void;
  setAvailableUpdates: (updates: UpdateInfo[]) => void;
  setMcpStatuses: (statuses: Record<string, "connected" | "disconnected" | "error">) => void;
  toggleItemEnabled: (itemId: string) => void;
  removeInstalledItem: (itemId: string) => void;
  addInstalledItem: (item: InstalledItem) => void;
}

export const useMarketplaceStore = create<MarketplaceState>((set) => ({
  searchQuery: "",
  activeTab: "all",
  searchResults: [],
  installedItems: [],
  selectedItem: null,
  isSearching: false,
  isInstalling: null,
  sortBy: "relevance",
  categoryFilter: null,
  error: null,
  availableUpdates: [],
  mcpStatuses: {},

  setSearchQuery: (q) => set({ searchQuery: q }),
  setActiveTab: (tab) => set({ activeTab: tab }),
  setSearchResults: (items) => set({ searchResults: items }),
  setInstalledItems: (items) => set({ installedItems: items }),
  setSelectedItem: (item) => set({ selectedItem: item }),
  setIsSearching: (v) => set({ isSearching: v }),
  setIsInstalling: (id) => set({ isInstalling: id }),
  setSortBy: (sort) => set({ sortBy: sort }),
  setCategoryFilter: (cat) => set({ categoryFilter: cat }),
  setError: (err) => set({ error: err }),
  setAvailableUpdates: (updates) => set({ availableUpdates: updates }),
  setMcpStatuses: (statuses) => set({ mcpStatuses: statuses }),
  toggleItemEnabled: (itemId) =>
    set((s) => ({
      installedItems: s.installedItems.map((item) =>
        item.id === itemId ? { ...item, enabled: !item.enabled } : item,
      ),
    })),
  removeInstalledItem: (itemId) =>
    set((s) => ({
      installedItems: s.installedItems.filter((item) => item.id !== itemId),
    })),
  addInstalledItem: (item) =>
    set((s) => ({
      installedItems: [...s.installedItems.filter((i) => i.id !== item.id), item],
    })),
}));

export interface MarketplaceItem {
  id: string;
  name: string;
  displayName: string;
  description: string;
  publisher: string;
  version: string;
  icon?: string;
  rating?: number;
  downloadCount?: number;
  categories: string[];
  tags: string[];
  registry: string;
}

export interface MarketplaceItemDetail extends MarketplaceItem {
  readme?: string;
  changelog?: string;
  repository?: string;
  license?: string;
  engines?: Record<string, string>;
  contributes?: {
    themes?: Array<{ label: string; uiTheme: string; path: string }>;
    grammars?: Array<{ language: string; scopeName: string; path: string }>;
    snippets?: Array<{ language: string; path: string }>;
    languages?: Array<{ id: string; aliases: string[]; extensions: string[] }>;
    configuration?: Record<string, unknown>;
  };
}

export interface InstalledItem extends MarketplaceItem {
  installDate: number;
  enabled: boolean;
  extensionPath: string;
}

export interface SearchOptions {
  category?: string;
  sortBy?: string;
  page?: number;
}

export interface MarketplaceRegistry {
  id: string;
  name: string;
  icon: string;
  search(query: string, opts?: SearchOptions): Promise<MarketplaceItem[]>;
  getDetails(itemId: string): Promise<MarketplaceItemDetail>;
  install(itemId: string, version?: string): Promise<InstalledItem>;
  uninstall(itemId: string): Promise<void>;
  update(itemId: string): Promise<InstalledItem>;
  listInstalled(): Promise<InstalledItem[]>;
  checkUpdates(): Promise<Array<{ id: string; currentVersion: string; latestVersion: string }>>;
}

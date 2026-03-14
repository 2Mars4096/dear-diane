import type {
  MarketplaceRegistry,
  MarketplaceItem,
  MarketplaceItemDetail,
  InstalledItem,
} from "./types";
import { nativeMcp } from "../electronBridge";

const CURATED_MCP_SERVERS: MarketplaceItem[] = [
  {
    id: "filesystem",
    name: "filesystem",
    displayName: "Filesystem",
    description:
      "Read, write, and manage files. Provides tools for directory listing, file reading/writing, and file search.",
    publisher: "modelcontextprotocol",
    version: "1.0.0",
    categories: ["MCP Servers"],
    tags: ["files", "filesystem", "io"],
    registry: "mcp-servers",
  },
  {
    id: "brave-search",
    name: "brave-search",
    displayName: "Brave Search",
    description: "Web and local search using the Brave Search API.",
    publisher: "modelcontextprotocol",
    version: "1.0.0",
    categories: ["MCP Servers"],
    tags: ["search", "web", "brave"],
    registry: "mcp-servers",
  },
  {
    id: "github",
    name: "github",
    displayName: "GitHub",
    description:
      "Interact with GitHub repositories, issues, pull requests, and more.",
    publisher: "modelcontextprotocol",
    version: "1.0.0",
    categories: ["MCP Servers"],
    tags: ["github", "git", "vcs"],
    registry: "mcp-servers",
  },
  {
    id: "postgres",
    name: "postgres",
    displayName: "PostgreSQL",
    description:
      "Read-only access to PostgreSQL databases with schema inspection.",
    publisher: "modelcontextprotocol",
    version: "1.0.0",
    categories: ["MCP Servers"],
    tags: ["database", "sql", "postgres"],
    registry: "mcp-servers",
  },
  {
    id: "puppeteer",
    name: "puppeteer",
    displayName: "Puppeteer",
    description:
      "Browser automation for web scraping, screenshots, and testing.",
    publisher: "modelcontextprotocol",
    version: "1.0.0",
    categories: ["MCP Servers"],
    tags: ["browser", "automation", "scraping"],
    registry: "mcp-servers",
  },
  {
    id: "slack",
    name: "slack",
    displayName: "Slack",
    description:
      "Send messages, read channels, and manage Slack workspaces.",
    publisher: "modelcontextprotocol",
    version: "1.0.0",
    categories: ["MCP Servers"],
    tags: ["messaging", "slack", "communication"],
    registry: "mcp-servers",
  },
  {
    id: "memory",
    name: "memory",
    displayName: "Memory",
    description:
      "Persistent memory with a knowledge graph for storing and retrieving entities and relations.",
    publisher: "modelcontextprotocol",
    version: "1.0.0",
    categories: ["MCP Servers"],
    tags: ["memory", "knowledge", "graph"],
    registry: "mcp-servers",
  },
  {
    id: "fetch",
    name: "fetch",
    displayName: "Fetch",
    description:
      "Fetch URLs and convert HTML to markdown for easy consumption.",
    publisher: "modelcontextprotocol",
    version: "1.0.0",
    categories: ["MCP Servers"],
    tags: ["http", "fetch", "web"],
    registry: "mcp-servers",
  },
];

export class McpServerAdapter implements MarketplaceRegistry {
  id = "mcp-servers";
  name = "MCP Servers";
  icon = "plug";

  async search(query: string): Promise<MarketplaceItem[]> {
    if (!query) return CURATED_MCP_SERVERS;
    const q = query.toLowerCase();
    return CURATED_MCP_SERVERS.filter(
      (s) =>
        s.displayName.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        s.tags.some((t) => t.includes(q)),
    );
  }

  async getDetails(itemId: string): Promise<MarketplaceItemDetail> {
    const item = CURATED_MCP_SERVERS.find((s) => s.id === itemId);
    if (!item) throw new Error(`MCP server ${itemId} not found`);

    return {
      ...item,
      readme: [
        `# ${item.displayName}`,
        "",
        item.description,
        "",
        "## Installation",
        "",
        "```bash",
        `npx -y @modelcontextprotocol/server-${item.id}`,
        "```",
        "",
        "## Available Tools",
        "",
        `This MCP server provides tools for ${item.tags.join(", ")}.`,
      ].join("\n"),
    };
  }

  async install(itemId: string): Promise<InstalledItem> {
    const item = CURATED_MCP_SERVERS.find((s) => s.id === itemId);
    if (!item) throw new Error(`MCP server ${itemId} not found`);

    await nativeMcp.install(itemId, {
      command: "npx",
      args: ["-y", `@modelcontextprotocol/server-${itemId}`],
    });

    return {
      ...item,
      installDate: Date.now(),
      enabled: true,
      extensionPath: "",
    };
  }

  async uninstall(itemId: string): Promise<void> {
    await nativeMcp.remove(itemId);
  }

  async update(itemId: string): Promise<InstalledItem> {
    return this.install(itemId);
  }

  async listInstalled(): Promise<InstalledItem[]> {
    const configs = await nativeMcp.listInstalled();
    return configs.map((c) => {
      const curated = CURATED_MCP_SERVERS.find((s) => s.id === c.id);
      return {
        id: c.id,
        name: c.name,
        displayName: curated?.displayName ?? c.name,
        description: curated?.description ?? "",
        publisher: curated?.publisher ?? "custom",
        version: "1.0.0",
        categories: ["MCP Servers"],
        tags: curated?.tags ?? [],
        registry: this.id,
        installDate: Date.now(),
        enabled: c.enabled,
        extensionPath: "",
      };
    });
  }

  async checkUpdates(): Promise<
    Array<{ id: string; currentVersion: string; latestVersion: string }>
  > {
    return [];
  }
}

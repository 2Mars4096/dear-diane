import { nativeMcp } from "./electronBridge";

interface McpTool {
  serverId: string;
  serverName: string;
  name: string;
  description: string;
  inputSchema?: any;
}

let registeredTools: McpTool[] = [];

export async function refreshMcpTools(): Promise<void> {
  const servers = await nativeMcp.listInstalled();
  const tools: McpTool[] = [];

  for (const server of servers) {
    if (!server.enabled) continue;

    const curatedTools = CURATED_TOOL_CATALOG[server.id];
    if (curatedTools) {
      tools.push(...curatedTools.map(t => ({
        serverId: server.id,
        serverName: server.name,
        ...t,
      })));
    }
  }

  registeredTools = tools;
}

export function getMcpTools(): McpTool[] {
  return registeredTools;
}

export function getMcpToolSuggestions(query: string): McpTool[] {
  if (!query) return registeredTools.slice(0, 5);
  const q = query.toLowerCase();
  return registeredTools.filter(t =>
    t.name.toLowerCase().includes(q) ||
    t.description.toLowerCase().includes(q) ||
    t.serverName.toLowerCase().includes(q),
  );
}

const CURATED_TOOL_CATALOG: Record<string, Array<{ name: string; description: string }>> = {
  filesystem: [
    { name: "read_file", description: "Read the contents of a file" },
    { name: "write_file", description: "Write content to a file" },
    { name: "list_directory", description: "List directory contents" },
    { name: "search_files", description: "Search for files matching a pattern" },
  ],
  "brave-search": [
    { name: "brave_web_search", description: "Search the web using Brave Search" },
    { name: "brave_local_search", description: "Search for local businesses and places" },
  ],
  github: [
    { name: "list_repos", description: "List GitHub repositories" },
    { name: "create_issue", description: "Create a new issue" },
    { name: "search_code", description: "Search code on GitHub" },
    { name: "create_pull_request", description: "Create a pull request" },
  ],
  postgres: [
    { name: "query", description: "Run a SQL query on the database" },
    { name: "list_tables", description: "List all tables in the database" },
    { name: "describe_table", description: "Describe a table's schema" },
  ],
  memory: [
    { name: "create_entity", description: "Create an entity in the knowledge graph" },
    { name: "create_relation", description: "Create a relation between entities" },
    { name: "search_nodes", description: "Search for nodes in the knowledge graph" },
  ],
  fetch: [
    { name: "fetch_url", description: "Fetch a URL and return its content" },
  ],
};

# 1-5: Marketplace & Extension Framework

**Parent:** [1-ui-spec](1-ui-spec.md)
**Status:** not-started
**Goal:** Build an extensible marketplace framework that supports VS Code extensions (critical for Code mode), DAN skills/recipes, MCP servers, and future registries — all through a unified browsing/install/manage UI.

## Context

A coding IDE without extensions is a toy. VS Code's dominance comes from its extension ecosystem — language support, linters, formatters, themes, debuggers, and thousands of workflow tools. DAN's Code mode needs access to this ecosystem on day one.

But DAN also has its own extension types that VS Code doesn't: skills (procedural knowledge), recipes (distilled domain expertise from token-burning sessions), MCP servers (external tool providers), and workflow templates. Rather than building separate UIs for each, this plan defines a generic marketplace framework where each source is a pluggable registry adapter.

This framework must not block Code mode's MVP. Native Monaco/LSP/git/debug integrations can ship first; marketplace support layers on top as the extensibility system.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              MarketplaceManager                      │
│                                                      │
│  ┌─────────────┐ ┌─────────────┐ ┌──────────────┐  │
│  │ OpenVSX     │ │ DAN Skills  │ │ MCP Registry │  │
│  │ Adapter     │ │ Adapter     │ │ Adapter      │  │
│  └──────┬──────┘ └──────┬──────┘ └──────┬───────┘  │
│         │               │               │           │
│  ┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼───────┐  │
│  │ Open VSX    │ │ ~/.dan/     │ │ MCP Server   │  │
│  │ Registry    │ │ skills/     │ │ Registry     │  │
│  │ (remote)    │ │ (local+hub) │ │ (remote)     │  │
│  └─────────────┘ └─────────────┘ └──────────────┘  │
│                                                      │
│  Future: [Recipe Store] [npm/pip] [Theme Gallery]   │
└─────────────────────────────────────────────────────┘
```

### MarketplaceRegistry Interface

Every marketplace adapter implements:

```typescript
interface MarketplaceRegistry {
  id: string;                    // "openvsx", "dan-skills", "mcp-servers"
  name: string;                  // "VS Code Extensions", "DAN Skills", "MCP Servers"
  icon: string;                  // icon for the marketplace tab

  search(query: string, opts?: SearchOptions): Promise<MarketplaceItem[]>;
  getDetails(itemId: string): Promise<MarketplaceItemDetail>;
  install(itemId: string, version?: string): Promise<InstallResult>;
  uninstall(itemId: string): Promise<void>;
  update(itemId: string): Promise<InstallResult>;
  listInstalled(): Promise<InstalledItem[]>;
  checkUpdates(): Promise<UpdateInfo[]>;
}

interface MarketplaceItem {
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
  registry: string;              // which marketplace this came from
}
```

### Scope Control

The framework should be staged deliberately:
- **Phase 2A:** native language intelligence and curated built-ins first
- **Phase 2B:** marketplace browsing/install UI
- **Phase 2C:** curated VS Code compatibility for high-value extension categories (themes, grammars, snippets, linters, formatters, selected language packs)

Non-goal: full compatibility with every VS Code extension. Success is supporting the subset required to make Code mode practical, not cloning the entire VS Code runtime.

### Registry Source Strategy

For VS Code-style extensions, the architecture should separate the **extension runtime** from the **extension source**:
- Default registry can be Open VSX
- Users should also be able to install a local `.vsix` file directly
- Registry source must stay swappable/configurable so future compatible registries can be added without changing the extension host

This matters because the Microsoft VS Code Marketplace has licensing/policy constraints. The UI should not hard-code one registry as the only possible source.

## Tasks

### 1. Marketplace Framework (Shared Infrastructure)
- [ ] 1-1. **MarketplaceManager**: singleton that holds registered marketplace adapters, routes search/install/update calls
- [ ] 1-2. **MarketplaceItem model**: unified item model across all registries (id, name, description, publisher, version, icon, rating, categories, tags, installed status)
- [ ] 1-3. **Extension storage**: `~/.dan/extensions/` directory structure with per-registry subdirectories
- [ ] 1-4. **Install/uninstall lifecycle**: download → extract → validate → activate → persist to manifest; uninstall → deactivate → remove files → update manifest
- [ ] 1-5. **Update checking**: periodic check for updates across all registries, badge indicator on marketplace icon
- [ ] 1-6. **Extension manifest**: `~/.dan/extensions/manifest.json` tracks all installed items across registries (version, install date, enabled/disabled, settings)
- [ ] 1-7. **Trust and permissions model**: extensions and MCP servers declare requested capabilities (filesystem, network, subprocess, debug attach, external auth). Install flow shows these permissions before activation.
- [ ] 1-8. **Enable scope**: enable/disable globally or per workspace so risky/heavy integrations do not have to run everywhere

### 2. Extensions Panel (UI)
- [ ] 2-1. **Extensions sidebar panel**: accessible from Code mode sidebar or Cmd+Shift+X
- [ ] 2-2. **Tabbed view by registry**: tabs for "VS Code", "Skills", "MCP Servers", "All"
- [ ] 2-3. **Search bar**: unified search across all registries or filtered by active tab
- [ ] 2-4. **Item cards**: icon, name, publisher, rating, short description, install/update/uninstall button
- [ ] 2-5. **Item detail view**: full description, readme, changelog, screenshots, settings, reviews
- [ ] 2-6. **Installed view**: list of all installed extensions with enable/disable toggle and uninstall
- [ ] 2-7. **Recommended extensions**: context-aware suggestions based on project type and workspace content
- [ ] 2-8. **Categories/filters**: filter by category (Languages, Themes, Linters, Debuggers, etc.)
- [ ] 2-9. **Sort options**: by relevance, downloads, rating, recently updated
- [ ] 2-10. **Extension settings**: per-extension configuration UI (renders extension's `contributes.configuration` for VS Code extensions, or skill metadata for DAN skills)

### 3. VS Code Extensions (Registry Adapter)
- [ ] 3-1. **Registry-source abstraction**: VS Code-style extension adapter can target Open VSX by default but swap to other compatible VSIX sources without changing the runtime
- [ ] 3-2. **Open VSX API client**: connect to `open-vsx.org` REST API for search, download, metadata
- [ ] 3-3. **VSIX packaging**: download and extract `.vsix` files (ZIP with `extension/` folder)
- [ ] 3-4. **Local VSIX import**: install extension from a user-supplied `.vsix` file (drag-and-drop or file picker)
- [ ] 3-5. **TextMate grammars**: load `.tmLanguage` / `.tmGrammar.json` for syntax highlighting in Monaco
- [ ] 3-6. **Theme loading**: parse VS Code color themes and apply to Monaco editor + app UI
- [ ] 3-7. **Icon themes**: load file icon themes (e.g., vscode-icons, material-icon-theme) for file explorer
- [ ] 3-8. **Snippet loading**: load snippet JSON files and register with Monaco completion provider
- [ ] 3-9. **Language configuration**: load `language-configuration.json` (comments, brackets, auto-close, folding)
- [ ] 3-10. **Extension API surface (partial)**: implement enough of the `vscode` namespace to support common extension types:
  - `vscode.languages` — register completion, hover, definition, reference providers
  - `vscode.workspace` — file system events, configuration, workspace folders
  - `vscode.window` — status bar items, output channels, information/warning/error messages
  - `vscode.commands` — command registration and execution
  - `vscode.extensions` — extension activation events
- [ ] 3-11. **Extension host process**: sandboxed Node.js process for running extension code (Electron utility process)
- [ ] 3-12. **Activation events**: `onLanguage:*`, `onCommand:*`, `workspaceContains:*`, `*` (always)
- [ ] 3-13. **Curated essentials**: ship or auto-install on first use: Python, ESLint, Prettier, GitLens, Tailwind CSS IntelliSense, Error Lens
- [ ] 3-14. **Compatibility tier system**: classify extensions by compatibility (Full / Partial / Unsupported) based on which API surfaces they use. Show compatibility badge in marketplace UI.
- [ ] 3-15. **Native-first fallback**: for core capabilities (syntax, LSP, formatting, debug), prefer native integrations when an extension is unavailable or only partially compatible
- [ ] 3-16. **Failure isolation / safe mode**: bad extensions fail inside the extension host without taking down the whole app; startup safe mode allows launching with extensions disabled

### 4. DAN Skills Adapter
- [ ] 4-1. **Local skill scanner**: read `~/.dan/skills/` and `.dan/skills/` for `SKILL.md` files (existing `SkillStore` infrastructure)
- [ ] 4-2. **Skill metadata display**: parse frontmatter (name, description, triggers, author, version) and display in marketplace panel
- [ ] 4-3. **Skill Hub (remote)**: DAN Skills Hub API for browsing/downloading community skills (future — initially local-only)
- [ ] 4-4. **Install from URL**: `/skill import <url>` command, downloads and installs to `~/.dan/skills/`
- [ ] 4-5. **Skill authoring**: create new skill from within the app (opens SKILL.md template in Code mode editor)
- [ ] 4-6. **Skill activation**: enable/disable skills per workspace (maps to existing skill attachment on workflows/projects)
- [ ] 4-7. **Skill versioning**: track installed version, detect updates from Hub

### 5. MCP Server Adapter
- [ ] 5-1. **MCP server registry**: list of known MCP servers with metadata (name, description, tools provided, install command)
- [ ] 5-2. **Browse MCP servers**: search available MCP servers (from curated list or remote registry)
- [ ] 5-3. **Install/configure**: `npm install` or download MCP server, store config in `~/.dan/mcp/config.json`
- [ ] 5-4. **Auto-connect**: installed MCP servers auto-connect on app startup (existing `mcp_bridge` infrastructure)
- [ ] 5-5. **Tool catalog**: show which tools each MCP server provides, with usage examples
- [ ] 5-6. **Health status**: indicator showing connected/disconnected/error for each MCP server
- [ ] 5-7. **Chat integration**: installed MCP server tools appear as available chat capabilities
- [ ] 5-8. **Process isolation policy**: MCP servers run in isolated processes with explicit install/start/stop/log controls and visible failure states

### 6. Recipe Store Adapter (Future — Phase 3+)
- [ ] 6-1. **Recipe model**: distilled domain knowledge package (metadata + extracted patterns + memory items + domain vectors)
- [ ] 6-2. **Recipe browser**: search/browse/preview recipes by domain (supply chain, finance, medicine, etc.)
- [ ] 6-3. **Install recipe**: imports domain knowledge into DAN's memory kernel as domain-scoped memories
- [ ] 6-4. **Recipe marketplace**: buy/sell/share recipes (distilled from 100-paper experiments or other token-burning)
- [ ] 6-5. **Recipe creation**: export domain knowledge from a workspace as a packaged recipe
- [ ] 6-6. **Recipe versioning**: track recipe updates as more knowledge is distilled

### 7. Extensibility (Adding New Marketplaces)
- [ ] 7-1. **Plugin architecture**: new marketplaces added by implementing `MarketplaceRegistry` interface and registering with `MarketplaceManager`
- [ ] 7-2. **Configuration-driven**: marketplace adapters configurable via settings (enable/disable, custom registry URLs)
- [ ] 7-3. **Documentation**: guide for adding a new marketplace adapter (template, required methods, UI integration points)
- [ ] 7-4. **Potential future registries**: npm packages (for Node.js tools), pip packages (for Python tools), Docker images (for isolated environments), Homebrew formulas (for CLI tools), theme galleries, prompt libraries

## Decisions
- Marketplace support is layered on top of a useful native Code mode; Code mode must not depend on general extension-host parity to be shippable.
- Installable items are executable/trusted content, not just static assets. Every registry must participate in the same permission and trust model.
- VS Code compatibility will be curated and tiered, not all-or-nothing.
- Registry source and extension runtime are separate concerns. Open VSX may be the default source, but the architecture must support compatible VSIX imports and future source adapters.
- Extension failures must be isolated from the core IDE. A broken extension cannot be allowed to brick the editor startup path.

## Notes
- VS Code extension compatibility is the make-or-break feature for Code mode adoption. Without language support extensions, the editor is just a fancy text editor.
- Open VSX is the open marketplace alternative to Microsoft's VS Code Marketplace. It has many popular extensions and avoids Microsoft's proprietary API, but it is not a perfect mirror of the Microsoft marketplace. Keep the source layer swappable and support local VSIX install.
- Full VS Code extension API compatibility is unrealistic (and unnecessary). Focus on the subset that powers: syntax highlighting, themes, language servers, snippets, formatters, linters, and icon themes. This covers 80%+ of commonly used extensions.
- Consider building on top of OpenSumi or Eclipse Theia's extension host, which already implement significant portions of the VS Code extension API.
- The DAN Skills adapter is straightforward since the `SkillStore` infrastructure already exists. The marketplace UI just needs to surface it.
- MCP server integration is also partially built (Phase 19, plan 29-9). The marketplace adapter wraps existing functionality with a discovery/install UI.
- Recipe Store is the most novel marketplace — it doesn't exist in any other tool. This is where the 100-paper experiment output gets distributed. Defer to Phase 3+ but design the framework to accommodate it.
- The generic `MarketplaceRegistry` interface ensures we never need a "marketplace of marketplaces" — just register new adapters as they're built.

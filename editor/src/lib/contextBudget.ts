export interface SurfaceContextInput {
  activeFilePath: string | null;
  activeFileContent: string | null;
  activeFileLanguage: string | null;
  selectionText: string | null;
  openFilePaths: string[];
  importNeighbors: string[];
  project: {
    type: string;
    name: string;
    frameworks: string[];
    package_manager?: string;
  } | null;
  mode: string;
  workspace_id: string;
}

export interface BudgetedSurfaceContext {
  mode: string;
  workspace_id: string;
  active_file: {
    path: string;
    content: string;
    language: string;
  } | null;
  selection_text?: string;
  open_files: string[];
  import_neighbors: string[];
  project: {
    type: string;
    name: string;
    frameworks: string[];
    package_manager?: string;
  } | null;
}

const TOTAL_BUDGET = 15000;
const ACTIVE_FILE_BUDGET = 8000;
const SELECTION_BUDGET = 2000;
const IMPORT_NEIGHBORS_BUDGET = 4000;

function charLen(obj: unknown): number {
  return JSON.stringify(obj).length;
}

export function buildSurfaceContext(
  input: SurfaceContextInput,
): BudgetedSurfaceContext {
  let remaining = TOTAL_BUDGET;

  const base: BudgetedSurfaceContext = {
    mode: input.mode,
    workspace_id: input.workspace_id,
    active_file: null,
    open_files: [],
    import_neighbors: [],
    project: input.project,
  };

  if (input.project) {
    remaining -= charLen(input.project);
  }
  remaining -= charLen({ mode: input.mode, workspace_id: input.workspace_id });

  if (input.activeFilePath && input.activeFileContent) {
    const truncatedContent = input.activeFileContent.slice(
      0,
      Math.min(ACTIVE_FILE_BUDGET, remaining - 100),
    );
    base.active_file = {
      path: input.activeFilePath,
      content: truncatedContent,
      language: input.activeFileLanguage ?? "plaintext",
    };
    remaining -= charLen(base.active_file);
  }

  if (input.selectionText && remaining > 200) {
    const truncated = input.selectionText.slice(
      0,
      Math.min(SELECTION_BUDGET, remaining - 100),
    );
    base.selection_text = truncated;
    remaining -= truncated.length + 20;
  }

  if (input.importNeighbors.length > 0 && remaining > 200) {
    const budget = Math.min(IMPORT_NEIGHBORS_BUDGET, remaining - 50);
    const neighbors: string[] = [];
    let used = 2;
    for (const n of input.importNeighbors) {
      const entryLen = n.length + 3;
      if (used + entryLen > budget) break;
      neighbors.push(n);
      used += entryLen;
    }
    base.import_neighbors = neighbors;
    remaining -= used;
  }

  if (input.openFilePaths.length > 0 && remaining > 100) {
    const paths: string[] = [];
    let used = 2;
    for (const p of input.openFilePaths) {
      const entryLen = p.length + 3;
      if (used + entryLen > remaining - 10) break;
      paths.push(p);
      used += entryLen;
    }
    base.open_files = paths;
  }

  return base;
}

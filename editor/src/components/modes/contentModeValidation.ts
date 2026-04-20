import {
  extractFrontmatterSummary,
  inferSectionFromRelPath,
} from "./contentModeModel";

export interface ContentValidationIssue {
  id: string;
  severity: "error" | "warning";
  category: "frontmatter" | "content" | "knowledge-base";
  message: string;
  detail?: string;
}

export interface ContentDraftValidationInput {
  filePath: string | null;
  relPath: string | null;
  content: string;
  knownPages?: Array<{
    filePath: string;
    pageId: string | null;
  }>;
}

function pushIssue(
  issues: ContentValidationIssue[],
  issue: ContentValidationIssue,
) {
  if (!issues.some((existing) => existing.id === issue.id)) {
    issues.push(issue);
  }
}

function validateYamlLikeFrontmatter(raw: string, issues: ContentValidationIssue[]) {
  const seenKeys = new Set<string>();
  raw.split(/\r?\n/).forEach((line, index) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) return;
    if (/^\s/.test(line) || trimmed.startsWith("- ")) return;
    const keyMatch = line.match(/^['"]?([A-Za-z0-9_.-]+)['"]?\s*:/);
    if (!keyMatch) {
      pushIssue(issues, {
        id: `frontmatter:line:${index}`,
        severity: "error",
        category: "frontmatter",
        message: `Frontmatter line ${index + 1} does not look like valid YAML.`,
        detail: line,
      });
      return;
    }
    const key = keyMatch[1];
    if (seenKeys.has(key)) {
      pushIssue(issues, {
        id: `frontmatter:duplicate:${key}`,
        severity: "warning",
        category: "frontmatter",
        message: `Frontmatter key '${key}' appears more than once.`,
      });
      return;
    }
    seenKeys.add(key);
  });
}

export function validateContentDraft(
  input: ContentDraftValidationInput,
): ContentValidationIssue[] {
  const issues: ContentValidationIssue[] = [];

  if (!input.filePath) {
    pushIssue(issues, {
      id: "content:no-file",
      severity: "error",
      category: "content",
      message: "Select a page before saving.",
    });
    return issues;
  }

  if (!input.relPath || !input.relPath.endsWith(".md")) {
    pushIssue(issues, {
      id: "content:not-markdown",
      severity: "error",
      category: "content",
      message: "Content mode can only save Markdown page files.",
      detail: input.relPath ?? input.filePath,
    });
  }

  if (input.content.startsWith("+++") || input.content.startsWith("{")) {
    pushIssue(issues, {
      id: "frontmatter:unsupported-style",
      severity: "warning",
      category: "frontmatter",
      message:
        "This page uses non-YAML frontmatter. Content mode validation currently assumes YAML-style frontmatter.",
    });
  }

  if (input.content.startsWith("---")) {
    const closingMatch = input.content.match(/^---\s*\n[\s\S]*?\n---\s*(?:\n|$)/);
    if (!closingMatch) {
      pushIssue(issues, {
        id: "frontmatter:missing-close",
        severity: "error",
        category: "frontmatter",
        message: "Frontmatter opens with '---' but never closes.",
      });
      return issues;
    }
  }

  const frontmatter = extractFrontmatterSummary(input.content);
  if (frontmatter.raw) {
    validateYamlLikeFrontmatter(frontmatter.raw, issues);
  }

  if (input.relPath) {
    const section = inferSectionFromRelPath(input.relPath);
    if (section === "root" && !frontmatter.title) {
      pushIssue(issues, {
        id: "content:root-title-missing",
        severity: "warning",
        category: "content",
        message:
          "Root-level pages usually benefit from an explicit frontmatter title.",
      });
    }
  }

  if (frontmatter.pageId && input.knownPages) {
    const duplicate = input.knownPages.find(
      (page) =>
        page.filePath !== input.filePath && page.pageId === frontmatter.pageId,
    );
    if (duplicate) {
      pushIssue(issues, {
        id: `knowledge-base:duplicate-pageid:${frontmatter.pageId}`,
        severity: "warning",
        category: "knowledge-base",
        message: `pageID '${frontmatter.pageId}' is already used by another page.`,
        detail: duplicate.filePath,
      });
    }
  }

  if (!frontmatter.pageId) {
    pushIssue(issues, {
      id: "knowledge-base:missing-pageid",
      severity: "warning",
      category: "knowledge-base",
      message:
        "This page does not declare a pageID yet. Cross-page references and tooling work better when pages keep stable IDs.",
    });
  }

  return issues;
}

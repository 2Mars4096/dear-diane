import { nativeFs } from "./electronBridge";

export interface FileCoverage {
  lines: Record<number, number>; // line → hit count (0 = uncovered)
  branches?: Record<string, { covered: number; total: number }>;
}

export interface CoverageData {
  files: Record<string, FileCoverage>;
  summary: { lines: number; covered: number; percentage: number };
}

function computeSummary(files: Record<string, FileCoverage>): CoverageData["summary"] {
  let total = 0;
  let covered = 0;
  for (const fc of Object.values(files)) {
    for (const hits of Object.values(fc.lines)) {
      total++;
      if (hits > 0) covered++;
    }
  }
  return { lines: total, covered, percentage: total > 0 ? Math.round((covered / total) * 1000) / 10 : 0 };
}

export function parseLcov(content: string): CoverageData {
  const files: Record<string, FileCoverage> = {};
  let currentFile: string | null = null;

  for (const rawLine of content.split("\n")) {
    const line = rawLine.trim();
    if (line.startsWith("SF:")) {
      currentFile = line.slice(3);
      if (!files[currentFile]) files[currentFile] = { lines: {} };
    } else if (line.startsWith("DA:") && currentFile) {
      const parts = line.slice(3).split(",");
      const lineNum = parseInt(parts[0], 10);
      const hits = parseInt(parts[1], 10);
      if (!isNaN(lineNum)) {
        files[currentFile].lines[lineNum] = hits;
      }
    } else if (line.startsWith("BRDA:") && currentFile) {
      const parts = line.slice(5).split(",");
      if (parts.length >= 4) {
        const branchId = `${parts[0]}:${parts[1]}`;
        if (!files[currentFile].branches) files[currentFile].branches = {};
        const existing = files[currentFile].branches![branchId] ?? { covered: 0, total: 0 };
        existing.total++;
        if (parts[3] !== "-" && parseInt(parts[3], 10) > 0) existing.covered++;
        files[currentFile].branches![branchId] = existing;
      }
    } else if (line === "end_of_record") {
      currentFile = null;
    }
  }

  return { files, summary: computeSummary(files) };
}

export function parseIstanbulJson(content: string): CoverageData {
  const files: Record<string, FileCoverage> = {};
  let parsed: Record<string, any>;
  try {
    parsed = JSON.parse(content);
  } catch {
    return { files: {}, summary: { lines: 0, covered: 0, percentage: 0 } };
  }

  for (const [filePath, data] of Object.entries(parsed)) {
    const fc: FileCoverage = { lines: {} };

    // Statement map → statement hits
    if (data.statementMap && data.s) {
      for (const [id, range] of Object.entries<any>(data.statementMap)) {
        const lineNum = range.start?.line;
        if (typeof lineNum === "number") {
          const hits = data.s[id] ?? 0;
          // Take max hit count if multiple statements on same line
          fc.lines[lineNum] = Math.max(fc.lines[lineNum] ?? 0, hits);
        }
      }
    }

    // Branch map
    if (data.branchMap && data.b) {
      fc.branches = {};
      for (const [id, branchInfo] of Object.entries<any>(data.branchMap)) {
        const counts: number[] = data.b[id] ?? [];
        const total = counts.length;
        const covered = counts.filter((c: number) => c > 0).length;
        fc.branches[`${branchInfo.loc?.start?.line ?? id}:${id}`] = { covered, total };
      }
    }

    files[filePath] = fc;
  }

  return { files, summary: computeSummary(files) };
}

export function parseCoverageXml(content: string): CoverageData {
  const files: Record<string, FileCoverage> = {};

  // Simple XML parser using regex (avoids DOM dependency in renderer)
  const packageMatches = content.matchAll(/<package[^>]*>([\s\S]*?)<\/package>/gi);
  for (const pkgMatch of packageMatches) {
    const pkgContent = pkgMatch[1];
    const classMatches = pkgContent.matchAll(
      /<class[^>]*\bfilename="([^"]*)"[^>]*>([\s\S]*?)<\/class>/gi,
    );
    for (const classMatch of classMatches) {
      const filename = classMatch[1];
      const classContent = classMatch[2];
      const fc: FileCoverage = { lines: {} };

      const lineRegex = new RegExp('<line\\s+number="(\\d+)"\\s+hits="(\\d+)"[^/]*\\/>', "gi");
      const lineMatches = classContent.matchAll(lineRegex);
      for (const lm of lineMatches) {
        const lineNum = parseInt(lm[1], 10);
        const hits = parseInt(lm[2], 10);
        if (!isNaN(lineNum)) {
          fc.lines[lineNum] = hits;
        }
      }

      // Branch parsing from line elements with branch="true"
      const branchRegex = new RegExp('<line\\s+number="(\\d+)"[^>]*\\bbranch="true"[^>]*\\bcondition-coverage="[^"]*\\((\\d+)\\/(\\d+)\\)"[^/]*\\/>', "gi");
      const branchLineMatches = classContent.matchAll(branchRegex);
      for (const blm of branchLineMatches) {
        if (!fc.branches) fc.branches = {};
        const lineNum = blm[1];
        const covered = parseInt(blm[2], 10);
        const total = parseInt(blm[3], 10);
        fc.branches[`line:${lineNum}`] = { covered, total };
      }

      files[filename] = fc;
    }
  }

  return { files, summary: computeSummary(files) };
}

const COVERAGE_PATHS = [
  "coverage/lcov.info",
  "coverage/coverage-final.json",
  "coverage/lcov-report/lcov.info",
  "htmlcov/coverage.xml",
  "coverage.xml",
  "cover/coverage.info",
  "coverage/cobertura-coverage.xml",
];

export async function loadCoverage(
  rootPath: string,
): Promise<CoverageData | null> {
  for (const p of COVERAGE_PATHS) {
    const fullPath = `${rootPath}/${p}`;
    const content = await nativeFs.readFile(fullPath);
    if (content) {
      try {
        if (p.endsWith(".info")) return parseLcov(content);
        if (p.endsWith(".json")) return parseIstanbulJson(content);
        if (p.endsWith(".xml")) return parseCoverageXml(content);
      } catch {
        continue;
      }
    }
  }
  return null;
}

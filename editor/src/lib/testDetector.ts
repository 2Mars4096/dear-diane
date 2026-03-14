import { nativeFs } from "./electronBridge";

export interface TestFramework {
  id: string;
  name: string;
  command: string;
  args: string[];
  filePattern: RegExp;
  parseOutput: (output: string) => TestResult[];
}

export interface TestItem {
  id: string;
  label: string;
  filePath: string;
  line?: number;
  children: TestItem[];
  status: "unknown" | "running" | "passed" | "failed" | "skipped";
  duration?: number;
  error?: string;
}

export interface TestResult {
  name: string;
  filePath?: string;
  line?: number;
  status: "passed" | "failed" | "skipped";
  duration?: number;
  error?: string;
}

function parseJestOutput(output: string): TestResult[] {
  try {
    const json = JSON.parse(output);
    const results: TestResult[] = [];
    for (const suite of json.testResults ?? []) {
      for (const test of suite.assertionResults ?? []) {
        results.push({
          name: test.fullName ?? test.title,
          filePath: suite.name,
          status:
            test.status === "passed"
              ? "passed"
              : test.status === "pending"
                ? "skipped"
                : "failed",
          duration: test.duration,
          error: test.failureMessages?.join("\n"),
        });
      }
    }
    return results;
  } catch {
    return [];
  }
}

function parseVitestOutput(output: string): TestResult[] {
  try {
    const json = JSON.parse(output);
    const results: TestResult[] = [];
    for (const file of json.testResults ?? []) {
      for (const test of file.assertionResults ?? []) {
        results.push({
          name: test.fullName ?? test.title ?? test.ancestorTitles?.join(" > "),
          filePath: file.name,
          status:
            test.status === "passed"
              ? "passed"
              : test.status === "pending" || test.status === "skipped"
                ? "skipped"
                : "failed",
          duration: test.duration,
          error: test.failureMessages?.join("\n"),
        });
      }
    }
    return results;
  } catch {
    return [];
  }
}

function parsePytestOutput(output: string): TestResult[] {
  try {
    const json = JSON.parse(output);
    const results: TestResult[] = [];
    for (const test of json.tests ?? []) {
      results.push({
        name: test.nodeid ?? test.name,
        filePath: test.nodeid?.split("::")[0],
        status:
          test.outcome === "passed"
            ? "passed"
            : test.outcome === "skipped"
              ? "skipped"
              : "failed",
        duration: test.duration,
        error: test.longrepr ?? test.call?.longrepr,
      });
    }
    return results;
  } catch {
    return parsePytestVerbose(output);
  }
}

function parsePytestVerbose(output: string): TestResult[] {
  const results: TestResult[] = [];
  const lines = output.split("\n");
  for (const line of lines) {
    const m = line.match(/^([\w/.]+::[\w:]+)\s+(PASSED|FAILED|SKIPPED)/);
    if (m) {
      results.push({
        name: m[1],
        filePath: m[1].split("::")[0],
        status: m[2].toLowerCase() as TestResult["status"],
      });
    }
  }
  return results;
}

function parseGoTestOutput(output: string): TestResult[] {
  const results: TestResult[] = [];
  const lines = output.split("\n");
  for (const line of lines) {
    try {
      const ev = JSON.parse(line);
      if (ev.Action === "pass" || ev.Action === "fail" || ev.Action === "skip") {
        if (ev.Test) {
          results.push({
            name: `${ev.Package}/${ev.Test}`,
            filePath: ev.Package,
            status:
              ev.Action === "pass"
                ? "passed"
                : ev.Action === "skip"
                  ? "skipped"
                  : "failed",
            duration: ev.Elapsed != null ? Math.round(ev.Elapsed * 1000) : undefined,
          });
        }
      }
    } catch {
      // non-JSON line, skip
    }
  }
  return results;
}

const FRAMEWORKS: TestFramework[] = [
  {
    id: "jest",
    name: "Jest",
    command: "npx",
    args: ["jest", "--json", "--verbose"],
    filePattern: /\.(test|spec)\.(ts|tsx|js|jsx)$/,
    parseOutput: parseJestOutput,
  },
  {
    id: "vitest",
    name: "Vitest",
    command: "npx",
    args: ["vitest", "run", "--reporter=json"],
    filePattern: /\.(test|spec)\.(ts|tsx|js|jsx)$/,
    parseOutput: parseVitestOutput,
  },
  {
    id: "pytest",
    name: "pytest",
    command: "pytest",
    args: ["--tb=short", "-v"],
    filePattern: /test_.*\.py$|.*_test\.py$/,
    parseOutput: parsePytestOutput,
  },
  {
    id: "go",
    name: "Go Test",
    command: "go",
    args: ["test", "-json", "./..."],
    filePattern: /_test\.go$/,
    parseOutput: parseGoTestOutput,
  },
];

export async function detectTestFramework(
  rootPath: string,
): Promise<TestFramework | null> {
  try {
    const pkg = await nativeFs.readFile(`${rootPath}/package.json`);
    if (pkg) {
      const parsed = JSON.parse(pkg);
      const deps = { ...parsed.dependencies, ...parsed.devDependencies };
      if (deps.vitest) return FRAMEWORKS.find((f) => f.id === "vitest")!;
      if (deps.jest || deps["@jest/core"])
        return FRAMEWORKS.find((f) => f.id === "jest")!;
    }
  } catch {
    /* no package.json */
  }

  try {
    if (
      (await nativeFs.exists(`${rootPath}/pyproject.toml`)) ||
      (await nativeFs.exists(`${rootPath}/pytest.ini`)) ||
      (await nativeFs.exists(`${rootPath}/setup.cfg`))
    ) {
      return FRAMEWORKS.find((f) => f.id === "pytest")!;
    }
  } catch {
    /* skip */
  }

  try {
    if (await nativeFs.exists(`${rootPath}/go.mod`)) {
      return FRAMEWORKS.find((f) => f.id === "go")!;
    }
  } catch {
    /* skip */
  }

  return null;
}

export async function discoverTests(
  rootPath: string,
  framework: TestFramework,
): Promise<TestItem[]> {
  const items: TestItem[] = [];

  async function walk(dir: string) {
    try {
      const entries = await nativeFs.readDir(dir);
      if (!entries) return;
      for (const entry of entries) {
        const fullPath = `${dir}/${entry.name}`;
        if (entry.isDirectory) {
          if (
            entry.name === "node_modules" ||
            entry.name === ".git" ||
            entry.name === "__pycache__" ||
            entry.name === ".venv" ||
            entry.name === "venv"
          )
            continue;
          await walk(fullPath);
        } else if (framework.filePattern.test(entry.name)) {
          items.push({
            id: fullPath,
            label: fullPath.replace(rootPath + "/", ""),
            filePath: fullPath,
            children: [],
            status: "unknown",
          });
        }
      }
    } catch {
      /* directory read error */
    }
  }

  await walk(rootPath);
  return items;
}

export function updateTestResults(
  items: TestItem[],
  results: TestResult[],
): TestItem[] {
  return items.map((item) => {
    const matching = results.filter(
      (r) =>
        r.filePath === item.filePath ||
        r.filePath?.endsWith(item.label) ||
        item.filePath.endsWith(r.filePath ?? ""),
    );

    if (matching.length > 0) {
      const allPassed = matching.every((r) => r.status === "passed");
      const anyFailed = matching.some((r) => r.status === "failed");
      const children: TestItem[] = matching.map((r) => ({
        id: `${item.id}::${r.name}`,
        label: r.name.split("::").pop() ?? r.name,
        filePath: r.filePath ?? item.filePath,
        line: r.line,
        children: [],
        status: r.status,
        duration: r.duration,
        error: r.error,
      }));

      return {
        ...item,
        status: anyFailed ? "failed" : allPassed ? "passed" : "unknown",
        children,
      };
    }
    return { ...item, children: updateTestResults(item.children, results) };
  });
}

import { nativeFs } from "./electronBridge";

export interface DetectedTask {
  id: string;
  label: string;
  command: string;
  args: string[];
  cwd: string;
  source: string;
  group?: "build" | "test" | "clean" | "lint" | "start" | "other";
  isDefault?: boolean;
}

export async function detectTasks(rootPath: string): Promise<DetectedTask[]> {
  const tasks: DetectedTask[] = [];

  await Promise.allSettled([
    detectNpmScripts(rootPath, tasks),
    detectMakeTargets(rootPath, tasks),
    detectPythonScripts(rootPath, tasks),
    detectCargoTasks(rootPath, tasks),
  ]);

  return tasks;
}

async function detectNpmScripts(rootPath: string, tasks: DetectedTask[]) {
  const pkg = await nativeFs.readFile(`${rootPath}/package.json`);
  if (!pkg) return;
  const parsed = JSON.parse(pkg);
  for (const [name, cmd] of Object.entries(parsed.scripts ?? {})) {
    if (typeof cmd !== "string") continue;
    tasks.push({
      id: `npm:${name}`,
      label: `npm: ${name}`,
      command: "npm",
      args: ["run", name],
      cwd: rootPath,
      source: "package.json",
      group: categorizeNpmScript(name),
    });
  }
}

async function detectMakeTargets(rootPath: string, tasks: DetectedTask[]) {
  const makefile = await nativeFs.readFile(`${rootPath}/Makefile`);
  if (!makefile) return;
  const targets = makefile.match(/^([a-zA-Z_][\w-]*)\s*:/gm);
  if (!targets) return;
  for (const target of targets) {
    const name = target.replace(":", "").trim();
    if (name.startsWith(".") || name === "PHONY") continue;
    tasks.push({
      id: `make:${name}`,
      label: `make: ${name}`,
      command: "make",
      args: [name],
      cwd: rootPath,
      source: "Makefile",
      group: categorizeMakeTarget(name),
    });
  }
}

async function detectPythonScripts(rootPath: string, tasks: DetectedTask[]) {
  const toml = await nativeFs.readFile(`${rootPath}/pyproject.toml`);
  if (!toml) return;

  const scriptSection = toml.match(
    /\[(?:tool\.poetry\.scripts|project\.scripts)\]([\s\S]*?)(?:\[|$)/,
  );
  if (scriptSection) {
    const lines = scriptSection[1].trim().split("\n");
    for (const line of lines) {
      const match = line.match(/^(\w+)\s*=\s*"(.+)"/);
      if (match) {
        tasks.push({
          id: `py:${match[1]}`,
          label: `python: ${match[1]}`,
          command: "python",
          args: ["-m", match[2]],
          cwd: rootPath,
          source: "pyproject.toml",
          group: "other",
        });
      }
    }
  }

  const hasPytest =
    toml.includes("[tool.pytest") ||
    (await nativeFs.exists(`${rootPath}/pytest.ini`));
  if (hasPytest) {
    tasks.push({
      id: "pytest",
      label: "pytest",
      command: "pytest",
      args: ["-v"],
      cwd: rootPath,
      source: "pyproject.toml",
      group: "test",
    });
  }
}

async function detectCargoTasks(rootPath: string, tasks: DetectedTask[]) {
  if (!(await nativeFs.exists(`${rootPath}/Cargo.toml`))) return;
  tasks.push(
    {
      id: "cargo:build",
      label: "cargo build",
      command: "cargo",
      args: ["build"],
      cwd: rootPath,
      source: "Cargo.toml",
      group: "build",
    },
    {
      id: "cargo:test",
      label: "cargo test",
      command: "cargo",
      args: ["test"],
      cwd: rootPath,
      source: "Cargo.toml",
      group: "test",
    },
    {
      id: "cargo:run",
      label: "cargo run",
      command: "cargo",
      args: ["run"],
      cwd: rootPath,
      source: "Cargo.toml",
      group: "start",
    },
  );
}

function categorizeNpmScript(name: string): DetectedTask["group"] {
  if (name.includes("build") || name.includes("compile")) return "build";
  if (name.includes("test") || name.includes("spec")) return "test";
  if (name.includes("lint") || name.includes("check")) return "lint";
  if (
    name.includes("start") ||
    name.includes("dev") ||
    name.includes("serve")
  )
    return "start";
  if (name.includes("clean")) return "clean";
  return "other";
}

function categorizeMakeTarget(name: string): DetectedTask["group"] {
  if (name === "build" || name === "all" || name === "compile") return "build";
  if (name === "test" || name === "check") return "test";
  if (name === "clean") return "clean";
  if (name === "lint") return "lint";
  return "other";
}

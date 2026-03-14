import { nativeFs } from "./electronBridge";

export interface ProjectDetection {
  type: string;
  name: string;
  version?: string;
  frameworks: string[];
  packageManager?: string;
  recommendedExtensions: string[];
  detectedFiles: string[];
}

export async function detectProjectType(
  rootPath: string,
): Promise<ProjectDetection> {
  const detection: ProjectDetection = {
    type: "unknown",
    name: "Project",
    frameworks: [],
    recommendedExtensions: [],
    detectedFiles: [],
  };

  const checks = await Promise.allSettled([
    detectNode(rootPath, detection),
    detectPython(rootPath, detection),
    detectRust(rootPath, detection),
    detectGo(rootPath, detection),
    detectDocker(rootPath, detection),
  ]);

  // Name fallback: use the last path segment
  if (detection.name === "Project") {
    const seg = rootPath.split("/").filter(Boolean).pop();
    if (seg) detection.name = seg;
  }

  void checks; // all errors swallowed inside each detector
  return detection;
}

async function detectNode(
  rootPath: string,
  d: ProjectDetection,
): Promise<void> {
  const pkg = await nativeFs.readFile(`${rootPath}/package.json`);
  if (!pkg) return;

  let parsed: Record<string, any>;
  try {
    parsed = JSON.parse(pkg);
  } catch {
    return;
  }

  d.type = "node";
  d.name = parsed.name ?? d.name;
  d.version = parsed.version;
  d.detectedFiles.push("package.json");

  const allDeps: Record<string, string> = {
    ...parsed.dependencies,
    ...parsed.devDependencies,
  };

  const [hasPnpm, hasYarn] = await Promise.all([
    nativeFs.exists(`${rootPath}/pnpm-lock.yaml`),
    nativeFs.exists(`${rootPath}/yarn.lock`),
  ]);
  d.packageManager = hasPnpm ? "pnpm" : hasYarn ? "yarn" : "npm";

  const frameworkMap: Record<string, string> = {
    react: "React",
    next: "Next.js",
    vue: "Vue",
    svelte: "Svelte",
    express: "Express",
    fastify: "Fastify",
    electron: "Electron",
    tailwindcss: "Tailwind CSS",
  };
  for (const [dep, label] of Object.entries(frameworkMap)) {
    if (allDeps[dep]) d.frameworks.push(label);
  }

  const extMap: Record<string, string> = {
    typescript: "TypeScript",
    tailwindcss: "Tailwind CSS IntelliSense",
    eslint: "ESLint",
    prettier: "Prettier",
    react: "ES7+ React Snippets",
    vue: "Vue - Official",
  };
  for (const [dep, label] of Object.entries(extMap)) {
    if (allDeps[dep]) d.recommendedExtensions.push(label);
  }
  if (allDeps.jest || allDeps.vitest)
    d.recommendedExtensions.push("Test Explorer");
}

async function detectPython(
  rootPath: string,
  d: ProjectDetection,
): Promise<void> {
  const [hasPyproject, hasRequirements, hasSetupPy] = await Promise.all([
    nativeFs.exists(`${rootPath}/pyproject.toml`),
    nativeFs.exists(`${rootPath}/requirements.txt`),
    nativeFs.exists(`${rootPath}/setup.py`),
  ]);

  if (!hasPyproject && !hasRequirements && !hasSetupPy) return;

  if (d.type === "unknown") d.type = "python";
  d.packageManager = d.packageManager ?? "pip";
  d.recommendedExtensions.push("Python", "Pylance");

  if (hasPyproject) {
    d.detectedFiles.push("pyproject.toml");
    const toml = (await nativeFs.readFile(`${rootPath}/pyproject.toml`)) ?? "";
    if (toml.includes("fastapi")) d.frameworks.push("FastAPI");
    if (toml.includes("django")) d.frameworks.push("Django");
    if (toml.includes("flask")) d.frameworks.push("Flask");
    if (toml.includes("pytest")) d.recommendedExtensions.push("pytest");
  }
  if (hasRequirements) d.detectedFiles.push("requirements.txt");
  if (hasSetupPy) d.detectedFiles.push("setup.py");
}

async function detectRust(
  rootPath: string,
  d: ProjectDetection,
): Promise<void> {
  if (!(await nativeFs.exists(`${rootPath}/Cargo.toml`))) return;
  if (d.type === "unknown") d.type = "rust";
  d.packageManager = d.packageManager ?? "cargo";
  d.detectedFiles.push("Cargo.toml");
  d.recommendedExtensions.push("rust-analyzer");
}

async function detectGo(
  rootPath: string,
  d: ProjectDetection,
): Promise<void> {
  if (!(await nativeFs.exists(`${rootPath}/go.mod`))) return;
  if (d.type === "unknown") d.type = "go";
  d.packageManager = d.packageManager ?? "go";
  d.detectedFiles.push("go.mod");
  d.recommendedExtensions.push("Go");
}

async function detectDocker(
  rootPath: string,
  d: ProjectDetection,
): Promise<void> {
  const [hasDockerfile, hasCompose] = await Promise.all([
    nativeFs.exists(`${rootPath}/Dockerfile`),
    nativeFs.exists(`${rootPath}/docker-compose.yml`),
  ]);
  if (!hasDockerfile && !hasCompose) return;
  if (hasDockerfile) d.detectedFiles.push("Dockerfile");
  if (hasCompose) d.detectedFiles.push("docker-compose.yml");
  d.recommendedExtensions.push("Docker");
}

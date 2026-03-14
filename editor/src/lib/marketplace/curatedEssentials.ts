export interface EssentialExtension {
  id: string;
  name: string;
  description: string;
  category: string;
  autoSuggest: boolean;
  projectTypes: string[];
}

export const CURATED_ESSENTIALS: EssentialExtension[] = [
  {
    id: "dbaeumer.vscode-eslint",
    name: "ESLint",
    description: "Integrates ESLint JavaScript into VS Code",
    category: "Linters",
    autoSuggest: true,
    projectTypes: ["node"],
  },
  {
    id: "esbenp.prettier-vscode",
    name: "Prettier",
    description: "Code formatter using prettier",
    category: "Formatters",
    autoSuggest: true,
    projectTypes: ["node"],
  },
  {
    id: "eamodio.gitlens",
    name: "GitLens",
    description: "Supercharge Git within VS Code",
    category: "Source Control",
    autoSuggest: false,
    projectTypes: [],
  },
  {
    id: "bradlc.vscode-tailwindcss",
    name: "Tailwind CSS IntelliSense",
    description: "Intelligent Tailwind CSS tooling",
    category: "CSS",
    autoSuggest: true,
    projectTypes: ["node"],
  },
  {
    id: "usernamehw.errorlens",
    name: "Error Lens",
    description: "Improve highlighting of errors, warnings and other language diagnostics",
    category: "Utilities",
    autoSuggest: false,
    projectTypes: [],
  },
  {
    id: "ms-python.python",
    name: "Python",
    description: "Python language support (note: basic support is built-in via pyright)",
    category: "Languages",
    autoSuggest: true,
    projectTypes: ["python"],
  },
  {
    id: "rust-lang.rust-analyzer",
    name: "rust-analyzer",
    description: "Rust language support",
    category: "Languages",
    autoSuggest: true,
    projectTypes: ["rust"],
  },
  {
    id: "golang.go",
    name: "Go",
    description: "Go language support",
    category: "Languages",
    autoSuggest: true,
    projectTypes: ["go"],
  },
  {
    id: "pkief.material-icon-theme",
    name: "Material Icon Theme",
    description: "Material Design Icons for Visual Studio Code",
    category: "Themes",
    autoSuggest: false,
    projectTypes: [],
  },
  {
    id: "dracula-theme.theme-dracula",
    name: "Dracula Official",
    description: "Dark theme for many editors",
    category: "Themes",
    autoSuggest: false,
    projectTypes: [],
  },
];

export function getEssentialsForProject(
  projectType: string,
): EssentialExtension[] {
  return CURATED_ESSENTIALS.filter(
    (e) =>
      e.autoSuggest &&
      (e.projectTypes.length === 0 || e.projectTypes.includes(projectType)),
  );
}

export function getAllEssentials(): EssentialExtension[] {
  return CURATED_ESSENTIALS;
}

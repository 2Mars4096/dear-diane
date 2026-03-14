export interface RecipeMetadata {
  id: string;
  name: string;
  domain: string;
  subdomain?: string;
  description: string;
  author: string;
  version: string;
  createdAt: number;
  updatedAt: number;
  paperCount: number;
  tokensBurned: number;
  qualityScore?: number;
  tags: string[];
  icon?: string;
}

export interface DomainVector {
  concept: string;
  associations: Array<{ term: string; weight: number }>;
}

export interface ExtractedPattern {
  type: "methodology" | "terminology" | "citation_norm" | "rhetorical_style" | "convention" | "finding";
  name: string;
  description: string;
  examples: string[];
  frequency: number;
  confidence: number;
}

export interface MemoryItem {
  key: string;
  content: string;
  source: string;
  domain: string;
  importance: number;
  createdAt: number;
}

export interface Recipe {
  metadata: RecipeMetadata;
  domainVectors: DomainVector[];
  patterns: ExtractedPattern[];
  memories: MemoryItem[];
  paperSources: Array<{
    title: string;
    authors: string[];
    year: number;
    venue?: string;
    doi?: string;
  }>;
  qualityBenchmark?: {
    beforeScore: number;
    afterScore: number;
    evaluationMethod: string;
    sampleSize: number;
  };
}

export function createEmptyRecipe(domain: string, author: string): Recipe {
  return {
    metadata: {
      id: `recipe-${domain.toLowerCase().replace(/\s+/g, "-")}-${Date.now()}`,
      name: `${domain} Domain Recipe`,
      domain,
      description: `Distilled knowledge from papers in ${domain}`,
      author,
      version: "0.1.0",
      createdAt: Date.now(),
      updatedAt: Date.now(),
      paperCount: 0,
      tokensBurned: 0,
      tags: [domain.toLowerCase()],
    },
    domainVectors: [],
    patterns: [],
    memories: [],
    paperSources: [],
  };
}

export function serializeRecipe(recipe: Recipe): string {
  return JSON.stringify(recipe, null, 2);
}

export function deserializeRecipe(json: string): Recipe {
  return JSON.parse(json);
}

const RECIPE_DIR = ".dan/recipes";

export function getRecipeStoragePath(): string {
  return RECIPE_DIR;
}

export function bumpRecipeVersion(recipe: Recipe, type: "patch" | "minor" | "major"): Recipe {
  const parts = recipe.metadata.version.split(".");
  const major = parseInt(parts[0], 10) || 0;
  const minor = parseInt(parts[1], 10) || 0;
  const patch = parseInt(parts[2], 10) || 0;
  let newVersion: string;
  switch (type) {
    case "major": newVersion = `${major + 1}.0.0`; break;
    case "minor": newVersion = `${major}.${minor + 1}.0`; break;
    case "patch": newVersion = `${major}.${minor}.${patch + 1}`; break;
  }
  return {
    ...recipe,
    metadata: {
      ...recipe.metadata,
      version: newVersion,
      updatedAt: Date.now(),
    },
  };
}

export function mergeRecipeKnowledge(base: Recipe, newKnowledge: Partial<Recipe>): Recipe {
  return {
    ...base,
    domainVectors: [...base.domainVectors, ...(newKnowledge.domainVectors ?? [])],
    patterns: [...base.patterns, ...(newKnowledge.patterns ?? [])],
    memories: [...base.memories, ...(newKnowledge.memories ?? [])],
    paperSources: [...base.paperSources, ...(newKnowledge.paperSources ?? [])],
    metadata: {
      ...base.metadata,
      updatedAt: Date.now(),
      paperCount: base.metadata.paperCount + (newKnowledge.metadata?.paperCount ?? 0),
      tokensBurned: base.metadata.tokensBurned + (newKnowledge.metadata?.tokensBurned ?? 0),
    },
  };
}

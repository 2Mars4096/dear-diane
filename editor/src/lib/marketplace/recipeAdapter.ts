import type {
  MarketplaceRegistry,
  MarketplaceItem,
  MarketplaceItemDetail,
  InstalledItem,
  SearchOptions,
} from "./types";
import type { Recipe } from "./recipeModel";
import { deserializeRecipe } from "./recipeModel";
import { nativeFs } from "../electronBridge";

const SAMPLE_RECIPES: MarketplaceItem[] = [
  {
    id: "recipe-supply-chain-mgmt",
    name: "Supply Chain Management",
    displayName: "Supply Chain Management",
    description:
      "Distilled from 100 papers in supply chain optimization, disruption management, and dual-sourcing strategies. Includes M&SOM, POM, and Management Science patterns.",
    publisher: "DAN Lab",
    version: "1.0.0",
    rating: 4.8,
    downloadCount: 156,
    categories: ["Recipes", "Operations"],
    tags: ["supply-chain", "operations", "management-science", "disruption", "inventory"],
    registry: "recipes",
    icon: "package",
  },
  {
    id: "recipe-empirical-finance",
    name: "Empirical Finance",
    displayName: "Empirical Finance",
    description:
      "Distilled from 100 papers in empirical asset pricing, factor models, and market microstructure. Covers JF, JFE, RFS conventions.",
    publisher: "DAN Lab",
    version: "1.0.0",
    rating: 4.6,
    downloadCount: 89,
    categories: ["Recipes", "Finance"],
    tags: ["finance", "asset-pricing", "econometrics", "factor-models"],
    registry: "recipes",
    icon: "trending-up",
  },
  {
    id: "recipe-causal-inference",
    name: "Causal Inference & Econometrics",
    displayName: "Causal Inference & Econometrics",
    description:
      "Distilled from 100 papers using DiD, RDD, IV, and synthetic control methods. AER, Econometrica, QJE style patterns.",
    publisher: "DAN Lab",
    version: "1.0.0",
    rating: 4.9,
    downloadCount: 234,
    categories: ["Recipes", "Economics"],
    tags: ["causal-inference", "econometrics", "did", "rdd", "iv"],
    registry: "recipes",
    icon: "git-branch",
  },
  {
    id: "recipe-nlp-transformers",
    name: "NLP & Transformer Models",
    displayName: "NLP & Transformer Models",
    description:
      "Distilled from 100 papers on transformer architectures, attention mechanisms, and pre-training strategies. ACL, EMNLP, NeurIPS style.",
    publisher: "DAN Lab",
    version: "1.0.0",
    rating: 4.7,
    downloadCount: 312,
    categories: ["Recipes", "ML/AI"],
    tags: ["nlp", "transformers", "attention", "pre-training", "deep-learning"],
    registry: "recipes",
    icon: "cpu",
  },
  {
    id: "recipe-clinical-trials",
    name: "Clinical Trial Design",
    displayName: "Clinical Trial Design",
    description:
      "Distilled from 100 papers on adaptive trial design, Bayesian methods, and regulatory requirements. NEJM, Lancet, BMJ conventions.",
    publisher: "DAN Lab",
    version: "1.0.0",
    rating: 4.5,
    downloadCount: 67,
    categories: ["Recipes", "Medicine"],
    tags: ["clinical-trials", "biostatistics", "bayesian", "regulatory"],
    registry: "recipes",
    icon: "heart-pulse",
  },
];

export class RecipeStoreAdapter implements MarketplaceRegistry {
  id = "recipes";
  name = "Recipe Store";
  icon = "chef-hat";

  async search(query: string, opts?: SearchOptions): Promise<MarketplaceItem[]> {
    const local = await this.listInstalled();
    const all = [
      ...SAMPLE_RECIPES.filter((s) => !local.some((l) => l.id === s.id)),
      ...local,
    ];

    let results: MarketplaceItem[];
    if (!query) {
      results = all;
    } else {
      const q = query.toLowerCase();
      results = all.filter(
        (r) =>
          r.name.toLowerCase().includes(q) ||
          r.description.toLowerCase().includes(q) ||
          r.tags.some((t) => t.toLowerCase().includes(q)) ||
          r.categories.some((c) => c.toLowerCase().includes(q)),
      );
    }

    if (opts?.category) {
      results = results.filter((r) => r.categories.includes(opts.category!));
    }

    if (opts?.sortBy === "downloads") {
      results.sort((a, b) => (b.downloadCount ?? 0) - (a.downloadCount ?? 0));
    } else if (opts?.sortBy === "rating") {
      results.sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0));
    }

    return results;
  }

  async getDetails(itemId: string): Promise<MarketplaceItemDetail> {
    const localRecipe = await this.loadLocalRecipe(itemId);
    if (localRecipe) {
      return this.recipeToDetail(localRecipe);
    }

    const sample = SAMPLE_RECIPES.find((r) => r.id === itemId);
    if (sample) {
      return {
        ...sample,
        readme: this.generateSampleReadme(sample),
        license: "Proprietary",
      };
    }

    throw new Error(`Recipe ${itemId} not found`);
  }

  async install(itemId: string): Promise<InstalledItem> {
    const sample = SAMPLE_RECIPES.find((r) => r.id === itemId);
    if (!sample) throw new Error(`Recipe ${itemId} not found in catalog`);

    const dir = this.getRecipeDir();
    await nativeFs.mkdir(dir);
    const recipePath = `${dir}/${itemId}.json`;
    const recipe: Recipe = {
      metadata: {
        id: sample.id,
        name: sample.name,
        domain: sample.categories.find((c) => c !== "Recipes") ?? "General",
        description: sample.description,
        author: sample.publisher,
        version: sample.version,
        createdAt: Date.now(),
        updatedAt: Date.now(),
        paperCount: 100,
        tokensBurned: 0,
        tags: sample.tags,
      },
      domainVectors: [],
      patterns: [],
      memories: [],
      paperSources: [],
    };

    await nativeFs.writeFile(recipePath, JSON.stringify(recipe, null, 2));

    return {
      ...sample,
      installDate: Date.now(),
      enabled: true,
      extensionPath: recipePath,
    };
  }

  async uninstall(itemId: string): Promise<void> {
    const recipePath = `${this.getRecipeDir()}/${itemId}.json`;
    try {
      await nativeFs.delete(recipePath);
    } catch {
      /* file may not exist */
    }
  }

  async update(itemId: string): Promise<InstalledItem> {
    return this.install(itemId);
  }

  async listInstalled(): Promise<InstalledItem[]> {
    try {
      const dir = this.getRecipeDir();
      const entries = await nativeFs.readDir(dir);
      if (!entries) return [];

      const recipes: InstalledItem[] = [];
      for (const entry of entries) {
        if (entry.isDirectory || !entry.name.endsWith(".json")) continue;
        try {
          const content = await nativeFs.readFile(`${dir}/${entry.name}`);
          if (!content) continue;
          const recipe: Recipe = JSON.parse(content);
          recipes.push({
            id: recipe.metadata.id,
            name: recipe.metadata.name,
            displayName: recipe.metadata.name,
            description: recipe.metadata.description,
            publisher: recipe.metadata.author,
            version: recipe.metadata.version,
            categories: ["Recipes", recipe.metadata.domain],
            tags: recipe.metadata.tags,
            registry: this.id,
            installDate: recipe.metadata.createdAt,
            enabled: true,
            extensionPath: `${dir}/${entry.name}`,
            downloadCount: recipe.metadata.paperCount,
            rating: recipe.metadata.qualityScore,
          });
        } catch {
          /* skip corrupt files */
        }
      }
      return recipes;
    } catch {
      return [];
    }
  }

  async checkUpdates(): Promise<
    Array<{ id: string; currentVersion: string; latestVersion: string }>
  > {
    return [];
  }

  private getRecipeDir(): string {
    const home =
      typeof process !== "undefined" && process.env?.HOME
        ? process.env.HOME
        : "~";
    return `${home}/.dan/recipes`;
  }

  private async loadLocalRecipe(id: string): Promise<Recipe | null> {
    try {
      const path = `${this.getRecipeDir()}/${id}.json`;
      const content = await nativeFs.readFile(path);
      if (!content) return null;
      return deserializeRecipe(content);
    } catch {
      return null;
    }
  }

  private recipeToDetail(recipe: Recipe): MarketplaceItemDetail {
    const m = recipe.metadata;
    return {
      id: m.id,
      name: m.name,
      displayName: m.name,
      description: m.description,
      publisher: m.author,
      version: m.version,
      categories: ["Recipes", m.domain],
      tags: m.tags,
      registry: this.id,
      rating: m.qualityScore,
      downloadCount: m.paperCount,
      readme: this.generateRecipeReadme(recipe),
      license: "Proprietary",
    };
  }

  private generateRecipeReadme(recipe: Recipe): string {
    const m = recipe.metadata;
    let md = `# ${m.name}\n\n`;
    md += `**Domain:** ${m.domain}${m.subdomain ? ` > ${m.subdomain}` : ""}\n`;
    md += `**Papers:** ${m.paperCount} | **Tokens Burned:** ${m.tokensBurned.toLocaleString()}\n`;
    if (recipe.qualityBenchmark) {
      const b = recipe.qualityBenchmark;
      md += `**Quality:** ${b.beforeScore.toFixed(1)} -> ${b.afterScore.toFixed(1)} (${b.evaluationMethod}, n=${b.sampleSize})\n`;
    }
    md += `\n## Domain Vectors\n`;
    if (recipe.domainVectors.length > 0) {
      for (const v of recipe.domainVectors.slice(0, 5)) {
        md += `- **${v.concept}** -> ${v.associations.slice(0, 5).map((a) => `${a.term} (${a.weight.toFixed(2)})`).join(", ")}\n`;
      }
      if (recipe.domainVectors.length > 5)
        md += `- ...and ${recipe.domainVectors.length - 5} more\n`;
    } else {
      md += `_No vectors extracted yet_\n`;
    }
    md += `\n## Patterns (${recipe.patterns.length})\n`;
    for (const p of recipe.patterns.slice(0, 10)) {
      md += `- **[${p.type}]** ${p.name} -- ${p.description} (freq: ${p.frequency}, conf: ${p.confidence.toFixed(2)})\n`;
    }
    md += `\n## Paper Sources (${recipe.paperSources.length})\n`;
    for (const s of recipe.paperSources.slice(0, 10)) {
      md += `- ${s.authors.slice(0, 3).join(", ")}${s.authors.length > 3 ? " et al." : ""} (${s.year}). ${s.title}. ${s.venue ?? ""}\n`;
    }
    return md;
  }

  private generateSampleReadme(item: MarketplaceItem): string {
    const domain =
      item.categories.find((c) => c !== "Recipes") ?? "the field";
    return `# ${item.displayName}\n\n${item.description}\n\n## About This Recipe\n\nThis recipe was distilled from reading and analyzing 100 papers in ${domain}. It encodes:\n\n- **Domain vectors** -- directional concept associations that capture the field's collective intuition\n- **Methodological patterns** -- common approaches, tools, and frameworks\n- **Terminological conventions** -- field-specific terminology and usage norms\n- **Citation norms** -- how papers reference prior work in this domain\n- **Rhetorical style** -- writing conventions and argument structure\n\n## How to Use\n\nInstall this recipe to import domain knowledge into DAN's memory kernel. The knowledge will:\n\n1. Improve paper writing quality for this domain\n2. Suggest relevant methodologies and frameworks\n3. Correct domain-specific terminology\n4. Follow appropriate citation norms\n5. Match the field's rhetorical expectations\n\n## Tags\n\n${item.tags.map((t) => `\`${t}\``).join(" ")}\n`;
  }
}

import { create } from "zustand";

export interface ResearchPaper {
  id: string;
  title: string;
  authors: string[];
  year: number;
  abstract?: string;
  doi?: string;
  filePath?: string;
  status: "unread" | "reading" | "read";
  relevanceScore?: number;
  tags: string[];
  notes?: string;
  addedAt: number;
}

export interface PipelineStageDetails {
  nodeCount?: number;
  tokenCount?: number;
  outputs?: string[];
}

export interface PipelineStage {
  id: string;
  label: string;
  status: "completed" | "active" | "queued" | "failed" | "paused";
  startedAt?: number;
  completedAt?: number;
  details?: string | PipelineStageDetails;
}

export type PipelinePreset = "research" | "distillation";

export interface ResearchNote {
  id: string;
  content: string;
  tags: string[];
  sourceType?: "pdf" | "chat" | "manual";
  sourceRef?: string;
  createdAt: number;
}

export type CitationStyle = "apa" | "chicago" | "harvard" | "vancouver";
export type ExportFormat = "latex" | "markdown";
export type WritingTone = "formal-academic" | "business" | "policy";

export interface DomainProfileData {
  id: string;
  citationStyle: CitationStyle;
  exportFormat: ExportFormat;
  writingTone: WritingTone;
}

// ---------------------------------------------------------------------------
// PDF Annotations
// ---------------------------------------------------------------------------

export interface PdfAnnotation {
  id: string;
  paperId: string;
  page: number;
  type: "highlight" | "note" | "underline";
  color: string;
  text: string;
  rects: Array<{ x: number; y: number; w: number; h: number }>;
  note?: string;
  createdAt: number;
}

// ---------------------------------------------------------------------------
// Per-page AI Summaries
// ---------------------------------------------------------------------------

export interface PageSummaryData {
  paperId: string;
  page: number;
  claims: string[];
  methods: string[];
  keyTerms: string[];
  figures: string[];
  generatedAt: number;
}

// ---------------------------------------------------------------------------
// Cell Tool Types
// ---------------------------------------------------------------------------

export type CellTool =
  | "python_eval"
  | "web_search"
  | "pdf_read"
  | "shell_exec"
  | "data_analyze";

type PrimaryTab = "editor" | "reader" | "furnace";
type ContextTab = "references" | "reviews" | "outline" | "notes" | "distillation";
type SecondaryTab = "code" | "figures" | "data";

export interface TrainingSession {
  id: string;
  sessionId?: string;
  recipeId?: string;
  parentSessionId?: string;
  familySessionId?: string;
  variantLabel?: string;
  tags?: string[];
  name: string;
  topic: string;
  status: "idle" | "running" | "paused" | "completed" | "failed";
  targetPapers: number;
  processedPapers: number;
  startedAt: number;
  lastActivityAt?: number;
  extractedPatterns?: number;
  extractedTerms?: number;
  currentPhase?: string;
  sourceCount?: number;
  failedSources?: number;
  totalCostUsd?: number;
  statusMessage?: string;
  recentEvents?: string[];
}

interface ResearchState {
  primaryTab: PrimaryTab;
  setPrimaryTab: (tab: PrimaryTab) => void;

  contextTab: ContextTab;
  setContextTab: (tab: ContextTab) => void;

  secondaryTab: SecondaryTab;
  setSecondaryTab: (tab: SecondaryTab) => void;

  papers: ResearchPaper[];
  addPaper: (paper: Omit<ResearchPaper, "id" | "addedAt">) => void;
  removePaper: (id: string) => void;
  updatePaper: (id: string, updates: Partial<ResearchPaper>) => void;

  activePaperId: string | null;
  setActivePaper: (id: string | null) => void;

  pipelinePreset: PipelinePreset;
  setPipelinePreset: (preset: PipelinePreset) => void;

  pipeline: PipelineStage[];
  setPipeline: (stages: PipelineStage[]) => void;
  updateStage: (id: string, updates: Partial<PipelineStage>) => void;
  addStage: (stage: PipelineStage) => void;
  removeStage: (id: string) => void;
  reorderStages: (fromIndex: number, toIndex: number) => void;

  notes: ResearchNote[];
  addNote: (note: Omit<ResearchNote, "id" | "createdAt">) => void;
  removeNote: (id: string) => void;

  documentContent: string;
  setDocumentContent: (content: string) => void;

  showPipeline: boolean;
  togglePipeline: () => void;
  showSecondary: boolean;
  toggleSecondary: () => void;

  showContextPanel: boolean;
  toggleContextPanel: () => void;
  setShowContextPanel: (show: boolean) => void;

  activeRailSection: "library" | "plan" | "training";
  setActiveRailSection: (section: "library" | "plan" | "training") => void;

  trainingSessions: TrainingSession[];
  addTrainingSession: (session: Omit<TrainingSession, "id" | "startedAt">) => void;
  updateTrainingSession: (id: string, updates: Partial<TrainingSession>) => void;
  updateTrainingSessionFromBackend: (sessionId: string, backend: Record<string, unknown>) => void;
  removeTrainingSession: (id: string) => void;

  activeQuickStart: string | null;
  setActiveQuickStart: (id: string | null) => void;

  domainProfile: DomainProfileData;
  setDomainProfile: (profile: DomainProfileData) => void;

  // PDF Annotations
  annotations: PdfAnnotation[];
  addAnnotation: (ann: Omit<PdfAnnotation, "id" | "createdAt">) => void;
  updateAnnotation: (id: string, updates: Partial<PdfAnnotation>) => void;
  removeAnnotation: (id: string) => void;
  getAnnotationsForPaper: (paperId: string) => PdfAnnotation[];

  // Per-page AI Summaries
  pageSummaries: Record<string, PageSummaryData>;
  setPageSummary: (key: string, summary: PageSummaryData) => void;

  // Split reader
  splitReaderActive: boolean;
  setSplitReaderActive: (active: boolean) => void;
  splitPaperId: string | null;
  setSplitPaperId: (id: string | null) => void;
}

function uid() {
  return `r-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

const TRAINING_SESSIONS_STORAGE_KEY = "dan-research-training-sessions";

function loadTrainingSessions(): TrainingSession[] {
  try {
    const raw = localStorage.getItem(TRAINING_SESSIONS_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as TrainingSession[]) : [];
  } catch {
    return [];
  }
}

function persistTrainingSessions(sessions: TrainingSession[]) {
  try {
    localStorage.setItem(TRAINING_SESSIONS_STORAGE_KEY, JSON.stringify(sessions));
  } catch {
    // Ignore quota/unavailable storage.
  }
}

export const useResearchStore = create<ResearchState>((set, get) => ({
  primaryTab: "editor",
  setPrimaryTab: (tab) => set({ primaryTab: tab }),

  contextTab: "references",
  setContextTab: (tab) => set({ contextTab: tab }),

  secondaryTab: "code",
  setSecondaryTab: (tab) => set({ secondaryTab: tab }),

  papers: [],
  addPaper: (paper) =>
    set((s) => ({
      papers: [...s.papers, { ...paper, id: uid(), addedAt: Date.now() }],
    })),
  removePaper: (id) =>
    set((s) => ({
      papers: s.papers.filter((p) => p.id !== id),
      activePaperId: s.activePaperId === id ? null : s.activePaperId,
    })),
  updatePaper: (id, updates) =>
    set((s) => ({
      papers: s.papers.map((p) => (p.id === id ? { ...p, ...updates } : p)),
    })),

  activePaperId: null,
  setActivePaper: (id) => set({ activePaperId: id }),

  pipelinePreset: "research",
  setPipelinePreset: (preset) => set({ pipelinePreset: preset }),

  pipeline: [],
  setPipeline: (stages) => set({ pipeline: stages }),
  updateStage: (id, updates) =>
    set((s) => ({
      pipeline: s.pipeline.map((st) =>
        st.id === id ? { ...st, ...updates } : st,
      ),
    })),
  addStage: (stage) =>
    set((s) => ({ pipeline: [...s.pipeline, stage] })),
  removeStage: (id) =>
    set((s) => ({ pipeline: s.pipeline.filter((st) => st.id !== id) })),
  reorderStages: (fromIndex, toIndex) =>
    set((s) => {
      const next = [...s.pipeline];
      const [moved] = next.splice(fromIndex, 1);
      next.splice(toIndex, 0, moved);
      return { pipeline: next };
    }),

  notes: [],
  addNote: (note) =>
    set((s) => ({
      notes: [...s.notes, { ...note, id: uid(), createdAt: Date.now() }],
    })),
  removeNote: (id) =>
    set((s) => ({ notes: s.notes.filter((n) => n.id !== id) })),

  documentContent: "",
  setDocumentContent: (content) => set({ documentContent: content }),

  showPipeline: false,
  togglePipeline: () => set((s) => ({ showPipeline: !s.showPipeline })),
  showSecondary: false,
  toggleSecondary: () => set((s) => ({ showSecondary: !s.showSecondary })),

  showContextPanel: false,
  toggleContextPanel: () => set((s) => ({ showContextPanel: !s.showContextPanel })),
  setShowContextPanel: (show) => set({ showContextPanel: show }),

  activeRailSection: "library",
  setActiveRailSection: (section) => set({ activeRailSection: section }),

  trainingSessions: loadTrainingSessions(),
  addTrainingSession: (session) =>
    set((s) => {
      const next = [...s.trainingSessions, { ...session, id: uid(), startedAt: Date.now() }];
      persistTrainingSessions(next);
      return { trainingSessions: next };
    }),
  updateTrainingSession: (id, updates) =>
    set((s) => {
      const next = s.trainingSessions.map((t) => (t.id === id ? { ...t, ...updates } : t));
      persistTrainingSessions(next);
      return { trainingSessions: next };
    }),
  updateTrainingSessionFromBackend: (sessionId, backend) =>
    set((s) => {
      const rawStatus = backend.status as string | undefined;
      const status =
        rawStatus === "active"
          ? "running"
          : rawStatus;
      const sess = s.trainingSessions.find(
        (t) => t.sessionId === sessionId || t.id === sessionId,
      );
      if (!sess) return s;
      const next = s.trainingSessions.map((t) =>
          t.sessionId === sessionId || t.id === sessionId
            ? {
                ...t,
                sessionId: (backend.session_id as string) ?? t.sessionId,
                recipeId: (backend.recipe_id as string) ?? t.recipeId,
                parentSessionId: (backend.parent_session_id as string) ?? t.parentSessionId,
                familySessionId: (backend.family_session_id as string) ?? t.familySessionId,
                variantLabel: (backend.variant_label as string) ?? t.variantLabel,
                tags: Array.isArray(backend.tags)
                  ? (backend.tags as string[])
                  : t.tags,
                name: (backend.name as string) ?? t.name,
                topic: (backend.topic as string) ?? t.topic,
                status: (status as TrainingSession["status"]) ?? t.status,
                targetPapers: (backend.target_count as number) ?? t.targetPapers,
                processedPapers: (backend.processed_count as number) ?? t.processedPapers,
                currentPhase: (backend.current_phase as string) ?? t.currentPhase,
                sourceCount: (backend.source_count as number) ?? t.sourceCount,
                failedSources: (backend.failed_sources as number) ?? t.failedSources,
                totalCostUsd: (backend.total_cost_usd as number) ?? t.totalCostUsd,
                statusMessage: (backend.status_message as string) ?? t.statusMessage,
                recentEvents: Array.isArray(backend.recent_events)
                  ? (backend.recent_events as string[])
                  : t.recentEvents,
                lastActivityAt:
                  (backend.last_activity_at as number) ??
                  (backend.updated_at as number) ??
                  Date.now(),
              }
            : t,
        );
      persistTrainingSessions(next);
      return { trainingSessions: next };
    }),
  removeTrainingSession: (id) =>
    set((s) => {
      const next = s.trainingSessions.filter((t) => t.id !== id);
      persistTrainingSessions(next);
      return { trainingSessions: next };
    }),

  activeQuickStart: null,
  setActiveQuickStart: (id) => set({ activeQuickStart: id }),

  domainProfile: (() => {
    try { return JSON.parse(localStorage.getItem("dan-research-profile") ?? "null"); } catch { return null; }
  })() ?? {
    id: "academic",
    citationStyle: "apa" as CitationStyle,
    exportFormat: "latex" as ExportFormat,
    writingTone: "formal-academic" as WritingTone,
  },
  setDomainProfile: (profile) => {
    try { localStorage.setItem("dan-research-profile", JSON.stringify(profile)); } catch { /* quota/unavailable */ }
    set({ domainProfile: profile });
  },

  annotations: (() => {
    try { return JSON.parse(localStorage.getItem("dan-research-annotations") ?? "[]") as PdfAnnotation[]; } catch { return [] as PdfAnnotation[]; }
  })(),
  addAnnotation: (ann) =>
    set((s) => {
      const next = [...s.annotations, { ...ann, id: uid(), createdAt: Date.now() }];
      try { localStorage.setItem("dan-research-annotations", JSON.stringify(next)); } catch { /* quota/unavailable */ }
      return { annotations: next };
    }),
  updateAnnotation: (id, updates) =>
    set((s) => {
      const next = s.annotations.map((a) => (a.id === id ? { ...a, ...updates } : a));
      try { localStorage.setItem("dan-research-annotations", JSON.stringify(next)); } catch { /* quota/unavailable */ }
      return { annotations: next };
    }),
  removeAnnotation: (id) =>
    set((s) => {
      const next = s.annotations.filter((a) => a.id !== id);
      try { localStorage.setItem("dan-research-annotations", JSON.stringify(next)); } catch { /* quota/unavailable */ }
      return { annotations: next };
    }),
  getAnnotationsForPaper: (paperId) => {
    return get().annotations.filter((a) => a.paperId === paperId);
  },

  pageSummaries: (() => {
    try { return JSON.parse(localStorage.getItem("dan-research-summaries") ?? "{}") as Record<string, PageSummaryData>; } catch { return {} as Record<string, PageSummaryData>; }
  })(),
  setPageSummary: (key, summary) =>
    set((s) => {
      const next = { ...s.pageSummaries, [key]: summary };
      try { localStorage.setItem("dan-research-summaries", JSON.stringify(next)); } catch { /* quota/unavailable */ }
      return { pageSummaries: next };
    }),

  // Split reader
  splitReaderActive: false,
  setSplitReaderActive: (active) => set({ splitReaderActive: active }),
  splitPaperId: null,
  setSplitPaperId: (id) => set({ splitPaperId: id }),
}));

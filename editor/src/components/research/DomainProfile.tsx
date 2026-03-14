import { type ReactNode } from "react";
import { GraduationCap, BarChart3, Scale } from "lucide-react";
import {
  useResearchStore,
  type CitationStyle,
  type ExportFormat,
  type WritingTone,
} from "../../store/useResearchStore";

/* ------------------------------------------------------------------ */
/*  Domain profile definitions                                         */
/* ------------------------------------------------------------------ */

export interface DomainProfile {
  id: string;
  name: string;
  description: string;
  icon: ReactNode;
  citationStyle: CitationStyle;
  exportFormat: ExportFormat;
  writingTone: WritingTone;
  defaultTemplate: string;
  reviewCriteria: string[];
  quickStarts: string[];
}

const ACADEMIC_TEMPLATE = `# Paper Title

## Abstract

## 1. Introduction

## 2. Literature Review

## 3. Methodology

## 4. Results

## 5. Discussion

## 6. Conclusion

## References
`;

const MARKET_RESEARCH_TEMPLATE = `# Market Research Report

## Executive Summary

## Market Overview

## Methodology

## Key Findings

## Competitive Analysis

## Recommendations

## Appendix
`;

const POLICY_TEMPLATE = `# Policy Analysis

## Executive Summary

## Background

## Current Policy Landscape

## Impact Assessment

## Stakeholder Analysis

## Recommendations

## Implementation Plan

## References
`;

export const DOMAIN_PROFILES: DomainProfile[] = [
  {
    id: "academic",
    name: "Academic Research",
    description:
      "LaTeX export, BibTeX citations, INFORMS/AER style, formal academic tone",
    icon: <GraduationCap size={16} className="text-purple-400" />,
    citationStyle: "apa",
    exportFormat: "latex",
    writingTone: "formal-academic",
    defaultTemplate: ACADEMIC_TEMPLATE,
    reviewCriteria: [
      "methodology-rigor",
      "writing-clarity",
      "novelty",
      "completeness",
      "theoretical-contribution",
    ],
    quickStarts: [
      "literature-review",
      "new-paper",
      "review-paper",
      "compare-papers",
      "summarize-paper",
      "learn-100",
    ],
  },
  {
    id: "market-research",
    name: "Market Research",
    description:
      "Survey templates, interview protocols, business tone, executive summary focus",
    icon: <BarChart3 size={16} className="text-blue-400" />,
    citationStyle: "harvard",
    exportFormat: "markdown",
    writingTone: "business",
    defaultTemplate: MARKET_RESEARCH_TEMPLATE,
    reviewCriteria: [
      "methodology",
      "data-quality",
      "actionability",
      "market-relevance",
    ],
    quickStarts: ["new-paper", "summarize-paper", "compare-papers"],
  },
  {
    id: "policy",
    name: "Policy Analysis",
    description:
      "Regulatory sources, impact assessment framework, government style",
    icon: <Scale size={16} className="text-yellow-400" />,
    citationStyle: "chicago",
    exportFormat: "markdown",
    writingTone: "policy",
    defaultTemplate: POLICY_TEMPLATE,
    reviewCriteria: [
      "evidence-quality",
      "policy-relevance",
      "stakeholder-impact",
      "feasibility",
    ],
    quickStarts: ["literature-review", "compare-papers", "summarize-paper"],
  },
];

/* ------------------------------------------------------------------ */
/*  Helper — get active profile object from store id                   */
/* ------------------------------------------------------------------ */

export function getActiveProfile(): DomainProfile {
  const id = useResearchStore.getState().domainProfile.id;
  return DOMAIN_PROFILES.find((p) => p.id === id) ?? DOMAIN_PROFILES[0];
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function DomainProfileSelector({
  onSelect,
}: {
  onSelect?: (profile: DomainProfile) => void;
}) {
  const activeId = useResearchStore((s) => s.domainProfile.id);
  const setDomainProfile = useResearchStore((s) => s.setDomainProfile);

  const handleSelect = (profile: DomainProfile) => {
    setDomainProfile({
      id: profile.id,
      citationStyle: profile.citationStyle,
      exportFormat: profile.exportFormat,
      writingTone: profile.writingTone,
    });
    onSelect?.(profile);
  };

  return (
    <div className="p-4">
      <h3 className="text-xs font-semibold text-gray-300 mb-3">
        Research Profile
      </h3>
      <div className="space-y-2">
        {DOMAIN_PROFILES.map((profile) => (
          <button
            key={profile.id}
            onClick={() => handleSelect(profile)}
            className={`w-full flex items-start gap-3 p-3 rounded-lg border transition-colors text-left ${
              activeId === profile.id
                ? "border-purple-500/50 bg-purple-900/10"
                : "border-gray-700/50 bg-gray-800/30 hover:border-gray-600"
            }`}
          >
            {profile.icon}
            <div className="min-w-0">
              <p className="text-xs text-gray-200 font-medium">
                {profile.name}
              </p>
              <p className="text-[10px] text-gray-500 mt-0.5">
                {profile.description}
              </p>
              <div className="flex flex-wrap gap-1 mt-1.5">
                <span className="text-[9px] bg-gray-800 text-gray-400 px-1.5 py-0.5 rounded">
                  {profile.citationStyle.toUpperCase()}
                </span>
                <span className="text-[9px] bg-gray-800 text-gray-400 px-1.5 py-0.5 rounded">
                  {profile.exportFormat}
                </span>
                <span className="text-[9px] bg-gray-800 text-gray-400 px-1.5 py-0.5 rounded">
                  {profile.writingTone}
                </span>
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

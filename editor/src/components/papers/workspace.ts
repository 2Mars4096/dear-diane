import type { Dispatch, SetStateAction } from 'react';
import type { MainTab } from '../workbench/MainTabs';
import type { DocumentFile } from '../documents/documents';
import { closeLibraryPaper, openLibraryPaper } from './reading';
import type { Paper, ReadingSession } from './library';
import { paperReference } from './library';
export async function openPaper(paper: Paper, openDocument: (file: DocumentFile) => void, setReadings: Dispatch<SetStateAction<Record<string, { paper: Paper; session: ReadingSession }>>>, setTabs: Dispatch<SetStateAction<MainTab[]>>) {
  const opened = await openLibraryPaper(paper.id);
  const id = `pdf:${opened.paper.path}`;
  setReadings(current => ({ ...current, [id]: opened }));
  openDocument({ name: opened.paper.path.split(/[\\/]/).pop() || `${paper.key}.pdf`, path: opened.paper.path, url: `/api/papers/${paper.id}/pdf`, root: opened.paper.source_root, onClose: () => { void closeLibraryPaper(paper.id); } });
  setTabs(tabs => tabs.map(tab => tab.id === id ? { ...tab, label: paper.title } : tab));
}
export function makePaperReference(papers: Paper[], workflowId = '', threadId = '') {
  return { text: paperReference(papers), title: papers.length === 1 ? papers[0].title : `${papers.length} papers`, workflowId, threadId, messageIds: [] as string[] };
}

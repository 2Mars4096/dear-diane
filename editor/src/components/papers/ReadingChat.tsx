import { SidecarChat } from '../workbench/SidecarChat';
import type { ComponentProps } from 'react';
import type { ReaderFile } from '../reader/ReaderView';
import type { Paper, ReadingSession, ReadingLink } from './library';
import { saveReadingLink } from './reading';

type Base = ComponentProps<typeof SidecarChat>;
export function ReadingChat({ file, reading, onLink, ...props }: Omit<Base, 'purpose' | 'initialLink' | 'onLinkChange' | 'parentId' | 'context'> & {
  file: ReaderFile; reading?: { paper: Paper; session: ReadingSession }; onLink: (link: ReadingLink) => void;
}) {
  return <SidecarChat {...props} parentId={`reader:${file.path}`} context={[]}
    workflowId={reading?.session.workflow_id || props.workflowId}
    workspaceId={reading ? '_dan_reading' : props.workspaceId}
    workspaceRoot={reading?.paper.source_root || props.workspaceRoot}
    initialLink={reading?.session.link}
    onLinkChange={reading ? async link => { await saveReadingLink(reading.session.id, link); onLink(link); } : undefined}
    purpose={{ title: `Reading ${file.name}`,
      framing: `You are a reading companion for the PDF "${file.name}" (${file.url.startsWith('blob:') ? 'browser-local copy; only supplied excerpts are available to you' : file.path}). This is the currently open document: resolve requests such as "summarize" or "explain this file" to it without asking for its name again. Read the file when a whole-document answer is needed and the path is accessible; supplied selections and page text are additional context. Never imply that you read the full document from an excerpt. Carry out the user request within the selected execution permissions.`,
      placeholder: 'Ask about this file or give Diane a task…', empty: `Reading ${file.name}. Ask about the whole file, or select a passage and choose Ask. Answers stay here.` }} />;
}

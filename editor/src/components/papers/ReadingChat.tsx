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
      framing: `You are a reading companion for the PDF "${file.name}" (${file.url.startsWith('blob:') ? 'browser-local copy; only supplied excerpts are available to you' : file.path}). The user is reading it and asks about a passage they selected. Answer directly and concisely, grounded in the passage and its page text; say plainly when that text is not enough. Do not modify files.`,
      placeholder: 'Ask about this passage…', empty: 'Select text in the PDF and choose Ask. Answers stay here while you keep reading.' }} />;
}

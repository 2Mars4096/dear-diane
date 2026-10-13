import type { ReactNode } from 'react';
import './reader-toolbar.css';

/** One reading control layout; each engine supplies its native navigation and sizing. */
export function ReaderToolbar({ navigation, chapters, sizing, layout, references, actions, status }: {
  navigation: ReactNode; chapters: ReactNode; sizing: ReactNode; layout: ReactNode;
  references: ReactNode; actions: ReactNode; status?: ReactNode;
}) {
  return <div className="wb-reader-toolbar" role="group" aria-label="Reading controls">
    <div className="wb-reader-toolbar-group wb-reader-toolbar-navigation">{navigation}</div>
    <div className="wb-reader-toolbar-chapters">{chapters}</div>
    <div className="wb-reader-toolbar-group">{sizing}</div>
    <div className="wb-reader-toolbar-group">{layout}</div>
    <div className="wb-reader-toolbar-actions">{references}{actions}</div>
    {status && <div className="wb-reader-toolbar-status" role="status">{status}</div>}
  </div>;
}

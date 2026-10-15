import type { ReactNode } from 'react';
import './reader-toolbar.css';
import { ReaderMenu } from './ReaderMenus';

/** One reading control layout; each engine supplies its native navigation and sizing. */
export function ReaderToolbar({ navigation, chapters, sizing, layout, references, actions, status, more }: {
  navigation: ReactNode; chapters: ReactNode; sizing: ReactNode; layout: ReactNode;
  references: ReactNode; actions: ReactNode; status?: ReactNode; more?: ReactNode;
}) {
  return <div className="wb-reader-toolbar" role="group" aria-label="Reading controls">
    <div className="wb-reader-toolbar-group wb-reader-toolbar-navigation">{navigation}</div>
    <div className="wb-reader-toolbar-chapters">{chapters}</div>
    <div className="wb-reader-toolbar-group wb-reader-sizing" role="group" aria-label="Text and zoom">{sizing}</div>
    <ReaderMenu label="Reading settings">{layout}</ReaderMenu>
    <div className="wb-reader-toolbar-actions">{references}{actions}{more && <ReaderMenu label="More document actions" more>{more}</ReaderMenu>}</div>
    {status && <div className="wb-reader-toolbar-status" role="status">{status}</div>}
  </div>;
}

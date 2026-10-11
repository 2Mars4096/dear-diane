import { MoreHorizontal } from 'lucide-react';
import { updateNeedsAttention, useDesktopUpdateStatus } from './desktopUpdateStatus';
export function SettingsUpdateButton({ onClick }: { onClick: () => void }) {
  const available = updateNeedsAttention(useDesktopUpdateStatus());
  return <button onClick={onClick} aria-label={available ? 'Settings, update available' : 'Settings'}>
    <MoreHorizontal size={15} />Settings{available && <span className="wb-update-dot" aria-hidden="true" />}
  </button>;
}

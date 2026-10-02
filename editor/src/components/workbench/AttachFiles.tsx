import { useRef } from 'react';
import { Paperclip } from 'lucide-react';
export default function AttachFiles({ onFiles, disabled }: { onFiles:(files:File[])=>void; disabled:boolean }) {
  const input=useRef<HTMLInputElement>(null);
  return <><button type="button" title="Attach files (up to 4, 20 MB each)" aria-label="Attach files" disabled={disabled} onClick={()=>input.current?.click()}><Paperclip size={16}/></button><input ref={input} type="file" multiple hidden aria-label="Choose attachments" onChange={event=>{ onFiles(Array.from(event.target.files || [])); event.target.value=''; }}/></>;
}

/** Native drag events inside an EPUB iframe do not bubble into the app window. */
export function bridgeReaderFileDrops(document: Document, target: HTMLElement) {
  const forward = (event: DragEvent) => {
    if (!Array.from(event.dataTransfer?.types || []).includes('Files')) return;
    event.preventDefault();
    if (event.dataTransfer && event.type === 'dragover') event.dataTransfer.dropEffect = 'copy';
    // Keep the original DataTransfer/File objects: Electron needs them for native paths.
    target.dispatchEvent(new DragEvent(event.type, { bubbles: true, cancelable: true, dataTransfer: event.dataTransfer }));
  };
  const events = ['dragenter', 'dragover', 'dragleave', 'drop'] as const;
  for (const type of events) document.addEventListener(type, forward);
  return () => { for (const type of events) document.removeEventListener(type, forward); };
}

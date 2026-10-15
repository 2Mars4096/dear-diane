/** Size the native editor independently of expensive workspace renders. */
export function resizeComposer(node: HTMLTextAreaElement) {
  const style = getComputedStyle(node);
  const line = parseFloat(style.lineHeight) || 24;
  const padding = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
  const border = (parseFloat(style.borderTopWidth) || 0) + (parseFloat(style.borderBottomWidth) || 0);
  const cssMaximum = parseFloat(style.maxHeight);
  const maximum = Math.min(line * 6 + padding + border, Number.isFinite(cssMaximum) ? cssMaximum : Infinity);
  node.style.height = 'auto';
  const needed = node.scrollHeight + border;
  node.style.height = `${Math.min(needed, maximum)}px`;
  node.style.overflowY = needed > maximum ? 'auto' : 'hidden';
  if (needed <= maximum) node.scrollTop = 0;
}

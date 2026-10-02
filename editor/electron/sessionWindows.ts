/** Only internal session destinations can create another privileged app window. */
export function sessionWindowDestination(value: string, base: string): string | null {
  try {
    const target = new URL(value);
    if (target.origin !== new URL(base).origin || target.pathname !== '/' || target.username || target.password) return null;
    const session = target.searchParams.get('session'), workflow = target.searchParams.get('workflow');
    if (!session || !workflow || session.length > 200 || workflow.length > 200) return null;
    target.search = ''; target.hash = '';
    target.searchParams.set('session', session); target.searchParams.set('workflow', workflow);
    return target.href;
  } catch { return null; }
}

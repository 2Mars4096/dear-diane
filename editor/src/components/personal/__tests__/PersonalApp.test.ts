// @vitest-environment happy-dom
import { act, createElement as h } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PersonalApp from '../PersonalApp';
import { personalApi } from '../../../lib/personalApi';
vi.mock('../../../lib/personalApi', () => ({ personalApi: { budget: vi.fn(), updateBudget: vi.fn(), capabilities: vi.fn(), list: vi.fn(), capture: vi.fn(), detail: vi.fn(), review: vi.fn(), upload: vi.fn(), sources: vi.fn(), deleteSource: vi.fn(), transcribe: vi.fn(), events: vi.fn(), decision: vi.fn(), source: (record: string, source: string) => `/sources/${record}/${source}`, extract: vi.fn(), inbox: vi.fn(), reminder: vi.fn(), read: vi.fn(), calendar: (id: string) => `/calendar/${id}` } }));
let host: HTMLDivElement, root: ReturnType<typeof createRoot>;
const record = { id: 'c1', revision: 1, title: 'Lunch', capture_id: 's1', date: null, time: null, timezone: null, all_day: false, location: '', lifecycle: 'draft' as const, attention: 'needs-input', created_at: '', updated_at: '' };
function button(label: string) { return [...host.querySelectorAll('button')].find(item => item.textContent === label)!; }
async function mount() { await act(async () => { root.render(h(PersonalApp)); }); }
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  localStorage.clear(); vi.clearAllMocks();
  vi.mocked(personalApi.capabilities).mockResolvedValue({ enabled: true });
  vi.mocked(personalApi.budget).mockResolvedValue({currency: 'USD', host_task_limit: '0.50', host_daily_limit: '2.00', paused: false, settings: {revision: 0, task_limit: '0.50', daily_limit: '2.00', paused: false}, task_limit: '0.50', daily_limit: '2.00', reserved_usd: '0.305152', used_reservations_usd: '0', remaining_usd: '2', available: true, day: '2026-09-29'});
  vi.mocked(personalApi.events).mockResolvedValue({events: [], cursor: 0, reset: false});
  vi.mocked(personalApi.sources).mockResolvedValue({sources: []});
  vi.mocked(personalApi.list).mockResolvedValue({ commitments: [], cursor: 0, has_more: false });
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('retains the source and operation ID after a lost response, then clears the draft only on success', async () => {
  localStorage.setItem('dan.personal.unsent.v1', JSON.stringify({ text: 'Lunch with Ada', operation: 'stable-operation-1' }));
  vi.mocked(personalApi.capture).mockRejectedValueOnce(new Error('Connection lost')).mockResolvedValueOnce({ commitment: record, capture: { id: 's1', text: 'Lunch with Ada', duplicate_candidates: [] } });
  await mount();
  await act(async () => button('Review details →').click());
  expect(host.querySelector('textarea')?.value).toBe('Lunch with Ada');
  expect(host.querySelector('[role=alert]')?.textContent).toContain('Connection lost');
  await act(async () => button('Review details →').click());
  expect(personalApi.capture).toHaveBeenNthCalledWith(1, 'stable-operation-1', 'Lunch with Ada');
  expect(personalApi.capture).toHaveBeenNthCalledWith(2, 'stable-operation-1', 'Lunch with Ada');
  expect(host.querySelector('textarea')?.value).toBe('');
  expect(button('Confirm details')).toBeTruthy();
  expect(JSON.parse(localStorage.getItem('dan.personal.unsent.v1')!).text).toBe('');
});

it('keeps capture usable and reports when local draft persistence is unavailable', async () => {
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => { throw new Error('full'); } });
  await mount();
  expect(host.textContent).toContain('cannot keep your unsent draft');
  expect(host.querySelector('textarea')).not.toBeNull();
});

it('does not fetch records or offer mutations when the host disables the pilot', async () => {
  vi.mocked(personalApi.capabilities).mockResolvedValue({ enabled: false });
  await mount();
  expect(personalApi.list).not.toHaveBeenCalled();
  expect(host.querySelector('textarea')).toBeNull();
  expect(host.textContent).toContain('not enabled');
});


it('starts extraction and offers Stop while the saved job is pending', async () => {
  vi.mocked(personalApi.capabilities).mockResolvedValue({ enabled: true, model: 'fixture-model' });
  vi.mocked(personalApi.list).mockResolvedValue({ commitments: [record], cursor: 1, has_more: false });
  vi.mocked(personalApi.detail).mockResolvedValue({ commitment: record, capture: { id: 's1', text: 'Lunch', duplicate_candidates: [] } });
  vi.mocked(personalApi.extract).mockResolvedValueOnce({ ...record, revision: 2, extraction: { status: 'queued', job_id: 'j1' } }).mockResolvedValueOnce({ ...record, revision: 3, extraction: { status: 'stopped', job_id: 'j1' } });
  await mount();
  await act(async () => host.querySelector<HTMLButtonElement>('.personal-record')!.click());
  vi.mocked(personalApi.detail).mockResolvedValueOnce({commitment: {...record, revision: 2, extraction: {status: 'queued', job_id: 'j1'}}, capture: {id: 's1', text: 'Lunch', duplicate_candidates: []}});
  await act(async () => button('Extract details').click());
  expect(button('Stop extraction')).toBeTruthy();
  expect(host.querySelector<HTMLFieldSetElement>('.personal-review fieldset')?.disabled).toBe(true);
  vi.mocked(personalApi.detail).mockResolvedValueOnce({commitment: {...record, revision: 3, extraction: {status: 'stopped', job_id: 'j1'}}, capture: {id: 's1', text: 'Lunch', duplicate_candidates: []}});
  await act(async () => button('Stop extraction').click());
  expect(personalApi.extract).toHaveBeenLastCalledWith('c1', expect.any(String), 2, true);
  expect(button('Extract details')).toBeTruthy();
});

it('refreshes the selected commitment when its reminder is delivered', async () => {
  vi.useFakeTimers();
  try {
    const scheduled = { ...record, lifecycle: 'active' as const, date: '2099-10-02', time: '14:00', timezone: 'UTC', reminder: { id: 'r1', state: 'scheduled' as const, due_at: '2099-10-02T14:00:00Z', timezone: 'UTC' } };
    const delivered = { ...scheduled, revision: 2, reminder: { ...scheduled.reminder, state: 'delivered' as const } };
    const capture = { id: 's1', text: 'Lunch', duplicate_candidates: [] };
    vi.mocked(personalApi.capabilities).mockResolvedValue({ enabled: true, reminders: 'inbox' });
    vi.mocked(personalApi.inbox).mockResolvedValue({ notifications: [], has_more: false });
    vi.mocked(personalApi.list).mockResolvedValue({ commitments: [scheduled], cursor: 1, has_more: false });
    vi.mocked(personalApi.detail).mockResolvedValueOnce({ commitment: scheduled, capture }).mockResolvedValue({ commitment: delivered, capture });
    await mount();
    await act(async () => host.querySelector<HTMLButtonElement>('.personal-record')!.click());
    expect(button('Pause reminder')).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
    expect(host.textContent).toContain('delivered');
    expect(button('Schedule reminder')).toBeTruthy();
  } finally { vi.useRealTimers(); }
});


it('keeps uploaded source references across reload and saves a file-only capture', async () => {
  const source = { id: 'file-001', name: 'invitation.eml', size: 100, media_type: 'message/rfc822' };
  localStorage.setItem('dan.personal.unsent.v1', JSON.stringify({ text: '', operation: 'file-capture-001', sources: [source] }));
  vi.mocked(personalApi.capture).mockResolvedValue({ commitment: record, capture: { id: 's1', text: 'Email source', duplicate_candidates: [], attachments: [source] } });
  await mount();
  expect(button('Review details →').disabled).toBe(false);
  await act(async () => button('Review details →').click());
  expect(personalApi.capture).toHaveBeenCalledWith('file-capture-001', '', ['file-001']);
  expect(host.querySelector('a[href="/sources/c1/file-001"]')?.textContent).toContain('invitation.eml');
  expect(JSON.parse(localStorage.getItem('dan.personal.unsent.v1')!).sources).toEqual([]);
});

it('requires an explicit new-commitment choice for a cancellation proposal', async () => {
  const cancellation = { ...record, date: '2026-10-02', time: '14:00', timezone: 'UTC', extraction: { status: 'completed', job_id: 'j1', draft: { title: 'Lunch', date: '2026-10-02', time: '14:00', timezone: 'UTC', intent: 'cancellation', questions: [], unresolved_fields: ['target_commitment'], anchors: [] } } };
  vi.mocked(personalApi.list).mockResolvedValue({ commitments: [cancellation], cursor: 1, has_more: false });
  vi.mocked(personalApi.detail).mockResolvedValue({ commitment: cancellation, capture: { id: 's1', text: 'Lunch cancelled', duplicate_candidates: [] } });
  vi.mocked(personalApi.review).mockResolvedValue({ ...cancellation, revision: 2, lifecycle: 'active' });
  await mount();
  await act(async () => host.querySelector<HTMLButtonElement>('.personal-record')!.click());
  expect(button('Confirm details').disabled).toBe(true);
  const choice = [...host.querySelectorAll('label')].find(label => label.textContent?.includes('new, separate commitment'))!.querySelector('input')!;
  await act(async () => choice.click());
  expect(button('Confirm details').disabled).toBe(false);
  await act(async () => button('Confirm details').click());
  expect(personalApi.review).toHaveBeenCalledWith('c1', expect.objectContaining({ confirm_as_new: true }));
});

it('pauses and resumes with revision checks and refreshed history', async () => {
  const active = {...record, lifecycle: 'active' as const, attention: null};
  const paused = {...active, revision: 2, attention: 'paused'};
  const capture = {id: 's1', text: 'Lunch', duplicate_candidates: []};
  vi.mocked(personalApi.list).mockResolvedValue({commitments: [active], cursor: 1, has_more: false});
  vi.mocked(personalApi.detail).mockResolvedValueOnce({commitment: active, capture});
  await mount();
  await act(async () => host.querySelector<HTMLButtonElement>('.personal-record')!.click());
  vi.mocked(personalApi.decision).mockResolvedValueOnce(paused);
  vi.mocked(personalApi.detail).mockResolvedValueOnce({commitment: paused, capture, history: [{cursor: 2, kind: 'task_pause', at: '2026-09-29T12:00:00Z'}]});
  await act(async () => button('Pause commitment').click());
  expect(personalApi.decision).toHaveBeenLastCalledWith('c1', expect.objectContaining({expected_revision: 1, action: 'pause'}));
  expect(button('Edit details').disabled).toBe(true);
  expect(host.textContent).toContain('task pause');
  const resumed = {...active, revision: 3};
  vi.mocked(personalApi.decision).mockResolvedValueOnce(resumed);
  vi.mocked(personalApi.detail).mockResolvedValueOnce({commitment: resumed, capture});
  await act(async () => button('Resume commitment').click());
  expect(personalApi.decision).toHaveBeenLastCalledWith('c1', expect.objectContaining({expected_revision: 2, action: 'resume'}));
  expect(button('Edit details').disabled).toBe(false);
});

it('retries reconnect when the snapshot succeeds but selected detail fails', async () => {
  vi.useFakeTimers();
  try {
    const active = {...record, lifecycle: 'active' as const};
    const paused = {...active, revision: 2, attention: 'paused'};
    const capture = {id: 's1', text: 'Lunch', duplicate_candidates: []};
    vi.mocked(personalApi.list).mockResolvedValueOnce({commitments: [active], cursor: 1, has_more: false}).mockResolvedValue({commitments: [paused], cursor: 2, has_more: false});
    vi.mocked(personalApi.events).mockResolvedValue({events: [{cursor: 2, entity_id: 'c1', kind: 'task_pause'}], cursor: 2, reset: false});
    vi.mocked(personalApi.detail).mockResolvedValueOnce({commitment: active, capture}).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({commitment: paused, capture});
    await mount();
    await act(async () => host.querySelector<HTMLButtonElement>('.personal-record')!.click());
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(button('Pause commitment')).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(personalApi.events).toHaveBeenNthCalledWith(2, 1);
    expect(button('Resume commitment')).toBeTruthy();
  } finally { vi.useRealTimers(); }
});

it('confirms original deletion and removes its unsent draft reference', async () => {
  const source = {id: 'source-001', revision: 1, name: 'letter.txt', size: 5, media_type: 'text/plain'};
  localStorage.setItem('dan.personal.unsent.v1', JSON.stringify({text: '', operation: 'capture-001', sources: [source]}));
  vi.mocked(personalApi.sources).mockResolvedValue({sources: [source]});
  vi.mocked(personalApi.deleteSource).mockResolvedValue({...source, revision: 2, original_deleted: true});
  await mount();
  await act(async () => {
    const library = host.querySelector<HTMLDetailsElement>('.personal-source-library')!;
    library.open = true;
    library.dispatchEvent(new Event('toggle'));
  });
  await act(async () => button('Delete original').click());
  expect(personalApi.deleteSource).not.toHaveBeenCalled();
  expect(host.textContent).toContain('Captured text and older database backups are retained');
  await act(async () => button('Keep original').click());
  expect(button('Confirm deletion')).toBeUndefined();
  await act(async () => button('Delete original').click());
  vi.mocked(personalApi.sources).mockResolvedValue({sources: []});
  await act(async () => button('Confirm deletion').click());
  expect(personalApi.deleteSource).toHaveBeenCalledWith(source, 'delete-source-001-1');
  expect(JSON.parse(localStorage.getItem('dan.personal.unsent.v1')!).sources).toEqual([]);
});

it('shows budget exhaustion while keeping manual capture available', async () => {
  vi.mocked(personalApi.capabilities).mockResolvedValue({enabled: true, model: 'fixture/model'});
  vi.mocked(personalApi.budget).mockResolvedValue({currency: 'USD', host_task_limit: '0.50', host_daily_limit: '2.00', paused: false, settings: {revision: 0, task_limit: '0.50', daily_limit: '2.00', paused: false}, task_limit: '0.50', daily_limit: '2.00', reserved_usd: '0.305152', used_reservations_usd: '1.830912', remaining_usd: '0.169088', available: false, day: '2026-09-29'});
  localStorage.setItem('dan.personal.unsent.v1', JSON.stringify({text: 'Still capture manually', operation: 'manual-budget-001', sources: []}));
  await mount();
  expect(host.textContent).toContain('AI limit reached');
  expect(host.textContent).toContain('1.830912 reserved');
  expect(button('Review details →').disabled).toBe(false);
});

it('saves a revision-bound extraction pause and reloads its durable status', async () => {
  vi.mocked(personalApi.capabilities).mockResolvedValue({enabled: true, model: 'fixture/model'});
  await mount();
  const checkbox = [...host.querySelectorAll('label')].find(label => label.textContent?.includes('Pause AI replies'))!.querySelector('input')!;
  await act(async () => checkbox.click());
  const saved = {revision: 1, task_limit: '0.50', daily_limit: '2.00', paused: true};
  vi.mocked(personalApi.updateBudget).mockResolvedValue(saved);
  vi.mocked(personalApi.budget).mockResolvedValue({currency: 'USD', host_task_limit: '0.50', host_daily_limit: '2.00', settings: saved, paused: true, task_limit: '0.50', daily_limit: '2.00', reserved_usd: '0.305152', used_reservations_usd: '0', remaining_usd: '2.00', available: false, day: '2026-09-29'});
  expect(button('Save limits').disabled).toBe(false);
  await act(async () => button('Save limits').click());
  expect(personalApi.updateBudget).toHaveBeenCalledWith(expect.objectContaining({expected_revision: 0, paused: true, task_limit: '0.50', daily_limit: '2.00'}));
  expect(host.textContent).toContain('AI paused');
  expect(host.textContent).toContain('Spending limits saved');
});


it('changes a scheduled reminder directly using its own date and timezone', async () => {
  const active = {...record, lifecycle: 'active' as const, date: '2099-10-03', time: '14:00', timezone: 'UTC', reminder: {id: 'r1', state: 'scheduled' as const, due_at: '2099-10-02T06:30:00Z', timezone: 'Asia/Hong_Kong'}};
  const capture = {id: 's1', text: 'Lunch', duplicate_candidates: []};
  vi.mocked(personalApi.capabilities).mockResolvedValue({enabled: true, reminders: 'inbox'});
  vi.mocked(personalApi.inbox).mockResolvedValue({notifications: [], has_more: false});
  vi.mocked(personalApi.list).mockResolvedValue({commitments: [active], cursor: 1, has_more: false});
  vi.mocked(personalApi.detail).mockResolvedValue({commitment: active, capture});
  vi.mocked(personalApi.reminder).mockResolvedValue({...active, revision: 2});
  await mount();
  await act(async () => host.querySelector<HTMLButtonElement>('.personal-record')!.click());
  expect(button('Save draft')).toBeUndefined();
  expect(button('Save changes')).toBeUndefined();
  await act(async () => button('Edit details').click());
  expect(button('Save changes').disabled).toBe(true);
  await act(async () => button('Cancel edits').click());
  await act(async () => button('Change reminder').click());
  expect(host.querySelector<HTMLInputElement>('.personal-reminder-controls input[type=date]')!.value).toBe('2099-10-02');
  expect(host.querySelector<HTMLInputElement>('.personal-reminder-controls input[type=time]')!.value).toBe('14:30');
  await act(async () => button('Save reminder').click());
  expect(personalApi.reminder).toHaveBeenCalledTimes(1);
  expect(personalApi.reminder).toHaveBeenCalledWith('c1', expect.objectContaining({action: 'schedule', expected_revision: 1, date: '2099-10-02', time: '14:30', timezone: 'Asia/Hong_Kong'}));
});

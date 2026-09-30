import { requestJson } from './http';

export interface Commitment {
  id: string; revision: number; title: string; capture_id: string;
  date: string | null; time: string | null; timezone: string | null; all_day: boolean; location: string; offset?: string | null;
  lifecycle: 'draft' | 'active' | 'cancelled' | 'fulfilled'; attention: string | null;
  wait_reason?: string; outcome?: { kind: string; note: string; at: string };
  created_at: string; updated_at: string;
  reminder?: { id: string; state: 'scheduled' | 'paused' | 'stopped' | 'delivered'; due_at: string; timezone: string };
  extraction?: { status: string; job_id: string; model?: string; run_id?: string; error?: string; draft?: {
    title: string; date: string | null; time: string | null; timezone: string | null;
    intent: string; questions: string[]; unresolved_fields: string[]; anchors: { field: string; quote: string }[];
  } };
}
export interface SourceAttachment { id: string; name: string; size: number; media_type: string; pages?: number; revision?: number; needs_transcription?: boolean; original_deleted?: boolean }
export interface Capture { id: string; text: string; duplicate_candidates: string[]; attachments?: SourceAttachment[] }
export interface Detail {
  commitment: Commitment; capture: Capture;
  history?: { cursor: number; kind: string; at: string }[];
}
export interface Review {
  operation_id: string; expected_revision: number; title: string; date: string | null;
  time: string | null; timezone: string | null; all_day: boolean; location: string; offset?: string | null;
  decision: 'save' | 'confirm' | 'dismiss'; confirm_as_new?: boolean;
}
export interface ReminderNotice { id: string; revision: number; commitment_id: string; title: string; due_at: string; created_at: string; read: boolean; late: boolean }
export interface ReminderChange { operation_id: string; expected_revision: number; action: 'schedule' | 'pause' | 'resume' | 'stop'; date?: string; time?: string; timezone?: string; offset?: string | null }
export interface SpendingSettings { revision: number; task_limit: string; daily_limit: string; paused: boolean }
export interface SpendingStatus { actual_usd?: string; held_usd?: string; unverified_usd?: string; currency: string; task_limit: string; daily_limit: string; host_task_limit: string; host_daily_limit: string; paused: boolean; settings: SpendingSettings; reserved_usd: string; used_reservations_usd: string | null; remaining_usd: string | null; available: boolean; day: string }
const root = '/api/personal';
export const personalApi = {
  budget: () => requestJson<SpendingStatus>(`${root}/budget`),
  updateBudget: (body: {operation_id: string; expected_revision: number; task_limit: string; daily_limit: string; paused: boolean}) => requestJson<SpendingSettings>(`${root}/budget`, {method: 'PATCH', body: JSON.stringify(body)}),
  capabilities: () => requestJson<{ enabled: boolean; extraction?: string; model?: string; reminders?: string | boolean }>(`${root}/capabilities`),
  list: () => requestJson<{ commitments: Commitment[]; cursor: number; has_more: boolean }>(`${root}/commitments`),
  events: (after: number) => requestJson<{ events: {cursor: number; entity_id: string; kind: string}[]; cursor: number; reset: boolean }>(`${root}/events?after=${after}`),
  decision: (id: string, body: {operation_id: string; expected_revision: number; action: 'complete' | 'wait' | 'pause' | 'resume' | 'cancel'; note: string}) => requestJson<Commitment>(`${root}/commitments/${encodeURIComponent(id)}/decision`, {method: 'POST', body: JSON.stringify(body)}),
  capture: (operation_id: string, text: string, source_ids?: string[]) => requestJson<Detail>(`${root}/captures`, { method: 'POST', body: JSON.stringify({ operation_id, text, ...(source_ids?.length ? { source_ids } : {}) }) }),
  upload: (file: File, operation_id: string) => requestJson<SourceAttachment>(`${root}/sources?name=${encodeURIComponent(file.name)}&operation_id=${encodeURIComponent(operation_id)}`, { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' }, timeoutMs: 60000 }),
  transcribe: (id: string, operation_id: string, text: string) => requestJson<SourceAttachment>(`${root}/sources/${encodeURIComponent(id)}/transcription`, { method: 'POST', body: JSON.stringify({ operation_id, expected_revision: 1, text }) }),
  sources: () => requestJson<{sources: SourceAttachment[]}>(`${root}/sources`),
  deleteSource: (source: SourceAttachment, operation_id: string) => requestJson<SourceAttachment>(`${root}/sources/${encodeURIComponent(source.id)}`, {method: 'DELETE', body: JSON.stringify({operation_id, expected_revision: source.revision || 1})}),
  source: (recordId: string, sourceId: string) => `${root}/commitments/${encodeURIComponent(recordId)}/sources/${encodeURIComponent(sourceId)}`,
  detail: (id: string) => requestJson<Detail>(`${root}/commitments/${encodeURIComponent(id)}`),
  review: (id: string, body: Review) => requestJson<Commitment>(`${root}/commitments/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) }),
  extract: (id: string, operation_id: string, expected_revision: number, stop = false) => requestJson<Commitment>(`${root}/commitments/${encodeURIComponent(id)}/extract${stop ? '/stop' : ''}`, { method: 'POST', body: JSON.stringify({ operation_id, expected_revision }) }),
  calendar: (id: string) => `${root}/commitments/${encodeURIComponent(id)}/calendar.ics`,
  reminder: (id: string, body: ReminderChange) => requestJson<Commitment>(`${root}/commitments/${encodeURIComponent(id)}/reminder`, { method: 'POST', body: JSON.stringify(body) }),
  inbox: () => requestJson<{ notifications: ReminderNotice[]; has_more: boolean }>(`${root}/notifications`),
  read: (item: ReminderNotice, operation_id: string) => requestJson<ReminderNotice>(`${root}/notifications/${encodeURIComponent(item.id)}/read`, { method: 'POST', body: JSON.stringify({ operation_id, expected_revision: item.revision }) }),
};

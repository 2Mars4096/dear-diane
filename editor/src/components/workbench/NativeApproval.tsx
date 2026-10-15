import { useState } from 'react';
import { requestJson } from '../../lib/http';
import type { WorkerRequest } from './teamPresentation';

export function NativeApproval({ request, endpoint }: { request: WorkerRequest; endpoint: string }) {
  const [busy, setBusy] = useState(false), [answered, setAnswered] = useState(false), [error, setError] = useState('');
  const input = (request.params.input || {}) as Record<string, unknown>;
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Record<string, string[]>>({});
  const questions = request.params.questions;
  const answers = Object.fromEntries((questions || []).map(question => [question.id,
    [...(selected[question.id] || []), drafts[question.id]?.trim()].filter(Boolean).join('\n')]));
  const complete = !questions?.some(question => !answers[question.id]);
  async function answer(decision: string) {
    if (busy || (decision === 'accept' && !complete)) return;
    setBusy(true); setError('');
    try { await requestJson(endpoint, { method: 'POST', body: JSON.stringify({ request_id: String(request.id), decision, answers }) }); setAnswered(true); }
    catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  }
  if (answered) return null;
  return <section className={`wb-native-approval${questions ? ' wb-question-composer' : ''}`} aria-label={questions ? 'Reply to Claude' : 'Claude permission request'}>
    <strong>{questions ? 'Claude has a question' : `Claude requests permission · ${String(request.params.tool_name || 'Tool')}`}</strong>
    {!questions && <p>{String(request.params.reason || 'Review this action before allowing it.')}</p>}
    {questions ? questions.map(question => {
      const originals = Array.isArray(input.questions) ? input.questions as { question?: string; multiSelect?: boolean }[] : [];
      const multiple = question.multiSelect ?? originals.find(item => item.question === question.question)?.multiSelect ?? false;
      return <fieldset key={question.id} disabled={busy} className="wb-question-field">
        <legend>{question.question}</legend>
        {!!question.options?.length && <div className="wb-question-choices" role={multiple ? 'group' : 'radiogroup'} aria-label={question.question}>
          {question.options.map(option => <label key={option.label} className="wb-question-choice">
            <input type={multiple ? 'checkbox' : 'radio'} name={`question-${request.id}-${question.id}`} checked={(selected[question.id] || []).includes(option.label)} onChange={() => { setDrafts(current => ({ ...current, [question.id]: "" })); setSelected(current => ({ ...current, [question.id]: multiple ? (current[question.id] || []).includes(option.label) ? current[question.id].filter(value => value !== option.label) : [...(current[question.id] || []), option.label] : [option.label] })); }} />
            <span><strong>{option.label}</strong>{option.description && <small>{option.description}</small>}</span>
          </label>)}
        </div>}
        <textarea aria-label={`Your answer: ${question.question}`} rows={3} placeholder={question.options?.length ? 'Or type your own answer…' : 'Type your answer…'} value={drafts[question.id] || ''} onChange={event => { const value = event.target.value; setDrafts(current => ({ ...current, [question.id]: value })); if (value.trim()) setSelected(current => ({ ...current, [question.id]: [] })); }} onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && complete) { event.preventDefault(); void answer('accept'); }
        }} />
      </fieldset>;
    }) : <dl>{Object.entries(input).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd><pre>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre></dd></div>)}</dl>}
    <div className="wb-question-actions"><button disabled={busy || !complete} onClick={() => void answer('accept')}>{busy ? 'Sending…' : questions ? 'Send answer' : 'Allow once'}</button>
    <button disabled={busy} onClick={() => void answer('decline')}>{questions ? 'Dismiss question' : 'Deny'}</button></div>
    {error && <p role="alert">{error}</p>}
  </section>;
}

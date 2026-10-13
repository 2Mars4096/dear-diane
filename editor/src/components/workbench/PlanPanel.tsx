import { useEffect, useState } from 'react';
import type { PlanQuestion, PlanState } from './sessionPlan';
import './plan-mode.css';

function Choices({ questions, busy, onAnswer }: { questions: PlanQuestion[]; busy: boolean; onAnswer: (text: string) => void }) {
  const [answers, setAnswers] = useState(() => questions.map(() => ({ selected: [] as string[], other: false, comment: '' })));
  const [submitted, setSubmitted] = useState(false);
  useEffect(() => { if (!busy) setSubmitted(false); }, [busy]);
  const valid = answers.every(answer => (answer.selected.length > 0 || answer.other) && (!answer.other || answer.comment.trim()));
  return <form className="wb-plan-question" onSubmit={event => {
    event.preventDefault();
    if (!valid || busy || submitted) return;
    setSubmitted(true);
    onAnswer(questions.map((question, index) => {
      const answer = answers[index];
      return `For "${question.question}": ${[...answer.selected, ...(answer.other ? [answer.comment.trim()] : [])].join('; ')}${!answer.other && answer.comment.trim() ? `\nComment: ${answer.comment.trim()}` : ''}`;
    }).join('\n\n'));
  }}>
    {questions.map((question, index) => {
      const answer = answers[index];
      const multiple = question.selection_mode === 'multiple';
      const choices = [...new Set([question.recommended, ...question.alternatives].filter(Boolean))];
      const update = (patch: Partial<typeof answer>) => setAnswers(current => current.map((value, i) => i === index ? { ...value, ...patch } : value));
      return <fieldset key={index} data-selection={multiple ? "multiple" : "single"} disabled={busy || submitted}>
        <legend>{question.question}</legend>
        {question.reason && <p>{question.reason}</p>}
        <p className="wb-plan-selection" aria-live="polite"><strong>{multiple ? 'Choose multiple' : 'Choose one'}</strong></p>
        {choices.map(choice => <label className="wb-plan-option" key={choice}>
          <input type={multiple ? "checkbox" : "radio"} name={`plan-choice-${index}`} checked={answer.selected.includes(choice)} onChange={() => update({ selected: multiple ? answer.selected.includes(choice) ? answer.selected.filter(value => value !== choice) : [...answer.selected, choice] : answer.selected.includes(choice) ? [] : [choice], other: multiple ? answer.other : false })} />
          <span>{choice}{choice === question.recommended && <small> Recommended</small>}</span>
        </label>)}
        <div className="wb-plan-other"><label className="wb-plan-option"><input type={multiple ? "checkbox" : "radio"} name={`plan-choice-${index}`} checked={answer.other} onChange={() => update({ other: !answer.other, selected: multiple ? answer.selected : [] })} /><span>Other</span></label>
        <label className="wb-plan-comment"><textarea aria-label={`Comments for ${question.question}`} placeholder={answer.other ? "Describe your choice…" : "Comments (optional)"} rows={1} value={answer.comment} onChange={event => update({ comment: event.target.value, ...(event.target.value.trim() ? { other: true, selected: multiple ? answer.selected : [] } : {}) })} /></label></div>
      </fieldset>;
    })}
    <button type="submit" disabled={!valid || busy || submitted}>{submitted ? 'Answers submitted' : questions.length > 1 ? 'Submit answers' : 'Submit answer'}</button>
  </form>;
}

export default function PlanPanel({ state, loaded, saving, busy, error, permission, onAnswer, onStart, onProposal, onReload }: {
  state: PlanState; loaded: boolean; saving: boolean; busy: boolean; error: string; permission: string;
  onAnswer: (text: string) => void; onStart: () => void;
  onProposal: (accept: boolean) => Promise<unknown>; onReload: () => Promise<unknown>;
}) {
  const doc = state.document;
  const questions = state.mode === 'plan' ? state.questions?.length ? state.questions : state.question ? [state.question] : [] : [];
  if (!state.document && !questions.length && !error) return null;
  const started = state.execution?.revision === state.revision;
  const blocked = Boolean(doc?.blockers.trim());
  return <section className="wb-plan wb-plan-inline" aria-label="Plan review">
    {!loaded ? <p>Loading plan…</p> : questions.length ? <Choices key={JSON.stringify(questions) + state.questions_asked} questions={questions} busy={busy || saving} onAnswer={onAnswer} /> : doc ? <>
      <h2>{doc.title}</h2>
      {doc.outcome && <p className="wb-plan-text">{doc.outcome}</p>}
      {([['Scope', doc.scope], ['Specifications', doc.specifications], ['Next steps', doc.steps], ['Assumptions', doc.assumptions], ['Needs a decision', doc.blockers]] as const).filter(([, text]) => text.trim()).map(([label, text]) => <section className="wb-plan-section" key={label}><h3>{label}</h3><p className="wb-plan-text">{text}</p></section>)}
      {state.proposal && <div><p>A newer draft is ready. Your saved plan is preserved.</p><details><summary>Review newer draft</summary><p className="wb-plan-text">{Object.values(state.proposal.document).filter(Boolean).join('\n\n')}</p></details><button disabled={busy || saving} onClick={() => void onProposal(true).catch(() => {})}>Use newer draft</button><button disabled={busy || saving} onClick={() => void onProposal(false).catch(() => {})}>Keep saved plan</button></div>}
      {started ? <p role="status">Plan started · {state.execution?.status || 'Starting'}</p> : <div className="wb-plan-actions"><button disabled={busy || saving || blocked || !doc.outcome.trim() || !doc.steps.trim() || Boolean(state.proposal) || Boolean(error) || permission === 'Read only'} onClick={onStart}>Start this plan</button>{blocked && <small>Resolve the remaining decision before starting.</small>}</div>}
    </> : null}
    {error && <div role="alert"><p>{error}</p><button onClick={() => void onReload().catch(() => {})}>Retry</button></div>}
  </section>;
}

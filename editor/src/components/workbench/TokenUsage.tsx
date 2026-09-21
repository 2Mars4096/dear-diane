import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { BACKENDS, FLAGS, curvePoints, fillDays, folderName, formatTokens, percent, visibleSessions, type Overview, type Report, type SessionFilter, type SessionRow, type Share } from "./tokenUsageFormat";

type Listing = { sessions: SessionRow[]; actions: Record<string, string>; stages: Record<string, string> };
type Classifier = { model: string; configured: boolean };

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || "Token usage could not be loaded.");
  return response.json();
}

/** DAN settings: which agent sessions use the most tokens, and why. */
export function TokenUsage() {
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  useEffect(() => { getJson<Listing>("/api/token-usage/sessions?limit=40").then(setListing).catch((caught) => setError(String(caught.message ?? caught))); }, []);
  const heaviest = listing ? visibleSessions(listing.sessions, "all", "tokens").slice(0, 3) : [];
  return <section className="wb-token-usage">
    <h3>Token usage</h3>
    <p>See which Claude Code and Codex sessions use the most tokens, what the agent was doing, and what to change. Read from the transcripts on this computer.</p>
    {error && <p role="alert">{error}</p>}
    {!listing && !error && <p>Reading recent sessions…</p>}
    {listing && <ul className="wb-token-mini">{heaviest.map((row) => <li key={row.id}><span>{row.title}</span><b>{formatTokens(row.total)}</b></li>)}</ul>}
    <button className="wb-token-open" disabled={!listing} onClick={() => setOpen(true)}>Open analyzer</button>
    {open && listing && <Analyzer listing={listing} onListing={setListing} onClose={() => setOpen(false)} />}
  </section>;
}

function Analyzer({ listing, onListing, onClose }: { listing: Listing; onListing: (listing: Listing) => void; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [filter, setFilter] = useState<SessionFilter>("all");
  const [sort, setSort] = useState<"recent" | "tokens">("tokens");
  const [selected, setSelected] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [classifier, setClassifier] = useState<Classifier | null>(null);
  const [tab, setTab] = useState<"overview" | "sessions">("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  useEffect(() => { void run("Combining sessions…", async () => setOverview(await getJson<Overview>("/api/token-usage/overview?limit=120"))); }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { dialog.current?.showModal(); getJson<Classifier>("/api/token-usage/settings").then(setClassifier).catch(() => undefined); }, []);
  const rows = visibleSessions(listing.sessions, filter, sort);
  const peak = Math.max(...rows.map((row) => row.total), 1);
  async function run(label: string, work: () => Promise<void>) {
    setBusy(label); setError("");
    try { await work(); } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); } finally { setBusy(""); }
  }
  const choose = (id: string) => { setTab("sessions"); setSelected(id); setReport(null); void run("Analyzing session…", async () => setReport(await getJson<Report>(`/api/token-usage/session?id=${encodeURIComponent(id)}`))); };
  const refresh = () => run("Reading sessions…", async () => { onListing(await getJson<Listing>("/api/token-usage/sessions?limit=120")); setOverview(await getJson<Overview>("/api/token-usage/overview?limit=120")); });
  const classify = () => run(`Labelling steps with ${classifier?.model}…`, async () => setReport(await getJson<Report>("/api/token-usage/classify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: selected }) })));
  const saveModel = (model: string) => run("", async () => setClassifier(await getJson<Classifier>("/api/token-usage/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }) })));
  return <dialog ref={dialog} className="wb-project-settings wb-token-dialog" onCancel={(event) => { event.stopPropagation(); onClose(); }} onClose={onClose} onClick={(event) => event.stopPropagation()} aria-labelledby="token-usage-title">
    <header><h2 id="token-usage-title">Token usage</h2><button aria-label="Close token usage" onClick={onClose}><X size={18} /></button></header>
    <div className="wb-token-bar">
      <span className="wb-token-toggle" role="tablist" aria-label="Token usage view"><button role="tab" aria-selected={tab === "overview"} aria-pressed={tab === "overview"} onClick={() => setTab("overview")}>All sessions</button><button role="tab" aria-selected={tab === "sessions"} aria-pressed={tab === "sessions"} onClick={() => setTab("sessions")}>By session</button></span>
      {tab === "sessions" && <><select aria-label="Show sessions from" value={filter} onChange={(event) => setFilter(event.target.value as SessionFilter)}><option value="all">All sessions</option><option value="claude">Claude Code</option><option value="codex">Codex</option><option value="dan">Started by DAN</option></select>
      <select aria-label="Sort sessions" value={sort} onChange={(event) => setSort(event.target.value as typeof sort)}><option value="tokens">Most tokens</option><option value="recent">Most recent</option></select></>}
      <button onClick={() => void refresh()} disabled={Boolean(busy)}>Refresh</button>
      <span role="status">{busy}</span>
    </div>
    {error && <p role="alert">{error}</p>}
    {tab === "overview" && <div className="wb-token-overview">{overview ? <Combined overview={overview} onOpen={choose} /> : !error && <p>Combining recent sessions…</p>}</div>}
    {tab === "sessions" && <div className="wb-token-columns">
      <ul className="wb-token-sessions" aria-label="Sessions">{rows.map((row) => <li key={row.id}><button aria-pressed={row.id === selected} onClick={() => choose(row.id)}>
        <span className="wb-token-title">{row.title}</span>
        <small>{BACKENDS[row.backend] ?? row.backend}{folderName(row.cwd) ? ` · ${folderName(row.cwd)}` : ""} · {new Date(row.updated_at * 1000).toLocaleDateString()}{row.dan ? ` · DAN ${row.dan.role}` : ""}</small>
        <span className="wb-token-meter"><i style={{ width: `${Math.max(1, (row.total / peak) * 100)}%` }} /></span>
        <small><b>{formatTokens(row.total)}</b> tokens · {row.totals.calls} calls · {row.totals.rounds} rounds{row.subagents ? ` · ${row.subagents} subagents` : ""}</small>
      </button></li>)}{!rows.length && <li><p>No sessions match.</p></li>}</ul>
      <div className="wb-token-detail">
        {!report && <p>{selected ? "" : "Choose a session to see where its tokens went."}</p>}
        {report && <Detail report={report} names={listing} classifier={classifier} busy={Boolean(busy)} onClassify={() => void classify()} onModel={(model) => void saveModel(model)} />}
      </div>
    </div>}
  </dialog>;
}

function Combined({ overview, onOpen }: { overview: Overview; onOpen: (id: string) => void }) {
  const [view, setView] = useState<"stage" | "action">("stage");
  const totals = overview.totals;
  const days = fillDays(overview.days);
  const dayPeak = Math.max(...days.map((day) => day.total), 1);
  const busiest = days.reduce((best, day) => day.total > best.total ? day : best, days[0]);
  const sessionPeak = Math.max(...overview.top_sessions.map((row) => row.total), 1);
  const tiles: [string, string][] = [["Total", formatTokens(overview.total)], ["Sessions", String(overview.sessions)], ["Per session", formatTokens(overview.total / Math.max(1, overview.sessions))], ["New input", formatTokens(totals.input_fresh)],
    ["Cached input", `${formatTokens(totals.cache_read)} (${percent(totals.cache_hit)})`], ["Output", formatTokens(totals.output)], ["Subagents", formatTokens(overview.subagent_total)], ["Model calls", totals.calls.toLocaleString()]];
  const names = (rows: Share[]) => Object.fromEntries(rows.map((row) => [row.key, `${row.count} sessions`]));
  return <>
    <p>The {overview.sessions} most recent Claude Code and Codex sessions on this computer. The heaviest tenth of them used {percent(overview.concentration)} of all tokens.</p>
    <dl className="wb-token-tiles">{tiles.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {days.length > 1 && <figure className="wb-token-days"><div role="img" aria-label={`Tokens per day, highest ${formatTokens(busiest.total)} on ${busiest.day}`}>{days.map((day) => <span key={day.day} title={`${day.day}: ${formatTokens(day.total)} tokens in ${day.sessions} sessions (Claude Code ${formatTokens(day.claude)}, Codex ${formatTokens(day.codex)})`}><i style={{ height: `${(day.total / dayPeak) * 100}%` }} /></span>)}</div>
      <figcaption><span>{days[0].day}</span><span>Tokens per day, by last activity · peak {formatTokens(busiest.total)} on {busiest.day}</span><span>{days[days.length - 1].day}</span></figcaption></figure>}
    {overview.advice.length > 0 && <><h4>What to change</h4><ol className="wb-token-advice">{overview.advice.map((tip) => <li key={tip.title}><strong>{tip.title}</strong> <b>{formatTokens(tip.tokens)}</b><p>{tip.detail}</p></li>)}</ol></>}
    <div className="wb-token-grid">
      <section><h4>Where the tokens went <span className="wb-token-toggle"><button aria-pressed={view === "stage"} onClick={() => setView("stage")}>By stage</button><button aria-pressed={view === "action"} onClick={() => setView("action")}>By action</button></span></h4>
        <Bars rows={view === "stage" ? overview.by_stage : overview.by_action} names={view === "stage" ? overview.stages : overview.actions} total={0} /></section>
      <section><h4>By project</h4><Bars rows={overview.by_project} names={names(overview.by_project)} total={0} unit="sessions" /></section>
      <section><h4>By agent</h4><Bars rows={overview.by_agent.map((row) => ({ ...row, key: BACKENDS[row.key] ?? row.key }))} names={{}} total={0} unit="sessions" />
        <h4>By model</h4><Bars rows={overview.by_model} names={{}} total={0} unit="sessions" /></section>
      <section><h4>Avoidable patterns</h4>{overview.flags.length ? <ul className="wb-token-flags">{overview.flags.map((flag) => <li key={flag.flag}><strong>{FLAGS[flag.flag] ?? flag.flag}</strong> · {flag.count.toLocaleString()}× · {formatTokens(flag.burden)} tokens</li>)}</ul> : <p>None found.</p>}</section>
    </div>
    <h4>Heaviest sessions</h4>
    <ol className="wb-token-rounds">{overview.top_sessions.map((row) => <li key={row.id}><button className="wb-token-title wb-token-link" onClick={() => onOpen(row.id)}>{row.title}</button><span className="wb-token-meter"><i style={{ width: `${Math.max(1, (row.total / sessionPeak) * 100)}%` }} /></span>
      <small><b>{formatTokens(row.total)}</b> · {BACKENDS[row.backend] ?? row.backend}{folderName(row.cwd) ? ` · ${folderName(row.cwd)}` : ""} · {new Date(row.updated_at * 1000).toLocaleDateString()}</small></li>)}</ol>
  </>;
}

function Bars({ rows, names, total, other, unit }: { rows: Share[]; names: Record<string, string>; total: number; other?: number; unit?: string }) {
  return <ul className="wb-token-bars">{rows.filter((row) => row.share >= 0.005).map((row) => <li key={row.key} title={names[row.key]}>
    <span className="wb-token-title">{row.key}</span><span className="wb-token-meter"><i style={{ width: `${row.share * 100}%` }} /></span><b>{percent(row.share)}</b><small>{formatTokens(row.weight)}{row.count ? ` · ${row.count}${unit ? ` ${unit}` : "×"}` : ""}</small>
  </li>)}{Boolean(other && total) && <li title="Attachments, injected reminders, and estimation error"><span>not attributed</span><span className="wb-token-meter" /><b /><small>{formatTokens(other!)} of {formatTokens(total)} read</small></li>}</ul>;
}

function Detail({ report, names, classifier, busy, onClassify, onModel }: { report: Report; names: Listing; classifier: Classifier | null; busy: boolean; onClassify: () => void; onModel: (model: string) => void }) {
  const [view, setView] = useState<"stage" | "action">("stage");
  const [model, setModel] = useState(classifier?.model ?? "");
  useEffect(() => setModel(classifier?.model ?? ""), [classifier?.model]);
  const totals = report.totals;
  const roundPeak = Math.max(...report.rounds.map((round) => round.total), 1);
  const tiles: [string, string][] = [["Total", formatTokens(report.total)], ["New input", formatTokens(totals.input_fresh)], ["Cached input", `${formatTokens(totals.cache_read)} (${percent(totals.cache_hit)})`], ["Output", formatTokens(totals.output)], ["Largest context", formatTokens(totals.peak_context)], ["Model calls", String(totals.calls)]];
  return <>
    <h3 className="wb-token-name">{report.title}</h3>
    <p>{BACKENDS[report.backend] ?? report.backend} · {totals.models.join(", ") || "model not recorded"} · <code>{report.session_id}</code></p>
    <dl className="wb-token-tiles">{tiles.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {report.context_curve.length > 1 && <figure className="wb-token-curve"><svg viewBox="0 0 600 60" preserveAspectRatio="none" role="img" aria-label={`Context size per model call, peaking at ${formatTokens(totals.peak_context)} tokens`}><polyline points={curvePoints(report.context_curve, 600, 58)} /></svg><figcaption>Context size per call{totals.compactions ? ` · ${totals.compactions} compactions` : ""}</figcaption></figure>}

    {report.advice.length > 0 && <><h4>What to change</h4><ol className="wb-token-advice">{report.advice.map((tip) => <li key={tip.title}><strong>{tip.title}</strong> <b>{formatTokens(tip.tokens)}</b><p>{tip.detail}</p></li>)}</ol></>}

    <h4>Where the tokens went <span className="wb-token-toggle"><button aria-pressed={view === "stage"} onClick={() => setView("stage")}>By stage</button><button aria-pressed={view === "action"} onClick={() => setView("action")}>By action</button></span></h4>
    <p>Each step is charged for the tokens it added, times the model calls that re-read them. Labels: {report.labelled_by === "rules" ? "built-in rules" : report.labelled_by}.</p>
    <Bars rows={view === "stage" ? report.by_stage : report.by_action} names={view === "stage" ? names.stages : names.actions} total={report.context_total} other={report.unattributed} />
    <div className="wb-token-classify">
      <label>Classifier model<input value={model} onChange={(event) => setModel(event.target.value)} onBlur={() => model.trim() && model !== classifier?.model && onModel(model)} spellCheck={false} /></label>
      <button onClick={onClassify} disabled={busy || !classifier?.configured}>Label stages with model</button>
      <p>{classifier?.configured ? "Sends each step's tool name, command or path, and size to OpenRouter. File contents and command output are never sent." : "Add an OpenRouter key to DAN's .env to use the classifier."}</p>
    </div>

    <h4>Chat rounds</h4>
    <ol className="wb-token-rounds">{report.rounds.map((round) => <li key={round.index}><span className="wb-token-title">{round.title}</span><span className="wb-token-meter"><i style={{ width: `${Math.max(1, (round.total / roundPeak) * 100)}%` }} /></span><small><b>{formatTokens(round.total)}</b> · {round.calls} calls · {round.steps} tools{round.top_stage ? ` · mostly ${round.top_stage}` : ""}</small></li>)}</ol>

    <h4>Most expensive steps</h4>
    <table className="wb-token-steps"><thead><tr><th>Step</th><th>Stage</th><th>Added</th><th>Cost</th></tr></thead><tbody>{report.top_steps.slice(0, 25).map((step) => <tr key={step.i}>
      <td><strong>{step.tool || step.kind}</strong> <span>{step.detail}</span>{step.flags?.map((flag) => <em key={flag}>{FLAGS[flag] ?? flag}</em>)}</td><td>{step.stage}</td><td>{formatTokens(step.added)}</td><td>{formatTokens(step.weight)}</td>
    </tr>)}</tbody></table>

    {report.flags.length > 0 && <><h4>Avoidable patterns</h4><ul className="wb-token-flags">{report.flags.map((flag) => <li key={flag.flag}><strong>{FLAGS[flag.flag] ?? flag.flag}</strong> · {flag.count}× · {formatTokens(flag.burden)} tokens<small>{flag.examples.join(" · ")}</small></li>)}</ul></>}
    {report.subagents.length > 0 && <><h4>Subagents</h4><ul className="wb-token-flags">{report.subagents.map((agent, index) => <li key={index}><strong>{agent.name}</strong> · {formatTokens(agent.totals.total)} tokens · {agent.totals.calls} calls</li>)}</ul></>}
  </>;
}

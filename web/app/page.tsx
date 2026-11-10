"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Answer, ApiError, Persona, ask, getPersonas, sendFeedback } from "@/lib/api";

const EXAMPLES = [
  "What is the attrition rate by department?",
  "Average monthly income by job role",
  "Are people who work overtime more likely to leave?",
  "What is the attrition rate for each team?",
  "What does employee 1 earn?",
];

const STATUS_LABEL: Record<Answer["status"], string> = {
  answered: "Checked answer",
  fallback: "Raw result",
  partial: "Partly answered",
  help: "About this assistant",
  refused: "Not answered",
  blocked: "Blocked for privacy",
  no_data: "Nothing to show",
  error: "Couldn’t answer",
};

type Entry = { id: number; persona: Persona; question: string; result: Answer };

const REPO = process.env.NEXT_PUBLIC_REPO_URL || "https://github.com/BinuShefieldShifani/workforce-assistant";

export default function Home() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [personaKey, setPersonaKey] = useState("ceo");
  const [question, setQuestion] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [busy, setBusy] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const nextId = useRef(1);

  useEffect(() => {
    getPersonas()
      .then((list) => {
        setPersonas(list);
        if (list.length && !list.some((p) => p.key === "ceo")) setPersonaKey(list[0].key);
      })
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Couldn’t load the demo leaders."));
  }, []);

  useEffect(() => {
    if (!busy) return;
    setSeconds(0);
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [busy]);

  const persona = personas.find((p) => p.key === personaKey);

  async function submit(e?: FormEvent, text?: string) {
    e?.preventDefault();
    const q = (text ?? question).trim();
    if (!q || !persona || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await ask(persona.key, q);
      setEntries((prev) => [{ id: nextId.current++, persona, question: q, result }, ...prev]);
      setQuestion("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="page">
      <header className="masthead">
        <div>
          <h1>Workforce Assistant</h1>
          <p>
            Ask about the people in your part of the organisation. Every number is checked against the data,
            and groups under 5 people are never shown.
          </p>
        </div>
        <div className="persona">
          <label htmlFor="persona">Asking as</label>
          <select
            id="persona"
            value={personaKey}
            onChange={(e) => setPersonaKey(e.target.value)}
            disabled={!personas.length}
          >
            {personas.map((p) => (
              <option key={p.key} value={p.key}>
                {p.title} ({p.headcount} people)
              </option>
            ))}
          </select>
          {persona && <small>{persona.unit}</small>}
        </div>
      </header>

      <form className="ask" onSubmit={submit}>
        <label htmlFor="question">Your question</label>
        <div className="ask-row">
          <textarea
            id="question"
            value={question}
            maxLength={500}
            placeholder="For example: What is the attrition rate by job role?"
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <button className="primary" type="submit" disabled={busy || !question.trim() || !persona}>
            {busy ? "Asking…" : "Ask"}
          </button>
        </div>
        <ul className="examples" aria-label="Example questions">
          {EXAMPLES.map((ex) => (
            <li key={ex}>
              <button type="button" disabled={busy || !persona} onClick={() => submit(undefined, ex)}>
                {ex}
              </button>
            </li>
          ))}
        </ul>
      </form>

      <div aria-live="polite">
        {busy && (
          <p className="working">
            Writing a query, checking it and verifying the answer… {seconds} s
            {seconds >= 12 && " The free model tier can be slow at busy times."}
          </p>
        )}
        {error && <p className="notice">{error}</p>}
      </div>

      {entries.length === 0 && !busy && (
        <p className="empty">
          Pick who you’re asking as, then ask a question or try one of the examples. You can ask about headcount,
          attrition, pay, satisfaction, overtime, tenure, promotions and training, by department, role, level or team.
          Try the 3-person team lead to see what happens when a group is too small.
        </p>
      )}

      <section className="entries" aria-label="Answers">
        {entries.map((entry) => (
          <EntryView key={entry.id} entry={entry} />
        ))}
      </section>

      <footer>
        <p>
          Fictional data: the IBM HR Attrition sample (1,470 people) with a generated org chart. Questions are logged
          to see what people ask, so don’t type real personal information.
        </p>
        <p>
          <a href={REPO}>Source code and evaluation report on GitHub</a>
        </p>
      </footer>
    </main>
  );
}

function EntryView({ entry }: { entry: Entry }) {
  const { result } = entry;
  const [rated, setRated] = useState<1 | -1 | null>(null);

  async function rate(value: 1 | -1) {
    setRated(value);
    try {
      await sendFeedback(entry.persona.key, entry.question, result.status, value);
    } catch {
      /* feedback is best-effort */
    }
  }

  const feedback = (
    <span className="feedback">
      {rated === null ? (
        <>
          Was this useful?
          <button type="button" onClick={() => rate(1)}>
            Yes
          </button>
          <button type="button" onClick={() => rate(-1)}>
            No
          </button>
        </>
      ) : (
        "Thanks, noted."
      )}
    </span>
  );

  return (
    <article className="entry">
      <p className="q">
        <strong>{entry.question}</strong> asked as {entry.persona.title}
      </p>
      {result.parts.length > 0 ? (
        <>
          <span className={`status ${result.status}`}>{STATUS_LABEL[result.status]}</span>
          <p className="split-note">I answered this as {result.parts.length} separate questions.</p>
          {result.parts.map((part, i) => (
            <section className="part" key={i} aria-label={part.question}>
              <h3>{part.question}</h3>
              <AnswerBody result={part} />
            </section>
          ))}
          <div className="tools">
            <span>{(result.latency_ms / 1000).toFixed(1)} s</span>
            {feedback}
          </div>
        </>
      ) : (
        <AnswerBody result={result} latency={result.latency_ms} feedback={feedback} />
      )}
    </article>
  );
}

function AnswerBody({
  result,
  latency,
  feedback,
}: {
  result: Answer;
  latency?: number;
  feedback?: React.ReactNode;
}) {
  const [showTable, setShowTable] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const hasRows = result.rows.length > 0;

  return (
    <>
      <span className={`status ${result.status}`}>{STATUS_LABEL[result.status]}</span>
      <p className="answer">{result.answer}</p>

      {result.trust_trail.length > 0 && (
        <ol className="trail" aria-label="Checks before this answer was shown">
          {result.trust_trail.map((step) => (
            <li key={step.label} className={step.ok ? undefined : "bad"}>
              {step.label}
            </li>
          ))}
        </ol>
      )}

      <div className="tools">
        {hasRows && (
          <button className="link-button" type="button" onClick={() => setShowTable((v) => !v)}>
            {showTable ? "Hide data" : `Show data (${result.total_rows} ${result.total_rows === 1 ? "row" : "rows"})`}
          </button>
        )}
        {result.sql && (
          <button className="link-button" type="button" onClick={() => setShowSql((v) => !v)}>
            {showSql ? "Hide SQL" : "Show SQL"}
          </button>
        )}
        {latency !== undefined && <span>{(latency / 1000).toFixed(1)} s</span>}
        {feedback}
      </div>

      {showTable && hasRows && <ResultTable rows={result.rows} total={result.total_rows} />}
      {showSql && result.sql && <pre className="sql">{result.sql}</pre>}
    </>
  );
}

function ResultTable({ rows, total }: { rows: Record<string, unknown>[]; total: number }) {
  const columns = Object.keys(rows[0]);
  const isNum = (c: string) => rows.every((r) => r[c] === null || typeof r[c] === "number");
  const header = (c: string) => (c === "group_size" ? "people" : c.replace(/_/g, " "));
  // A blank number is a count under 5 that was masked; a blank label is a real empty
  // value, e.g. unit heads have no team.
  const cell = (v: unknown, numeric: boolean) => {
    if (v === null || v === undefined) return numeric ? "under 5" : "(none)";
    if (typeof v === "boolean") return v ? "yes" : "no";
    return String(v);
  };

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} className={isNum(c) ? "num" : undefined} scope="col">
                {header(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} className={isNum(c) ? "num" : undefined}>
                  {cell(r[c], isNum(c))}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {total > rows.length && <p className="table-note">Showing {rows.length} of {total} rows.</p>}
    </div>
  );
}

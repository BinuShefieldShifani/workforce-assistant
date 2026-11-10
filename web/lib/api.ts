export type Persona = {
  key: string;
  title: string;
  blurb: string;
  leader_id: number;
  unit: string;
  headcount: number;
};

export type TrailStep = { label: string; ok: boolean };

export type Status =
  | "answered"
  | "fallback"
  | "partial"
  | "refused"
  | "blocked"
  | "no_data"
  | "help"
  | "error";

export type Answer = {
  question: string;
  status: Status;
  assumption: string | null;
  parts: Answer[];
  answer: string;
  sql: string | null;
  rows: Record<string, unknown>[];
  total_rows: number;
  hidden_groups: number;
  masked_cells: number;
  trust_trail: TrailStep[];
  latency_ms: number;
};

const BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    });
  } catch {
    throw new ApiError(
      "Can’t reach the server. The demo back end may be starting up; try again in a minute."
    );
  }
  if (!res.ok) {
    let detail = `The server returned an error (${res.status}).`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(detail);
  }
  return res.json() as Promise<T>;
}

export const getPersonas = () => request<Persona[]>("/api/personas");

export const ask = (persona: string, question: string) =>
  request<Answer>("/api/ask", { method: "POST", body: JSON.stringify({ persona, question }) });

export const sendFeedback = (persona: string, question: string, status: string, rating: 1 | -1) =>
  request<{ ok: boolean }>("/api/feedback", {
    method: "POST",
    body: JSON.stringify({ persona, question, status, rating }),
  });

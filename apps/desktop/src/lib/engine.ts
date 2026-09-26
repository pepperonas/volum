/**
 * The client for the local engine.
 *
 * The shell starts the engine and hands the window its address and session
 * token; everything here is an ordinary HTTP call to `127.0.0.1` with that
 * token attached. No pipeline logic lives in the window — if a decision has to
 * be made about *what happens*, it belongs in `volum_core.service`, where the
 * CLI can reach it too.
 */
import { invoke } from "@tauri-apps/api/core";
import { SseDecoder } from "./sse";
import type {
  DoctorReport,
  InstallTask,
  JobRecord,
  JobRequest,
  JobSubmission,
  ModelEntry,
  SettingsPatch,
  SettingsView,
} from "./types";

export interface EngineInfo {
  baseUrl: string;
  token: string;
  version: string;
  pid: number;
}

export interface EngineFailure {
  message: string;
  detail: string;
  suggestions: string[];
}

/** A failure the engine reported, or one that stopped us reaching it. */
export class EngineError extends Error {
  readonly technical: string | null;
  readonly suggestions: string[];
  readonly status: number | null;

  constructor(
    message: string,
    options: { technical?: string | null; suggestions?: string[]; status?: number | null } = {},
  ) {
    super(message);
    this.name = "EngineError";
    this.technical = options.technical ?? null;
    this.suggestions = options.suggestions ?? [];
    this.status = options.status ?? null;
  }
}

/**
 * Turn a failed response into an error a person can read.
 *
 * The engine answers every failure in one shape (`docs/architecture.md` §3).
 * When the body is not that shape — a crash before the handlers, a proxy in
 * the way — the status is all there is, and saying so beats inventing a cause.
 */
export function toEngineError(status: number, body: unknown): EngineError {
  const error =
    typeof body === "object" && body !== null && "error" in body ? body.error : null;

  if (typeof error === "object" && error !== null && "message" in error) {
    const shaped = error as { message: unknown; technical?: unknown; suggestions?: unknown };
    return new EngineError(String(shaped.message), {
      technical: typeof shaped.technical === "string" ? shaped.technical : null,
      suggestions: Array.isArray(shaped.suggestions) ? shaped.suggestions.map(String) : [],
      status,
    });
  }
  return new EngineError(`The engine answered with an unexpected error (HTTP ${status}).`, {
    technical: typeof body === "string" ? body : JSON.stringify(body),
    status,
  });
}

let pending: Promise<EngineInfo> | null = null;

/**
 * The engine's address and token, starting it on the first call.
 *
 * The promise is cached rather than the value, so several screens mounting at
 * once wait on one start instead of racing into several.
 */
export function engineInfo(): Promise<EngineInfo> {
  pending ??= invoke<EngineInfo>("engine_info").catch((failure: unknown) => {
    pending = null;
    const shaped = failure as Partial<EngineFailure>;
    throw new EngineError(shaped?.message ?? "VOLUM could not start its engine.", {
      technical: shaped?.detail ?? String(failure),
      suggestions: shaped?.suggestions ?? [],
    });
  });
  return pending;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const engine = await engineInfo();
  let response: Response;
  try {
    response = await fetch(`${engine.baseUrl}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${engine.token}`,
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
  } catch (cause) {
    throw new EngineError("VOLUM cannot reach its engine.", {
      technical: String(cause),
      suggestions: ["Restart VOLUM."],
    });
  }

  if (!response.ok) {
    throw toEngineError(response.status, await readBody(response));
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

/**
 * Read a server-sent event stream, calling back with each parsed payload.
 *
 * Every frame carries the whole record, so a late reader or a dropped frame
 * never leaves the caller holding a partial state.
 */
export async function stream<T>(
  path: string,
  onEvent: (value: T) => void,
  signal?: AbortSignal,
): Promise<void> {
  const engine = await engineInfo();
  const response = await fetch(`${engine.baseUrl}${path}`, {
    headers: { Authorization: `Bearer ${engine.token}` },
    signal,
  });
  if (!response.ok) throw toEngineError(response.status, await readBody(response));
  if (!response.body) throw new EngineError("The engine sent no stream.");

  const reader = response.body.getReader();
  const decoder = new SseDecoder();
  const utf8 = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const payload of decoder.push(utf8.decode(value, { stream: true }))) {
        onEvent(JSON.parse(payload) as T);
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/** A URL for an artifact, with the token attached — for `fetch`, not for `src`. */
export async function artifact(jobId: string, name: string): Promise<Response> {
  const engine = await engineInfo();
  const response = await fetch(`${engine.baseUrl}/api/jobs/${jobId}/artifacts/${name}`, {
    headers: { Authorization: `Bearer ${engine.token}` },
  });
  if (!response.ok) throw toEngineError(response.status, await readBody(response));
  return response;
}

export const api = {
  doctor: (deep = true) => call<DoctorReport>(`/api/system/doctor?deep=${deep}`),

  models: () => call<ModelEntry[]>("/api/models"),
  model: (id: string) => call<ModelEntry>(`/api/models/${id}`),
  installModel: (id: string, body: { hfToken?: string; allowMarginal?: boolean } = {}) =>
    call<InstallTask>(`/api/models/${id}/install`, {
      method: "POST",
      body: JSON.stringify({
        hf_token: body.hfToken ?? null,
        allow_marginal: body.allowMarginal ?? null,
      }),
    }),
  removeModel: (id: string) => call<{ removed: boolean }>(`/api/models/${id}`, { method: "DELETE" }),
  installEvents: (id: string, onEvent: (task: InstallTask) => void, signal?: AbortSignal) =>
    stream<InstallTask>(`/api/models/${id}/install/events`, onEvent, signal),

  jobs: (limit?: number) => call<JobRecord[]>(`/api/jobs${limit ? `?limit=${limit}` : ""}`),
  job: (id: string) => call<JobRecord>(`/api/jobs/${id}`),
  createJob: (request: JobRequest) =>
    call<JobSubmission>("/api/jobs", { method: "POST", body: JSON.stringify(request) }),
  cancelJob: (id: string) => call<JobRecord>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  deleteJob: (id: string) => call<{ removed: boolean }>(`/api/jobs/${id}`, { method: "DELETE" }),
  jobEvents: (id: string, onEvent: (job: JobRecord) => void, signal?: AbortSignal) =>
    stream<JobRecord>(`/api/jobs/${id}/events`, onEvent, signal),

  settings: () => call<SettingsView>("/api/settings"),
  updateSettings: (patch: SettingsPatch) =>
    call<SettingsView>("/api/settings", { method: "PATCH", body: JSON.stringify(patch) }),
};

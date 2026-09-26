/**
 * A fetch client for the instances router, wherever a host mounted it.
 *
 * The host passes the router's base URL (`/api/instances`) and, if it needs
 * one, its own `fetch` — one that adds an auth header, say. Failures throw an
 * {@link InstancesApiError} carrying the server's `detail`, which is written
 * for a person: "Worktree is required", not "422".
 */

import type {
  InstanceHealth,
  InstanceListing,
  InstanceLogs,
  InstanceView,
  LaunchRequest,
  TemplateInfo,
} from './types';

export class InstancesApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'InstancesApiError';
    this.status = status;
  }
}

export interface InstancesClient {
  list(project?: string): Promise<InstanceListing>;
  templates(): Promise<TemplateInfo[]>;
  launch(request: LaunchRequest): Promise<InstanceView>;
  get(id: string): Promise<InstanceView>;
  health(id: string): Promise<InstanceHealth>;
  logs(id: string, tail?: number): Promise<InstanceLogs>;
  stop(id: string): Promise<void>;
}

async function failure(response: Response): Promise<InstancesApiError> {
  const text = await response.text();
  let detail = text;
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed === 'object' && parsed !== null && 'detail' in parsed) {
      const value = (parsed as { detail: unknown }).detail;
      detail = typeof value === 'string' ? value : JSON.stringify(value);
    }
  } catch {
    /* silent-ok: a non-JSON body is already the message; `detail` holds it */
  }
  return new InstancesApiError(response.status, detail || `HTTP ${response.status}`);
}

export function createInstancesClient(
  baseUrl: string,
  fetchImpl: typeof fetch = (...args) => fetch(...args),
): InstancesClient {
  const base = baseUrl.replace(/\/$/, '');
  const id = (value: string): string => encodeURIComponent(value);

  async function call<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetchImpl(`${base}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
    if (!response.ok) throw await failure(response);
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  return {
    list: (project) => call(project ? `?project=${encodeURIComponent(project)}` : ''),
    templates: () => call('/templates'),
    launch: (request) => call('', { method: 'POST', body: JSON.stringify(request) }),
    get: (value) => call(`/${id(value)}`),
    health: (value) => call(`/${id(value)}/health`),
    logs: (value, tail = 200) => call(`/${id(value)}/logs?tail=${tail}`),
    stop: (value) => call(`/${id(value)}`, { method: 'DELETE' }),
  };
}

/**
 * The wire shapes of `lore_eden.instances`' HTTP router.
 *
 * Mirrors `python/lore_eden/instances/models.py` and `templates.py`. The two
 * are kept in step by hand; `tests/instances.test.tsx` pins the fields the UI
 * reads so a rename on one side fails a test rather than rendering blanks.
 */

export type InstanceKind = 'server' | 'client';
export type InstanceRole = 'main' | 'branch';
export type InstanceState = 'starting' | 'ready' | 'stalled' | 'exited';

export interface InstanceView {
  id: string;
  project: string;
  name: string;
  kind: InstanceKind;
  role: InstanceRole;
  template: string | null;
  pid: number;
  managed: boolean;
  host: string;
  port: number;
  url: string;
  health_path: string | null;
  ready_timeout_seconds: number;
  cwd: string | null;
  log_path: string | null;
  data_dir: string | null;
  target_instance_id: string | null;
  labels: Record<string, string>;
  started_at: string;
  ready_at: string | null;
  exit_code: number | null;
  last_error: string | null;
  state: InstanceState;
}

export interface UnreadableRecord {
  path: string;
  error: string;
}

export interface InstanceListing {
  instances: InstanceView[];
  unreadable: UnreadableRecord[];
}

export interface TemplateParam {
  key: string;
  label: string;
  description: string;
  required: boolean;
  default: string | null;
  choices: string[] | null;
}

export interface TemplateInfo {
  name: string;
  kind: InstanceKind;
  description: string;
  params: TemplateParam[];
}

export interface LaunchRequest {
  template: string;
  name?: string;
  params: Record<string, string>;
}

export interface InstanceHealth {
  ok: boolean;
  latency_ms: number;
  error: string;
  status_code: number | null;
}

export interface InstanceLogs {
  path: string | null;
  lines: string[];
  truncated: boolean;
}

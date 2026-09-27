import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  createInstancesClient,
  InstancesApiError,
  InstancesPanel,
  LaunchForm,
} from '../src/instances';
import type { InstancesClient, InstanceListing, InstanceView, TemplateInfo } from '../src/instances';

const instance = (overrides: Partial<InstanceView> = {}): InstanceView => ({
  id: 'demo-api-abc123',
  project: 'demo',
  name: 'api',
  kind: 'server',
  role: 'branch',
  template: 'api',
  pid: 42,
  managed: true,
  host: '127.0.0.1',
  port: 8101,
  url: 'http://127.0.0.1:8101',
  health_path: '/health',
  ready_timeout_seconds: 120,
  cwd: '/w',
  log_path: '/r/logs/demo-api-abc123.log',
  data_dir: '/r/data/demo-api-abc123',
  target_instance_id: null,
  labels: { branch: 'feat-x' },
  started_at: '2026-09-26T00:00:00Z',
  ready_at: '2026-09-26T00:00:05Z',
  exit_code: null,
  last_error: null,
  state: 'ready',
  ...overrides,
});

const TEMPLATES: TemplateInfo[] = [
  {
    name: 'server',
    kind: 'server',
    description: 'A branch server',
    params: [
      {
        key: 'worktree',
        label: 'Worktree',
        description: '',
        required: true,
        default: null,
        choices: ['/w/main', '/w/feat-x'],
      },
      { key: 'note', label: 'Note', description: '', required: false, default: null, choices: null },
    ],
  },
];

function fakeClient(listing: InstanceListing, overrides: Partial<InstancesClient> = {}): InstancesClient {
  return {
    list: vi.fn().mockResolvedValue(listing),
    templates: vi.fn().mockResolvedValue(TEMPLATES),
    launch: vi.fn().mockResolvedValue(instance()),
    get: vi.fn(),
    health: vi.fn(),
    logs: vi.fn().mockResolvedValue({ path: '/x', lines: ['booted'], truncated: false }),
    stop: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

describe('createInstancesClient', () => {
  it('turns a failure into an error carrying the server detail', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Worktree is required' }), { status: 422 }),
    );
    const client = createInstancesClient('/api/instances/', fetchImpl);
    const error = await client.launch({ template: 'server', params: {} }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(InstancesApiError);
    expect((error as InstancesApiError).status).toBe(422);
    expect((error as InstancesApiError).message).toBe('Worktree is required');
    expect(fetchImpl).toHaveBeenCalledWith('/api/instances', expect.objectContaining({ method: 'POST' }));
  });

  it('encodes an id before putting it in the path', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    await createInstancesClient('/api/instances', fetchImpl).stop('a/b');
    expect(fetchImpl.mock.calls[0][0]).toBe('/api/instances/a%2Fb');
  });
});

describe('InstancesPanel', () => {
  it('shows a skeleton, not the empty state, before the first load', () => {
    const client = fakeClient({ instances: [], unreadable: [] }, { list: vi.fn(() => new Promise<InstanceListing>(() => {})) });
    render(<InstancesPanel client={client} />);
    expect(screen.getByLabelText('Loading instances')).toBeInTheDocument();
    expect(screen.queryByText(/Nothing is running/)).not.toBeInTheDocument();
  });

  it('says how to get an instance when there are none', async () => {
    render(<InstancesPanel client={fakeClient({ instances: [], unreadable: [] })} />);
    expect(await screen.findByText(/Nothing is running/)).toBeInTheDocument();
  });

  it('reports a failed load instead of claiming nothing is running', async () => {
    const client = fakeClient({ instances: [], unreadable: [] }, {
      list: vi.fn().mockRejectedValue(new Error('connection refused')),
    });
    render(<InstancesPanel client={client} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('connection refused');
    expect(screen.queryByText(/Nothing is running/)).not.toBeInTheDocument();
  });

  it('shows an exited instance with its error, and offers dismiss rather than stop', async () => {
    const crashed = instance({ state: 'exited', last_error: 'exited with code 3', exit_code: 3 });
    render(<InstancesPanel client={fakeClient({ instances: [crashed], unreadable: [] })} />);
    expect(await screen.findByText('exited with code 3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('does not offer to stop an instance that registered itself', async () => {
    const main = instance({ id: 'demo-main', name: 'main', role: 'main', managed: false });
    render(<InstancesPanel client={fakeClient({ instances: [main], unreadable: [] })} />);
    expect(await screen.findByText('Not managed here')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument();
  });

  it('disables stop while it is in flight, so it cannot fire twice', async () => {
    let finish: () => void = () => {};
    const stop = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    render(<InstancesPanel client={fakeClient({ instances: [instance()], unreadable: [] }, { stop })} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Stop' }));
    const busy = await screen.findByRole('button', { name: 'Stopping…' });
    expect(busy).toBeDisabled();
    fireEvent.click(busy);
    expect(stop).toHaveBeenCalledTimes(1);
    await act(async () => finish());
  });

  it('refuses to launch until required parameters are filled, then sends only what was set', async () => {
    const client = fakeClient({ instances: [], unreadable: [] });
    render(<InstancesPanel client={client} />);
    const form = await screen.findByRole('form', { name: 'Launch an instance' });
    const launch = within(form).getByRole('button', { name: 'Launch' });
    expect(launch).toBeDisabled();
    fireEvent.change(within(form).getByLabelText('Worktree (required)'), { target: { value: '/w/feat-x' } });
    fireEvent.click(launch);
    await waitFor(() =>
      expect(client.launch).toHaveBeenCalledWith({
        template: 'server',
        name: undefined,
        params: { worktree: '/w/feat-x' },
      }),
    );
  });

  it('shows a launch failure inline and hands it to the host', async () => {
    const onError = vi.fn();
    const client = fakeClient({ instances: [], unreadable: [] }, {
      launch: vi.fn().mockRejectedValue(new InstancesApiError(503, 'no free port')),
    });
    render(<InstancesPanel client={client} onError={onError} />);
    const form = await screen.findByRole('form', { name: 'Launch an instance' });
    fireEvent.change(within(form).getByLabelText('Worktree (required)'), { target: { value: '/w/main' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Launch' }));
    expect(await screen.findByText('Launch failed: no free port')).toBeInTheDocument();
    expect(onError).toHaveBeenCalledWith('Launch failed', expect.any(InstancesApiError));
  });

  it('names the server a client points at', async () => {
    const server = instance();
    const client = instance({ id: 'demo-ui-1', name: 'ui', kind: 'client', target_instance_id: server.id });
    render(<InstancesPanel client={fakeClient({ instances: [server, client], unreadable: [] })} />);
    expect(await screen.findByText(/branch client → api/)).toBeInTheDocument();
  });

  it('opens the log from a keyboard-reachable toggle', async () => {
    render(<InstancesPanel client={fakeClient({ instances: [instance()], unreadable: [] })} />);
    const toggle = await screen.findByRole('button', { name: 'Log' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(await screen.findByLabelText('Log for demo-api-abc123')).toHaveTextContent('booted');
    expect(screen.getByRole('button', { name: 'Hide log' })).toHaveAttribute('aria-expanded', 'true');
  });
});

describe('LaunchForm', () => {
  it('keeps what was typed when the host hands it a fresh templates list', () => {
    // The panel re-fetches templates as it polls; every fetch is a new array
    // of new objects. Typed values must survive that, not reset to defaults.
    const onLaunch = vi.fn();
    const { rerender } = render(<LaunchForm templates={TEMPLATES} launching={false} onLaunch={onLaunch} />);
    fireEvent.change(screen.getByLabelText('Worktree (required)'), { target: { value: '/w/feat-x' } });
    rerender(<LaunchForm templates={structuredClone(TEMPLATES)} launching={false} onLaunch={onLaunch} />);
    expect(screen.getByLabelText('Worktree (required)')).toHaveValue('/w/feat-x');
    fireEvent.click(screen.getByRole('button', { name: 'Launch' }));
    expect(onLaunch).toHaveBeenCalledWith({ template: 'server', name: undefined, params: { worktree: '/w/feat-x' } });
  });
});

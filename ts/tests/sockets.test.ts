import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DEFAULT_POLICY,
  JsonSocket,
  THINKING_POLICY,
  createWebSocketThinkingTransport,
  type SocketStatus,
} from '../src/sockets';

/**
 * A fake websocket, because the whole point is the reconnect lifecycle and a
 * real server cannot be made to drop a connection on cue.
 */
class FakeSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static instances: FakeSocket[] = [];

  readyState = FakeSocket.CONNECTING;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closedByCaller = false;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }

  accept(): void {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  deliver(payload: unknown): void {
    this.onmessage?.({ data: typeof payload === 'string' ? payload : JSON.stringify(payload) });
  }

  drop(): void {
    this.readyState = 3;
    this.onclose?.();
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closedByCaller = true;
    this.readyState = 3;
  }
}

const factory = (url: string) => new FakeSocket(url) as unknown as WebSocket;

function socket(overrides: Record<string, unknown> = {}) {
  const statuses: SocketStatus[] = [];
  const messages: unknown[] = [];
  const instance = new JsonSocket({
    url: 'wss://host/feed',
    factory,
    handlers: {
      onStatus: (status) => statuses.push(status),
      onMessage: (message) => messages.push(message),
    },
    ...overrides,
  });
  return { instance, statuses, messages };
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.useFakeTimers();
  vi.stubGlobal('WebSocket', FakeSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('ReconnectingSocket', () => {
  it('reports connecting then open', () => {
    const { instance, statuses } = socket();
    instance.open();
    expect(statuses).toEqual(['connecting']);
    FakeSocket.instances[0].accept();
    expect(statuses).toEqual(['connecting', 'open']);
  });

  it('hands parsed frames to the caller', () => {
    const { instance, messages } = socket();
    instance.open();
    FakeSocket.instances[0].accept();
    FakeSocket.instances[0].deliver({ hello: 'world' });
    expect(messages).toEqual([{ hello: 'world' }]);
  });

  it('survives a frame it cannot parse', () => {
    // The server's problem, not a reason to tear down a working connection.
    const { instance, messages, statuses } = socket();
    instance.open();
    FakeSocket.instances[0].accept();
    FakeSocket.instances[0].deliver('{not json');
    expect(messages).toEqual([]);
    expect(statuses).toEqual(['connecting', 'open']);
  });

  it('ignores a non-string payload', () => {
    const { instance, messages } = socket();
    instance.open();
    FakeSocket.instances[0].accept();
    FakeSocket.instances[0].onmessage?.({ data: new ArrayBuffer(4) });
    expect(messages).toEqual([]);
  });

  it('reports closed rather than connecting through the backoff', () => {
    // A caller showing a spinner through the wait shows nothing useful, and one
    // that can fall back to polling must be told to start now.
    const { instance, statuses } = socket();
    instance.open();
    FakeSocket.instances[0].accept();
    FakeSocket.instances[0].drop();
    expect(statuses).toEqual(['connecting', 'open', 'closed']);
  });

  it('reconnects after a drop', () => {
    const { instance } = socket();
    instance.open();
    FakeSocket.instances[0].accept();
    FakeSocket.instances[0].drop();
    expect(FakeSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(DEFAULT_POLICY.baseDelayMs);
    expect(FakeSocket.instances).toHaveLength(2);
  });

  it('backs off exponentially and stops at the ceiling', () => {
    const { instance } = socket({ policy: { baseDelayMs: 100, maxDelayMs: 400 } });
    instance.open();

    const delays: number[] = [];
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const before = FakeSocket.instances.length;
      FakeSocket.instances[before - 1].drop();
      // Find the delay by advancing one millisecond at a time would be slow;
      // advance the expected amount and assert a socket appeared.
      const expected = Math.min(100 * 2 ** attempt, 400);
      vi.advanceTimersByTime(expected - 1);
      expect(FakeSocket.instances).toHaveLength(before);
      vi.advanceTimersByTime(1);
      expect(FakeSocket.instances).toHaveLength(before + 1);
      delays.push(expected);
    }
    expect(delays).toEqual([100, 200, 400, 400, 400]);
  });

  it('resets the backoff when a connection opens, not when data arrives', () => {
    // A backoff that only reset on data would keep growing across quiet
    // reconnects, so a socket nobody talks on ends up on the ceiling.
    const { instance } = socket({ policy: { baseDelayMs: 100, maxDelayMs: 10_000 } });
    instance.open();
    FakeSocket.instances[0].drop();
    vi.advanceTimersByTime(100);
    expect(instance.retryCount).toBe(1);

    FakeSocket.instances[1].accept();
    expect(instance.retryCount).toBe(0);

    FakeSocket.instances[1].drop();
    vi.advanceTimersByTime(99);
    expect(FakeSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances).toHaveLength(3);
  });

  it('a close we asked for is not reported as a drop', () => {
    // Otherwise the caller reconnects, or falls back to polling, on its way
    // out of the page.
    const { instance, statuses } = socket();
    instance.open();
    FakeSocket.instances[0].accept();
    instance.close();
    expect(statuses).toEqual(['connecting', 'open']);
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it('a pending reconnect is cancelled by close', () => {
    const { instance } = socket();
    instance.open();
    FakeSocket.instances[0].drop();
    instance.close();
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it('open after close does nothing', () => {
    const { instance } = socket();
    instance.open();
    instance.close();
    instance.open();
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it('sends only on an open connection', () => {
    const { instance } = socket();
    instance.open();
    expect(instance.send({ a: 1 })).toBe(false);
    FakeSocket.instances[0].accept();
    expect(instance.send({ a: 1 })).toBe(true);
    expect(FakeSocket.instances[0].sent).toEqual(['{"a":1}']);
  });

  it('reports its own status', () => {
    const { instance } = socket();
    expect(instance.status).toBe('closed');
    instance.open();
    expect(instance.status).toBe('connecting');
    FakeSocket.instances[0].accept();
    expect(instance.status).toBe('open');
  });
});

describe('the websocket thinking transport', () => {
  const transport = (overrides: Record<string, unknown> = {}) =>
    createWebSocketThinkingTransport({
      url: (turnId) => `wss://host/turns/${turnId}`,
      factory,
      ...overrides,
    });

  it('opens a socket for the turn it was asked about', () => {
    transport().subscribe('turn-7', () => undefined);
    expect(FakeSocket.instances[0].url).toBe('wss://host/turns/turn-7');
  });

  it('delivers frames the default parser understands', () => {
    const frames: unknown[] = [];
    transport().subscribe('t', (frame) => frames.push(frame));
    FakeSocket.instances[0].accept();
    FakeSocket.instances[0].deliver({ content: 'thinking', answer: '', isStreaming: true });
    expect(frames).toEqual([
      { content: 'thinking', answer: '', activity: '', isStreaming: true },
    ]);
  });

  it('ignores a frame that names none of the fields', () => {
    // A heartbeat or an ack. Returning a blank frame for it would erase a
    // transcript that had content.
    const frames: unknown[] = [];
    transport().subscribe('t', (frame) => frames.push(frame));
    FakeSocket.instances[0].accept();
    FakeSocket.instances[0].deliver({ type: 'heartbeat' });
    expect(frames).toEqual([]);
  });

  it('accepts a host parser, since a server names its fields otherwise', () => {
    const frames: unknown[] = [];
    transport({
      parse: (message: unknown) => ({
        content: (message as { reasoning: string }).reasoning,
        answer: '',
        activity: '',
        isStreaming: true,
      }),
    }).subscribe('t', (frame) => frames.push(frame));
    FakeSocket.instances[0].accept();
    FakeSocket.instances[0].deliver({ reasoning: 'from a different server' });
    expect(frames).toEqual([
      { content: 'from a different server', answer: '', activity: '', isStreaming: true },
    ]);
  });

  it('unsubscribing closes that turn’s socket', () => {
    // The interface promises the teardown belongs to the subscription, so a
    // switched turn must not leak a socket per turn the user glanced at.
    const unsubscribe = transport().subscribe('t', () => undefined);
    FakeSocket.instances[0].accept();
    unsubscribe();
    expect(FakeSocket.instances[0].closedByCaller).toBe(true);
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it('reconnects mid-turn on a drop', () => {
    transport().subscribe('t', () => undefined);
    FakeSocket.instances[0].accept();
    FakeSocket.instances[0].drop();
    vi.advanceTimersByTime(THINKING_POLICY.baseDelayMs);
    expect(FakeSocket.instances).toHaveLength(2);
  });

  it('uses a shorter ceiling than a general socket', () => {
    // A two-minute turn cannot afford a thirty-second gap in its own
    // transcript.
    expect(THINKING_POLICY.maxDelayMs).toBeLessThan(DEFAULT_POLICY.maxDelayMs);
  });

  it('reports status per turn when asked', () => {
    const seen: [string, SocketStatus][] = [];
    transport({ onStatus: (turnId: string, status: SocketStatus) => seen.push([turnId, status]) })
      .subscribe('turn-9', () => undefined);
    FakeSocket.instances[0].accept();
    expect(seen).toEqual([
      ['turn-9', 'connecting'],
      ['turn-9', 'open'],
    ]);
  });

  it('opens one socket per turn', () => {
    const t = transport();
    t.subscribe('a', () => undefined);
    t.subscribe('b', () => undefined);
    expect(FakeSocket.instances.map((s) => s.url)).toEqual([
      'wss://host/turns/a',
      'wss://host/turns/b',
    ]);
  });
});

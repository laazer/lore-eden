import { describe, expect, it, vi } from 'vitest';

import { openEventStream, type EventStreamOptions } from '../src/sockets';

/**
 * A fake EventSource, because the cases that matter — an error after `done`,
 * an event already queued when the stream is disposed — are orderings a real
 * server cannot be made to produce on cue.
 *
 * Built on EventTarget so `addEventListener` behaves as the browser's does,
 * including a transport error dispatched under the name `error` to every
 * listener for that name before `onerror` runs.
 */
class FakeEventSource extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  readyState = FakeEventSource.CONNECTING;
  onerror: ((event: Event) => void) | null = null;
  closeCalls = 0;

  constructor(
    readonly url: string,
    readonly init?: EventSourceInit,
  ) {
    super();
  }

  /** A named event from the server. Delivered whether or not we closed: that is the point. */
  emit(name: string, data: unknown): void {
    const text = typeof data === 'string' ? data : JSON.stringify(data);
    this.dispatchEvent(new MessageEvent(name, { data: text }));
  }

  /** The browser's transport error: no data, `readyState` set as the browser would. */
  fail(readyState: number = FakeEventSource.CONNECTING): void {
    if (this.readyState !== FakeEventSource.CLOSED) this.readyState = readyState;
    const event = new Event('error');
    this.dispatchEvent(event);
    this.onerror?.(event);
  }

  close(): void {
    this.closeCalls += 1;
    this.readyState = FakeEventSource.CLOSED;
  }
}

type Progress = { log: { line: string } };
type Terminal = { done: { output_file: string | null }; error: { message: string } };

function open(overrides: Partial<EventStreamOptions<Progress, Terminal>> = {}) {
  const calls: Array<[string, unknown]> = [];
  let source: FakeEventSource | null = null;
  const dispose = openEventStream<Progress, Terminal>({
    url: '/any/path?cmd=x',
    progress: { log: (data) => calls.push(['log', data]) },
    terminal: {
      done: (data) => calls.push(['done', data]),
      error: (data) => calls.push(['error', data]),
    },
    onTransportError: () => calls.push(['transport', null]),
    factory: (url, init) => {
      source = new FakeEventSource(url, init);
      return source as unknown as EventSource;
    },
    ...overrides,
  });
  return { calls, dispose, source: source as unknown as FakeEventSource };
}

describe('openEventStream', () => {
  it('opens the URL the caller gave, unchanged', () => {
    const { source } = open({ init: { withCredentials: true } });
    expect(source.url).toBe('/any/path?cmd=x');
    expect(source.init).toEqual({ withCredentials: true });
  });

  it('routes the caller’s own event names, parsed', () => {
    const { source, calls } = open();
    source.emit('log', { line: 'one' });
    source.emit('log', { line: 'two' });
    source.emit('unrelated', { line: 'ignored' });
    expect(calls).toEqual([
      ['log', { line: 'one' }],
      ['log', { line: 'two' }],
    ]);
  });

  it('hands a non-JSON payload over verbatim instead of throwing', () => {
    const { source, calls } = open();
    source.emit('log', 'plain text, not {json');
    source.emit('done', 'finished!');
    expect(calls).toEqual([
      ['log', 'plain text, not {json'],
      ['done', 'finished!'],
    ]);
  });

  it('fires a terminal event once and closes the stream', () => {
    const { source, calls } = open();
    source.emit('done', { output_file: 'a.glb' });
    source.emit('done', { output_file: 'b.glb' });
    source.emit('error', { message: 'late' });
    source.emit('log', { line: 'queued behind done' });
    expect(calls).toEqual([['done', { output_file: 'a.glb' }]]);
    expect(source.closeCalls).toBe(1);
  });

  it('discards a transport error that arrives after done', () => {
    // The server closes the connection it just finished on, and the browser
    // reports that as an error. A run that succeeded must not read as failed.
    const { source, calls } = open();
    source.emit('done', { output_file: null });
    source.fail();
    source.fail(FakeEventSource.CLOSED);
    expect(calls).toEqual([['done', { output_file: null }]]);
  });

  it('reports a transport error before any terminal event, once, and closes', () => {
    const { source, calls } = open();
    source.emit('log', { line: 'working' });
    source.fail();
    source.fail();
    expect(calls).toEqual([
      ['log', { line: 'working' }],
      ['transport', null],
    ]);
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
  });

  it('suppresses events already in flight when disposed', () => {
    // A component unmounts and closes the stream; events the browser queued
    // before the close must not call back into a dead tree. Defect in the
    // source: its disposer set `finished`, which only the terminal events
    // checked, so a queued `log` still reached `onLog` after disposal.
    const { source, calls, dispose } = open();
    source.emit('log', { line: 'before' });
    dispose();
    source.emit('log', { line: 'in flight' });
    source.emit('done', { output_file: 'x.glb' });
    source.emit('error', { message: 'in flight' });
    source.fail();
    expect(calls).toEqual([['log', { line: 'before' }]]);
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
  });

  it('can be disposed twice, or after it finished', () => {
    const { source, dispose } = open();
    source.emit('done', { output_file: null });
    expect(() => {
      dispose();
      dispose();
    }).not.toThrow();
  });

  it('does not mistake a transport error for a terminal event named error', () => {
    // Defect in the source: its `error` listener also received the browser's
    // transport error, which has no data, so JSON.parse(undefined) threw and a
    // dropped connection was reported as "Stream error" — and `onerror`, which
    // said "Connection lost", was then ignored as already finished.
    const { source, calls } = open();
    source.fail();
    expect(calls).toEqual([['transport', null]]);
  });

  it('closes the stream even when the terminal handler throws', () => {
    // Defect in the source: it closed *after* calling the handler, so a
    // handler that threw left the EventSource open, and the browser kept
    // reconnecting to a run that had already finished.
    const { source } = open({
      terminal: {
        done: () => {
          throw new Error('handler bug');
        },
      },
    });
    // EventTarget reports a listener's exception rather than rethrowing it,
    // as the browser does, so the evidence is the state it leaves behind.
    const reported = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    source.emit('done', {});
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
    expect(source.closeCalls).toBe(1);
    reported.mockRestore();
  });

  it('lets the browser reconnect when asked, failing only when it gives up', () => {
    const onReconnecting = vi.fn();
    const { source, calls } = open({ reconnect: true, onReconnecting });
    source.fail(FakeEventSource.CONNECTING);
    expect(onReconnecting).toHaveBeenCalledTimes(1);
    expect(calls).toEqual([]);
    expect(source.closeCalls).toBe(0);

    source.fail(FakeEventSource.CLOSED);
    expect(calls).toEqual([['transport', null]]);
  });

  it('refuses a name declared both progress and terminal', () => {
    expect(() =>
      openEventStream<{ x: unknown }, { x: unknown }>({
        url: '/s',
        progress: { x: () => undefined },
        terminal: { x: () => undefined },
        factory: (url) => new FakeEventSource(url) as unknown as EventSource,
      }),
    ).toThrow(/both progress and terminal/);
  });

  it('uses the global EventSource when no factory is given', () => {
    const created: string[] = [];
    vi.stubGlobal(
      'EventSource',
      class extends FakeEventSource {
        constructor(url: string, init?: EventSourceInit) {
          super(url, init);
          created.push(url);
        }
      },
    );
    try {
      openEventStream({ url: '/global' })();
      expect(created).toEqual(['/global']);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

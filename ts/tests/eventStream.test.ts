import { describe, expect, it, vi } from 'vitest';

import { openEventStream, type EventStreamOptions } from '../src/sockets';

/**
 * A fake EventSource, because the cases that matter — an error after `done`,
 * an event already queued when the stream is disposed — are orderings a real
 * server cannot be made to produce on cue.
 *
 * Built on EventTarget so `addEventListener` behaves as the browser's does,
 * including a transport error dispatched under the name `error` to every
 * listener for that name before `onerror` runs. Like the browser, it drops
 * events and errors once closed: the HTML spec dispatches each queued event
 * only "if readyState is other than CLOSED", and aborts a reconnect when
 * CLOSED. `honoursClose = false` turns that off to model a polyfill that keeps
 * dispatching, which is the only way to reach the stream's own `settled`
 * guards — defence in depth, not a browser behaviour.
 */
class FakeEventSource extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  readyState = FakeEventSource.CONNECTING;
  onerror: ((event: Event) => void) | null = null;
  closeCalls = 0;
  honoursClose = true;

  constructor(
    readonly url: string,
    readonly init?: EventSourceInit,
  ) {
    super();
  }

  /** A named event from the server, dropped once closed unless `honoursClose` is off. */
  emit(name: string, data: unknown): void {
    if (this.dropsAfterClose()) return;
    const text = typeof data === 'string' ? data : JSON.stringify(data);
    this.dispatchEvent(new MessageEvent(name, { data: text }));
  }

  /** The browser's transport error: no data, `readyState` set as the browser would. */
  fail(readyState: number = FakeEventSource.CONNECTING): void {
    if (this.dropsAfterClose()) return;
    if (this.readyState !== FakeEventSource.CLOSED) this.readyState = readyState;
    const event = new Event('error');
    this.dispatchEvent(event);
    this.onerror?.(event);
  }

  close(): void {
    this.closeCalls += 1;
    this.readyState = FakeEventSource.CLOSED;
  }

  private dropsAfterClose(): boolean {
    return this.honoursClose && this.readyState === FakeEventSource.CLOSED;
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

  it('hands JSON that is not an object over verbatim, as text', () => {
    const { source, calls } = open();
    for (const raw of ['42', 'true', 'null', '[1]']) source.emit('log', raw);
    expect(calls).toEqual([
      ['log', '42'],
      ['log', 'true'],
      ['log', 'null'],
      ['log', '[1]'],
    ]);
  });

  it('fires a terminal event once and closes the stream', () => {
    const { source, calls } = open();
    source.emit('done', { output_file: 'a.glb' });
    expect(calls).toEqual([['done', { output_file: 'a.glb' }]]);
    expect(source.closeCalls).toBe(1);
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
  });

  it('ignores whatever a polyfill still dispatches after a terminal event', () => {
    // Defence in depth: a browser drops these itself once closed. The fake is
    // told not to, so the stream's own `settled` guard is what is under test.
    const { source, calls } = open();
    source.honoursClose = false;
    source.emit('done', { output_file: 'a.glb' });
    source.emit('done', { output_file: 'b.glb' });
    source.emit('error', { message: 'late' });
    source.emit('log', { line: 'queued behind done' });
    expect(calls).toEqual([['done', { output_file: 'a.glb' }]]);
    expect(source.closeCalls).toBe(1);
  });

  it('discards a transport error that arrives after done', () => {
    // The server closes the connection it just finished on. Closing on `done`
    // is what stops the browser reporting that as an error; the `settled`
    // guard repeats it for a polyfill. Either way a success must not read as
    // failed.
    for (const honoursClose of [true, false]) {
      const { source, calls } = open();
      source.honoursClose = honoursClose;
      source.emit('done', { output_file: null });
      source.fail();
      source.fail(FakeEventSource.CLOSED);
      expect(calls).toEqual([['done', { output_file: null }]]);
    }
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

  it('suppresses events a polyfill still dispatches after disposal', () => {
    // A component unmounts and closes the stream. A browser then delivers
    // nothing more; the `settled` guard is defence in depth for a polyfill
    // that keeps dispatching, so the fake is told to keep dispatching.
    const { source, calls, dispose } = open();
    source.honoursClose = false;
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

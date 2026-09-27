/**
 * A server-sent event stream that runs once and finishes: a long-running
 * command writing progress, then one terminal event.
 *
 *     const dispose = openEventStream<{ log: { line: string } }, { done: { file: string } }>({
 *       url: `/api/run/stream?${params}`,
 *       progress: { log: (data) => append(typeof data === 'string' ? data : data.line) },
 *       terminal: { done: (data) => finish(data) },
 *       onTransportError: () => fail('Connection lost'),
 *     });
 *
 * Extracted from blobert's asset editor (`src/api/runStream.ts`, with the same
 * wiring written again in `useStreamingOutput.ts`). The URL, the event names
 * and the payload shapes were that host's protocol and are the caller's here.
 *
 * ## Why this is not built on ReconnectingSocket
 *
 * Deliberately separate, sharing no policy type. `ReconnectPolicy` is a
 * client-side backoff: the socket decides how long to wait and opens a new
 * connection itself. `EventSource` has no such knob — the browser reconnects on
 * its own, after a delay the *server* sets with the `retry:` field, resuming
 * from `Last-Event-ID`. A shared policy type would be a setting this module
 * accepts and cannot honour. The lifecycle differs too: a socket is meant to
 * stay up indefinitely, while this stream is meant to *end*, and the only
 * reconnect decision left to the client is whether to allow the browser's at
 * all — which is the `reconnect` flag, not a policy.
 *
 * ## Three guarantees
 *
 * - **A terminal event settles the stream exactly once.** Whatever arrives
 *   after it — a second terminal event, a progress event already queued, or
 *   the transport error the browser fires when the server closes the
 *   connection it just finished on — is discarded. That last one is the
 *   reason: without it a run that succeeded is reported as a failure.
 * - **Disposing suppresses events already in flight.** Closing an
 *   `EventSource` does not recall events already queued, so every listener
 *   checks first. A component that unmounted is not called back into.
 * - **A payload that is not JSON arrives verbatim** rather than throwing.
 *   Every consumer needs the same forgiveness, so it lives here.
 */

/** `EventSource.CONNECTING` — local so this module loads where the global does not exist. */
const CONNECTING = 0;

/**
 * A payload as the handler receives it: the caller's declared shape, or the
 * raw text when it was not JSON. The shape is a declaration, not a check —
 * the stream parses and does not validate.
 */
export type StreamPayload<T> = T | string;

export type StreamHandlers<TMap> = {
  [K in keyof TMap]?: (data: StreamPayload<TMap[K]>, event: MessageEvent) => void;
};

export interface EventStreamOptions<TProgress, TTerminal> {
  url: string;
  /** Events that may arrive any number of times while the stream runs. */
  progress?: StreamHandlers<TProgress>;
  /** Events that end the stream. The first to arrive closes it. */
  terminal?: StreamHandlers<TTerminal>;
  /**
   * The connection failed before a terminal event. Terminal: the stream is
   * closed when this runs. Never called after a terminal event or disposal.
   */
  onTransportError?: (event: Event) => void;
  /**
   * Let the browser reconnect a dropped connection instead of treating the
   * drop as terminal. Off by default: a finishing stream whose server does not
   * resume from `Last-Event-ID` replays or restarts on reconnect. When on,
   * `onTransportError` runs only once the browser gives up (a fatal response).
   */
  reconnect?: boolean;
  /** Called on each drop the browser will retry, when `reconnect` is on. */
  onReconnecting?: () => void;
  init?: EventSourceInit;
  /** Injectable so a test can drive a fake without a live server. */
  factory?: (url: string, init?: EventSourceInit) => EventSource;
}

/** A named `error` event from the server carries data; the transport's does not. */
function isServerEvent(event: Event): event is MessageEvent {
  return typeof (event as MessageEvent).data === 'string';
}

function parsePayload(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    // Not JSON: the handler gets the text as the server sent it.
    return raw;
  }
}

/**
 * Open the stream. Returns a disposer that closes it and suppresses every
 * event not yet delivered; calling it more than once is harmless.
 */
export function openEventStream<
  TProgress extends object = Record<string, unknown>,
  TTerminal extends object = Record<string, unknown>,
>(options: EventStreamOptions<TProgress, TTerminal>): () => void {
  const progress = Object.entries(options.progress ?? {}) as Array<[string, Handler | undefined]>;
  const terminal = Object.entries(options.terminal ?? {}) as Array<[string, Handler | undefined]>;
  const clash = progress.find(([name]) => terminal.some(([other]) => other === name));
  if (clash) {
    // One name cannot both end the stream and not end it.
    throw new Error(`event "${clash[0]}" is declared both progress and terminal`);
  }

  const factory = options.factory ?? ((url, init) => new EventSource(url, init));
  const source = factory(options.url, options.init);
  let settled = false;

  const settle = (run: () => void) => {
    if (settled) return;
    settled = true;
    // Closed before the handler runs, so a handler that throws cannot leave
    // the browser reconnecting to a stream that already finished.
    source.close();
    run();
  };

  for (const [name, handler] of progress) {
    source.addEventListener(name, (event) => {
      if (settled || !isServerEvent(event)) return;
      handler?.(parsePayload(event.data), event);
    });
  }

  for (const [name, handler] of terminal) {
    source.addEventListener(name, (event) => {
      // A host may name a terminal event `error`, and the browser dispatches
      // its transport error under the same name. That one carries no data and
      // belongs to `onerror` below; parsing it here reports a dropped
      // connection as a malformed server error.
      if (!isServerEvent(event)) return;
      settle(() => handler?.(parsePayload(event.data), event));
    });
  }

  source.onerror = (event) => {
    if (settled) return;
    if (options.reconnect && source.readyState === CONNECTING) {
      options.onReconnecting?.();
      return;
    }
    settle(() => options.onTransportError?.(event));
  };

  return () => {
    settled = true;
    source.close();
  };
}

type Handler = (data: unknown, event: MessageEvent) => void;

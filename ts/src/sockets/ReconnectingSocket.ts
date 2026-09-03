/**
 * The reconnect lifecycle every long-lived socket shares.
 *
 * Only the lifecycle: opening, backing off, and the difference between a drop
 * and a close we asked for. Framing, ordering and what a message *means* stay
 * with the caller — those are the parts that actually differ between a queue
 * feed and a chat turn's thinking channel, and folding them in here would
 * trade one duplication for a switch statement.
 *
 * Extracted from loregarden, where two sockets are built on it. It arrives
 * because `chat/thinking.ts` deliberately defines a transport *interface* and
 * ships no implementation — correct, since a websocket URL cannot travel — and
 * a host filling that seam needs exactly this and would otherwise write it.
 */

/**
 * Deliberately three states, not four.
 *
 * An earlier client had a separate `error` that behaved identically to
 * `closed` for every consumer, and an `error` that never cleared was how a
 * dashboard got stuck. A socket is either trying, up, or down.
 */
export type SocketStatus = 'connecting' | 'open' | 'closed';

/**
 * How long to wait before each retry.
 *
 * Both bounds are per-socket: a turn that lasts two minutes cannot afford a
 * queue socket's thirty-second ceiling.
 */
export interface ReconnectPolicy {
  /** First reconnect delay, doubling from here. */
  baseDelayMs: number;
  /** Ceiling for the backoff. */
  maxDelayMs: number;
}

export const DEFAULT_POLICY: ReconnectPolicy = { baseDelayMs: 500, maxDelayMs: 10_000 };

/** The one thing every socket's handlers must offer: where state goes. */
export interface SocketStatusHandler {
  onStatus: (status: SocketStatus) => void;
}

export interface ReconnectingSocketOptions<THandlers extends SocketStatusHandler> {
  url: string;
  handlers: THandlers;
  policy?: ReconnectPolicy;
  /** Injectable so a test can drive a fake without a live server. */
  factory?: (url: string) => WebSocket;
}

export abstract class ReconnectingSocket<THandlers extends SocketStatusHandler> {
  private socket: WebSocket | null = null;
  private stopped = false;
  private attempts = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly url: string;
  private readonly factory: (url: string) => WebSocket;
  private readonly policy: ReconnectPolicy;
  protected readonly handlers: THandlers;

  constructor(options: ReconnectingSocketOptions<THandlers>) {
    // An options object rather than the source's positional trio. Every
    // subclass there wired an identical constructor to pass a policy up, so
    // the ceremony was the same function written twice; here the policy is
    // just another option with a default.
    this.url = options.url;
    this.handlers = options.handlers;
    this.policy = options.policy ?? DEFAULT_POLICY;
    this.factory = options.factory ?? ((url) => new WebSocket(url));
  }

  get status(): SocketStatus {
    if (this.socket === null) return 'closed';
    return this.socket.readyState === WebSocket.OPEN ? 'open' : 'connecting';
  }

  /** Attempts to reconnect so far. Resets on a successful open. */
  get retryCount(): number {
    return this.attempts;
  }

  open(): void {
    if (this.stopped) return;

    this.emitStatus('connecting');
    const socket = this.factory(this.url);
    this.socket = socket;

    socket.onopen = () => {
      // Reset here rather than on the first message: the connection is up
      // whether or not the server has anything to say yet, and a backoff that
      // only reset on data would keep growing across quiet reconnects.
      this.attempts = 0;
      this.emitStatus('open');
    };

    socket.onmessage = (event: MessageEvent) => {
      if (typeof event.data !== 'string') return;
      let message: unknown;
      try {
        message = JSON.parse(event.data);
      } catch {
        // A frame we cannot parse is the server's problem, not a reason to
        // tear down a working connection.
        return;
      }
      this.handleMessage(message);
    };

    socket.onclose = () => this.scheduleReconnect();
    // `onerror` carries no detail by design; `onclose` always follows it and
    // is where the recovery belongs.
    socket.onerror = () => undefined;
  }

  /** Send a frame. False when there is no open connection to send it on. */
  send(message: unknown): boolean {
    if (this.socket === null || this.socket.readyState !== WebSocket.OPEN) return false;
    this.socket.send(typeof message === 'string' ? message : JSON.stringify(message));
    return true;
  }

  close(): void {
    this.stopped = true;
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.socket !== null) {
      // Handlers dropped first: a close we asked for must not be reported as a
      // connection that dropped, or the caller starts reconnecting — or falls
      // back to polling — on its way out of the page.
      this.socket.onopen = null;
      this.socket.onmessage = null;
      this.socket.onclose = null;
      this.socket.onerror = null;
      this.socket.close();
      this.socket = null;
    }
  }

  /** One parsed frame. Subclasses own the shape and what to do with it. */
  protected abstract handleMessage(message: unknown): void;

  protected emitStatus(status: SocketStatus): void {
    this.handlers.onStatus(status);
  }

  private scheduleReconnect(): void {
    this.socket = null;
    if (this.stopped) return;

    // `closed`, not `connecting` — a caller that shows a spinner through the
    // backoff shows nothing useful, and one that can fall back to polling must
    // be told to start now rather than after the retry also fails.
    this.emitStatus('closed');

    const delay = Math.min(
      this.policy.baseDelayMs * 2 ** this.attempts,
      this.policy.maxDelayMs,
    );
    this.attempts += 1;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.open();
    }, delay);
  }
}

export interface JsonSocketHandlers extends SocketStatusHandler {
  onMessage: (message: unknown) => void;
}

/**
 * A reconnecting socket that hands every parsed frame to a callback.
 *
 * For the common case, so a host wanting reconnection does not have to declare
 * a subclass to get it. Subclass {@link ReconnectingSocket} when the framing
 * is worth a type of its own.
 */
export class JsonSocket extends ReconnectingSocket<JsonSocketHandlers> {
  protected handleMessage(message: unknown): void {
    this.handlers.onMessage(message);
  }
}

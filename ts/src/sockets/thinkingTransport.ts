/**
 * A websocket implementation of the chat's `ThinkingTransport` seam.
 *
 * `chat/thinking.ts` defines the interface and ships nothing behind it, which
 * is right — a websocket URL belongs to a host, not a library. But every host
 * filling that seam needs reconnection with backoff, and would write it. So
 * this is offered as an **optional** implementation: the chat components do not
 * import it, and a host that has its own transport never touches it.
 *
 *     <ThinkingTransportContext.Provider
 *       value={createWebSocketThinkingTransport({
 *         url: (turnId) => `wss://host/turns/${turnId}/thinking`,
 *       })}
 *     >
 *
 * ## One socket per turn
 *
 * A turn is the unit that starts and ends, and `subscribe` is called per turn,
 * so a socket per turn is the shape that matches. A single multiplexed socket
 * would need the server to route by turn id and would keep a connection open
 * between turns for no one — and its teardown could not be the per-turn
 * unsubscribe the interface promises.
 */

import type { ThinkingFrame } from '../chat/thinking';
import { NO_THINKING } from '../chat/thinking';
import { JsonSocket, type ReconnectPolicy, type SocketStatus } from './ReconnectingSocket';

/**
 * A turn lasts as long as the agent thinks, so the ceiling is short: a two
 * minute turn cannot afford a thirty-second gap in its own transcript.
 */
export const THINKING_POLICY: ReconnectPolicy = { baseDelayMs: 300, maxDelayMs: 3_000 };

export interface WebSocketThinkingTransportOptions {
  /** The socket URL for one turn. */
  url: (turnId: string) => string;
  policy?: ReconnectPolicy;
  /** Injectable for tests. */
  factory?: (url: string) => WebSocket;
  /**
   * Turn a server frame into a {@link ThinkingFrame}.
   *
   * Defaulted to reading the field names `chat/thinking` documents, and
   * overridable because a host's server almost certainly names them otherwise
   * — and rewriting the socket to change three field names is the reason a
   * seam like this gets bypassed.
   */
  parse?: (message: unknown) => ThinkingFrame | null;
  onStatus?: (turnId: string, status: SocketStatus) => void;
}

function defaultParse(message: unknown): ThinkingFrame | null {
  if (typeof message !== 'object' || message === null) return null;
  const frame = message as Record<string, unknown>;
  // A frame that names none of the fields is not a thinking frame — a
  // heartbeat, an ack — and returning NO_THINKING for it would blank a
  // transcript that had content.
  const known = ['content', 'answer', 'activity', 'isStreaming'];
  if (!known.some((key) => key in frame)) return null;
  return {
    content: typeof frame.content === 'string' ? frame.content : '',
    answer: typeof frame.answer === 'string' ? frame.answer : '',
    activity: typeof frame.activity === 'string' ? frame.activity : '',
    isStreaming: frame.isStreaming !== false,
  };
}

export function createWebSocketThinkingTransport(
  options: WebSocketThinkingTransportOptions,
) {
  const parse = options.parse ?? defaultParse;

  return {
    subscribe(turnId: string, onFrame: (frame: ThinkingFrame) => void): () => void {
      const socket = new JsonSocket({
        url: options.url(turnId),
        policy: options.policy ?? THINKING_POLICY,
        factory: options.factory,
        handlers: {
          onStatus: (status) => options.onStatus?.(turnId, status),
          onMessage: (message) => {
            const frame = parse(message);
            if (frame !== null) onFrame(frame);
          },
        },
      });
      socket.open();
      // The interface promises the teardown belongs to the subscription, so a
      // switched turn closes its own socket rather than leaking one per turn
      // the user glanced at.
      return () => socket.close();
    },
  };
}

export { NO_THINKING };

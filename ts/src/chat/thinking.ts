/**
 * Watching a turn think, without owning the wire.
 *
 * An agent's reasoning arrives while the turn runs, and every transport does it
 * differently — a websocket, server-sent events, a poll, a local generator in a
 * test. The version this came from opened a websocket against one app's URL and
 * fell back to polling one app's REST endpoint, which is exactly the part that
 * cannot travel.
 *
 * So the components take a *transport*: subscribe to a turn, receive frames,
 * unsubscribe. With none supplied they render the settled transcript and no
 * live stream, which is a working chat rather than a broken one.
 */

import { createContext, useContext, useEffect, useState } from 'react';

/** What a turn has produced so far. Frames are cumulative, not incremental. */
export interface ThinkingFrame {
  /** Reasoning and tool steps so far. Empty when the turn has produced none. */
  content: string;
  /** The reply as it forms. Replaced by the settled message when the turn ends. */
  answer: string;
  /** What the agent is doing right now, or "" when it has not said. */
  activity: string;
  /** Whether anything is still arriving. */
  isStreaming: boolean;
}

export const NO_THINKING: ThinkingFrame = {
  content: '',
  answer: '',
  activity: '',
  isStreaming: false,
};

/**
 * A source of frames for one turn.
 *
 * `subscribe` returns its own teardown. Frames are whole transcripts rather
 * than deltas, which is what lets a transport mix a socket with a catch-up poll
 * without the two having to agree on ordering — the consumer keeps the latest.
 */
export interface ThinkingTransport {
  subscribe: (turnId: string, onFrame: (frame: ThinkingFrame) => void) => () => void;
}

export const ThinkingTransportContext = createContext<ThinkingTransport | null>(null);

/**
 * The live state of a turn.
 *
 * `null`/empty turn id, or no transport, both yield the settled state — a chat
 * with no streaming is a supported configuration, not a degraded one.
 */
export function useTurnThinking(turnId: string | null | undefined): ThinkingFrame {
  const transport = useContext(ThinkingTransportContext);
  const [frame, setFrame] = useState<ThinkingFrame>(NO_THINKING);

  useEffect(() => {
    setFrame(NO_THINKING);
    if (!turnId || transport === null) return;
    // The teardown is the transport's own, so a subscription cannot outlive the
    // turn it was opened for — switching turns mid-stream is the ordinary case,
    // not an edge one.
    return transport.subscribe(turnId, setFrame);
  }, [turnId, transport]);

  return frame;
}

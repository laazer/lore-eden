/**
 * The transcript: settled messages, and the turn currently forming.
 *
 * Written for this package rather than lifted, and that is worth being straight
 * about. The component this replaces was 250 lines in which the generic parts —
 * scroll, roles, the live stream, the avatar row — were interleaved with one
 * product's features: agent plans, superseded-plan bookkeeping, a plan/run
 * summary line. Stripping those out would have left something that read like an
 * extraction but was really a rewrite with the seams still showing. This keeps
 * the behaviours worth keeping and states where they came from.
 *
 * The behaviours worth keeping, each for a reason:
 *
 * - **Autoscroll only when already at the bottom.** Yanking someone back down
 *   while they are reading earlier messages is worse than not following.
 * - **The forming reply is not rendered as markdown.** Half a fenced block or
 *   half a table renders as garbage that rearranges itself on every frame.
 * - **The reasoning stream and the pacing placeholder are alternatives.** Once
 *   the agent is saying what it is doing, a "thinking…" animation beside it is
 *   noise.
 * - **The avatar is a slot.** The source's was a branded mascot with five
 *   states; that is a product's, not a library's.
 */

import { useEffect, useLayoutEffect, useRef, type ReactNode } from 'react';

import { ChatMessageBubble } from './ChatMessageBubble';
import { LiveThinkingStream } from './LiveThinkingStream';
import { chatMessageBody, isUserChatRole, type ChatMessageView } from './chatUtils';
import { renderChatParts, type ChatPartRegistry } from './parts';
import { useTurnThinking } from './thinking';
import './ChatLook.css';

/** How the assistant is depicted while a turn is in one state or another. */
export type AssistantActivity = 'idle' | 'thinking' | 'answering';

export interface ChatMessagesProps {
  messages: ChatMessageView[];
  /** Shown when there are no messages at all. */
  emptyMessage?: ReactNode;
  /** Whether a turn is in flight. */
  isThinking?: boolean;
  /** The turn to stream, when one is running. */
  activeTurnId?: string | null;
  /** Headline while a turn runs and has not yet said what it is doing. */
  thinkingMessage?: string;
  /** Second line under that headline. */
  thinkingSub?: ReactNode;
  /** What the assistant is called, in labels and accessible names. */
  assistantLabel?: string;
  /**
   * The assistant's likeness, if it has one.
   *
   * A slot: given the current activity, render something. Omitted, the
   * transcript is text and reads perfectly well.
   */
  renderAvatar?: (props: { activity: AssistantActivity; label: string }) => ReactNode;
  /** Renderers for structured message parts. */
  partRegistry?: ChatPartRegistry;
  /** Anything to draw after a given message — a reaction row, a divider. */
  renderAfterMessage?: (message: ChatMessageView, index: number) => ReactNode;
  /** Pinned to the end of the transcript, after any live turn. */
  trailing?: ReactNode;
  /** Follow new messages down. On by default. */
  autoScroll?: boolean;
}

/** Within this many pixels of the bottom still counts as "at the bottom". */
const AT_BOTTOM_SLACK_PX = 48;

export function ChatMessages({
  messages,
  emptyMessage,
  isThinking = false,
  activeTurnId = null,
  thinkingMessage = 'Working…',
  thinkingSub,
  assistantLabel = 'Assistant',
  renderAvatar,
  partRegistry,
  renderAfterMessage,
  trailing,
  autoScroll = true,
}: ChatMessagesProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const wasAtBottom = useRef(true);

  const thinking = useTurnThinking(isThinking ? activeTurnId : null);
  const hasLiveThinking = Boolean(thinking.content.trim() || thinking.answer.trim());

  // Measured before the DOM updates: afterwards the new content has already
  // moved the scroll position, and the question "were they at the bottom?"
  // can no longer be answered.
  useLayoutEffect(() => {
    const node = scrollRef.current;
    if (node === null) return;
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    wasAtBottom.current = distance <= AT_BOTTOM_SLACK_PX;
  });

  useEffect(() => {
    if (!autoScroll || !wasAtBottom.current) return;
    const tail = bottomRef.current;
    // Feature-detected rather than assumed: `scrollIntoView` is absent in jsdom
    // and in some embedded webviews, and a missing scroll helper must not take
    // the whole transcript down with it. Not following is a far smaller failure
    // than not rendering.
    if (typeof tail?.scrollIntoView !== 'function') return;
    tail.scrollIntoView({ block: 'end' });
  }, [messages, thinking.content, thinking.answer, isThinking, autoScroll]);

  const activity: AssistantActivity = !isThinking
    ? 'idle'
    : thinking.answer.trim()
      ? 'answering'
      : 'thinking';

  return (
    <div ref={scrollRef} className="lg-chat-messages" data-testid="chat-messages">
      {messages.length === 0 && !isThinking ? (
        <div className="lg-chat-empty">{emptyMessage}</div>
      ) : null}

      {messages.map((message, index) => {
        const parts = renderChatParts(message.parts, partRegistry, message.id);
        return (
          <div
            key={message.id}
            className="lg-chat-msg-row"
            data-chat-role={isUserChatRole(message.role) ? 'user' : 'assistant'}
          >
            {!isUserChatRole(message.role) && renderAvatar
              ? renderAvatar({ activity: 'idle', label: assistantLabel })
              : null}
            <div className="lg-chat-msg-body">
              <ChatMessageBubble message={message} assistantLabel={assistantLabel} />
              {parts.length > 0 ? <div className="lg-chat-parts">{parts}</div> : null}
              {renderAfterMessage?.(message, index)}
            </div>
          </div>
        );
      })}

      {isThinking ? (
        <div className="lg-chat-msg-row" data-chat-role="assistant" data-testid="chat-thinking">
          {renderAvatar?.({ activity, label: assistantLabel })}
          <div className="lg-chat-loading">
            <p className="lg-chat-loading-title">{thinkingMessage}</p>
            {hasLiveThinking ? (
              <>
                {thinking.content.trim() ? (
                  <LiveThinkingStream
                    content={thinking.content}
                    activity={thinking.activity}
                    label={assistantLabel}
                  />
                ) : null}
                {thinking.answer.trim() ? (
                  // Deliberately not markdown while it streams — see the
                  // module note.
                  <div className="lg-chat-reply lg-chat-reply--streaming" data-testid="chat-reply">
                    {thinking.answer}
                  </div>
                ) : null}
              </>
            ) : thinkingSub !== undefined ? (
              <p className="lg-chat-loading-sub">{thinkingSub}</p>
            ) : null}
          </div>
        </div>
      ) : null}

      {trailing}
      <div ref={bottomRef} className="lg-chat-messages-tail" aria-hidden />
    </div>
  );
}

/** The text a message displays, for a host building a preview or a title. */
export { chatMessageBody };

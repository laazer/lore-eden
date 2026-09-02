/**
 * Chat: the transcript, the composer, and the three seams that had to be cut.
 *
 * The streaming transport, the structured parts, and the avatar were all
 * hardcoded to one product. Each is asserted here as a seam — supplied, and
 * absent — because "works when configured" and "still works unconfigured" are
 * different claims and a library owes both.
 */

import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import {
  ChatComposer,
  ChatMessages,
  ChatPartRegistry,
  NO_THINKING,
  ThinkingTransportContext,
  assistantTextFromParts,
  isTextPart,
  renderChatParts,
  type ChatMessageView,
  type ThinkingFrame,
  type ThinkingTransport,
} from '../src/chat';

const messages: ChatMessageView[] = [
  { id: 'm1', role: 'user', content: 'What changed?' },
  { id: 'm2', role: 'assistant', content: 'Three files.' },
];

describe('the transcript', () => {
  it('renders the messages it is given', () => {
    render(<ChatMessages messages={messages} />);

    expect(screen.getByText('What changed?')).toBeInTheDocument();
    expect(screen.getByText('Three files.')).toBeInTheDocument();
  });

  it('marks who said what, so a stylesheet can tell them apart', () => {
    const { container } = render(<ChatMessages messages={messages} />);

    expect(container.querySelectorAll('[data-chat-role="user"]')).toHaveLength(1);
    expect(container.querySelectorAll('[data-chat-role="assistant"]')).toHaveLength(1);
  });

  it('shows the empty state only when there is nothing at all', () => {
    render(<ChatMessages messages={[]} emptyMessage="No messages yet" />);

    expect(screen.getByText('No messages yet')).toBeInTheDocument();
  });

  it('does not show the empty state while a turn is starting', () => {
    // An empty transcript with a turn in flight is not an empty conversation.
    render(<ChatMessages messages={[]} emptyMessage="No messages yet" isThinking />);

    expect(screen.queryByText('No messages yet')).toBeNull();
  });

  it('renders whatever a host appends after a message', () => {
    render(
      <ChatMessages
        messages={messages}
        renderAfterMessage={(message) => <span key={message.id}>after {message.id}</span>}
      />,
    );

    expect(screen.getByText('after m1')).toBeInTheDocument();
  });
});

describe('the avatar slot', () => {
  it('renders nothing when the host has no likeness to offer', () => {
    // The source's was a branded mascot with five states. A transcript without
    // one is text, and reads perfectly well.
    render(<ChatMessages messages={messages} />);

    expect(screen.queryByTestId('avatar')).toBeNull();
    expect(screen.getByText('Three files.')).toBeInTheDocument();
  });

  it('renders the host’s, told what the assistant is doing', () => {
    render(
      <ChatMessages
        messages={messages}
        isThinking
        renderAvatar={({ activity }) => <span data-testid="avatar">{activity}</span>}
      />,
    );

    // One per assistant message, plus the turn in flight.
    const avatars = screen.getAllByTestId('avatar');
    expect(avatars.map((node) => node.textContent)).toContain('thinking');
  });
});

describe('the thinking transport', () => {
  function transportOf(frame: ThinkingFrame): ThinkingTransport {
    return {
      subscribe: (_turnId, onFrame) => {
        onFrame(frame);
        return () => undefined;
      },
    };
  }

  it('renders a settled transcript with no transport at all', () => {
    // A chat with no streaming is a supported configuration, not a degraded one.
    render(<ChatMessages messages={messages} isThinking activeTurnId="t1" />);

    expect(screen.getByTestId('chat-thinking')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-reply')).toBeNull();
  });

  it('shows the reply as it forms', () => {
    render(
      <ThinkingTransportContext.Provider
        value={transportOf({ ...NO_THINKING, answer: 'Half a sen', isStreaming: true })}
      >
        <ChatMessages messages={messages} isThinking activeTurnId="t1" />
      </ThinkingTransportContext.Provider>,
    );

    expect(screen.getByTestId('chat-reply')).toHaveTextContent('Half a sen');
  });

  it('shows the reasoning stream when there is reasoning', () => {
    render(
      <ThinkingTransportContext.Provider
        value={transportOf({ ...NO_THINKING, content: 'Reading the diff', isStreaming: true })}
      >
        <ChatMessages messages={messages} isThinking activeTurnId="t1" />
      </ThinkingTransportContext.Provider>,
    );

    expect(screen.getByText(/Reading the diff/)).toBeInTheDocument();
  });

  it('unsubscribes when the turn changes, so a stream cannot outlive its turn', () => {
    const unsubscribe = vi.fn();
    const transport: ThinkingTransport = { subscribe: () => unsubscribe };

    const { rerender } = render(
      <ThinkingTransportContext.Provider value={transport}>
        <ChatMessages messages={messages} isThinking activeTurnId="t1" />
      </ThinkingTransportContext.Provider>,
    );
    rerender(
      <ThinkingTransportContext.Provider value={transport}>
        <ChatMessages messages={messages} isThinking activeTurnId="t2" />
      </ThinkingTransportContext.Provider>,
    );

    expect(unsubscribe).toHaveBeenCalled();
  });

  it('does not subscribe when no turn is running', () => {
    const subscribe = vi.fn(() => () => undefined);

    render(
      <ThinkingTransportContext.Provider value={{ subscribe }}>
        <ChatMessages messages={messages} isThinking={false} activeTurnId="t1" />
      </ThinkingTransportContext.Provider>,
    );

    expect(subscribe).not.toHaveBeenCalled();
  });
});

describe('structured parts', () => {
  it('renders a kind the host registered', () => {
    const registry = new ChatPartRegistry().register('diff', ({ part }) => (
      <span data-testid="diff">{String(part.file)}</span>
    ));

    render(
      <ChatMessages
        messages={[{ id: 'm1', role: 'assistant', content: '', parts: [{ kind: 'diff', file: 'a.ts' }] }]}
        partRegistry={registry}
      />,
    );

    expect(screen.getByTestId('diff')).toHaveTextContent('a.ts');
  });

  it('falls back rather than throwing on a kind this build dropped', () => {
    // A transcript outlives the build that wrote it; a message naming a part
    // this build no longer has must still be readable.
    const registry = new ChatPartRegistry().setFallback(({ part }) => (
      <span data-testid="fallback">{part.kind}</span>
    ));

    render(renderChatParts([{ kind: 'from-the-future' }], registry) as React.ReactElement[]);

    expect(screen.getByTestId('fallback')).toHaveTextContent('from-the-future');
  });

  it('skips a kind with no renderer and no fallback', () => {
    expect(renderChatParts([{ kind: 'unknown' }], new ChatPartRegistry())).toHaveLength(0);
  });

  it('ignores anything that is not shaped like a part', () => {
    const registry = new ChatPartRegistry().setFallback(() => <span />);

    expect(renderChatParts([null, 'text', 42, {}], registry)).toHaveLength(0);
  });

  it('resolves kinds through a Map, so a crafted kind cannot reach the prototype', () => {
    const registry = new ChatPartRegistry();

    expect(registry.get('__proto__')).toBeUndefined();
  });

  it('refuses to register one kind twice', () => {
    const registry = new ChatPartRegistry().register('diff', () => null);

    expect(() => registry.register('diff', () => null)).toThrow(/already registered/);
  });

  it('ships one part shape, because the transcript needs to know what was said', () => {
    expect(isTextPart({ kind: 'text', content: 'hello' })).toBe(true);
    expect(isTextPart({ kind: 'text' })).toBe(false);
    expect(isTextPart({ kind: 'diff', content: 'x' })).toBe(false);
  });

  it('prefers text parts over the raw content when both exist', () => {
    const text = assistantTextFromParts(
      [{ kind: 'text', content: 'From the parts' }],
      'From the content',
    );

    expect(text).toBe('From the parts');
  });

  it('falls back to the content when no part carries text', () => {
    expect(assistantTextFromParts([{ kind: 'diff' }], 'From the content')).toBe('From the content');
    expect(assistantTextFromParts(undefined, 'From the content')).toBe('From the content');
  });
});

describe('the composer', () => {
  function Composer(props: Partial<React.ComponentProps<typeof ChatComposer>> = {}) {
    return (
      <ChatComposer value="" onChange={() => undefined} onSubmit={() => undefined} {...props} />
    );
  }

  it('is fully controlled', () => {
    const onChange = vi.fn();
    render(<Composer value="draft" onChange={onChange} />);

    const input = screen.getByRole('textbox');
    expect(input).toHaveValue('draft');

    fireEvent.change(input, { target: { value: 'drafted' } });
    expect(onChange).toHaveBeenCalledWith('drafted');
  });

  it('refuses to send an empty draft', () => {
    const onSubmit = vi.fn();
    render(<Composer value="   " onSubmit={onSubmit} />);

    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('sends a real one', () => {
    const onSubmit = vi.fn();
    render(<Composer value="ship it" onSubmit={onSubmit} />);

    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    expect(onSubmit).toHaveBeenCalled();
  });

  it('offers Stop while a turn is in flight', () => {
    const onStop = vi.fn();
    render(<Composer value="" isSending onStop={onStop} />);

    fireEvent.click(screen.getByRole('button', { name: /stop/i }));

    expect(onStop).toHaveBeenCalled();
  });

  it('lets a command run while a turn is in flight, when an ordinary send cannot', () => {
    // "/stop" has to work at exactly the moment sending is refused, so the
    // command path is consulted before the send gate rather than after it.
    const onSubmit = vi.fn();
    const submit = vi.fn(() => true);
    render(
      <Composer
        value=""
        isSending
        onSubmit={onSubmit}
        commands={{
          inputRef: React.createRef(),
          items: [],
          activeIndex: 0,
          setActiveIndex: () => undefined,
          triggerKind: null,
          accept: () => undefined,
          close: () => undefined,
          handleChange: () => undefined,
          handleKeyDown: () => false,
          submit,
        }}
      />,
    );

    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });

    expect(submit).toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('renders what a host puts above it', () => {
    render(<Composer renderAbove={() => <div data-testid="above">queued</div>} />);

    expect(screen.getByTestId('above')).toBeInTheDocument();
  });

  it('shows an error when there is one', () => {
    render(<Composer error="Could not send" />);

    expect(screen.getByText('Could not send')).toBeInTheDocument();
  });
});

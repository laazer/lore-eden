/**
 * Structured content a message can carry beyond its text, and how it renders.
 *
 * A message is usually prose, but an assistant that can *act* wants to show
 * what it acted on — a record, a plan, a diff — as something the reader can
 * look at rather than a paragraph describing it.
 *
 * The codebase this came from had twenty such parts, each a card for one of its
 * own nouns, in a union mirrored from a server module. None of that travels: the
 * parts are the product, and a package shipping them would be shipping one
 * app's screens under a general name.
 *
 * What travels is the seam. A part is `{ kind, ...anything }`; a host registers
 * a renderer per kind; an unknown kind falls back rather than throwing, because
 * a transcript outlives the build that wrote it and a message from last week
 * naming a part this build dropped must still be readable.
 */

import type { ComponentType, ReactNode } from 'react';

/**
 * One structured element of a message.
 *
 * Deliberately open. Narrowing is the renderer's job, at the point that knows
 * what the kind means.
 *
 * `kind` rather than the `primitive` the source discriminated on: this package
 * already calls a pane's contents a primitive, and one word meaning two things
 * across two modules of the same library is a confusion worth spending a rename
 * to avoid. A host mapping from the other spelling does it once, at its
 * boundary.
 */
export interface ChatPart {
  kind: string;
  [field: string]: unknown;
}

/**
 * The one part shape this package defines, because it has to.
 *
 * A message list needs to know what a message *says* — to show a preview, to
 * fall back when no renderer is registered, to put something in the DOM a test
 * can assert on. Everything else is the host's vocabulary; this one is the
 * floor beneath it.
 */
export const TEXT_PART_KIND = 'text';

export interface TextPart extends ChatPart {
  kind: typeof TEXT_PART_KIND;
  content: string;
}

export function isTextPart(part: ChatPart): part is TextPart {
  return part.kind === TEXT_PART_KIND && typeof part.content === 'string';
}

export interface ChatPartProps {
  part: ChatPart;
  /** The message this part belongs to, for a renderer that needs the context. */
  messageId?: string;
}

export type ChatPartRenderer = ComponentType<ChatPartProps>;

/**
 * The renderers a host offers, by part kind.
 *
 * A `Map` rather than an object for the reason the pane registry uses one: a
 * `kind` arrives inside stored or transported JSON, and `"__proto__" in obj`
 * answers true.
 */
export class ChatPartRegistry {
  private readonly byKind = new Map<string, ChatPartRenderer>();
  private fallback?: ChatPartRenderer;

  register(kind: string, renderer: ChatPartRenderer): this {
    if (this.byKind.has(kind)) {
      throw new Error(`A renderer for chat part ${kind} is already registered`);
    }
    this.byKind.set(kind, renderer);
    return this;
  }

  /**
   * What to draw for a kind nothing is registered for.
   *
   * Worth setting to something visible. The alternative — rendering nothing —
   * makes a message look like it said less than it did, which is worse than an
   * unfamiliar card.
   */
  setFallback(renderer: ChatPartRenderer): this {
    this.fallback = renderer;
    return this;
  }

  get(kind: string): ChatPartRenderer | undefined {
    return this.byKind.get(kind) ?? this.fallback;
  }

  kinds(): string[] {
    return [...this.byKind.keys()];
  }
}

export const defaultChatPartRegistry = new ChatPartRegistry();

/** True for something shaped like a part, without asserting which kind. */
export function isChatPart(value: unknown): value is ChatPart {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { kind?: unknown }).kind === 'string'
  );
}

export function renderChatParts(
  parts: readonly unknown[] | undefined,
  registry: ChatPartRegistry = defaultChatPartRegistry,
  messageId?: string,
): ReactNode[] {
  if (!parts) return [];
  const rendered: ReactNode[] = [];
  parts.forEach((raw, index) => {
    if (!isChatPart(raw)) return;
    const Renderer = registry.get(raw.kind);
    if (Renderer === undefined) return;
    rendered.push(<Renderer key={`${raw.kind}:${index}`} part={raw} messageId={messageId} />);
  });
  return rendered;
}

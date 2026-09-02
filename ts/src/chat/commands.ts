/**
 * The contract between a composer and whatever drives its `/` and `@` menus.
 *
 * The binding this replaces carried twenty-odd members, and most of them were
 * one product's composer features — saved notes, a queued-message strip, a help
 * panel. None of that is a property of a composer; all of it is a property of
 * that app.
 *
 * What is left is the part the *component* genuinely needs to render a menu and
 * hand keystrokes to it. A host implements this over whatever command
 * vocabulary it has, and puts its own features above the composer through the
 * `renderAbove` slot rather than through this interface.
 */

import type { KeyboardEvent, RefObject } from 'react';

export type ComposerInput = HTMLInputElement | HTMLTextAreaElement;

/** What opened the menu, which is also how it is labelled. */
export type ComposerTrigger = 'slash' | 'mention';

export interface ComposerMenuItem {
  /** Stable identity, for keys and for equality. */
  id: string;
  /** What the row reads as. */
  label: string;
  /** A second line, when there is something worth saying. */
  detail?: string;
}

export interface ComposerCommands {
  /** The composer's input, so a menu can anchor to it. */
  inputRef: RefObject<ComposerInput | null>;
  /** Rows to offer, or empty when no trigger is open. */
  items: ComposerMenuItem[];
  activeIndex: number;
  setActiveIndex: (index: number) => void;
  triggerKind: ComposerTrigger | null;
  accept: (item: ComposerMenuItem) => void;
  close: () => void;
  /** Call from the input's `onChange`, with the event's element. */
  handleChange: (value: string, element: ComposerInput | null) => void;
  /**
   * Returns true when the key was consumed by the menu.
   *
   * The composer skips its own handling then — otherwise Enter both picks a
   * menu row and sends the draft, which is one keystroke doing two things.
   */
  handleKeyDown: (event: KeyboardEvent<ComposerInput>) => boolean;
  /**
   * Interpret the draft and act on it. Returns true when it handled the send.
   *
   * Consulted *before* the composer's own send gate, deliberately: a command
   * like "stop" has to work while a turn is in flight, which is exactly when an
   * ordinary send is refused.
   */
  submit: () => boolean;
}

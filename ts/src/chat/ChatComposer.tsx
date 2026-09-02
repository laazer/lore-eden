/**
 * The composer: a controlled textarea, a send/stop control, and an optional
 * command menu.
 *
 * Fully controlled — it holds no draft of its own. That was already true of the
 * component this came from, and it is what makes it reusable: a host that wants
 * to persist a draft, prefill it, or clear it on send does so by owning the
 * value, not by reaching inside.
 */

import type { ReactNode } from 'react';

import { ComposerCommandMenu } from './ComposerCommandMenu';
import type { ComposerCommands } from './commands';
import './ChatLook.css';

export type ChatComposerVariant = 'panel' | 'dock';

export function ChatComposer({
  value,
  onChange,
  onSubmit,
  onStop,
  placeholder,
  isSending,
  isStopping,
  disabled,
  sendLabel = "Send",
  sendingLabel = "Sending…",
  stopLabel = "Stop",
  toolbar,
  error,
  variant = "panel",
  showShortcut,
  iconOnlySend,
  commands,
  renderAbove,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  /** When set, the send control becomes Stop while a turn is in flight. */
  onStop?: () => void;
  placeholder?: string;
  isSending?: boolean;
  isStopping?: boolean;
  disabled?: boolean;
  sendLabel?: string;
  sendingLabel?: string;
  stopLabel?: string;
  toolbar?: ReactNode;
  error?: string | null;
  /** `panel` for side panes; `dock` for the floating bottom composer. */
  variant?: ChatComposerVariant;
  showShortcut?: boolean;
  /** Round icon-only send. Defaults on for the dock variant. */
  iconOnlySend?: boolean;
  /**
   * `/` commands and `@` references, driven by the host.
   *
   * Optional because not every composer belongs to a conversation that can act
   * on them — a surface without one gets a plain composer rather than a menu
   * whose entries would do nothing.
   */
  commands?: ComposerCommands;
  /**
   * Anything the host wants directly above the input — saved drafts, a queued
   * strip, a warning. A slot rather than interface members, because those are
   * the host's features and the composer has no opinion about them.
   */
  renderAbove?: () => ReactNode;
}) {
  const canStop = Boolean(isSending && onStop) && !isStopping && !disabled;
  const canSend = value.trim().length > 0 && !isSending && !disabled;
  const showStop = Boolean(isSending && onStop);
  // Round icon-only is fine for Send; Stop must read as Stop — a square swap
  // on the same accent chip is too easy to miss mid-stream.
  const roundSend = (iconOnlySend ?? variant === "dock") && !showStop;

  const submit = () => {
    // `/stop`, `/queue`, `/help` and friends must work while a turn is in
    // flight — gate the ordinary send on canSend, not the command path.
    if (commands?.submit()) return;
    if (!canSend) return;
    onSubmit();
  };

  const stop = () => {
    if (!canStop || !onStop) return;
    onStop();
  };

  return (
    <div
      className={[
        "lg-chat-composer-wrap",
        `lg-chat-composer-wrap--${variant}`,
      ].join(" ")}
    >
          {renderAbove?.()}
      <div className="lg-chat-composer">
        <div className="lg-composer-commands">
          {commands ? (
            <ComposerCommandMenu
              items={commands.items}
              activeIndex={commands.activeIndex}
              triggerKind={commands.triggerKind}
              anchorRef={commands.inputRef}
              onHover={commands.setActiveIndex}
              onPick={commands.accept}
            />
          ) : null}
          <textarea
            ref={commands?.inputRef as React.Ref<HTMLTextAreaElement>}
            className="lg-chat-composer-input"
            value={value}
            onChange={(e) =>
              commands ? commands.handleChange(e.target.value, e.target) : onChange(e.target.value)
            }
            onBlur={() => commands?.close()}
            placeholder={placeholder}
            disabled={disabled}
            rows={variant === "dock" ? 1 : 2}
            onKeyDown={(e) => {
              // The menu owns Enter, Tab and the arrows while it is open, so a
              // completion is accepted rather than sent as a half-typed draft.
              if (commands?.handleKeyDown(e)) return;
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (value.trim().startsWith("/") && commands?.submit()) return;
                if (showStop) {
                  // Enter aborts the turn only on an empty draft. The textarea
                  // stays live mid-turn so `/stop` can be typed at all, which
                  // newly exposes this path to someone typing their next
                  // message while the agent works — and killing the run is not
                  // what they pressed Enter for. The Stop button is still one
                  // click away, and `/stop` still works with text in the box.
                  if (!value.trim()) stop();
                  return;
                }
                submit();
              }
            }}
          />
        </div>
        <div className="lg-chat-composer-toolbar">
          {toolbar}
          <div className="lg-chat-composer-spacer" />
          {showShortcut && !showStop ? (
            <span className="lg-chat-composer-shortcut" aria-hidden>
              ⌘J
            </span>
          ) : null}
          <button
            type="button"
            className={[
              "lg-chat-composer-send",
              roundSend ? "" : "lg-chat-composer-send--labeled",
              showStop ? "lg-chat-composer-send--stop" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            disabled={showStop ? !canStop : !canSend}
            onClick={showStop ? stop : submit}
            aria-label={
              roundSend
                ? isSending
                  ? sendingLabel
                  : sendLabel
                : showStop
                  ? isStopping
                    ? "Stopping…"
                    : stopLabel
                  : undefined
            }
          >
            {roundSend
              ? null
              : showStop
                ? isStopping
                  ? "Stopping…"
                  : stopLabel
                : isSending
                  ? sendingLabel
                  : sendLabel}
            {showStop ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                <rect x="6" y="6" width="12" height="12" rx="1.5" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
                <path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" />
              </svg>
            )}
          </button>
        </div>
        {error ? <div className="lg-chat-composer-error">{error}</div> : null}
      </div>
    </div>
  );
}

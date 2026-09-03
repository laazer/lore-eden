/**
 * Feedback: a spinner and a toast.
 *
 * From loremaker's `liquid-glass/feedback`. The dismissal behaviour is the
 * substance — a toast that stays until dismissed and one that leaves on a timer
 * are different components, and the source folded both into one with a prop.
 */

import React, { forwardRef, useEffect, useRef } from 'react';

import './controls.css';

export type ToastTone = 'neutral' | 'ok' | 'warn' | 'crit';
export type DismissReason = 'timeout' | 'explicit';

const classes = (...parts: (string | false | undefined)[]): string =>
  parts.filter(Boolean).join(' ');

export interface SpinnerProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Announced to a screen reader. Empty makes the spinner decorative. */
  label?: string;
}

export const Spinner = forwardRef<HTMLSpanElement, SpinnerProps>(function Spinner(
  { label = 'Loading', className, ...rest },
  ref,
) {
  return (
    <span
      {...rest}
      ref={ref}
      role={label ? 'status' : undefined}
      aria-label={label || undefined}
      aria-hidden={label ? undefined : true}
      className={classes('le-spinner', className)}
    />
  );
});

export interface ToastProps {
  message: React.ReactNode;
  tone?: ToastTone;
  /** Milliseconds until it dismisses itself. Omit to make it stay. */
  duration?: number;
  onDismiss?: (reason: DismissReason) => void;
  closeLabel?: string;
  className?: string;
}

export function Toast({
  message,
  tone = 'neutral',
  duration,
  onDismiss,
  closeLabel = 'Dismiss',
  className,
}: ToastProps): React.ReactElement {
  // Held in a ref so the timer depends on the duration alone. Taking the
  // callback as a dependency restarts the countdown whenever the parent
  // re-renders with a fresh arrow, and a toast whose parent renders often
  // never dismisses itself.
  const dismiss = useRef(onDismiss);
  dismiss.current = onDismiss;

  useEffect(() => {
    if (duration === undefined) return;
    const timer = setTimeout(() => dismiss.current?.('timeout'), duration);
    return () => clearTimeout(timer);
  }, [duration]);

  return (
    <div
      // `alert` interrupts; `status` waits for a pause. A warning that needs
      // acting on earns the interruption, and a confirmation does not.
      role={tone === 'crit' || tone === 'warn' ? 'alert' : 'status'}
      className={classes('le-toast', tone !== 'neutral' && `le-toast--${tone}`, className)}
    >
      <span>{message}</span>
      {onDismiss !== undefined && (
        <button
          type="button"
          className="le-toast__close"
          aria-label={closeLabel}
          onClick={() => dismiss.current?.('explicit')}
        >
          ×
        </button>
      )}
    </div>
  );
}

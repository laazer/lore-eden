/**
 * A confirm step for an action that changes what other people — or agents —
 * will see, optionally requiring a reason.
 *
 * Extracted from loregarden's `DiscreditConfirmModal`, the confirm step before
 * a learning is withdrawn from every future agent briefing. What generalises is
 * the contract, not the copy:
 *
 * - **Keyboard first.** Focus moves in on open, stays in while open and goes
 *   back to the opener on close (`useDialogFocusTrap`); Escape closes, and only
 *   the innermost dialog's Escape (`useDialogDismiss`).
 * - **One click, one action.** While `busy`, every control is disabled and
 *   neither Escape nor the backdrop dismisses — a second press cannot fire the
 *   action twice, and a dismissal cannot orphan a write that is in flight.
 * - **A required reason is required.** Whitespace does not count; the confirm
 *   button stays disabled and the form does not submit until one is given. The
 *   trimmed reason is what `onConfirm` receives.
 */

import React, { useId, useState } from 'react';

import { useDialogDismiss } from '../hooks/useDialogDismiss';
import { useDialogFocusTrap } from '../hooks/useDialogFocusTrap';
import { Button } from './inputs';
import './controls.css';

export interface ConfirmDialogProps {
  /** Whether the dialog is showing. */
  open: boolean;
  title: string;
  /** What will happen, in a sentence or two. */
  description?: React.ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  /** Label for the reason field. Omit for a dialog that asks for none. */
  reasonLabel?: string;
  /** Styles the confirm button as destructive. */
  danger?: boolean;
  busy?: boolean;
  onCancel: () => void;
  /** Receives the trimmed reason, or "" when no reason is asked for. */
  onConfirm: (reason: string) => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = 'Cancel',
  reasonLabel,
  danger = false,
  busy = false,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  const [reason, setReason] = useState('');
  const titleId = useId();
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  useDialogDismiss(!open ? null : busy ? undefined : onCancel);
  if (!open) return null;

  const trimmed = reason.trim();
  const blocked = busy || (reasonLabel !== undefined && trimmed === '');

  return (
    <>
      <div
        className="le-dialog-backdrop"
        onClick={busy ? undefined : onCancel}
        role="presentation"
      />
      <div
        ref={dialogRef}
        className="le-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!blocked) onConfirm(reasonLabel === undefined ? '' : trimmed);
          }}
        >
          <h2 id={titleId} className="le-dialog-title">
            {title}
          </h2>
          {description ? <div className="le-dialog-body">{description}</div> : null}
          {reasonLabel !== undefined ? (
            <label className="le-dialog-reason">
              <span>{reasonLabel}</span>
              <textarea
                value={reason}
                rows={3}
                required
                disabled={busy}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
          ) : null}
          <div className="le-dialog-actions">
            <Button variant="ghost" disabled={busy} onClick={onCancel}>
              {cancelLabel}
            </Button>
            <Button type="submit" variant={danger ? 'danger' : 'primary'} disabled={blocked}>
              {confirmLabel}
            </Button>
          </div>
        </form>
      </div>
    </>
  );
}

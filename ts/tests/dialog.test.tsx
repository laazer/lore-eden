import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ConfirmDialog } from '../src/controls';

function Harness({
  onConfirm = vi.fn(),
  busy = false,
  reasonLabel,
}: {
  onConfirm?: (reason: string) => void;
  busy?: boolean;
  reasonLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      <button type="button">Behind</button>
      <ConfirmDialog
        open={open}
        title="Discredit this learning?"
        confirmLabel="Discredit"
        reasonLabel={reasonLabel}
        busy={busy}
        onCancel={() => setOpen(false)}
        onConfirm={onConfirm}
      />
    </>
  );
}

const open = () => fireEvent.click(screen.getByRole('button', { name: 'Open' }));

describe('ConfirmDialog', () => {
  it('takes focus on open, holds it, and gives it back on close', () => {
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'Open' });
    opener.focus();
    open();
    const dialog = screen.getByRole('dialog', { name: 'Discredit this learning?' });
    expect(dialog.contains(document.activeElement)).toBe(true);

    const confirm = screen.getByRole('button', { name: 'Discredit' });
    confirm.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }));

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it('will not confirm until a non-blank reason is given, and passes it trimmed', () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} reasonLabel="Reason" />);
    open();
    const confirm = screen.getByRole('button', { name: 'Discredit' });
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByRole('textbox', { name: 'Reason' }), { target: { value: '   ' } });
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByRole('textbox', { name: 'Reason' }), {
      target: { value: '  wrong since 3.45 ' },
    });
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledWith('wrong since 3.45');
  });

  it('while busy, nothing dismisses it and nothing fires twice', () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} busy />);
    open();
    expect(screen.getByRole('button', { name: 'Discredit' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('asks for no reason when given no reason label', () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);
    open();
    expect(screen.queryByRole('textbox')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Discredit' }));
    expect(onConfirm).toHaveBeenCalledWith('');
  });
});

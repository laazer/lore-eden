import React, { useRef, useState } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  Button,
  Checkbox,
  Field,
  Kbd,
  Select,
  Skeleton,
  Spinner,
  StatusDot,
  Switch,
  Tag,
  TextInput,
  Toast,
} from '../src/controls';

/**
 * Behaviour and semantics, never class names.
 *
 * The source's tests asserted `toHaveClass("btn--primary")` — and **that class
 * is defined in no stylesheet in that application**, along with 29 of the other
 * 33 it emits. So every one of those tests passed against a component that
 * renders as an unstyled browser default. A test that asserts a string is
 * present proves the string is present.
 */

describe('Button', () => {
  it('defaults its type to button', () => {
    // HTML defaults it to submit, so a button inside a form that omits it
    // submits — a bug that presents as a routing problem.
    render(<Button>Save</Button>);
    expect(screen.getByRole('button')).toHaveAttribute('type', 'button');
  });

  it('does not fire onClick while disabled', () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        Save
      </Button>,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(onClick).not.toHaveBeenCalled();
  });

  it('forwards a ref, so a caller can focus it', () => {
    function Probe(): React.ReactElement {
      const ref = useRef<HTMLButtonElement>(null);
      return (
        <>
          <Button ref={ref}>Save</Button>
          <button onClick={() => ref.current?.focus()}>focus it</button>
        </>
      );
    }
    render(<Probe />);
    fireEvent.click(screen.getByText('focus it'));
    expect(screen.getByRole('button', { name: 'Save' })).toHaveFocus();
  });

  it('passes arbitrary button attributes through', () => {
    render(<Button aria-expanded>Menu</Button>);
    expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'true');
  });
});

describe('TextInput', () => {
  it('announces invalidity rather than only colouring it', () => {
    // A red border tells a sighted user and nobody else.
    render(<TextInput invalid aria-label="Email" />);
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true');
  });

  it('is not marked invalid when it is not', () => {
    render(<TextInput aria-label="Email" />);
    expect(screen.getByLabelText('Email')).not.toHaveAttribute('aria-invalid');
  });

  it('stays controlled', () => {
    function Probe(): React.ReactElement {
      const [value, setValue] = useState('');
      return (
        <TextInput aria-label="Name" value={value} onChange={(e) => setValue(e.target.value)} />
      );
    }
    render(<Probe />);
    const input = screen.getByLabelText('Name');
    fireEvent.change(input, { target: { value: 'otter' } });
    expect(input).toHaveValue('otter');
  });
});

describe('Select', () => {
  const OPTIONS = [
    { value: 'a', label: 'Alpha' },
    { value: 'b', label: 'Beta', disabled: true },
  ];

  it('renders its options and honours disabled ones', () => {
    render(<Select aria-label="Pick" options={OPTIONS} />);
    expect(screen.getByRole('option', { name: 'Alpha' })).toBeEnabled();
    expect(screen.getByRole('option', { name: 'Beta' })).toBeDisabled();
  });

  it('renders a placeholder that cannot be chosen', () => {
    render(<Select aria-label="Pick" options={OPTIONS} placeholder="Choose one" />);
    expect(screen.getByRole('option', { name: 'Choose one' })).toBeDisabled();
  });
});

describe('Switch', () => {
  it('is a switch, not a checkbox', () => {
    // A screen reader then says "on/off" rather than "checked", which is what
    // the control means.
    render(<Switch aria-label="Dark mode" />);
    expect(screen.getByRole('switch')).toBeInTheDocument();
  });

  it('toggles when controlled', () => {
    function Probe(): React.ReactElement {
      const [on, setOn] = useState(false);
      return <Switch aria-label="Dark mode" checked={on} onChange={() => setOn(!on)} />;
    }
    render(<Probe />);
    const control = screen.getByRole('switch');
    expect(control).not.toBeChecked();
    fireEvent.click(control);
    expect(control).toBeChecked();
  });
});

describe('Checkbox', () => {
  it('sets the indeterminate state, which has no attribute', () => {
    render(<Checkbox indeterminate aria-label="All" />);
    expect((screen.getByLabelText('All') as HTMLInputElement).indeterminate).toBe(true);
  });

  it('clears indeterminate when it goes false', () => {
    const view = render(<Checkbox indeterminate aria-label="All" />);
    view.rerender(<Checkbox indeterminate={false} aria-label="All" />);
    expect((screen.getByLabelText('All') as HTMLInputElement).indeterminate).toBe(false);
  });

  it('links a label without needing an id', () => {
    // The source required an id and silently produced an unlinked label
    // without one, so clicking the text did nothing.
    render(<Checkbox label="Include drafts" />);
    fireEvent.click(screen.getByText('Include drafts'));
    expect(screen.getByRole('checkbox')).toBeChecked();
  });

  it('still forwards a ref through its own', () => {
    // It keeps an internal ref for the indeterminate write; a naive
    // implementation drops the caller's.
    const seen: HTMLInputElement[] = [];
    render(
      <Checkbox
        aria-label="One"
        ref={(node) => {
          if (node !== null) seen.push(node);
        }}
      />,
    );
    expect(seen).toHaveLength(1);
  });
});

describe('Field', () => {
  it('wires the label to the control it was given', () => {
    render(
      <Field label="Email">
        <TextInput />
      </Field>,
    );
    expect(screen.getByLabelText('Email')).toBeInTheDocument();
  });

  it('describes the control with its hint', () => {
    // So a screen reader reads the guidance as part of the field rather than
    // as loose text somewhere nearby. The source rendered all three elements
    // with none of the attributes joining them.
    render(
      <Field label="Email" hint="We never share it.">
        <TextInput />
      </Field>,
    );
    expect(screen.getByLabelText('Email')).toHaveAccessibleDescription('We never share it.');
  });

  it('an error replaces the hint and marks the control invalid', () => {
    render(
      <Field label="Email" hint="We never share it." error="That is not an address.">
        <TextInput />
      </Field>,
    );
    const input = screen.getByLabelText('Email');
    expect(input).toHaveAccessibleDescription('That is not an address.');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(screen.queryByText('We never share it.')).not.toBeInTheDocument();
  });

  it('keeps an id the caller gave', () => {
    render(
      <Field label="Email">
        <TextInput id="chosen" />
      </Field>,
    );
    expect(screen.getByLabelText('Email')).toHaveAttribute('id', 'chosen');
  });

  it('gives two fields on one page distinct ids', () => {
    render(
      <>
        <Field label="First">
          <TextInput />
        </Field>
        <Field label="Second">
          <TextInput />
        </Field>
      </>,
    );
    expect(screen.getByLabelText('First').id).not.toBe(screen.getByLabelText('Second').id);
  });
});

describe('StatusDot', () => {
  it('is decoration when it has no label', () => {
    // A bare coloured dot says nothing to anything that does not render colour.
    const { container } = render(<StatusDot tone="ok" />);
    expect(container.firstElementChild).toHaveAttribute('aria-hidden', 'true');
  });

  it('is an image with alt text when it has one', () => {
    render(<StatusDot tone="crit" label="Failing" />);
    expect(screen.getByRole('img', { name: 'Failing' })).toBeInTheDocument();
  });
});

describe('Spinner and Skeleton', () => {
  it('a spinner announces itself by default', () => {
    render(<Spinner />);
    expect(screen.getByRole('status', { name: 'Loading' })).toBeInTheDocument();
  });

  it('an empty label makes it decorative', () => {
    const { container } = render(<Spinner label="" />);
    expect(container.firstElementChild).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('a skeleton is always hidden from assistive tech', () => {
    const { container } = render(<Skeleton />);
    expect(container.firstElementChild).toHaveAttribute('aria-hidden', 'true');
  });
});

describe('Toast', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('interrupts for a problem and waits for anything else', () => {
    // A warning that needs acting on earns the interruption; a confirmation
    // does not.
    const view = render(<Toast message="Saved" tone="ok" />);
    expect(screen.getByRole('status')).toBeInTheDocument();
    view.rerender(<Toast message="Failed" tone="crit" />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('stays put when given no duration', () => {
    const onDismiss = vi.fn();
    render(<Toast message="Saved" onDismiss={onDismiss} />);
    act(() => void vi.advanceTimersByTime(60_000));
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it('dismisses itself on a timer, saying why', () => {
    const onDismiss = vi.fn();
    render(<Toast message="Saved" duration={4000} onDismiss={onDismiss} />);
    act(() => void vi.advanceTimersByTime(4000));
    expect(onDismiss).toHaveBeenCalledWith('timeout');
  });

  it('a re-rendering parent does not restart the countdown', () => {
    // The callback lives in a ref for this reason: taking it as a dependency
    // restarts the timer on every fresh arrow, and a toast whose parent
    // renders often never dismisses itself.
    const onDismiss = vi.fn();
    const view = render(<Toast message="Saved" duration={4000} onDismiss={() => onDismiss()} />);
    act(() => void vi.advanceTimersByTime(3000));
    view.rerender(<Toast message="Saved" duration={4000} onDismiss={() => onDismiss()} />);
    act(() => void vi.advanceTimersByTime(1000));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('the close button is reachable and says what it does', () => {
    const onDismiss = vi.fn();
    render(<Toast message="Saved" onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onDismiss).toHaveBeenCalledWith('explicit');
  });

  it('has no close button when there is nothing to call', () => {
    render(<Toast message="Saved" />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('does not fire after unmounting', () => {
    const onDismiss = vi.fn();
    const view = render(<Toast message="Saved" duration={4000} onDismiss={onDismiss} />);
    view.unmount();
    act(() => void vi.advanceTimersByTime(8000));
    expect(onDismiss).not.toHaveBeenCalled();
  });
});

describe('Tag and Kbd', () => {
  it('a tag renders its content', () => {
    render(<Tag tone="warn">beta</Tag>);
    expect(screen.getByText('beta')).toBeInTheDocument();
  });

  it('a keycap is a kbd element, because that is what the element is for', () => {
    const { container } = render(<Kbd>⌘K</Kbd>);
    expect(container.querySelector('kbd')).toBeInTheDocument();
  });
});

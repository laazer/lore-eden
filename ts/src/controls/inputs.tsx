/**
 * Form controls: a button, a text input, a select, a switch, a checkbox.
 *
 * Extracted from loremaker's `liquid-glass/input`, whose **behaviour** is worth
 * having — controlled/uncontrolled handling done properly, an indeterminate
 * checkbox driven through a ref because the DOM offers no attribute for it,
 * disabled semantics, labels wired to their control.
 *
 * Its **styling** was not extracted, because there was none: the source emits
 * class names that no stylesheet in that application defines. See
 * `controls.css`, which is written rather than copied.
 *
 * Every control forwards its ref. A component library that does not is one a
 * caller cannot focus, measure, or scroll to, and the omission only surfaces
 * once somebody needs it.
 */

import React, { forwardRef, useEffect, useRef } from 'react';

import './controls.css';

export type ButtonVariant = 'primary' | 'ghost' | 'danger';
export type ControlSize = 'sm' | 'md';

const classes = (...parts: (string | false | undefined)[]): string =>
  parts.filter(Boolean).join(' ');

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ControlSize;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size, className, type = 'button', ...rest },
  ref,
) {
  // `type` defaults to "button". HTML defaults it to "submit", so a button
  // inside a form that omits it submits — which is a bug that looks like a
  // routing problem and is found by nobody until the form has one.
  return (
    <button
      {...rest}
      ref={ref}
      type={type}
      className={classes('le-btn', `le-btn--${variant}`, size === 'sm' && 'le-btn--sm', className)}
    />
  );
});

export interface TextInputProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> {
  invalid?: boolean;
}

export const TextInput = forwardRef<HTMLInputElement, TextInputProps>(function TextInput(
  { invalid, className, type = 'text', 'aria-invalid': ariaInvalid, ...rest },
  ref,
) {
  // Either source counts. Written as a bare attribute after the spread, the
  // component's own value silently overwrote one a parent had set — so a
  // `Field` reporting an error produced a control that announced nothing.
  // Composition failures look like this: both halves correct alone.
  const marked = invalid === true || ariaInvalid === true || ariaInvalid === 'true';

  return (
    <input
      {...rest}
      ref={ref}
      type={type}
      // Announced, not merely coloured: a red border tells a sighted user and
      // nobody else.
      aria-invalid={marked || undefined}
      className={classes('le-input', marked && 'le-input--invalid', className)}
    />
  );
});

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  options: readonly SelectOption[];
  placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { options, placeholder, className, ...rest },
  ref,
) {
  return (
    <select {...rest} ref={ref} className={classes('le-select', className)}>
      {placeholder !== undefined && (
        <option value="" disabled>
          {placeholder}
        </option>
      )}
      {options.map((option) => (
        <option key={option.value} value={option.value} disabled={option.disabled}>
          {option.label}
        </option>
      ))}
    </select>
  );
});

export interface SwitchProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size' | 'type'> {
  size?: ControlSize;
}

export const Switch = forwardRef<HTMLInputElement, SwitchProps>(function Switch(
  { size, className, ...rest },
  ref,
) {
  return (
    <input
      {...rest}
      ref={ref}
      type="checkbox"
      // `role="switch"` rather than a styled checkbox alone: a screen reader
      // says "on/off" instead of "checked", which is what the control means.
      role="switch"
      className={classes('le-switch', size === 'sm' && 'le-switch--sm', className)}
    />
  );
});

export interface CheckboxProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: React.ReactNode;
  /** Neither checked nor unchecked. Set through the DOM; there is no attribute. */
  indeterminate?: boolean;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, indeterminate, className, id, ...rest },
  ref,
) {
  const own = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const node = own.current;
    if (node !== null) node.indeterminate = indeterminate ?? false;
  }, [indeterminate]);

  const attach = (node: HTMLInputElement | null): void => {
    own.current = node;
    if (typeof ref === 'function') ref(node);
    else if (ref !== null) ref.current = node;
  };

  const input = (
    <input
      {...rest}
      ref={attach}
      id={id}
      type="checkbox"
      className={classes('le-checkbox', className)}
    />
  );

  if (label === undefined) return input;

  // Wrapped in the label rather than paired by `htmlFor`, so clicking the text
  // toggles the box even when no id was given. The source required an id and
  // silently produced an unlinked label without one.
  return (
    <label className="le-checkbox-row">
      {input}
      <span>{label}</span>
    </label>
  );
});

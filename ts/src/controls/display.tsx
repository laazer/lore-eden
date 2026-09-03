/**
 * The small display pieces: a labelled field, a tag, a keycap, a status dot,
 * a skeleton.
 *
 * From loremaker's `liquid-glass/foundation`. Same story as `inputs.tsx` — the
 * markup and the semantics came across, the styling is new because the source
 * had none.
 */

import React, { forwardRef, useId } from 'react';

import './controls.css';

export type Tone = 'neutral' | 'ok' | 'warn' | 'crit';

const classes = (...parts: (string | false | undefined)[]): string =>
  parts.filter(Boolean).join(' ');

const toneClass = (base: string, tone: Tone): string | false =>
  tone !== 'neutral' && `${base}--${tone}`;

/**
 * What {@link Field} needs to be able to set on its control.
 *
 * Named rather than left as `any`: React 19 types `element.props` as `unknown`,
 * and the cast that silences that would also silence a caller passing something
 * that cannot take an id.
 */
export interface FieldControlProps {
  id?: string;
  'aria-describedby'?: string;
  'aria-invalid'?: boolean | 'true' | 'false';
}

export interface FieldProps {
  label: React.ReactNode;
  /** Guidance below the control. Rendered as the error when `error` is set. */
  hint?: React.ReactNode;
  error?: React.ReactNode;
  children: React.ReactElement<FieldControlProps>;
  className?: string;
}

/**
 * A label, a control, and a hint — wired together.
 *
 * The wiring is the point. The control gets an id, the label points at it, and
 * the hint is referenced by `aria-describedby`, so a screen reader reads the
 * guidance as part of the field rather than as loose text somewhere nearby.
 * The source rendered the same three elements with none of those attributes.
 */
export function Field({ label, hint, error, children, className }: FieldProps): React.ReactElement {
  const generated = useId();
  const controlId = children.props.id ?? generated;
  const messageId = `${controlId}-message`;
  const message = error ?? hint;

  return (
    <div className={classes('le-field', className)}>
      <label className="le-field__label" htmlFor={controlId}>
        {label}
      </label>
      {React.cloneElement<FieldControlProps>(children, {
        id: controlId,
        'aria-describedby': message !== undefined ? messageId : undefined,
        'aria-invalid': error !== undefined ? true : children.props['aria-invalid'],
      })}
      {message !== undefined && (
        <span
          id={messageId}
          className={classes('le-field__hint', error !== undefined && 'le-field__hint--error')}
        >
          {message}
        </span>
      )}
    </div>
  );
}

export interface TagProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
}

export const Tag = forwardRef<HTMLSpanElement, TagProps>(function Tag(
  { tone = 'neutral', className, ...rest },
  ref,
) {
  return <span {...rest} ref={ref} className={classes('le-tag', toneClass('le-tag', tone), className)} />;
});

export interface KbdProps extends React.HTMLAttributes<HTMLElement> {}

/** A keycap. `<kbd>` because that is what the element is for. */
export const Kbd = forwardRef<HTMLElement, KbdProps>(function Kbd({ className, ...rest }, ref) {
  return <kbd {...rest} ref={ref} className={classes('le-kbd', className)} />;
});

export interface StatusDotProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
  /** What the colour means. Without it the dot says nothing to a screen reader. */
  label?: string;
}

export const StatusDot = forwardRef<HTMLSpanElement, StatusDotProps>(function StatusDot(
  { tone = 'neutral', label, className, ...rest },
  ref,
) {
  return (
    <span
      {...rest}
      ref={ref}
      // A bare coloured dot is invisible to anything that does not render
      // colour. With a label it is an image with alt text; without one it is
      // decoration and should be skipped rather than announced as nothing.
      role={label !== undefined ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label === undefined || undefined}
      className={classes('le-dot', toneClass('le-dot', tone), className)}
    />
  );
});

export interface SkeletonProps extends React.HTMLAttributes<HTMLDivElement> {
  width?: string | number;
  height?: string | number;
}

export const Skeleton = forwardRef<HTMLDivElement, SkeletonProps>(function Skeleton(
  { width = '100%', height = '1rem', className, style, ...rest },
  ref,
) {
  return (
    <div
      {...rest}
      ref={ref}
      aria-hidden="true"
      className={classes('le-skeleton', className)}
      style={{ width, height, ...style }}
    />
  );
});

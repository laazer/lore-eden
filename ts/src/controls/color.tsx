/**
 * A colour control: a hex field, a swatch, a gradient direction selector, and
 * the picker that puts them together.
 *
 * From blobert's asset editor (`components/ColorPicker/`). Its value is a
 * discriminated union, which is what was worth taking — the mode and its data
 * are one object, so they cannot disagree.
 *
 * What changed on the way, each with a test that fails against the source:
 *
 * - **The picker has no separate mode prop.** The source took `mode` *and*
 *   `value`, and rendered a panel only when the two agreed, so pressing
 *   "Gradient" while the value was still a single colour showed an empty picker
 *   until the parent had built a gradient value itself. Here the mode is
 *   `value.type`, and switching emits a value of the new type — the one last
 *   seen in that mode, or one converted from the current colour.
 * - **Copy writes only a whole colour.** The source's copy button wrote
 *   `#${hex.toUpperCase()}` of whatever was in the field, so a half-typed `ff`
 *   went to the clipboard as `#FF` — and a whole one as upper case, unlike the
 *   `#rrggbb` its own clipboard helper writes.
 * - **Blur never invents a colour.** The source sanitized by deleting non-hex
 *   characters, so pasting `color: #ff0000` and leaving the field stored
 *   `cff000` — the `c` of "color" plus five digits — and `12, 34, 56` stored
 *   `123456`. Blur now keeps a hex only when the text is one or plainly holds
 *   one (`findHexInText`); anything else clears the field, which is how a
 *   partial value was already settled, and an empty value renders as "No
 *   colour" rather than as a colour. Blur emits only when that changes the
 *   value.
 * - **A pending paste lands on the current value.** The clipboard read is
 *   async; the picker builds the new value from the latest one when it
 *   resolves, so a direction chosen meanwhile is kept.
 * - **Mode memory follows the parent.** When the parent replaces the value
 *   (not by echoing one the picker emitted), what each mode last held is
 *   forgotten, so a tab converts from the colour on screen, not an older one.
 * - **An empty or partial value renders as empty, not black.** `#${color ||
 *   "000000"}` shows black for "nobody chose", and `#fff` of a half-typed
 *   `fff` shows white; either reads as a chosen colour.
 * - **Every label is wired.** Field labels were loose `<div>`s and the direction
 *   buttons were named "→", "↓" and "◯".
 *
 * Styling is `color.css`, on tokens only. The source's was a
 * `colorPickerStyles` object of literal hex (`#3c3c3c`, `#0e639c`), so it stayed
 * dark when the app went light. The one colour a component here sets inline is
 * the *user's*, on the swatch.
 *
 * The source's image mode is not here: it cropped atlases against that app's
 * asset service, and the uv-rect maths it used is a Blender convention.
 */

import React, { useEffect, useId, useRef } from 'react';

import { copyHexToClipboard, findHexInText, hexForColorInput, isHex6, readHexFromClipboard } from './colorHex';
import { Field } from './display';
import { Button, TextInput } from './inputs';
import './color.css';

export type GradientDirection = 'horizontal' | 'vertical' | 'radial';

/** A colour is six lowercase hex digits with no `#`, e.g. `"34d77f"`. */
export type ColorPickerValue =
  | { type: 'single'; color: string }
  | { type: 'gradient'; colorA: string; colorB: string; direction: GradientDirection };

export type ColorPickerMode = ColorPickerValue['type'];

const classes = (...parts: (string | false | undefined)[]): string =>
  parts.filter(Boolean).join(' ');

/** Text for a field; a malformed value renders as empty rather than crashing. */
const asText = (raw: unknown): string => (typeof raw === 'string' ? raw : '');

// ── ColorSwatch ───────────────────────────────────────────────────────

export interface ColorSwatchProps {
  /** Six hex digits, no `#`. Anything else renders as an empty swatch. */
  color: string;
  label?: string;
  disabled?: boolean;
  onClick?: () => void;
}

export function ColorSwatch({ color, label, disabled = false, onClick }: ColorSwatchProps): React.ReactElement {
  const valid = isHex6(color);
  const name = valid ? `#${color.toLowerCase()}` : 'No colour';
  const chip = (
    <span
      role="img"
      aria-label={name}
      className={classes('le-color-swatch', !valid && 'le-color-swatch--empty', disabled && 'le-color-swatch--disabled')}
      style={valid ? { backgroundColor: `#${color}` } : undefined}
    />
  );
  const labelled = (
    <>
      {label !== undefined && <span className="le-color-label">{label}</span>}
      {chip}
    </>
  );

  if (onClick !== undefined && !disabled) {
    return (
      <Button variant="ghost" size="sm" className="le-color-swatch-btn" onClick={onClick}>
        {labelled}
      </Button>
    );
  }
  return <span className="le-color-swatch-row">{labelled}</span>;
}

// ── HexInput ──────────────────────────────────────────────────────────

export interface HexInputProps {
  /** Six hex digits, no `#`; partial while the user types. */
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  label?: string;
  /** Show Copy and Paste. On by default. */
  clipboard?: boolean;
  /** Told whether a copy reached the clipboard, so a host can say so. */
  onCopy?: (ok: boolean) => void;
}

export function HexInput({
  value,
  onChange,
  disabled = false,
  label,
  clipboard = true,
  onCopy,
}: HexInputProps): React.ReactElement {
  const text = asText(value);
  // The clipboard read is async: a paste calls whichever `onChange` is current
  // when it resolves, not the one captured when Paste was pressed.
  const latestOnChange = useRef(onChange);
  latestOnChange.current = onChange;

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>): void => {
    // Partial input is allowed while typing; blur is where it is settled.
    onChange(e.target.value.replace(/^#/, '').toLowerCase());
  };
  const handleBlur = (e: React.FocusEvent<HTMLInputElement>): void => {
    // A hex the text is, or plainly holds; otherwise empty — never digits
    // gathered from text that was not a colour.
    const settled = findHexInText(e.target.value) ?? '';
    if (settled !== text) onChange(settled);
  };
  const copy = async (): Promise<void> => {
    const ok = await copyHexToClipboard(text);
    onCopy?.(ok);
  };
  const paste = async (): Promise<void> => {
    const hex = await readHexFromClipboard();
    if (hex !== null) latestOnChange.current(hex);
  };

  const field = (
    <TextInput
      className="le-color-hex"
      placeholder="RRGGBB"
      value={text}
      onChange={handleChange}
      onBlur={handleBlur}
      disabled={disabled}
      spellCheck={false}
      aria-label={label === undefined ? 'Hex colour' : undefined}
    />
  );

  return (
    <div className="le-color-row">
      <input
        type="color"
        className="le-color-native"
        value={hexForColorInput(text)}
        onChange={(e) => onChange(e.target.value.replace(/^#/, '').toLowerCase())}
        disabled={disabled}
        title={label === undefined ? 'Pick color' : `${label} picker`}
      />
      {label !== undefined ? (
        <Field label={label} className="le-color-field">
          {field}
        </Field>
      ) : (
        field
      )}
      {clipboard && (
        <span className="le-color-actions">
          <Button
            variant="ghost"
            size="sm"
            title="Copy hex to clipboard"
            disabled={disabled || !isHex6(text)}
            onClick={() => void copy()}
          >
            Copy
          </Button>
          <Button variant="ghost" size="sm" title="Paste hex from clipboard" disabled={disabled} onClick={() => void paste()}>
            Paste
          </Button>
        </span>
      )}
    </div>
  );
}

// ── DirectionSelector ─────────────────────────────────────────────────

const DIRECTIONS: readonly { value: GradientDirection; name: string; glyph: string }[] = [
  { value: 'horizontal', name: 'Horizontal', glyph: '→' },
  { value: 'vertical', name: 'Vertical', glyph: '↓' },
  { value: 'radial', name: 'Radial', glyph: '◯' },
];

export interface DirectionSelectorProps {
  direction: GradientDirection;
  onChange: (direction: GradientDirection) => void;
  disabled?: boolean;
}

export function DirectionSelector({ direction, onChange, disabled = false }: DirectionSelectorProps): React.ReactElement {
  return (
    <div className="le-color-row">
      <span className="le-color-label">Direction</span>
      <div className="le-color-segments" role="group" aria-label="Gradient direction">
        {DIRECTIONS.map((d) => (
          <Button
            key={d.value}
            variant="ghost"
            size="sm"
            disabled={disabled}
            aria-pressed={direction === d.value}
            aria-label={d.name}
            onClick={() => onChange(d.value)}
          >
            <span aria-hidden="true">{d.glyph}</span>
          </Button>
        ))}
      </div>
    </div>
  );
}

// ── ColorPicker ───────────────────────────────────────────────────────

const MODE_LABELS: Record<ColorPickerMode, string> = { single: 'Color', gradient: 'Gradient' };
const ALL_MODES: readonly ColorPickerMode[] = ['single', 'gradient'];

type Remembered = { [M in ColorPickerMode]?: Extract<ColorPickerValue, { type: M }> };

/** A value of `target`, carrying the current colour across where it can. */
/** Same mode and same data — a parent's echo of an emitted value, even if copied. */
function sameValue(a: ColorPickerValue | null, b: ColorPickerValue): boolean {
  if (a === null || a.type !== b.type) return false;
  if (a.type === 'single' && b.type === 'single') return a.color === b.color;
  if (a.type === 'gradient' && b.type === 'gradient') {
    return a.colorA === b.colorA && a.colorB === b.colorB && a.direction === b.direction;
  }
  return false;
}

function convert(value: ColorPickerValue, target: ColorPickerMode): ColorPickerValue {
  const base = value.type === 'gradient' ? asText(value.colorA) : value.type === 'single' ? asText(value.color) : '';
  if (target === 'single') return { type: 'single', color: base };
  return { type: 'gradient', colorA: base, colorB: base, direction: 'horizontal' };
}

export interface ColorPickerProps {
  value: ColorPickerValue;
  onChange: (value: ColorPickerValue) => void;
  /**
   * Modes offered as tabs. With one, the tabs are hidden. The picker always
   * shows `value.type`: a value whose mode is not listed renders in that mode,
   * with no tab for it (and with one listed mode, no tabs at all).
   */
  modes?: readonly ColorPickerMode[];
  disabled?: boolean;
  label?: string;
  onCopy?: (ok: boolean) => void;
}

export function ColorPicker({
  value,
  onChange,
  modes = ALL_MODES,
  disabled = false,
  label,
  onCopy,
}: ColorPickerProps): React.ReactElement {
  // What each mode last held, so Color → Gradient → Color returns the colour
  // the user left rather than one converted back from the gradient.
  const remembered = useRef<Remembered>({});
  const emitted = useRef<ColorPickerValue | null>(null);
  // Handlers read the value and callback of the latest render, so a paste that
  // resolves after a direction change builds on that change.
  const latest = useRef({ value, onChange });
  latest.current = { value, onChange };
  const labelId = useId();

  useEffect(() => {
    // A value the picker did not emit came from the parent; what the modes
    // held before it no longer describes what is on screen.
    if (!sameValue(emitted.current, value)) remembered.current = {};
  }, [value]);

  const emit = (next: ColorPickerValue): void => {
    emitted.current = next;
    latest.current.onChange(next);
  };

  const switchTo = (target: ColorPickerMode): void => {
    const current = latest.current.value;
    if (target === current.type) return;
    if (current.type === 'single') remembered.current.single = current;
    else if (current.type === 'gradient') remembered.current.gradient = current;
    emit(remembered.current[target] ?? convert(current, target));
  };

  const updateGradient = (patch: Partial<Extract<ColorPickerValue, { type: 'gradient' }>>): void => {
    const current = latest.current.value;
    // A paste that resolves after the user switched to single mode has no
    // gradient left to land on.
    if (current.type !== 'gradient') return;
    emit({
      type: 'gradient',
      colorA: patch.colorA ?? current.colorA,
      colorB: patch.colorB ?? current.colorB,
      direction: patch.direction ?? current.direction,
    });
  };

  return (
    <div className="le-color-picker" role="group" aria-labelledby={label !== undefined ? labelId : undefined}>
      {label !== undefined && (
        <span id={labelId} className="le-color-label">
          {label}
        </span>
      )}
      {modes.length > 1 && (
        <div className="le-color-segments" role="group" aria-label="Color picker mode">
          {modes.map((m) => (
            <Button
              key={m}
              variant="ghost"
              size="sm"
              disabled={disabled}
              aria-pressed={value.type === m}
              onClick={() => switchTo(m)}
            >
              {MODE_LABELS[m]}
            </Button>
          ))}
        </div>
      )}
      {value.type === 'single' && (
        <HexInput
          label="Color"
          value={value.color}
          onChange={(color) => {
            if (latest.current.value.type === 'single') emit({ type: 'single', color });
          }}
          disabled={disabled}
          onCopy={onCopy}
        />
      )}
      {value.type === 'gradient' && (
        <>
          <HexInput
            label="From Color"
            value={value.colorA}
            onChange={(colorA) => updateGradient({ colorA })}
            disabled={disabled}
            onCopy={onCopy}
          />
          <HexInput
            label="To Color"
            value={value.colorB}
            onChange={(colorB) => updateGradient({ colorB })}
            disabled={disabled}
            onCopy={onCopy}
          />
          <DirectionSelector
            direction={value.direction}
            onChange={(direction) => updateGradient({ direction })}
            disabled={disabled}
          />
        </>
      )}
    </div>
  );
}

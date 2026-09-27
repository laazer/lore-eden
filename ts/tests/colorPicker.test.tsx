import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import React, { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ColorPicker,
  ColorSwatch,
  DirectionSelector,
  HexInput,
  type ColorPickerValue,
} from '../src/controls';
import { tokenSpecs } from '../src/tokens/specs';

/**
 * The colour control. Carried from blobert's `components/ColorPicker/` tests —
 * `HexInput`, `ColorSwatch`, `DirectionSelector`, `SingleColorMode`,
 * `GradientMode`, `ColorPickerUniversal` and its adversarial suite — adapted
 * where the API changed (no `mode`/`onModeChange`; the mode components are the
 * picker in that mode). Image-mode cases stayed behind with image mode.
 *
 * Dropped: one assertion that a disabled swatch carried `opacity` in its inline
 * style. That is a style string, which this kit's tests do not assert.
 *
 * The final `describe` is new: the defects the extraction exposed, each failing
 * against the source.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const stubClipboard = (readText = ''): { write: ReturnType<typeof vi.fn> } => {
  const write = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', {
    clipboard: { writeText: write, readText: vi.fn().mockResolvedValue(readText) },
  });
  return { write };
};

const single = (color: string): ColorPickerValue => ({ type: 'single', color });
const gradient = (colorA = 'ff0000', colorB = '0000ff'): ColorPickerValue => ({
  type: 'gradient',
  colorA,
  colorB,
  direction: 'horizontal',
});

describe('HexInput', () => {
  it('renders hex input with current value', () => {
    render(<HexInput value="ff0000" onChange={vi.fn()} />);
    expect((screen.getByPlaceholderText('RRGGBB') as HTMLInputElement).value).toBe('ff0000');
  });

  it('calls onChange when hex text input changes', () => {
    const onChange = vi.fn();
    render(<HexInput value="ff0000" onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('RRGGBB'), { target: { value: '00ff00' } });
    expect(onChange).toHaveBeenCalledWith('00ff00');
  });

  it('sanitizes hex value on blur', () => {
    const onChange = vi.fn();
    render(<HexInput value="gg0000" onChange={onChange} />);
    fireEvent.blur(screen.getByPlaceholderText('RRGGBB'));
    expect(onChange).toHaveBeenCalledWith('');
  });

  it('accepts hex with # prefix and removes it', () => {
    const onChange = vi.fn();
    render(<HexInput value="ff0000" onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('RRGGBB'), { target: { value: '#00ff00' } });
    expect(onChange).toHaveBeenCalledWith('00ff00');
  });

  it('converts input to lowercase', () => {
    const onChange = vi.fn();
    render(<HexInput value="ff0000" onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('RRGGBB'), { target: { value: 'FF00FF' } });
    expect(onChange).toHaveBeenCalledWith('ff00ff');
  });

  it('renders a native colour input', () => {
    render(<HexInput value="ff0000" onChange={vi.fn()} />);
    const colorInput = document.querySelector('input[type="color"]') as HTMLInputElement;
    expect(colorInput).toBeInTheDocument();
    expect(colorInput).toHaveAttribute('title', 'Pick color');
    expect(colorInput.value).toBe('#ff0000');
  });

  it('disables all inputs when disabled prop is true', () => {
    render(<HexInput value="ff0000" onChange={vi.fn()} disabled />);
    expect((screen.getByPlaceholderText('RRGGBB') as HTMLInputElement).disabled).toBe(true);
    for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled();
  });

  it('renders label when provided', () => {
    render(<HexInput value="ff0000" onChange={vi.fn()} label="Primary Color" />);
    expect(screen.getByText('Primary Color')).toBeInTheDocument();
  });

  it('shows a copy button', () => {
    render(<HexInput value="ff0000" onChange={vi.fn()} />);
    expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument();
  });

  it('copies the value when the copy button is clicked', async () => {
    const { write } = stubClipboard();
    const onCopy = vi.fn();
    render(<HexInput value="ff0000" onChange={vi.fn()} onCopy={onCopy} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /copy/i }));
    });
    expect(write).toHaveBeenCalledWith('#ff0000');
    expect(onCopy).toHaveBeenCalledWith(true);
  });

  it('updates when value changes', () => {
    const onChange = vi.fn();
    const { rerender } = render(<HexInput value="ff0000" onChange={onChange} />);
    rerender(<HexInput value="00ff00" onChange={onChange} />);
    expect((screen.getByPlaceholderText('RRGGBB') as HTMLInputElement).value).toBe('00ff00');
  });

  it('pastes a hex recovered from surrounding text', async () => {
    stubClipboard('🎨 #C0FFEE');
    const onChange = vi.fn();
    render(<HexInput value="ff0000" onChange={onChange} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /paste/i }));
    });
    expect(onChange).toHaveBeenCalledWith('c0ffee');
  });

  it('leaves the value alone when the clipboard holds no colour', async () => {
    stubClipboard('not a colour');
    const onChange = vi.fn();
    render(<HexInput value="ff0000" onChange={onChange} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /paste/i }));
    });
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('ColorSwatch', () => {
  it('renders the colour as its background', () => {
    const { container } = render(<ColorSwatch color="ff0000" />);
    const swatch = container.querySelector("[style*='background-color']");
    expect(swatch?.getAttribute('style')).toContain('rgb(255, 0, 0)');
  });

  it('renders label when provided', () => {
    render(<ColorSwatch color="ff0000" label="Body Color" />);
    expect(screen.getByText('Body Color')).toBeInTheDocument();
  });

  it('is clickable when onClick is provided', () => {
    const onClick = vi.fn();
    render(<ColorSwatch color="ff0000" onClick={onClick} />);
    fireEvent.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalled();
  });

  it('is not clickable when disabled', () => {
    render(<ColorSwatch color="ff0000" onClick={vi.fn()} disabled />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('handles various hex colours', () => {
    const { rerender, container } = render(<ColorSwatch color="00ff00" />);
    expect(container.querySelector("[style*='background-color']")?.getAttribute('style')).toContain('rgb(0, 255, 0)');
    rerender(<ColorSwatch color="0000ff" />);
    expect(container.querySelector("[style*='background-color']")?.getAttribute('style')).toContain('rgb(0, 0, 255)');
  });
});

describe('DirectionSelector', () => {
  const buttonsOf = (): HTMLElement[] =>
    within(screen.getByRole('group', { name: 'Gradient direction' })).getAllByRole('button');

  it('renders three direction buttons', () => {
    render(<DirectionSelector direction="horizontal" onChange={vi.fn()} />);
    expect(buttonsOf()).toHaveLength(3);
  });

  it('marks current direction as pressed', () => {
    render(<DirectionSelector direction="vertical" onChange={vi.fn()} />);
    const buttons = buttonsOf();
    expect(buttons[1]).toHaveAttribute('aria-pressed', 'true');
    expect(buttons[0]).toHaveAttribute('aria-pressed', 'false');
    expect(buttons[2]).toHaveAttribute('aria-pressed', 'false');
  });

  it('calls onChange when a direction button is clicked', () => {
    const onChange = vi.fn();
    render(<DirectionSelector direction="horizontal" onChange={onChange} />);
    fireEvent.click(buttonsOf()[1]);
    expect(onChange).toHaveBeenCalledWith('vertical');
  });

  it('renders direction label', () => {
    render(<DirectionSelector direction="horizontal" onChange={vi.fn()} />);
    expect(screen.getByText('Direction')).toBeInTheDocument();
  });

  it('disables all buttons when disabled', () => {
    render(<DirectionSelector direction="horizontal" onChange={vi.fn()} disabled />);
    for (const button of buttonsOf()) expect(button).toBeDisabled();
  });

  it('updates pressed state when direction changes', () => {
    const { rerender } = render(<DirectionSelector direction="horizontal" onChange={vi.fn()} />);
    expect(buttonsOf()[0]).toHaveAttribute('aria-pressed', 'true');
    rerender(<DirectionSelector direction="radial" onChange={vi.fn()} />);
    expect(buttonsOf()[2]).toHaveAttribute('aria-pressed', 'true');
    expect(buttonsOf()[0]).toHaveAttribute('aria-pressed', 'false');
  });

  it('renders direction symbols', () => {
    const { container } = render(<DirectionSelector direction="horizontal" onChange={vi.fn()} />);
    expect(container.textContent).toContain('→');
    expect(container.textContent).toContain('↓');
    expect(container.textContent).toContain('◯');
  });
});

describe('ColorPicker in single mode', () => {
  it('renders the hex field with the current colour', () => {
    render(<ColorPicker value={single('00ff00')} onChange={vi.fn()} />);
    expect((screen.getByPlaceholderText('RRGGBB') as HTMLInputElement).value).toBe('00ff00');
  });

  it('marks the Color tab pressed', () => {
    render(<ColorPicker value={single('ff0000')} onChange={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Color', pressed: true })).toBeInTheDocument();
  });

  it('emits a single value when the hex text changes', () => {
    const onChange = vi.fn();
    render(<ColorPicker value={single('ff0000')} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('RRGGBB'), { target: { value: '00ff00' } });
    expect(onChange).toHaveBeenCalledWith({ type: 'single', color: '00ff00' });
  });

  it('renders copy button', () => {
    render(<ColorPicker value={single('ff0000')} onChange={vi.fn()} />);
    expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument();
  });

  it('disables every control when disabled', () => {
    render(<ColorPicker value={single('ff0000')} onChange={vi.fn()} disabled />);
    expect((screen.getByPlaceholderText('RRGGBB') as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Color' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Gradient' })).toBeDisabled();
  });

  it('renders the picker label', () => {
    render(<ColorPicker value={single('ff0000')} onChange={vi.fn()} label="Pick a color" />);
    expect(screen.getByRole('group', { name: 'Pick a color' })).toBeInTheDocument();
  });

  it('hides the mode tabs when only one mode is offered', () => {
    render(<ColorPicker value={single('ff0000')} onChange={vi.fn()} modes={['single']} />);
    expect(screen.queryByRole('group', { name: /color picker mode/i })).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('RRGGBB')).toBeInTheDocument();
  });
});

describe('ColorPicker in gradient mode', () => {
  it('renders two hex fields with the gradient colours', () => {
    render(<ColorPicker value={gradient('ff0000', '00ff00')} onChange={vi.fn()} />);
    const inputs = screen.getAllByPlaceholderText('RRGGBB') as HTMLInputElement[];
    expect(inputs.map((i) => i.value)).toEqual(['ff0000', '00ff00']);
  });

  it('emits the whole gradient when either colour changes', () => {
    const onChange = vi.fn();
    render(<ColorPicker value={gradient()} onChange={onChange} />);
    const inputs = screen.getAllByPlaceholderText('RRGGBB');
    fireEvent.change(inputs[0], { target: { value: 'ffff00' } });
    expect(onChange).toHaveBeenLastCalledWith({ ...gradient(), colorA: 'ffff00' });
    fireEvent.change(inputs[1], { target: { value: '00ff00' } });
    expect(onChange).toHaveBeenLastCalledWith({ ...gradient(), colorB: '00ff00' });
  });

  it('emits the gradient with a new direction', () => {
    const onChange = vi.fn();
    render(<ColorPicker value={gradient()} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Vertical' }));
    expect(onChange).toHaveBeenCalledWith({ ...gradient(), direction: 'vertical' });
  });

  it('labels both colours and the direction', () => {
    render(<ColorPicker value={gradient()} onChange={vi.fn()} />);
    expect(screen.getByText('From Color')).toBeInTheDocument();
    expect(screen.getByText('To Color')).toBeInTheDocument();
    expect(screen.getByText('Direction')).toBeInTheDocument();
  });

  it('disables both fields when disabled', () => {
    render(<ColorPicker value={gradient()} onChange={vi.fn()} disabled />);
    for (const input of screen.getAllByPlaceholderText('RRGGBB')) expect(input).toBeDisabled();
  });

  it('shows copy buttons for both colours', () => {
    render(<ColorPicker value={gradient()} onChange={vi.fn()} />);
    expect(screen.getAllByRole('button', { name: /copy/i })).toHaveLength(2);
  });
});

describe('ColorPicker switching modes', () => {
  function Harness({ initial }: { initial: ColorPickerValue }): React.ReactElement {
    const [value, setValue] = useState(initial);
    return <ColorPicker value={value} onChange={setValue} />;
  }

  const clickMode = (name: string): void => {
    const modes = screen.getByRole('group', { name: /color picker mode/i });
    fireEvent.click(within(modes).getByRole('button', { name }));
  };

  it('preserves the inactive mode value across a round trip', () => {
    // The source's version of this test needed the parent to keep one state per
    // mode. Holding one value is enough here.
    render(<Harness initial={single('ff0000')} />);
    clickMode('Gradient');
    const [a, b] = screen.getAllByPlaceholderText('RRGGBB');
    fireEvent.change(a, { target: { value: '111111' } });
    fireEvent.change(b, { target: { value: '222222' } });
    clickMode('Color');
    expect((screen.getByPlaceholderText('RRGGBB') as HTMLInputElement).value).toBe('ff0000');
    clickMode('Gradient');
    const values = (screen.getAllByPlaceholderText('RRGGBB') as HTMLInputElement[]).map((i) => i.value);
    expect(values).toEqual(['111111', '222222']);
  });

  it('does not emit when the active mode is pressed again', () => {
    const onChange = vi.fn();
    render(<ColorPicker value={single('ff0000')} onChange={onChange} />);
    clickMode('Color');
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('ColorPicker — malformed values at runtime', () => {
  // From the source's adversarial suite: a value that slipped past the types
  // (parsed JSON, a stale store) must not crash the picker.
  it.each([
    ['missing color', { type: 'single' }],
    ['null color', { type: 'single', color: null }],
    ['numeric color', { type: 'single', color: 16711680 }],
  ])('renders the single field with %s', (_what, value) => {
    render(<ColorPicker value={value as unknown as ColorPickerValue} onChange={vi.fn()} />);
    expect((screen.getByPlaceholderText('RRGGBB') as HTMLInputElement).value).toBe('');
  });

  it('renders the gradient with missing fields', () => {
    const value = { type: 'gradient', colorA: null } as unknown as ColorPickerValue;
    render(<ColorPicker value={value} onChange={vi.fn()} />);
    expect(screen.getAllByPlaceholderText('RRGGBB')).toHaveLength(2);
  });

  it.each([
    ['missing', { color: 'ff0000' }],
    ['a wrong string', { type: 'color', color: 'ff0000' }],
    ['null', { type: null, color: 'ff0000' }],
    ['a number', { type: 1, color: 'ff0000' }],
  ])('does not throw when the type tag is %s', (_what, value) => {
    expect(() =>
      render(<ColorPicker value={value as unknown as ColorPickerValue} onChange={vi.fn()} />),
    ).not.toThrow();
  });

  it('emits a well-formed value from a malformed one', () => {
    const onChange = vi.fn();
    render(<ColorPicker value={{ type: 'color' } as unknown as ColorPickerValue} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Gradient' }));
    expect(onChange).toHaveBeenCalledWith({ type: 'gradient', colorA: '', colorB: '', direction: 'horizontal' });
  });
});

describe('ColorPickerValue — the mode and its data cannot disagree', () => {
  it('rejects an impossible pairing at compile time', () => {
    // `tsc --noEmit` covers this file; each directive fails the typecheck if
    // the line under it ever compiles.
    // @ts-expect-error a single colour has no colorA
    const a: ColorPickerValue = { type: 'single', colorA: 'ff0000' };
    // @ts-expect-error a gradient needs both colours and a direction
    const b: ColorPickerValue = { type: 'gradient', color: 'ff0000' };
    // @ts-expect-error there is no image mode here
    const c: ColorPickerValue = { type: 'image', file: null };
    expect([a, b, c]).toHaveLength(3);
  });

  it('narrows exhaustively on type', () => {
    const describeValue = (v: ColorPickerValue): string => {
      switch (v.type) {
        case 'single':
          return v.color;
        case 'gradient':
          return `${v.colorA}-${v.colorB}-${v.direction}`;
        default: {
          const unreachable: never = v;
          return unreachable;
        }
      }
    };
    expect(describeValue(single('ff0000'))).toBe('ff0000');
    expect(describeValue(gradient())).toBe('ff0000-0000ff-horizontal');
  });
});

describe('color.css follows the theme', () => {
  // jsdom applies no imported stylesheet, so the evidence that the control
  // follows the theme is the sheet itself: nothing it sets is a fixed colour,
  // and every custom property it reads is one the token table defines — which
  // is what the light overrides are written against.
  const sheet = readFileSync(resolve(__dirname, '..', 'src', 'controls', 'color.css'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '');

  it('contains no literal colour', () => {
    expect(sheet).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(/);
  });

  it('reads only tokens the table defines', () => {
    const defined = new Set(Object.values(tokenSpecs).map((spec) => spec.css));
    const used = [...sheet.matchAll(/var\((--[a-z0-9-]+)\)/g)].map((m) => m[1]);
    expect(used.length).toBeGreaterThan(0);
    expect(used.filter((name) => !defined.has(name))).toEqual([]);
  });
});

describe('defects the extraction exposed', () => {
  it('switching mode emits a value of the new mode, converted from the current colour', () => {
    // Source: the tab only called `onModeChange`; the value stayed a single
    // colour, and the picker rendered no panel at all until the parent built a
    // gradient itself.
    const onChange = vi.fn();
    render(<ColorPicker value={single('34d77f')} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Gradient' }));
    expect(onChange).toHaveBeenCalledWith({
      type: 'gradient',
      colorA: '34d77f',
      colorB: '34d77f',
      direction: 'horizontal',
    });
  });

  it('copy writes nothing for a half-typed value, and lower case for a whole one', async () => {
    // Source: `#${hex.toUpperCase()}` of the field — `#FF` for a partial,
    // `#FF0000` for a whole one.
    const { write } = stubClipboard();
    const { rerender } = render(<ColorPicker value={single('ff')} onChange={vi.fn()} />);
    expect(screen.getByRole('button', { name: /copy/i })).toBeDisabled();
    rerender(<ColorPicker value={single('ff0000')} onChange={vi.fn()} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /copy/i }));
    });
    expect(write).toHaveBeenCalledWith('#ff0000');
  });

  it('blur recovers the hex from pasted CSS instead of stripping it into another colour', () => {
    // Source: sanitize alone, so `color: #ff0000` became `cff000`.
    const onChange = vi.fn();
    render(<HexInput value="color: #ff0000" onChange={onChange} />);
    fireEvent.blur(screen.getByPlaceholderText('RRGGBB'));
    expect(onChange).toHaveBeenCalledWith('ff0000');
  });

  it.each(['', 'fff'])('an empty or partial value (%j) renders no colour', (color) => {
    // Source: black for empty, and white for a half-typed `fff`.
    const { container } = render(<ColorSwatch color={color} />);
    expect(container.querySelector("[style*='background-color']")).toBeNull();
    expect(screen.getByRole('img', { name: 'No colour' })).toBeInTheDocument();
  });

  it('labels are wired to their controls', () => {
    // Source: loose <div> labels, and direction buttons named by their glyph.
    render(<ColorPicker value={gradient()} onChange={vi.fn()} />);
    expect(screen.getByLabelText('From Color')).toHaveValue('ff0000');
    expect(screen.getByRole('button', { name: 'Radial' })).toBeInTheDocument();
  });
});

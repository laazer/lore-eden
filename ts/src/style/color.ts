/**
 * Colour as a value: parse it, convert it, lighten it, and ask what text is
 * readable on top of it.
 *
 * A thin, immutable wrapper over `tinycolor2` — a ~15KB dependency that already
 * knows every CSS colour format, and whose arithmetic the source's palette was
 * tuned against. Reimplementing it costs correctness for nothing: writing the
 * conversions by hand landed two silent off-by-ones against this library, one
 * of which turns on `Math.round(-25.5)` being `-25` rather than `-26`.
 *
 * What this file adds over the library:
 *
 * - **Immutability.** `tinycolor2`'s `brighten`/`darken` mutate the receiver and
 *   return it. The source inherited that under a fluent API, so two callers
 *   holding one colour changed it under each other and `darkenHex` left its
 *   input darkened. Every derivation here returns a new instance.
 * - **A parse failure that is a failure.** `tinycolor2` answers an invalid
 *   colour with a valid-looking black. A typo'd colour name then renders as
 *   though somebody chose black. {@link parseColor} returns `undefined` and the
 *   constructor throws.
 * - **Types.** `HexString`/`RgbString`/`ColorString` template-literal types, so
 *   a border prop can ask for a colour rather than for a string.
 */

import tinycolor from 'tinycolor2';

export type HexPair = string;

export type HexString =
  | `#${HexPair}${HexPair}${HexPair}`
  | `#${HexPair}${HexPair}${HexPair}${HexPair}`;

export type RgbaString = `rgba(${number},${number},${number},${number})`;
export type RgbString = `rgb(${number},${number},${number})` | RgbaString;
export type ColorString = RgbString | HexString;

export type ColorFormat = 'rgb' | 'hsl' | 'hsv' | 'hex';

export type RGBColor = tinycolor.ColorFormats.RGBA;
export type HSLColor = tinycolor.ColorFormats.HSLA;
export type HSVColor = tinycolor.ColorFormats.HSVA;

/** Anything a colour can be built from — every format `tinycolor2` accepts. */
export type ColorInput = tinycolor.ColorInput | EasyColor;

export class ColorParseError extends Error {
  constructor(readonly input: string) {
    super(`Not a colour: ${JSON.stringify(input)}`);
    this.name = 'ColorParseError';
  }
}

const HEX = /^#?[0-9a-f]+$/i;

function instanceFrom(input: ColorInput): tinycolor.Instance {
  return tinycolor(input instanceof EasyColor ? input.rgb : input);
}

/** Parse a colour, or `undefined` if it is not one. */
export function parseColor(input: ColorInput): RGBColor | undefined {
  const parsed = instanceFrom(input);
  return parsed.isValid() ? parsed.toRgb() : undefined;
}

/**
 * Whether a string is a hex colour.
 *
 * Hex specifically — a named colour is a valid colour and not a valid hex. The
 * source's version additionally rejected the 4- and 8-digit alpha forms that
 * its own parser accepted, so a colour could be invalid and usable at once.
 */
export function isValidHex(hex: string): boolean {
  const text = hex.trim();
  if (text.toLowerCase() === 'transparent') return true;
  return HEX.test(text) && tinycolor(text).isValid();
}

export function rgbToHsl(rgb: RGBColor): HSLColor {
  return tinycolor(rgb).toHsl();
}

export function hslToRgb(hsl: HSLColor): RGBColor {
  return tinycolor(hsl).toRgb();
}

export function rgbToHsv(rgb: RGBColor): HSVColor {
  return tinycolor(rgb).toHsv();
}

export function hsvToRgb(hsv: HSVColor): RGBColor {
  return tinycolor(hsv).toRgb();
}

/**
 * An immutable colour.
 *
 * Holds its channels rather than a library instance, so there is nothing
 * mutable to hand out; each derivation builds a fresh instance to compute with
 * and wraps the result.
 */
export class EasyColor {
  readonly rgb: RGBColor;
  /** The format this colour arrived in, if the caller said. Purely informational. */
  readonly source?: ColorFormat;

  constructor(input: ColorInput, source?: ColorFormat) {
    const parsed = parseColor(input);
    if (parsed === undefined) throw new ColorParseError(String(input));
    this.rgb = parsed;
    this.source = source;
  }

  /** `undefined` instead of throwing, for input you have not vetted. */
  static from(input: ColorInput, source?: ColorFormat): EasyColor | undefined {
    return parseColor(input) === undefined ? undefined : new EasyColor(input, source);
  }

  /** A throwaway instance to compute with. Never escapes, so its mutability is harmless. */
  private get scratch(): tinycolor.Instance {
    return tinycolor(this.rgb);
  }

  get hsl(): HSLColor {
    return this.scratch.toHsl();
  }

  get hsv(): HSVColor {
    return this.scratch.toHsv();
  }

  get isTransparent(): boolean {
    return this.rgb.a === 0;
  }

  /** `#rrggbb`, or `#rrggbbaa` when partly transparent. */
  get hex(): string {
    // `toHex8String` scales alpha by 255. The source scaled by 256 in its own
    // getter, so an alpha of 0.999 rounded to 256 and produced a
    // nine-character string no browser accepts.
    return this.rgb.a < 1 ? this.scratch.toHex8String() : this.scratch.toHexString();
  }

  toHex(): string {
    return this.hex;
  }

  toRgb(): RGBColor {
    return { ...this.rgb };
  }

  toHsl(): HSLColor {
    return this.hsl;
  }

  toHsv(): HSVColor {
    return this.hsv;
  }

  /** Matches the {@link RgbString} type — no spaces, unlike the library's own. */
  toRgbString(): string {
    const { r, g, b, a } = this.rgb;
    return a < 1 ? `rgba(${r},${g},${b},${a})` : `rgb(${r},${g},${b})`;
  }

  toString(): string {
    return this.hex;
  }

  /** Lighter, by adding to each RGB channel. */
  brighter(amount = 10): EasyColor {
    return new EasyColor(this.scratch.brighten(amount).toRgb());
  }

  /** Darker, by reducing HSL lightness. Not the inverse of {@link brighter}. */
  darken(amount = 10): EasyColor {
    return new EasyColor(this.scratch.darken(amount).toRgb());
  }

  /**
   * Black or white, whichever is readable on this colour.
   *
   * YIQ luma. A transparent colour has nothing to contrast against, so it
   * answers with a soft black — whatever is behind it is unknown, and dark text
   * on an unknown background is the safer of the two guesses.
   */
  contrastingColor(): EasyColor {
    if (this.isTransparent) return new EasyColor('rgba(0,0,0,0.4)');
    const { r, g, b } = this.rgb;
    const yiq = (r * 299 + g * 587 + b * 114) / 1000;
    return new EasyColor(yiq >= 128 ? '#000' : '#fff', 'hex');
  }

  static darkenHex(hex: string, amount = 1): string {
    return new EasyColor(hex, 'hex').darken(amount).toHex();
  }

  static brighterHex(hex: string, amount = 1): string {
    return new EasyColor(hex, 'hex').brighter(amount).toHex();
  }

  static contrastingHex(hex?: string): string {
    if (hex === undefined || hex === '') return '#ffffff';
    return new EasyColor(hex, 'hex').contrastingColor().toHex();
  }
}

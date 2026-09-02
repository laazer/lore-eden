/**
 * Colour as a value: parse it, convert it, lighten it, and ask what text is
 * readable on top of it.
 *
 * ## Why this does not wrap a colour library
 *
 * The source wrapped `tinycolor2`, and used six of its methods. A library
 * consumed by one application can afford a dependency for six methods; a
 * shared library cannot as easily — every consumer inherits it, including the
 * ones that only wanted the `ColorString` *type*. The conversions below are
 * the standard ones and are pinned by tests against known values, so the
 * trade is ~200 lines here against a transitive dependency everywhere.
 *
 * The lighten and darken maths deliberately match `tinycolor2`'s, since that
 * is what the source's colours were tuned against:
 *
 * - `brighten(n)` adds `round(255 · n/100)` to each RGB channel. Additive, so
 *   it moves greys as readily as saturated colours.
 * - `darken(n)` subtracts `n/100` from HSL lightness. Multiplicative in
 *   effect, so it preserves hue and saturation.
 *
 * They are not inverses, and that asymmetry is inherited on purpose rather
 * than tidied — tidying it would shift every colour the source derived.
 *
 * ## What parses
 *
 * `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`, `rgb(...)`, `rgba(...)`, and the
 * keyword `transparent`. Not named CSS colours, and not `hsl()` strings.
 * Anything else is a parse *failure* — {@link parseColor} returns `undefined`
 * and the {@link EasyColor} constructor throws. The source's library returned
 * an "invalid" colour that stringified to black, which is the silent-failure
 * shape: a typo'd colour name renders as if somebody chose black.
 */

export type HexPair = string;

export type HexString =
  | `#${HexPair}${HexPair}${HexPair}`
  | `#${HexPair}${HexPair}${HexPair}${HexPair}`;

export type RgbaString = `rgba(${number},${number},${number},${number})`;
export type RgbString = `rgb(${number},${number},${number})` | RgbaString;
export type ColorString = RgbString | HexString;

export type ColorFormat = 'rgb' | 'hsl' | 'hsv' | 'hex';

export interface RGBColor {
  r: number;
  g: number;
  b: number;
  a: number;
}

export interface HSLColor {
  h: number;
  s: number;
  l: number;
  a: number;
}

export interface HSVColor {
  h: number;
  s: number;
  v: number;
  a: number;
}

/** Anything {@link EasyColor} can be built from. */
export type ColorInput = string | RGBColor | HSLColor | HSVColor | EasyColor;

export class ColorParseError extends Error {
  constructor(readonly input: string) {
    super(`Not a colour this module understands: ${JSON.stringify(input)}`);
    this.name = 'ColorParseError';
  }
}

const TRANSPARENT: RGBColor = { r: 0, g: 0, b: 0, a: 0 };

const HEX = /^#?([0-9a-f]{3,8})$/i;
const RGB_FUNC = /^rgba?\(\s*([^)]+)\)$/i;

const clamp = (value: number, min: number, max: number): number =>
  Math.max(min, Math.min(max, value));

const clamp01 = (value: number): number => clamp(value, 0, 1);

const byte = (value: number): number => clamp(Math.round(value), 0, 255);

function parseHex(body: string): RGBColor | undefined {
  const expand = (pair: string): number => parseInt(pair.length === 1 ? pair + pair : pair, 16);
  const chunk = (size: number): string[] =>
    Array.from({ length: body.length / size }, (_unused, i) => body.slice(i * size, (i + 1) * size));

  // 3 and 4 are the shorthand forms, one character per channel; 6 and 8 the
  // long ones. 5 and 7 are neither, and are a typo rather than a colour.
  if (body.length === 3 || body.length === 4) {
    const [r, g, b, a] = chunk(1).map(expand);
    return { r, g, b, a: a === undefined ? 1 : a / 255 };
  }
  if (body.length === 6 || body.length === 8) {
    const [r, g, b, a] = chunk(2).map(expand);
    return { r, g, b, a: a === undefined ? 1 : a / 255 };
  }
  return undefined;
}

function parseRgbFunction(body: string): RGBColor | undefined {
  const parts = body
    .split(/[\s,/]+/)
    .filter((part) => part !== '')
    .map((part) => (part.endsWith('%') ? (Number(part.slice(0, -1)) / 100) * 255 : Number(part)));
  if (parts.length < 3 || parts.length > 4 || parts.some((n) => !Number.isFinite(n))) {
    return undefined;
  }
  const [r, g, b, a] = parts;
  // Alpha is a fraction, so the percent scaling applied above has to come back
  // off: `rgba(0,0,0,50%)` is half-transparent, not 127× opaque.
  return { r: byte(r), g: byte(g), b: byte(b), a: a === undefined ? 1 : clamp01(a > 1 ? a / 255 : a) };
}

/** Parse a colour, or `undefined` if it is not one this module understands. */
export function parseColor(input: ColorInput): RGBColor | undefined {
  if (input instanceof EasyColor) return input.rgb;
  if (typeof input === 'object' && input !== null) {
    if ('r' in input) return { ...input, a: input.a ?? 1 };
    if ('l' in input) return hslToRgb(input);
    if ('v' in input) return hsvToRgb(input);
    return undefined;
  }
  const text = input.trim().toLowerCase();
  if (text === 'transparent') return { ...TRANSPARENT };
  const hex = HEX.exec(text);
  if (hex !== null) return parseHex(hex[1]);
  const rgb = RGB_FUNC.exec(text);
  if (rgb !== null) return parseRgbFunction(rgb[1]);
  return undefined;
}

/**
 * Whether a string is a hex colour this module accepts.
 *
 * The source rejected the 4- and 8-digit alpha forms here while its parser
 * accepted them, so a colour could be simultaneously invalid and usable.
 * This agrees with {@link parseColor} by construction.
 */
export function isValidHex(hex: string): boolean {
  if (hex.trim().toLowerCase() === 'transparent') return true;
  const match = HEX.exec(hex.trim());
  return match !== null && parseHex(match[1]) !== undefined;
}

export function rgbToHsl({ r, g, b, a }: RGBColor): HSLColor {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  if (max === min) return { h: 0, s: 0, l, a };
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  if (max === rn) h = (gn - bn) / d + (gn < bn ? 6 : 0);
  else if (max === gn) h = (bn - rn) / d + 2;
  else h = (rn - gn) / d + 4;
  return { h: h * 60, s, l, a };
}

export function hslToRgb({ h, s, l, a }: HSLColor): RGBColor {
  const alpha = a ?? 1;
  if (s === 0) {
    const grey = byte(l * 255);
    return { r: grey, g: grey, b: grey, a: alpha };
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const hue = (((h % 360) + 360) % 360) / 360;
  const channel = (t: number): number => {
    const shifted = ((t % 1) + 1) % 1;
    if (shifted < 1 / 6) return p + (q - p) * 6 * shifted;
    if (shifted < 1 / 2) return q;
    if (shifted < 2 / 3) return p + (q - p) * (2 / 3 - shifted) * 6;
    return p;
  };
  return {
    r: byte(channel(hue + 1 / 3) * 255),
    g: byte(channel(hue) * 255),
    b: byte(channel(hue - 1 / 3) * 255),
    a: alpha,
  };
}

export function rgbToHsv({ r, g, b, a }: RGBColor): HSVColor {
  const { h } = rgbToHsl({ r, g, b, a });
  const max = Math.max(r, g, b) / 255;
  const min = Math.min(r, g, b) / 255;
  return { h, s: max === 0 ? 0 : (max - min) / max, v: max, a };
}

export function hsvToRgb({ h, s, v, a }: HSVColor): RGBColor {
  const l = ((2 - s) * v) / 2;
  const sl = l === 0 || l === 1 ? 0 : (v - l) / Math.min(l, 1 - l);
  return hslToRgb({ h, s: clamp01(sl), l, a: a ?? 1 });
}

const hexPair = (value: number): string => byte(value).toString(16).padStart(2, '0');

/**
 * An immutable colour.
 *
 * Every derivation returns a new instance. The source's `brighter` and
 * `darken` mutated the wrapped colour and returned `this`, which reads like a
 * fluent immutable API and is not one — two callers holding the same colour
 * would change it under each other, and `darkenHex` left its input darkened.
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

  get hsl(): HSLColor {
    return rgbToHsl(this.rgb);
  }

  get hsv(): HSVColor {
    return rgbToHsv(this.rgb);
  }

  get isTransparent(): boolean {
    return this.rgb.a === 0;
  }

  /**
   * `#rrggbb`, or `#rrggbbaa` when partly transparent.
   *
   * Alpha scales by 255, not 256. The source used 256, so an alpha of 0.999
   * rounded to `256` and produced a nine-character hex string that no browser
   * accepts.
   */
  get hex(): string {
    const base = `#${hexPair(this.rgb.r)}${hexPair(this.rgb.g)}${hexPair(this.rgb.b)}`;
    return this.rgb.a < 1 ? `${base}${hexPair(this.rgb.a * 255)}` : base;
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

  toRgbString(): string {
    const { r, g, b, a } = this.rgb;
    return a < 1 ? `rgba(${r},${g},${b},${a})` : `rgb(${r},${g},${b})`;
  }

  toString(): string {
    return this.hex;
  }

  /** Lighter by adding to each RGB channel. See the note at the top of the file. */
  brighter(amount = 10): EasyColor {
    const step = Math.round(255 * (amount / 100));
    const { r, g, b, a } = this.rgb;
    return new EasyColor({ r: byte(r + step), g: byte(g + step), b: byte(b + step), a });
  }

  /** Darker by reducing HSL lightness. See the note at the top of the file. */
  darken(amount = 10): EasyColor {
    const hsl = this.hsl;
    return new EasyColor({ ...hsl, l: clamp01(hsl.l - amount / 100) });
  }

  /**
   * Black or white, whichever is readable on this colour.
   *
   * YIQ luma, the same weighting the source used. A transparent colour has
   * nothing to contrast against, so it answers with a soft black — whatever is
   * behind it is unknown, and dark text on an unknown background is the safer
   * of the two guesses.
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

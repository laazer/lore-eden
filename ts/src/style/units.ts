/**
 * A CSS length as data: a number and a unit, with arithmetic that refuses to
 * add pixels to percentages.
 *
 * The point of the type is that `"12px"` and `12` and `"50%"` stop being
 * strings you concatenate and start being values you can do sums on. The unit
 * check is what earns it: adding a `%` to a `px` is a question with no answer,
 * and a system that quietly picks one produces a layout that is wrong in a way
 * nobody can see.
 *
 * ## Parsing is one regex, not a loop over unit names
 *
 * The source searched by `String.split(unit)` over a list of unit names, which
 * had three problems that a regex simply does not have:
 *
 * - `split` looks *anywhere* in the string, so `"2rem"` matched `em` and
 *   parsed as `Number("2r")` — `NaN`, silently.
 * - The loop used `return` inside `forEach` intending to break. `forEach`
 *   ignores it, so the search was last-match-wins where first-match was meant.
 * - `rem` and `vw` were missing from the list entirely, so both parsed to `NaN`.
 */

/** The units this module understands. */
export type UnitOfMeasurement = 'px' | 'em' | 'rem' | 'vh' | 'vw' | '%';

export const UNITS: readonly UnitOfMeasurement[] = ['px', 'em', 'rem', 'vh', 'vw', '%'];

/** What a bare number means. */
export const DEFAULT_UNIT: UnitOfMeasurement = 'px';

/** A length written as a string, or a bare number meaning {@link DEFAULT_UNIT}. */
export type CssStyleUnit = `${number}${UnitOfMeasurement}` | number;

export interface ParsedUnit {
  value: number;
  unit: UnitOfMeasurement;
}

/** Thrown when arithmetic is asked to combine two different units. */
export class UnitMismatchError extends Error {
  constructor(
    readonly left: UnitOfMeasurement,
    readonly right: UnitOfMeasurement,
  ) {
    super(`Cannot combine ${left} with ${right}: style units must share a unit of measurement.`);
    this.name = 'UnitMismatchError';
  }
}

const LENGTH = /^\s*([+-]?(?:\d+\.?\d*|\.\d+))\s*([a-z%]*)\s*$/i;

function isUnit(candidate: string): candidate is UnitOfMeasurement {
  return (UNITS as readonly string[]).includes(candidate);
}

/**
 * Split a length into its number and unit, or `undefined` if it is not one.
 *
 * A bare number, or a string of digits with no unit, takes {@link DEFAULT_UNIT}
 * — that is what a bare number means everywhere else in CSS-in-JS.
 */
export function parseStyleUnit(input?: CssStyleUnit | string | null): ParsedUnit | undefined {
  if (input === undefined || input === null || input === '') return undefined;
  if (typeof input === 'number') {
    return Number.isFinite(input) ? { value: input, unit: DEFAULT_UNIT } : undefined;
  }
  const match = LENGTH.exec(input);
  if (match === null) return undefined;
  const [, digits, suffix] = match;
  if (suffix === '') return { value: Number(digits), unit: DEFAULT_UNIT };
  const unit = suffix.toLowerCase();
  if (!isUnit(unit)) return undefined;
  return { value: Number(digits), unit };
}

/**
 * The numeric part of a length.
 *
 * Absent is `0`; present-but-unparseable is `NaN`. The two are kept apart
 * deliberately — "nobody set this" and "somebody set this to nonsense" want
 * different reactions, and the source returned `NaN` for the string `"0"`,
 * which is neither.
 */
export function unitNumber(input?: CssStyleUnit | string | null): number {
  if (input === undefined || input === null || input === '') return 0;
  return parseStyleUnit(input)?.value ?? NaN;
}

/** The unit of a length, or `undefined` if it is not a length. */
export function unitOf(input?: CssStyleUnit | string | null): UnitOfMeasurement | undefined {
  return parseStyleUnit(input)?.unit;
}

/**
 * A length as a CSS string, defaulting a bare number to pixels.
 *
 * A value already carrying a unit is returned untouched. The source appended
 * `px` to anything its number check accepted, and its number check accepted
 * `"50%"` — so `px("50%")` returned `"50%px"`.
 */
export function px(value?: CssStyleUnit | string | null): string | undefined {
  const parsed = parseStyleUnit(value);
  if (parsed === undefined) return undefined;
  return `${parsed.value}${parsed.unit}`;
}

/** True when the value is absent or measures zero, whatever its unit. */
export function isZeroLength(value?: CssStyleUnit | string | null): boolean {
  return unitNumber(value) === 0;
}

/**
 * An immutable length.
 *
 * Every operation returns a new instance, so a `StyleUnit` handed to two
 * callers cannot be changed underneath either of them.
 */
export class StyleUnit {
  readonly value: number;
  readonly unit: UnitOfMeasurement;

  constructor(input?: CssStyleUnit | string | null, unit?: UnitOfMeasurement) {
    const parsed = parseStyleUnit(input);
    // An explicit unit wins over a parsed one, so `new StyleUnit('12px', '%')`
    // is 12%. A string whose unit was not given keeps its own — the source
    // dropped it and every string became px.
    this.value = parsed?.value ?? 0;
    this.unit = unit ?? parsed?.unit ?? DEFAULT_UNIT;
  }

  get asNumber(): number {
    return this.value;
  }

  get asString(): string {
    return `${this.value}${this.unit}`;
  }

  toString(): string {
    return this.asString;
  }

  private sameUnit(other: StyleUnit): number {
    if (other.unit !== this.unit) throw new UnitMismatchError(this.unit, other.unit);
    return other.value;
  }

  plus(amount: number): StyleUnit {
    return new StyleUnit(this.value + amount, this.unit);
  }

  minus(amount: number): StyleUnit {
    return new StyleUnit(this.value - amount, this.unit);
  }

  mult(factor: number): StyleUnit {
    return new StyleUnit(this.value * factor, this.unit);
  }

  div(divisor: number): StyleUnit {
    return new StyleUnit(this.value / divisor, this.unit);
  }

  plusU(other: StyleUnit): StyleUnit {
    return this.plus(this.sameUnit(other));
  }

  minusU(other: StyleUnit): StyleUnit {
    return this.minus(this.sameUnit(other));
  }

  // No `multU`/`divU`. The source had them, and they validated that both sides
  // shared a unit and then returned that same unit — so 100px × 2px came back
  // as 200px when the honest answer is 200px², and 100px ÷ 2px came back as
  // 50px when the honest answer is the unitless 50. Scaling by a plain number
  // is the operation that actually means something; that is `mult` and `div`.

  /** The same unit, a different number. */
  mapUnit(value: number): StyleUnit {
    return new StyleUnit(value, this.unit);
  }
}

export function styleUnit(input?: CssStyleUnit | string | null, unit?: UnitOfMeasurement): StyleUnit {
  return new StyleUnit(input, unit);
}

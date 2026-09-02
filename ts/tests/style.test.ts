import { describe, expect, it } from 'vitest';

import {
  DEFAULT_UNIT,
  StyleUnit,
  UnitMismatchError,
  isZeroLength,
  parseStyleUnit,
  px,
  styleUnit,
  unitNumber,
  unitOf,
} from '../src/style/units';
import {
  clampCoord,
  coordAbsDelta,
  expandRect,
  rectContains,
  rectContainsCoord,
  rectContainsPosition,
  rectFrom,
  rectOverlap,
  shrinkRect,
  snapToGrid,
  xyCoord,
} from '../src/style/geometry';
import { NO_BORDER, makeBorderStyles } from '../src/style/border';
import { marginFromAnchor, marginFromAnchored } from '../src/style/anchor';
import {
  ColorParseError,
  EasyColor,
  isValidHex,
  hslToRgb,
  parseColor,
  rgbToHsl,
  rgbToHsv,
} from '../src/style/color';

describe('parseStyleUnit', () => {
  it('reads a number and a unit', () => {
    expect(parseStyleUnit('12px')).toEqual({ value: 12, unit: 'px' });
    expect(parseStyleUnit('50%')).toEqual({ value: 50, unit: '%' });
    expect(parseStyleUnit('-1.5em')).toEqual({ value: -1.5, unit: 'em' });
  });

  it('defaults a bare number to pixels', () => {
    expect(parseStyleUnit(12)).toEqual({ value: 12, unit: DEFAULT_UNIT });
    expect(parseStyleUnit('12')).toEqual({ value: 12, unit: DEFAULT_UNIT });
  });

  it('reads rem and vw, which the source silently turned into NaN', () => {
    // `"2rem".split("em")` matched, so the source parsed `Number("2r")`.
    expect(parseStyleUnit('2rem')).toEqual({ value: 2, unit: 'rem' });
    expect(parseStyleUnit('80vw')).toEqual({ value: 80, unit: 'vw' });
  });

  it('refuses a unit it does not know, rather than guessing', () => {
    expect(parseStyleUnit('12pt')).toBeUndefined();
    expect(parseStyleUnit('auto')).toBeUndefined();
    expect(parseStyleUnit('12px 4px')).toBeUndefined();
  });

  it('treats absent as absent', () => {
    expect(parseStyleUnit(undefined)).toBeUndefined();
    expect(parseStyleUnit('')).toBeUndefined();
  });
});

describe('unitNumber', () => {
  it('separates absent from unparseable', () => {
    expect(unitNumber(undefined)).toBe(0);
    expect(unitNumber('')).toBe(0);
    expect(unitNumber('nonsense')).toBeNaN();
  });

  it('reads zero as zero', () => {
    // The source tested `Number(num)` for truthiness, so the string "0" fell
    // through to a unit search that found none and returned NaN.
    expect(unitNumber('0')).toBe(0);
    expect(unitNumber('0px')).toBe(0);
    expect(unitNumber(0)).toBe(0);
  });

  it('reports the unit separately', () => {
    expect(unitOf('50%')).toBe('%');
    expect(unitOf(4)).toBe('px');
    expect(unitOf('bogus')).toBeUndefined();
  });
});

describe('px', () => {
  it('adds px only to a value that has no unit', () => {
    expect(px(12)).toBe('12px');
    expect(px('12')).toBe('12px');
    expect(px('12px')).toBe('12px');
  });

  it('leaves a percentage alone', () => {
    // The source's number check accepted "50%", so it returned "50%px".
    expect(px('50%')).toBe('50%');
  });

  it('is undefined for nothing', () => {
    expect(px(undefined)).toBeUndefined();
    expect(px('nope')).toBeUndefined();
  });
});

describe('StyleUnit', () => {
  it('keeps the unit a string arrived with', () => {
    // The source only consulted the unit for *numeric* input, so every string
    // became px: `new StyleUnit('100%').asString` was '100px'.
    expect(new StyleUnit('100%').asString).toBe('100%');
    expect(new StyleUnit('3rem').unit).toBe('rem');
  });

  it('lets an explicit unit win', () => {
    expect(new StyleUnit('12px', '%').asString).toBe('12%');
  });

  it('defaults to zero pixels', () => {
    expect(new StyleUnit().asString).toBe('0px');
  });

  it('scales without changing unit', () => {
    expect(styleUnit('10%').mult(3).asString).toBe('30%');
    expect(styleUnit(10).div(4).asString).toBe('2.5px');
    expect(styleUnit('2em').plus(1).asString).toBe('3em');
    expect(styleUnit('2em').minus(5).asString).toBe('-3em');
  });

  it('adds two lengths that share a unit', () => {
    expect(styleUnit('10px').plusU(styleUnit('5px')).asString).toBe('15px');
    expect(styleUnit('10px').minusU(styleUnit('5px')).asString).toBe('5px');
  });

  it('refuses to add lengths that do not share a unit', () => {
    expect(() => styleUnit('10px').plusU(styleUnit('5%'))).toThrow(UnitMismatchError);
  });

  it('is immutable', () => {
    const original = styleUnit('10px');
    const derived = original.plus(5);
    expect(original.asString).toBe('10px');
    expect(derived).not.toBe(original);
  });

  it('maps to a new value on the same unit', () => {
    expect(styleUnit('10%').mapUnit(80).asString).toBe('80%');
  });

  it('reports zero regardless of unit', () => {
    expect(isZeroLength('0%')).toBe(true);
    expect(isZeroLength(undefined)).toBe(true);
    expect(isZeroLength('1px')).toBe(false);
  });
});

describe('geometry', () => {
  const rect = { top: 0, left: 0, bottom: 10, right: 10 };

  it('builds a rect from an origin and a size', () => {
    expect(rectFrom({ x: 2, y: 3 }, { width: 4, height: 6 })).toEqual({
      top: 3,
      bottom: 9,
      left: 2,
      right: 6,
    });
  });

  it('centres when asked', () => {
    expect(rectFrom({ x: 0, y: 0 }, { width: 4, height: 4 }, true)).toEqual({
      top: -2,
      bottom: 2,
      left: -2,
      right: 2,
    });
  });

  it('does not count touching edges as overlap', () => {
    expect(rectOverlap(rect, { top: 0, left: 10, bottom: 10, right: 20 })).toBe(false);
    expect(rectOverlap(rect, { top: 0, left: 9, bottom: 10, right: 20 })).toBe(true);
  });

  it('expands and shrinks symmetrically', () => {
    const grown = expandRect(rect, { x: 1, y: 2 });
    expect(grown).toEqual({ top: -2, bottom: 12, left: -1, right: 11 });
    expect(shrinkRect(grown, { x: 1, y: 2 })).toEqual(rect);
  });

  it('contains points on its edges', () => {
    expect(rectContainsPosition(rect, { x: 0, y: 10 })).toBe(true);
    expect(rectContainsPosition(rect, { x: 11, y: 5 })).toBe(false);
  });

  it('contains a rect, and loosens by a buffer', () => {
    const inner = { top: 1, left: 1, bottom: 9, right: 9 };
    expect(rectContains(rect, inner)).toBe(true);
    const overhanging = { top: -1, left: -1, bottom: 11, right: 11 };
    expect(rectContains(rect, overhanging)).toBe(false);
    expect(rectContains(rect, overhanging, { x: 2, y: 2 })).toBe(true);
  });

  it('tests a coord against an origin and size', () => {
    expect(rectContainsCoord({ x: 1, y: 1 }, { x: 0, y: 0 }, { width: 4, height: 4 })).toBe(true);
    expect(rectContainsCoord({ x: -1, y: 0 }, { x: 0, y: 0 }, { width: 4, height: 4 })).toBe(false);
    expect(rectContainsCoord({ x: -1, y: 0 }, { x: 0, y: 0 }, { width: 4, height: 4 }, true)).toBe(
      true,
    );
  });

  it('snaps and clamps', () => {
    expect(snapToGrid(11, 19, 10)).toEqual([10, 20]);
    expect(clampCoord({ x: -5, y: 500 }, { width: 100, height: 100 })).toEqual({ x: 0, y: 100 });
    expect(coordAbsDelta({ x: 1, y: 1 }, { x: 4, y: -3 })).toEqual({ x: 3, y: 4 });
    expect(xyCoord([1, 2])).toEqual({ x: 1, y: 2 });
  });
});

describe('makeBorderStyles', () => {
  it('emits only the keys it was given', () => {
    expect(makeBorderStyles({ style: 'solid' })).toEqual({ borderStyle: 'solid' });
    expect(makeBorderStyles()).toEqual({});
  });

  it('gives weight a unit', () => {
    // The source looked up the value under the pre-rename key, so `weight`
    // never reached its px conversion while `radius` did.
    expect(makeBorderStyles({ weight: 2 })).toEqual({ borderWidth: '2px' });
    expect(makeBorderStyles({ radius: '50%' })).toEqual({ borderRadius: '50%' });
  });

  it('spells corner properties as CSS spells them', () => {
    // lodash `capitalize` lowercased the tail, producing `borderTopleftRadius`.
    expect(makeBorderStyles({ topLeft: { radius: 4 } })).toEqual({ borderTopLeftRadius: '4px' });
    expect(makeBorderStyles({ bottomRight: { radius: 4 } })).toEqual({
      borderBottomRightRadius: '4px',
    });
  });

  it('puts the side before the property in kebab case', () => {
    // The source emitted `border-width-left`.
    expect(makeBorderStyles({ left: { weight: 1 } }, 'kebab')).toEqual({
      'border-left-width': '1px',
    });
    expect(makeBorderStyles({ topLeft: { radius: 4 } }, 'kebab')).toEqual({
      'border-top-left-radius': '4px',
    });
  });

  it('flattens a base with side overrides', () => {
    expect(makeBorderStyles({ style: 'solid', weight: 2, right: { weight: 0, color: '#fff' } })).toEqual(
      {
        borderStyle: 'solid',
        borderWidth: '2px',
        borderRightWidth: '0px',
        borderRightColor: '#fff',
      },
    );
  });

  it('flattens NO_BORDER', () => {
    expect(makeBorderStyles(NO_BORDER)).toEqual({
      borderColor: 'transparent',
      borderStyle: 'hidden',
      borderWidth: '0px',
      borderRadius: '0px',
    });
  });
});

describe('marginFromAnchor', () => {
  it('pushes away from the side it anchors to', () => {
    expect(marginFromAnchor('left', 'top')).toEqual({ marginRight: 'auto', marginBottom: 'auto' });
    expect(marginFromAnchor('right', 'bottom')).toEqual({ marginLeft: 'auto', marginTop: 'auto' });
  });

  it('centres an axis that was not named', () => {
    expect(marginFromAnchor('left')).toEqual({
      marginRight: 'auto',
      marginTop: 'auto',
      marginBottom: 'auto',
    });
    expect(marginFromAnchor()).toEqual({
      marginLeft: 'auto',
      marginRight: 'auto',
      marginTop: 'auto',
      marginBottom: 'auto',
    });
  });

  it('reads an Anchored object', () => {
    expect(marginFromAnchored({ xAnchor: 'center', yAnchor: 'bottom' })).toEqual(
      marginFromAnchor('center', 'bottom'),
    );
  });
});

describe('parseColor', () => {
  it('reads the hex forms', () => {
    expect(parseColor('#fff')).toEqual({ r: 255, g: 255, b: 255, a: 1 });
    expect(parseColor('#ff0000')).toEqual({ r: 255, g: 0, b: 0, a: 1 });
    expect(parseColor('#00ff0080')?.a).toBeCloseTo(128 / 255, 5);
  });

  it('reads rgb and rgba', () => {
    expect(parseColor('rgb(1,2,3)')).toEqual({ r: 1, g: 2, b: 3, a: 1 });
    expect(parseColor('rgba(1,2,3,0.5)')).toEqual({ r: 1, g: 2, b: 3, a: 0.5 });
  });

  it('reads transparent', () => {
    expect(parseColor('transparent')).toEqual({ r: 0, g: 0, b: 0, a: 0 });
  });

  it('refuses what it does not understand rather than answering black', () => {
    expect(parseColor('rebeccapurple')).toBeUndefined();
    expect(parseColor('#12345')).toBeUndefined();
    expect(parseColor('not a colour')).toBeUndefined();
  });

  it('agrees with isValidHex', () => {
    // The source's validator rejected the 4- and 8-digit alpha forms its own
    // parser accepted, so a colour could be invalid and usable at once.
    for (const hex of ['#fff', '#ffff', '#ffffff', '#ffffffff', 'transparent']) {
      expect(isValidHex(hex)).toBe(true);
      expect(parseColor(hex)).toBeDefined();
    }
    for (const bad of ['#12345', '#gg0000', 'blue']) {
      expect(isValidHex(bad)).toBe(false);
    }
  });
});

describe('colour conversion', () => {
  it('round-trips rgb through hsl', () => {
    for (const rgb of [
      { r: 255, g: 0, b: 0, a: 1 },
      { r: 18, g: 52, b: 86, a: 1 },
      { r: 128, g: 128, b: 128, a: 1 },
      { r: 0, g: 0, b: 0, a: 1 },
      { r: 255, g: 255, b: 255, a: 1 },
    ]) {
      expect(hslToRgb(rgbToHsl(rgb))).toEqual(rgb);
    }
  });

  it('reports known hsl values', () => {
    expect(rgbToHsl({ r: 255, g: 0, b: 0, a: 1 })).toEqual({ h: 0, s: 1, l: 0.5, a: 1 });
    expect(rgbToHsl({ r: 0, g: 0, b: 255, a: 1 })).toEqual({ h: 240, s: 1, l: 0.5, a: 1 });
  });

  it('reports known hsv values', () => {
    const hsv = rgbToHsv({ r: 0, g: 128, b: 0, a: 1 });
    expect(hsv.h).toBe(120);
    expect(hsv.s).toBe(1);
    expect(hsv.v).toBeCloseTo(128 / 255, 5);
  });
});

describe('EasyColor', () => {
  it('throws on input it cannot parse', () => {
    expect(() => new EasyColor('chartreuse')).toThrow(ColorParseError);
    expect(EasyColor.from('chartreuse')).toBeUndefined();
    expect(EasyColor.from('#abc')).toBeInstanceOf(EasyColor);
  });

  it('renders hex, with alpha only when partly transparent', () => {
    expect(new EasyColor('#ff0000').hex).toBe('#ff0000');
    expect(new EasyColor('rgba(255,0,0,0.5)').hex).toBe('#ff000080');
  });

  it('keeps an alpha near one inside eight digits', () => {
    // The source scaled alpha by 256, so 0.999 rounded to 256 and produced a
    // nine-character string: '#ff0000100'.
    const hex = new EasyColor('rgba(255,0,0,0.999)').hex;
    expect(hex).toBe('#ff0000ff');
    expect(hex).toHaveLength(9);
  });

  it('does not mutate when deriving', () => {
    // The source's brighter/darken mutated the wrapped colour and returned
    // `this`, so a shared colour changed under everyone holding it.
    const base = new EasyColor('#808080');
    const lighter = base.brighter(10);
    const darker = base.darken(10);
    expect(base.hex).toBe('#808080');
    expect(lighter).not.toBe(base);
    expect(darker).not.toBe(base);
    expect(lighter.hex).not.toBe(base.hex);
    expect(darker.hex).not.toBe(base.hex);
  });

  it('brightens by adding to each channel', () => {
    // 0x80 = 128, plus round(255 * 0.10) = 26, is 154 = 0x9a.
    expect(new EasyColor('#808080').brighter(10).hex).toBe('#9a9a9a');
    expect(new EasyColor('#ffffff').brighter(50).hex).toBe('#ffffff');
  });

  it('darkens by reducing lightness', () => {
    // #808080 is l ≈ 0.502; less 0.10 is 0.402, which is 0x67.
    expect(new EasyColor('#808080').darken(10).hex).toBe('#676767');
    expect(new EasyColor('#000000').darken(50).hex).toBe('#000000');
  });

  it('leaves its argument untouched in the static helpers', () => {
    expect(EasyColor.darkenHex('#808080', 10)).toBe('#676767');
    expect(EasyColor.brighterHex('#808080', 10)).toBe('#9a9a9a');
  });

  it('picks readable text', () => {
    expect(new EasyColor('#ffffff').contrastingColor().hex).toBe('#000000');
    expect(new EasyColor('#000000').contrastingColor().hex).toBe('#ffffff');
    expect(EasyColor.contrastingHex('#ffffff')).toBe('#000000');
    expect(EasyColor.contrastingHex()).toBe('#ffffff');
  });

  it('answers a soft black for a transparent colour', () => {
    const contrast = new EasyColor('transparent').contrastingColor();
    expect(contrast.rgb).toEqual({ r: 0, g: 0, b: 0, a: 0.4 });
  });

  it('renders an rgb string', () => {
    expect(new EasyColor('#010203').toRgbString()).toBe('rgb(1,2,3)');
    expect(new EasyColor('rgba(1,2,3,0.5)').toRgbString()).toBe('rgba(1,2,3,0.5)');
  });

  it('accepts another EasyColor and hsl/hsv objects', () => {
    const red = new EasyColor('#ff0000');
    expect(new EasyColor(red).hex).toBe('#ff0000');
    expect(new EasyColor({ h: 0, s: 1, l: 0.5, a: 1 }).hex).toBe('#ff0000');
    expect(new EasyColor({ h: 0, s: 1, v: 1, a: 1 }).hex).toBe('#ff0000');
  });
});

/**
 * A border described as structured props, flattened to CSS.
 *
 *     makeBorderStyles({ style: 'solid', weight: 2, right: { weight: 0 } })
 *     // { borderStyle: 'solid', borderWidth: '2px', borderRightWidth: '0px' }
 *
 * `weight` rather than `width` because a border's width is not the element's
 * width and reading `width` in a border object invites the confusion. It maps
 * to `border-width` on the way out.
 *
 * ## Why the property names are a table
 *
 * The source built them by string concatenation with lodash `capitalize`,
 * which lowercases everything after the first letter — so a corner override
 * produced `borderTopleftRadius`, a property that does not exist and that
 * React drops without a word. Its kebab-case branch emitted
 * `border-width-left` where CSS spells it `border-left-width`. Both are the
 * same class of bug: an invented name that no layer downstream validates.
 *
 * A table cannot invent a name. It also cannot silently gain one, because
 * TypeScript checks it against the side and corner unions.
 */

import type { CSSProperties } from 'react';

import type { ColorString } from './color';
import { px, type CssStyleUnit } from './units';

export type BorderStyle =
  | 'dotted'
  | 'dashed'
  | 'solid'
  | 'double'
  | 'groove'
  | 'ridge'
  | 'inset'
  | 'outset'
  | 'none'
  | 'hidden';

export const BORDER_STYLES: readonly BorderStyle[] = [
  'dotted',
  'dashed',
  'solid',
  'double',
  'groove',
  'ridge',
  'inset',
  'outset',
  'none',
  'hidden',
];

export type BorderSide = 'top' | 'right' | 'bottom' | 'left';
export type BorderCorner = 'topLeft' | 'topRight' | 'bottomLeft' | 'bottomRight';

export interface BorderSideProps {
  color?: ColorString | string;
  style?: BorderStyle;
  weight?: CssStyleUnit;
}

export interface BorderBaseProps extends BorderSideProps {
  radius?: CssStyleUnit;
}

export interface BorderCornerProps {
  radius: CssStyleUnit;
}

export type BorderOverrides = Partial<Record<BorderSide, BorderSideProps>> &
  Partial<Record<BorderCorner, BorderCornerProps>>;

export type BorderProps = BorderBaseProps & BorderOverrides;

/** No border at all, spelled out rather than implied by omission. */
export const NO_BORDER: BorderBaseProps = {
  color: 'transparent',
  style: 'hidden',
  weight: 0,
  radius: 0,
};

/** How the flattened property names are spelled. */
export type CssCase = 'camel' | 'kebab';

const SIDES: readonly BorderSide[] = ['top', 'right', 'bottom', 'left'];
const CORNERS: readonly BorderCorner[] = ['topLeft', 'topRight', 'bottomLeft', 'bottomRight'];

const BASE_PROPERTY: Record<keyof BorderBaseProps, { camel: string; kebab: string }> = {
  color: { camel: 'borderColor', kebab: 'border-color' },
  style: { camel: 'borderStyle', kebab: 'border-style' },
  weight: { camel: 'borderWidth', kebab: 'border-width' },
  radius: { camel: 'borderRadius', kebab: 'border-radius' },
};

const SIDE_PROPERTY: Record<BorderSide, Record<keyof BorderSideProps, { camel: string; kebab: string }>> = {
  top: {
    color: { camel: 'borderTopColor', kebab: 'border-top-color' },
    style: { camel: 'borderTopStyle', kebab: 'border-top-style' },
    weight: { camel: 'borderTopWidth', kebab: 'border-top-width' },
  },
  right: {
    color: { camel: 'borderRightColor', kebab: 'border-right-color' },
    style: { camel: 'borderRightStyle', kebab: 'border-right-style' },
    weight: { camel: 'borderRightWidth', kebab: 'border-right-width' },
  },
  bottom: {
    color: { camel: 'borderBottomColor', kebab: 'border-bottom-color' },
    style: { camel: 'borderBottomStyle', kebab: 'border-bottom-style' },
    weight: { camel: 'borderBottomWidth', kebab: 'border-bottom-width' },
  },
  left: {
    color: { camel: 'borderLeftColor', kebab: 'border-left-color' },
    style: { camel: 'borderLeftStyle', kebab: 'border-left-style' },
    weight: { camel: 'borderLeftWidth', kebab: 'border-left-width' },
  },
};

const CORNER_PROPERTY: Record<BorderCorner, { camel: string; kebab: string }> = {
  topLeft: { camel: 'borderTopLeftRadius', kebab: 'border-top-left-radius' },
  topRight: { camel: 'borderTopRightRadius', kebab: 'border-top-right-radius' },
  bottomLeft: { camel: 'borderBottomLeftRadius', kebab: 'border-bottom-left-radius' },
  bottomRight: { camel: 'borderBottomRightRadius', kebab: 'border-bottom-right-radius' },
};

/** Lengths become CSS strings; colours and styles pass through. */
function lengthValue(value: CssStyleUnit | undefined): string | undefined {
  return value === undefined ? undefined : px(value);
}

/**
 * Flatten border props into style properties.
 *
 * Only the keys actually present are emitted, so a partial override leaves the
 * rest of the border to whatever the stylesheet already said.
 */
export function makeBorderStyles(props?: BorderProps, casing: CssCase = 'camel'): CSSProperties {
  if (props === undefined) return {};
  const out: Record<string, string> = {};

  const put = (name: { camel: string; kebab: string }, value: string | undefined): void => {
    if (value !== undefined) out[name[casing]] = value;
  };

  put(BASE_PROPERTY.color, props.color);
  put(BASE_PROPERTY.style, props.style);
  // `weight` is a length and needs its unit. The source px'd `radius` but not
  // `weight`, because its lookup used the pre-rename key — so a numeric weight
  // reached CSS as a bare number.
  put(BASE_PROPERTY.weight, lengthValue(props.weight));
  put(BASE_PROPERTY.radius, lengthValue(props.radius));

  for (const side of SIDES) {
    const override = props[side];
    if (override === undefined) continue;
    put(SIDE_PROPERTY[side].color, override.color);
    put(SIDE_PROPERTY[side].style, override.style);
    put(SIDE_PROPERTY[side].weight, lengthValue(override.weight));
  }

  for (const corner of CORNERS) {
    const override = props[corner];
    if (override === undefined) continue;
    put(CORNER_PROPERTY[corner], lengthValue(override.radius));
  }

  return out as CSSProperties;
}

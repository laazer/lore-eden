// @vitest-environment node
/**
 * useHashRoute under server rendering, where there is no `window`: the route
 * reads as empty rather than throwing.
 */
import React from 'react';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { useHashRoute } from '../src/hooks/useHashRoute';

describe('useHashRoute on the server', () => {
  it('renders an empty route without touching window', () => {
    expect(typeof window).toBe('undefined');
    function Probe(): React.ReactElement {
      const { route } = useHashRoute();
      return <span>[{route.segment}]</span>;
    }
    expect(renderToString(<Probe />)).toContain('[<!-- -->]');
  });
});

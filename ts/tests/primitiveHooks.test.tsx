import React, { useState } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useEventListener } from '../src/hooks/useEventListener';
import { usePrevious } from '../src/hooks/usePrevious';
import { useThrottle } from '../src/hooks/useThrottle';
import { useMousePosition } from '../src/hooks/useMousePosition';
import { CheckpointProvider, useCheckpoints, type NavAdapter } from '../src/nav';
import { TabView, TAB_DIVIDER, type TabEntry } from '../src/components/TabView';

describe('usePrevious', () => {
  it('is undefined on the first render, then trails by one', () => {
    const seen: (number | undefined)[] = [];
    function Probe({ value }: { value: number }): React.ReactElement {
      seen.push(usePrevious(value));
      return <span />;
    }
    const view = render(<Probe value={1} />);
    view.rerender(<Probe value={2} />);
    view.rerender(<Probe value={3} />);
    expect(seen).toEqual([undefined, 1, 2]);
  });
});

describe('useEventListener', () => {
  it('does not reattach when the handler identity changes', () => {
    const add = vi.spyOn(window, 'addEventListener');
    function Probe({ tick }: { tick: number }): React.ReactElement {
      // A fresh arrow every render — what callers actually pass. The source
      // spread its extra deps into the attach effect, so this reattached each
      // time, and a listener absent for an instant each frame drops events.
      useEventListener('custom-evt', () => void tick);
      return <span />;
    }
    const view = render(<Probe tick={0} />);
    const afterMount = add.mock.calls.filter(([name]) => name === 'custom-evt').length;
    view.rerender(<Probe tick={1} />);
    view.rerender(<Probe tick={2} />);
    expect(add.mock.calls.filter(([name]) => name === 'custom-evt')).toHaveLength(afterMount);
    add.mockRestore();
  });

  it('calls the latest handler, not the one captured at attach', () => {
    const seen: number[] = [];
    function Probe({ tick }: { tick: number }): React.ReactElement {
      useEventListener('custom-evt', () => seen.push(tick));
      return <span />;
    }
    const view = render(<Probe tick={1} />);
    view.rerender(<Probe tick={2} />);
    act(() => void window.dispatchEvent(new Event('custom-evt')));
    expect(seen).toEqual([2]);
  });

  it('detaches on unmount', () => {
    const seen = vi.fn();
    function Probe(): React.ReactElement {
      useEventListener('custom-evt', seen);
      return <span />;
    }
    const view = render(<Probe />);
    view.unmount();
    act(() => void window.dispatchEvent(new Event('custom-evt')));
    expect(seen).not.toHaveBeenCalled();
  });

  it('attaches to an explicit target', () => {
    const seen = vi.fn();
    const target = document.createElement('div');
    function Probe(): React.ReactElement {
      useEventListener('custom-evt', seen, target);
      return <span />;
    }
    render(<Probe />);
    act(() => void target.dispatchEvent(new Event('custom-evt')));
    expect(seen).toHaveBeenCalledTimes(1);
  });
});

describe('useThrottle', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('runs the first call and drops the rest of the window', () => {
    const inner = vi.fn();
    let call: (n: number) => void = () => undefined;
    function Probe(): React.ReactElement {
      call = useThrottle(inner, 100);
      return <span />;
    }
    render(<Probe />);
    act(() => {
      call(1);
      call(2);
      call(3);
    });
    expect(inner).toHaveBeenCalledTimes(1);
    expect(inner).toHaveBeenCalledWith(1);
  });

  it('reopens after the delay', () => {
    const inner = vi.fn();
    let call: (n: number) => void = () => undefined;
    function Probe(): React.ReactElement {
      call = useThrottle(inner, 100);
      return <span />;
    }
    render(<Probe />);
    act(() => void call(1));
    act(() => void vi.advanceTimersByTime(150));
    act(() => void call(2));
    expect(inner).toHaveBeenCalledTimes(2);
  });

  it('keeps its window across renders', () => {
    // Rebuilding the throttled function each render resets the window, which
    // is how a "throttled" handler ends up firing on every render.
    const inner = vi.fn();
    let call: (n: number) => void = () => undefined;
    function Probe({ tick }: { tick: number }): React.ReactElement {
      call = useThrottle(inner, 100);
      return <span>{tick}</span>;
    }
    const view = render(<Probe tick={0} />);
    act(() => void call(1));
    view.rerender(<Probe tick={1} />);
    act(() => void call(2));
    expect(inner).toHaveBeenCalledTimes(1);
  });

  it('does not fire into an unmounted tree', () => {
    const inner = vi.fn();
    function Probe(): React.ReactElement {
      const call = useThrottle(inner, 100);
      React.useEffect(() => call(1), [call]);
      return <span />;
    }
    const view = render(<Probe />);
    view.unmount();
    act(() => void vi.advanceTimersByTime(500));
    expect(inner).toHaveBeenCalledTimes(1);
  });
});

describe('useMousePosition', () => {
  it('coalesces a burst of moves into one render', () => {
    const frames: number[] = [];
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      frames.push(1);
      return setTimeout(() => cb(0), 0) as unknown as number;
    });
    let renders = 0;
    function Probe(): React.ReactElement {
      renders += 1;
      const { x } = useMousePosition();
      return <span>{x ?? 'none'}</span>;
    }
    render(<Probe />);
    const before = renders;
    act(() => {
      for (let i = 0; i < 20; i += 1) {
        window.dispatchEvent(new MouseEvent('mousemove', { clientX: i, clientY: i }));
      }
    });
    // 20 events, one frame scheduled — the source set state on every one.
    expect(frames).toHaveLength(1);
    expect(renders - before).toBeLessThanOrEqual(1);
    vi.unstubAllGlobals();
  });

  it('is undefined until the pointer moves', () => {
    function Probe(): React.ReactElement {
      const { x } = useMousePosition();
      return <span>{x === undefined ? 'unknown' : String(x)}</span>;
    }
    render(<Probe />);
    expect(screen.getByText('unknown')).toBeTruthy();
  });
});

function CheckpointHarness({ start = '/' }: { start?: string }): React.ReactElement {
  const [path, setPath] = useState(start);
  const adapter: NavAdapter = { currentPath: path, navigate: setPath };
  return (
    <CheckpointProvider adapter={adapter}>
      <Controls path={path} />
    </CheckpointProvider>
  );
}

function Controls({ path }: { path: string }): React.ReactElement {
  const { checkpoints, push, pop, jump, clear } = useCheckpoints();
  return (
    <div>
      <span data-testid="path">{path}</span>
      <span data-testid="stack">{checkpoints.join(',')}</span>
      <button onClick={() => push('/docs')}>push-docs</button>
      <button onClick={() => push('/settings')}>push-settings</button>
      <button onClick={() => pop()}>pop</button>
      <button onClick={() => jump()}>jump</button>
      <button onClick={() => clear()}>clear</button>
    </div>
  );
}

describe('CheckpointProvider', () => {
  const stack = (): string => screen.getByTestId('stack').textContent ?? '';
  const path = (): string => screen.getByTestId('path').textContent ?? '';

  it('pushes and jumps back', () => {
    render(<CheckpointHarness />);
    fireEvent.click(screen.getByText('push-docs'));
    expect(stack()).toBe('/,/docs');
    fireEvent.click(screen.getByText('jump'));
    expect(path()).toBe('/docs');
  });

  it('pops without leaving state and render disagreeing', () => {
    // The source mutated the state array and passed the same reference to the
    // setter, so React skipped the render while the array had already changed.
    render(<CheckpointHarness />);
    fireEvent.click(screen.getByText('push-docs'));
    fireEvent.click(screen.getByText('push-settings'));
    expect(stack()).toBe('/,/docs,/settings');
    fireEvent.click(screen.getByText('pop'));
    expect(stack()).toBe('/,/docs');
  });

  it('skips a checkpoint that is where we already are', () => {
    render(<CheckpointHarness start="/settings" />);
    fireEvent.click(screen.getByText('push-docs'));
    fireEvent.click(screen.getByText('push-settings'));
    fireEvent.click(screen.getByText('jump'));
    expect(path()).toBe('/docs');
  });

  it('does not navigate from inside a render', () => {
    // Resolving the jump inside a setState updater ran it during render, so
    // the router's own setState was called mid-render — legal-looking, and
    // React logs an error about it.
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(<CheckpointHarness />);
    fireEvent.click(screen.getByText('push-docs'));
    fireEvent.click(screen.getByText('jump'));
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });

  it('sees its own result when two mutators run in one tick', () => {
    render(<CheckpointHarness />);
    fireEvent.click(screen.getByText('push-docs'));
    act(() => {
      fireEvent.click(screen.getByText('push-settings'));
      fireEvent.click(screen.getByText('pop'));
    });
    expect(stack()).toBe('/,/docs');
  });

  it('falls back rather than becoming a dead control', () => {
    render(<CheckpointHarness />);
    fireEvent.click(screen.getByText('clear'));
    fireEvent.click(screen.getByText('jump'));
    expect(path()).toBe('/');
  });
});

describe('TabView', () => {
  const tabsOf = (...keys: string[]): TabEntry[] =>
    keys.map((key) => ({ key, label: key, content: `panel-${key}` }));

  const selected = (): string =>
    screen.getAllByRole('tab').find((el) => el.getAttribute('aria-selected') === 'true')
      ?.textContent ?? '';

  it('selects the first tab by default and switches on click', () => {
    render(<TabView tabs={tabsOf('a', 'b')} />);
    expect(selected()).toBe('a');
    fireEvent.click(screen.getByText('b'));
    expect(selected()).toBe('b');
  });

  it('selects a newly added tab', () => {
    const view = render(<TabView tabs={tabsOf('a', 'b')} />);
    view.rerender(<TabView tabs={tabsOf('a', 'b', 'c')} />);
    expect(selected()).toBe('c');
  });

  it('follows the selected tab when an earlier one closes', () => {
    const view = render(<TabView tabs={tabsOf('a', 'b', 'c')} defaultSelectedKey="c" />);
    expect(selected()).toBe('c');
    view.rerender(<TabView tabs={tabsOf('b', 'c')} />);
    // Index-based reconciliation would leave position 2 selected, which no
    // longer exists, or slide onto a different document.
    expect(selected()).toBe('c');
  });

  it('does not reconcile on the first render', () => {
    render(<TabView tabs={tabsOf('a', 'b', 'c')} defaultSelectedKey="b" />);
    expect(selected()).toBe('b');
  });

  it('renders a divider that is not a tab', () => {
    render(<TabView tabs={[...tabsOf('a'), TAB_DIVIDER, ...tabsOf('b')]} />);
    expect(screen.getAllByRole('tab')).toHaveLength(2);
  });

  it('shows only the active panel', () => {
    render(<TabView tabs={tabsOf('a', 'b')} />);
    expect(screen.getByText('panel-a').closest('[role="tabpanel"]')?.hasAttribute('hidden')).toBe(
      false,
    );
    expect(screen.getByText('panel-b').closest('[role="tabpanel"]')?.hasAttribute('hidden')).toBe(
      true,
    );
  });

  it('reports a reconciled selection to the caller', () => {
    const onSelect = vi.fn();
    const view = render(<TabView tabs={tabsOf('a', 'b')} onSelect={onSelect} />);
    view.rerender(<TabView tabs={tabsOf('a', 'b', 'c')} onSelect={onSelect} />);
    expect(onSelect).toHaveBeenCalledWith('c');
  });
});

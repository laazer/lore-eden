/**
 * Panes: what size they report, and what they mount.
 *
 * The registry invariant is the one worth defending — arrangement code never
 * names a primitive — so the last test here registers one the package has never
 * heard of and mounts it in *both* arrangements without either surface changing.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  COMPACT_HEIGHT,
  COMPACT_WIDTH,
  ContainerPane,
  ContainerPrimitiveHost,
  PrimitiveRegistry,
  WIDE_WIDTH,
  composeSettings,
  containerWithSettings,
  definePrimitive,
  newContainerFor,
  paneTierFor,
  paneTitle,
  usePaneSize,
} from '../src/panes';
import {
  addItem,
  emptyLayoutFor,
  readCanvasItems,
  readGridTree,
  type GridNodeModel,
} from '../src/layout';
import { CanvasSurface } from '../src/surfaces/CanvasSurface';
import { FlexGridSurface } from '../src/surfaces/FlexGridSurface';

/** A primitive this package has never heard of. */
const notes = definePrimitive({
  id: 'notes',
  displayName: 'Notes',
  icon: '📝',
  category: 'content',
  containerKind: 'panel',
  settingsFields: [{ kind: 'string', key: 'title', label: 'Title', default: 'Untitled' }],
  parseSettings: (settings) => ({
    title: typeof settings.title === 'string' ? settings.title : 'Untitled',
  }),
  Component: ({ settings }) => {
    const size = usePaneSize();
    return (
      <div data-testid="notes">
        <span data-testid="notes-title">{String(settings.title ?? '')}</span>
        <span data-testid="notes-tier">{size.tier}</span>
      </div>
    );
  },
});

function registryWithNotes(): PrimitiveRegistry {
  return new PrimitiveRegistry().register(notes);
}

const noop = () => undefined;

describe('size tiers', () => {
  it('calls a pane compact when either dimension is small', () => {
    // Height is part of the answer, not a footnote: 900×90 is a letterbox, and a
    // primitive consulting only width would lay a three-column card into it.
    expect(paneTierFor(COMPACT_WIDTH - 1, 400)).toBe('compact');
    expect(paneTierFor(900, COMPACT_HEIGHT - 1)).toBe('compact');
  });

  it('calls a roomy pane wide', () => {
    expect(paneTierFor(WIDE_WIDTH, 400)).toBe('wide');
  });

  it('calls everything between regular', () => {
    expect(paneTierFor(COMPACT_WIDTH, COMPACT_HEIGHT)).toBe('regular');
    expect(paneTierFor(WIDE_WIDTH - 1, 400)).toBe('regular');
  });

  it('reports regular before anything has been measured', () => {
    // Guessing compact would flash every pane's dense layout on mount; guessing
    // wide would do the reverse. Regular is the layout primitives are written
    // for, so the pre-measurement frame looks like the steady state.
    render(
      <ContainerPrimitiveHost
        containerId="c1"
        settings={{ primitive_id: 'notes' }}
        registry={registryWithNotes()}
      />,
    );

    expect(screen.getByTestId('notes-tier')).toHaveTextContent('regular');
  });

  it('publishes the tier as an attribute as well as a value', () => {
    // So a primitive can answer the easy cases in its own stylesheet without
    // re-rendering on every drag frame.
    const { container } = render(
      <ContainerPrimitiveHost
        containerId="c1"
        settings={{ primitive_id: 'notes' }}
        registry={registryWithNotes()}
      />,
    );

    expect(container.querySelector('[data-pane-tier="regular"]')).not.toBeNull();
  });
});

describe('the registry', () => {
  it('mounts the primitive a container names', () => {
    render(
      <ContainerPrimitiveHost
        containerId="c1"
        settings={{ primitive_id: 'notes', title: 'Shopping' }}
        registry={registryWithNotes()}
      />,
    );

    expect(screen.getByTestId('notes-title')).toHaveTextContent('Shopping');
  });

  it('says so in the pane when the build has no such primitive', () => {
    // A view outlives the primitive it names — a renamed id, a container written
    // by a newer build. Say so here rather than taking the whole view down.
    const { container } = render(
      <ContainerPrimitiveHost
        containerId="c1"
        settings={{ primitive_id: 'gone' }}
        registry={registryWithNotes()}
      />,
    );

    expect(container.querySelector('[data-primitive-unknown="true"]')).not.toBeNull();
  });

  it('refuses a container stored under a kind its primitive does not belong to', () => {
    // Two records of the same decision disagreeing; mounting anyway lets the
    // wrong one win silently.
    const { container } = render(
      <ContainerPrimitiveHost
        containerId="c1"
        settings={{ primitive_id: 'notes' }}
        kind="a-different-kind"
        registry={registryWithNotes()}
      />,
    );

    expect(container.querySelector('[data-primitive-unknown="kind-mismatch"]')).not.toBeNull();
  });

  it('resolves ids through a Map, so a crafted id cannot reach Object.prototype', () => {
    const registry = registryWithNotes();

    expect(registry.get('__proto__')).toBeUndefined();
    expect(registry.get('constructor')).toBeUndefined();
  });

  it('refuses to register one id twice', () => {
    // Replacing silently means a container that mounted one component yesterday
    // mounts another today, with nothing saying so.
    const registry = registryWithNotes();

    expect(() => registry.register(notes)).toThrow(/already registered/);
  });

  it('names a pane from the registry rather than from the container', () => {
    const container = { kind: 'panel', settings: { primitive_id: 'notes' } };

    expect(paneTitle(container, registryWithNotes())).toBe('Notes');
  });

  it('separates a pane with no primitive from one whose primitive is missing', () => {
    const registry = registryWithNotes();

    expect(paneTitle({ kind: 'panel', settings: {} }, registry)).toBe('Empty pane');
    expect(paneTitle({ kind: 'panel', settings: { primitive_id: 'gone' } }, registry)).toBe(
      'Unknown contents',
    );
  });
});

describe('composing a container', () => {
  it('fills declared fields with their defaults', () => {
    const container = newContainerFor('notes', registryWithNotes());

    expect(container).toEqual({
      kind: 'panel',
      settings: { title: 'Untitled', primitive_id: 'notes' },
    });
  });

  it('stamps the kind the primitive belongs to', () => {
    // An edit that patched settings underneath a stale kind renders a
    // placeholder, so the container is always built whole.
    expect(newContainerFor('notes', registryWithNotes())?.kind).toBe('panel');
  });

  it('carries no key the schema does not declare', () => {
    const container = containerWithSettings(
      'notes',
      new Map([['title', 'x'], ['stray', 'y']]),
      registryWithNotes(),
    );

    expect(container?.settings).not.toHaveProperty('stray');
  });

  it('is undefined for an id the registry does not know', () => {
    expect(newContainerFor('gone', registryWithNotes())).toBeUndefined();
  });

  it('cannot have its primitive_id overwritten by a declared field', () => {
    const settings = composeSettings(
      [{ kind: 'string', key: 'primitive_id', label: 'x', default: 'evil' }],
      new Map([['primitive_id', 'also-evil']]),
      'notes',
    );

    expect(settings.primitive_id).toBe('notes');
  });

  it('defines a __proto__ field rather than hitting the prototype setter', () => {
    // Assignment would be swallowed by Object.prototype's setter and the
    // declared field would never reach the wire, with no error raised.
    const settings = composeSettings(
      [{ kind: 'string', key: '__proto__', label: 'x', default: 'value' }],
      new Map(),
      'notes',
    );

    expect(Object.getOwnPropertyNames(settings)).toContain('__proto__');
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });
});

describe('a host-registered primitive mounts in both arrangements', () => {
  // The invariant: neither surface names a primitive, so adding one is a
  // registration and no change to either.
  const registry = registryWithNotes();

  it('mounts in the grid', () => {
    const layout = emptyLayoutFor('flex_grid');
    const tree = readGridTree(layout) as GridNodeModel;
    const containers = layout.containers as Record<string, unknown>;
    containers[(tree as { container_id: string }).container_id] = {
      kind: 'panel',
      settings: { primitive_id: 'notes', title: 'In a grid' },
    };

    render(
      <FlexGridSurface
        tree={tree}
        containers={containers}
        edit={noop}
        pickPrimitive={noop}
        registry={registry}
      />,
    );

    expect(screen.getByTestId('notes-title')).toHaveTextContent('In a grid');
  });

  it('mounts on the canvas', () => {
    const layout = addItem(emptyLayoutFor('canvas'), 0, 0);
    const [item] = readCanvasItems(layout);
    (layout.containers as Record<string, unknown>)[item.container_id] = {
      kind: 'panel',
      settings: { primitive_id: 'notes', title: 'On a canvas' },
    };

    render(
      <CanvasSurface
        layout={layout}
        viewport={{}}
        edit={noop}
        writeViewport={noop}
        pickPrimitive={noop}
        registry={registry}
      />,
    );

    expect(screen.getByTestId('notes-title')).toHaveTextContent('On a canvas');
  });
});

describe('an empty pane', () => {
  it('offers no chooser when the host supplies no picker', () => {
    // A button that opens nothing is a control that lies about being usable.
    render(
      <ContainerPane
        containerId="c1"
        container={{ kind: 'panel', settings: {} }}
        pickPrimitive={noop}
        registry={registryWithNotes()}
      />,
    );

    expect(screen.queryByRole('button', { name: /choose a primitive/i })).toBeNull();
  });

  it('offers the host’s chooser when one is supplied', () => {
    render(
      <ContainerPane
        containerId="c1"
        container={{ kind: 'panel', settings: {} }}
        pickPrimitive={noop}
        registry={registryWithNotes()}
        renderPicker={() => <div data-testid="picker" />}
      />,
    );

    expect(screen.getByRole('button', { name: /choose a primitive/i })).toBeInTheDocument();
  });
});

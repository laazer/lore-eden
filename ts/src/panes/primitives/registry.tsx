/**
 * The primitive registry, and the one dispatcher that mounts from it.
 *
 * A container stores a `primitive_id` in its settings and nothing else;
 * `ContainerPrimitiveHost` is how that string becomes a component. Arrangement
 * code never names a primitive — that invariant is what lets a host add one
 * without touching the grid or the canvas, and it is worth defending: the moment
 * a surface special-cases an id, the surfaces stop being reusable.
 *
 * The registry is populated by the host rather than declared here. In the
 * codebase this came from it was a hardcoded array of that product's own panes,
 * which is exactly the part that cannot travel.
 *
 * A stored id is attacker-influencable text. It is resolved through a `Map` —
 * never a plain-object lookup, which reaches `constructor` and `__proto__` — and
 * never through a dynamic import.
 */

import { useEffect, useMemo, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';

import { PaneSizeContext, paneTierFor, type PaneSize } from '../paneSize';
import { PrimitiveErrorBoundary } from './PrimitiveErrorBoundary';
import type { ContainerKind, RegisteredPrimitive, SettingsField, ViewContainer } from './types';

/**
 * The primitives a host offers.
 *
 * A class rather than a module-level array so an app can hold more than one —
 * a test, a story, or a second surface offering a different set — without them
 * writing over each other.
 */
export class PrimitiveRegistry {
  private readonly byId = new Map<string, RegisteredPrimitive>();

  /**
   * Add a primitive. Registering an id twice is refused rather than silently
   * replacing the first: a container that mounted one component yesterday and a
   * different one today, with nothing saying so, is not a state worth allowing.
   */
  register(...primitives: RegisteredPrimitive[]): this {
    for (const primitive of primitives) {
      if (this.byId.has(primitive.id)) {
        throw new Error(`A primitive with id ${primitive.id} is already registered`);
      }
      this.byId.set(primitive.id, primitive);
    }
    return this;
  }

  /** Registration order, which is the order a picker should offer them in. */
  all(): RegisteredPrimitive[] {
    return [...this.byId.values()];
  }

  /**
   * Look one up. `undefined` for an id this build does not have, because the id
   * comes from stored text and a view can outlive the primitive it names.
   */
  get(id: string): RegisteredPrimitive | undefined {
    return this.byId.get(id);
  }

  has(id: string): boolean {
    return this.byId.has(id);
  }

  /** For a test that wants a clean slate. */
  clear(): void {
    this.byId.clear();
  }
}

/** The registry the surfaces read when none is supplied through context. */
export const defaultPrimitiveRegistry = new PrimitiveRegistry();

/**
 * The settings a primitive id and a set of field values become.
 *
 * Exported and separately tested because two of its rules have no reachable
 * path through the callers below — no sensible primitive declares a `__proto__`
 * or a `primitive_id` field — and would otherwise be asserted nowhere.
 */
export function composeSettings(
  fields: SettingsField[],
  values: ReadonlyMap<string, unknown>,
  primitiveId: string,
): Record<string, unknown> {
  const settings = Object.fromEntries(
    fields.map((field) => [
      field.key,
      values.has(field.key) ? values.get(field.key) : field.default,
    ]),
  );
  // `primitive_id` last, so a field declaring that key cannot overwrite the id
  // with a user-typed string and store a container nothing can mount.
  //
  // Spread rather than assignment, for the same reason `fromEntries` is used
  // above: both *define* their keys. `settings["__proto__"] = x` instead hits
  // `Object.prototype`'s setter, is silently swallowed, and the declared field
  // never reaches the wire — with no error raised anywhere.
  return { ...settings, primitive_id: primitiveId };
}

/**
 * The container a primitive id and a set of values become.
 *
 * Built **whole** rather than merged into a stored one, and that is the point
 * rather than a detail: the host below refuses a container whose `kind`
 * disagrees with its `primitive_id`, so an edit that patched `settings`
 * underneath a stale `kind` would render a placeholder. A container composed
 * here always stamps the `kind` its primitive belongs to, and carries no key the
 * schema does not declare.
 *
 * `values` is a `Map`, not an object: a key reaching here is compared against
 * declared field keys, and `"key" in plainObject` answers true for
 * `constructor` and `__proto__`.
 */
export function containerWithSettings(
  primitiveId: string,
  values: ReadonlyMap<string, unknown>,
  registry: PrimitiveRegistry = defaultPrimitiveRegistry,
): ViewContainer | undefined {
  const entry = registry.get(primitiveId);
  if (entry === undefined) return undefined;
  return {
    kind: entry.containerKind,
    settings: composeSettings(entry.settingsFields, values, entry.id),
  };
}

/** The container a freshly picked primitive becomes: its schema's defaults. */
export function newContainerFor(
  primitiveId: string,
  registry: PrimitiveRegistry = defaultPrimitiveRegistry,
): ViewContainer | undefined {
  return containerWithSettings(primitiveId, new Map(), registry);
}

/**
 * Fill the pane; do not assert a size of your own.
 *
 * `min-height: 0` is the load-bearing one: a flex child defaults to
 * `min-height: auto` and refuses to shrink below its content, which is the
 * mechanism behind almost every "it overflows when the pane is small" bug.
 */
const HOST_STYLE: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  height: '100%',
  width: '100%',
  minHeight: '0',
  minWidth: '0',
  overflow: 'auto',
};

/**
 * Measure the host element, so the primitive inside it lays out for the room it
 * has rather than the room it hoped for.
 *
 * Here rather than in each primitive: there is one host, every primitive mounts
 * through it, and N observers watching N elements to learn the same number is N
 * chances to disagree about it.
 *
 * `ResizeObserver` is absent in jsdom and in older embedded webviews. The size
 * stays `null` there and `usePaneSize` reports `regular` — the layout every
 * primitive was written for before tiers existed, so the feature degrades to the
 * previous behaviour rather than to a broken one.
 */
function usePaneMeasurement(node: HTMLElement | null): PaneSize | null {
  const [box, setBox] = useState<{ width: number; height: number } | null>(null);

  useEffect(() => {
    if (node === null) return;
    if (typeof ResizeObserver === 'undefined') return;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry === undefined) return;
      // `contentRect` rather than `borderBoxSize`: the primitive lays out inside
      // the padding box, which is what the content rect describes, and it is the
      // field every browser with a ResizeObserver agrees on.
      const { width, height } = entry.contentRect;
      // Rounded, and compared before storing: a pane being dragged reports
      // sub-pixel widths continuously, and re-rendering every primitive in the
      // view on each fractional change is a resize that costs more than it
      // informs.
      const next = { width: Math.round(width), height: Math.round(height) };
      setBox((previous) =>
        previous !== null && previous.width === next.width && previous.height === next.height
          ? previous
          : next,
      );
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);

  return useMemo(
    () => (box === null ? null : { ...box, tier: paneTierFor(box.width, box.height) }),
    [box],
  );
}

export interface ContainerPrimitiveHostProps {
  containerId: string;
  /** The container's stored settings, verbatim: snake_case and unvalidated. */
  settings: Record<string, unknown>;
  /**
   * The `kind` the container is stored under, when the caller has it.
   *
   * Optional because a caller holding only a settings map is legitimate, but
   * when supplied it is *checked*: a container stored under one kind whose
   * primitive belongs to another is a disagreement between two records of the
   * same decision, and mounting anyway lets the wrong one win silently.
   */
  kind?: ContainerKind;
  registry?: PrimitiveRegistry;
}

function Placeholder({
  containerId,
  reason,
  children,
}: {
  containerId: string;
  reason: string;
  children: ReactNode;
}) {
  return (
    <div data-container-id={containerId} data-primitive-unknown={reason} style={HOST_STYLE}>
      <p style={{ margin: 0, padding: 16, color: 'var(--faint)', fontSize: 12.5 }}>{children}</p>
    </div>
  );
}

export function ContainerPrimitiveHost({
  containerId,
  settings,
  kind,
  registry = defaultPrimitiveRegistry,
}: ContainerPrimitiveHostProps) {
  const [node, setNode] = useState<HTMLElement | null>(null);
  const size = usePaneMeasurement(node);
  const storedId = settings.primitive_id;
  const entry = typeof storedId === 'string' ? registry.get(storedId) : undefined;

  if (entry === undefined) {
    // A view can outlive the primitive it names — a renamed id, a container
    // written by a newer build. Say so in the pane rather than taking the whole
    // view down with an exception.
    return (
      <Placeholder containerId={containerId} reason="true">
        This container asks for a primitive this build does not have.
      </Placeholder>
    );
  }

  if (kind !== undefined && kind !== entry.containerKind) {
    return (
      <Placeholder containerId={containerId} reason="kind-mismatch">
        This container is stored as a kind its primitive does not belong to.
      </Placeholder>
    );
  }

  const Primitive = entry.Component;
  return (
    <div
      ref={setNode}
      data-container-id={containerId}
      data-primitive-id={entry.id}
      // The tier as an attribute as well as a context value, so a primitive can
      // answer the easy cases in its own stylesheet — padding, a hidden
      // secondary column — without re-rendering on every drag frame.
      data-pane-tier={size?.tier ?? 'regular'}
      style={HOST_STYLE}
    >
      {/* One boundary here, rather than one per arrangement: a primitive that
          throws must lose its own pane and nothing else. */}
      <PrimitiveErrorBoundary resetKey={`${containerId}:${entry.id}`}>
        <PaneSizeContext.Provider value={size}>
          <Primitive containerId={containerId} settings={settings} />
        </PaneSizeContext.Provider>
      </PrimitiveErrorBoundary>
    </div>
  );
}

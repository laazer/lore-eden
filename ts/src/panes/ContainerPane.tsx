/**
 * One container's pane: either the prompt an unconfigured container shows, or
 * the primitive it named.
 *
 * It lives beside `PrimitivePicker` rather than inside the `/view/:viewId` page
 * because a grid leaf (440) and a canvas item (442) both put one of these on
 * screen. Page-local, each renderer would re-derive the empty-vs-configured
 * branch and the rule that a pick *replaces* the container — the re-derivation
 * the primitive registry exists to prevent.
 *
 * The pane obtains its own write through `useViewLayoutWrite`, so neither
 * renderer carries a handler through its recursion. The identity that write
 * needs comes from the same two places the page reads it from: the chrome's
 * workspace and the route's view id.
 */

import { useState, type ReactNode } from "react";

import { asJson } from "../layout/layouts";
import { paneSettings } from "./paneChrome";
import "./paneChrome.css";
import { ContainerPrimitiveHost, type PrimitiveRegistry } from "./primitives/registry";
import { containerKindOf } from "./primitives/types";

/**
 * The picker a pane opens, supplied by the host.
 *
 * A slot rather than a component imported here, for the same reason the header
 * takes one: which primitives are offered, and what the chooser looks like, are
 * the host's decisions.
 */
export type RenderPrimitivePicker = (props: {
  legend: string;
  onClose: () => void;
  onPick: (primitiveId: string) => void;
}) => ReactNode;

/**
 * A container with no `primitive_id` yet — the state a freshly seeded grid opens
 * in, and the whole of AC6.
 *
 * The pick *replaces* the container rather than merging an id into it: the
 * placeholder is stored as `kind: "panel"`, and a terminal primitive stored
 * under `panel` is a disagreement `ContainerPrimitiveHost` refuses to mount.
 */
function EmptyContainerPrompt({
  containerId,
  onPick,
  renderPicker,
}: {
  containerId: string;
  onPick: (primitiveId: string) => void;
  renderPicker?: RenderPrimitivePicker;
}) {
  const [picking, setPicking] = useState(false);

  return (
    <div className="pane-empty" data-container-id={containerId}>
      <div className="pane-empty-eyebrow">Empty pane</div>
      <p className="pane-empty-hint">
        This pane has no contents yet. Pick what it should hold — you can change it later.
      </p>
      {renderPicker === undefined ? null : (
        <button type="button" className="btn-primary btn-compact" onClick={() => setPicking(true)}>
          Choose a primitive
        </button>
      )}
      {/* The prompt stays put behind the dialog rather than being replaced by
          the list (557). The list is now long enough that an empty pane could
          not hold it, and a modal is not something the pane has to make room
          for. */}
      {picking
        ? renderPicker?.({
            legend: "Choose a primitive",
            onClose: () => setPicking(false),
            onPick: (primitiveId: string) => {
              setPicking(false);
              onPick(primitiveId);
            },
          })
        : null}
    </div>
  );
}

export function ContainerPane({
  containerId,
  container,
  pickPrimitive,
  renderPicker,
  registry,
}: {
  containerId: string;
  /** The container as the layout stores it: unvalidated, and possibly absent. */
  container: unknown;
  /** Store this container's chosen primitive. */
  pickPrimitive: (containerId: string, primitiveId: string) => void;
  renderPicker?: RenderPrimitivePicker;
  registry?: PrimitiveRegistry;
}) {
  const stored = asJson(container);
  const settings = paneSettings(container);

  if (typeof settings.primitive_id !== "string") {
    return (
      <EmptyContainerPrompt
        containerId={containerId}
        onPick={(primitiveId: string) => pickPrimitive(containerId, primitiveId)}
        renderPicker={renderPicker}
      />
    );
  }
  return (
    <ContainerPrimitiveHost
      containerId={containerId}
      settings={settings}
      kind={containerKindOf(stored?.kind)}
      registry={registry}
    />
  );
}

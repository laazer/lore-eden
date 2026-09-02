/**
 * The vocabulary a container primitive is described in.
 *
 * A primitive is "a thing a view container can show": its display metadata (so
 * a picker can offer it without knowing what it is), a settings schema (so an
 * operator can configure it without knowing what it is), and a component that
 * receives *parsed* settings.
 *
 * There is no schema library in this client, so the schema is a field
 * descriptor array plus a hand-written `parseSettings`. `parseSettings` is the
 * only narrowing step: everything downstream of it is typed.
 */

import type { ComponentType } from "react";

/**
 * What a container is stored *as*, alongside which primitive fills it.
 *
 * An open string. It was a closed three-value union in the codebase this came
 * from — `"terminal" | "panel" | "web_embed"` — mirroring that backend's own
 * enum, and those are its concepts rather than a property of arrangements. A
 * host has its own kinds, and one it cannot name is one it cannot store.
 *
 * The value still earns its keep: `ContainerPrimitiveHost` compares the stored
 * kind against the kind its primitive declares, because two records of the same
 * decision disagreeing means one of them is wrong and mounting anyway lets the
 * wrong one win silently. That check works on any vocabulary.
 */
export type ContainerKind = string;

/** A stored `kind`, when one was stored at all. */
export function containerKindOf(value: unknown): ContainerKind | undefined {
  return typeof value === "string" ? value : undefined;
}

/**
 * The input kinds a settings editor knows how to render.
 *
 * Values first, type derived: a reader checking a kind against the vocabulary
 * tests membership of this list instead of re-spelling the literals, which is a
 * copy no compiler keeps in step. Closed, unlike `ContainerKind` — these are the
 * inputs a settings editor knows how to render, which is this package's own
 * decision rather than a host's.
 */
export const SETTINGS_FIELD_KINDS = ["string", "number", "boolean", "choice"] as const;

export type SettingsFieldKind = (typeof SETTINGS_FIELD_KINDS)[number];

/**
 * What a `choice` field offers, named rather than enumerated.
 *
 * A field names a source and the host resolves it, for the same reason the
 * registry exists: a primitive declaring "this is an account id" should not also
 * have to know how accounts are fetched, and N copies of that knowledge is N
 * chances for one to go stale. The host supplies the loaders in one place.
 *
 * An open string, not a closed union. It was closed in the codebase this came
 * from — `"workspace" | "ticket" | "agent" | …` — and those are that product's
 * nouns. A host has its own, and a vocabulary it cannot extend is one it cannot
 * use. Which sources exist is checked by the host's loader map, at the point
 * that actually knows.
 */
export type ChoiceSource = string;

interface SettingsFieldBase {
  /** Wire key inside the container's `settings` map — snake_case, per 433. */
  key: string;
  label: string;
  /** Optional one-line hint for the settings editor. */
  help?: string;
}

/**
 * A settings field, discriminated on `kind`.
 *
 * `kind` and `default` are one decision, not two: `{kind: "number", default:
 * "twelve"}` is a schema that no editor can render and no `parseSettings` can
 * honour, and a shared `string | number | boolean` default type accepts it. The
 * union also means a settings editor (438) can switch on `kind` and get the
 * matching `default` type without narrowing it by hand.
 */
export type SettingsField =
  | (SettingsFieldBase & { kind: "string"; default: string })
  | (SettingsFieldBase & { kind: "number"; default: number })
  | (SettingsFieldBase & { kind: "boolean"; default: boolean })
  /**
   * A string whose value names something the app can list. It stores exactly
   * what a `string` field stores — the wire is unchanged and no `parseSettings`
   * moves — so `source` buys an operator the list without costing the primitive
   * anything. It is not a constraint: an unlisted value is still storable, on
   * the same reasoning that keeps the editor from validating slugs.
   */
  | (SettingsFieldBase & { kind: "choice"; default: string; source: ChoiceSource });

/**
 * A primitive's parsed settings: an open, JSON-shaped map.
 *
 * The constraint exists so the registry's erased `parseSettings` can promise a
 * usable return type rather than `unknown`. Write a primitive's settings as a
 * `type` alias, not an `interface` — TypeScript gives object type aliases an
 * implicit index signature and interfaces none, so an interface will not
 * satisfy this bound.
 */
export type ParsedSettings = Record<string, unknown>;

/** What a primitive's own component is handed: its container's id and parsed settings. */
export interface PrimitiveProps<TSettings> {
  containerId: string;
  settings: TSettings;
}

/**
 * A container as 433's wire model holds it: no id (it is the key of the
 * container registry), a `kind` from the server enum, and an open settings map
 * carrying `primitive_id`.
 */
export interface ViewContainer {
  kind: ContainerKind;
  settings: Record<string, unknown>;
}

/**
 * A primitive as its author writes it — generic over the settings type
 * `parseSettings` produces and `Component` consumes.
 */
export interface PrimitiveEntry<TSettings extends ParsedSettings> {
  /** Stable registry key; also the value stored as `settings.primitive_id`. */
  id: string;
  displayName: string;
  icon: string;
  category: string;
  /**
   * The `kind` a container holding this primitive must be stored as.
   *
   * Checked, not advisory: `newContainerFor` stamps it, and
   * `ContainerPrimitiveHost` refuses to mount a primitive whose entry disagrees
   * with the kind the container was stored under.
   */
  containerKind: ContainerKind;
  settingsFields: SettingsField[];
  parseSettings: (raw: Record<string, unknown>) => TSettings;
  Component: ComponentType<PrimitiveProps<TSettings>>;
}

/**
 * A primitive as the registry holds it, with `TSettings` erased.
 *
 * The erasure happens inside `definePrimitive`, which closes over the generic
 * and wraps the component so it is handed `parseSettings(raw)`. That is why no
 * entry in this registry needs a cast: the only place that knows `TSettings` is
 * the one place that is still generic.
 */
export interface RegisteredPrimitive {
  id: string;
  displayName: string;
  icon: string;
  category: string;
  containerKind: ContainerKind;
  settingsFields: SettingsField[];
  /**
   * Erased down to the bound, not to `unknown`: a consumer that wants to read a
   * parsed value back (a settings editor previewing a default, a test) gets a
   * usable map without an assertion, and the primitive's own component still
   * sees the precise type because `definePrimitive` applies this before the
   * erasure.
   */
  parseSettings: (raw: Record<string, unknown>) => ParsedSettings;
  Component: ComponentType<PrimitiveProps<Record<string, unknown>>>;
}

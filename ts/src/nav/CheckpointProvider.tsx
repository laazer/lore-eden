/**
 * The checkpoint stack as React context, over a router the host supplies.
 *
 * The source called `useHistory` and `useLocation` from react-router v5
 * directly. `useHistory` was removed in v6, so extracting that as-is would have
 * pinned every consumer of this library to a router two majors behind — and to
 * react-router at all, which a Next or TanStack app is not using.
 *
 * A host passes a {@link NavAdapter} instead: two functions, both of which any
 * router can supply in a line.
 *
 *     const adapter = { currentPath: useLocation().pathname, navigate: useNavigate() };
 *     <CheckpointProvider adapter={adapter}>…</CheckpointProvider>
 */

import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';

import {
  DEFAULT_ALIASES,
  addAlias,
  popCheckpoint,
  pushCheckpoint,
  resolveJump,
  type PathAliases,
} from './checkpoints';

/** What the checkpoint stack needs from a router. */
export interface NavAdapter {
  /** The path being displayed right now. */
  currentPath: string;
  /** Go to a path. */
  navigate: (path: string) => void;
}

export interface CheckpointApi {
  checkpoints: readonly string[];
  /** Record a place worth returning to. Defaults to the current path. */
  push: (path?: string) => void;
  pop: () => void;
  /** Go back to the nearest checkpoint that is not where we already are. */
  jump: () => void;
  clear: () => void;
  addPathAlias: (from: string, to: string) => void;
}

const NOT_PROVIDED: CheckpointApi = {
  checkpoints: [],
  push: () => undefined,
  pop: () => undefined,
  jump: () => undefined,
  clear: () => undefined,
  addPathAlias: () => undefined,
};

export const CheckpointContext = createContext<CheckpointApi>(NOT_PROVIDED);

export interface CheckpointProviderProps {
  children: React.ReactNode;
  adapter: NavAdapter;
  initialStack?: readonly string[];
  initialAliases?: PathAliases;
  /** Where `jump` goes when the stack is exhausted. */
  fallback?: string;
}

export function CheckpointProvider({
  children,
  adapter,
  initialStack = ['/'],
  initialAliases = DEFAULT_ALIASES,
  fallback = '/',
}: CheckpointProviderProps): React.ReactElement {
  const [stack, setStack] = useState<readonly string[]>(initialStack);
  const [aliases, setAliases] = useState<PathAliases>(initialAliases);

  // The adapter is read through a ref so the callbacks below stay stable. A
  // router hook returns a fresh `navigate` on most renders, and depending on it
  // would rebuild every callback and re-run any effect keyed on one.
  const adapterRef = useRef(adapter);
  adapterRef.current = adapter;
  const aliasesRef = useRef(aliases);
  aliasesRef.current = aliases;
  // The stack is mirrored into a ref so `jump` can read the committed value
  // without resolving inside a state updater — an updater runs during render,
  // and navigating from there means calling the router's setState while React
  // is rendering this component.
  const stackRef = useRef(stack);
  stackRef.current = stack;

  // All three mutators go through the ref rather than a functional updater, so
  // two of them in one tick see each other's result — a functional updater
  // would, but `jump` cannot use one, and a provider where two of the three
  // batch differently from the third is a bug waiting for the day someone
  // calls them together.
  const commit = useCallback((next: readonly string[]) => {
    if (next === stackRef.current) return;
    stackRef.current = next;
    setStack(next);
  }, []);

  const push = useCallback(
    (path?: string) => {
      const target = path ?? adapterRef.current.currentPath;
      commit(pushCheckpoint(stackRef.current, target, aliasesRef.current));
    },
    [commit],
  );

  const pop = useCallback(() => {
    commit(popCheckpoint(stackRef.current));
  }, [commit]);

  const jump = useCallback(() => {
    const { path, stack: remaining } = resolveJump(
      stackRef.current,
      adapterRef.current.currentPath,
      aliasesRef.current,
      fallback,
    );
    // Resolved and committed outside any updater, so the navigate below runs
    // in the event handler rather than during a render.
    commit(remaining);
    adapterRef.current.navigate(path);
  }, [commit, fallback]);

  const clear = useCallback(() => commit([]), [commit]);

  const addPathAlias = useCallback((from: string, to: string) => {
    // Functional update: two calls in one tick both land, where the source's
    // spread of a captured `pathMappings` silently dropped the first.
    setAliases((current) => addAlias(current, from, to));
  }, []);

  const value = useMemo<CheckpointApi>(
    () => ({ checkpoints: stack, push, pop, jump, clear, addPathAlias }),
    [stack, push, pop, jump, clear, addPathAlias],
  );

  return <CheckpointContext.Provider value={value}>{children}</CheckpointContext.Provider>;
}

export function useCheckpoints(): CheckpointApi {
  return useContext(CheckpointContext);
}

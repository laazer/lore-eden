/**
 * Polls the instance list, and runs launch and stop against it.
 *
 * Polling is what moves an instance from `starting` to `ready`: the server
 * probes a starting instance each time it is listed. So the hook polls faster
 * while anything is starting, and slower once everything has settled.
 *
 * A failed poll keeps the last good list and reports the error beside it.
 * Blanking the list because one request failed would show "no instances" for
 * servers that are still running — the empty state standing in for an error.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { asError } from '../util/errors';
import type { InstancesClient } from './client';
import type { InstanceListing, InstanceView, LaunchRequest, TemplateInfo } from './types';

export interface UseInstancesOptions {
  project?: string;
  /** Poll interval while every instance has settled. */
  idleIntervalMs?: number;
  /** Poll interval while any instance is starting. */
  busyIntervalMs?: number;
}

export interface UseInstancesResult {
  /** The last list that loaded; null until the first one does. */
  listing: InstanceListing | null;
  templates: TemplateInfo[] | null;
  /** The most recent refresh failure, cleared by the next success. */
  error: Error | null;
  /** True only before anything has loaded — not on every poll. */
  loading: boolean;
  /** Ids with a stop in flight, so a second click cannot fire it twice. */
  stopping: ReadonlySet<string>;
  launching: boolean;
  refresh(): Promise<void>;
  /** Rejects with the server's reason; the caller decides how to show it. */
  launch(request: LaunchRequest): Promise<InstanceView>;
  stop(id: string): Promise<void>;
}

const anyStarting = (listing: InstanceListing | null): boolean =>
  listing?.instances.some((i) => i.state === 'starting') ?? false;

export function useInstances(
  client: InstancesClient,
  { project, idleIntervalMs = 5000, busyIntervalMs = 1500 }: UseInstancesOptions = {},
): UseInstancesResult {
  const [listing, setListing] = useState<InstanceListing | null>(null);
  const [templates, setTemplates] = useState<TemplateInfo[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [stopping, setStopping] = useState<ReadonlySet<string>>(new Set());
  const [launching, setLaunching] = useState(false);
  const inFlight = useRef(false);
  const again = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    // One request at a time: a slow server would otherwise stack them, and an
    // older answer could land last and roll the list back. A refresh asked for
    // meanwhile — after a stop, say — is run once the current one lands, so
    // the list the caller awaits really is from after its action.
    if (inFlight.current) {
      again.current = true;
      return;
    }
    inFlight.current = true;
    try {
      do {
        again.current = false;
        try {
          const [nextListing, nextTemplates] = await Promise.all([
            client.list(project),
            client.templates(),
          ]);
          if (!mounted.current) return;
          setListing(nextListing);
          setTemplates(nextTemplates);
          setError(null);
        } catch (caught) {
          if (mounted.current) setError(asError(caught, 'Could not load instances'));
        }
      } while (again.current && mounted.current);
    } finally {
      inFlight.current = false;
    }
  }, [client, project]);

  const busy = anyStarting(listing);
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), busy ? busyIntervalMs : idleIntervalMs);
    return () => clearInterval(timer);
  }, [refresh, busy, busyIntervalMs, idleIntervalMs]);

  const launch = useCallback(
    async (request: LaunchRequest) => {
      setLaunching(true);
      try {
        const created = await client.launch(request);
        await refresh();
        return created;
      } finally {
        if (mounted.current) setLaunching(false);
      }
    },
    [client, refresh],
  );

  const stop = useCallback(
    async (id: string) => {
      setStopping((prev) => new Set(prev).add(id));
      try {
        await client.stop(id);
        await refresh();
      } finally {
        if (mounted.current) {
          setStopping((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
        }
      }
    },
    [client, refresh],
  );

  return {
    listing,
    templates,
    error,
    loading: listing === null && error === null,
    stopping,
    launching,
    refresh,
    launch,
    stop,
  };
}

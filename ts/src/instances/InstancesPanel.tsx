/**
 * Manage local instances: see what is running, launch one, stop one.
 *
 * ```tsx
 * const client = useMemo(() => createInstancesClient('/api/instances'), []);
 * <InstancesPanel client={client} project="shop" onError={(title, e) => toast(title, e)} />
 * ```
 *
 * Action failures are shown inline *and* passed to `onError`, so a host with
 * a toast system can route them there without the panel depending on it.
 */

import React, { useCallback, useMemo } from 'react';

import { Button, Skeleton } from '../controls';
import { describeError } from '../util/errors';
import type { InstancesClient } from './client';
import { InstanceRow } from './InstanceRow';
import { LaunchForm } from './LaunchForm';
import type { InstanceView, LaunchRequest } from './types';
import { useInstances } from './useInstances';
import type { UseInstancesOptions } from './useInstances';

import './instances.css';

export interface InstancesPanelProps extends UseInstancesOptions {
  client: InstancesClient;
  /** Called with a short title and the error when a launch or stop fails. */
  onError?(title: string, error: unknown): void;
  /** Called with the new instance once a launch is accepted. */
  onLaunched?(instance: InstanceView): void;
  heading?: string;
}

export function InstancesPanel({
  client,
  onError,
  onLaunched,
  heading = 'Local instances',
  ...options
}: InstancesPanelProps): React.ReactElement {
  const state = useInstances(client, options);
  const [actionError, setActionError] = React.useState<string | null>(null);

  const report = useCallback(
    (title: string, error: unknown) => {
      setActionError(`${title}: ${describeError(error)}`);
      onError?.(title, error);
    },
    [onError],
  );

  const launch = useCallback(
    (request: LaunchRequest) => {
      setActionError(null);
      state.launch(request).then(
        (created) => onLaunched?.(created),
        (error: unknown) => report('Launch failed', error),
      );
    },
    [state, onLaunched, report],
  );

  const stop = useCallback(
    (instance: InstanceView) => {
      setActionError(null);
      state.stop(instance.id).catch((error: unknown) => report(`Could not stop ${instance.name}`, error));
    },
    [state, report],
  );

  const names = useMemo(
    () => new Map((state.listing?.instances ?? []).map((i) => [i.id, i.name])),
    [state.listing],
  );
  const instances = state.listing?.instances ?? [];

  return (
    <section className="le-instances" aria-labelledby="le-instances-heading" aria-busy={state.loading}>
      <header className="le-instances__header">
        <h2 id="le-instances-heading">{heading}</h2>
        <Button variant="ghost" size="sm" onClick={() => void state.refresh()}>
          Refresh
        </Button>
      </header>

      {state.error !== null && (
        <div className="le-instances__error" role="alert">
          Could not load instances: {state.error.message}
          {state.listing !== null && ' — showing the last list that loaded.'}
        </div>
      )}
      {actionError !== null && (
        <div className="le-instances__error" role="alert">
          {actionError}
        </div>
      )}
      {state.listing?.unreadable.map((bad) => (
        <p key={bad.path} className="le-instances__error">
          Unreadable registry record {bad.path}: {bad.error}
        </p>
      ))}

      {state.loading ? (
        <div className="le-instances__list" aria-label="Loading instances">
          <Skeleton height="3rem" />
          <Skeleton height="3rem" />
        </div>
      ) : state.listing === null ? null : instances.length === 0 ? (
        <p className="le-instances__empty">
          Nothing is running. Launch a client against the main server to try a UI change, or a server
          of its own for a backend change.
        </p>
      ) : (
        <ul className="le-instances__list">
          {instances.map((instance) => (
            <InstanceRow
              key={instance.id}
              instance={instance}
              targetName={
                instance.target_instance_id
                  ? (names.get(instance.target_instance_id) ?? `${instance.target_instance_id} (gone)`)
                  : undefined
              }
              client={client}
              stopping={state.stopping.has(instance.id)}
              onStop={stop}
            />
          ))}
        </ul>
      )}

      {state.templates !== null && (
        <LaunchForm templates={state.templates} launching={state.launching} onLaunch={launch} />
      )}
    </section>
  );
}

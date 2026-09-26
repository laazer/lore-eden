/**
 * One instance: what it is, whether it is up, where it is, and how to stop it.
 */

import React, { useState } from 'react';

import { Button, Spinner, Tag } from '../controls';
import type { Tone } from '../controls';
import { describeError } from '../util/errors';
import type { InstancesClient } from './client';
import type { InstanceLogs, InstanceState, InstanceView } from './types';

const STATE_TONE: Record<InstanceState, Tone> = {
  starting: 'neutral',
  ready: 'ok',
  stalled: 'warn',
  exited: 'crit',
};

const STATE_LABEL: Record<InstanceState, string> = {
  starting: 'Starting',
  ready: 'Ready',
  stalled: 'Not ready',
  exited: 'Exited',
};

export interface InstanceRowProps {
  instance: InstanceView;
  /** Name of the instance this one targets, when it is a client. */
  targetName?: string;
  client: InstancesClient;
  stopping: boolean;
  onStop(instance: InstanceView): void;
}

function LogTail({ client, id }: { client: InstancesClient; id: string }): React.ReactElement {
  const [logs, setLogs] = useState<InstanceLogs | null>(null);
  const [error, setError] = useState<string | null>(null);

  React.useEffect(() => {
    let live = true;
    client
      .logs(id, 80)
      .then((next) => live && setLogs(next))
      .catch((caught: unknown) => live && setError(describeError(caught, 'Could not load the log')));
    return () => {
      live = false;
    };
  }, [client, id]);

  if (error !== null) return <p className="le-instances__error" role="alert">{error}</p>;
  if (logs === null) return <Spinner label="Loading log" />;
  if (logs.lines.length === 0) {
    return <p className="le-instances__note">The log is empty — the process has written nothing yet.</p>;
  }
  return (
    <pre className="le-instances__log" tabIndex={0} aria-label={`Log for ${id}`}>
      {logs.truncated ? '…\n' : ''}
      {logs.lines.join('\n')}
    </pre>
  );
}

export function InstanceRow({
  instance,
  targetName,
  client,
  stopping,
  onStop,
}: InstanceRowProps): React.ReactElement {
  const [showLogs, setShowLogs] = useState(false);
  const exited = instance.state === 'exited';
  const logsId = `le-instance-logs-${instance.id}`;
  const labels = Object.entries(instance.labels);

  return (
    <li className="le-instances__row">
      <div className="le-instances__head">
        <Tag tone={STATE_TONE[instance.state]}>{STATE_LABEL[instance.state]}</Tag>
        <strong className="le-instances__name">{instance.name}</strong>
        <span className="le-instances__meta">
          {instance.project} · {instance.role} {instance.kind}
          {targetName !== undefined && ` → ${targetName}`}
        </span>
      </div>
      <div className="le-instances__detail">
        {exited ? (
          <span className="le-instances__meta">{instance.url}</span>
        ) : (
          <a href={instance.url} target="_blank" rel="noreferrer">
            {instance.url}
          </a>
        )}
        {labels.map(([key, value]) => (
          <span key={key} className="le-instances__label">
            {key}: {value}
          </span>
        ))}
      </div>
      {instance.last_error && (
        <p className="le-instances__error" role={exited ? 'alert' : undefined}>
          {instance.last_error}
        </p>
      )}
      {instance.state === 'stalled' && (
        <p className="le-instances__note">
          Running, but not answering {instance.health_path ?? 'on its port'} after{' '}
          {instance.ready_timeout_seconds}s. Check its log.
        </p>
      )}
      <div className="le-instances__actions">
        {instance.log_path !== null && (
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={showLogs}
            aria-controls={logsId}
            onClick={() => setShowLogs((open) => !open)}
          >
            {showLogs ? 'Hide log' : 'Log'}
          </Button>
        )}
        {instance.managed ? (
          <Button
            variant={exited ? 'ghost' : 'danger'}
            size="sm"
            disabled={stopping}
            aria-busy={stopping}
            onClick={() => onStop(instance)}
          >
            {stopping ? 'Stopping…' : exited ? 'Dismiss' : 'Stop'}
          </Button>
        ) : (
          <span className="le-instances__meta" title="It registered itself; stop it where it was started.">
            Not managed here
          </span>
        )}
      </div>
      {showLogs && (
        <div id={logsId}>
          <LogTail client={client} id={instance.id} />
        </div>
      )}
    </li>
  );
}

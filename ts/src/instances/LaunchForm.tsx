/**
 * Launch an instance from one of the host's templates.
 *
 * The fields are the template's parameters, so the form is whatever the host
 * declared: a parameter with `choices` is a picker, anything else is text.
 * Defaults are pre-filled, which is what makes the common case one click.
 */

import React, { useMemo, useState } from 'react';

import { Button, Field, Select, TextInput } from '../controls';
import type { LaunchRequest, TemplateInfo, TemplateParam } from './types';

export interface LaunchFormProps {
  templates: readonly TemplateInfo[];
  launching: boolean;
  onLaunch(request: LaunchRequest): void;
}

const defaultsFor = (template: TemplateInfo | undefined): Record<string, string> =>
  Object.fromEntries((template?.params ?? []).map((p) => [p.key, p.default ?? '']));

function ParamField({
  param,
  value,
  onChange,
}: {
  param: TemplateParam;
  value: string;
  onChange(value: string): void;
}): React.ReactElement {
  const label = param.required ? `${param.label} (required)` : param.label;
  return (
    <Field label={label} hint={param.description || undefined}>
      {param.choices ? (
        <Select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          options={param.choices.map((choice) => ({ value: choice, label: choice }))}
        />
      ) : (
        <TextInput value={value} required={param.required} onChange={(e) => onChange(e.target.value)} />
      )}
    </Field>
  );
}

export function LaunchForm({ templates, launching, onLaunch }: LaunchFormProps): React.ReactElement {
  const [templateName, setTemplateName] = useState(templates[0]?.name ?? '');
  const template = useMemo(
    () => templates.find((t) => t.name === templateName) ?? templates[0],
    [templates, templateName],
  );
  const [name, setName] = useState('');
  // Typed values, kept per template name. Not reset by an effect on the
  // template object: the host re-fetches templates as it polls, every fetch is
  // a new object, and an effect keyed on it wiped whatever had been typed —
  // on a slow machine, between typing and clicking Launch. Keyed by name, a
  // switch to another template starts from its own defaults and a switch back
  // finds what was typed there.
  const [edits, setEdits] = useState<Record<string, Record<string, string>>>({});

  if (template === undefined) {
    return (
      <p className="le-instances__note">
        This host has registered no launch templates, so instances can only be listed here.
      </p>
    );
  }

  const params = edits[template.name] ?? defaultsFor(template);
  const setParam = (key: string, value: string): void =>
    setEdits((prev) => ({ ...prev, [template.name]: { ...params, [key]: value } }));
  const missing = template.params.filter((p) => p.required && !params[p.key]);
  const submit = (event: React.FormEvent): void => {
    event.preventDefault();
    if (launching || missing.length > 0) return;
    const filled = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== ''));
    onLaunch({ template: template.name, name: name.trim() || undefined, params: filled });
  };

  return (
    <form className="le-instances__form" onSubmit={submit} aria-label="Launch an instance">
      <Field label="Template" hint={template.description || undefined}>
        <Select
          value={template.name}
          onChange={(e) => setTemplateName(e.target.value)}
          options={templates.map((t) => ({ value: t.name, label: `${t.name} (${t.kind})` }))}
        />
      </Field>
      <Field label="Name" hint="Optional. Defaults to the template's name.">
        <TextInput value={name} onChange={(e) => setName(e.target.value)} placeholder={template.name} />
      </Field>
      {template.params.map((param) => (
        <ParamField
          key={param.key}
          param={param}
          value={params[param.key] ?? ''}
          onChange={(value) => setParam(param.key, value)}
        />
      ))}
      <Button type="submit" disabled={launching || missing.length > 0} aria-busy={launching}>
        {launching ? 'Launching…' : 'Launch'}
      </Button>
    </form>
  );
}

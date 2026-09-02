# lore-eden

Shared libraries for building on agents: a harness for driving agent workflows (MCP
transport, a stage state machine, CLI agent execution, approvals), a UI kit
(design tokens, a pane/canvas layout system, chat primitives), and a set of
repo-agnostic CI gates.

Nothing here is written from scratch. Every module arrives by extraction from one
of two existing projects — `loregarden`, an agent SDLC control plane, and
`loremaker` — chosen per layer by which implementation was already the stronger
one:

| Layer | Extracted from | Status |
|---|---|---|
| CI gates + managed-hook installer | loregarden `.lefthook/scripts/` | **shipped** — see [`gates/`](gates/) |
| Lint policy, reusable CI workflows, `hooks:*` Taskfile | loregarden, plus the drifted copies elsewhere | planned |
| MCP transport + external-server registry | loregarden `server/loregarden/mcp/` | **shipped** — see [`python/`](python/) |
| Stage state machine, workflow loader, gate runner | loregarden `server/loregarden/core/` | **shipped** — see [`python/`](python/) |
| Design tokens + theming (dark/light) | loremaker `client/src/liquid-glass/` | **shipped** — see [`ts/`](ts/) |
| Pane/canvas layout (split-grid, free placement, size tiers, z-order) | loregarden `client/src/lib/`, `components/views/` | **shipped** — see [`ts/`](ts/) |
| Chat primitives (composer, message list, streaming) | loregarden `client/src/components/` | **shipped** — see [`ts/`](ts/) |
| CLI executor + permission bridge | loregarden `server/loregarden/agents/` | **shipped** — see [`python/`](python/) |
| Approvals + orchestration dispatch | loregarden `services/orchestration.py` | **shipped** — see [`python/`](python/) |

The harness is meant to be usable for agent work that has nothing to do with
writing code, and extensible with MCP servers and APIs beyond the ones the source
projects happen to ship.

## What's here now

[`gates/`](gates/) — organization, safety and diff-scoping gates that run from
lefthook pre-commit hooks and from orchestration stage transitions, against a repo
whose layout they detect rather than assume. Its README covers installation, the
individual gates, scopes, configuration and waivers.

[`python/`](python/) — the agent harness. An MCP server you mount on FastAPI and
register tools into, the registry of third-party MCP servers a host makes
reachable, and a workflow engine: stage routing, YAML templates, and the shell
commands that gate a transition.

[`ts/`](ts/) — the UI kit. Design tokens defined once and derived into the views
callers need, a provider that publishes them as CSS custom properties and as
values resolved for the active mode, and a pane/canvas layout system: two
arrangements over one pane implementation, sized by tier and persisted through a
queue that will not lose an edit to the one before it.

The rest is planned, tracked as tickets rather than written here.

## Extraction rules

Load-bearing, not aspirational — they are how the source projects avoid breaking
while this is built:

1. **Copy, don't move.** An extraction copies code here. It does not delete or
   rewrite the source file. The source projects keep running untouched.
2. **Cut-over is separate, one component at a time.** Its bar is "the consuming
   project's existing test suite is still green after switching to lore-eden" —
   not "lore-eden exists." Rollback is reverting one commit; the original code is
   still there until a later cleanup.
3. **Consumed by path link while the API moves.** No publishing until the
   interfaces have stabilized against a second consumer.
4. **Every cut-over runs the consuming project's own gates**, not just these.
5. **This repo grades itself with its own gates.** If they cannot be lived with
   here, they should not be installed anywhere else.

## Requirements

The gates are deliberately dependency-free and run under whatever `python3` a
repo hands them (3.10+). The TypeScript gate ships its own parser; run
`npm install` in `gates/` once. The UI kit needs `npm install` in `ts/`, and the
harness `pip install -e "python[dev]"`.

## License

AGPL-3.0. See [LICENSE](LICENSE).

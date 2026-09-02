# lore-eden

Shared libraries extracted from [loregarden](../loregarden) and [loremaker](../loremaker):
an agent harness (MCP transport, stage orchestration, CLI agent execution, approvals) and a
UI kit (design tokens, pane/canvas layout, chat primitives).

Nothing here is written from scratch. Every module arrives by extraction from one of the two
source projects, chosen per layer by which implementation was already the stronger one:

| Layer | Extracted from |
|---|---|
| Shared CI gates + managed-block installer | loregarden `.lefthook/scripts/`, `scripts/install-workspace-hooks.sh` |
| Shared lint policy, reusable CI workflows, `hooks:*` Taskfile | loregarden + the drifted copies in loremaker, blobert, corpocoin, bridgepath |
| MCP transport + external-server registry | loregarden `server/loregarden/mcp/`, `services/mcp_registry.py` |
| Stage state machine, workflow loader, gate runner | loregarden `server/loregarden/core/`, `services/gate_runner.py` |
| Design tokens + theming (dark/light) | loremaker `client/src/liquid-glass/tokens.ts`, `theme/` |
| Pane/canvas layout (split-grid, free-placement, size tiers, z-order) | loregarden `client/src/lib/canvasLayout.ts`, `paneSize.ts`, `components/views/` |
| Chat primitives (composer, message list, streaming) | loregarden `client/src/components/studio/StudioChat.tsx`, `components/chat/` |
| CLI executor + permission bridge, approvals | loregarden `server/loregarden/agents/executors/`, `services/orchestration.py` |

## Status

Scaffold only. Work is tracked as tickets in loregarden under the `lore-eden` workspace.

## Extraction rules

These are load-bearing, not aspirational — they are how the two source projects avoid breaking
while this is built:

1. **Copy, don't move.** An extraction ticket copies code here. It does not delete or rewrite
   the source file in loregarden/loremaker. Both projects keep running untouched.
2. **Cut-over is a separate ticket per component.** Its acceptance criterion is "the source
   project's existing test suite is still green after switching its import to lore-eden" — not
   "lore-eden exists." Rollback is reverting one import-switch commit; the local code is still
   there until a later cleanup ticket.
3. **Consumed by path link while the API moves.** `npm workspaces` / `pip install -e` from the
   consuming repo. No publishing until the interfaces have stabilized against both consumers.
4. **Every cut-over runs the consuming project's own gates**, not just lore-eden's.

## Note on the gate library

The shared gates are the one part of this that already exists and already works cross-repo:
loregarden's `.lefthook/scripts/` gates detect repo layout rather than assuming it, and are
installed into other repos as a marker-delimited managed block. blobert and this repo both
carry that block today.

What they lack is a home. Both installed blocks reference loregarden by absolute filesystem
path with no versioning, and the two projects that predate the installer (loremaker, blobert)
still run stale vendored forks of the same files. Giving those gates a versioned package is
the first ticket for a reason.

## Layout

    python/    agent harness (FastAPI/Pydantic v2, Python 3.11)
    ts/        UI kit (React 19 + TypeScript)
    gates/     shared CI gates, hooks installer, lint policy, reusable workflows

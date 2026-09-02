# lore-eden

Shared libraries extracted from [loregarden](../loregarden) and [loremaker](../loremaker):
an agent harness (MCP transport, stage orchestration, CLI agent execution, approvals) and a
UI kit (design tokens, pane/canvas layout, chat primitives).

Nothing here is written from scratch. Every module arrives by extraction from one of the two
source projects, chosen per layer by which implementation was already the stronger one:

| Layer | Extracted from |
|---|---|
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

## Layout

    python/    agent harness (FastAPI/Pydantic v2, Python 3.11)
    ts/        UI kit (React 19 + TypeScript)

# Polymarket Integrated Workspace - AI Agent Guide

## Scope

This workspace is the isolated development baseline for the integrated Polymarket control panel project under `D:\AI\vibe_coding4`.

The goal of this workspace is:

- keep the current Windows/Linux single-user workflow available in source form
- integrate `POLYMARKET_MAKER_copytrade_v2` and `POLY_SMARTMONEY` behind one control panel
- evolve the panel toward VPS deployment, per-user isolated instances, and an Android shell client
- avoid breaking the original workspace in `D:\AI\vibe_coding3`

This workspace is for active development, not for preserving a frozen release build.

## Source Of Truth

Use this workspace as the only place for modifications related to the integrated version:

- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2`
- `D:\AI\vibe_coding4\POLY_SMARTMONEY`

The original workspace in `D:\AI\vibe_coding3` is a reference baseline and should not be modified unless explicitly requested.

## Project Direction

The intended product shape is:

1. Python strategy processes continue to run on Windows or Linux.
2. A single web control panel manages both `POLYMARKET_MAKER_copytrade_v2` and `POLY_SMARTMONEY`.
3. Linux VPS deployment is the primary target for user-facing operation.
4. Android is a shell/client over the remote panel, not a native host for the trading logic.
5. User isolation should be designed around one instance per user.

Do not optimize for packaging local Windows executables unless the task explicitly asks for it.

## Primary Code Areas

Treat these files as the main integration surface:

- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\server.py`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\config_store.py`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\runtime_paths.py`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\static\index.html`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\static\app.js`
- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2\panel\static\styles.css`

Strategy code under `copytrade`, `POLYMARKET_MAKER_AUTO`, and `POLY_SMARTMONEY` is core runtime logic and should be changed carefully.

## Working Principles

1. Preserve behavior first.
   - Prefer adapting the current panel and runtime flow over rewriting from scratch.
   - Keep the existing operational model familiar to current Windows/Linux users.

2. Separate control plane from execution plane.
   - The panel, authentication, deployment scripts, and mobile adaptation belong to the control plane.
   - Trading logic should remain in the strategy/runtime layer.

3. Design for per-user instance isolation.
   - Paths, config access, logs, and runtime metadata should move toward instance-scoped storage.
   - Avoid assumptions that only one global account or one global runtime exists forever.

4. Prefer Linux/VPS compatibility over Windows-only convenience.
   - Windows fallback support is useful, but VPS deployment is the target.
   - New runtime abstractions should not depend on `.bat`, Windows process flags, or packaged `.exe` files.

5. Keep secrets safe.
   - Do not print private keys, API secrets, or passphrases in logs or user-facing output.
   - If a new feature touches auth or config persistence, review secret exposure risk explicitly.

## Expected Near-Term Work

Near-term tasks should usually fall into one of these buckets:

- runtime path cleanup for integrated deployment
- config and log path instance isolation
- unified service control for `v2`, `autorun`, and `v3/smartmoney`
- authentication and session handling
- mobile web layout improvements
- VPS deployment scripts and documentation
- Android shell packaging after the web panel is stable

## Editing Guidance

- Prefer small targeted changes over broad rewrites.
- Do not replace working source files wholesale unless there is a strong reason.
- Keep text encoding stable. If a file already contains encoding issues, avoid making them worse.
- Preserve user data formats unless migration is part of the task.
- If a change affects config file schema or runtime layout, document the migration impact.

## Validation Expectations

After code changes:

- run focused validation relevant to the touched area
- check for syntax/runtime regressions where practical
- verify the panel still resolves both strategy roots correctly when path logic changes
- verify API handlers still match frontend expectations when panel endpoints change

If full end-to-end validation is not possible, state what was checked and what remains unverified.

## File And Deployment Conventions

Suggested workspace evolution:

- `D:\AI\vibe_coding4\POLYMARKET_MAKER_copytrade_v2`
- `D:\AI\vibe_coding4\POLY_SMARTMONEY`
- `D:\AI\vibe_coding4\deploy`
- `D:\AI\vibe_coding4\android`
- `D:\AI\vibe_coding4\docs`
- `D:\AI\vibe_coding4\instances`

When adding new deployment or instance-management code, place it in a predictable top-level location instead of burying it inside unrelated runtime modules.

## Decision Rules

- Use source projects as the development base, not packaged output directories.
- Prefer extending the existing panel over building a second admin surface.
- Avoid introducing multi-tenant shared-backend complexity until single-instance-per-user deployment is stable.
- When a task has both a quick patch and a structure-improving option, prefer the structure-improving option if it does not create large migration risk.

## Communication Rules

- Be direct and concrete.
- Explain tradeoffs when choosing architecture or deployment direction.
- Flag security, migration, or operational risk early.
- Do not invent successful verification. State clearly what was or was not tested.
- End every assistant reply with the exact suffix `喵~` so the user can quickly verify the workspace rules are still being followed.

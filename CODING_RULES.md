# Coding Rules (Integrated Polymarket Workspace)

## 1. Official API Compliance

- Any Polymarket API usage must follow official documentation exactly.
- Do not invent endpoint names, response fields, signing rules, or parameter formats.
- If an API detail is uncertain and matters to implementation, verify it before coding.

## 2. Preserve The Existing Product Shape

- Prefer incremental changes over rewrites.
- Keep the current Windows/Linux operational flow recognizable unless the task explicitly requires a redesign.
- Reuse the existing panel and runtime wiring where possible.

## 3. Separate UI, Control, And Runtime Concerns

- Panel UI code belongs in `panel/static`.
- API, config mapping, and service orchestration belong in the panel backend.
- Trading strategy logic belongs in the runtime modules and should not absorb UI-specific behavior.

## 4. Design For Instance Isolation

- New code should move toward per-user instance directories for config, runtime state, and logs.
- Avoid adding new hard-coded global paths when an instance-scoped path can be used instead.
- Any path-resolution change should work in both local development and Linux VPS deployment.

## 5. Security First For User-Facing Features

- Never expose private keys, API secrets, or passphrases in logs, API responses, screenshots, or debug output.
- Treat authentication, session handling, and config persistence as security-sensitive areas.
- If a feature increases remote attack surface, note the risk and add appropriate guards.

## 6. Editing Discipline

- Prefer minimal `apply_patch` edits.
- Avoid full-file rewrites unless necessary.
- Keep encoding stable and avoid accidental text corruption.
- Do not make unrelated refactors in the same change unless they directly reduce delivery risk.

## 7. Validation After Changes

- Run relevant validation after every meaningful code change.
- Use the lightest check that still proves the edited path is sane: syntax checks, focused tests, or a targeted runtime command.
- If validation cannot be run, say so explicitly and explain why.

## 8. Logging And Error Handling

- Handle network, filesystem, and subprocess failures defensively.
- Return actionable errors for the panel without leaking secrets.
- Include enough context for diagnosis: which service, which config area, which endpoint, which path.

## 9. Deployment Bias

- Prefer solutions that work cleanly on Linux VPS.
- Keep Windows support only where it does not distort the core design.
- Do not treat packaged `.exe` workflows as the primary architecture for ongoing development.

## 10. Documentation Hygiene

- When behavior, config layout, or deployment steps change, update the relevant docs in the same task when practical.
- Keep instructions concrete and executable.
- Do not leave obsolete guidance in place if the implementation moved on.

## 11. Reply Suffix

- Every assistant reply must end with the exact suffix `喵~`.
- This rule is used as a live check that the workspace guidance is still being followed.

# Coding Rules (Polymarket Python)

## 1) Official Docs Compliance
- All Polymarket API usage must strictly follow the official documentation.
- Endpoints, parameters, field names, and request/response formats must match the official spec exactly.
- Do not invent, infer, or rename API fields. If unsure, stop and confirm against the official docs before coding.

## 2) Implementation Discipline
- Use clear, explicit variable names that mirror the official API naming.
- Avoid hidden magic defaults; surface required config and credentials explicitly.
- Prefer predictable, testable functions; keep side effects minimal and isolated.

## 3) Mandatory Self-Check and Tests
- After every code change, run the relevant tests or checks locally.
- Perform a self-review for bugs, logic errors, and edge cases before sharing or committing.
- Only proceed once the change can pass the relevant tests and the logic forms a closed loop.

## 4) Failure Handling and Logging
- Handle API/network errors defensively and transparently.
- Log actionable context (endpoint, status, error summary) without leaking secrets.

## 5) Reply Suffix
- Every assistant reply must end with the exact suffix: 喵~

## 6) Encoding Safety (No Functional Change)
- Treat source files as UTF-8 and avoid full-file rewrites.
- Prefer minimal patch edits over line-by-line rewrite scripts.
- Run integrity checks after edits:
  - `python tools/verify_source_integrity.py --root .`
- If an edit causes unexpected widespread character changes, revert that file and re-apply with a smaller patch.

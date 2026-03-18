#!/usr/bin/env python3
"""Non-functional source integrity checks for vibe-coding safety.

Checks:
1) Python files can be decoded as UTF-8.
2) Python files compile successfully.
"""

from __future__ import annotations

import argparse
import os
import py_compile
import sys
from pathlib import Path


def iter_py_files(root: Path):
    skip_dirs = {".git", ".pytest_cache", "__pycache__", ".venv", "venv"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def check_utf8(path: Path) -> None:
    path.read_text(encoding="utf-8")


def check_compile(path: Path) -> None:
    py_compile.compile(str(path), doraise=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root to scan (default: current directory)",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    failures: list[str] = []
    total = 0

    for py in iter_py_files(root):
        total += 1
        try:
            check_utf8(py)
        except Exception as exc:
            failures.append(f"UTF-8 decode failed: {py} -> {exc}")
            continue
        try:
            check_compile(py)
        except Exception as exc:
            failures.append(f"Compile failed: {py} -> {exc}")

    if failures:
        print(f"[FAIL] {len(failures)} / {total} file(s) failed integrity checks.")
        for item in failures:
            print(f"- {item}")
        return 1

    print(f"[OK] Integrity checks passed for {total} Python file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

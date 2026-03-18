from __future__ import annotations

import os
import sys
from pathlib import Path


def _looks_like_repo_root(path: Path) -> bool:
    return (path / "POLYMARKET_MAKER_copytrade_v2").exists() and (path / "POLY_SMARTMONEY").exists()


def _search_repo_root(*seeds: Path) -> Path | None:
    for seed in seeds:
        current = seed.resolve()
        for candidate in (current, *current.parents):
            if _looks_like_repo_root(candidate):
                return candidate
    return None


def resolve_repo_root() -> Path:
    override = os.getenv("POLY_APP_ROOT")
    if override:
        return Path(override).resolve()

    search_seeds = [Path.cwd(), Path(__file__).resolve()]
    if getattr(sys, "frozen", False):
        search_seeds.append(Path(sys.executable).resolve())

    found = _search_repo_root(*search_seeds)
    if found is not None:
        return found

    return Path(__file__).resolve().parents[2]


def resolve_v2_root() -> Path:
    return resolve_repo_root() / "POLYMARKET_MAKER_copytrade_v2"


def resolve_v3_root() -> Path:
    return resolve_repo_root() / "POLY_SMARTMONEY" / "copytrade_v3_muti"


def resolve_desktop_bin_dir() -> Path | None:
    override = os.getenv("POLY_DESKTOP_BIN_DIR")
    if override:
        return Path(override).resolve()

    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parent / "bin"
        if candidate.exists():
            return candidate
    return None

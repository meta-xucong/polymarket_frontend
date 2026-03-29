from __future__ import annotations

import os
import sys
from pathlib import Path


def _looks_like_workspace_root(path: Path) -> bool:
    return (path / "POLYMARKET_MAKER_copytrade_v2").exists() and (path / "POLY_SMARTMONEY").exists()


def _looks_like_portable_app_root(path: Path) -> bool:
    return _looks_like_workspace_root(path)


def _search_workspace_root(*seeds: Path) -> Path | None:
    for seed in seeds:
        current = seed.resolve()
        for candidate in (current, *current.parents):
            if _looks_like_workspace_root(candidate):
                return candidate
    return None


def _resolve_path_override(env_name: str) -> Path | None:
    raw = str(os.getenv(env_name) or "").strip()
    if not raw:
        return None
    return Path(raw).resolve()


def resolve_source_root() -> Path:
    override = os.getenv("POLY_APP_ROOT")
    if override:
        return Path(override).resolve()

    cwd_app_root = Path.cwd() / "app_root"
    if _looks_like_portable_app_root(cwd_app_root):
        return cwd_app_root.resolve()

    module_app_root = Path(__file__).resolve().parent / "app_root"
    if _looks_like_portable_app_root(module_app_root):
        return module_app_root.resolve()

    exe_app_root = Path(sys.executable).resolve().parent / "app_root"
    if _looks_like_portable_app_root(exe_app_root):
        return exe_app_root.resolve()

    search_seeds = [Path.cwd(), Path(__file__).resolve()]
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        search_seeds.append(exe_path)
        # In onefile mode, sys.executable may point to a temp extraction path.
        # Prefer probing app_root near the original launch location first.
        launch_hints = [Path.cwd(), exe_path.parent]
        if sys.argv:
            with_suppress = str(sys.argv[0] or "").strip()
            if with_suppress:
                arg0_path = Path(with_suppress).resolve()
                search_seeds.append(arg0_path)
                launch_hints.append(arg0_path.parent)
        for hint in launch_hints:
            portable_app_root = hint / "app_root"
            if _looks_like_portable_app_root(portable_app_root):
                return portable_app_root.resolve()

    found = _search_workspace_root(*search_seeds)
    if found is not None:
        return found

    return Path(__file__).resolve().parents[2]


def resolve_repo_root() -> Path:
    # Backward-compatible alias used by older imports.
    return resolve_source_root()


def resolve_instance_root() -> Path:
    override = _resolve_path_override("POLY_INSTANCE_ROOT")
    if override is not None:
        return override
    return resolve_source_root()


def resolve_v2_source_root() -> Path:
    return resolve_source_root() / "POLYMARKET_MAKER_copytrade_v2"


def resolve_v3_source_root() -> Path:
    return resolve_source_root() / "POLY_SMARTMONEY" / "copytrade_v3_muti"


def resolve_v2_root() -> Path:
    override = _resolve_path_override("POLY_V2_ROOT")
    if override is not None:
        return override

    instance_root = resolve_instance_root()
    source_root = resolve_source_root()
    candidates = [
        instance_root / "POLYMARKET_MAKER_copytrade_v2",
        instance_root / "v2",
        source_root / "POLYMARKET_MAKER_copytrade_v2",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[-1].resolve()


def resolve_v3_root() -> Path:
    override = _resolve_path_override("POLY_V3_ROOT")
    if override is not None:
        return override

    instance_root = resolve_instance_root()
    source_root = resolve_source_root()
    candidates = [
        instance_root / "POLY_SMARTMONEY" / "copytrade_v3_muti",
        instance_root / "smartmoney",
        source_root / "POLY_SMARTMONEY" / "copytrade_v3_muti",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[-1].resolve()


def resolve_logs_root() -> Path:
    override = _resolve_path_override("POLY_LOG_ROOT")
    if override is not None:
        return override
    return (resolve_instance_root() / "logs").resolve()


def resolve_run_root() -> Path:
    override = _resolve_path_override("POLY_RUN_ROOT")
    if override is not None:
        return override
    return (resolve_instance_root() / "run").resolve()


def resolve_desktop_bin_dir() -> Path | None:
    override = os.getenv("POLY_DESKTOP_BIN_DIR")
    if override:
        return Path(override).resolve()

    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parent / "bin"
        if candidate.exists():
            return candidate
    return None

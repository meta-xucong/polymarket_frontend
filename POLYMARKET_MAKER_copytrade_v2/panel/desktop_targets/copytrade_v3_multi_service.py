from __future__ import annotations

import os
import sys
from pathlib import Path


def _ensure_panel_path() -> None:
    panel_dir = Path(__file__).resolve().parents[1]
    panel_dir_str = str(panel_dir)
    if panel_dir_str not in sys.path:
        sys.path.insert(0, panel_dir_str)


_ensure_panel_path()

from runtime_paths import resolve_repo_root, resolve_v3_root


def main() -> None:
    repo_root = resolve_repo_root()
    v3_dir = resolve_v3_root()
    os.chdir(v3_dir)
    sys.path.insert(0, str(repo_root / "POLY_SMARTMONEY"))
    sys.path.insert(0, str(v3_dir))
    from copytrade_run import main as run_main

    run_main()


if __name__ == "__main__":
    main()

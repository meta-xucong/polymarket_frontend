from __future__ import annotations

import os
import sys

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

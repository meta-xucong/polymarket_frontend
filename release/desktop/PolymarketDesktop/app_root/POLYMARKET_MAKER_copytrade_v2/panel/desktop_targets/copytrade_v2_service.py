from __future__ import annotations

import os
import sys

from runtime_paths import resolve_v2_root


def main() -> None:
    copytrade_dir = resolve_v2_root() / "copytrade"
    os.chdir(copytrade_dir)
    sys.path.insert(0, str(copytrade_dir))
    from copytrade_run import main as run_main

    run_main(["--config", "copytrade_config.json"])


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
import sys

from runtime_paths import resolve_v2_root


def main() -> None:
    autorun_dir = resolve_v2_root() / "POLYMARKET_MAKER_AUTO"
    maker_dir = autorun_dir / "POLYMARKET_MAKER"
    os.chdir(autorun_dir)
    sys.path.insert(0, str(autorun_dir))
    sys.path.insert(0, str(maker_dir))
    from poly_maker_autorun import main as run_main

    run_main(["--no-repl"])


if __name__ == "__main__":
    main()

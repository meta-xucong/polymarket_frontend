from __future__ import annotations

import os

from desktop_launcher import main


def _main() -> None:
    os.environ["POLY_DESKTOP_FORCE_BROWSER"] = "1"
    os.environ["POLY_DESKTOP_APP_MODE"] = "browser"
    main()


if __name__ == "__main__":
    _main()

from __future__ import annotations

import runpy
import sys
import unittest
from pathlib import Path


class DesktopTargetBootstrapTests(unittest.TestCase):
    def test_desktop_target_scripts_load_runtime_paths(self) -> None:
        panel_dir = Path(__file__).resolve().parents[1]
        if str(panel_dir) not in sys.path:
            sys.path.insert(0, str(panel_dir))

        targets = (
            panel_dir / "desktop_targets" / "autorun_v2_service.py",
            panel_dir / "desktop_targets" / "copytrade_v2_service.py",
            panel_dir / "desktop_targets" / "copytrade_v3_multi_service.py",
        )

        for target in targets:
            loaded_globals = runpy.run_path(str(target), run_name="__probe__")
            self.assertIn("main", loaded_globals, msg=f"missing main() in {target.name}")


if __name__ == "__main__":
    unittest.main()

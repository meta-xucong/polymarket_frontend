from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
PANEL_DIR = REPO_ROOT / "panel"
if str(PANEL_DIR) not in sys.path:
    sys.path.insert(0, str(PANEL_DIR))

import server


def test_local_service_start_stop_round_trip(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    svc_dir = tmp_path / "svc"
    svc_dir.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "svc.log"
    script_path = svc_dir / "dummy.py"
    script_path.write_text(
        "import os\n"
        "import time\n"
        "from pathlib import Path\n"
        "stop = Path(os.environ['POLY_PANEL_STOP_FILE'])\n"
        "while not stop.exists():\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(server, "RUN_DIR", run_dir)
    monkeypatch.setattr(server, "LOCAL_SERVICE_SPECS", {
        "copytrade": {
            "cwd": svc_dir,
            "cmd": [sys.executable, "dummy.py"],
            "log": log_path,
        }
    })

    started = server._start_local_service("copytrade")
    try:
        assert started["ok"] is True
        pid = started["pid"]
        assert server._pid_exists(pid) is True

        status = server._local_service_status()
        assert status["services"]["copytrade"]["active"] is True

        stopped = server._stop_local_service("copytrade")
        assert stopped["ok"] is True
        assert stopped["message"] == "stopped gracefully"
    finally:
        if server._pid_exists(server._read_pid("copytrade")):
            server._stop_local_service("copytrade")

    assert server._pid_exists(pid) is False

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from copytrade_run import _resolve_runtime_mirror_paths, _write_json_with_mirrors


def test_resolve_runtime_mirror_paths_in_instance_mode(monkeypatch, tmp_path):
    source_root = tmp_path / "source"
    instance_root = tmp_path / "instance"
    source_root.mkdir()
    instance_root.mkdir()

    monkeypatch.setenv("POLY_APP_ROOT", str(source_root))
    monkeypatch.setenv("POLY_INSTANCE_ROOT", str(instance_root))

    runtime_path = instance_root / "v2" / "copytrade" / "tokens_from_copytrade.json"
    paths = _resolve_runtime_mirror_paths(runtime_path)

    assert runtime_path in paths
    assert source_root / "POLYMARKET_MAKER_copytrade_v2" / "copytrade" / "tokens_from_copytrade.json" in paths


def test_write_json_with_mirrors_updates_instance_and_source(monkeypatch, tmp_path):
    source_root = tmp_path / "source"
    instance_root = tmp_path / "instance"
    source_root.mkdir()
    instance_root.mkdir()

    monkeypatch.setenv("POLY_APP_ROOT", str(source_root))
    monkeypatch.setenv("POLY_INSTANCE_ROOT", str(instance_root))

    runtime_path = instance_root / "v2" / "copytrade" / "copytrade_state.json"
    payload = {"updated_at": "now", "targets": {"a": {"since_ms": 1}}}

    _write_json_with_mirrors(runtime_path, payload)

    source_path = source_root / "POLYMARKET_MAKER_copytrade_v2" / "copytrade" / "copytrade_state.json"
    assert json.loads(runtime_path.read_text(encoding="utf-8")) == payload
    assert json.loads(source_path.read_text(encoding="utf-8")) == payload

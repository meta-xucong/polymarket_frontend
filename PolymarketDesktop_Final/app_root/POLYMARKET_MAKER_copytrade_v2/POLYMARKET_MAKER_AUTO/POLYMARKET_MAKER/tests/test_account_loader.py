from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from account_loader import get_account_value, get_required_account_value, load_account_config


def test_load_account_config_reads_json(tmp_path: Path) -> None:
    path = tmp_path / "account.json"
    payload = {"POLY_HOST": "https://clob.polymarket.com", "POLY_CHAIN_ID": 137}
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_account_config(path)

    assert loaded == payload


def test_get_account_value_prefers_json_over_env(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "account.json"
    path.write_text(json.dumps({"POLY_FUNDER": "0xjson"}), encoding="utf-8")
    monkeypatch.setenv("POLY_FUNDER", "0xenv")

    value = get_account_value("POLY_FUNDER", path=path)

    assert value == "0xjson"


def test_get_account_value_falls_back_to_env_when_json_missing(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "account.json"
    path.write_text(json.dumps({"POLY_FUNDER": "REPLACE_WITH_FUNDER_ADDRESS"}), encoding="utf-8")
    monkeypatch.setenv("POLY_FUNDER", "0xenv")

    value = get_account_value("POLY_FUNDER", path=path)

    assert value == "0xenv"


def test_get_required_account_value_raises_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "account.json"
    path.write_text("{}", encoding="utf-8")

    try:
        get_required_account_value("POLY_KEY", path=path)
    except KeyError as exc:
        assert exc.args == ("POLY_KEY",)
    else:  # pragma: no cover
        raise AssertionError("expected KeyError")

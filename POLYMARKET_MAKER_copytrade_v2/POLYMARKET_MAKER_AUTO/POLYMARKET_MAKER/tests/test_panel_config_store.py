from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
PANEL_DIR = REPO_ROOT / "panel"
if str(PANEL_DIR) not in sys.path:
    sys.path.insert(0, str(PANEL_DIR))

import config_store


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_save_account_payload_round_trip(tmp_path: Path, monkeypatch) -> None:
    account_path = tmp_path / "account.json"
    _write_json(
        account_path,
        {
            "POLY_HOST": "https://clob.polymarket.com",
            "POLY_CHAIN_ID": 137,
            "POLY_SIGNATURE": 2,
            "POLY_KEY": "old-key",
            "POLY_FUNDER": "old-funder",
        },
    )
    monkeypatch.setattr(config_store, "ACCOUNT_PATH", account_path)

    saved = config_store.save_account_payload(
        {
            "POLY_KEY": "new-key",
            "POLY_FUNDER": "0xabc",
            "POLY_API_KEY": "api-key",
        }
    )

    assert saved["POLY_KEY"] == "new-key"
    assert saved["POLY_FUNDER"] == "0xabc"
    assert saved["POLY_API_KEY"] == "api-key"


def test_save_settings_payload_updates_existing_files(tmp_path: Path, monkeypatch) -> None:
    copytrade_path = tmp_path / "copytrade_config.json"
    global_path = tmp_path / "global_config.json"
    run_params_path = tmp_path / "run_params.json"
    strategy_defaults_path = tmp_path / "strategy_defaults.json"

    _write_json(copytrade_path, {"poll_interval_sec": 60, "targets": [{"account": "0xold", "min_size": 5.0, "enabled": True}]})
    _write_json(global_path, {"scheduler": {"max_concurrent_tasks": 10, "copytrade_poll_seconds": 30, "command_poll_seconds": 30, "strategy_mode": "classic", "burst_slots": 10}})
    _write_json(run_params_path, {"drop_pct": 0.0, "profit_pct": 0.003, "sell_mode": "aggressive", "shock_guard": {"enabled": False, "shock_window_sec": 180, "shock_drop_pct": 0.1}})
    _write_json(strategy_defaults_path, {"default": {"order_size": 10.0, "max_position_per_market": 10.0}})

    monkeypatch.setattr(config_store, "COPYTRADE_CONFIG_PATH", copytrade_path)
    monkeypatch.setattr(config_store, "GLOBAL_CONFIG_PATH", global_path)
    monkeypatch.setattr(config_store, "RUN_PARAMS_PATH", run_params_path)
    monkeypatch.setattr(config_store, "STRATEGY_DEFAULTS_PATH", strategy_defaults_path)

    saved = config_store.save_settings_payload(
        {
            "copytrade": {
                "target_addresses": ["0x1", "0x2"],
                "poll_interval_sec": 24,
                "min_size": 7,
            },
            "scheduler": {
                "max_concurrent_tasks": 15,
                "copytrade_poll_seconds": 20,
                "command_poll_seconds": 9,
                "strategy_mode": "aggressive",
                "burst_slots": 4,
            },
            "strategy": {
                "order_size": 22,
                "max_position_per_market": 99,
                "drop_pct": 0.02,
                "profit_pct": 0.01,
                "sell_mode": "conservative",
                "shock_guard_enabled": True,
                "shock_window_sec": 90,
                "shock_drop_pct": 0.07,
            },
        }
    )

    assert saved["copytrade"]["target_addresses"] == ["0x1", "0x2"]
    assert saved["copytrade"]["min_size"] == 7.0
    assert saved["scheduler"]["max_concurrent_tasks"] == 15
    assert saved["strategy"]["order_size"] == 22.0
    assert saved["strategy"]["shock_guard_enabled"] is True


def test_v3_settings_and_account_round_trip(tmp_path: Path, monkeypatch) -> None:
    v3_config_path = tmp_path / "copytrade_config.json"
    v3_accounts_path = tmp_path / "accounts.json"
    v3_log_dir = tmp_path / "logs"
    v3_log_dir.mkdir(parents=True, exist_ok=True)
    (v3_log_dir / "copytrade_P12.log").write_text("line1\nline2\n", encoding="utf-8")

    _write_json(
        v3_config_path,
        {
            "target_addresses": ["0xtarget1"],
            "poll_interval_sec": 24,
            "poll_interval_sec_exiting": 4,
            "boot_sync_mode": "baseline_only",
            "actions_replay_window_sec": 3600,
            "follow_new_topics_only": False,
            "min_order_usd": 1,
            "max_order_usd": 6,
            "max_notional_per_token": 10,
            "max_notional_total": 20,
            "taker_enabled": True,
            "taker_spread_threshold": 0.011,
            "taker_order_type": "FAK",
            "maker_max_wait_sec": 0,
            "maker_to_taker_enabled": False,
            "lowp_guard_enabled": True,
            "lowp_price_threshold": 0.05,
            "lowp_follow_ratio_mult": 0.02,
            "lowp_min_order_usd": 1,
            "lowp_max_order_usd": 2,
            "log_dir": "logs",
        },
    )
    _write_json(
        v3_accounts_path,
        {
            "accounts": [
                {
                    "name": "P12",
                    "my_address": "0x1111111111111111111111111111111111111111",
                    "private_key": "key-1",
                    "env_key_suffix": "",
                    "follow_ratio": 1.0,
                    "enabled": True,
                    "max_notional_per_token": 100,
                    "max_notional_total": 200,
                },
                {
                    "name": "P13",
                    "my_address": "0x2222222222222222222222222222222222222222",
                    "private_key": "key-2",
                    "env_key_suffix": "_2",
                    "follow_ratio": 0.5,
                    "enabled": False,
                    "max_notional_per_token": 50,
                    "max_notional_total": 80,
                },
            ]
        },
    )

    monkeypatch.setattr(config_store, "V3_CONFIG_PATH", v3_config_path)
    monkeypatch.setattr(config_store, "V3_ACCOUNTS_PATH", v3_accounts_path)
    monkeypatch.setattr(config_store, "V3_BASE_DIR", tmp_path)

    loaded = config_store.get_v3_settings_payload()
    assert loaded["global"]["target_addresses"] == ["0xtarget1"]
    assert loaded["global"]["boot_sync_mode"] == "baseline_only"
    assert loaded["accounts"][0]["name"] == "P12"
    assert loaded["selected_account"]["name"] == "P12"

    saved_settings = config_store.save_v3_settings_payload(
        {
            "global": {
                "target_addresses": ["0xtarget2", "0xtarget3"],
                "poll_interval_sec": 30,
                "poll_interval_sec_exiting": 5,
                "boot_sync_mode": "baseline_replay",
                "actions_replay_window_sec": 7200,
                "follow_new_topics_only": True,
                "max_notional_per_token": 66,
                "max_notional_total": 188,
                "taker_enabled": False,
                "taker_spread_threshold": 0.009,
                "maker_max_wait_sec": 15,
                "maker_to_taker_enabled": True,
            }
        }
    )
    assert saved_settings["global"]["poll_interval_sec"] == 30.0
    assert saved_settings["global"]["target_addresses"] == ["0xtarget2", "0xtarget3"]
    assert saved_settings["global"]["boot_sync_mode"] == "baseline_replay"
    assert saved_settings["global"]["actions_replay_window_sec"] == 7200
    assert saved_settings["global"]["follow_new_topics_only"] is True
    assert saved_settings["global"]["maker_to_taker_enabled"] is True

    saved_account = config_store.save_v3_account_payload(
        1,
        {
            "name": "P13-new",
            "my_address": "0x3333333333333333333333333333333333333333",
            "private_key": "key-3",
            "env_key_suffix": "_3",
            "follow_ratio": 0.75,
            "enabled": True,
            "max_notional_per_token": "",
            "max_notional_total": "",
        },
    )
    assert saved_account["name"] == "P13-new"
    assert saved_account["private_key"] == "key-3"
    assert saved_account["follow_ratio"] == 0.75
    assert saved_account["enabled"] is True
    assert saved_account["max_notional_per_token"] is None
    assert saved_account["max_notional_total"] is None

    runtime = config_store.get_v3_runtime_payload()
    assert runtime["active_account_count"] == 2
    assert runtime["target_address_count"] == 2
    assert "line2" in runtime["copytrade_log_tail"]


def test_delete_v3_account_keeps_remaining_accounts(tmp_path: Path, monkeypatch) -> None:
    v3_config_path = tmp_path / "copytrade_config.json"
    v3_accounts_path = tmp_path / "accounts.json"
    _write_json(v3_config_path, {"target_addresses": ["0xtarget1"]})
    _write_json(
        v3_accounts_path,
        {
            "accounts": [
                {"name": "A", "my_address": "0x1111111111111111111111111111111111111111", "private_key": "k1"},
                {"name": "B", "my_address": "0x2222222222222222222222222222222222222222", "private_key": "k2"},
            ]
        },
    )
    monkeypatch.setattr(config_store, "V3_CONFIG_PATH", v3_config_path)
    monkeypatch.setattr(config_store, "V3_ACCOUNTS_PATH", v3_accounts_path)
    monkeypatch.setattr(config_store, "V3_BASE_DIR", tmp_path)

    saved = config_store.delete_v3_account_payload(0)
    assert [item["name"] for item in saved["accounts"]] == ["B"]

    try:
        config_store.delete_v3_account_payload(0)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "at least one account" in str(exc)

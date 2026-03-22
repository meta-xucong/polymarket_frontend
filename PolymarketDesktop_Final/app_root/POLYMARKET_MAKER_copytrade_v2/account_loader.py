from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

ACCOUNT_JSON_PATH = Path(__file__).resolve().with_name("account.json")


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return True
        if text.startswith("REPLACE_WITH_"):
            return True
    return False


def load_account_config(path: Optional[Path] = None) -> Dict[str, Any]:
    account_path = path or ACCOUNT_JSON_PATH
    if not account_path.exists():
        return {}

    payload = json.loads(account_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"account.json must contain a JSON object: {account_path}")
    return payload


def get_account_value(key: str, default: Any = None, *, path: Optional[Path] = None) -> Any:
    if path is not None:
        payload = load_account_config(path)
        value = payload.get(key)
        if not _is_missing(value):
            return value

        env_value = os.getenv(key)
        if not _is_missing(env_value):
            return env_value

        return default

    env_value = os.getenv(key)
    if not _is_missing(env_value):
        return env_value

    return default


def get_required_account_value(key: str, *, path: Optional[Path] = None) -> Any:
    value = get_account_value(key, path=path)
    if _is_missing(value):
        raise KeyError(key)
    return value

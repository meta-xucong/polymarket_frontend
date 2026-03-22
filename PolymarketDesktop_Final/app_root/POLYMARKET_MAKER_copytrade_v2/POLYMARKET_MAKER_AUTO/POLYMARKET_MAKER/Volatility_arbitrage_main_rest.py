# Volatility_arbitrage_main.py
# -*- coding: utf-8 -*-
"""
Polymarket CLOB API 接入主模块（最小版）

用途：被其它模块（价格查询/买入/卖出）导入复用，提供一个已完成鉴权的 ClobClient 实例。

环境变量（必须）：
- POLY_KEY          : 私钥（hex 字符串，0x 前缀可选）
- POLY_FUNDER       : Proxy Wallet / Deposit Address（你的充值地址）

环境变量（可选，带默认）：
- POLY_HOST         : 默认 https://clob.polymarket.com
- POLY_CHAIN_ID     : 默认 137（Polygon）
- POLY_SIGNATURE    : 默认 2（EIP-712）

用法：
>>> from Volatility_arbitrage_main import get_client
>>> client = get_client()
>>> # 之后在任意模块里复用 client 即可下单/询价
"""
from py_clob_client.client import ClobClient
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from account_loader import get_account_value, get_required_account_value

# ---- 默认配置 ----
DEFAULT_HOST = "https://clob.polymarket.com"
DEFAULT_CHAIN_ID = 137
DEFAULT_SIGNATURE_TYPE = 2

_CLIENT_SINGLETON = None  # 模块级单例


def _normalize_privkey(k: str) -> str:
    # 允许传入带/不带 0x 的 hex；统一去掉 0x 前缀
    return k[2:] if k.startswith(("0x", "0X")) else k


def init_client() -> ClobClient:
    host = str(get_account_value("POLY_HOST", DEFAULT_HOST))
    chain_id = int(get_account_value("POLY_CHAIN_ID", DEFAULT_CHAIN_ID))
    signature_type = int(get_account_value("POLY_SIGNATURE", DEFAULT_SIGNATURE_TYPE))

    key = str(get_required_account_value("POLY_KEY"))
    funder = str(get_required_account_value("POLY_FUNDER"))

    key = _normalize_privkey(key)

    client = ClobClient(
        host,
        key=key,
        chain_id=chain_id,
        signature_type=signature_type,
        funder=funder,
    )
    # 生成并设置 API 凭证（基于私钥派生）
    api_creds = client.create_or_derive_api_creds()
    client.set_api_creds(api_creds)
    try:
        setattr(client, "api_creds", api_creds)
    except Exception:
        pass
    return client


def get_client() -> ClobClient:
    """获取（或懒加载）单例客户端。"""
    global _CLIENT_SINGLETON
    if _CLIENT_SINGLETON is None:
        _CLIENT_SINGLETON = init_client()
    return _CLIENT_SINGLETON


if __name__ == "__main__":
    # 简单自检：仅做初始化，不发起额外网络调用
    c = get_client()
    print("[INIT] ClobClient 就绪。host=%s chain_id=%s signature_type=%s funder=%s" % (
        str(get_account_value("POLY_HOST", DEFAULT_HOST)),
        str(get_account_value("POLY_CHAIN_ID", DEFAULT_CHAIN_ID)),
        str(get_account_value("POLY_SIGNATURE", DEFAULT_SIGNATURE_TYPE)),
        str(get_account_value("POLY_FUNDER", "?"))[:10] + "...",
    ))
